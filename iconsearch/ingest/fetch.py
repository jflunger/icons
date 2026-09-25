"""npm registry lookups, tarball download with a local cache, and tar helpers."""
from __future__ import annotations

import io
import logging
import re
import tarfile
import time
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger("iconsearch.fetch")

USER_AGENT = "icon-search/0.1 (personal self-hosted icon index; weekly fetch)"
REGISTRY = "https://registry.npmjs.org"


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def with_retry(fn: Callable, what: str, tries: int = 3):
    delay = 2.0
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except (httpx.HTTPError, OSError) as e:
            if attempt == tries:
                raise
            log.warning("%s failed (%s), retry %d/%d in %.0fs", what, e, attempt, tries - 1, delay)
            time.sleep(delay)
            delay *= 2


def npm_latest(client: httpx.Client, package: str) -> tuple[str, str]:
    """Return (version, tarball_url) for the latest release of an npm package."""
    def go():
        r = client.get(f"{REGISTRY}/{package}/latest")
        r.raise_for_status()
        d = r.json()
        return d["version"], d["dist"]["tarball"]
    return with_retry(go, f"npm metadata {package}")


def cache_name(package: str, version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", package.lstrip("@")) + f"-{version}.tgz"


def fetch_tarball(client: httpx.Client, url: str, cache_dir: Path, filename: str) -> bytes:
    """Download `url` into cache_dir/filename, or reuse the cached copy."""
    path = cache_dir / filename
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()

    def go():
        r = client.get(url)
        r.raise_for_status()
        return r.content
    data = with_retry(go, f"download {url}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def read_members(data: bytes, want: Callable[[str], bool]) -> dict[str, bytes]:
    """Extract matching regular files from a .tgz held in memory."""
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf:
            if m.isfile() and want(m.name):
                f = tf.extractfile(m)
                if f:
                    out[m.name] = f.read()
    return out


def prune_cache(cache_dir: Path, keep: set[str]) -> int:
    """Delete cached tarballs that are not in `keep`. Returns the number removed."""
    removed = 0
    if not cache_dir.exists():
        return 0
    for p in cache_dir.iterdir():
        if p.is_file() and p.name not in keep:
            p.unlink()
            removed += 1
    return removed
