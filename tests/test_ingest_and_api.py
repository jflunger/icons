import io
import json
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def db_path(cfg_path):
    return Path(cfg_path).parent / "data" / "icons.db"


def test_fixture_ingest_contents(data_env):
    conn = sqlite3.connect(db_path(data_env))
    rows = {r[0]: r for r in conn.execute("SELECT id, width, height, tags_text, category, synonyms_text FROM icons")}
    assert set(rows) == {"fake:arrow-left", "fake:chevron", "fake:trash"}  # hidden skipped
    assert rows["fake:chevron"][1:3] == (16.0, 16.0)                        # icon dims
    assert rows["fake:arrow-left"][1:3] == (24.0, 24.0)                     # set default dims
    assert "arrow thing" in rows["fake:chevron"][3]                          # alias folded in
    assert "flipped" not in rows["fake:chevron"][3]                          # transformed alias skipped
    assert rows["fake:chevron"][4] == "Navigation"
    assert "delete" in rows["fake:trash"][5]                                 # synonyms
    s = conn.execute("SELECT name, version, license_spdx, total, license_text FROM sets").fetchone()
    assert s == ("Fake Icons", "1.0.0", "MIT", 3, "MIT License text")
    state = json.loads((Path(data_env).parent / "data" / "state.json").read_text())
    assert state["sets"] == {"fake": "1.0.0"}


def test_search_ranks_name_matches_before_tag_matches(client):
    ids = [i["id"] for i in client.get("/api/search", params={"q": "arrow"}).json()["items"]]
    assert ids == ["fake:arrow-left", "fake:chevron"]


def test_search_prefix_and_synonym(client):
    assert [i["id"] for i in client.get("/api/search", params={"q": "arr"}).json()["items"]][0] == "fake:arrow-left"
    assert [i["id"] for i in client.get("/api/search", params={"q": "delete"}).json()["items"]] == ["fake:trash"]


def test_search_filters_and_browse(client):
    d = client.get("/api/search").json()
    assert d["total"] == 3
    d = client.get("/api/search", params={"category": "Navigation"}).json()
    assert {i["id"] for i in d["items"]} == {"fake:arrow-left", "fake:chevron"}
    assert client.get("/api/search", params={"providers": "nope"}).json()["total"] == 0
    assert client.get("/api/search", params={"q": '"(*:-'}).status_code == 200


def test_svg_endpoint(client):
    r = client.get("/icon/fake/arrow-left.svg", params={"color": "2563eb", "size": 64})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert 'filename="fake-arrow-left.svg"' in r.headers["content-disposition"]
    assert "currentColor" not in r.text
    ET.fromstring(r.text)
    assert client.get("/icon/fake/arrow-left.svg", params={"dl": 1}).headers["content-disposition"].startswith("attachment")
    assert client.get("/icon/fake/arrow-left.svg", params={"color": "blue"}).status_code == 422
    assert client.get("/icon/fake/missing.svg").status_code == 404


def test_png_endpoint(client):
    r = client.get("/icon/fake/arrow-left.png", params={"color": "2563eb", "size": 64})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert 'filename="fake-arrow-left.png"' in r.headers["content-disposition"]
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert r.content[37:41] == b"pHYs"  # dpi chunk right after IHDR
    assert client.get("/icon/fake/arrow-left.gif").status_code == 404


def test_detail_sets_health_licenses_zip(client):
    d = client.get("/api/icon/fake:chevron").json()
    assert d["siblings"][0]["id"] == "fake:chevron"
    assert client.get("/api/sets").json()["sets"][0]["prefix"] == "fake"
    assert client.get("/api/health").json()["icons"] == 3
    assert "Fake Icons" in client.get("/licenses").text
    r = client.get("/api/zip", params={"ids": "fake:trash,fake:chevron"})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert {"fake-trash.svg", "fake-chevron.svg", "LICENSES/fake-LICENSE.txt"} <= set(names)
