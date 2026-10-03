from dataclasses import dataclass
from typing import Protocol, Iterator

@dataclass(frozen = True)
class ConnParams:
    engine: str
    host: str
    port: int
    user: str
    password: str
    database: str

@dataclass(frozen = True)
class Column:
    name: str
    type: str
    translatable: bool

class DbAdapter(Protocol):
    def probe(self) -> None: ...

    def query_columns(self, sql_text: str) -> list[Column]: ...
    def count_distinct(self, sql_text: str, column: str) -> int: ...
    def iter_columns(self, sql_text: str, column: str) -> Iterator[str]: ...

    def ensure_dictionary(self, table: str) -> None: ...
    def write_pairs(self, table:str, pairs: list[tuple[str, str]]) -> int: ... # затем отказаться от пар str - str
    