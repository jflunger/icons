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

## Project notes

<!-- Yours. Describe what this project is, how it is laid out, and anything Claude should
     know before changing it. -->
