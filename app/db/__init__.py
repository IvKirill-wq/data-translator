from .base import Column, ConnParams, DbAdapter
from .clickhouse import ClickHouseAdapter
from .postgres import PostgresAdapter


def get_adapter(params: ConnParams) -> DbAdapter:
    if params.engine == 'clickhouse':
        return ClickHouseAdapter(params)
    if params.engine == 'postgres':
        return PostgresAdapter(params)