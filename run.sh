#!/usr/bin/env bash
# run.sh contract (vibe starts this file; do not run it yourself):
#   - Run in the FOREGROUND. Never daemonize, never background with & or nohup.
#   - Listen on $PORT. vibe assigns it and passes it in the environment.
#   - Exit non-zero on failure, so vibe can see the crash and restart you.
# Output on stdout and stderr is appended to /var/lib/vibe/logs/icons.log.
set -euo pipefail

PORT="${PORT:?vibe sets PORT; run this through vibe, not by hand}"

echo "icons starting on port $PORT"

# Replace this with the real server. It must stay in the foreground.
exec python3 -m http.server "$PORT" --bind 0.0.0.0
