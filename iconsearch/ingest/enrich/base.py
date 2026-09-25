"""Enricher interface: optional per-set tag/category sources that fail soft."""
from __future__ import annotations

from pathlib import Path

import httpx

from .. import fetch

# name -> {"tags": [...], "categories": [...]}
EnrichData = dict[str, dict[str, list[str]]]


class Enricher:
    prefix: str = ""          # Iconify prefix this enricher applies to
    label: str = ""
    package: str = ""         # npm package holding the metadata, if any

    def __init__(self):
        self.version = ""
        self.cache_file = ""

    def load(self, client: httpx.Client, cache_dir: Path) -> EnrichData:
        raise NotImplementedError

    def keys(self, name: str, base_name: str, aliases: list[str]) -> list[str]:
        """Names to try, in order, when looking up an icon in the loaded data."""
        return [name, base_name, *aliases]

    # helper for npm-hosted metadata
    def npm_files(self, client: httpx.Client, cache_dir: Path, want) -> dict[str, bytes]:
        self.version, url = fetch.npm_latest(client, self.package)
        self.cache_file = fetch.cache_name(self.package, self.version)
        data = fetch.fetch_tarball(client, url, cache_dir, self.cache_file)
        return fetch.read_members(data, want)


def clean(values, lower: bool = True) -> list[str]:
    out: dict[str, None] = {}
    for v in values or []:
        s = str(v).strip()
        s = s.lower() if lower else s
        if s and s != "*new*":
            out.setdefault(s, None)
    return list(out)
