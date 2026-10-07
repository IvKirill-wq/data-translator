import argparse
import os
import statistics
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.core.glossary import load as load_glossary
from app.core.prefilter import needs_translation, normalize_key
from app.core.protect import Protector
from app.core.translate_client import LibreTranslateClient, TranslationError


def arguments():
    parser = argparse.ArgumentParser(
        description='Замер скорости движка перевода на ваших значениях'
    )
    parser.add_argument('--url', default=os.environ.get('LIBRE_TRANSLATE_URL', ''))
    parser.add_argument('--key', default=os.environ.get('LIBRE_TRANSLATE_KEY', ''))
    parser.add_argument('--file', required=True)
    parser.add_argument('--tsv', action='store_true')
    parser.add_argument('--glossary', default=str(BASE_DIR / 'glossary.tsv'))
    parser.add_argument('--batch', type=int, default=24)
    parser.add_argument('--chars', type=int, default=1200)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--limit', type=int, default=60)
    parser.add_argument('--top', type=int, default=10)
    return parser.parse_args()


def read_values(options) -> list:
    lines = Path(options.file).read_text(encoding='utf-8').splitlines()
    values = []
    for line in lines:
        if not line.strip():
            continue
        text = line.split('\t')[1] if options.tsv and line.count('\t') >= 2 else line
        if needs_translation(text):
            values.append(text)
    unique = {}
    for text in values:
        unique.setdefault(normalize_key(text), text)
    return list(unique.values())


def main() -> None:
    options = arguments()
    if not options.url:
        raise SystemExit('нужен --url или переменная LIBRE_TRANSLATE_URL')

    glossary = load_glossary(options.glossary)
    protector = Protector(keep=glossary.keep)
    client = LibreTranslateClient(
        options.url, batch=options.batch, chars=options.chars,
        timeout=options.timeout, api_key=options.key,
    )

    values = read_values(options)[:options.limit]
    if not values:
        raise SystemExit('в файле нет значений с кириллицей')
    prepared = [protector.mask(glossary.fix_source(text))[0] for text in values]

    print(f'значений: {len(values)}, символов: {sum(len(t) for t in prepared)}')
    print('прогрев...', flush=True)
    client.translate(['Проверка связи'])

    rows = []
    for text, masked in zip(values, prepared):
        started = time.monotonic()
        try:
            answer = client.translate([masked])[0]
        except TranslationError as error:
            answer, spent = f'ОШИБКА: {error}', time.monotonic() - started
        else:
            spent = time.monotonic() - started
        rows.append((spent, len(masked), text, answer))
        print(f'  {spent:6.2f} с  {len(masked):5} симв.  {text[:60]}', flush=True)

    times = [row[0] for row in rows]
    chars = sum(row[1] for row in rows)
    print()
    print(f'итого по одному: {sum(times):.1f} с на {len(rows)} значений')
    print(f'среднее {statistics.mean(times):.2f} с, медиана {statistics.median(times):.2f} с, '
          f'максимум {max(times):.2f} с')
    print(f'пропускная способность: {chars / sum(times):.0f} символов/с')
    print()
    print(f'самые медленные {options.top}:')
    for spent, length, text, answer in sorted(rows, reverse=True)[:options.top]:
        print(f'  {spent:6.2f} с  {length:5} симв.  {text[:70]}')
        print(f'          -> {str(answer)[:70]}')

    print()
    batch = prepared[:options.batch]
    started = time.monotonic()
    try:
        client.translate(batch)
    except TranslationError as error:
        print(f'батч из {len(batch)} значений: ОШИБКА {error}')
    else:
        spent = time.monotonic() - started
        budget = sum(len(t) for t in batch)
        print(f'батч из {len(batch)} значений ({budget} символов): {spent:.1f} с')
        if spent:
            print(f'оценка на 1000 значений такого размера: '
                  f'{spent / len(batch) * 1000 / 60:.1f} мин')
        if spent > options.timeout * 0.5:
            print(f'[!] батч съедает больше половины таймаута ({options.timeout} с) — '
                  f'уменьшайте TRANSLATION_CHARS')


if __name__ == '__main__':
    main()
