from dataclasses import dataclass, replace
from typing import Iterator, Protocol, Sequence

TEXT = 'text'
INT = 'int'
BIGINT = 'bigint'
UBIGINT = 'ubigint'
FLOAT = 'float'
DECIMAL = 'decimal'
BOOL = 'bool'
DATE = 'date'
DATETIME = 'datetime'
DATETIMETZ = 'datetimetz'
UUID = 'uuid'


class DbError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnParams:
    engine: str
    host: str
    port: int
    user: str
    password: str
    database: str
    safe_types: bool = True


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    raw_type: str
    precision: int = 0
    scale: int = 0

    @property
    def is_text(self) -> bool:
        return self.kind == TEXT

    def as_text(self) -> 'Column':
        return replace(self, kind=TEXT)

    def renamed(self, name: str) -> 'Column':
        return replace(self, name=name)


@dataclass(frozen=True)
class TableName:
    schema: str
    name: str


def split_table(table: str) -> TableName:
    parts = [part.strip() for part in (table or '').split('.')]
    if len(parts) == 1 and parts[0]:
        return TableName('', parts[0])
    if len(parts) == 2 and all(parts):
        return TableName(parts[0], parts[1])
    raise DbError(f'не разобрать имя таблицы: {table!r}')


def ensure_unique(columns: Sequence[Column], engine: str) -> list[Column]:
    counts: dict = {}
    for column in columns:
        counts[column.name] = counts.get(column.name, 0) + 1
    repeated = [name for name, count in counts.items() if count > 1]
    if repeated:
        raise DbError(
            f'{engine}: имена колонок повторяются: {", ".join(repeated)}. '
            'Задайте им разные псевдонимы через AS'
        )
    return list(columns)


class RowStream(Protocol):
    columns: Sequence[Column]

    def __iter__(self) -> Iterator[tuple]: ...


class DbAdapter(Protocol):
    def columns(self, sql_text: str) -> list[Column]: ...

    def rows(self, sql_text: str): ...

    def fit_columns(self, columns: Sequence[Column]) -> list[Column]: ...

    def table_columns(self, table: str) -> list[Column] | None: ...

    def create_table(self, table: str, columns: Sequence[Column]) -> None: ...

    def insert(
        self,
        table: str,
        columns: Sequence[Column],
        rows: Iterator[tuple],
        batch: int = 10_000,
        clear: bool = False,
    ) -> int: ...
