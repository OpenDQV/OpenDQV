"""
Demo script for the OpenDQV README GIF.
Runs three API calls showing the core value prop:
  bad data → rejected with clear errors
  good data → accepted
"""

import json
import sys
import urllib.request

BASE = "http://localhost:8000"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
        return json.loads(r.read())


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def hr(char="─", n=54):
    print(f"{DIM}{char * n}{RESET}")


# ── 1. Health ──────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{CYAN}❯ Health check{RESET}")
h = get("/health")
print(f"  status    {GREEN}{h['status']}{RESET}")
print(f"  auth_mode {CYAN}{h['auth_mode']}{RESET}")

# ── 2. Invalid record ─────────────────────────────────────────────────────────
print(f"\n{BOLD}{CYAN}❯ Validate — bad customer record{RESET}")
bad = post("/api/v1/validate", {
    "contract": "customer",
    "record": {
        "email": "not-an-email",
        "age": -5,
        "name": "",
        "phone": "abc",
        "score": 150,
        "username": "bad name!",
    },
})
status = f"{RED}✗ invalid{RESET}" if not bad["valid"] else f"{GREEN}✓ valid{RESET}"
print(f"  result    {status}")
for e in bad["errors"]:
    print(f"  {RED}✗{RESET} {BOLD}{e['field']:<12}{RESET} {e['message']}")

# ── 3. Valid record ───────────────────────────────────────────────────────────
print(f"\n{BOLD}{CYAN}❯ Validate — corrected record{RESET}")
good = post("/api/v1/validate", {
    "contract": "customer",
    "record": {
        "email": "alice@example.com",
        "age": 29,
        "name": "Alice Smith",
        "phone": "+447700900123",
        "score": 87,
        "username": "alice_smith",
        "password": "Secur3Pass",
        "date": "2024-03-15",
        "balance": 1250.0,
    },
})
status = f"{GREEN}✓ valid{RESET}" if good["valid"] else f"{RED}✗ invalid{RESET}"
print(f"  result    {status}")
print(f"  contract  {good['contract']} v{good['version']}")
print(f"  errors    {len(good['errors'])}")
print()
