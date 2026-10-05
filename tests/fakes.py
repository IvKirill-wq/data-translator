from contextlib import contextmanager

from app.db.base import TEXT, Column


def text_column(name: str) -> Column:
    return Column(name=name, kind=TEXT, raw_type='text')


class FakeStream:
    def __init__(self, columns, rows):
        self.columns = columns
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeAdapter:
    def __init__(self, columns=(), rows=(), existing=None):
        self._columns = list(columns)
        self._rows = list(rows)
        self.existing = existing
        self.created = None
        self.inserted = []
        self.reads = 0

    @contextmanager
    def rows(self, sql_text):
        self.reads += 1
        yield FakeStream(self._columns, self._rows)

    def columns(self, sql_text):
        return list(self._columns)

    def fit_columns(self, columns):
        return list(columns)

    def table_columns(self, table):
        return self.existing

    def create_table(self, table, columns):
        self.created = (table, list(columns))
        self.existing = list(columns)

    def insert(self, table, columns, rows, batch=10_000, clear=False):
        materialized = list(rows)
        self.inserted.append((table, list(columns), materialized, clear))
        return len(materialized)


class FakeTranslator:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.seen = []

    def translate(self, texts):
        self.seen.extend(texts)
        return [self.answers.get(text, 'EN ' + text) for text in texts]
