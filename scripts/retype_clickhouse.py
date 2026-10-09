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
    ch_decimal_spec,
    ch_safe_type,
    ch_type_is_castable,
    ch_type_is_safe,
    strip_nullable,
)

PLAIN_VIEW = 'View'
VIEW_ENGINES = (PLAIN_VIEW, 'MaterializedView', 'LiveView', 'WindowView')
EXAMPLES = 4

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
    parser.add_argument('--apply-views', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--no-check', action='store_true')
    parser.add_argument('--verbose', action='store_true')
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


def condition_for(column: str, target: str) -> str:
    name = qi(column)
    core = strip_nullable(target)[0]
    if core.startswith('Decimal'):
        precision, scale = ch_decimal_spec(target)
        whole = precision - scale
        if whole <= 0:
            return ''
        return f'countIf(abs(toFloat64({name})) >= 1e{whole})'
    if core.startswith('DateTime'):
        low, high = CH_SAFE_LIMITS.min_time.date(), CH_SAFE_LIMITS.max_time.date()
    elif core.startswith('Date'):
        low, high = CH_SAFE_LIMITS.min_date, CH_SAFE_LIMITS.max_date
    else:
        return ''
    return (
        f"countIf(toDate32({name}) < toDate32('{low:%Y-%m-%d}')"
        f" or toDate32({name}) > toDate32('{high:%Y-%m-%d}'))"
    )


def check_query(database: str, table: str, items) -> str:
    body = ', '.join(condition for _, condition in items)
    return f'select {body} from {qi(database)}.{qi(table)}'


def counted(connection, database: str, table: str, items) -> dict:
    if not items:
        return {}
    try:
        row = connection.query(check_query(database, table, items)).result_rows[0]
        return {column: (int(row[index]), '') for index, (column, _) in enumerate(items)}
    except Exception as whole:
        if len(items) == 1:
            return {items[0][0]: (0, str(whole))}
    answer = {}
    for item in items:
        answer.update(counted(connection, database, table, [item]))
    return answer


def rounding(current: str, target: str) -> str:
    before = ch_decimal_spec(current)[1]
    after = ch_decimal_spec(target)[1]
    if before and after < before:
        return f'дробная часть округлится с {before} до {after} знаков'
    return ''


def view_reason(facts: dict) -> str:
    if facts['engine'] != PLAIN_VIEW:
        return f'представление {facts["engine"]}: только вручную'
    if not facts['select']:
        return 'представление: не прочитать текст запроса'
    return 'представление: переписывается ключом --apply-views'


def plan(connection, options, tables: dict) -> tuple[list, list]:
    ready, manual = [], []
    wanted = set(options.table)
    for table, facts in tables.items():
        if wanted and table not in wanted:
            continue
        is_view = facts['engine'] in VIEW_ENGINES
        pending = []
        for column, current in facts['columns']:
            if ch_type_is_safe(current):
                continue
            target = ch_safe_type(current)
            if is_view:
                manual.append((table, column, current, target, view_reason(facts)))
            elif column in facts['keys']:
                manual.append((table, column, current, target, 'колонка в ключе сортировки'))
            elif not ch_type_is_castable(current):
                manual.append((table, column, current, target, 'тип не приводится напрямую'))
            else:
                pending.append((column, current, target))
        if not pending:
            continue
        checks = {}
        if not options.no_check:
            items = [
                (column, condition_for(column, target))
                for column, _, target in pending
            ]
            checks = counted(
                connection, options.database, table,
                [(column, condition) for column, condition in items if condition],
            )
        for column, current, target in pending:
            broken, failure = checks.get(column, (0, ''))
            if failure:
                manual.append((table, column, current, target, f'проверка не удалась: {failure}'))
            elif broken and not options.force:
                manual.append((
                    table, column, current, target,
                    f'{broken} значений не влезут в новый тип (--force чтобы всё равно)',
                ))
            else:
                notes = [rounding(current, target)]
                if broken:
                    notes.append(f'принудительно, {broken} значений потеряются')
                ready.append((table, column, current, target, '; '.join(filter(None, notes))))
    return ready, manual


def report(title: str, entries: list, verbose: bool) -> None:
    if not entries:
        return
    print()
    print(f'--- {title}: колонок {len(entries)} ---')
    if verbose:
        for table, column, current, target, note in entries:
            print(f'  {table}.{column}: {current} -> {target}' + (f'   {note}' if note else ''))
        return
    groups = {}
    for table, column, current, target, note in entries:
        groups.setdefault((current, target, note), []).append((table, column))
    for (current, target, note), places in sorted(groups.items()):
        tables = {table for table, _ in places}
        head = f'  {current} -> {target}: колонок {len(places)} в таблицах {len(tables)}'
        print(head + (f'   [{note}]' if note else ''))
        shown = [f'{table}.{column}' for table, column in places[:EXAMPLES]]
        tail = f', и ещё {len(places) - EXAMPLES}' if len(places) > EXAMPLES else ''
        print('      ' + ', '.join(shown) + tail)
    print('      (полный список колонок — ключ --verbose)')


def view_statement(database: str, facts: dict, table: str) -> str:
    parts = []
    for column, current in facts['columns']:
        if ch_type_is_safe(current):
            parts.append(f'    {qi(column)}')
        else:
            parts.append(f'    cast({qi(column)} as {ch_safe_type(current)}) as {qi(column)}')
    return (
        f'create or replace view {qi(database)}.{qi(table)} as select\n'
        + ',\n'.join(parts)
        + f'\nfrom (\n{facts["select"]}\n)'
    )


def rewritable(tables: dict, manual: list) -> list:
    names = {table for table, *_ in manual}
    return sorted(
        table for table in names
        if tables[table]['engine'] == PLAIN_VIEW and tables[table]['select']
    )


def apply_columns(connection, options, ready: list) -> tuple[int, int]:
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
    return done, failed


def apply_views(connection, options, tables: dict, views: list) -> tuple[int, int]:
    done, failed = 0, 0
    for table in views:
        try:
            connection.command(view_statement(options.database, tables[table], table))
        except Exception as error:
            failed += 1
            print(f'  ОШИБКА представления {table}: {error}')
        else:
            done += 1
            print(f'  готово представление {table}')
    return done, failed


def main() -> None:
    options = arguments()
    connection = client(options)
    tables = inventory(connection, options.database)
    if not tables:
        raise SystemExit(f'в базе {options.database} нет таблиц или нет прав')

    ready, manual = plan(connection, options, tables)
    views = rewritable(tables, manual)
    left = [entry for entry in manual if entry[0] not in views]
    print(f'база {options.database}: таблиц и представлений {len(tables)}, '
          f'к переводу колонок {len(ready)}, представлений к переписи {len(views)}, '
          f'вручную {len(left)}')

    report('можно привести автоматически', ready, options.verbose)
    report('переписываются как представления', [
        entry for entry in manual if entry[0] in views
    ], options.verbose)
    report('нужно руками', left, options.verbose)

    if views and not options.apply_views:
        for table in views:
            print()
            print(f'--- предложение для представления {table} ---')
            print(view_statement(options.database, tables[table], table))

    if not (options.apply or options.apply_views):
        print()
        print('это был разбор без изменений; чтобы применить — добавьте --apply '
              'для колонок и --apply-views для представлений')
        raise SystemExit(1 if manual else 0)

    print()
    print('--- применяю ---')
    done, failed = 0, 0
    if options.apply:
        done, failed = apply_columns(connection, options, ready)
    if options.apply_views:
        made, broken = apply_views(connection, options, tables, views)
        done += made
        failed += broken
    print()
    print(f'изменено объектов: {done}, ошибок: {failed}, осталось вручную: {len(left)}')
    raise SystemExit(1 if failed or left or (views and not options.apply_views) else 0)


if __name__ == '__main__':
    main()
