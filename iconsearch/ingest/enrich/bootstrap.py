"""Bootstrap Icons tags from GitHub `twbs/icons` docs/content/icons/*.md front matter.

The release tag comes from the npm `bootstrap-icons` version (no GitHub API
call), and the tarball from codeload.github.com. Front matter looks like:
    ---
    title: Trash
    categories:
      - UI and keyboard
    tags:
      - garbage
    ---
"""
from __future__ import annotations

import re

from .. import fetch
from .base import Enricher, EnrichData, clean

MD_RE = re.compile(r"/docs/content/icons/([^/]+)\.md$")


def parse_front_matter(text: str) -> dict[str, list[str]]:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out: dict[str, list[str]] = {"tags": [], "categories": []}
    if not m:
        return out
    key = None
    for line in m.group(1).splitlines():
        top = re.match(r"^(\w+):\s*(.*)$", line)
        if top:
            key = top.group(1) if top.group(1) in out else None
            inline = top.group(2).strip()
            if key and inline.startswith("["):
                out[key] = [s.strip(" '\"") for s in inline.strip("[]").split(",") if s.strip()]
            continue
        item = re.match(r"^\s*-\s*(.+)$", line)
        if item and key:
            out[key].append(item.group(1).strip(" '\""))
    return out


class BootstrapEnricher(Enricher):
    prefix = "bi"
    label = "Bootstrap"
    package = "bootstrap-icons"

    def load(self, client, cache_dir) -> EnrichData:
        self.version, _ = fetch.npm_latest(client, self.package)
        self.cache_file = f"twbs-icons-{self.version}.tgz"
        url = f"https://codeload.github.com/twbs/icons/tar.gz/refs/tags/v{self.version}"
        data = fetch.fetch_tarball(client, url, cache_dir, self.cache_file)
        out: EnrichData = {}
        for path, raw in fetch.read_members(data, lambda n: bool(MD_RE.search(n))).items():
            fm = parse_front_matter(raw.decode("utf-8", "replace"))
            out[MD_RE.search(path).group(1)] = {"tags": clean(fm["tags"]), "categories": clean(fm["categories"], lower=False)}
        return out
