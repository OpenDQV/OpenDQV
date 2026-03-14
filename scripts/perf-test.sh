#!/usr/bin/env bash
# OpenDQV performance test runner
#
# Brings up the API with the perf overlay (raised rate limits, WEB_CONCURRENCY=4),
# runs the load test suite, then restores the normal stack.
#
# Usage:
#   ./scripts/perf-test.sh            # quick: 1-minute run only
#   ./scripts/perf-test.sh full       # full: 1 min + 5 min + 10 min
#   ./scripts/perf-test.sh custom 30 5  # custom: 30s duration, 5 concurrency

set -euo pipefail

MODE=${1:-quick}
CUSTOM_DURATION=${2:-60}
CUSTOM_CONCURRENCY=${3:-10}
API_URL=${OPENDQV_URL:-http://localhost:8000}
CONCURRENCY=10

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { echo "[perf] $*"; }

require() {
  if ! command -v "$1" &>/dev/null; then
    echo "Error: '$1' is required but not found." >&2
    exit 1
  fi
}

wait_healthy() {
  log "Waiting for API to be healthy at $API_URL/health ..."
  for i in $(seq 1 30); do
    if curl -sf "$API_URL/health" >/dev/null 2>&1; then
      log "API is up."
      return 0
    fi
    sleep 2
  done
  echo "Error: API did not become healthy after 60s." >&2
  exit 1
}

run_test() {
  local label=$1
  local duration=$2
  local concurrency=$3
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "Run: $label (${duration}s, concurrency=${concurrency})"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  OPENDQV_URL="$API_URL" node tests/load-test-universal.js "$duration" "$concurrency"
}

restore_stack() {
  log "Restoring normal stack (standard rate limits)..."
  docker compose up -d api 2>/dev/null || true
  log "Done."
}

# ── Pre-flight ────────────────────────────────────────────────────────────────

require node
require docker
require curl

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# ── Start perf stack ──────────────────────────────────────────────────────────

log "Tearing down existing stack (volumes + orphans)..."
docker compose down -v --remove-orphans
log "Starting API with perf overlay (rate limits disabled, WEB_CONCURRENCY=4)..."
docker compose -f docker-compose.yml -f docker-compose.perf.yml up -d --build api
trap restore_stack EXIT

wait_healthy

# ── Run tests ─────────────────────────────────────────────────────────────────

case "$MODE" in
  quick)
    run_test "1-minute" 60 $CONCURRENCY
    ;;
  full)
    run_test "1-minute"  60  $CONCURRENCY
    run_test "5-minute"  300 $CONCURRENCY
    run_test "10-minute" 600 $CONCURRENCY
    ;;
  custom)
    run_test "custom" "$CUSTOM_DURATION" "$CUSTOM_CONCURRENCY"
    ;;
  *)
    echo "Usage: $0 [quick|full|custom [duration_s] [concurrency]]" >&2
    exit 1
    ;;
esac

echo ""
log "All tests complete."
