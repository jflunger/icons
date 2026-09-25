# icons

- **Port:** `9005` — vibe passes it in as `$PORT`. Never hard-code it.
- **Entry point:** `./run.sh` (foreground only, listens on `$PORT`, exits non-zero on failure).
- **Log file:** `/var/lib/vibe/logs/icons.log` — vibe appends this project's stdout and stderr here.

## Running it

Do not start this project yourself: no `nohup`, no `&`, no `systemctl`. vibe owns the
process. Use the vibe MCP tools instead:

- `project_restart` — restart after a change, then read the returned logs to confirm it came up.
- `project_status` — state, port and uptime.
- `project_logs` — recent output.

They default to this project via the `VIBE_PROJECT` environment variable, so you can
call them without naming it.

## Layout

- `iconsearch/server.py`: FastAPI app. It also runs the ingest on a schedule, in a background thread that starts a subprocess.
- `iconsearch/ingest/`: `python -m iconsearch.ingest` rebuilds `data/icons.db` from npm/GitHub and swaps it in atomically.
- `web/`: static frontend (index.html, app.js, styles.css). There is no build step, and the files are served with `no-cache`.
- `data/` is gitignored: `icons.db` (~30 MB), `cache/` tarballs and `state.json`.
- Config lives in `config.toml` (sets, schedule, license allowlist) and `synonyms.toml`.
- Tests: `.venv/bin/python -m pytest -q`. See README.md for details.
- Public URL: `icon.adun.ai`, served by nginx (`/etc/nginx/sites-available/icon`, source copy in `deploy/nginx-icon.conf`) proxying to 127.0.0.1:9005. TLS comes from `certbot --nginx -d icon.adun.ai`, and after that certbot owns the installed file.

## Project notes

<!-- Yours. Describe what this project is, how it is laid out, and anything Claude should
     know before changing it. -->
