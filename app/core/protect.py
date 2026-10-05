import re
from typing import Sequence

from .prefilter import CYRILLIC

MARKER = 'QQ{}QQ'
QUOTED = r'«[^»]*»|“[^”]*”|„[^“”]*[“”]|"[^"]*"'
LATIN = r'[A-Za-z][A-Za-z0-9]*(?:[._/\\-][A-Za-z0-9]+)*'
NUMBER = r'\d+(?:[.,:/-]\d+)*\s*%?'
JOIN = r'[ \t]*[-–—/,.:]?[ \t]*'


class Protector:
    def __init__(
        self,
        keep: Sequence[str] = (),
        marker: str = MARKER,
        numbers: bool = False,
    ):
        self._marker = marker
        self._keep = tuple(sorted({term for term in keep if term}, key=len, reverse=True))
        self._pattern = self._build(numbers)
        self._finder = re.compile(
            re.escape(marker).replace(r'\{\}', r'(\d+)'), re.IGNORECASE
        )

    def _build(self, numbers: bool) -> re.Pattern:
        parts = []
        if self._keep:
            terms = '|'.join(re.escape(term) for term in self._keep)
            parts.append(r'(?<!\w)(?:' + terms + r')(?!\w)')
        parts.append(QUOTED)
        parts.append(LATIN)
        if numbers:
            parts.append(NUMBER)
        token = '(?:' + '|'.join(parts) + ')'
        return re.compile(token + '(?:' + JOIN + token + ')*')

    def mask(self, value: str) -> tuple[str, list[str]]:
        parts: list[str] = []

        def swap(match: re.Match) -> str:
            parts.append(match.group(0))
            return self._marker.format(len(parts))

        return self._pattern.sub(swap, value), parts

    def unmask(self, value: str, parts: Sequence[str]) -> str | None:
        if not parts:
            return value
        counts = [0] * len(parts)

        def swap(match: re.Match) -> str:
            index = int(match.group(1))
            if not 1 <= index <= len(parts):
                raise KeyError(index)
            counts[index - 1] += 1
            return parts[index - 1]

        try:
            restored = self._finder.sub(swap, value)
        except KeyError:
            return None
        if any(count != 1 for count in counts):
            return None
        return restored

    def has_translatable_rest(self, masked: str) -> bool:
        return bool(CYRILLIC.search(masked))
