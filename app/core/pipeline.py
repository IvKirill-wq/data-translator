# core/pipeline.py
from core.prefilter import needs_translation, normalize_key
from core.protect import protect
from core.glossary import Glossary


def run(adapter, translator, cache, sql_text, column, domain) -> list[tuple[str, str]]:
    glossary = Glossary(domain)                       # домен — свойство колонки, 1 раз на прогон
    values = adapter.fetch_distinct(sql_text, column) # distinct-значения из БД

    # 1. Отсев: что вообще переводим
    todo = [v for v in values if needs_translation(v)]

    # 2. Кэш + whole — до движка
    results: dict[str, str] = {}
    to_engine: list[tuple[str, object]] = []          # (оригинал, ProtectedText)

    for v in todo:
        key = normalize_key(v)                        # ключ кэша — нормализованный
        if (hit := cache.get(key, domain)) is not None:
            results[v] = hit
            continue
        if (whole := glossary.lookup_whole(v)) is not None:
            results[v] = whole
            cache.set(key, domain, whole)
            continue

        protected = protect(v, glossary)
        to_engine.append((v, protected))

    # 3. Один батч-вызов движка
    if to_engine:
        masked = [p.masked for _, p in to_engine]
        translated = translator.translate(masked, ctx=domain)  # ← вот сюда ctx
        if len(translated) != len(masked):
            raise RuntimeError('движок вернул не то число строк')

        for (v, protected), tr in zip(to_engine, translated):
            key = normalize_key(v)
            if not protected.placeholders_intact(tr):
                results[v] = v 
                continue
            final = glossary.post_correct(protected.restore(tr))
            results[v] = final
            cache.set(key, domain, final)

    # 4. Возвращаем пары (оригинал, перевод)
    return [(v, results.get(v, v)) for v in values]