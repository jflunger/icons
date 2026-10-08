"""Shared fixtures: a tiny fake Iconify package ingested into a temp data dir.

ICONSEARCH_CONFIG is set before iconsearch.server is first imported, because
the server reads its config at import time.
"""
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE_ICONS = {
    "prefix": "fake",
    "width": 24, "height": 24,
    "icons": {
        "arrow-left": {"body": '<path fill="none" stroke="currentColor" stroke-width="2" d="M19 12H5m7 7l-7-7l7-7"/>'},
        "chevron": {"body": '<path fill="currentColor" d="M9 6l6 6l-6 6z"/>', "width": 16, "height": 16},
        "trash": {"body": '<path fill="currentColor" d="M3 6h18v2H3z"/>'},
        "secret": {"body": '<path d="M0 0h1v1H0z"/>', "hidden": True},
    },
    "aliases": {
        "arrow-thing": {"parent": "chevron"},
        "chevron-flipped": {"parent": "chevron", "hFlip": True},
    },
}
FAKE_INFO = {"name": "Fake Icons", "author": {"name": "Tester"},
             "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.com/LICENSE"}}
FAKE_META = {"categories": {"Navigation": ["arrow-left", "chevron"]}}


def make_tarball(files: dict[str, object]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, obj in files.items():
            data = json.dumps(obj).encode()
            ti = tarfile.TarInfo(f"package/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture(scope="session")
def fake_tarball():
    return make_tarball({"icons.json": FAKE_ICONS, "info.json": FAKE_INFO, "metadata.json": FAKE_META})


@pytest.fixture(scope="session")
def data_env(tmp_path_factory, fake_tarball):
    """Run the real ingest against the fake package; return the config path."""
    tmp = tmp_path_factory.mktemp("iconsearch")
    cfg_path = tmp / "config.toml"
    cfg_path.write_text('[ingest]\ndata_dir = "data"\nschedule = "off"\n[sets.fake]\nenabled = true\n')
    (tmp / "synonyms.toml").write_text('groups = [["trash", "delete", "bin"]]\n')

    from iconsearch.ingest import __main__ as ingest_main
    from iconsearch.ingest import fetch

    mp = pytest.MonkeyPatch()
    mp.setattr(fetch, "npm_latest", lambda client, pkg: ("1.0.0", "https://example.invalid/fake.tgz"))
    mp.setattr(fetch, "fetch_tarball", lambda client, url, cache_dir, name: fake_tarball)
    mp.setattr(ingest_main, "fetch_license_text", lambda client, url: "MIT License text")
    rc = ingest_main.main(["--config", str(cfg_path), "--no-enrich"])
    assert rc == 0
    rc2 = ingest_main.main(["--config", str(cfg_path), "--no-enrich"])
    assert rc2 == 0  # second run: "up to date"
    mp.undo()

    os.environ["ICONSEARCH_CONFIG"] = str(cfg_path)
    os.environ["ICONSEARCH_NO_SCHEDULER"] = "1"
    os.environ["ICONSEARCH_CART_PASSWORD"] = "pw"
    return cfg_path


@pytest.fixture(scope="session")
def client(data_env):
    from fastapi.testclient import TestClient
    from iconsearch import server
    with TestClient(server.app) as c:
        yield c
