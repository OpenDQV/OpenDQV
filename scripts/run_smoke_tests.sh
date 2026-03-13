#!/usr/bin/env bash
# =============================================================================
# OpenDQV Smoke Test Runner (ACT-004 proxy)
#
# Part 1: Isolated unit tests in a clean Python container (Dockerfile.smoketest)
# Part 2: Full Docker Compose stack HTTP checks (clean_room_test.sh)
# Part 3: pip install smoke test — installs as a package, runs opendqv CLI
#
# Usage:  bash scripts/run_smoke_tests.sh
# Exits:  0 = all pass,  1 = any failure
# =============================================================================

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

echo ""
echo -e "${BOLD}OpenDQV Smoke Tests${NC}"
echo "Project: $PROJECT_DIR"
echo "Date:    $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "════════════════════════════════════════════════════════"

PART1_PASS=false
PART2_PASS=false
PART3_PASS=false

# =============================================================================
echo ""
echo -e "${CYAN}${BOLD}PART 1 — Isolated unit tests (Dockerfile.smoketest)${NC}"
echo "────────────────────────────────────────"
# =============================================================================

echo "Building smoketest image..."
docker build -f Dockerfile.smoketest -t opendqv-smoketest:local . 2>&1 | tail -5

echo ""
echo "Running 950+ tests in clean Python environment..."
if docker run --rm \
    -e OPENDQV_NODE_ID=ci-smoketest \
    opendqv-smoketest:local; then
  echo -e "\n${GREEN}${BOLD}  Part 1 PASSED${NC}"
  PART1_PASS=true
else
  echo -e "\n${RED}${BOLD}  Part 1 FAILED${NC}"
fi

# =============================================================================
echo ""
echo -e "${CYAN}${BOLD}PART 2 — Full Docker Compose stack (20 HTTP checks)${NC}"
echo "────────────────────────────────────────"
# =============================================================================

if bash scripts/clean_room_test.sh; then
  echo -e "\n${GREEN}${BOLD}  Part 2 PASSED${NC}"
  PART2_PASS=true
else
  echo -e "\n${RED}${BOLD}  Part 2 FAILED${NC}"
fi

# =============================================================================
echo ""
echo -e "${CYAN}${BOLD}PART 3 — pip install smoke test${NC}"
echo "────────────────────────────────────────"
# =============================================================================
# Mounts source into a clean python:3.11-slim container, installs the package,
# and verifies the opendqv CLI entry point works end-to-end.

echo "Running pip install in clean python:3.11-slim container..."
PIPINSTALL_OUT=$(docker run --rm \
  -v "$(pwd):/src:ro" \
  python:3.11-slim \
  bash -c "
    set -e
    apt-get update -qq && apt-get install -y -qq --no-install-recommends gcc > /dev/null 2>&1
    pip install --no-cache-dir /src -q --disable-pip-version-check
    echo '--- version ---'
    opendqv --version
    echo '--- help ---'
    opendqv --help 2>&1 | head -4
    echo '--- done ---'
  " 2>&1) || true

echo "$PIPINSTALL_OUT" | grep -v "^$" | tail -12

if echo "$PIPINSTALL_OUT" | grep -q "opendqv 1.0.0" && \
   echo "$PIPINSTALL_OUT" | grep -q "Trust is cheaper" && \
   echo "$PIPINSTALL_OUT" | grep -q "\-\-\- done \-\-\-"; then
  echo -e "\n${GREEN}${BOLD}  Part 3 PASSED${NC}"
  PART3_PASS=true
else
  echo -e "\n${RED}${BOLD}  Part 3 FAILED${NC}"
fi

# =============================================================================
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${BOLD}SMOKE TEST SUMMARY${NC}"
echo "════════════════════════════════════════════════════════"

if $PART1_PASS; then
  echo -e "  Part 1 (unit tests):      ${GREEN}${BOLD}PASS${NC}"
else
  echo -e "  Part 1 (unit tests):      ${RED}${BOLD}FAIL${NC}"
fi

if $PART2_PASS; then
  echo -e "  Part 2 (HTTP + auth):     ${GREEN}${BOLD}PASS${NC}"
else
  echo -e "  Part 2 (HTTP + auth):     ${RED}${BOLD}FAIL${NC}"
fi

if $PART3_PASS; then
  echo -e "  Part 3 (pip install CLI): ${GREEN}${BOLD}PASS${NC}"
else
  echo -e "  Part 3 (pip install CLI): ${RED}${BOLD}FAIL${NC}"
fi

echo ""

if $PART1_PASS && $PART2_PASS && $PART3_PASS; then
  echo -e "${GREEN}${BOLD}  ALL SMOKE TESTS PASSED${NC}"
  exit 0
else
  echo -e "${RED}${BOLD}  SMOKE TESTS FAILED${NC}"
  exit 1
fi
