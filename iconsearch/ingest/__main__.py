"""Build the icon database: `python -m iconsearch.ingest [--force] [--only a,b] ...`.

Every run rebuilds the full DB into icons.new.db, validates it and swaps it in
with os.replace(). Sets that fail to download, or that --only leaves out, are
copied over from the current DB so one flaky source never empties the index.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import config as config_mod
from .. import db
from . import fetch, iconify, normalize, variants
from .enrich import for_prefix

log = logging.getLogger("iconsearch.ingest")

STROKE_RE = re.compile(r'stroke="(?!none")[^"]+"')


@dataclass
class SetResult:
    prefix: str
    version: str = ""
    status: str = "ok"           # ok | carried | skipped | failed
    icons: int = 0
    enriched: str = "-"
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    cache_files: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(cfg) -> dict:
    try:
        return json.loads(cfg.state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(cfg, state: dict) -> None:
    tmp = cfg.state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(cfg.state_path)


def fingerprint(cfg) -> str:
    """Changes when anything other than upstream versions would change the DB."""
    h = hashlib.sha256()
    h.update(str(db.SCHEMA_VERSION).encode())
    for s in sorted(cfg.enabled_sets, key=lambda s: s.prefix):
        h.update(f"{s.prefix}:{s.keep_sizes}".encode())
    h.update(",".join(cfg.license_allowlist).encode())
    if cfg.synonyms_path.exists():
        h.update(cfg.synonyms_path.read_bytes())
    return h.hexdigest()[:16]


def build_set(conn, s: iconify.IconSet, set_cfg, synonyms, enrich: bool, client, cfg, res: SetResult):
    # Variants and size filtering
    detected = [(ic.name, variants.detect(s.prefix, ic.name, s.suffixes)) for ic in s.icons]
    keep = variants.filter_sizes(detected, set_cfg.keep_sizes)
    vmap = dict(detected)
    icons = [ic for ic in s.icons if ic.name in keep]
    if len(icons) != len(s.icons):
        res.notes.append(f"size filter kept {len(icons)}/{len(s.icons)}")

    # Enrichment (optional, fails soft)
    extra: dict[str, dict] = {}
    if enrich:
        rates = []
        for enr in for_prefix(s.prefix):
            try:
                data = enr.load(client, cfg.cache_dir)
            except Exception as e:  # noqa: BLE001 - any enricher failure is soft
                log.warning("%s enricher failed: %s", enr.label, e)
                res.notes.append(f"{enr.label} enricher failed")
                continue
            if enr.cache_file:
                res.cache_files.append(enr.cache_file)
            hit = 0
            for ic in icons:
                v = vmap[ic.name]
                for key in enr.keys(ic.name, v.base_name, ic.aliases):
                    if key in data:
                        extra[ic.name] = data[key]
                        hit += 1
                        break
            pct = 100.0 * hit / max(len(icons), 1)
            rates.append(f"{pct:.0f}%")
            log.info("%s: %d/%d icons enriched (%.1f%%) from %s %s",
                     enr.label, hit, len(icons), pct, enr.package, enr.version)
            if pct < 85:
                log.warning("%s enrichment below 85%% - upstream format may have changed", enr.label)
        if rates:
            res.enriched = ",".join(rates)

    rows = []
    for ic in icons:
        v = vmap[ic.name]
        e = extra.get(ic.name, {})
        tags = e.get("tags", [])
        cats = [*ic.categories, *(c for c in e.get("categories", []) if c not in ic.categories)]
        # Index "<base> <variant>" rather than the raw name so size suffixes
        # like Fluent's "-24-" don't match every numeric query.
        index_name = v.base_name if v.variant in variants.DEFAULT_VARIANTS else f"{v.base_name}-{v.variant}"
        name_tokens, tags_text, syn_text = normalize.build_text(index_name, ic.aliases, tags, cats, synonyms)
        rows.append((
            f"{s.prefix}:{ic.name}", s.prefix, ic.name, v.base_name, v.variant,
            ic.width, ic.height, ic.body, 1 if STROKE_RE.search(ic.body) else 0,
            (cats[0][:1].upper() + cats[0][1:]) if cats else None, json.dumps(tags),
            name_tokens, tags_text, syn_text,
        ))
    conn.executemany(
        f"INSERT INTO icons ({','.join(db.ICON_COLUMNS)}) VALUES ({','.join('?' * len(db.ICON_COLUMNS))})",
        rows,
    )
    lic, author = s.license, s.author
    conn.execute(
        "INSERT INTO sets (prefix, name, version, author, author_url, license_title, license_spdx,"
        " license_url, total, ingested_at, license_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (s.prefix, s.name, s.version, author.get("name"), author.get("url"),
         lic.get("title"), lic.get("spdx"), lic.get("url"), len(rows), now_iso(),
         fetch_license_text(client, lic.get("url"))),
    )
    res.icons = len(rows)


GITHUB_BLOB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$")


def fetch_license_text(client, url: str | None) -> str | None:
    """Fetch the LICENSE file so ZIP exports can bundle it. Fails soft."""
    m = GITHUB_BLOB_RE.match(url or "")
    if not m:
        return None
    branch = m[3].split("/", 1)[0]
    base = f"https://raw.githubusercontent.com/{m[1]}/{m[2]}"
    # The linked path is sometimes stale (renamed file), so try the usual names too.
    candidates = [f"{base}/{m[3]}", *(f"{base}/{branch}/{n}" for n in ("LICENSE", "LICENSE.md", "LICENSE.txt"))]
    for raw in dict.fromkeys(candidates):
        try:
            r = client.get(raw)
            if r.status_code == 200 and r.text.strip():
                return r.text
        except Exception:  # noqa: BLE001
            pass
    log.warning("could not fetch license text for %s", url)
    return None


def carry_over(conn, prefix: str, live_path) -> int:
    """Copy one set's rows from the live DB. Returns the number of icons copied."""
    if not live_path.exists():
        return 0
    conn.execute("ATTACH DATABASE ? AS old", (f"file:{live_path}?mode=ro",))
    try:
        # Copy by column name so a DB from an older schema still carries over.
        for table in ("icons", "sets"):
            new_cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            old_cols = {r[1] for r in conn.execute(f"PRAGMA old.table_info({table})")}
            cols = ",".join(c for c in new_cols if c in old_cols)
            conn.execute(f"INSERT INTO main.{table} ({cols}) SELECT {cols} FROM old.{table} WHERE prefix=?", (prefix,))
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM icons WHERE prefix=?", (prefix,)).fetchone()[0]
    except sqlite3.DatabaseError as e:
        log.warning("could not carry over %s from the live DB: %s", prefix, e)
        return 0
    finally:
        conn.execute("DETACH DATABASE old")


