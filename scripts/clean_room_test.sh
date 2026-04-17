#!/usr/bin/env bash
# =============================================================================
# OpenDQV Clean-Room Integration Test
# Simulates a brand-new user cloning the repo and running everything.
#
# Usage:  bash scripts/clean_room_test.sh
# Exits:  0 = all pass,  1 = any failure
# =============================================================================

PASS=0
FAIL=0
ERRORS=()
SERVICE_STARTED=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}── $1${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $1"; ((PASS++)) || true; }
fail()  { echo -e "  ${RED}✗${NC} $1"; ((FAIL++)) || true; ERRORS+=("$1"); }
info()  { echo -e "  ${YELLOW}→${NC} $1"; }

cleanup() {
  if $SERVICE_STARTED; then
    step "Teardown"
    docker compose down -v --remove-orphans 2>/dev/null || true
    info "Service stopped, volumes removed"
    # Reset any contract YAML files modified during the test run (e.g. draft version
    # counters auto-incremented by the registry when draft contracts are exercised).
    # Only runs if inside a git repo — safe no-op otherwise.
    if git rev-parse --git-dir > /dev/null 2>&1; then
      git checkout -- opendqv/contracts/ 2>/dev/null || true
      info "opendqv/contracts/ reset to committed state"
    fi
  fi
}
trap cleanup EXIT

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

# Ensure .env exists (simulates the `cp .env.example .env` step from quickstart)
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  info "Created .env from .env.example (clean-room setup)"
fi

# Warn if Docker disk space is low — the no-cache build needs ~2GB free
DOCKER_AVAIL=$(docker system df 2>/dev/null | awk '/Build Cache/{print $4}' | grep -oE '[0-9]+(\.[0-9]+)?GB' | head -1 || echo "")
HOST_AVAIL_KB=$(df -k / 2>/dev/null | tail -1 | awk '{print $4}' || echo "999999")
if [ "${HOST_AVAIL_KB:-999999}" -lt 3000000 ] 2>/dev/null; then
  echo ""
  echo -e "  ${YELLOW}⚠  WARNING: Low disk space (< 3 GB free on /).${NC}"
  echo -e "  ${YELLOW}   The no-cache Docker build may fail. Free space first:${NC}"
  echo -e "  ${YELLOW}   docker system prune -f${NC}"
  echo ""
fi

echo -e "\n${BOLD}OpenDQV Clean-Room Integration Test${NC}"
echo "Project: $PROJECT_DIR"
echo "Date:    $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "────────────────────────────────────────"

# =============================================================================
step "1. No-cache Docker build (simulates fresh git clone)"
# =============================================================================
BUILD_LOG=$(docker compose build --no-cache 2>&1) || { fail "Docker build exited non-zero"; echo "$BUILD_LOG" | tail -20; exit 1; }
if echo "$BUILD_LOG" | grep -qi "^error"; then
  fail "Docker build reported errors"
  echo "$BUILD_LOG" | tail -20
else
  ok "Image built from scratch (no cache)"
fi

# =============================================================================
step "2. Full unit + integration test suite"
# =============================================================================
info "Running pytest inside fresh container..."
TEST_OUTPUT=$(docker compose run --rm api sh -c "pip install pytest pytest-asyncio -q --disable-pip-version-check 2>/dev/null; python -m pytest tests/ -q --tb=short" 2>&1) || true
echo "$TEST_OUTPUT" | tail -6

# grep without ^ anchor: handles Docker Desktop prepending container name/metadata lines
if echo "$TEST_OUTPUT" | grep -qE '[0-9]+ passed'; then
  TEST_COUNT=$(echo "$TEST_OUTPUT" | grep -E '[0-9]+ passed' | grep -E '[0-9]+' | head -1)
  FAIL_COUNT=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ failed' | grep -oE '^[0-9]+' || echo "0")
  if [ "$FAIL_COUNT" = "0" ]; then
    ok "All $TEST_COUNT tests passed"
  else
    fail "$FAIL_COUNT test(s) failed out of $TEST_COUNT"
    echo "$TEST_OUTPUT" | grep "^FAILED" | head -10
  fi
else
  fail "pytest output not parseable or fatal error"
fi

# =============================================================================
step "3. Start service (AUTH_MODE=open for smoke tests)"
# =============================================================================
SERVICE_STARTED=true
docker compose up -d api 2>&1 | tail -3

info "Waiting for health check (up to 30s)..."
HEALTHY=false
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    ok "Service healthy after ${i}s"
    HEALTHY=true
    break
  fi
  sleep 1
done
if ! $HEALTHY; then
  fail "Service failed to become healthy within 30s"
  docker compose logs api | tail -20
  exit 1
fi

# helper: run a python check against a JSON string
check_json() {
  local label="$1"
  local json="$2"
  local code="$3"
  if echo "$json" | python3 -c "$code" 2>/dev/null; then
    ok "$label"
    return 0
  else
    fail "$label"
    echo "    Response: ${json:0:300}"
    return 1
  fi
}

# =============================================================================
step "4. Health endpoint"
# =============================================================================
H=$(curl -sf http://localhost:8000/health || echo '{}')
check_json "GET /health → status healthy" "$H" \
  "import sys,json; d=json.load(sys.stdin); assert d.get('status') in ('ok','healthy'), d; print(f'    status={d[\"status\"]} auth={d.get(\"auth_mode\")} contracts={d.get(\"contracts_loaded\",\"n/a\")}')"

# =============================================================================
step "5. Contract list with asset_id"
# =============================================================================
CS=$(curl -sf http://localhost:8000/api/v1/contracts || echo '[]')
check_json "GET /api/v1/contracts → asset_id present on customer" "$CS" "
import sys, json
cs = json.load(sys.stdin)
c = next((x for x in cs if x['name']=='customer'), None)
assert c, 'customer missing'
assert 'asset_id' in c, f'asset_id missing from: {list(c.keys())}'
assert c['asset_id'] == 'urn:opendqv:customer', f'wrong: {c[\"asset_id\"]}'
print(f'    {len(cs)} contracts, customer.asset_id={c[\"asset_id\"]}')
"

# =============================================================================
step "6. Contract detail with asset_id"
# =============================================================================
D=$(curl -sf http://localhost:8000/api/v1/contracts/customer || echo '{}')
check_json "GET /api/v1/contracts/customer → detail with rules + asset_id" "$D" "
import sys, json
d = json.load(sys.stdin)
assert d.get('name') == 'customer'
assert d.get('asset_id') == 'urn:opendqv:customer'
assert len(d.get('rules',[])) > 0
print(f'    {len(d[\"rules\"])} rules, {len(d[\"contexts\"])} contexts')
"

# =============================================================================
step "7. Single record validation — valid"
# =============================================================================
VR=$(curl -sf -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "record": {"email":"alice@example.com","age":30,"name":"Alice",
               "phone":"+14155551234","username":"alice","password":"pass1234",
               "score":85,"date":"2024-01-15","id":"c001","balance":100.0},
    "contract": "customer",
    "record_id": "smoke-valid"
  }' || echo '{}')
check_json "POST /api/v1/validate → valid record accepted" "$VR" "
import sys, json
d = json.load(sys.stdin)
assert d.get('valid') is True, f'not valid: {d}'
assert d.get('record_id') == 'smoke-valid'
assert d.get('owner') == 'Data Governance Team'
"

# =============================================================================
step "8. Single record validation — invalid blocked"
# =============================================================================
IR=$(curl -sf -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{"record":{"email":"not-an-email","age":-5,"name":""},
       "contract":"customer","record_id":"smoke-invalid"}' || echo '{}')
check_json "POST /api/v1/validate → invalid record blocked" "$IR" "
import sys, json
d = json.load(sys.stdin)
assert d.get('valid') is False
assert len(d.get('errors',[])) > 0
fields = [e['field'] for e in d['errors']]
print(f'    {len(d[\"errors\"])} errors on fields: {fields}')
"

# =============================================================================
step "9. Batch validation with rule_failure_counts"
# =============================================================================
BR=$(curl -sf -X POST http://localhost:8000/api/v1/validate/batch \
  -H "Content-Type: application/json" \
  -d '{
    "records": [
      {"email":"a@b.com","name":"Alice","age":25,"id":"r1"},
      {"email":"bad","name":"","age":-1,"id":"r2"},
      {"email":"c@d.com","name":"Carol","age":30,"id":"r3"}
    ],
    "contract": "customer"
  }' || echo '{}')
check_json "POST /api/v1/validate/batch → summary + rule_failure_counts" "$BR" "
import sys, json
d = json.load(sys.stdin)
s = d.get('summary', {})
assert s.get('total') == 3
assert s.get('failed') >= 1
rfc = s.get('rule_failure_counts', {})
assert len(rfc) > 0, 'rule_failure_counts empty'
print(f'    total={s[\"total\"]} passed={s[\"passed\"]} failed={s[\"failed\"]} top_rule={max(rfc,key=rfc.get)}')
"

# =============================================================================
step "10. Quality trend includes asset_id"
# =============================================================================
TR=$(curl -sf "http://localhost:8000/api/v1/contracts/customer/quality-trend?days=7" || echo '{}')
check_json "GET quality-trend → responds with asset_id" "$TR" "
import sys, json
d = json.load(sys.stdin)
assert d.get('contract') == 'customer'
assert d.get('days') == 7
assert 'points' in d
assert d.get('asset_id') == 'urn:opendqv:customer', f'asset_id={d.get(\"asset_id\")}'
print(f'    {len(d[\"points\"])} data points, asset_id={d[\"asset_id\"]}')
"

# =============================================================================
step "11. Context-aware validation"
# =============================================================================
CR=$(curl -sf -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{"record":{"email":"k@example.com","name":"Kid","age":25},
       "contract":"customer","context":"kids_app"}' || echo '{}')
check_json "kids_app context → age 25 rejected (must be 5-17)" "$CR" "
import sys, json
d = json.load(sys.stdin)
age_err = [e for e in d.get('errors',[]) if e['field']=='age']
assert len(age_err) > 0, f'expected age error, got: {d}'
print(f'    age error: {age_err[0][\"message\"]}')
"

# =============================================================================
step "12. Cross-field compare rule (proof_of_play)"
# =============================================================================
XR=$(curl -sf -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "record": {
      "impression_start": "2024-01-15T10:00:00",
      "impression_end":   "2024-01-15T09:00:00",
      "panel_id": "P001", "campaign_id": "C001",
      "duration_seconds": 30, "panel_type": "STATIC"
    },
    "contract": "proof_of_play"
  }' || echo '{}')
check_json "Cross-field compare → end < start is rejected" "$XR" "
import sys, json
d = json.load(sys.stdin)
all_issues = d.get('errors',[]) + d.get('warnings',[])
assert len(all_issues) > 0 or d.get('valid') is False, f'expected failure: {d}'
print(f'    valid={d[\"valid\"]}, {len(d.get(\"errors\",[]))} errors')
"

# =============================================================================
step "13. GraphQL — contracts with assetId"
# =============================================================================
GR=$(curl -sf -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query":"{ contracts { name version assetId ruleCount } }"}' || echo '{}')
check_json "GraphQL /graphql → contracts with assetId" "$GR" "
import sys, json
d = json.load(sys.stdin)
cs = d.get('data',{}).get('contracts',[])
assert len(cs) > 0
c = next((x for x in cs if x['name']=='customer'), None)
assert c is not None
print(f'    {len(cs)} contracts via GraphQL, customer assetId={c.get(\"assetId\")}')
"

# =============================================================================
step "14. Contract history with approved_by + hash chain"
# =============================================================================
HI=$(curl -sf http://localhost:8000/api/v1/contracts/customer/history || echo '[]')
check_json "GET /history → entry_hash + approved_by fields present" "$HI" "
import sys, json
raw = json.load(sys.stdin)
# Response may be a list or {'contract':..., 'history':[...]}
d = raw if isinstance(raw, list) else raw.get('history', raw.get('entries', []))
assert len(d) > 0, f'empty history: {raw}'
e = d[0]
assert 'entry_hash' in e, f'missing entry_hash: {list(e.keys())}'
assert 'prev_hash' in e, 'missing prev_hash'
assert 'approved_by' in e, f'missing approved_by: {list(e.keys())}'
assert len(e['entry_hash']) == 64
print(f'    {len(d)} entries, hash={e[\"entry_hash\"][:16]}..., approved_by={e[\"approved_by\"]}')
"

# =============================================================================
step "15. 404 for unknown contract"
# =============================================================================
NF_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8000/api/v1/contracts/does_not_exist_xyz)
if [ "$NF_CODE" = "404" ]; then
  ok "GET /api/v1/contracts/does_not_exist → 404 Not Found"
else
  fail "Expected 404, got: $NF_CODE"
fi

# =============================================================================
step "16. Contract reload"
# =============================================================================
RL=$(curl -sf -X POST http://localhost:8000/api/v1/contracts/reload || echo '{}')
check_json "POST /api/v1/contracts/reload → success" "$RL" "
import sys, json
d = json.load(sys.stdin)
assert isinstance(d, dict) and len(d) > 0, f'empty response: {d}'
"

# =============================================================================
step "17. SDK — synchronous validate_batch call"
# =============================================================================
SDK_OUT=$(docker compose run --rm api python3 -c "
import sys
sys.path.insert(0, '/app')
import urllib.request, json

# Simulate what the SDK does: POST to batch endpoint
payload = json.dumps({
    'records': [
        {'email': 'sdk@test.com', 'name': 'SDK Test', 'age': 28},
        {'email': 'bad', 'name': '', 'age': -1},
    ],
    'contract': 'customer',
}).encode()

# Direct API call (service not running inside the run container, test SDK logic instead)
from opendqv.core.validator import validate_batch
from opendqv.core.rule_parser import Rule
rules = [
    Rule(name='valid_email', type='regex', field='email', pattern=r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\$'),
    Rule(name='name_required', type='not_empty', field='name'),
    Rule(name='age_min', type='min', field='age', min=0),
]
result = validate_batch(
    [{'email':'sdk@test.com','name':'SDK Test','age':28},
     {'email':'bad','name':'','age':-1}],
    rules
)
s = result['summary']
assert s['total'] == 2
assert s['passed'] == 1
assert s['failed'] == 1
assert len(s['rule_failure_counts']) > 0
print(f'SDK batch: total={s[\"total\"]} passed={s[\"passed\"]} failed={s[\"failed\"]} rule_counts={s[\"rule_failure_counts\"]}')
" 2>&1) || true
if echo "$SDK_OUT" | grep -q "SDK batch:"; then
  ok "SDK validate_batch → correct summary + rule_failure_counts"
  echo "    $(echo "$SDK_OUT" | grep 'SDK batch:')"
else
  fail "SDK validate_batch test failed"
  echo "    $SDK_OUT"
fi

# =============================================================================
step "18. audit-verify CLI"
# =============================================================================
AUDIT=$(docker compose run --rm api bash -c "
python -c \"
import opendqv.config as config; config.DB_PATH='/tmp/clean_room_audit.db'
from pathlib import Path; from opendqv.core.contracts import ContractRegistry
ContractRegistry(Path('opendqv/contracts'))
\" && python -m opendqv.cli audit-verify --db /tmp/clean_room_audit.db
" 2>&1) || true
if echo "$AUDIT" | grep -q "Chain integrity: PASS"; then
  COUNT=$(echo "$AUDIT" | grep -oE 'All [0-9]+ entries' | grep -oE '[0-9]+' || echo "?")
  ok "opendqv audit-verify → $COUNT entries, chain integrity: PASS"
else
  fail "audit-verify failed"
  echo "    $AUDIT"
fi

# =============================================================================
step "19. HTTP lookup rule — unit test via test suite"
# =============================================================================
HTTP_TEST=$(docker compose run --rm api sh -c "pip install pytest pytest-asyncio -q --disable-pip-version-check 2>/dev/null; python -m pytest tests/test_core.py -k HttpLookup -q --tb=short" 2>&1) || true
if echo "$HTTP_TEST" | grep -q "passed"; then
  COUNT=$(echo "$HTTP_TEST" | grep -E '[0-9]+ passed' | grep -E '[0-9]+')
  ok "HTTP lookup tests → $COUNT passed"
else
  fail "HTTP lookup tests failed"
  echo "$HTTP_TEST" | tail -10
fi

# =============================================================================
step "20. contract test suite — asset_id + approved_by"
# =============================================================================
CONTRACT_TEST=$(docker compose run --rm api sh -c "pip install pytest pytest-asyncio -q --disable-pip-version-check 2>/dev/null; python -m pytest tests/test_contracts.py -q --tb=short" 2>&1) || true
if echo "$CONTRACT_TEST" | grep -q "passed"; then
  COUNT=$(echo "$CONTRACT_TEST" | grep -E '[0-9]+ passed' | grep -E '[0-9]+')
  ok "Contract tests → $COUNT passed (incl. asset_id + approved_by)"
else
  fail "Contract tests failed"
  echo "$CONTRACT_TEST" | tail -10
fi

# =============================================================================
step "21. RT45 write guardrails — ACTIVE contract is immutable"
# =============================================================================
# Open mode gives admin role → can activate. Activate customer, then try to add a rule.
ACTIVATE_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8000/api/v1/contracts/customer/status?status=active" \
  -H "Content-Type: application/json")
if [ "$ACTIVATE_HTTP" = "200" ]; then
  ok "customer contract activated (open mode = admin role)"
else
  fail "Failed to activate customer for RT45 test: HTTP $ACTIVATE_HTTP"
fi

GUARD_BODY_FILE=$(mktemp)
GUARD_HTTP=$(curl -s -o "$GUARD_BODY_FILE" -w "%{http_code}" \
  -X POST "http://localhost:8000/api/v1/contracts/customer/rules" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke_immutability_check","type":"not_empty","field":"smoke_field"}')
GUARD_DETAIL=$(python3 -c "import sys,json; d=json.load(open('$GUARD_BODY_FILE')); print(d.get('detail','')[:90])" 2>/dev/null || echo "")
rm -f "$GUARD_BODY_FILE"
if [ "$GUARD_HTTP" = "409" ]; then
  ok "POST /rules on ACTIVE contract → 409 Conflict (RT45 enforced)"
  echo "    $GUARD_DETAIL"
else
  fail "RT45 guardrail missed: expected 409, got $GUARD_HTTP"
  echo "    $GUARD_DETAIL"
fi

# =============================================================================
step "22. Token auth — restart service in AUTH_MODE=token"
# =============================================================================
info "Restarting API with AUTH_MODE=token (shell env overrides .env)..."
AUTH_MODE=token docker compose up -d api 2>&1 | tail -3

TOKEN_HEALTHY=false
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    TOKEN_HEALTHY=true
    break
  fi
  sleep 1
done
if $TOKEN_HEALTHY; then
  ok "Service healthy in AUTH_MODE=token"
else
  fail "Service failed to become healthy in token mode"
  docker compose logs api | tail -10
fi

# =============================================================================
step "23. Token auth — unauthenticated request returns 401"
# =============================================================================
# POST /validate is auth-protected; GET /contracts is intentionally public
UNAUTH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{}')
if [ "$UNAUTH_HTTP" = "401" ]; then
  ok "POST /api/v1/validate without token → 401 Unauthorized"
else
  fail "Expected 401 in token mode, got $UNAUTH_HTTP"
fi

# =============================================================================
step "24. Token auth — admin PAT grants access"
# =============================================================================
# Use exec -T (runs inside live container, same DB as the service).
# Docker Compose status lines ("Container X Starting") go to stderr — drop 2>&1
# so only actual CLI stdout is captured, making JWT extraction reliable on all platforms.
ADMIN_TOKEN_OUT=$(docker compose exec -T api \
  python -m opendqv.cli token-generate smoke-admin --role admin --expiry-days 1 2>/dev/null)
ADMIN_PAT=$(echo "$ADMIN_TOKEN_OUT" | grep -oE 'eyJ[A-Za-z0-9._-]+' | head -1)
if [ -z "$ADMIN_PAT" ]; then
  fail "Failed to generate admin PAT via exec"
  # Show stderr separately for diagnostics
  docker compose exec -T api python -m opendqv.cli token-generate smoke-admin-diag --role admin --expiry-days 1 || true
else
  # Verify against an auth-protected endpoint (POST /validate requires auth in token mode)
  AUTH_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "http://localhost:8000/api/v1/validate" \
    -H "Authorization: Bearer $ADMIN_PAT" \
    -H "Content-Type: application/json" \
    -d '{"record":{"name":"smoke"},"contract":"customer"}')
  if [ "$AUTH_HTTP" != "401" ] && [ "$AUTH_HTTP" != "403" ]; then
    ok "Admin PAT → POST /validate authenticated (HTTP $AUTH_HTTP)"
  else
    fail "Admin PAT auth failed: HTTP $AUTH_HTTP (expected non-401)"
  fi
fi

# =============================================================================
step "25. Token auth — validator role blocked from contract activation (maker-checker)"
# =============================================================================
VALIDATOR_TOKEN_OUT=$(docker compose exec -T api \
  python -m opendqv.cli token-generate smoke-validator --role validator --expiry-days 1 2>/dev/null)
VALIDATOR_PAT=$(echo "$VALIDATOR_TOKEN_OUT" | grep -oE 'eyJ[A-Za-z0-9._-]+' | head -1)
if [ -z "$VALIDATOR_PAT" ]; then
  fail "Failed to generate validator PAT via exec"
  echo "    $VALIDATOR_TOKEN_OUT"
else
  VALIDATOR_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "http://localhost:8000/api/v1/contracts/customer/status?status=active" \
    -H "Authorization: Bearer $VALIDATOR_PAT" \
    -H "Content-Type: application/json")
  if [ "$VALIDATOR_HTTP" = "403" ]; then
    ok "Validator PAT → POST /status?status=active returns 403 (maker-checker enforced)"
  else
    fail "Expected 403 for validator activation, got $VALIDATOR_HTTP"
  fi
fi

# =============================================================================
step "26. Restore open mode for remaining HTTP tests"
# =============================================================================
docker compose up -d api 2>&1 | tail -2
OPEN_HEALTHY=false
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    OPEN_HEALTHY=true; break
  fi
  sleep 1
done
if $OPEN_HEALTHY; then
  ok "Service healthy in AUTH_MODE=open"
else
  fail "Service failed to become healthy after open-mode restore"
fi

# =============================================================================
step "27. CLI — list contracts + show contract detail"
# =============================================================================
CLI_LIST=$(docker compose exec -T api python -m opendqv.cli list 2>&1) || true
if echo "$CLI_LIST" | grep -q "customer" && echo "$CLI_LIST" | grep -q "NAME"; then
  COUNT=$(echo "$CLI_LIST" | grep -c "^[a-z]" || echo "?")
  ok "opendqv list → $COUNT contracts, tabular output with NAME header"
else
  fail "opendqv list output unexpected"
  echo "    $CLI_LIST" | head -5
fi

CLI_SHOW=$(docker compose exec -T api python -m opendqv.cli show customer 2>&1) || true
if echo "$CLI_SHOW" | grep -q "Contract: customer" && \
   echo "$CLI_SHOW" | grep -q "Owner:" && \
   echo "$CLI_SHOW" | grep -q "RULE"; then
  RULE_COUNT=$(echo "$CLI_SHOW" | grep -cP "^\s+\S" || echo "?")
  ok "opendqv show customer → contract detail with rules table"
else
  fail "opendqv show customer output unexpected"
  echo "$CLI_SHOW" | head -8
fi

# =============================================================================
step "28. CLI — export-odcs + generate snowflake"
# =============================================================================
CLI_ODCS=$(docker compose exec -T api python -m opendqv.cli export-odcs customer 2>&1) || true
if echo "$CLI_ODCS" | grep -qi "customer" && \
   (echo "$CLI_ODCS" | grep -q "^title\|^name\|^contract\|opendqv" || \
    echo "$CLI_ODCS" | grep -q "version\|owner"); then
  ok "opendqv export-odcs customer → ODCS YAML produced"
else
  fail "opendqv export-odcs customer output unexpected"
  echo "$CLI_ODCS" | head -5
fi

CLI_GEN=$(docker compose exec -T api python -m opendqv.cli generate customer snowflake 2>&1) || true
if echo "$CLI_GEN" | grep -q "CREATE OR REPLACE FUNCTION"; then
  ok "opendqv generate customer snowflake → SQL function generated"
else
  fail "opendqv generate customer snowflake output unexpected"
  echo "$CLI_GEN" | head -5
fi

# =============================================================================
step "29. Bad contract YAML — malformed file silently rejected, others unaffected"
# =============================================================================
BEFORE_COUNT=$(curl -sf http://localhost:8000/api/v1/contracts | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

# Write a syntactically broken YAML (unmatched quote triggers yaml.scanner.ScannerError)
cat > opendqv/contracts/smoke_bad_yaml_test.yaml << 'EOF'
contract:
  name: smoke_bad_yaml_test
  rules:
    - name: bad_rule
      type: "not_empty
      field: id
EOF

curl -sf -X POST http://localhost:8000/api/v1/contracts/reload > /dev/null 2>&1 || true

AFTER_COUNT=$(curl -sf http://localhost:8000/api/v1/contracts | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
BAD_PRESENT=$(curl -sf http://localhost:8000/api/v1/contracts | python3 -c "import sys,json; cs=json.load(sys.stdin); print('yes' if any(c['name']=='smoke_bad_yaml_test' for c in cs) else 'no')" 2>/dev/null || echo "unknown")

rm -f opendqv/contracts/smoke_bad_yaml_test.yaml
curl -sf -X POST http://localhost:8000/api/v1/contracts/reload > /dev/null 2>&1 || true

if [ "$BAD_PRESENT" = "no" ] && [ "$AFTER_COUNT" = "$BEFORE_COUNT" ]; then
  ok "Malformed YAML rejected — contract count unchanged ($BEFORE_COUNT), bad contract not loaded"
else
  fail "Bad YAML handling unexpected: before=$BEFORE_COUNT after=$AFTER_COUNT bad_present=$BAD_PRESENT"
fi

# =============================================================================
step "30. Batch file upload — CSV multipart POST"
# =============================================================================
BATCH_CSV="smoke_batch_$$.csv"
cat > "$BATCH_CSV" << 'EOF'
email,name,age,id
alice@example.com,Alice,30,r1
bob@example.com,Bob,25,r2
not-an-email,,bad-age,r3
EOF

BATCH_FILE_OUT=$(MSYS_NO_PATHCONV=1 curl -sf \
  -F "file=@${BATCH_CSV};type=text/csv" \
  "http://localhost:8000/api/v1/validate/batch/file?contract=customer" || echo '{}')
rm -f "$BATCH_CSV"

check_json "POST /validate/batch/file → summary with rows/passed/failed" "$BATCH_FILE_OUT" "
import sys, json
d = json.load(sys.stdin)
assert 'rows' in d or 'summary' in d, f'unexpected shape: {list(d.keys())}'
rows = d.get('rows') or d.get('summary', {}).get('total', 0)
assert rows >= 3, f'expected ≥3 rows, got {rows}'
print(f'    rows={rows} passed={d.get(\"passed\", d.get(\"summary\",{}).get(\"passed\",\"?\"))} failed={d.get(\"failed\", d.get(\"summary\",{}).get(\"failed\",\"?\"))}')
"

# =============================================================================
step "31. Webhook SSRF protection — private IPs rejected"
# =============================================================================
SSRF1_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "http://127.0.0.1:9999/hook"}')
if [ "$SSRF1_HTTP" = "400" ]; then
  ok "http://127.0.0.1 webhook → 400 (loopback blocked)"
else
  fail "Expected 400 for loopback webhook, got $SSRF1_HTTP"
fi

SSRF2_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "http://192.168.1.1/hook"}')
if [ "$SSRF2_HTTP" = "400" ]; then
  ok "http://192.168.1.1 webhook → 400 (RFC-1918 private range blocked)"
else
  fail "Expected 400 for RFC-1918 webhook, got $SSRF2_HTTP"
fi

SSRF3_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{"url": "http://169.254.169.254/latest/meta-data/"}')
if [ "$SSRF3_HTTP" = "400" ]; then
  ok "http://169.254.169.254 (cloud metadata) webhook → 400 (link-local blocked)"
else
  fail "Expected 400 for metadata endpoint webhook, got $SSRF3_HTTP"
fi

# =============================================================================
step "32. UI reachability — Streamlit workbench returns HTTP 200"
# =============================================================================
info "Starting UI service (depends on healthy API)..."
docker compose up -d ui 2>&1 | tail -2

UI_HEALTHY=false
# Check root path / — the real end-to-end test that the UI is accessible.
# 60s timeout: Mac Docker Desktop cold-starts Streamlit slower than Linux.
for i in $(seq 1 60); do
  UI_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8501/ 2>/dev/null || echo "000")
  if [ "$UI_HTTP" = "200" ]; then
    UI_HEALTHY=true; break
  fi
  sleep 1
done

if $UI_HEALTHY; then
  ok "Streamlit UI → http://localhost:8501 returns 200"
else
  fail "Streamlit UI did not respond with 200 within 60s (last HTTP: $UI_HTTP)"
  docker compose logs ui | tail -10
fi

# =============================================================================
step "33. Rate limiting — 429 enforced on validate endpoint"
# =============================================================================
# Temporarily lower the validate rate limit to 3/minute so the test completes quickly
cp .env /tmp/smoke_ratelimit_env_backup
# sed -i requires '' on macOS (BSD sed) but not on Linux (GNU sed)
if sed --version 2>/dev/null | grep -q GNU; then
  sed -i 's|RATE_LIMIT_VALIDATE=.*|RATE_LIMIT_VALIDATE=3/minute|' .env
else
  sed -i '' 's|RATE_LIMIT_VALIDATE=.*|RATE_LIMIT_VALIDATE=3/minute|' .env
fi
docker compose up -d api 2>&1 | tail -2
RL_HEALTHY=false
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    RL_HEALTHY=true; break
  fi
  sleep 1
done

if $RL_HEALTHY; then
  GOT_429=false
  PAYLOAD='{"record":{"email":"rl@test.com","name":"RL","age":25},"contract":"customer","record_id":"rl-test"}'
  for i in 1 2 3 4 5; do
    RL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST http://localhost:8000/api/v1/validate \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD")
    if [ "$RL_HTTP" = "429" ]; then GOT_429=true; fi
  done
  if $GOT_429; then
    ok "POST /validate rate limit → 429 Too Many Requests after 3 req/min exceeded"
  else
    fail "Rate limit not enforced: sent 5 requests at 3/min limit, no 429 received"
  fi
else
  fail "Service failed to restart for rate limit test"
fi

# Restore original .env and restart; wait for healthy before next step
cp /tmp/smoke_ratelimit_env_backup .env
docker compose up -d api 2>&1 | tail -2
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/health > /dev/null 2>&1 && break
  sleep 1
done

# =============================================================================
step "34. Federation API — status, log, health, and SSE stream"
# =============================================================================
# /federation/status — standalone mode
FS=$(curl -sf http://localhost:8000/api/v1/federation/status || echo '{}')
check_json "GET /federation/status → opendqv_node_id + is_federated=false (standalone)" "$FS" "
import sys, json
d = json.load(sys.stdin)
assert 'opendqv_node_id' in d, f'missing opendqv_node_id: {d}'
assert d.get('is_federated') is False, f'expected standalone, got is_federated={d.get(\"is_federated\")}'
assert d.get('opendqv_node_state') in ('online','degraded','isolated'), f'bad node_state: {d.get(\"opendqv_node_state\")}'
print(f'    opendqv_node_id={d[\"opendqv_node_id\"]} state={d[\"opendqv_node_state\"]} is_federated={d[\"is_federated\"]}')
"

# /federation/log — replication cursor endpoint
FL=$(curl -sf "http://localhost:8000/api/v1/federation/log?since=0" || echo '{}')
check_json "GET /federation/log?since=0 → opendqv_node_id + count + events list" "$FL" "
import sys, json
d = json.load(sys.stdin)
assert 'opendqv_node_id' in d, f'missing opendqv_node_id: {d}'
assert 'count' in d and 'events' in d, f'missing count/events: {list(d.keys())}'
assert isinstance(d['events'], list), 'events must be a list'
print(f'    opendqv_node_id={d[\"opendqv_node_id\"]} count={d[\"count\"]} events={len(d[\"events\"])}')
"

# /federation/health — node health for control plane
FH=$(curl -sf "http://localhost:8000/api/v1/federation/health" || echo '{}')
check_json "GET /federation/health → opendqv_node_state + transition log" "$FH" "
import sys, json
d = json.load(sys.stdin)
assert 'opendqv_node_state' in d, f'missing opendqv_node_state: {d}'
assert d.get('opendqv_node_state') in ('online','degraded','isolated'), f'bad state: {d.get(\"opendqv_node_state\")}'
print(f'    opendqv_node_state={d[\"opendqv_node_state\"]}')
"

# /federation/events — SSE stream; limit=1 sends connected event then closes
SSE_OUT=$(curl -sN --max-time 5 \
  "http://localhost:8000/api/v1/federation/events?limit=1" 2>&1 || echo "")
SSE_CT=$(curl -sI "http://localhost:8000/api/v1/federation/events?limit=1" 2>/dev/null \
  | grep -i "content-type" | head -1 || echo "")
if echo "$SSE_OUT" | grep -q "event: connected" && \
   echo "$SSE_OUT" | grep -q "opendqv_node_id" && \
   echo "$SSE_OUT" | grep -q "node_state"; then
  ok "GET /federation/events?limit=1 → SSE stream emits 'connected' event with opendqv_node_id + node_state"
  echo "    $(echo "$SSE_OUT" | grep 'data:' | head -1 | cut -c1-80)"
else
  fail "SSE stream did not emit expected 'connected' event"
  echo "    output: ${SSE_OUT:0:200}"
fi

# =============================================================================
step "35. Contract name path traversal protection — 422 on malicious name"
# =============================================================================
TRAVERSAL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "http://localhost:8000/api/v1/import/odcs?save=true" \
  -H "Content-Type: application/json" \
  -d '{"apiVersion":"v3","kind":"DataContract","info":{"title":"../../etc/passwd","version":"1.0"},"schema":[]}')
if [ "$TRAVERSAL_HTTP" = "422" ]; then
  ok "Path traversal contract name → 422 Unprocessable Entity"
else
  fail "Expected 422 for path traversal contract name, got $TRAVERSAL_HTTP"
fi

# =============================================================================
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "${BOLD}CLEAN-ROOM TEST RESULTS${NC}"
echo "════════════════════════════════════════════════════════"
printf "  %-12s %s\n" "Passed:" "$(echo -e "${GREEN}${BOLD}${PASS}${NC}")"
printf "  %-12s %s\n" "Failed:" "$(echo -e "${RED}${BOLD}${FAIL}${NC}")"
echo ""

if [ ${#ERRORS[@]} -gt 0 ]; then
  echo -e "${RED}FAILURES:${NC}"
  for e in "${ERRORS[@]}"; do
    echo "  ✗ $e"
  done
  echo ""
fi

TOTAL=$((PASS + FAIL))
echo "  $PASS / $TOTAL checks passed"
echo ""

if [ $FAIL -eq 0 ]; then
  echo -e "${GREEN}${BOLD}  ALL CHECKS PASSED — APPROVED FOR PUBLIC RELEASE${NC}"
  exit 0
else
  echo -e "${RED}${BOLD}  RELEASE BLOCKED — $FAIL check(s) failed${NC}"
  exit 1
fi
