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
# Pre-flight: fail fast if Docker filesystem has less than 4GB free.
# The smoke test builds a Python 3.11 image (~2-3GB peak — Playwright excluded).
# Stale Docker images and build cache are the most common cause of this;
# run `docker system prune -af` to reclaim space before retrying.
# =============================================================================
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "/var/lib/docker")
AVAIL_KB=$(df -k "$DOCKER_ROOT" 2>/dev/null | awk 'NR==2 {print $4}')
AVAIL_GB=$(( ${AVAIL_KB:-0} / 1048576 ))
if [ "${AVAIL_KB:-0}" -lt 4194304 ]; then  # 4GB in KB
  echo ""
  echo -e "${RED}${BOLD}  PRE-FLIGHT FAILED — insufficient disk space (${AVAIL_GB}GB free, need 4GB)${NC}"
  echo ""
  echo "  The smoke test builds a ~2-3GB Docker image (Python 3.11 + dependencies)."
  echo "  Free up space by removing unused Docker artefacts:"
  echo "    docker system prune -af"
  echo "  Then re-run the smoke tests."
  echo ""
  exit 1
fi

# =============================================================================
# Pre-flight: fail fast if ports 8000 or 8501 are already bound.
# The smoke test spins up its own Docker Compose stack on these ports.
# A competing stack causes partial port conflicts that produce misleading
# check failures (some checks hit the wrong container, others get 000).
# =============================================================================
PORTS_IN_USE=()
for port in 8000 8501; do
  if ss -tlnH "sport = :$port" 2>/dev/null | grep -q ":$port" || \
     lsof -iTCP:$port -sTCP:LISTEN -t 2>/dev/null | grep -q .; then
    PORTS_IN_USE+=($port)
  fi
done
if [ ${#PORTS_IN_USE[@]} -gt 0 ]; then
  echo ""
  echo -e "${RED}${BOLD}  PRE-FLIGHT FAILED — ports already in use: ${PORTS_IN_USE[*]}${NC}"
  echo ""
  echo "  The smoke test starts its own Docker Compose stack on ports 8000 and 8501."
  echo "  A competing process on these ports causes misleading test failures."
  echo ""
  echo "  Stop the running stack first:"
  echo "    docker compose down"
  echo "  Then re-run the smoke tests."
  echo ""
  exit 1
fi

# =============================================================================
echo ""
echo -e "${CYAN}${BOLD}PART 1 — Isolated unit tests (Dockerfile.smoketest)${NC}"
echo "────────────────────────────────────────"
# =============================================================================

echo "Building smoketest image..."
docker build -f Dockerfile.smoketest -t opendqv-smoketest:local . 2>&1 | tail -5

echo ""
echo "Running 1,000+ tests in clean Python environment..."
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
# On Windows/Git Bash, $(pwd -W) gives a Windows-style path Docker can mount;
# on Linux/Mac, pwd -W is unavailable so we fall back to $(pwd).
SRC_PATH=$(pwd -W 2>/dev/null || pwd)
PIPINSTALL_OUT=$(MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${SRC_PATH}:/src:ro" \
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
