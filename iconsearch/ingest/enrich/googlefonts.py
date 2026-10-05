"""Material Symbols tags from Google Fonts (fonts.google.com/metadata/icons).

The response starts with an XSSI guard line (")]}'"), then JSON:
{"families": [...], "icons": [{"name": "10k", "tags": [...], "categories": [...],
"unsupported_families": [...], ...}]}. Names use underscores where Iconify uses
hyphens. Categories are skipped because Iconify's metadata already has the same
ones with different spelling ("Audio&Video" vs "Audio & Video").

This is not an npm package, so it has no version. Each build fetches it fresh and
falls back to the last cached copy if that fails.

Google's tags are generous (~35 per icon), and the common ones are noise: "home" is
on ~300 icons and "arrow" on ~600. Tags on more than MAX_TAG_SHARE of icons are
dropped, along with style words that would match every icon.
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from .. import fetch
from .base import Enricher, EnrichData, clean

log = logging.getLogger("iconsearch.enrich")

URL = "https://fonts.google.com/metadata/icons?key=material_symbols&incomplete=true"
CACHE_FILE = "google-fonts-icons.json"
MAX_TAG_SHARE = 0.03
STYLE_WORDS = {"fill", "filled", "outline", "outlined", "solid", "stroke", "rounded", "sharp", "icon", "symbol"}


class GoogleFontsEnricher(Enricher):
    prefix = "material-symbols"
    label = "Google Fonts"
    package = "fonts.google.com/metadata/icons"

    def load(self, client, cache_dir) -> EnrichData:
        path = cache_dir / CACHE_FILE

        def go():
            r = client.get(URL)
            r.raise_for_status()
            return r.text
        try:
            text = fetch.with_retry(go, "Google Fonts icon metadata")
            parse(text)  # don't cache a response we can't read
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_text(text)
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            if not path.exists():
                raise
            log.warning("Google Fonts metadata fetch failed (%s), using cached copy", e)
            text = path.read_text()
        self.cache_file = CACHE_FILE
        data = parse(text)
        self.version = str(max((i.get("version", 0) for i in data["icons"]), default=0))
        icons = {i["name"].replace("_", "-"): clean(i.get("tags")) for i in data["icons"]
                 if "Material Symbols Outlined" not in i.get("unsupported_families", [])}
        df = Counter(t for tags in icons.values() for t in tags)
        limit = MAX_TAG_SHARE * len(icons)
        return {name: {"tags": [t for t in tags if df[t] <= limit and t not in STYLE_WORDS], "categories": []}
                for name, tags in icons.items()}


def parse(text: str) -> dict:
    return json.loads(text[text.index("{"):])
