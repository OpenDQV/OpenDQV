"""
Demo script for the README GIF.
Story: contract definition → bad record rejected (422) → fix it (200)

Modelled after scripts/demo.py — same style, stdlib only, controlled pacing.
"""

import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"

R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


# ── 1. Contract ────────────────────────────────────────────────────────────────
time.sleep(0.4)
p(f"\n{BOLD}{CYAN}❯ Data contract — order{R}")
p(f"  {DIM}contracts/order.yaml{R}")
p(f"  rules   {CYAN}valid_email{R}  ·  {CYAN}amount_positive{R}  ·  {CYAN}status_valid{R}")
p(f"  owner   {DIM}Data Governance{R}")

time.sleep(0.9)

# ── 2. Invalid record ──────────────────────────────────────────────────────────
p(f"\n{BOLD}{CYAN}❯ Validate — bad record{R}")
p(f"  {DIM}email=not-an-email  amount=-5  status=unknown{R}")
time.sleep(0.5)

bad = post("/api/v1/validate", {
    "contract": "order",
    "record": {
        "email": "not-an-email",
        "amount": -5,
        "status": "unknown",
    },
})

p(f"  result   {RED}✗ invalid  ({len(bad['errors'])} errors){R}")
for e in bad["errors"]:
    p(f"  {RED}✗{R}  {BOLD}{e['field']:<18}{R}  {e['message']}", delay=0.15)

time.sleep(1.0)

# ── 3. Valid record ────────────────────────────────────────────────────────────
p(f"\n{BOLD}{CYAN}❯ Validate — fixed record{R}")
p(f"  {DIM}email=alice@example.com  amount=49.99  status=pending{R}")
time.sleep(0.5)

good = post("/api/v1/validate", {
    "contract": "order",
    "record": {
        "email": "alice@example.com",
        "amount": 49.99,
        "status": "pending",
    },
})

p(f"  result   {GREEN}✓ valid{R}")
p(f"  contract {good['contract']} v{good['version']}")
p(f"  errors   {len(good['errors'])}")
p()
time.sleep(0.6)
