import clickhouse_connect

from .base import Column


def qi(name: str) -> str:
    return '`' + name.replace('`', '``') + '`'


class ClickHouseAdapter:
    def __init__(self, params):
        self._client = clickhouse_connect.get_client(
            host=params.host, port=params.port, username=params.user,
            password=params.password, database=params.database,
        )

    def query_columns(self, sql_text):
        result = self._client.query(f'SELECT * FROM ({sql_text}) AS src LIMIT 0')
        return [
            Column(name=name, type=str(type_), translatable='String' in str(type_))
            for name, type_ in zip(result.column_names, result.column_types)
        ]