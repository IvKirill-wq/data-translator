import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import clickhouse_connect

from app.db.clickhouse import qi
from app.db.types import (
    CH_SAFE_LIMITS,
    ch_safe_type,
    ch_type_is_castable,
    ch_type_is_safe,
)

VIEW_ENGINES = ('View', 'MaterializedView', 'LiveView', 'WindowView')
DECIMAL_PATTERN = 'Decimal('

INVENTORY = """
    select name, engine, sorting_key, primary_key, as_select
    from system.tables where database = {db:String} order by name
"""
INVENTORY_OLD = """
    select name, engine, sorting_key, primary_key, '' as as_select
    from system.tables where database = {db:String} order by name
"""
COLUMNS = """
    select table, name, type, position
    from system.columns where database = {db:String} order by table, position
"""


def arguments():
    parser = argparse.ArgumentParser(
        description='Приведение типов колонок ClickHouse, на которых зависает конструктор '
                    'запросов: Date32 -> Date, DateTime64 -> DateTime, '
                    'Decimal(p, s) с p + s > 28 -> Decimal в бюджете 28 разрядов'
    )
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8123)
    parser.add_argument('--user', default='default')
    parser.add_argument('--password', default=os.environ.get('CH_PASSWORD', ''))
    parser.add_argument('--database', required=True)
    parser.add_argument('--table', action='append', default=[])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--no-check', action='store_true')
    parser.add_argument('--timeout', type=int, default=600)
    return parser.parse_args()


def client(options):
    return clickhouse_connect.get_client(
        host=options.host,
        port=options.port,
        username=options.user,
        password=options.password,
        connect_timeout=10,
        send_receive_timeout=options.timeout,
        client_name='data-translator-retype',
    )


def inventory(connection, database: str) -> dict:
    try:
        rows = connection.query(INVENTORY, parameters={'db': database}).result_rows
    except Exception:
        rows = connection.query(INVENTORY_OLD, parameters={'db': database}).result_rows
    tables = {}
    for name, engine, sorting_key, primary_key, as_select in rows:
        keys = set()
        for text in (sorting_key, primary_key):
            keys.update(part.strip().strip('`') for part in (text or '').split(',') if part.strip())
        tables[name] = {'engine': engine, 'keys': keys, 'select': as_select or '', 'columns': []}
    for table, name, type_name, _ in connection.query(
        COLUMNS, parameters={'db': database}
    ).result_rows:
        if table in tables:
            tables[table]['columns'].append((name, type_name))
    return tables


def violations(connection, database: str, table: str, column: str, target: str) -> int:
    name = qi(column)
    if 'Date(' in target or target.endswith('Date)') or target == 'Date':
        low, high = CH_SAFE_LIMITS.min_date, CH_SAFE_LIMITS.max_date
    elif 'DateTime' in target:
        low, high = CH_SAFE_LIMITS.min_time.date(), CH_SAFE_LIMITS.max_time.date()
    elif DECIMAL_PATTERN in target:
        digits = target[target.index('(') + 1:target.rindex(')')].split(',')
        whole = int(digits[0]) - int(digits[1])
        condition = f'countIf(abs(toFloat64({name})) >= 1e{whole})'
        statement = f'select {condition} from {qi(database)}.{qi(table)}'
        return int(connection.query(statement).result_rows[0][0])
    else:
        return 0
    condition = (
        f"countIf(toDate32({name}) < toDate32('{low:%Y-%m-%d}')"
        f" or toDate32({name}) > toDate32('{high:%Y-%m-%d}'))"
    )
    statement = f'select {condition} from {qi(database)}.{qi(table)}'
    return int(connection.query(statement).result_rows[0][0])


def plan(connection, options, tables: dict) -> tuple[list, list]:
    ready, manual = [], []
    wanted = set(options.table)
    for table, facts in tables.items():
        if wanted and table not in wanted:
            continue
        is_view = facts['engine'] in VIEW_ENGINES
        for column, current in facts['columns']:
            if ch_type_is_safe(current):
                continue
            target = ch_safe_type(current)
            if is_view:
                manual.append((table, column, current, target, f'представление ({facts["engine"]})'))
                continue
            if column in facts['keys']:
                manual.append((table, column, current, target, 'колонка в ключе сортировки'))
                continue
            if not ch_type_is_castable(current):
                manual.append((table, column, current, target, 'тип не приводится напрямую'))
                continue
            broken = 0
            if not options.no_check:
                try:
                    broken = violations(connection, options.database, table, column, target)
                except Exception as error:
                    manual.append((table, column, current, target, f'проверка не удалась: {error}'))
                    continue
            if broken and not options.force:
                manual.append((
                    table, column, current, target,
                    f'{broken} значений не влезут в новый тип (--force чтобы всё равно)',
                ))
                continue
            ready.append((table, column, current, target, broken))
    return ready, manual


def view_statement(database: str, facts: dict, table: str) -> str:
    parts = []
    for column, current in facts['columns']:
        if ch_type_is_safe(current):
            parts.append(f'    {qi(column)}')
        else:
            parts.append(f'    cast({qi(column)} as {ch_safe_type(current)}) as {qi(column)}')
    body = facts['select'] or f'select * from {qi(database)}.{qi(table)}_source'
    return (
        f'create or replace view {qi(database)}.{qi(table)} as select\n'
        + ',\n'.join(parts)
        + f'\nfrom (\n{body}\n)'
    )


def main() -> None:
    options = arguments()
    connection = client(options)
    tables = inventory(connection, options.database)
    if not tables:
        raise SystemExit(f'в базе {options.database} нет таблиц или нет прав')

    ready, manual = plan(connection, options, tables)
    print(f'база {options.database}: таблиц и представлений {len(tables)}, '
          f'к переводу колонок {len(ready)}, вручную {len(manual)}')

    if ready:
        print()
        print('--- можно привести автоматически ---')
        for table, column, current, target, broken in ready:
            note = f'  [принудительно, {broken} значений потеряются]' if broken else ''
            print(f'  {table}.{column}: {current} -> {target}{note}')

    if manual:
        print()
        print('--- нужно руками ---')
        for table, column, current, target, reason in manual:
            print(f'  {table}.{column}: {current} -> {target}   {reason}')

    views = sorted({table for table, *_ in manual if tables[table]['engine'] in VIEW_ENGINES})
    for table in views:
        print()
        print(f'--- предложение для представления {table} (выполнять самостоятельно) ---')
        print(view_statement(options.database, tables[table], table))

    if not options.apply:
        print()
        print('это был разбор без изменений; чтобы применить — добавьте --apply')
        raise SystemExit(1 if manual else 0)

    print()
    print('--- применяю ---')
    done, failed = 0, 0
    for table, column, current, target, _ in ready:
        statement = (
            f'alter table {qi(options.database)}.{qi(table)} '
            f'modify column {qi(column)} {target}'
        )
        try:
            connection.command(statement, settings={'mutations_sync': '2'})
        except Exception as error:
            failed += 1
            print(f'  ОШИБКА {table}.{column}: {error}')
        else:
            done += 1
            print(f'  готово {table}.{column}: {current} -> {target}')
    print()
    print(f'приведено колонок: {done}, ошибок: {failed}, осталось вручную: {len(manual)}')
    raise SystemExit(1 if failed or manual else 0)


if __name__ == '__main__':
    main()
