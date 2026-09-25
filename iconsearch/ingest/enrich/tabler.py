"""Tabler tags from the npm package `@tabler/icons` (package/icons.json).

Shape: {name: {"name", "category", "tags": [...], "styles": {...}}}. Filled
icons share the outline entry, so lookup falls back to base_name.
"""
from __future__ import annotations

import json

from .base import Enricher, EnrichData, clean


class TablerEnricher(Enricher):
    prefix = "tabler"
    label = "Tabler"
    package = "@tabler/icons"

    def load(self, client, cache_dir) -> EnrichData:
        files = self.npm_files(client, cache_dir, lambda n: n == "package/icons.json")
        data = json.loads(files["package/icons.json"])
        out: EnrichData = {}
        for name, v in data.items():
            cat = v.get("category")
            out[name] = {"tags": clean(v.get("tags")), "categories": clean([cat], lower=False)}
        return out
