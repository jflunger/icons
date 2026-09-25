"""Phosphor tags from the npm package `@phosphor-icons/core` (dist/index.mjs).

The metadata only ships as a JS module, so this regex-extracts it rather than
requiring Node: each entry looks like
    { name: "acorn", pascal_name: ..., categories: [e.FINANCE, e.NATURE],
      figma_category: a.WEATHER, tags: ["savings", ...], ... }
and the `e` enum near the top maps FINANCE -> "finances". If the format changes
the match rate logged by the ingest drops, which is the signal to revisit this.
"""
from __future__ import annotations

import re

from .base import Enricher, EnrichData, clean

ENUM_ITEM_RE = re.compile(r"\w\.(\w+)\s*=\s*\"([^\"]*)\"")
ENTRY_RE = re.compile(r"\{\s*name:\s*\"([^\"]+)\"(.*?)\n\s*\}", re.S)
CATS_RE = re.compile(r"\bcategories:\s*\[([^\]]*)\]")
TAGS_RE = re.compile(r"\btags:\s*\[([^\]]*)\]")
STR_RE = re.compile(r"\"([^\"]*)\"")


class PhosphorEnricher(Enricher):
    prefix = "ph"
    label = "Phosphor"
    package = "@phosphor-icons/core"

    def load(self, client, cache_dir) -> EnrichData:
        files = self.npm_files(client, cache_dir, lambda n: n == "package/dist/index.mjs")
        return parse_module(files["package/dist/index.mjs"].decode())

    def keys(self, name, base_name, aliases):
        return [base_name, name, *aliases]


def parse_module(src: str) -> EnrichData:
    # Enum variables are single letters; collect "var.KEY" -> value for each.
    enums: dict[str, str] = {}
    head = src[: src.find("const ")] if "const " in src else src[:5000]
    for m in re.finditer(r"(\w)\s*=\s*/\*\s*@__PURE__\s*\*/\s*\(\((\w)\)\s*=>\s*\((.*?)\)\)\(\1\s*\|\|\s*\{\}\)", head, re.S):
        var = m.group(1)
        for k, v in ENUM_ITEM_RE.findall(m.group(3)):
            enums[f"{var}.{k}"] = v

    out: EnrichData = {}
    for m in ENTRY_RE.finditer(src):
        name, rest = m.group(1), m.group(2)
        tags_m, cats_m = TAGS_RE.search(rest), CATS_RE.search(rest)
        tags = STR_RE.findall(tags_m.group(1)) if tags_m else []
        cats = []
        if cats_m:
            for ref in (c.strip() for c in cats_m.group(1).split(",")):
                if ref:
                    cats.append(enums.get(ref, ref.split(".")[-1].lower()))
        out[name] = {"tags": clean(tags), "categories": clean(cats, lower=False)}
    return out
