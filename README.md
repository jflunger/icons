# Icon Search

A self-hosted search over ~47,000 free, commercially usable icons from 9 sets
(Lucide, Tabler, Phosphor, Heroicons, Bootstrap Icons, Iconoir, Fluent UI, Carbon,
Google's Material Symbols).
Recolour, resize and change the stroke width, then drag an icon into PowerPoint as a
vector SVG, copy it as SVG or PNG, or download it.

- **Backend:** FastAPI and SQLite FTS5 (`iconsearch/`)
- **Frontend:** one static page with vanilla JS (`web/`), no build step
- **Data:** per-set `@iconify-json/<prefix>` npm packages plus 5 tag enrichers, rebuilt weekly

## Running

**Under vibe (this server):** vibe runs `./run.sh`. It creates `.venv` and installs
`requirements.txt` when that file changes, then starts uvicorn on `0.0.0.0:$PORT`.
On first start with no `data/icons.db`, the server builds the index in the background
(about 10 s), and the page shows "being built" until it's ready.

**Locally on a Mac:**

```sh
make setup && make ingest && make serve     # http://localhost:8765
```

To run it at login, copy `deploy/com.user.iconsearch.server.plist` to
`~/Library/LaunchAgents/`, replace `/Users/YOU/icon-search` with your checkout path, run
`mkdir -p ~/Library/Logs/iconsearch`, then:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.iconsearch.server.plist   # install
launchctl bootout   gui/$(id -u)/com.user.iconsearch.server                                # uninstall
```

## Weekly updates

The server process schedules the ingest itself (`[ingest] schedule = "weekly" | "daily" | "off"`).
It checks every 10 minutes whether `data/state.json` → `last_check` is older than the
period, and if so runs `python -m iconsearch.ingest` in a subprocess. When no upstream
version changed, the ingest exits "up to date" in about a second.

If you'd rather use an external scheduler, set `schedule = "off"` and use one of:

- **macOS:** `deploy/com.user.iconsearch.ingest.plist` (Sunday 03:00). Install it the same way as the server plist.
- **Linux cron:** `0 3 * * 0 cd /path/to/icons && .venv/bin/python -m iconsearch.ingest >> data/ingest.log 2>&1`

## Ingest

```sh
.venv/bin/python -m iconsearch.ingest [--force] [--only lucide,ph] [--no-enrich] [--dry-run] [-v]
```

1. Look up the latest version of each enabled set on the npm registry.
2. If nothing changed (versions, `config.toml` set options, `synonyms.toml`, schema), exit 0 with "up to date".
3. Otherwise rebuild the **whole** DB into `data/icons.new.db`:
   - Download each tarball, or reuse it from `data/cache/`.
   - Skip hidden icons and fold aliases into their parent as search words.
   - Derive `base_name` and `variant`, and apply the Fluent size filter.
   - Check the license against the allowlist.
   - Run the enrichers.
4. Run the FTS rebuild, `integrity_check`, and a sanity check (each set must have ≥ 80% of its previous icon count). Then `os.replace()` the new DB over `icons.db`. The server's read-only connections pick up the new file automatically.
5. Print a summary table and prune old cached tarballs.

**Failure handling:** A set that fails to download or parse is **copied from the current DB**
("carried"), so one flaky source never empties the index. `--only` uses the same mechanism
for every set it leaves out. The exit code is non-zero if a set failed with nothing to carry
over, or if validation failed; in either case the old DB is kept.

**License allowlist:** `MIT`, `ISC`, `Apache-2.0`, `CC0-1.0` and `CC-BY-4.0`, configurable
in `config.toml`. If a set's SPDX license leaves the allowlist, the set is skipped and a
loud warning is logged. Each set's LICENSE text is fetched from GitHub where `info.json`
links one (Carbon links none), and is bundled into ZIP exports.

### Data formats (verified 2026-09)

| Source | What's used |
|---|---|
| `@iconify-json/<prefix>` | `icons.json` (icons, aliases, set width/height), `info.json` (name, author, license), `metadata.json` (`categories` for iconoir/carbon, `suffixes` for ph/heroicons/fluent) |
| `lucide-static` | `tags.json`: `{name: [tags]}` (no categories) |
| `@tabler/icons` | `icons.json`: `{name: {category, tags}}` |
| `@phosphor-icons/core` | `dist/index.mjs` is JS only. Tags and categories are **regex-extracted** (no Node needed); categories map through the `e` enum at the top of the file |
| `fonts.google.com/metadata/icons?key=material_symbols` | Material Symbols tags. Not on npm, so it's fetched on every rebuild and cached as `data/cache/google-fonts-icons.json` (the fallback if the fetch fails). Strip the `)]}'` first line, then read the JSON. Names use `_` where Iconify uses `-`. Tags on more than 3% of icons ("home", "arrow", …) and style words are dropped as noise |
| `twbs/icons` (GitHub) | `docs/content/icons/*.md` YAML front matter. The tag is the npm `bootstrap-icons` version, and the tarball comes from codeload.github.com |

