#!/usr/bin/env bash
# run.sh contract (vibe starts this file; do not run it yourself):
#   - Run in the FOREGROUND. Never daemonize, never background with & or nohup.
#   - Listen on $PORT. vibe assigns it and passes it in the environment.
#   - Exit non-zero on failure, so vibe can see the crash and restart you.
# Output on stdout and stderr is appended to /var/lib/vibe/logs/icons.log.
set -euo pipefail

PORT="${PORT:?vibe sets PORT; run this through vibe, not by hand}"
cd "$(dirname "$0")"

# Create/refresh the venv only when requirements.txt changes.
if [ ! -x .venv/bin/python ]; then
  echo "creating .venv"
  python3 -m venv .venv
fi
stamp=".venv/.requirements.sha"
want="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  echo "installing requirements"
  .venv/bin/pip install -q -r requirements.txt
  echo "$want" > "$stamp"
fi

echo "icons starting on port $PORT"
# The server also runs the weekly ingest (python -m iconsearch.ingest) itself,
# and builds the index on first start if data/icons.db is missing.
exec .venv/bin/uvicorn iconsearch.server:app --host 127.0.0.1 --port "$PORT" \
  --workers 1 --no-access-log --proxy-headers
