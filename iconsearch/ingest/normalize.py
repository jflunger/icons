"""Search-text building: name tokens, tag text and synonym expansion."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

WORD_RE = re.compile(r"[a-z0-9]+")


def tokens(name: str) -> list[str]:
    """`arrow-up-right-2` -> ['arrow', 'up', 'right', '2']."""
    return WORD_RE.findall(name.lower().replace("_", "-"))


def words(texts) -> list[str]:
    """Lowercased, deduped words from free-text tags, in first-seen order."""
    seen: dict[str, None] = {}
    for t in texts:
        for w in WORD_RE.findall(str(t).lower()):
            seen.setdefault(w, None)
    return list(seen)


class Synonyms:
    def __init__(self, groups: list[list[str]]):
        self.groups = [[w.lower() for w in g] for g in groups]
        self.index: dict[str, list[int]] = {}
        for i, g in enumerate(self.groups):
            for w in g:
                for part in WORD_RE.findall(w):
                    self.index.setdefault(part, []).append(i)

    @classmethod
    def load(cls, path: Path) -> "Synonyms":
        if not path.exists():
            return cls([])
        return cls(tomllib.loads(path.read_text()).get("groups", []))

    def expand(self, present: list[str]) -> list[str]:
        """Words from every group touched by `present`, minus words already present."""
        have = set(present)
        out: dict[str, None] = {}
        for w in present:
            for gi in self.index.get(w, ()):
                for syn in words(self.groups[gi]):
                    if syn not in have:
                        out.setdefault(syn, None)
        return list(out)


def build_text(
    name: str,
    aliases: list[str],
    tags: list[str],
    categories: list[str],
    synonyms: Synonyms,
) -> tuple[str, str, str]:
    """Return (name_tokens, tags_text, synonyms_text) for the FTS index."""
    name_tok = tokens(name)
    tag_words = [w for w in words([*tags, *(a.replace("-", " ") for a in aliases), *categories])
                 if w not in name_tok]
    # Synonyms come from the name and explicit tags only: category words like
    # "system" are too broad to expand.
    syn = [w for w in synonyms.expand(words([*name_tok, *tags])) if w not in tag_words]
    return " ".join(name_tok), " ".join(tag_words), " ".join(syn)