Enricher match rates are logged on every build. They were all 100% on 2026-09-24, and a drop
below 85% logs a warning that the upstream format has probably changed.

### Variants

| Set | Rule | Variants |
|---|---|---|
| ph | `-thin/-light/-bold/-fill/-duotone`, none = regular | 6 weights |
| tabler | `-filled` | outline / filled |
| heroicons | `-solid`, `-20-solid`, `-16-solid` | outline / solid / mini / micro |
| bi | `-fill` | outline / fill |
| iconoir | `-solid` | regular / solid |
| fluent | `-<size>-regular/filled/light` | regular / filled / light |
| material-symbols | bare = filled, `-outline`, `-rounded`, `-outline-rounded`, `-sharp`, `-outline-sharp` | outline / filled / rounded / rounded-filled / sharp / sharp-filled |
| lucide, carbon | none | default |

For Fluent, `keep_sizes = [24]` keeps the 24 px version of each icon. If an icon has no
24 px version, the nearest size is kept, so nothing is lost (6,102 of 19,850 kept).
Material Symbols has no plain `-filled` suffix: the bare name is the filled glyph. Where filling makes no
difference, Iconify keeps only the bare name and makes `-outline` an alias, so those icons are labelled
`outline` (every base name has an outline variant). A style that is identical to another one, often
`-sharp`, is also just an alias and doesn't get its own variant.

Unknown sets fall back to the package's `suffixes` map.

## Adding a set

Add `[sets.<iconify-prefix>]` with `enabled = true` to `config.toml`. The next ingest picks
it up, because a config change forces a rebuild. Enrichers are optional extras in
`iconsearch/ingest/enrich/`.

## Search

- FTS5 over `name_tokens`, `tags_text` (tags, alias words and categories), `synonyms_text` and `category`, with bm25 weights 10 / 3 / 2 / 1.
- Each query word is quoted, and the last one is prefix-matched for type-ahead. Only word characters reach the FTS query, so user input can't inject FTS syntax.
- Exact name and base-name matches get a large bonus. A one-word query that is a synonym of an icon's whole base name also gets a bonus: "home" puts `house` first and "delete" puts `trash` first.
- Results are grouped per `(set, base_name)`, showing the default variant (regular/outline) or the filtered variant. Siblings are shown in the detail panel.
- `synonyms.toml` holds about 90 hand-kept groups. Keep them tight: a broad word attaches the whole group to many icons.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search?q=&providers=&variant=&category=&limit=&offset=&group=` | `{total, offset, items:[{id,prefix,name,base_name,variant,width,height,body,is_stroke}]}` |
| GET | `/api/sets` | sets with version, license, variants and group counts |
| GET | `/api/categories?providers=` | category list with counts |
| GET | `/api/icon/{id}` | metadata, tags and sibling variants |
| GET | `/api/icons?ids=a,b` | several icons in the given order (favourites, recents) |
| GET | `/api/similar/{id}` | icons in other sets sharing name words and tags |
| GET | `/api/zip?ids=&color=&size=&stroke=` | ZIP of SVGs plus `LICENSES/` |
| GET, HEAD | `/icon/{prefix}/{name}.svg?color=&size=&stroke=&bg=&dl=` | PowerPoint-safe SVG |
| GET | `/licenses` | license page |
| GET | `/api/health` | DB age, counts, scheduler state |
| POST | `/mcp` | MCP server (local only) for Claude: `add_to_shopping_list`, `get_cart_zip` |

The MCP endpoint answers only requests made on this server, never ones nginx forwards. Register it with
`claude mcp add --scope user --transport http icons http://127.0.0.1:9005/mcp`. Claude can only add purposes
and write the finished cart ZIP to a path; you still pick the icons in the web UI.

