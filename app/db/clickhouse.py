import logging
from contextlib import contextmanager
from typing import Iterator, Sequence

import clickhouse_connect

from .base import (
    DATE,
    DATETIME,
    DATETIMETZ,
    Column,
    ConnParams,
    DbError,
    ensure_unique,
    split_table,
)
from .types import (
    CH_SAFE_LIMITS,
    CH_WIDE_LIMITS,
    ch_ddl,
    ch_decimal_spec,
    ch_kind,
    ch_safe_column,
    fit_temporal,
)

try:
    from clickhouse_connect.driver.exceptions import ClickHouseError as DriverError
except ImportError:
    DriverError = Exception

LOG = logging.getLogger(__name__)

ENGINE = 'ClickHouse'
PEEK_SETTINGS = {'max_result_rows': '1', 'result_overflow_mode': 'break'}


def qi(name: str) -> str:
    return '`' + str(name).replace('\\', '\\\\').replace('`', '\\`') + '`'


def type_name(column_type) -> str:
    return getattr(column_type, 'name', None) or str(column_type)


def describe(source) -> list[Column]:
    names = list(getattr(source, 'column_names', None) or [])
    types = list(getattr(source, 'column_types', None) or [])
    if not names:
        raise DbError(f'{ENGINE}: запрос не вернул колонок')
    if len(types) < len(names):
        types = types + [''] * (len(names) - len(types))
    columns = []
    for name, column_type in zip(names, types):
        raw = type_name(column_type)
        precision, scale = ch_decimal_spec(raw)
        columns.append(Column(
            name=name, kind=ch_kind(raw), raw_type=raw, precision=precision, scale=scale
        ))
    return ensure_unique(columns, ENGINE)


class Stream:
    def __init__(self, blocks, columns: list[Column]):
        self._blocks = blocks
        self.columns = columns

    def __iter__(self) -> Iterator[tuple]:
        try:
            for block in self._blocks:
                for row in block:
                    yield tuple(row)
        except DriverError as error:
            raise DbError(f'{ENGINE}: чтение результата прервано: {error}') from error


class ClickHouseAdapter:
    def __init__(self, params: ConnParams):
        self._database = params.database
        self._safe = bool(params.safe_types)
        self._settings = {
            'host': params.host,
            'port': params.port,
            'username': params.user,
            'password': params.password,
            'connect_timeout': 10,
            'send_receive_timeout': 600,
            'client_name': 'data-translator',
        }

    @contextmanager
    def _client(self, database: str | None = None):
        settings = dict(self._settings)
        if database:
            settings['database'] = database
        try:
            client = clickhouse_connect.get_client(**settings)
        except Exception as error:
            raise DbError(f'{ENGINE}: подключение не удалось: {error}') from error
        try:
            yield client
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _target(self, table: str) -> tuple[str, str]:
        parsed = split_table(table)
        database = parsed.schema or self._database
        if not database:
            raise DbError(f'{ENGINE}: не указана база данных для таблицы {table}')
        return database, parsed.name

    def _qualified(self, table: str) -> str:
        database, name = self._target(table)
        return f'{qi(database)}.{qi(name)}'

    @contextmanager
    def rows(self, sql_text: str, peek: bool = False):
        with self._client(self._database) as client:
            settings = dict(PEEK_SETTINGS) if peek else None
            try:
                context = client.query_row_block_stream(sql_text, settings=settings)
            except Exception as error:
                raise DbError(f'{ENGINE}: запрос не выполнен: {error}') from error
            with context as blocks:
                try:
                    columns = describe(getattr(context, 'source', context))
                except DbError:
                    raise
                except Exception as error:
                    raise DbError(
                        f'{ENGINE}: не прочитать описание результата: {error}'
                    ) from error
                yield Stream(blocks, columns)

    def columns(self, sql_text: str) -> list[Column]:
        with self.rows(sql_text, peek=True) as stream:
            return list(stream.columns)

    def fit_columns(self, columns: Sequence[Column]) -> list[Column]:
        if not self._safe:
            return list(columns)
        return [ch_safe_column(column) for column in columns]

    def table_columns(self, table: str) -> list[Column] | None:
        database, name = self._target(table)
        statement = (
            'SELECT name, type FROM system.columns '
            'WHERE database = {db:String} AND table = {tb:String} ORDER BY position'
        )
        with self._client() as client:
            try:
                result = client.query(statement, parameters={'db': database, 'tb': name})
            except Exception as error:
                raise DbError(f'{ENGINE}: не прочитать описание {table}: {error}') from error
        found = list(result.result_rows)
        if not found:
            return None
        return [Column(name=row[0], kind=ch_kind(row[1]), raw_type=row[1]) for row in found]

    def create_table(self, table: str, columns: Sequence[Column]) -> None:
        database, _ = self._target(table)
        body = ', '.join(f'{qi(column.name)} {ch_ddl(column, self._safe)}' for column in columns)
        statements = [
            f'CREATE DATABASE IF NOT EXISTS {qi(database)}',
            f'CREATE TABLE IF NOT EXISTS {self._qualified(table)} ({body}) '
            'ENGINE = MergeTree ORDER BY tuple()',
        ]
        self._run(statements, f'не создать таблицу {table}')

    def _run(self, statements: Sequence[str], failure: str) -> None:
        with self._client() as client:
            for statement in statements:
                try:
                    client.command(statement)
                except Exception as error:
                    raise DbError(f'{ENGINE}: {failure}: {error}') from error

    def insert(
        self,
        table: str,
        columns: Sequence[Column],
        rows: Iterator[tuple],
        batch: int = 10_000,
        clear: bool = False,
    ) -> int:
        database, name = self._target(table)
        names = [column.name for column in columns]
        moments = [
            index for index, column in enumerate(columns)
            if column.kind in (DATETIME, DATETIMETZ)
        ]
        days = [index for index, column in enumerate(columns) if column.kind == DATE]
        limits = CH_SAFE_LIMITS if self._safe else CH_WIDE_LIMITS
        dropped = [0]
        written = 0
        chunk: list[tuple] = []
        if clear:
            self._run([f'TRUNCATE TABLE {self._qualified(table)}'], f'не очистить таблицу {table}')
        with self._client() as client:
            for row in rows:
                chunk.append(
                    fit_temporal(row, moments, days, dropped, limits) if moments or days else row
                )
                if len(chunk) >= batch:
                    self._send(client, database, name, names, chunk)
                    written += len(chunk)
                    chunk = []
            if chunk:
                self._send(client, database, name, names, chunk)
                written += len(chunk)
        if dropped[0]:
            LOG.warning(
                '%s: значений даты вне диапазона %s-%s заменено на NULL: %s',
                ENGINE, limits.min_time.year, limits.max_date.year, dropped[0],
            )
        return written

    def _send(
        self, client, database: str, name: str, names: Sequence[str], chunk: Sequence[tuple]
    ) -> None:
        try:
            client.insert(table=name, database=database, data=list(chunk), column_names=list(names))
        except Exception as error:
            raise DbError(f'{ENGINE}: запись в {database}.{name} не удалась: {error}') from error
