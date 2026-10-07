import logging
from contextlib import contextmanager
from typing import Iterator, Sequence

import psycopg
from psycopg import postgres, pq, sql
from psycopg.adapt import Loader

from .base import Column, ConnParams, DbError, ensure_unique, split_table
from .types import clip_name, pg_ddl, pg_kind, pg_name

LOG = logging.getLogger(__name__)

ITERSIZE = 10_000
CURSOR_NAME = 'data_translator'
LOCK_TIMEOUT = '10s'
ENGINE = 'PostgreSQL'
TEMPORAL_TYPES = ('date', 'timestamp', 'timestamptz')
LENIENT_CACHE: dict = {}


def lenient(native: type) -> type:
    ready = LENIENT_CACHE.get(native)
    if ready is not None:
        return ready

    class LenientLoader(Loader):
        format = native.format
        _warned = False

        def __init__(self, oid, context=None):
            super().__init__(oid, context)
            self._native = native(oid, context)

        def load(self, data):
            try:
                return self._native.load(data)
            except psycopg.DataError as error:
                if not self._warned:
                    LOG.warning(
                        '%s: значение даты вне диапазона Python прочитано как NULL (%s)',
                        ENGINE, error,
                    )
                    LenientLoader._warned = True
                return None

    LenientLoader.__name__ = 'Lenient' + native.__name__
    LENIENT_CACHE[native] = LenientLoader
    return LenientLoader


def lenient_loaders(conn) -> list:
    found = []
    for name in TEMPORAL_TYPES:
        try:
            info = postgres.types.get(name)
        except Exception:
            info = None
        if info is None:
            continue
        for fmt in (pq.Format.TEXT, pq.Format.BINARY):
            native = conn.adapters.get_loader(info.oid, fmt)
            if native is None:
                continue
            try:
                ready = lenient(native)
                ready(info.oid, conn)
            except Exception as error:
                LOG.warning(
                    '%s: загрузчик %s не подменить, бесконечные даты остаются ошибкой (%s)',
                    ENGINE, getattr(native, '__name__', native), error,
                )
                continue
            found.append((info.oid, ready))
    return found


def type_name(oid: int) -> str:
    try:
        info = postgres.types.get(oid)
    except Exception:
        info = None
    if info is None:
        return str(oid)
    return pg_name(oid, info.name, info.array_oid)


def describe(cursor) -> list[Column]:
    if cursor.description is None:
        raise DbError(
            f'{ENGINE}: запрос не вернул результат — '
            'нужен SELECT или процедура, отдающая строки'
        )
    columns = []
    for item in cursor.description:
        raw = type_name(item.type_code)
        columns.append(Column(
            name=item.name,
            kind=pg_kind(raw),
            raw_type=raw,
            precision=item.precision or 0,
            scale=item.scale or 0,
        ))
    return ensure_unique(columns, ENGINE)


def advance_to_result(cursor) -> None:
    while cursor.description is None:
        try:
            if not cursor.nextset():
                return
        except psycopg.Error:
            return


class Stream:
    def __init__(self, cursor, columns: list[Column]):
        self._cursor = cursor
        self.columns = columns

    def __iter__(self) -> Iterator[tuple]:
        try:
            for row in self._cursor:
                yield tuple(row)
        except psycopg.Error as error:
            raise DbError(f'{ENGINE}: чтение результата прервано: {error}') from error


