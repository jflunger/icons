"""FastAPI app: search API, SVG rendering, licenses page and the ingest scheduler."""
from __future__ import annotations

import base64
import html
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import config as config_mod
from . import db, svg
from .cart import ICON_KEYS, Cart
from .ingest.normalize import Synonyms

log = logging.getLogger("iconsearch.server")
cfg = config_mod.load()
live = db.LiveDB(cfg.db_path)
WEB_DIR = config_mod.ROOT / "web"
WORD_RE = re.compile(r"\w+", re.UNICODE)
synonyms = Synonyms.load(cfg.synonyms_path)
cart = Cart(cfg.cart_path)

# Variant display order for sibling lists and the "any" preference.
VARIANT_ORDER = ["default", "regular", "outline", "thin", "light", "bold", "fill", "filled",
                 "solid", "mini", "micro", "duotone",
                 "rounded", "rounded-filled", "sharp", "sharp-filled"]


# ---------------------------------------------------------------- scheduler

class IngestScheduler:
    """Runs `python -m iconsearch.ingest` in a subprocess on the configured schedule.

    vibe owns this process, so the weekly update lives here rather than in cron.
    The ingest itself exits "up to date" quickly when nothing changed upstream.
    """

    CHECK_EVERY_S = 600

    def __init__(self):
        self.running = False
        self.last_exit: int | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def period(self) -> timedelta | None:
        return {"weekly": timedelta(days=7), "daily": timedelta(days=1)}.get(cfg.schedule)

    def last_check(self) -> datetime | None:
        try:
            state = json.loads(cfg.state_path.read_text())
            return datetime.fromisoformat(state["last_check"])
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def due(self) -> bool:
        if not cfg.db_path.exists():
            return True
        period, last = self.period(), self.last_check()
        if period is None:
            return False
        return last is None or datetime.now(timezone.utc) - last >= period

    def run_now(self, *extra: str) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        try:
            log.info("starting ingest %s", " ".join(extra))
            proc = subprocess.run([sys.executable, "-m", "iconsearch.ingest", *extra], cwd=config_mod.ROOT)
            self.last_exit = proc.returncode
            log.info("ingest finished with exit code %s", proc.returncode)
        finally:
            self.running = False
        return True

    def loop(self):
        while not self._stop.is_set():
            try:
                if self.due():
                    self.run_now()
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                log.exception("scheduled ingest crashed")
            self._stop.wait(self.CHECK_EVERY_S)

    def start(self):
        if cfg.schedule == "off" and cfg.db_path.exists():
            return
        threading.Thread(target=self.loop, name="ingest-scheduler", daemon=True).start()

    def stop(self):
        self._stop.set()


scheduler = IngestScheduler()