The SVG endpoint:
- Replaces `currentColor` with the literal hex, and uses presentation attributes only (no `<style>`, no CSS variables).
- Sets explicit `width`/`height`: the longer side is `size`, clamped to 16–2048, and the aspect ratio is kept.
- Interprets `stroke` in px on a 24-unit grid and scales it to the icon's viewBox, so `2` looks the same in every set. It only applies to stroke icons.

## Frontend

- **Search:** debounced at 120 ms, with in-flight requests cancelled. Infinite scroll loads 300 results per page. Search state is kept in the URL (`?q=&p=&v=&cat=`).
- **Controls:** colour (default white; the Auto background picks dark tiles for light icons and light tiles for dark ones) with 6 saved swatches (right-click a swatch to remove it), size, stroke and preview background (auto/light/dark/checker). All of these persist in localStorage.
- **Providers:** chips toggle a set; Shift-click shows that set only.
- **Views:** Results, Favourites and Recent (the last 30 icons copied, dragged or downloaded). The detail panel adds Similar icons and a ZIP collection of up to 200 icons.
- **Keyboard:**

  | Key | Action |
  |---|---|
  | `/` | focus search |
  | `↓` / `Enter` in search | jump into the grid |
  | arrows | move the selection |
  | `Enter` | open detail |
  | `c` | copy SVG |
  | `p` | copy PNG |
  | `f` | favourite |
  | `Esc` | close detail, then clear the search |

  Double-clicking a tile copies its SVG.
- **Drag:** the toolbar's **Drag** switch picks one of two modes, saved per browser:
  - **PowerPoint** (default): the tile is dragged with only `text/html` set, an `<img>` whose `src` is the SVG as a base64 `data:` URL. PowerPoint for Mac inserts it as a real SVG graphic (Convert to Shape works).
  - **File:** the browser's native drag of an invisible SVG `<img>` over each tile/preview, so Finder and Figma get an `.svg` file. PowerPoint drops nothing in this mode.
- **Copy PNG:** fetches the SVG, draws it to a canvas at the chosen size (256/512/1024/2048, default 512) and writes an `image/png` ClipboardItem.
- **Copy SVG:** writes `text/plain` plus `image/svg+xml` where supported.

### ⚠️ Clipboard needs a secure context

The Clipboard API only works on `https://` or `http://localhost`. On this server the app is
at `http://<host>:9005`, so the page shows a banner there:
- Copy PNG is disabled.
- Copy SVG falls back to plain text through `execCommand`.
- Drag and Download work normally.

The public URL is `icon.adun.ai`. nginx proxies it to port 9005 (see `deploy/nginx-icon.conf`), and `certbot --nginx -d icon.adun.ai` adds HTTPS, which re-enables copying. On other hosts,
put HTTPS in front of the app. For example, use `tailscale serve 9005`, or add a Caddy or nginx site with a certificate that proxies to `127.0.0.1:9005`.

### Drag-and-drop results (Edge on macOS, Oct 2026)

| Drag data | PowerPoint for Mac | Finder |
|---|---|---|
| SVG `<img>` (native image drag) | nothing | `.svg` file |
| PNG `<img>` | picture (bitmap) | `.png` file |
| `text/html` with a `data:` SVG `<img>` | **SVG graphic** | nothing |
| PNG `<img>` + `DownloadURL` | nothing | not tested |
| URL text | hyperlink | — |

## Tests

```sh
.venv/bin/python -m pytest -q                       # unit tests + fixture ingest + API
.venv/bin/python scripts/check_svgs.py [--base URL] # render 50 random icons per set, parse as XML
```

## Licensing

All 9 default sets are MIT, ISC or Apache-2.0, and each is checked at every ingest. The
`/licenses` page lists version, author and license for each set. ZIP exports include each
set's LICENSE. If this is ever hosted publicly or used to redistribute files, keep
that bundling.
