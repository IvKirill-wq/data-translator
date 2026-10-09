import argparse
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import clickhouse_connect

from app.db.clickhouse import qi
from app.db.types import CH_SAFE_DIGITS, ch_decimal_spec, strip_nullable

KNOWN_SCALARS = (
    'String', 'Float32', 'Float64', 'Bool', 'UUID',
    'Int8', 'Int16', 'Int32', 'Int64', 'UInt8', 'UInt16', 'UInt32', 'UInt64',
    'DateTime',
)
KNOWN_ENGINES = (
    'MergeTree', 'ReplacingMergeTree', 'SummingMergeTree', 'AggregatingMergeTree',
    'CollapsingMergeTree', 'VersionedCollapsingMergeTree', 'GraphiteMergeTree',
    'ReplicatedMergeTree', 'ReplicatedReplacingMergeTree', 'ReplicatedSummingMergeTree',
    'ReplicatedAggregatingMergeTree', 'View',
)
PLAIN_NAME = re.compile(r'^[0-9A-Za-zА-Яа-яЁё_][0-9A-Za-zА-Яа-яЁё_ .\-]*$')
SYSTEM_DATABASES = ('system', 'INFORMATION_SCHEMA', 'information_schema')
EXAMPLES = 8

DATABASES = 'select name, engine from system.databases order by name'
TABLES = """
    select name, engine, sorting_key, primary_key, total_rows, as_select
    from system.tables where database = {db:String} order by name
"""
TABLES_OLD = """
    select name, engine, sorting_key, primary_key, 0 as total_rows, '' as as_select
    from system.tables where database = {db:String} order by name
"""
COLUMNS = """
    select table, position, name, type, default_kind, default_expression
    from system.columns where database = {db:String} order by table, position
"""
COLUMNS_OLD = """
    select table, position, name, type, '' as default_kind, '' as default_expression
    from system.columns where database = {db:String} order by table, position
"""


