"""
Demo: UK Ofcom / Online Safety Act — social media age verification.

Three cases that mirror real regulatory scenarios:
  1. Minor (age 11, DOB confirms under-13) — blocked, no appeal
  2. Teen (age 17, self-declared, no verification) — allowed with advisory
  3. Adult (age 25, government ID verified) — full audit trail, clean pass

Contract: age_compliance_record (social_media_age_compliance.yaml)
"""

import json
import textwrap
import urllib.request

BASE = "http://localhost:8000"
CONTRACT = "age_compliance_record"

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


_COL = 22   # field column width
_WRAP = 72  # message wrap width


def _print_row(icon: str, field: str, message: str) -> None:
    prefix = f"  {icon} {BOLD}{field:<{_COL}}{RESET} "
    indent = " " * (5 + _COL)
    lines = textwrap.wrap(message, width=_WRAP)
    print(prefix + lines[0])
    for line in lines[1:]:
        print(indent + line)


def post(body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/api/v1/validate", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def show(label: str, record: dict, note: str = "") -> None:
    print(f"\n{BOLD}{CYAN}❯ {label}{RESET}")
    if note:
        print(f"  {DIM}{note}{RESET}")
    res = post({"contract": CONTRACT, "record": record})
    if res["valid"]:
        print(f"  result    {GREEN}✓ valid{RESET}")
    else:
        print(f"  result    {RED}✗ blocked{RESET}")
        for e in res["errors"]:
            _print_row(RED + "✗" + RESET, e["field"], e["message"])
    for w in res.get("warnings", []):
        _print_row(YELLOW + "⚠" + RESET, w["field"], w["message"])


# ── Case 1: Minor — age 11, DOB confirms it ───────────────────────────────────
show(
    "Case 1 — Minor (age 11)",
    note="UK Online Safety Act: min age 13, no exceptions",
    record={
        "user_id":    "USR-0001",
        "age":        11,
        "dob":        "2014-08-20",
        "verified_identity": "FALSE",
    },
)

# ── Case 2: Teen — age 17, self-declared, no verification ─────────────────────
show(
    "Case 2 — Teen (age 17, self-declared)",
    note="Permitted but Ofcom enhanced-restrictions advisory applies",
    record={
        "user_id":    "USR-0042",
        "age":        17,
        "dob":        "2008-11-05",
        "verified_identity": "FALSE",
    },
)

# ── Case 3: Verified adult — full audit trail ─────────────────────────────────
show(
    "Case 3 — Adult (age 25, Government ID verified)",
    note="Full verification chain — meets Ofcom age assurance guidance",
    record={
        "user_id":               "USR-0089",
        "age":                   25,
        "dob":                   "2000-06-15",
        "verified_identity":     "TRUE",
        "verification_method":   "GOVERNMENT_ID",
        "verification_timestamp": "2026-03-14T09:30:00Z",
    },
)

print()
