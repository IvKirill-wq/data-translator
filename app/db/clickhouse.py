import clickhouse_connect

from .base import Column


def qi(name: str) -> str:
    return '`' + name.replace('`', '``') + '`'


class ClickHouseAdapter:
    def __init__(self, params):
        self._client = clickhouse_connect.get_client(host=params.host, port=params.port, username=params.user, password=params.password, database=params.database,)


    def probe(self, params):    
        result = self._client.query(f'SHOW TABLES FROM {params.database} LIMIT 1')
        table_names = [table[0] for table in result.result_rows]

        if table_names:
            return True
        else:
            return False


    def query_columns(self, sql_text) -> list:
        result = self._client.query(f'SELECT *  FROM ({sql_text}) AS src LIMIT 0')
        return [
            Column(name=name, type=str(type_), translatable='String' in str(type_))
            for name, type_ in zip(result.column_names, result.column_types)
        ]

    def count_distinct(self, sql_text, Column) -> int:
        result = self._client.query (f'SELECT COUNT(DISTINCT {Column}) FROM ({sql_text}) AS  src')
    def iter_columns(self):

    def ensure_dictionary(self):

    def write_pairs(self):
