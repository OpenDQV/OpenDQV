"""
MCP scenario demo — no Docker, no MCP client required.

Calls the same core functions used by the MCP tools internally, proving the
end-to-end flow works from a plain Python script.

Scenarios:
  A. Use existing contract (social_media_age_compliance)
       list_contracts → validate minor (fail) → validate adult (pass) → explain_rule
  B. Create contract on the fly (MCP_demo_app_users)
       create_draft → validate immediately (DRAFT is testable)

Usage:
    cd ~/OpenDQV && source .venv/bin/activate
    python scripts/demo_mcp_scenario.py
"""

import sys
from pathlib import Path

# Project root → importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core.contracts import ContractRegistry
from core.validator import validate_record
from core.explainer import explain_rule

# ── Colour palette ────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def header(text: str) -> None:
    width = 68
    print(f"\n{BOLD}{CYAN}{'─' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * width}{RESET}")


def agent(text: str) -> None:
    print(f"\n{BOLD}  ❯ agent:{RESET} {text}")


def result_ok(text: str) -> None:
    print(f"  {GREEN}✓{RESET} {text}")


def result_fail(text: str) -> None:
    print(f"  {RED}✗{RESET} {text}")


def result_info(text: str) -> None:
    print(f"  {DIM}{text}{RESET}")


def show_errors(errors: list, warnings: list) -> None:
    for e in errors:
        result_fail(f"{BOLD}{e['field']}{RESET}: {e['message']}")
    for w in warnings:
        print(f"  {YELLOW}⚠{RESET} {BOLD}{w['field']}{RESET}: {w['message']}")


# ── Bootstrap registry ────────────────────────────────────────────────
registry = ContractRegistry(config.CONTRACTS_DIR)

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        result_ok(label + (f"  {DIM}({detail}){RESET}" if detail else ""))
    else:
        FAIL += 1
        result_fail(label + (f"  {DIM}({detail}){RESET}" if detail else ""))


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO A — use existing contract: social_media_age_compliance
# ═══════════════════════════════════════════════════════════════════════
header("SCENARIO A — existing contract: social_media_age_compliance")

CONTRACT = "social_media_age_compliance"

# ── A1: list_contracts ────────────────────────────────────────────────
agent("list_contracts()")
contracts = registry.list_contracts()
names = [c["name"] for c in contracts]
age_contract_present = CONTRACT in names
check(
    "social_media_age_compliance in list_contracts()",
    age_contract_present,
    f"{len(contracts)} contracts visible",
)
if age_contract_present:
    meta = next(c for c in contracts if c["name"] == CONTRACT)
    result_info(
        f"  status={meta['status']}  rules={meta['rule_count']}  "
        f"owner={meta.get('owner', '—')}"
    )

# ── A2: validate_record — minor (should fail) ─────────────────────────
agent("validate_record(social_media_age_compliance, minor record) → expect FAIL")
contract_obj = registry.get(CONTRACT)
minor_record = {
    "user_id": "USR-0001",
    "age": 11,
    "dob": "2014-08-20",
    "verified_identity": "FALSE",
}
res = validate_record(minor_record, contract_obj.rules, CONTRACT)
check("minor record is invalid (age 11)", not res["valid"])
show_errors(res.get("errors", []), res.get("warnings", []))

# ── A3: validate_record — adult, verified (should pass) ──────────────
agent("validate_record(social_media_age_compliance, adult verified record) → expect PASS")
adult_record = {
    "user_id": "USR-0089",
    "age": 25,
    "dob": "2000-06-15",
    "verified_identity": "TRUE",
    "verification_method": "GOVERNMENT_ID",
    "verification_timestamp": "2026-03-14T09:30:00Z",
}
res_adult = validate_record(adult_record, contract_obj.rules, CONTRACT)
check("adult verified record is valid", res_adult["valid"])
show_errors(res_adult.get("errors", []), res_adult.get("warnings", []))

