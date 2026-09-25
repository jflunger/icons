"""Parse an @iconify-json/<prefix> package (IconifyJSON format)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import fetch


@dataclass
class RawIcon:
    name: str
    body: str
    width: float
    height: float
    aliases: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass
class IconSet:
    prefix: str
    version: str
    info: dict
    icons: list[RawIcon]
    suffixes: dict[str, str]
    hidden: int = 0
    skipped_aliases: int = 0

    @property
    def name(self) -> str:
        return self.info.get("name") or self.prefix

    @property
    def license(self) -> dict:
        return self.info.get("license") or {}

    @property
    def author(self) -> dict:
        return self.info.get("author") or {}


def package_name(prefix: str) -> str:
    return f"@iconify-json/{prefix}"


def parse_tarball(prefix: str, version: str, data: bytes) -> IconSet:
    files = fetch.read_members(
        data, lambda n: n in ("package/icons.json", "package/info.json", "package/metadata.json")
    )
    if "package/icons.json" not in files:
        raise ValueError(f"{prefix}: package/icons.json missing from tarball")
    icons_json = json.loads(files["package/icons.json"])
    info = json.loads(files.get("package/info.json", b"{}"))
    metadata = json.loads(files.get("package/metadata.json", b"{}"))
    return parse(prefix, version, icons_json, info, metadata)


def parse(prefix: str, version: str, icons_json: dict, info: dict, metadata: dict) -> IconSet:
    default_w = icons_json.get("width") or info.get("width")
    default_h = icons_json.get("height") or info.get("height")

    icons: dict[str, RawIcon] = {}
    hidden = 0
    for name, v in icons_json.get("icons", {}).items():
        if v.get("hidden"):
            hidden += 1
            continue
        w = v.get("width") or default_w or 16
        h = v.get("height") or default_h or w or 16
        icons[name] = RawIcon(name=name, body=v["body"], width=float(w), height=float(h))

    # Aliases are folded into their parent as extra search tokens. Transformed
    # aliases (rotate/flip) would need their own body, so v1 skips them.
    skipped = 0
    aliases = icons_json.get("aliases", {})
    for alias, v in aliases.items():
        if set(v) - {"parent"}:
            skipped += 1
            continue
        parent, seen = v.get("parent"), set()
        while parent in aliases and parent not in seen:  # alias of an alias
            seen.add(parent)
            parent = aliases[parent].get("parent")
        if parent in icons:
            icons[parent].aliases.append(alias)

    categories = icons_json.get("categories") or metadata.get("categories") or {}
    for cat, names in categories.items():
        for n in names:
            if n in icons and cat not in icons[n].categories:
                icons[n].categories.append(cat)

    suffixes = icons_json.get("suffixes") or metadata.get("suffixes") or {}
    return IconSet(
        prefix=prefix, version=version, info=info, icons=list(icons.values()),
        suffixes=suffixes, hidden=hidden, skipped_aliases=skipped,
    )
