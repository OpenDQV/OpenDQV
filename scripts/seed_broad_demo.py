#!/usr/bin/env python3
"""
Schema-driven broad demo seeder — sends realistic pass/fail records for every
active contract not already covered by seed_demo_data.py.

Record generators are contract-aware: each contract's YAML rules are read
directly and valid/invalid records are built from the actual allowed_values
lists, min/max bounds, date_format rules, and regex pattern lookups.

Safe to re-run.
"""
import json
import os
import re as _re
import random
import time
import yaml
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

random.seed(99)

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("OPENDQV_TOKEN", "")

# Contracts dir — try env var first, then relative to this script's parent
_script_dir = Path(__file__).parent
CONTRACTS_DIR = Path(
    os.environ.get("OPENDQV_CONTRACTS_DIR", str(_script_dir.parent / "opendqv" / "contracts"))
)


# ── HTTP helpers ───────────────────────────────────────────────────────

def _call(method, path, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            time.sleep(2)
        return {}


def post(path, body):
    return _call("POST", path, body)


def validate(contract, record):
    post("/api/v1/validate", {"contract": contract, "record": record})


def rdate(days_back=365):
    return (date.today() - timedelta(days=random.randint(0, days_back))).isoformat()


def fdate(days=30):
    return (date.today() + timedelta(days=random.randint(1, days))).isoformat()


def rdt(days_back=90):
    d = date.today() - timedelta(days=random.randint(0, days_back))
    return f"{d.isoformat()}T{random.randint(6, 22):02d}:{random.randint(0, 59):02d}:00"


# ── Regex safe-value lookup ────────────────────────────────────────────

def _safe_regex_value(rule: dict) -> str:
    """Return a known-good value for the rule's regex pattern."""
    pattern = rule.get("pattern", "")
    name = rule.get("name", "").lower()
    field = rule.get("field", "").lower()

    # Email
    if "@" in pattern or "email" in name or "email" in field:
        return f"user{random.randint(100, 999)}@example.com"

    # Phone / E.164 (starts with \+ or \+[1-9] or \+44)
    if r"\+[1-9]" in pattern or r"\+44" in pattern or "phone" in name or "msisdn" in name:
        return f"+4477{random.randint(10000000, 99999999)}"

    # UK NI number — two letters + 6 digits + A-D
    if "ni_number" in name or "ni_number" in field or "national_insurance" in name:
        return "AB123456C"

    # NHS number
    if "nhs" in name or "nhs" in field:
        return "123 456 7890"

    # UK postcode: [A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}
    if "postcode" in name or "postcode" in field or (r"[A-Z]{1,2}" in pattern and r"\d[A-Z]{2}" in pattern):
        return "SW1A 1AA"

    # LEI (Legal Entity Identifier): [A-Z0-9]{18}[0-9]{2}
    if "[A-Z0-9]{18}" in pattern:
        return "HWUPKR0MPOU8FGXBT394"  # example LEI

    # MIC (Market Identifier Code): [A-Z]{4}
    if pattern == "^[A-Z]{4}$" or ("[A-Z]{4}" in pattern and len(pattern) < 15):
        return "XLON"

    # ISIN — two uppercase letters + 10 alphanumeric + 1 check digit: [A-Z]{2}[A-Z0-9]{9}[0-9]
    if "[A-Z]{2}" in pattern and ("[A-Z0-9]{9}" in pattern or r"\d{10}" in pattern):
        return "GB0001234567"

    # VIN
    if "vin" in name or "vin" in field:
        return "WBA12345678901234"

    # Date patterns — YYYY-MM-DD (using either \d or [0-9] notation)
    has_year = r"\d{4}" in pattern or "[0-9]{4}" in pattern
    if has_year and ("0[1-9]" in pattern or r"\d{2}-\d{2}" in pattern):
        return rdate()

    # Digit-separator patterns like \d{3}-\d{2,3} (network/MCC-MNC codes)
    if ("-" in pattern or "." in pattern) and (r"\d{3}" in pattern or "[0-9]{3}" in pattern):
        return "234-20"

    # Patterns with alternation (|) — use the first branch for digit extraction
    first_branch = pattern.split("|")[0].lstrip("^").rstrip("$")

    # Pure digit patterns: [0-9]{N} or \d{N}  (exact length)
    m = _re.search(r"(?:\[0-9\]|\\d)\{(\d+)\}$", first_branch)
    if m:
        n = int(m.group(1))
        return "".join(str(random.randint(0, 9)) for _ in range(n))

    # Digit range: \d{M,N} or [0-9]{M,N} — use min length
    m = _re.search(r"(?:\[0-9\]|\\d)\{(\d+),(\d+)\}", first_branch)
    if m:
        n = int(m.group(1))
        return "".join(str(random.randint(0, 9)) for _ in range(n))

    # Any pure digit pattern (might have extra anchors/chars)
    m = _re.search(r"(?:\[0-9\]|\\d)\{(\d+)\}", first_branch)
    if m:
        n = int(m.group(1))
        return "".join(str(random.randint(0, 9)) for _ in range(n))

    # ISO 4217 currency code — three uppercase letters
    if "[A-Z]{3}" in pattern or "[a-zA-Z]{3}" in pattern:
        return "GBP"

    # ISO country code — two uppercase letters
    if "[A-Z]{2}" in pattern and len(pattern) < 25:
        return "GB"

    # Two lowercase letters (language code)
    if "[a-z]{2}" in pattern and len(pattern) < 15:
        return "en"

    # MPAN — 13-digit electricity meter
    if "mpan" in name or "mpan" in field:
        return "1234567890123"

    # MPRN — 6-10 digits
    if "mprn" in name or "mprn" in field or r"\d{6,10}" in pattern:
        return "1234567"

    # Alphanumeric / generic fallback
    return f"VALUE{random.randint(1000, 9999)}"


# ── Schema-driven record generators ───────────────────────────────────

def make_valid_record(rules: list) -> dict:
    """
    Build a valid record by introspecting the contract's rules.

    Rules are applied in priority order so that more specific constraints
    override less specific ones on the same field:
      1. allowed_values (most specific — exact set of valid values)
      2. regex / date_format / min_length / max_length  (format constraints)
      3. min / max / range  (numeric bounds)
      4. not_empty  (least specific — just needs a non-empty value)
      5. compare  (last — depends on other fields being set first)
    """
    record: dict = {}

    # ── Pass 1: allowed_values + lookup (highest priority) ────────────
    for rule in rules:
        field = rule.get("field")
        if not field:
            continue
        if rule.get("type") == "allowed_values":
            vals = rule.get("allowed_values") or []
            if vals:
                record[field] = random.choice(vals)
        elif rule.get("type") == "lookup":
            vals = _load_ref_values(rule.get("lookup_file", ""))
            if vals:
                record[field] = random.choice(vals)

    # ── Pass 2: regex / date_format / length constraints ──────────────
    for rule in rules:
        field = rule.get("field")
        if not field:
            continue
        if field in record:  # already set by allowed_values
            continue
        rule_type = rule.get("type", "")
        if rule_type == "regex":
            record[field] = _safe_regex_value(rule)
        elif rule_type == "date_format":
            record[field] = rdate()
        elif rule_type in ("min_length", "max_length"):
            min_l = int(rule.get("min_length") or 1)
            record[field] = "x" * min_l + str(random.randint(0, 99))

    # ── Pass 3: numeric bounds ─────────────────────────────────────────
    for rule in rules:
        field = rule.get("field")
        if not field or field in record:
            continue
        rule_type = rule.get("type", "")
        if rule_type in ("min", "range"):
            lo = float(rule.get("min") or rule.get("min_value") or 0)
            hi_raw = rule.get("max") or rule.get("max_value")
            hi = float(hi_raw) if hi_raw is not None else max(lo * 10, lo + 1000)
            if hi <= lo:
                hi = lo + 100
            record[field] = round(random.uniform(lo, hi), 2)
        elif rule_type == "max":
            hi = float(rule.get("max") or rule.get("max_value") or 1000)
            record[field] = round(random.uniform(0, hi), 2)

    # ── Pass 4: not_empty fallback ─────────────────────────────────────
    for rule in rules:
        field = rule.get("field")
        if not field or field in record:
            continue
        if rule.get("type") == "not_empty":
            record[field] = f"value-{random.randint(1, 999)}"

    # ── Pass 4b: required_if + date_diff (conditional presence) ──────
    for rule in rules:
        field = rule.get("field")
        if not field:
            continue
        rule_type = rule.get("type", "")

        if rule_type == "required_if":
            cond = rule.get("required_if") or {}
            cond_field = cond.get("field")
            cond_value = str(cond.get("value", ""))
            if cond_field and str(record.get(cond_field, "")) == cond_value:
                # Condition triggered — ensure field is set to something non-empty
                if not record.get(field):
                    record[field] = f"value-{random.randint(1, 999)}"

        elif rule_type == "date_diff":
            # Must be within N days of another date field — use same date
            ref_field = rule.get("date_diff_field")
            if ref_field and ref_field in record and field not in record:
                record[field] = record[ref_field]
            elif ref_field and ref_field in record:
                # Already set — overwrite to ensure it satisfies the constraint
                record[field] = record[ref_field]

    # ── Pass 5: compare rules (need other fields already set) ──────────
    for rule in rules:
        if rule.get("type") != "compare":
            continue
        field = rule.get("field")
        compare_to = rule.get("compare_to")
        compare_op = rule.get("compare_op", "gte")
        if not field or not compare_to:
            continue
        base_val = record.get(compare_to)
        if base_val is None:
            continue

        if isinstance(base_val, (int, float)):
            # Numeric compare — always set to satisfy the constraint
            if compare_op in ("gte", "gt"):
                offset = 1 if compare_op == "gt" else 0
                record[field] = round(base_val + offset + random.uniform(0, 50), 2)
            elif compare_op in ("lte", "lt"):
                offset = 1 if compare_op == "lt" else 0
                record[field] = round(max(0, base_val - offset - random.uniform(0, 50)), 2)
            else:
                record[field] = base_val
        elif isinstance(base_val, str) and len(base_val) == 10 and base_val[4] == "-":
            # Date string compare — set field to base date (same day satisfies >=)
            # For gt, use the next day; for gte/eq, use same day
            from datetime import date as _date, timedelta as _td
            try:
                base_date = _date.fromisoformat(base_val)
                if compare_op == "gt":
                    record[field] = (base_date + _td(days=1)).isoformat()
                else:  # gte, lte, lt
                    record[field] = base_date.isoformat()
            except ValueError:
                record[field] = base_val
        else:
            record[field] = base_val

    return record


def make_invalid_record(valid: dict, rules: list) -> dict:
    """
    Build an invalid record by corrupting one field in the valid record.

    Priority: corrupt an allowed_values/lookup field first (clear failure),
    then fall back to emptying a not_empty field.
    """
    record = dict(valid)

    constrained_rules = [
        r for r in rules
        if r.get("type") in ("allowed_values", "lookup") and r.get("field")
    ]
    if constrained_rules:
        field = random.choice(constrained_rules)["field"]
        record[field] = "INVALID_SEED_VALUE"
        return record

    ne_rules = [
        r for r in rules
        if r.get("type") == "not_empty" and r.get("field")
        and r.get("severity", "error") == "error"
    ]
    if ne_rules:
        record[random.choice(ne_rules)["field"]] = ""
        return record

    # Fallback — corrupt the first field
    if record:
        key = next(iter(record))
        record[key] = None

    return record


# ── Contract loader ────────────────────────────────────────────────────

def load_contract_rules(contract_name: str) -> list:
    """Load a contract's rules from its YAML file."""
    yaml_path = CONTRACTS_DIR / f"{contract_name}.yaml"
    if not yaml_path.exists():
        return []
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return raw.get("contract", {}).get("rules", [])


def _load_ref_values(lookup_file: str) -> list:
    """Load values from a ref txt file (one value per line)."""
    ref_path = CONTRACTS_DIR / lookup_file
    if not ref_path.exists():
        return []
    lines = [ln.strip() for ln in ref_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines


def generate_record(rules: list, valid: bool) -> dict:
    if valid:
        return make_valid_record(rules)
    return make_invalid_record(make_valid_record(rules), rules)


# ── Seeder loop ────────────────────────────────────────────────────────

def seed_n(contract, rules, n=40, fail_every=6):
    pass_pct = round(100 - 100 / fail_every)
    print(f"  {contract} ({n} records, ~{pass_pct}% pass) …")
    sent = 0
    for i in range(n):
        valid = (i % fail_every != 0)
        record = generate_record(rules, valid)
        validate(contract, record)
        sent += 1
    print(f"    ✓ {sent} sent")


# ── Contract list — (name, n_records, fail_every) ─────────────────────
#
# These 32 contracts are not covered by seed_demo_data.py.
# n_records and fail_every control volume and pass rate per contract.

CONTRACTS = [
    ("hr_employee",                    50, 6),
    ("insurance_claim",                45, 7),
    ("manufacturing_iot",              60, 5),
    ("retail_product",                 40, 8),
    ("energy_meter_reading",           50, 6),
    ("real_estate_property",           40, 7),
    ("telecoms_cdr",                   45, 6),
    ("travel_booking",                 35, 8),
    ("technology_event",               50, 5),
    ("financial_trade",                40, 6),
    ("pharma_clinical_trial",          30, 7),
    ("water_utility_reading",          40, 8),
    ("agriculture_batch",              35, 6),
    ("media_content",                  40, 7),
    ("public_sector_service",          35, 6),
    ("automotive_vehicle",             40, 7),
    ("education_student",              35, 6),
    ("fmcg_product",                   40, 8),
    ("mifid_transaction_report",       30, 5),
    ("sox_control_test",               25, 6),
    ("ppds_menu_item",                 35, 8),
    ("martyns_law_event",              25, 5),
    ("pretix_event",                   30, 7),
    ("dora_ict_incident",              25, 6),
    ("gdpr_dsar_request",              25, 7),
    ("hipaa_disclosure_accounting",    20, 6),
    ("financial_services_customer",    35, 7),
    ("sf_lead",                        40, 8),
    ("companies_house_filing",         30, 6),
    ("universal_benchmark",            40, 7),
    ("building_safety_golden_thread",  20, 5),
    ("martyns_law_venue",              25, 6),
]


def main():
    print(f"OpenDQV schema-driven broad seeder — {BASE_URL}")
    print(f"Contracts dir: {CONTRACTS_DIR}")
    print(f"Seeding {len(CONTRACTS)} contracts …\n")

    total = 0
    skipped = 0
    for contract, n, fail_every in CONTRACTS:
        rules = load_contract_rules(contract)
        if not rules:
            print(f"  {contract} — YAML not found, skipping")
            skipped += 1
            continue
        seed_n(contract, rules, n=n, fail_every=fail_every)
        total += n
        time.sleep(0.3)  # stay under rate limit

    print(
        f"\nDone — ~{total} validation events sent across "
        f"{len(CONTRACTS) - skipped} contracts ({skipped} skipped)."
    )


if __name__ == "__main__":
    main()
