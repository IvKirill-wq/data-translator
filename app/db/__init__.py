from .base import Column, ConnParams, DbAdapter, DbError

__all__ = ['Column', 'ConnParams', 'DbAdapter', 'DbError', 'get_adapter']


def get_adapter(params: ConnParams) -> DbAdapter:
    if params.engine == 'postgres':
        from .postgres import PostgresAdapter

        return PostgresAdapter(params)
    if params.engine == 'clickhouse':
        from .clickhouse import ClickHouseAdapter

        return ClickHouseAdapter(params)
    raise DbError(f'неизвестный движок: {params.engine!r}')
