import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .prefilter import normalize_key

WHOLE = 'whole'
BEFORE = 'before'
AFTER = 'after'
KEEP = 'keep'
SECTIONS = (WHOLE, BEFORE, AFTER, KEEP)


class GlossaryError(RuntimeError):
    pass


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
    head = r'(?<!\w)' if source[:1].isalnum() else ''
    tail = r'(?!\w)' if source[-1:].isalnum() else ''
    return re.compile(head + re.escape(source) + tail, re.IGNORECASE)


def ordered(rules: Sequence[tuple]) -> tuple:
    longest_first = sorted(rules, key=lambda rule: -rule[0])
    return tuple((pattern, target) for _, pattern, target in longest_first)


def reader() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(
        delimiters=('=',),
        comment_prefixes=('#', ';'),
        inline_comment_prefixes=None,
        allow_no_value=True,
        strict=False,
    )
    parser.optionxform = str
    return parser


def pairs(parser, section: str, problems: list) -> list:
    if not parser.has_section(section):
        return []
    found = []
    for source, target in parser.items(section):
        name = source.strip()
        if not name:
            continue
        if target is None or not target.strip():
            problems.append(f'[{section}] «{name}» — нет перевода после знака =')
            continue
        found.append((name, target.strip()))
    return found


def load(path) -> Glossary:
    file = Path(path)
    if not file.is_file():
        return EMPTY
    expected = ', '.join(f'[{name}]' for name in SECTIONS)
    parser = reader()
    try:
        parser.read_string(file.read_text(encoding='utf-8'), source=file.name)
    except configparser.MissingSectionHeaderError as error:
        raise GlossaryError(
            f'глоссарий {file.name}, строка {error.lineno}: правило должно лежать '
            f'внутри раздела — {expected}'
        ) from error
    except configparser.Error as error:
        raise GlossaryError(f'глоссарий {file.name}: {error}') from error

    problems = [
        f'раздел [{name}] не распознан, бывают только {expected}'
        for name in parser.sections()
        if name not in SECTIONS
    ]

    whole = {normalize_key(source): target for source, target in pairs(parser, WHOLE, problems)}
    pre = [(len(source), term_pattern(source), target)
           for source, target in pairs(parser, BEFORE, problems)]
    post = [(len(source), term_pattern(source), target)
            for source, target in pairs(parser, AFTER, problems)]
    keep = [source.strip() for source, _ in parser.items(KEEP)] if parser.has_section(KEEP) else []

    if problems:
        raise GlossaryError(f'глоссарий {file.name}: ' + '; '.join(problems))
    return Glossary(whole, ordered(pre), ordered(post), tuple(source for source in keep if source))