def print_summary(results: list[SetResult], total_s: float) -> None:
    head = f"{'set':<11}{'version':<10}{'status':<9}{'icons':>7}  {'enriched':<9}{'time':>6}  notes"
    print(head)
    print("-" * len(head))
    for r in results:
        print(f"{r.prefix:<11}{r.version:<10}{r.status:<9}{r.icons:>7}  {r.enriched:<9}"
              f"{r.seconds:>5.1f}s  {'; '.join(r.notes)}")
    print("-" * len(head))
    print(f"{'total':<30}{sum(r.icons for r in results):>7}  {'':<9}{total_s:>5.1f}s")
    sys.stdout.flush()


def run(args) -> int:
    cfg = config_mod.load(args.config)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)

    lock = open(cfg.data_dir / "ingest.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("another ingest is already running")
        return 1

    state = load_state(cfg)
    only = {p.strip() for p in args.only.split(",")} if args.only else None
    sets = cfg.enabled_sets
    if only:
        unknown = only - {s.prefix for s in sets}
        if unknown:
            log.error("--only names sets that are not enabled in config: %s", ",".join(sorted(unknown)))
            return 2

    started = time.monotonic()
    client = fetch.make_client()

    # 1. Latest versions
    latest: dict[str, tuple[str, str]] = {}
    for s in sets:
        if only and s.prefix not in only:
            continue
        try:
            latest[s.prefix] = fetch.npm_latest(client, iconify.package_name(s.prefix))
        except Exception as e:  # noqa: BLE001
            log.warning("%s: registry lookup failed: %s", s.prefix, e)

    # 2. Up to date?
    fp = fingerprint(cfg)
    prev_versions = state.get("sets", {})
    unchanged = (
        cfg.db_path.exists()
        and state.get("fingerprint") == fp
        and set(prev_versions) == {s.prefix for s in sets}
        and all(prev_versions.get(p) == v for p, (v, _) in latest.items())
    )
    if unchanged and not (args.force or only):
        log.info("up to date (%d sets)", len(sets))
        if not args.dry_run:
            state["last_check"] = now_iso()
            save_state(cfg, state)
        return 0

    # 3. Rebuild
    synonyms = normalize.Synonyms.load(cfg.synonyms_path)
    conn = db.create(cfg.new_db_path)
    results: list[SetResult] = []
    for s in sets:
        res = SetResult(s.prefix)
        results.append(res)
        t0 = time.monotonic()
        if only and s.prefix not in only or s.prefix not in latest:
            res.icons = carry_over(conn, s.prefix, cfg.db_path)
            res.version = prev_versions.get(s.prefix, "")
            res.status = "carried" if res.icons else "failed"
            if s.prefix not in latest and not (only and s.prefix not in only):
                res.notes.append("registry lookup failed")
            continue
        version, url = latest[s.prefix]
        res.version = version
        try:
            fname = fetch.cache_name(iconify.package_name(s.prefix), version)
            data = fetch.fetch_tarball(client, url, cfg.cache_dir, fname)
            res.cache_files.append(fname)
            iset = iconify.parse_tarball(s.prefix, version, data)
            spdx = iset.license.get("spdx", "")
            if spdx not in cfg.license_allowlist:
                log.warning("!!! %s: license %r is not in the allowlist %s - SKIPPING this set !!!",
                            s.prefix, spdx or iset.license.get("title"), cfg.license_allowlist)
                res.status = "skipped"
                res.notes.append(f"license {spdx or '?'} not allowed")
                continue
            build_set(conn, iset, s, synonyms, not args.no_enrich, client, cfg, res)
            conn.commit()
            if iset.hidden:
                res.notes.append(f"{iset.hidden} hidden")
        except Exception as e:  # noqa: BLE001 - one set must never sink the run
            conn.rollback()
            log.warning("%s: ingest failed (%s); keeping previous data", s.prefix, e, exc_info=args.verbose)
            res.icons = carry_over(conn, s.prefix, cfg.db_path)
            res.status = "carried" if res.icons else "failed"
            res.version = prev_versions.get(s.prefix, "") if res.icons else version
            res.notes.append(f"error: {e}")
        finally:
            res.seconds = time.monotonic() - t0
    client.close()

    # 4. FTS + validation
    db.rebuild_fts(conn)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (now_iso(),))
    conn.commit()
    ok = db.integrity_ok(conn)
    conn.execute("VACUUM")
    conn.close()
    print_summary(results, time.monotonic() - started)

    if not ok:
        log.error("integrity_check failed on the new DB; keeping the old one")
        return 1
    prev_counts = db.counts_by_prefix(cfg.db_path)
    bad = []
    for r in results:
        before = prev_counts.get(r.prefix, 0)
        if r.status != "skipped" and before and r.icons < 0.8 * before:
            bad.append(f"{r.prefix} {before}->{r.icons}")
    if bad:
        log.error("sanity check failed (fewer than 80%% of previous icons): %s; keeping the old DB", ", ".join(bad))
        return 1
    if not any(r.icons for r in results):
        log.error("no icons ingested; keeping the old DB")
        return 1

    if args.dry_run:
        log.info("dry run: new DB left at %s, not swapped", cfg.new_db_path)
        return 0

    db.swap(cfg.new_db_path, cfg.db_path)
    new_versions = {r.prefix: r.version for r in results if r.icons}
    state = {"sets": new_versions, "fingerprint": fp, "last_check": now_iso(), "last_build": now_iso()}
    save_state(cfg, state)

    # Prune tarballs that are no longer current. Carried sets keep nothing
    # cached, which is fine: they are re-downloaded next time they are built.
    if not only:
        keep = {f for r in results for f in r.cache_files}
        removed = fetch.prune_cache(cfg.cache_dir, keep)
        if removed:
            log.info("pruned %d old cached tarballs", removed)
    log.info("swapped in new DB: %d icons", sum(r.icons for r in results))
    return 1 if any(r.status == "failed" for r in results) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m iconsearch.ingest", description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="rebuild even if nothing changed upstream")
    ap.add_argument("--only", help="comma-separated prefixes to rebuild; other sets are copied from the live DB")
    ap.add_argument("--no-enrich", action="store_true", help="skip tag enrichers")
    ap.add_argument("--dry-run", action="store_true", help="build and validate, but do not swap or save state")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s ingest %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
