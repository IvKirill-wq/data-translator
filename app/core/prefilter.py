import re

CYRILLIC = re.compile(r'[а-яёА-ЯЁ]')


def needs_translation(value):
    if value is None:
        return False
    if not value.strip():
        return False
    if not CYRILLIC.search(value):
        return False
    return True


def normalize_key(value):
    v = value.replace(' ', ' ')
    v = v.replace('ё', 'е').replace('Ё', 'Е')
    v = re.sub(r'\s+', ' ', v).strip()
    return v.casefold()