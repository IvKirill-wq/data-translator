import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    key TEXT PRIMARY KEY,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

UPSERT = """
INSERT INTO translations (key, src, dst, reviewed, updated_at)
VALUES (?, ?, ?, ?, datetime('now'))
ON CONFLICT(key) DO UPDATE SET
    src = excluded.src,
    dst = excluded.dst,
    reviewed = excluded.reviewed,
    updated_at = excluded.updated_at
WHERE excluded.reviewed >= translations.reviewed
"""

CHUNK = 500


class Cache:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=30)

    def fetch(self, keys: Iterable[str]) -> dict:
        unique = list(dict.fromkeys(keys))
        found: dict = {}
        if not unique:
            return found
        with closing(self._connect()) as conn:
            for start in range(0, len(unique), CHUNK):
                chunk = unique[start:start + CHUNK]
                marks = ', '.join('?' * len(chunk))
                statement = f'SELECT key, dst, reviewed FROM translations WHERE key IN ({marks})'
                for key, dst, reviewed in conn.execute(statement, chunk):
                    found[key] = (dst, bool(reviewed))
        return found

    def save(self, entries: Sequence[tuple], reviewed: bool) -> int:
        rows = [(key, src, dst, 1 if reviewed else 0) for key, src, dst in entries]
        if not rows:
            return 0
        with closing(self._connect()) as conn:
            with conn:
                conn.executemany(UPSERT, rows)
        return len(rows)
