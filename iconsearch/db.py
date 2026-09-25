"""SQLite schema, read-only server connections and the atomic swap."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE sets (
  prefix TEXT PRIMARY KEY,
  name TEXT, version TEXT, author TEXT, author_url TEXT,
  license_title TEXT, license_spdx TEXT, license_url TEXT,
  total INTEGER, ingested_at TEXT,
  license_text TEXT               -- full LICENSE file, bundled into ZIP exports
);

CREATE TABLE icons (
  id TEXT PRIMARY KEY,            -- "lucide:arrow-left"
  prefix TEXT NOT NULL REFERENCES sets(prefix),
  name TEXT NOT NULL,
  base_name TEXT NOT NULL,
  variant TEXT NOT NULL,
  width REAL, height REAL,
  body TEXT NOT NULL,             -- inner SVG markup
  is_stroke INTEGER,              -- body uses stroke= (for stroke-width control)
  category TEXT,
  tags TEXT,                      -- JSON list of enrichment tags, for display
  name_tokens TEXT, tags_text TEXT, synonyms_text TEXT
);
CREATE INDEX idx_icons_prefix ON icons(prefix);
CREATE INDEX idx_icons_base ON icons(prefix, base_name);

CREATE VIRTUAL TABLE icons_fts USING fts5(
  name_tokens, tags_text, synonyms_text, category,
  content='icons', content_rowid='rowid',
  tokenize='unicode61', prefix='2 3 4'
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

ICON_COLUMNS = (
    "id", "prefix", "name", "base_name", "variant", "width", "height", "body",
    "is_stroke", "category", "tags", "name_tokens", "tags_text", "synonyms_text",
)


def create(path: Path) -> sqlite3.Connection:
    """Create a fresh database at `path`, replacing any leftover file."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return conn


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO icons_fts(icons_fts) VALUES('rebuild')")


def integrity_ok(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def counts_by_prefix(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    conn = connect_ro(path)
    try:
        return dict(conn.execute("SELECT prefix, COUNT(*) FROM icons GROUP BY prefix"))
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()


def swap(new_path: Path, live_path: Path) -> None:
    os.replace(new_path, live_path)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class LiveDB:
    """Per-thread read-only connection that reopens when the file is swapped."""

    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()

    def _stamp(self):
        st = os.stat(self.path)
        return (st.st_ino, st.st_mtime_ns)

    def get(self) -> sqlite3.Connection | None:
        try:
            stamp = self._stamp()
        except FileNotFoundError:
            return None
        cached = getattr(self._local, "conn", None)
        if cached and cached[0] == stamp:
            return cached[1]
        if cached:
            cached[1].close()
        conn = connect_ro(self.path)
        self._local.conn = (stamp, conn)
        return conn
