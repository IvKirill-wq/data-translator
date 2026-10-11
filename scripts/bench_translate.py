import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
    parser.add_argument('--glossary', default=str(BASE_DIR / 'glossary.ini'))
    parser.add_argument('--batch', type=int, default=24)
    parser.add_argument('--chars', type=int, default=1200)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--limit', type=int, default=60)
    parser.add_argument('--top', type=int, default=10)
    parser.add_argument('--jobs', default='1')
    return parser.parse_args()


def job_counts(text: str) -> list:
    counts = []
    for part in (text or '').split(','):
        part = part.strip()
        if part.isdigit() and int(part) > 0 and int(part) not in counts:
            counts.append(int(part))
    return counts or [1]


def measure(client, values: list, jobs: int) -> float:
    groups = [values[index::jobs] for index in range(jobs)]
    groups = [group for group in groups if group]
    started = time.monotonic()
    if len(groups) == 1:
        client.translate(groups[0])
    else:
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            list(pool.map(client.translate, groups))
    return time.monotonic() - started


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

    counts = job_counts(options.jobs)
    if counts != [1]:
        print()
        print('--- параллельные запросы к движку ---')
        timings = {}
        single = 0.0
        for jobs in counts:
            spent = measure(client, prepared, jobs)
            timings[jobs] = spent
            single = single or spent
            speed = len(prepared) / spent * 60 if spent else 0
            note = f'   ускорение {single / spent:.1f}x' if spent else ''
            print(f'  потоков {jobs}: {spent:.1f} с, {speed:.0f} значений/мин{note}')
        best = min(timings, key=lambda jobs: timings[jobs])
        gain = single / timings[best] if timings[best] else 1.0
        print()
        if gain >= 1.4:
            print(f'[+] движок недогружен: {best} параллельных запросов дают ~{gain:.1f}x. '
                  'Поднимайте ARGOS_INTER_THREADS и слайте батчи параллельно')
        else:
            print('[=] движок уже занимает все ядра: параллелизм и второй экземпляр '
                  'не помогут, помогут ARGOS_BEAM_SIZE=1, ARGOS_COMPUTE_TYPE=int8 '
                  'и больше ядер')

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
