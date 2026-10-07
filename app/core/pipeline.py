import json
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from ..db.base import Column
from .prefilter import needs_translation, normalize_key

CREATED = 'создана'
REPLACED = 'перезаписана'


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Pair:
    source: str
    translation: str


@dataclass(frozen=True)
class Stats:
    rows: int = 0
    values: int = 0
    columns: int = 0
    engine: int = 0
    raw: int = 0
    glossary: int = 0
    protected: int = 0
    fallback: int = 0
    timeout: int = 0


@dataclass(frozen=True)
class Translated:
    pairs: dict = field(default_factory=dict)
    stats: Stats = Stats()


@dataclass(frozen=True)
class Applied:
    written: int
    replaced: int
    action: str
    table: str
    columns: int


def as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str, sort_keys=False)
        except TypeError:
            return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def column_index(columns: Sequence[Column], name: str) -> int:
    for index, column in enumerate(columns):
        if column.name == name:
            return index
    available = ', '.join(column.name for column in columns)
    raise PipelineError(f'в результате запроса нет колонки {name!r}; есть: {available}')


def collect(adapter, sql_text: str, columns: Sequence[str], limit: int) -> tuple[dict, int]:
    values: dict = {}
    rows = 0
    total = 0
    with adapter.rows(sql_text) as stream:
        available = [column.name for column in stream.columns]
        chosen = list(columns) if columns else list(available)
        targets = [(name, column_index(stream.columns, name)) for name in chosen]
        for name, _ in targets:
            values.setdefault(name, {})
        for row in stream:
            rows += 1
            for name, index in targets:
                raw = row[index]
                if raw is None:
                    continue
                text = as_text(raw)
                if not needs_translation(text):
                    continue
                key = normalize_key(text)
                bucket = values[name]
                if key in bucket:
                    continue
                if total >= limit:
                    raise PipelineError(
                        f'уникальных значений для перевода больше {limit}: '
                        'сузьте запрос, выберите меньше колонок или поднимите '
                        'MAX_RU_DISTINCT_QTY'
                    )
                bucket[key] = text
                total += 1
    return {name: bucket for name, bucket in values.items() if bucket}, rows


def decide(values: dict, glossary, protector, translator) -> tuple[dict, dict]:
    decided: dict = {}
    queue: list = []
    counters = {'engine': 0, 'raw': 0, 'glossary': 0, 'protected': 0, 'fallback': 0,
                'timeout': 0}

    for key in values:
        whole = glossary.value_for(key)
        if whole:
            decided[key] = whole
            counters['glossary'] += 1
            continue
        queue.append(key)

    masked: list = []
    parts_by_key: dict = {}
    prepared_by_key: dict = {}
    for key in queue:
        prepared = glossary.fix_source(values[key])
        prepared_by_key[key] = prepared
        text, parts = protector.mask(prepared)
        if not protector.has_translatable_rest(text):
            decided[key] = prepared
            counters['protected'] += 1
            continue
        masked.append((key, text))
        parts_by_key[key] = parts

    if masked:
        answers = translator.translate([text for _, text in masked])
        lost: list = []
        for (key, _), answer in zip(masked, answers):
            if answer is None:
                decided[key] = prepared_by_key[key]
                counters['timeout'] += 1
                continue
            restored = protector.unmask(glossary.fix_terms(answer), parts_by_key[key])
            if restored is None or not restored.strip():
                lost.append(key)
                continue
            decided[key] = restored.strip()
            counters['engine'] += 1
        if lost:
            plain = translator.translate([prepared_by_key[key] for key in lost])
            for key, answer in zip(lost, plain):
                if answer is None:
                    decided[key] = prepared_by_key[key]
                    counters['timeout'] += 1
                    continue
                text = glossary.fix_terms(answer).strip()
                if not text:
                    decided[key] = prepared_by_key[key]
                    counters['fallback'] += 1
                    continue
                decided[key] = text
                counters['raw'] += 1

    missing = [key for key in values if key not in decided]
    if missing:
        raise PipelineError(
            f'движок перевода вернул меньше значений, чем принял: {len(missing)} без ответа'
        )
    return decided, counters


def translate(
    values_by_column: dict, glossary, protector, translator, rows: int = 0
) -> Translated:
    merged: dict = {}
    for bucket in values_by_column.values():
        for key, text in bucket.items():
            merged.setdefault(key, text)

    decided, counters = decide(merged, glossary, protector, translator)

    pairs: dict = {}
    for name, bucket in values_by_column.items():
        rendered = [
            Pair(source=bucket[key], translation=decided[key])
            for key in bucket
        ]
        rendered.sort(key=lambda pair: pair.source.casefold())
        pairs[name] = rendered

    stats = Stats(rows=rows, values=len(merged), columns=len(values_by_column), **counters)
    return Translated(pairs=pairs, stats=stats)


def target_columns(columns: Sequence[Column], indexes) -> list[Column]:
    chosen = set(indexes)
    return [
        column.as_text() if position in chosen else column
        for position, column in enumerate(columns)
    ]


def prepare_target(adapter, table: str, columns: Sequence[Column]) -> str:
    existing = adapter.table_columns(table)
    if existing is None:
        adapter.create_table(table, columns)
        return CREATED
    found = [column.name for column in existing]
    wanted = [column.name for column in columns]
    if found != wanted:
        raise PipelineError(
            f'таблица {table} уже есть, и её колонки другие. '
            f'В таблице: {", ".join(found)}. В результате запроса: {", ".join(wanted)}'
        )
    return REPLACED


def mapped_rows(stream, mappings: dict, columns: Sequence[Column], hits: list) -> Iterator[tuple]:
    flags = [column.is_text for column in columns]
    width = len(flags)
    for row in stream:
        values = list(row)
        if len(values) != width:
            raise PipelineError(
                f'строка результата содержит {len(values)} значений вместо {width}'
            )
        for index, mapping in mappings.items():
            raw = values[index]
            if raw is None:
                continue
            text = as_text(raw)
            translation = mapping.get(normalize_key(text))
            if translation is None:
                values[index] = text
            else:
                values[index] = translation
                hits[0] += 1
        for position, is_text in enumerate(flags):
            value = values[position]
            if is_text and value is not None and not isinstance(value, str):
                values[position] = as_text(value)
        yield tuple(values)


def apply(
    source_adapter,
    target_adapter,
    sql_text: str,
    pairs_by_column: dict,
    table: str,
    batch: int = 10_000,
) -> Applied:
    by_column: dict = {}
    for name, pairs in pairs_by_column.items():
        mapping: dict = {}
        for pair in pairs:
            translation = (pair.translation or '').strip()
            if not translation:
                continue
            mapping[normalize_key(pair.source)] = translation
        if mapping:
            by_column[name] = mapping
    if not by_column:
        raise PipelineError('нет ни одного заполненного перевода — записывать нечего')

    hits = [0]
    with source_adapter.rows(sql_text) as stream:
        columns = list(stream.columns)
        mappings = {column_index(columns, name): mapping for name, mapping in by_column.items()}
        target = target_adapter.fit_columns(target_columns(columns, mappings))
        action = prepare_target(target_adapter, table, target)
        written = target_adapter.insert(
            table,
            target,
            mapped_rows(stream, mappings, target, hits),
            batch,
            clear=action == REPLACED,
        )

    return Applied(
        written=written,
        replaced=hits[0],
        action=action,
        table=table,
        columns=len(by_column),
    )