# ── A4: explain_rule on a failing rule from minor result ──────────────
agent("explain_rule(age_minimum_13) → remediation guidance")
failing_rules = [
    r for r in contract_obj.rules
    if r.name == "age_minimum_13"
]
if failing_rules:
    info = explain_rule(failing_rules[0])
    check(
        "explain_rule returns structured guidance",
        bool(info.get("explanation")) and bool(info.get("valid_examples")),
    )
    result_info(f"  rule_type : {info['rule_type']}")
    result_info(f"  explanation : {info['explanation']}")
    result_info(f"  valid_examples : {info['valid_examples']}")
    result_info(f"  invalid_examples : {info['invalid_examples']}")
else:
    result_fail("age_minimum_13 rule not found in contract")
    FAIL += 1


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO B — create contract on the fly: MCP_demo_app_users
# ═══════════════════════════════════════════════════════════════════════
header("SCENARIO B — create draft contract: MCP_demo_app_users")

DRAFT_NAME = "MCP_demo_app_users"

# Clean up any leftover contract from a previous run
_existing_yaml = config.CONTRACTS_DIR / f"{DRAFT_NAME}.yaml"
if _existing_yaml.exists():
    _existing_yaml.unlink()
    # Re-init registry so it doesn't have the stale entry
    registry = ContractRegistry(config.CONTRACTS_DIR)

# ── B1: create_draft ──────────────────────────────────────────────────
agent(f"create_draft('{DRAFT_NAME}', 3 rules)")
rules_data = [
    {
        "name": "email_required",
        "type": "not_empty",
        "field": "email",
        "error_message": "email is required",
    },
    {
        "name": "email_format",
        "type": "regex",
        "field": "email",
        "pattern": r"^[^@]+@[^@]+\.[^@]+$",
        "error_message": "Must be a valid email address",
    },
    {
        "name": "username_min_length",
        "type": "min_length",
        "field": "username",
        "min_length": 3,
        "error_message": "Username must be at least 3 characters",
    },
]
try:
    draft = registry.create_draft(
        name=DRAFT_NAME,
        description="Basic user registration validation — MCP demo",
        owner="Platform Engineering",
        created_by="demo@example.com",
        rules_data=rules_data,
    )
    check(
        f"DRAFT contract created: {draft.name}",
        draft.name == DRAFT_NAME,
        f"status={draft.status.value}  rules={len(draft.rules)}  source={draft.source}",
    )
except ValueError as exc:
    result_fail(f"create_draft failed: {exc}")
    FAIL += 1
    draft = None

# ── B2: validate against DRAFT — invalid record ───────────────────────
if draft:
    agent(f"validate_record('{DRAFT_NAME}', bad record) → expect FAIL (DRAFT is testable)")
    bad_record = {"email": "not-an-email", "username": "x"}
    res_bad = validate_record(bad_record, draft.rules, DRAFT_NAME)
    check("bad record is invalid against DRAFT contract", not res_bad["valid"])
    show_errors(res_bad.get("errors", []), res_bad.get("warnings", []))

    # ── B3: validate against DRAFT — valid record ─────────────────────
    agent(f"validate_record('{DRAFT_NAME}', good record) → expect PASS")
    good_record = {"email": "alice@example.com", "username": "alice_42"}
    res_good = validate_record(good_record, draft.rules, DRAFT_NAME)
    check("good record is valid against DRAFT contract", res_good["valid"])
    show_errors(res_good.get("errors", []), res_good.get("warnings", []))

    # ── Clean up YAML written to contracts/ ───────────────────────────
    _draft_yaml = config.CONTRACTS_DIR / f"{DRAFT_NAME}.yaml"
    if _draft_yaml.exists():
        _draft_yaml.unlink()
        result_info("(demo YAML cleaned up from contracts/)")


# ═══════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════
header("Summary")
total = PASS + FAIL
colour = GREEN if FAIL == 0 else RED
print(f"\n  {colour}{BOLD}{PASS}/{total} checks passed{RESET}\n")

if FAIL > 0:
    sys.exit(1)
