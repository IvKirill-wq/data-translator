import re

QUOTED   = re.compile(r'"[^"]+"|«[^»]+»|\'[^\']+\'')
ARTICLES = re.compile(r'[А-ЯЁ]{2,}\s*\d+(?:[-–]\d+)?')
LATIN    = re.compile(r'[A-Za-z][A-Za-z0-9._\-]*')
NUMBERS  = re.compile(r'\d+(?:[.,]\d+)*')

PLACEHOLDER = "__K{}__"
PLACEHOLDER_RE = re.compile(r"__K(\d+)__")

class ProtectedText:
    def __init__(self, masked: str, mapping: dict[str, str]):
        self.masked = masked
        self.mapping = mapping

    def placeholders_intact(self, translated: str) -> bool:
        found = PLACEHOLDER_RE.findall(translated)
        return sorted(found) == sorted(k[3:-2] for k in self.mapping)

    def restore(self, translated: str) -> str:
        for key, original in self.mapping.items():
            translated = translated.replace(key, original)
        return translated


def _collect_spans(text: str, glossary) -> list[tuple[int, int]]:
    spans = []
    for rx in (QUOTED, ARTICLES, LATIN, NUMBERS):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))

    for term in glossary.keep:
        rx = re.compile(r"\b" + re.escape(term) + r"\b")
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    return spans


def _resolve_overlaps(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last_end = [], -1
    for s, e in spans:
        if s >= last_end:
            out.append((s, e))
            last_end = e
    return out


def protect(text: str, glossary) -> ProtectedText:
    spans = _resolve_overlaps(_collect_spans(text, glossary))
    if not spans:
        return ProtectedText(text, {})

    mapping: dict[str, str] = {}
    chunks: list[str] = []
    cursor = 0
    for i, (s, e) in enumerate(spans, start=1):
        key = PLACEHOLDER.format(i)
        chunks.append(text[cursor:s])
        chunks.append(key)
        mapping[key] = text[s:e]
        cursor = e
    chunks.append(text[cursor:])
    return ProtectedText("".join(chunks), mapping)