import re

CYRILLIC = re.compile('[а-яёА-ЯЁ]')
SPACES = re.compile(r'\s+')
THIN_SPACES = (' ', ' ', ' ', ' ', '﻿')


def needs_translation(value) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    return bool(CYRILLIC.search(value))


def normalize_key(value: str) -> str:
    text = str(value)
    for space in THIN_SPACES:
        text = text.replace(space, ' ')
    text = text.replace('ё', 'е').replace('Ё', 'Е')
    return SPACES.sub(' ', text).strip().casefold()
