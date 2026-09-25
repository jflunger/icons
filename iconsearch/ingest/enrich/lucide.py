"""Lucide tags from the npm package `lucide-static` (package/tags.json: name -> [tags]).

lucide-static carries no categories; Iconify has none for Lucide either.
"""
from __future__ import annotations

import json

from .base import Enricher, EnrichData, clean


class LucideEnricher(Enricher):
    prefix = "lucide"
    label = "Lucide"
    package = "lucide-static"

    def load(self, client, cache_dir) -> EnrichData:
        files = self.npm_files(client, cache_dir, lambda n: n == "package/tags.json")
        tags = json.loads(files["package/tags.json"])
        return {name: {"tags": clean(t), "categories": []} for name, t in tags.items()}
