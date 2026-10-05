import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.core.glossary import load as load_glossary
from app.core.pipeline import Pair, apply, collect, translate
from app.core.protect import Protector
from app.core.translate_client import LibreTranslateClient
from app.db import ConnParams, get_adapter


def arguments():
    parser = argparse.ArgumentParser(description='Прогон перевода без Flask')
    parser.add_argument('--engine', choices=('postgres', 'clickhouse'), required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--password', default=os.environ.get('DT_PASSWORD', ''))
    parser.add_argument('--database', required=True)
    parser.add_argument('--query')
    parser.add_argument('--query-file')
    parser.add_argument('--columns', action='append', default=[])
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--limit', type=int, default=500)

    parser.add_argument('--url', default=os.environ.get('LIBRE_TRANSLATE_URL', ''))
    parser.add_argument('--key', default=os.environ.get('LIBRE_TRANSLATE_KEY', ''))
    parser.add_argument('--batch', type=int, default=24)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--glossary', default=str(BASE_DIR / 'glossary.tsv'))

    parser.add_argument('--out')
    parser.add_argument('--from-tsv')

    parser.add_argument('--to-engine', choices=('postgres', 'clickhouse'))
    parser.add_argument('--to-host')
    parser.add_argument('--to-port', type=int)
    parser.add_argument('--to-user')
    parser.add_argument('--to-password', default=os.environ.get('DT_TARGET_PASSWORD', ''))
    parser.add_argument('--to-database')
    parser.add_argument('--to-table')
    parser.add_argument('--insert-batch', type=int, default=10_000)
    return parser.parse_args()


def query_text(options) -> str:
    if options.query_file:
        return Path(options.query_file).read_text(encoding='utf-8').strip().rstrip(';')
    if options.query:
        return options.query.strip().rstrip(';')
    raise SystemExit('нужен --query или --query-file')


def column_names(options) -> list:
    names = []
    for item in options.columns:
        names.extend(name.strip() for name in item.split(',') if name.strip())
    return [] if options.auto else names


def source_params(options) -> ConnParams:
    return ConnParams(
        engine=options.engine,
        host=options.host,
        port=options.port,
        user=options.user,
        password=options.password,
        database=options.database,
    )


def target_params(options) -> ConnParams:
    missing = [
        name for name in ('to_engine', 'to_host', 'to_port', 'to_user', 'to_database')
        if not getattr(options, name)
    ]
    if missing:
        raise SystemExit(
            'для записи нужны: ' + ', '.join('--' + name.replace('_', '-') for name in missing)
        )
    return ConnParams(
        engine=options.to_engine,
        host=options.to_host,
        port=options.to_port,
        user=options.to_user,
        password=options.to_password,
        database=options.to_database,
    )


def read_pairs(path: str) -> dict:
    pairs: dict = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) < 3:
            continue
        pairs.setdefault(fields[0], []).append(
            Pair(source=fields[1], translation=fields[2])
        )
    return pairs


def write_pairs(pairs: dict, path) -> None:
    lines = [
        f'{column}\t{pair.source}\t{pair.translation}'
        for column, rows in pairs.items()
        for pair in rows
    ]
    body = '\n'.join(lines) + '\n'
    if path:
        Path(path).write_text(body, encoding='utf-8')
        print(f'переводов записано в {path}: {len(lines)}', file=sys.stderr)
    else:
        sys.stdout.write(body)


def main() -> None:
    options = arguments()
    sql_text = query_text(options)
    source = get_adapter(source_params(options))

    if not options.columns and not options.auto and not options.from_tsv:
        for column in source.columns(sql_text):
            print(f'{column.name}\t{column.raw_type}\t{column.kind}')
        return

    if options.from_tsv:
        pairs = read_pairs(options.from_tsv)
        total = sum(len(rows) for rows in pairs.values())
        print(f'переводов прочитано из {options.from_tsv}: {total}', file=sys.stderr)
    else:
        if not options.url:
            raise SystemExit('нужен --url или переменная LIBRE_TRANSLATE_URL')
        glossary = load_glossary(options.glossary)
        values, rows = collect(source, sql_text, column_names(options), options.limit)
        found = ', '.join(values) or 'нет'
        print(f'строк просмотрено: {rows}, колонки: {found}', file=sys.stderr)
        result = translate(
            values,
            glossary,
            Protector(keep=glossary.keep),
            LibreTranslateClient(
                options.url, batch=options.batch, timeout=options.timeout, api_key=options.key
            ),
            rows=rows,
        )
        pairs = result.pairs
        stats = result.stats
        print(
            f'значений: {stats.values}, движок: {stats.engine}, без защиты: {stats.raw}, '
            f'глоссарий: {stats.glossary}, защищено целиком: {stats.protected}, '
            f'откат: {stats.fallback}',
            file=sys.stderr,
        )
        write_pairs(pairs, options.out)

    if not options.to_table:
        return

    applied = apply(
        source,
        get_adapter(target_params(options)),
        sql_text,
        pairs,
        options.to_table,
        batch=options.insert_batch,
    )
    print(
        f'таблица {applied.table} {applied.action}, колонок: {applied.columns}, '
        f'записано строк: {applied.written}, заменено значений: {applied.replaced}',
        file=sys.stderr,
    )


if __name__ == '__main__':
    main()
