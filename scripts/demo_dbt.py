"""
OpenDQV + dbt integration demo — import workflow.

Shows a dbt schema.yml being imported as an OpenDQV validation contract,
followed by a record validation against the imported contract.
Stdlib + ANSI codes only — designed for VHS recording.
"""

import sys
import time

R     = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


def hr():
    p(f"{DIM}{'─' * 74}{R}")


# ── Header ──────────────────────────────────────────────────────────────────
time.sleep(0.3)
p(f"\n{BOLD}{CYAN}OpenDQV + dbt — import your schema.yml as a validation contract{R}")
p(f"{DIM}  Shift-left: validate records before they enter the warehouse{R}\n")
time.sleep(0.8)

# ── Show the input: sample dbt schema.yml ───────────────────────────────────
hr()
p(f"\n{CYAN}  models/schema.yml{R}")
hr()
time.sleep(0.4)

schema_lines = [
    "version: 2",
    "models:",
    "  - name: orders",
    "    description: 'Core orders model'",
    "    columns:",
    "      - name: order_id",
    "        data_tests:",
    "          - not_null",
    "          - unique",
    "      - name: status",
    "        data_tests:",
    "          - not_null",
    "          - accepted_values:",
    "              values: [pending, confirmed, shipped, cancelled]",
    "      - name: amount",
    "        data_tests:",
    "          - not_null",
]

for line in schema_lines:
    p(f"  {DIM}{line}{R}", delay=0.06)

time.sleep(0.6)
hr()

# ── Run the import command ───────────────────────────────────────────────────
time.sleep(0.5)
p(f"\n{DIM}${R} opendqv import-dbt models/schema.yml\n")
time.sleep(1.2)

p(f"  {CYAN}Parsing{R}  models/schema.yml ...", delay=0.5)
p(f"  {CYAN}Model{R}    orders  ({BOLD}3 columns{R}, 5 dbt tests found)")
time.sleep(0.4)

# ── Show rule mapping ────────────────────────────────────────────────────────
p(f"\n  {DIM}Mapping dbt tests → OpenDQV rules:{R}")
mappings = [
    ("order_id",  "not_null",        "→", "not_empty",  ""),
    ("order_id",  "unique",          "→", "unique",     ""),
    ("status",    "not_null",        "→", "not_empty",  ""),
    ("status",    "accepted_values", "→", "regex",      "(converted to pattern)"),
    ("amount",    "not_null",        "→", "not_empty",  ""),
]
for field, src, arrow, dst, note in mappings:
    note_str = f"  {DIM}{note}{R}" if note else ""
    p(f"    {DIM}{field:<12}{R}  {src:<18}  {CYAN}{arrow}{R}  {GREEN}{dst}{R}{note_str}", delay=0.1)

time.sleep(0.6)

# ── Show contract YAML being written ────────────────────────────────────────
hr()
p(f"\n{CYAN}  Writing contract: contracts/orders.yaml{R}\n")
time.sleep(0.5)

contract_lines = [
    "contract:",
    "  name: orders",
    "  asset_id: \"dbt::orders\"",
    "  status: active",
    "  rules:",
    "    - name: order_id_not_null",
    "      type: not_empty",
    "      field: order_id",
    "      severity: error",
    "    - name: order_id_unique",
    "      type: unique",
    "      field: order_id",
    "      severity: error",
    "    - name: status_not_null",
    "      type: not_empty",
    "      field: status",
    "      severity: error",
    "    - name: status_accepted_values",
    "      type: regex",
    "      field: status",
    "      pattern: \"^(pending|confirmed|shipped|cancelled)$\"",
    "      severity: error",
    "    - name: amount_not_null",
    "      type: not_empty",
    "      field: amount",
    "      severity: error",
]

for line in contract_lines:
    p(f"  {DIM}{line}{R}", delay=0.05)

time.sleep(0.5)
p(f"\n  {GREEN}✓{R}  {BOLD}5 rules written{R}  ·  contract name: {BOLD}orders{R}  ·  asset_id: {DIM}dbt::orders{R}")
time.sleep(0.8)

hr()

# ── Validate a record against the imported contract ──────────────────────────
time.sleep(0.5)
p(f"\n{CYAN}  Validating a record against the imported contract...{R}\n")
time.sleep(0.4)

p(f"{DIM}${R} opendqv validate orders \\")
p("      '{\"order_id\": \"ORD-1042\", \"status\": \"confirmed\", \"amount\": 149.99}'\n")
time.sleep(1.4)

p(f"  {DIM}Contract:{R}  orders  ({DIM}dbt::orders{R})")
p(f"  {DIM}Record:{R}    order_id=ORD-1042  status=confirmed  amount=149.99\n")
time.sleep(0.5)

rule_results = [
    ("order_id_not_null",       "not_empty", "PASS"),
    ("order_id_unique",         "unique",    "PASS"),
    ("status_not_null",         "not_empty", "PASS"),
    ("status_accepted_values",  "regex",     "PASS"),
    ("amount_not_null",         "not_empty", "PASS"),
]

for name, rtype, result in rule_results:
    marker = f"{GREEN}✓{R}" if result == "PASS" else f"{RED}✗{R}"
    p(f"  {marker}  {name:<30}  {DIM}{rtype:<12}{R}  {GREEN}{result}{R}", delay=0.15)

time.sleep(0.6)
hr()

p(f"\n  {GREEN}✓{R}  {BOLD}PASS{R}  —  5/5 rules passed  ·  0 violations\n")
time.sleep(0.5)

# ── Closing ──────────────────────────────────────────────────────────────────
p(f"  {DIM}Contract is live — wire it into your pipeline{R}\n")
time.sleep(0.9)
