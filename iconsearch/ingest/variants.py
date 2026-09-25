"""Derive (base_name, variant, size) from flattened Iconify icon names.

Known sets use curated rules (verified against the 2026-09 packages) so variant
names stay short and consistent. Unknown sets fall back to the package's
`suffixes` map, then to "default".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_VARIANTS = ("regular", "outline", "default")


@dataclass(frozen=True)
class Variant:
    base_name: str
    variant: str
    size: int | None = None


# prefix -> (ordered [(suffix, variant)], variant when no suffix matches)
RULES: dict[str, tuple[list[tuple[str, str]], str]] = {
    "ph": ([("-duotone", "duotone"), ("-thin", "thin"), ("-light", "light"),
            ("-bold", "bold"), ("-fill", "fill")], "regular"),
    "tabler": ([("-filled", "filled")], "outline"),
    "heroicons": ([("-20-solid", "mini"), ("-16-solid", "micro"), ("-solid", "solid")], "outline"),
    "bi": ([("-fill", "fill")], "outline"),
    "iconoir": ([("-solid", "solid")], "regular"),
}

FLUENT_RE = re.compile(r"^(?P<base>.+)-(?P<size>\d+)-(?P<variant>regular|filled|light|color)$")


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "default"


def detect(prefix: str, name: str, suffixes: dict[str, str] | None = None) -> Variant:
    if prefix == "fluent":
        m = FLUENT_RE.match(name)
        if m:
            return Variant(m["base"], m["variant"], int(m["size"]))
        return Variant(name, "regular")

    if prefix in RULES:
        rules, default = RULES[prefix]
        for suffix, variant in rules:
            if name.endswith(suffix) and len(name) > len(suffix):
                return Variant(name[: -len(suffix)], variant)
        return Variant(name, default)

    if suffixes:
        for key in sorted((k for k in suffixes if k), key=len, reverse=True):
            if name.endswith("-" + key) and len(name) > len(key) + 1:
                return Variant(name[: -len(key) - 1], _slug(suffixes[key]))
        if "" in suffixes:
            return Variant(name, _slug(suffixes[""]))
    return Variant(name, "default")


def filter_sizes(items: list[tuple[str, Variant]], keep_sizes: list[int]) -> set[str]:
    """Names to keep when a set ships the same icon at several pixel sizes.

    For each (base_name, variant), keep the sizes in `keep_sizes`; if none of
    them exist, keep the single size nearest to keep_sizes[0] so no icon is lost.
    """
    if not keep_sizes:
        return {n for n, _ in items}
    groups: dict[tuple[str, str], list[tuple[str, Variant]]] = {}
    for name, v in items:
        groups.setdefault((v.base_name, v.variant), []).append((name, v))
    keep: set[str] = set()
    target = keep_sizes[0]
    for members in groups.values():
        sized = [(n, v) for n, v in members if v.size is not None]
        keep.update(n for n, v in members if v.size is None)
        if not sized:
            continue
        exact = [n for n, v in sized if v.size in keep_sizes]
        if exact:
            keep.update(exact)
        else:
            keep.add(min(sized, key=lambda nv: (abs(nv[1].size - target), -nv[1].size))[0])
    return keep
