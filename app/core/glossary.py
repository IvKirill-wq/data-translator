import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .prefilter import CYRILLIC, normalize_key

WHOLE = 'whole'
TERM = 'term'
KEEP = 'keep'
KINDS = (WHOLE, TERM, KEEP)


@dataclass(frozen=True)
class Glossary:
    whole: dict
    pre: Sequence[tuple]
    post: Sequence[tuple]
    keep: Sequence[str]

    def value_for(self, key: str) -> str | None:
        return self.whole.get(key)

    def fix_source(self, text: str) -> str:
        return replace_all(self.pre, text)

    def fix_terms(self, text: str) -> str:
        return replace_all(self.post, text)


EMPTY = Glossary({}, (), (), ())


def replace_all(rules: Sequence[tuple], text: str) -> str:
    for pattern, replacement in rules:
        text = pattern.sub(lambda match: replacement, text)
    return text


def term_pattern(source: str) -> re.Pattern:
    return re.compile(r'(?<!\w)' + re.escape(source) + r'(?!\w)', re.IGNORECASE)


def load(path) -> Glossary:
    file = Path(path)
    if not file.is_file():
        return EMPTY
    whole: dict = {}
    pre: list = []
    post: list = []
    keep: list = []
    for line in file.read_text(encoding='utf-8').splitlines():
        row = line.strip()
        if not row or row.startswith('#'):
            continue
        fields = [field.strip() for field in row.split('\t') if field.strip() != '']
        if len(fields) < 2 or fields[0] not in KINDS:
            continue
        kind, source = fields[0], fields[1]
        target = fields[2] if len(fields) > 2 else ''
        if kind == KEEP:
            keep.append(source)
        elif target and kind == WHOLE:
            whole[normalize_key(source)] = target
        elif target and kind == TERM:
            rules = pre if CYRILLIC.search(source) else post
            rules.append((term_pattern(source), target))
    return Glossary(whole, tuple(pre), tuple(post), tuple(keep))