@asynccontextmanager
async def lifespan(_app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    if os.environ.get("ICONSEARCH_NO_SCHEDULER") != "1":
        scheduler.start()
    # A session manager runs only once, so each app start (and each test client) builds a new one.
    mcp.streamable_http_app(stateless_http=True, json_response=True)
    async with mcp.session_manager.run():
        yield
    scheduler.stop()


app = FastAPI(title="Icon Search", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- basic auth

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # /mcp only answers this server itself (see mcp_endpoint), so it skips the site password.
    if cfg.basic_auth and request.url.path != "/mcp":
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                given = base64.b64decode(header[6:]).decode()
                ok = secrets.compare_digest(given, cfg.basic_auth)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Authentication required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="icon-search"'})
    return await call_next(request)


# ---------------------------------------------------------------- helpers

def conn():
    c = live.get()
    if c is None:
        raise HTTPException(503, "The icon index is still being built. Try again in a minute.")
    return c


def csv_list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def fts_query(q: str) -> tuple[str, list[str]]:
    """Tokenise free text into a safe FTS5 query: quoted terms, AND-ed, last one prefix-matched.

    Only word characters survive, so FTS syntax (quotes, -, *, :, parens, NEAR,
    AND/OR/NOT) in user input can never reach the MATCH expression unquoted.
    """
    tokens = [t for t in WORD_RE.findall(q.lower().replace("_", " "))]
    if not tokens:
        return "", []
    parts = [f'"{t}"' for t in tokens]
    parts[-1] += "*"
    return " ".join(parts), tokens


def variant_rank_sql(col: str = "variant") -> str:
    cases = " ".join(f"WHEN '{v}' THEN {i}" for i, v in enumerate(VARIANT_ORDER))
    return f"(CASE {col} {cases} ELSE {len(VARIANT_ORDER)} END)"


ICON_FIELDS = "i.id, i.prefix, i.name, i.base_name, i.variant, i.width, i.height, i.body, i.is_stroke"


def icon_dict(row) -> dict:
    d = dict(row)
    d["is_stroke"] = bool(d.get("is_stroke"))
    return d


# ---------------------------------------------------------------- pages

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------- API

@app.get("/api/search")
def search(
    q: str = "",
    providers: str | None = None,
    variant: str | None = None,
    category: str | None = None,
    limit: int = Query(300, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    group: bool = True,
):
    c = conn()
    match, tokens = fts_query(q)
    where, params = [], {}
    provs = csv_list(providers)
    if provs:
        where.append(f"i.prefix IN ({','.join(f':p{n}' for n in range(len(provs)))})")
        params.update({f"p{n}": p for n, p in enumerate(provs)})
    if variant:
        where.append("i.variant = :variant")
        params["variant"] = variant
    if category:
        where.append("i.category = :category")
        params["category"] = category

    if match:
        params["match"] = match
        params["qn"] = "-".join(tokens)
        # A one-word query that is a synonym of an icon's whole base name
        # ("home" -> house, "delete" -> trash) ranks that icon near the top.
        syns = synonyms.expand(tokens)[:30] if len(tokens) == 1 else []
        params.update({f"s{n}": w for n, w in enumerate(syns)})
        syn_bonus = (f"- (CASE WHEN i.base_name IN ({','.join(f':s{n}' for n in range(len(syns)))}) THEN 300 ELSE 0 END)"
                     if syns else "")
        # bm25 is negative (lower = better); exact name/base matches jump ahead.
        source = f"""
            SELECT i.rowid AS rid, i.prefix, i.base_name, i.name, i.variant,
                   bm25(icons_fts, 10.0, 3.0, 2.0, 1.0)
                   - (CASE WHEN i.name = :qn THEN 1000 ELSE 0 END)
                   - (CASE WHEN i.base_name = :qn THEN 500 ELSE 0 END) {syn_bonus} AS score
            FROM icons_fts JOIN icons i ON i.rowid = icons_fts.rowid
            WHERE icons_fts MATCH :match {''.join(' AND ' + w for w in where)}"""
        order = ["gscore", "prefix", "base_name"]
    else:
        source = f"""
            SELECT i.rowid AS rid, i.prefix, i.base_name, i.name, i.variant, 0 AS score
            FROM icons i {('WHERE ' + ' AND '.join(where)) if where else ''}"""
        order = ["prefix", "base_name", "name"]

    if group:
        sql = f"""
            WITH m AS ({source}),
            g AS (
              SELECT rid, prefix, base_name, name,
                     MIN(score) OVER w AS gscore,
                     ROW_NUMBER() OVER (PARTITION BY prefix, base_name
                                        ORDER BY {variant_rank_sql()}, score, name) AS rn
              FROM m WINDOW w AS (PARTITION BY prefix, base_name)
            ),
            r AS (SELECT rid, prefix, base_name, name, gscore, COUNT(*) OVER () AS total
                  FROM g WHERE rn = 1 ORDER BY {', '.join(order)} LIMIT :limit OFFSET :offset)
            SELECT {ICON_FIELDS}, r.total FROM r JOIN icons i ON i.rowid = r.rid
            ORDER BY {', '.join('r.' + col for col in order)}"""
    else:
        order = ["score", "prefix", "name"] if match else ["prefix", "base_name", "name"]
        sql = f"""
            WITH m AS ({source}),
            r AS (SELECT rid, score, prefix, base_name, name, COUNT(*) OVER () AS total
                  FROM m ORDER BY {', '.join(order)} LIMIT :limit OFFSET :offset)
            SELECT {ICON_FIELDS}, r.total FROM r JOIN icons i ON i.rowid = r.rid
            ORDER BY {', '.join('r.' + col for col in order)}"""
    params.update(limit=limit, offset=offset)
    rows = c.execute(sql, params).fetchall()
    total = rows[0]["total"] if rows else 0
    items = []
    for r in rows:
        d = icon_dict(r)
        d.pop("total", None)
        items.append(d)
    return {"total": total, "offset": offset, "items": items}


@app.get("/api/sets")
def sets():
    c = conn()
    variants: dict[str, list[str]] = {}
    for r in c.execute(f"SELECT prefix, variant FROM icons GROUP BY prefix, variant ORDER BY prefix, {variant_rank_sql()}"):
        variants.setdefault(r["prefix"], []).append(r["variant"])
    out = []
    for r in c.execute("SELECT * FROM sets ORDER BY rowid"):
        d = dict(r)
        d["variants"] = variants.get(r["prefix"], [])
        d["groups"] = c.execute("SELECT COUNT(DISTINCT base_name) FROM icons WHERE prefix=?", (r["prefix"],)).fetchone()[0]
        out.append(d)
    return {"sets": out, "all_variants": sorted({v for vs in variants.values() for v in vs},
                                                key=lambda v: (VARIANT_ORDER.index(v) if v in VARIANT_ORDER else 99, v)),
            "png_size": cfg.png_size}


@app.get("/api/categories")
def categories(providers: str | None = None):
    c = conn()
    provs = csv_list(providers)
    where = f"AND prefix IN ({','.join('?' * len(provs))})" if provs else ""
    rows = c.execute(
        f"SELECT category, COUNT(DISTINCT prefix || ':' || base_name) AS n FROM icons "
        f"WHERE category IS NOT NULL {where} GROUP BY category ORDER BY category COLLATE NOCASE",
        provs,
    ).fetchall()
    return [{"category": r["category"], "count": r["n"]} for r in rows]


@app.get("/api/icon/{icon_id:path}")
def icon_detail(icon_id: str):
    c = conn()
    row = c.execute(f"SELECT {ICON_FIELDS}, i.category, i.tags, i.tags_text FROM icons i WHERE i.id = ?",
                    (icon_id,)).fetchone()
    if not row:
        raise HTTPException(404, "icon not found")
    d = icon_dict(row)
    d["tags"] = json.loads(d["tags"] or "[]")
    siblings = c.execute(
        f"SELECT {ICON_FIELDS} FROM icons i WHERE prefix = ? AND base_name = ? ORDER BY {variant_rank_sql()}, name",
        (d["prefix"], d["base_name"]),
    ).fetchall()
    d["siblings"] = [icon_dict(s) for s in siblings]
    s = c.execute("SELECT name, license_title, license_spdx, license_url, version FROM sets WHERE prefix=?",
                  (d["prefix"],)).fetchone()
    d["set"] = dict(s) if s else None
    return d


@app.get("/api/icons")
def icons_by_id(ids: str):
    """Icons for a list of ids, in the given order (favourites, recents)."""
    wanted = csv_list(ids)[:500]
    if not wanted:
        return {"items": []}
    rows = conn().execute(
        f"SELECT {ICON_FIELDS} FROM icons i WHERE i.id IN ({','.join('?' * len(wanted))})", wanted
    ).fetchall()
    by_id = {r["id"]: icon_dict(r) for r in rows}
    return {"items": [by_id[i] for i in wanted if i in by_id]}


@app.get("/api/similar/{icon_id:path}")
def similar(icon_id: str, limit: int = Query(60, ge=1, le=300)):
    """Icons in *other* sets sharing the most name words and tags with this one."""
    c = conn()
    row = c.execute("SELECT prefix, base_name, tags FROM icons WHERE id = ?", (icon_id,)).fetchone()
    if not row:
        raise HTTPException(404, "icon not found")
    words = WORD_RE.findall(row["base_name"].replace("-", " ").replace("_", " "))
    for tag in json.loads(row["tags"] or "[]")[:8]:
        words += WORD_RE.findall(tag.lower())
    words = list(dict.fromkeys(w for w in words if len(w) > 1 and not w.isdigit()))[:12]
    if not words:
        return {"items": []}
    match = " OR ".join(f'"{w}"' for w in words)
    rows = c.execute(f"""
        WITH m AS (
          SELECT i.rowid AS rid, i.prefix, i.base_name, i.variant, i.name,
                 bm25(icons_fts, 10.0, 3.0, 1.0, 0.5) - (CASE WHEN i.base_name = :base THEN 100 ELSE 0 END) AS score
          FROM icons_fts JOIN icons i ON i.rowid = icons_fts.rowid
          WHERE icons_fts MATCH :match AND i.prefix != :prefix),
        g AS (SELECT rid, MIN(score) OVER (PARTITION BY prefix, base_name) AS gscore,
                     ROW_NUMBER() OVER (PARTITION BY prefix, base_name ORDER BY {variant_rank_sql()}, score) AS rn
              FROM m)
        SELECT {ICON_FIELDS} FROM g JOIN icons i ON i.rowid = g.rid WHERE rn = 1
        ORDER BY gscore LIMIT :limit""",
        {"match": match, "prefix": row["prefix"], "base": row["base_name"], "limit": limit}).fetchall()
    return {"items": [icon_dict(r) for r in rows]}


@app.get("/api/zip")
def zip_collection(
    ids: str,
    color: str = "ffffff",
    size: int = 256,
    stroke: float | None = None,
):
    """A ZIP of rendered SVGs plus each included set's LICENSE."""
    wanted = csv_list(ids)[:200]
    c = conn()
    rows = c.execute(
        f"SELECT id, prefix, name, body, width, height, is_stroke FROM icons WHERE id IN ({','.join('?' * len(wanted))})",
        wanted,
    ).fetchall() if wanted else []
    if not rows:
        raise HTTPException(404, "no matching icons")
    return icons_zip([(f"{r['prefix']}-{r['name']}.svg", r) for r in rows], f"icons-{len(rows)}.zip",
                     color=color, size=size, stroke=stroke)


def icons_zip(files: list[tuple[str, dict]], zip_name: str, *, color: str, size: int, stroke: float | None,
              licenses: bool = True) -> Response:
    return Response(icons_zip_bytes(files, color=color, size=size, stroke=stroke, licenses=licenses),
                    media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})


def icons_zip_bytes(files: list[tuple[str, dict]], *, color: str, size: int, stroke: float | None,
                    licenses: bool = True) -> bytes:
    """A ZIP of rendered SVGs (filename, icon), plus each included set's LICENSE unless `licenses` is off."""
    c = conn()
    buf = io.BytesIO()
    prefixes = sorted({icon["prefix"] for _, icon in files})
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, icon in files:
            try:
                doc = svg.build(icon["body"], icon["width"], icon["height"], color=color, size=size,
                                stroke=stroke, is_stroke=bool(icon["is_stroke"]))
            except svg.SvgParamError as e:
                raise HTTPException(422, str(e)) from None
            zf.writestr(filename, doc)
        if licenses:
            summary = []
            for s in c.execute(f"SELECT * FROM sets WHERE prefix IN ({','.join('?' * len(prefixes))})", prefixes):
                summary.append(f"{s['name']} ({s['prefix']}) v{s['version']}\n  by {s['author'] or '?'}\n"
                               f"  license: {s['license_title'] or s['license_spdx']} {s['license_url'] or ''}\n")
                if s["license_text"]:
                    zf.writestr(f"LICENSES/{s['prefix']}-LICENSE.txt", s["license_text"])
            zf.writestr("LICENSES/README.txt", "Icons in this archive come from these sets:\n\n" + "\n".join(summary))
    return buf.getvalue()


@app.api_route("/icon/{prefix}/{filename}", methods=["GET", "HEAD"])
def icon_svg(
    prefix: str,
    filename: str,
    color: str = "ffffff",
    size: int = 256,
    stroke: float | None = None,
    bg: str | None = None,
    dl: bool = False,
):
    name, _, ext = filename.rpartition(".")
    if ext not in ("svg", "png"):
        raise HTTPException(404, "only .svg and .png are served")
    row = conn().execute(
        "SELECT body, width, height, is_stroke FROM icons WHERE id = ?", (f"{prefix}:{name}",)
    ).fetchone()
    if not row:
        raise HTTPException(404, "icon not found")
    try:
        doc = svg.build(row["body"], row["width"], row["height"], color=color, size=size,
                        stroke=stroke, is_stroke=bool(row["is_stroke"]), bg=bg)
    except svg.SvgParamError as e:
        raise HTTPException(422, str(e)) from None
    media_type = "image/svg+xml"
    if ext == "png":
        # PowerPoint takes a dragged <img> as a picture only if it can decode it, and its
        # HTML import doesn't handle SVG reliably, so drag uses this PNG.
        doc, media_type = svg.to_png(doc, size), "image/png"
    disposition = "attachment" if dl else "inline"
    return Response(doc, media_type=media_type, headers={
        "Content-Disposition": f'{disposition}; filename="{prefix}-{name}.{ext}"',
        "Cache-Control": "public, max-age=86400",
        "Access-Control-Allow-Origin": "*",
    })


# ---------------------------------------------------------------- cart

def require_cart_password(given: str | None):
    if not cfg.cart_password:
        raise HTTPException(404, "the cart is not set up (no secrets/cart_password)")
    if not given or not secrets.compare_digest(given.encode(), cfg.cart_password.encode()):
        raise HTTPException(401, "wrong cart password")


def is_local(request: Request) -> bool:
    """True for requests made on this server itself, never ones nginx forwards from the internet.

    nginx always sets X-Forwarded-For/X-Real-IP, so their presence alone marks a proxied request.
    """
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and "x-forwarded-for" not in request.headers \
        and "x-real-ip" not in request.headers


def require_local(request: Request):
    if not is_local(request):
        raise HTTPException(403, "this endpoint is only available on the server itself")


def cart_icon(icon_id: str) -> dict:
    row = conn().execute(f"SELECT {ICON_FIELDS} FROM icons i WHERE i.id = ?", (icon_id,)).fetchone()
    if not row:
        raise HTTPException(404, "icon not found")
    d = icon_dict(row)
    return {k: d[k] for k in ICON_KEYS}


def cart_response(items: list[dict]) -> dict:
    return {"items": items}


def cart_edit(fn, *args, **kwargs) -> dict:
    try:
        return cart_response(fn(*args, **kwargs))
    except KeyError:
        raise HTTPException(404, "cart entry not found") from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/cart")
def cart_get(x_cart_password: str | None = Header(None)):
    require_cart_password(x_cart_password)
    return cart_response(cart.items())


@app.post("/api/cart/items")
def cart_add(body: dict, x_cart_password: str | None = Header(None)):
    """Body: {"icon_id": "lucide:house", "purpose"?: "..."}; the purpose defaults to the icon name."""
    require_cart_password(x_cart_password)
    icon = cart_icon(str(body.get("icon_id", "")))
    purpose = body.get("purpose")
    return cart_edit(cart.add, str(purpose) if purpose is not None else icon["name"], icon)


@app.patch("/api/cart/items/{entry_id}")
def cart_update(entry_id: str, body: dict, x_cart_password: str | None = Header(None)):
    """Body: {"purpose"?: "...", "icon_id"?: "..."}; an icon_id replaces the entry's icon."""
    require_cart_password(x_cart_password)
    icon = cart_icon(str(body["icon_id"])) if body.get("icon_id") else None
    purpose = body.get("purpose")
    return cart_edit(cart.update, entry_id, purpose=str(purpose) if purpose is not None else None, icon=icon)


@app.delete("/api/cart/items/{entry_id}")
def cart_remove(entry_id: str, x_cart_password: str | None = Header(None)):
    require_cart_password(x_cart_password)
    return cart_edit(cart.remove, entry_id)


@app.delete("/api/cart")
def cart_clear(x_cart_password: str | None = Header(None)):
    require_cart_password(x_cart_password)
    return cart_edit(cart.clear)


UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')


def purpose_filenames(items: list[dict]) -> list[str]:
    """A unique, filesystem-safe "<purpose>.svg" for each item, in order."""
    names, seen = [], set()
    for item in items:
        base = UNSAFE_FILENAME_RE.sub("-", item["purpose"]).strip(" .-")[:120] or item["icon"]["name"]
        name, n = f"{base}.svg", 2
        while name.lower() in seen:
            name, n = f"{base} ({n}).svg", n + 1
        seen.add(name.lower())
        names.append(name)
    return names


@app.get("/api/cart/zip")
def cart_zip(x_cart_password: str | None = Header(None), color: str = "000000", size: int = 256,
             stroke: float | None = None):
    """The cart's icons as a ZIP of SVGs named after their purposes. Entries without an icon are left out."""
    require_cart_password(x_cart_password)
    # Internal use only, so no LICENSES/ folder: just the purpose-named SVGs.
    return icons_zip(cart_zip_files(), "icon-cart.zip", color=color, size=size, stroke=stroke, licenses=False)


def cart_zip_files() -> list[tuple[str, dict]]:
    """(purpose filename, icon) for each cart entry that has an icon."""
    items = [i for i in cart.items() if i["icon"]]
    if not items:
        raise HTTPException(404, "the cart has no icons yet")
    return list(zip(purpose_filenames(items), (i["icon"] for i in items)))


@app.get("/api/local/cart")
def local_cart(request: Request, color: str = "000000", size: int = 256, stroke: float | None = None):
    """Local only. Every cart entry with its purpose and, once an icon is assigned, the SVG file."""
    require_local(request)
    out = []
    for item in cart.items():
        icon, entry = item["icon"], {"purpose": item["purpose"], "icon_id": None, "filename": None, "svg": None}
        if icon:
            try:
                doc = svg.build(icon["body"], icon["width"], icon["height"], color=color, size=size,
                                stroke=stroke, is_stroke=bool(icon["is_stroke"]))
            except svg.SvgParamError as e:
                raise HTTPException(422, str(e)) from None
            entry.update(icon_id=icon["id"], filename=f"{icon['prefix']}-{icon['name']}.svg", svg=doc)
        out.append(entry)
    return {"items": out}


@app.post("/api/local/shopping-list")
def local_shopping_list(request: Request, body: dict):
    """Local only. Body: {"purposes": ["...", ...]}; adds them as entries without an icon.

    Purposes already in the cart are skipped, so posting the same list twice is harmless.
    """
    require_local(request)
    purposes = body.get("purposes")
    if not isinstance(purposes, list) or not all(isinstance(p, str) for p in purposes):
        raise HTTPException(422, 'body must be {"purposes": ["...", ...]}')
    return cart_edit(cart.add_purposes, purposes)


# ---------------------------------------------------------------- MCP

# Lets Claude on this server add to the shopping list and fetch the finished cart.
# Picking the icons stays with the person in the web UI, so there are no search or assign tools.
# Register with: claude mcp add --transport http icons http://127.0.0.1:9005/mcp
mcp = MCPServer("icons", instructions=(
    "The icon shopping cart of the Icon Search app. Add the purposes you need icons for with "
    "add_to_shopping_list; the user then picks an icon for each in the web UI. Once they say "
    "they are done, fetch the icons with get_cart_zip."))


@mcp.tool()
def add_to_shopping_list(purposes: list[str]) -> dict:
    """Add entries to the icon shopping list, one per purpose (e.g. "Delete button", "Settings tab").

    The user picks an icon for each purpose in the web UI. Purposes already in the cart are skipped.
    """
    before = {i["purpose"] for i in cart.items()}
    try:
        items = cart.add_purposes(purposes)
    except ValueError as e:
        raise ToolError(str(e)) from None
    added = [i["purpose"] for i in items if i["purpose"] not in before]
    return {"added": added, "already_in_cart": sorted({p.strip() for p in purposes} & before),
            "waiting_for_icon": [i["purpose"] for i in items if not i["icon"]]}


@mcp.tool()
def get_cart_zip(path: str, color: str = "000000", size: int = 256, stroke: float | None = None) -> dict:
    """Write the cart's chosen icons as a ZIP of SVGs named after their purposes ("Delete button.svg").

    `path` is an absolute file path for the ZIP, or an existing directory to put icon-cart.zip in.
    `color` is a hex colour without "#", `size` the SVG size in px, `stroke` an optional stroke width
    for outline icons. Purposes the user has not picked an icon for yet are left out and listed.
    """
    dest = Path(path).expanduser()
    if not dest.is_absolute():
        raise ToolError("path must be absolute")
    if dest.is_dir():
        dest = dest / "icon-cart.zip"
    try:
        files = cart_zip_files()
        data = icons_zip_bytes(files, color=color, size=size, stroke=stroke, licenses=False)
    except HTTPException as e:
        raise ToolError(e.detail) from None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"path": str(dest), "files": [f for f, _ in files],
            "without_icon": [i["purpose"] for i in cart.items() if not i["icon"]]}


class MCPEndpoint:
    """A raw ASGI app (a class, so Starlette doesn't wrap it as a request handler), local requests only."""

    async def __call__(self, scope, receive, send):
        if not is_local(Request(scope)):
            await JSONResponse({"detail": "this endpoint is only available on the server itself"},
                               status_code=403)(scope, receive, send)
            return
        await mcp.session_manager.handle_request(scope, receive, send)


app.router.add_route("/mcp", MCPEndpoint(), include_in_schema=False)


@app.get("/api/health")
def health():
    c = live.get()
    body = {"db": False, "ingest_running": scheduler.running, "last_ingest_exit": scheduler.last_exit,
            "schedule": cfg.schedule}
    last = scheduler.last_check()
    body["last_check"] = last.isoformat() if last else None
    if c is None:
        return JSONResponse(body, status_code=503)
    built = c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
    body.update(
        db=True,
        built_at=built[0] if built else None,
        age_hours=round((datetime.now(timezone.utc) - datetime.fromisoformat(built[0])).total_seconds() / 3600, 1)
        if built else None,
        icons=c.execute("SELECT COUNT(*) FROM icons").fetchone()[0],
        sets={r[0]: r[1] for r in c.execute("SELECT prefix, COUNT(*) FROM icons GROUP BY prefix")},
    )
    return body


@app.get("/licenses", response_class=HTMLResponse)
def licenses():
    c = conn()
    rows = c.execute("SELECT * FROM sets ORDER BY name COLLATE NOCASE").fetchall()
    e = html.escape

    def link(text, url):
        return f'<a href="{e(url)}">{e(text)}</a>' if url else e(text or "")

    trs = "\n".join(
        f"<tr><td>{e(r['name'] or r['prefix'])}</td><td><code>{e(r['prefix'])}</code></td>"
        f"<td>{e(r['version'] or '')}</td><td>{link(r['author'], r['author_url'])}</td>"
        f"<td>{link(r['license_title'] or r['license_spdx'], r['license_url'])}</td>"
        f"<td>{r['total']:,}</td></tr>"
        for r in rows
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Icon licenses</title><script src="/static/theme.js"></script><link rel="stylesheet" href="/static/styles.css"></head>
<body class="page"><main class="licenses">
<p><a href="/">&larr; Back to search</a></p>
<h1>Icon set licenses</h1>
<p>Every set here uses a permissive license (checked against an allowlist at each ingest).
Attribution is appreciated, and required for CC-BY sets. If you redistribute icon files,
include the set's license.</p>
<div class="table-wrap"><table><thead><tr><th>Set</th><th>Prefix</th><th>Version</th><th>Author</th>
<th>License</th><th>Icons</th></tr></thead><tbody>{trs}</tbody></table></div>
<p class="muted">Icon data comes from the <a href="https://iconify.design/">Iconify</a> per-set npm packages.</p>
</main></body></html>"""
