#!/usr/bin/env bash
# =============================================================================
# OpenDQV API — dev/local launcher
#
# Boots uvicorn against opendqv.main:app (the ASGI entry point after the
# v2.1.0 namespace migration — `uvicorn main:app` no longer resolves).
#
# Usage:
#   bash scripts/start_api.sh            # foreground
#   bash scripts/start_api.sh --bg       # background, logs to /tmp/opendqv-api.log
#
# Env overrides:
#   HOST  (default 0.0.0.0)
#   PORT  (default 8000)
#   LOG   (default /tmp/opendqv-api.log)
#
# Anything beyond --bg is forwarded to uvicorn — e.g. --reload, --workers 4.
# For production-style runs use gunicorn (see Dockerfile).
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG="${LOG:-/tmp/opendqv-api.log}"

BG=0
ARGS=()
for a in "$@"; do
    if [[ "$a" == "--bg" ]]; then
        BG=1
    else
        ARGS+=("$a")
    fi
done

CMD=(python -m uvicorn opendqv.main:app --host "$HOST" --port "$PORT" "${ARGS[@]}")

if [[ "$BG" -eq 1 ]]; then
    echo "Starting API in background on $HOST:$PORT → $LOG"
    nohup "${CMD[@]}" >> "$LOG" 2>&1 &
    disown
    # Startup can take several seconds on populated installs while the persistent
    # stats store hydrates in-memory state (worker_heartbeat, quality_stats).
    # Poll /health for up to 30s before giving up.
    for _ in $(seq 1 30); do
        if curl -sSf --max-time 2 "http://localhost:${PORT}/health" >/dev/null 2>&1; then
            echo "API healthy on http://localhost:${PORT}"
            exit 0
        fi
        sleep 1
    done
    echo "API did not respond on /health within 30s — tail $LOG for details." >&2
    exit 1
else
    exec "${CMD[@]}"
fi