class PostgresAdapter:
    def __init__(self, params: ConnParams):
        self._database = params.database
        self._params = {
            'host': params.host,
            'port': params.port,
            'user': params.user,
            'password': params.password,
            'dbname': params.database,
            'connect_timeout': 10,
            'application_name': 'data-translator',
        }

    @contextmanager
    def _connect(self):
        try:
            conn = psycopg.connect(**self._params)
        except psycopg.Error as error:
            raise DbError(f'{ENGINE}: подключение не удалось: {error}') from error
        try:
            for oid, loader in lenient_loaders(conn):
                conn.adapters.register_loader(oid, loader)
        except psycopg.Error as error:
            LOG.warning('%s: не удалось подменить загрузчики даты: %s', ENGINE, error)
        try:
            yield conn
        finally:
            conn.close()

    def _ident(self, table: str):
        parsed = split_table(table)
        if parsed.schema:
            return sql.Identifier(parsed.schema, parsed.name)
        return sql.Identifier(parsed.name)

    @contextmanager
    def rows(self, sql_text: str):
        with self._connect() as conn:
            cursor = None
            try:
                cursor = conn.cursor(name=CURSOR_NAME)
                cursor.itersize = ITERSIZE
                cursor.execute(sql_text)
                columns = describe(cursor)
            except psycopg.Error as declare_error:
                cursor = self._fallback_cursor(conn, cursor, sql_text, declare_error)
                columns = describe(cursor)
            try:
                yield Stream(cursor, columns)
            finally:
                try:
                    cursor.close()
                except psycopg.Error:
                    pass

    def _fallback_cursor(self, conn, declared, sql_text: str, previous: Exception):
        if declared is not None:
            try:
                declared.close()
            except psycopg.Error:
                pass
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        try:
            conn.autocommit = True
        except psycopg.Error:
            pass
        cursor = conn.cursor()
        try:
            cursor.execute(sql_text)
            advance_to_result(cursor)
        except psycopg.Error as error:
            raise DbError(f'{ENGINE}: запрос не выполнен: {error}') from error
        if cursor.description is None:
            raise DbError(f'{ENGINE}: запрос не выполнен: {previous}') from previous
        return cursor

    def columns(self, sql_text: str) -> list[Column]:
        with self.rows(sql_text) as stream:
            return list(stream.columns)

    def fit_columns(self, columns: Sequence[Column]) -> list[Column]:
        return ensure_unique(
            [column.renamed(clip_name(column.name)) for column in columns], ENGINE
        )

    def table_columns(self, table: str) -> list[Column] | None:
        statement = """
            SELECT a.attname, t.typname
            FROM pg_attribute a
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE a.attrelid = to_regclass(%s) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """
        with self._connect() as conn:
            target = self._ident(table).as_string(conn)
            try:
                with conn.cursor() as cursor:
                    cursor.execute(statement, (target,))
                    found = cursor.fetchall()
            except psycopg.Error as error:
                raise DbError(f'{ENGINE}: не прочитать описание {table}: {error}') from error
        if not found:
            return None
        return [
            Column(name=name, kind=pg_kind(typname), raw_type=typname)
            for name, typname in found
        ]

    def create_table(self, table: str, columns: Sequence[Column]) -> None:
        parsed = split_table(table)
        body = sql.SQL(', ').join(
            sql.SQL('{} {}').format(sql.Identifier(column.name), sql.SQL(pg_ddl(column)))
            for column in columns
        )
        statements = []
        if parsed.schema:
            statements.append(
                sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(parsed.schema))
            )
        statements.append(
            sql.SQL('CREATE TABLE IF NOT EXISTS {} ({})').format(self._ident(table), body)
        )
        with self._connect() as conn:
            try:
                with conn.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                conn.commit()
            except psycopg.Error as error:
                conn.rollback()
                raise DbError(f'{ENGINE}: не создать таблицу {table}: {error}') from error

    def insert(
        self,
        table: str,
        columns: Sequence[Column],
        rows: Iterator[tuple],
        batch: int = 10_000,
        clear: bool = False,
    ) -> int:
        names = sql.SQL(', ').join(sql.Identifier(column.name) for column in columns)
        copy_statement = sql.SQL('COPY {} ({}) FROM STDIN').format(self._ident(table), names)
        written = 0
        with self._connect() as conn:
            try:
                with conn.cursor() as cursor:
                    if clear:
                        cursor.execute(
                            sql.SQL('SET LOCAL lock_timeout = {}').format(sql.Literal(LOCK_TIMEOUT))
                        )
                        cursor.execute(sql.SQL('TRUNCATE TABLE {}').format(self._ident(table)))
                    with cursor.copy(copy_statement) as copy:
                        for row in rows:
                            copy.write_row(row)
                            written += 1
                conn.commit()
            except psycopg.Error as error:
                conn.rollback()
                raise DbError(f'{ENGINE}: запись в {table} не удалась: {error}') from error
        return written
