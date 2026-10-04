import psycopg
from psycopg import sql


class PostgresAdapter:
    def __init__(self, params):
        self._params = dict(host=params.host, port=params.port, user=params.user, password=params.password, dbname=params.database)

    def probe():
        
    def query_columns():

    def count_distinct():

    def iter_columns():

    def ensure_dictionary():

    def write_pairs():

    def iter_distinct(self, sql_text, column):
        query = sql.SQL(
            'SELECT DISTINCT {col} FROM ({src}) AS src WHERE {col} IS NOT NULL'
        ).format(col=sql.Identifier(column), src=sql.SQL(sql_text))

        with psycopg.connect(**self._params) as conn:
            conn.read_only = True
            with conn.cursor(name='distinct_cur') as cur:
                cur.itersize = 10_000 
                cur.execute(query)
                for (value,) in cur:
                    yield value