from dataclasses import dataclass
from pathlib import Path
import re
import yaml

GLOSSARY_DIR = Path(__file__).resolve().parent.parent/'glossaries'

@dataclass (frozen=True)
class Entry:
    src: str
    dst: str | None = None
    kind: str

class Glossary:
    def __init__(self, domain:str):
        self.domain = domain
        self.entries: list[Entry] = self._load(domain)

        self.keep: list[str] = [e.src for e in self.entries if e.kind == 'keep']

        self.whole: dict[str, str] = {
            e.src: e.dst for e in self.entries 
            if e.kind == 'whole' and e.dst
            }

        self._terms: list[tuple[re.Pattern, str]] = [
            (self._word_re(e.src), e.dst)
            for e in self.entries
            if e.kind == 'terms' and e.dst
        ]

    def _load(self, domain:str) -> list[Entry]:
        path = GLOSSARY_DIR / f'{domain}.yaml'
        if not path.exists():
            return []
        
        with path.open(encoding='utf-8') as f:
            raw = yaml.safe_load(f) or []

        return [Entry(**item) for item in raw]

    def lookup_whole(self, value:str):
        return self.whole.get(value.strip())

    @staticmethod
    def _word_re(src:str):
        return re.compile(r'\b' + re.escape(src) + r'\b', re.IGNORECASE)

    def post_correct(self, text:str):
        for pattern, dst in self._terms:
            text = pattern.sub(dst, text)
        return text

    def add_entry(self, entry:Entry) -> None:
        self.entries.append(entry)