def arguments():
    parser = argparse.ArgumentParser(
        description='Выгрузка всех объектов, колонок и типов базы ClickHouse с пометкой '
                    'того, что может не принять драйвер конструктора дашбордов',
        epilog='вывод длинный — удобнее сразу в файл: ... > dump.txt 2>&1'
    )
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8123)
    parser.add_argument('--user', default='default')
    parser.add_argument('--password', default=os.environ.get('CH_PASSWORD', ''))
    parser.add_argument('--database', action='append', default=[])
    parser.add_argument('--brief', action='store_true')
    parser.add_argument('--no-probe', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    return parser.parse_args()


def client(options):
    return clickhouse_connect.get_client(
        host=options.host,
        port=options.port,
        username=options.user,
        password=options.password,
        connect_timeout=10,
        send_receive_timeout=options.timeout,
        client_name='data-translator-dump',
    )


def ask(connection, statement, fallback=None, parameters=None):
    try:
        return connection.query(statement, parameters=parameters).result_rows
    except Exception as error:
        if fallback is None:
            raise
        print(f'  (запрос не прошёл, беру упрощённый: {error})')
        return connection.query(fallback, parameters=parameters).result_rows


def judge_type(type_name: str) -> str:
    inner, _ = strip_nullable(type_name)
    core = inner.strip()
    if core in KNOWN_SCALARS:
        return ''
    if core.startswith('DateTime64'):
        return 'DateTime64 — драйвер его не знает'
    if core.startswith('DateTime('):
        return ''
    if core in ('Date', 'Date32'):
        return 'тип даты вместо DateTime'
    if core.startswith('Decimal'):
        precision, scale = ch_decimal_spec(core)
        if not precision:
            return 'Decimal без разрядности'
        if precision + scale > CH_SAFE_DIGITS:
            return f'Decimal шире {CH_SAFE_DIGITS} разрядов ({precision} + {scale})'
        return ''
    for head in ('LowCardinality', 'Enum', 'Array', 'Map', 'Tuple', 'Nested',
                 'FixedString', 'IPv4', 'IPv6', 'AggregateFunction',
                 'SimpleAggregateFunction', 'Int128', 'Int256', 'UInt128', 'UInt256',
                 'Nothing', 'Variant', 'Dynamic', 'JSON', 'Object', 'Point', 'Ring',
                 'Polygon', 'MultiPolygon', 'Interval'):
        if core.startswith(head):
            return f'{head} — нет в проверенном перечне'
    return 'нет в проверенном перечне'


def judge_name(name: str) -> str:
    if not (name or '').strip():
        return 'пустое имя'
    if name != name.strip():
        return 'пробел на краю имени'
    if len((name or '').encode('utf-8')) > 63:
        return 'имя длиннее 63 байт'
    if not PLAIN_NAME.match(name):
        return 'необычные символы в имени'
    return ''


def judge_engine(engine: str) -> str:
    if engine in KNOWN_ENGINES:
        return ''
    if engine in ('MaterializedView', 'LiveView', 'WindowView'):
        return f'{engine} — особое представление'
    return f'движок {engine} — нет в проверенном перечне'


def collect(connection, database: str) -> tuple[list, dict]:
    tables = ask(connection, TABLES, TABLES_OLD, {'db': database})
    columns: dict = {}
    for table, position, name, type_name, kind, expression in ask(
        connection, COLUMNS, COLUMNS_OLD, {'db': database}
    ):
        columns.setdefault(table, []).append((position, name, type_name, kind, expression))
    return tables, columns


def probe(connection, database: str, table: str) -> str:
    try:
        connection.query(f'describe table {qi(database)}.{qi(table)}')
    except Exception as error:
        return str(error).replace('\n', ' ')[:300]
    return ''


def grouped(entries: dict, title: str) -> None:
    if not entries:
        return
    print()
    print(f'--- {title} ---')
    for key, places in sorted(entries.items(), key=lambda item: (-len(item[1]), item[0])):
        tables = {table for table, _ in places}
        print(f'  {key}: колонок {len(places)} в объектах {len(tables)}')
        shown = ', '.join(f'{table}.{column}' for table, column in places[:EXAMPLES])
        tail = f', и ещё {len(places) - EXAMPLES}' if len(places) > EXAMPLES else ''
        print(f'      {shown}{tail}')


def report(connection, options, database: str, engine: str) -> int:
    tables, columns = collect(connection, database)
    total = sum(len(items) for items in columns.values())
    print()
    print('=' * 78)
    print(f'база {database} ({engine}): объектов {len(tables)}, колонок {total}')

    kinds: dict = {}
    doubts: dict = {}
    defaults: dict = {}
    names: dict = {}
    engines: dict = {}
    broken: list = []

    for name, table_engine, sorting_key, primary_key, rows, _ in tables:
        engines.setdefault(table_engine, []).append(name)
        for position, column, type_name, kind, expression in columns.get(name, []):
            kinds.setdefault(type_name, []).append((name, column))
            trouble = judge_type(type_name)
            if trouble:
                doubts.setdefault(f'{type_name}   [{trouble}]', []).append((name, column))
            if kind:
                defaults.setdefault(f'{kind} {expression}'.strip(), []).append((name, column))
            problem = judge_name(column)
            if problem:
                names.setdefault(problem, []).append((name, column))
        if not options.no_probe:
            failure = probe(connection, database, name)
            if failure:
                broken.append((name, table_engine, failure))

    grouped(kinds, 'все типы')
    grouped(doubts, 'ВНЕ ПРОВЕРЕННОГО ПЕРЕЧНЯ — первые подозреваемые')
    grouped(defaults, 'колонки с DEFAULT/MATERIALIZED/ALIAS')
    grouped(names, 'подозрительные имена колонок')

    print()
    print('--- движки объектов ---')
    for table_engine, found in sorted(engines.items()):
        trouble = judge_engine(table_engine)
        print(f'  {table_engine}: {len(found)}' + (f'   [{trouble}]' if trouble else ''))
        if trouble:
            print('      ' + ', '.join(sorted(found)[:EXAMPLES]))

    if broken:
        print()
        print('--- объекты, которые не описываются (describe не работает) ---')
        for name, table_engine, failure in broken:
            print(f'  {name} ({table_engine}): {failure}')

    if not options.brief:
        print()
        print('--- объекты и колонки ---')
        for name, table_engine, sorting_key, primary_key, rows, select in tables:
            head = f'{name}  [{table_engine}]'
            if sorting_key:
                head += f'  ключ: {sorting_key}'
            if rows:
                head += f'  строк: {rows}'
            print()
            print(head)
            if select:
                body = ' '.join(select.split())
                print(f'  запрос: {body[:300]}' + ('...' if len(body) > 300 else ''))
            for position, column, type_name, kind, expression in columns.get(name, []):
                trouble = judge_type(type_name) or judge_name(column)
                mark = f'   <-- {trouble}' if trouble else ''
                extra = f'  {kind} {expression}'.rstrip() if kind else ''
                print(f'  {position:3} {column:38} {type_name}{extra}{mark}')

    return len(doubts) + len(broken)


def main() -> None:
    options = arguments()
    connection = client(options)
    version = connection.query('select version()').result_rows[0][0]
    zone = connection.query('select timezone()').result_rows[0][0]
    print(f'сервер {options.host}:{options.port}, ClickHouse {version}, пояс сервера {zone}')

    found = ask(connection, DATABASES)
    wanted = set(options.database)
    chosen = [
        (name, engine) for name, engine in found
        if (name in wanted if wanted else name not in SYSTEM_DATABASES)
    ]
    if not chosen:
        raise SystemExit('нет баз для разбора: ' + ', '.join(name for name, _ in found))
    if not wanted:
        print('базы: ' + ', '.join(f'{name} ({engine})' for name, engine in found))

    doubts = 0
    for name, engine in chosen:
        doubts += report(connection, options, name, engine)

    print()
    print('=' * 78)
    if doubts:
        print(f'подозрительных групп всего: {doubts} — смотрите разделы '
              f'«ВНЕ ПРОВЕРЕННОГО ПЕРЕЧНЯ» и «не описываются»')
    else:
        print('всё в проверенном перечне типов, и все объекты описываются')
    raise SystemExit(0)


if __name__ == '__main__':
    main()
