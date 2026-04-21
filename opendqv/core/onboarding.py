"""
OpenDQV Onboarding Wizard

Guides a new user from zero to their first successful validation
in under 90 seconds (after Docker images are pulled — first pull takes 2–3 min).

Usage:
    python -m opendqv.cli onboard
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Cross-platform temp dir — /tmp on Linux/Mac, %TEMP% on Windows
_SESSION_FILE = Path(tempfile.gettempdir()) / ".opendqv_session"


def _pid_alive(pid: int) -> bool:
    """Return True if the process is running. Cross-platform.

    On Unix, os.kill(pid, 0) checks liveness without sending a signal.
    On Windows, signal 0 is CTRL_C_EVENT — sending it to the current process
    raises KeyboardInterrupt. Use OpenProcess instead.
    """
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

# ── Rich is optional — plain-text fallback if not installed ───────────────────
try:
    from rich.console import Console
    from rich.rule import Rule
    from rich.text import Text

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Questionary is optional — numbered-list fallback if not installed ──────────
try:
    import questionary
    from questionary import Style as _QStyle

    HAS_QUESTIONARY = True
    WIZARD_STYLE = _QStyle([
        ("qmark",       "fg:cyan bold"),
        ("question",    "bold"),
        ("answer",      "fg:cyan bold"),
        ("pointer",     "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected",    "fg:cyan"),
        ("separator",   "fg:grey"),
        ("instruction", "fg:grey"),
    ])
except ImportError:
    HAS_QUESTIONARY = False
    WIZARD_STYLE = None

# Sentinel value for "Build my own" in questionary template picker
_BUILD_OWN = "__build_own__"

# ── Workbench PID lock file ────────────────────────────────────────────────────
_WORKBENCH_LOCK = Path(".opendqv_workbench.lock")


def _read_workbench_lock() -> "tuple[int, int] | None":
    """Return (pid, port) if lock file exists and the process is still alive."""
    try:
        data = json.loads(_WORKBENCH_LOCK.read_text())
        pid, port = int(data["pid"]), int(data["port"])
        if not _pid_alive(pid):
            return None
        return pid, port
    except Exception:
        return None


def _write_workbench_lock(pid: int, port: int) -> None:
    """Write lock file recording the live Streamlit PID and port."""
    _WORKBENCH_LOCK.write_text(json.dumps({"pid": pid, "port": port}))

# ── API PID lock file ─────────────────────────────────────────────────────────
_API_LOCK = Path(".opendqv_api.lock")


def _read_api_lock() -> "tuple[int, int] | None":
    """Return (pid, port) if lock file exists and the process is still alive."""
    try:
        data = json.loads(_API_LOCK.read_text())
        pid, port = int(data["pid"]), int(data["port"])
        if not _pid_alive(pid):
            return None
        return pid, port
    except Exception:
        return None


def _write_api_lock(pid: int, port: int) -> None:
    """Write lock file recording the live API PID and port."""
    _API_LOCK.write_text(json.dumps({"pid": pid, "port": port}))

# ── Logo ──────────────────────────────────────────────────────────────────────

LOGO = """\
 ██████╗ ██████╗ ███████╗███╗   ██╗██████╗  ██████╗ ██╗   ██╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔═══██╗██║   ██║
██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║  ██║██║   ██║██║   ██║
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║  ██║██║▄▄ ██║╚██╗ ██╔╝
╚██████╔╝██║     ███████╗██║ ╚████║██████╔╝╚██████╔╝ ╚████╔╝
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═════╝  ╚══▀▀═╝   ╚═══╝ """

TAGLINE = "Open Data Quality Validation  ·  v1.0.0"

# ── Field name → rule inference ───────────────────────────────────────────────

_FIELD_RULES: dict[str, dict] = {
    # Email
    "email":         {"type": "regex", "pattern": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", "error_message": "must be a valid email address"},
    "email_address": {"type": "regex", "pattern": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", "error_message": "must be a valid email address"},
    # Phone
    "phone":         {"type": "regex", "pattern": r"^\+?[\d\s\-\(\)]{7,20}$", "error_message": "must be a valid phone number"},
    "mobile":        {"type": "regex", "pattern": r"^\+?[\d\s\-\(\)]{7,20}$", "error_message": "must be a valid phone number"},
    "telephone":     {"type": "regex", "pattern": r"^\+?[\d\s\-\(\)]{7,20}$", "error_message": "must be a valid phone number"},
    # Names
    "name":          {"type": "not_empty", "error_message": "name is required"},
    "first_name":    {"type": "not_empty", "error_message": "first_name is required"},
    "last_name":     {"type": "not_empty", "error_message": "last_name is required"},
    "full_name":     {"type": "not_empty", "error_message": "full_name is required"},
    "surname":       {"type": "not_empty", "error_message": "surname is required"},
    # Age
    "age":           {"type": "range", "min": 0, "max": 150, "error_message": "age must be between 0 and 150"},
    # Dates
    "date":          {"type": "date_format", "error_message": "must be a valid date (YYYY-MM-DD)"},
    "created_at":    {"type": "date_format", "error_message": "must be a valid date"},
    "updated_at":    {"type": "date_format", "error_message": "must be a valid date"},
    "dob":           {"type": "date_format", "error_message": "must be a valid date of birth"},
    "birth_date":    {"type": "date_format", "error_message": "must be a valid date"},
    "date_of_birth": {"type": "date_format", "error_message": "must be a valid date"},
    "start_date":    {"type": "date_format", "error_message": "must be a valid date"},
    "end_date":      {"type": "date_format", "error_message": "must be a valid date"},
    # URLs
    "url":           {"type": "regex", "pattern": r"^https?://[^\s/$.?#].[^\s]*$", "error_message": "must be a valid URL"},
    "website":       {"type": "regex", "pattern": r"^https?://[^\s/$.?#].[^\s]*$", "error_message": "must be a valid URL"},
    "link":          {"type": "regex", "pattern": r"^https?://[^\s/$.?#].[^\s]*$", "error_message": "must be a valid URL"},
    # Postcodes
    "postcode":      {"type": "regex", "pattern": r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$", "error_message": "must be a valid UK postcode"},
    "zip":           {"type": "regex", "pattern": r"^\d{5}(-\d{4})?$", "error_message": "must be a valid ZIP code"},
    "zip_code":      {"type": "regex", "pattern": r"^\d{5}(-\d{4})?$", "error_message": "must be a valid ZIP code"},
    # Country
    "country":       {"type": "min_length", "min_length": 2, "error_message": "country must be at least 2 characters"},
    "country_code":  {"type": "regex", "pattern": r"^[A-Z]{2,3}$", "error_message": "must be a 2 or 3 letter country code"},
    # Money
    "amount":        {"type": "min", "min": 0, "error_message": "amount must be >= 0"},
    "price":         {"type": "min", "min": 0, "error_message": "price must be >= 0"},
    "cost":          {"type": "min", "min": 0, "error_message": "cost must be >= 0"},
    "revenue":       {"type": "min", "min": 0, "error_message": "revenue must be >= 0"},
    "balance":       {"type": "range", "min": -999999999, "max": 999999999, "error_message": "balance out of acceptable range"},
    "salary":        {"type": "min", "min": 0, "error_message": "salary must be >= 0"},
    # Scores / percentages / grades
    "score":         {"type": "range", "min": 0, "max": 100, "error_message": "score must be between 0 and 100"},
    "rating":        {"type": "range", "min": 0, "max": 5, "error_message": "rating must be between 0 and 5"},
    "percentage":    {"type": "range", "min": 0, "max": 100, "error_message": "percentage must be between 0 and 100"},
    "gpa":           {"type": "range", "min": 0, "max": 4, "error_message": "gpa must be between 0.0 and 4.0"},
    # Booleans
    "available":     {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    "active":        {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    "enabled":       {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    "is_deleted":    {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    "is_active":     {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    "is_enabled":    {"type": "regex", "pattern": r"^(true|false|yes|no|1|0|Y|N)$", "error_message": "must be a boolean value (true/false/yes/no/1/0/Y/N)"},
    # Retail / e-commerce
    "loyalty_tier":  {"type": "lookup", "ref": "ref/loyalty_tiers.txt"},
}

_ID_SUFFIXES = ("_id", "_code", "_ref", "_key", "_no", "_number", "_num", "_uuid")

# Exact-field-name → valid example for fields that don't match generic patterns
_FIELD_EXAMPLES: dict[str, str] = {
    # Healthcare
    "ward": "General",
    # Demographics / HR
    "gender": "M",
    "department": "Engineering",
    "job_title": "Analyst",
    "contract_type": "Permanent",
    # Automotive
    "vin": "1HGBH41JXMN109186",
    "make": "Toyota",
    "model": "Corolla",
    "fuel_type": "Petrol",
    "registration": "AB12 CDE",
    # Real estate
    "postcode": "SW1A 1AA",
    # Finance
    "currency": "GBP",
    "channel": "web",
    "carrier": "DHL",
    # Status / classification
    "status": "ACTIVE",
    "meter_status": "Active",
    "meter_type": "Smart",
    "epc_rating": "B",
    "service_type": "Planning",
    "claim_type": "Accident",
    "call_type": "Voice",
    "event_type": "page_view",
    "content_type": "video",
    "property_type": "Residential",
    "tariff_zone": "Zone-A",
    # Products / retail
    "brand": "Acme",
    "category": "Electronics",
    "product_name": "Premium Widget",
    "barcode": "5901234123457",
    "sku": "SKU-001",
    # Agriculture
    "crop_type": "Wheat",
    # Location
    "market": "UK",
    "region": "London",
    "location": "London",
    "origin": "London",
    "destination": "Paris",
    "rights_territory": "UK",
    # Tech
    "platform": "iOS",
    "sdk_version": "1.0.0",
    # Salesforce / CRM
    "company": "Acme Corp",
    "leadsource": "Web",
    # Telecoms
    "msisdn": "+447911123456",
    # Media
    "title": "The Cloud Report",
}

# Rule-type priority for multi-rule fields: lower number = wins (most restrictive)
_RULE_PRIORITY = {
    "regex":      0,
    "date_format": 1,
    "range":      2,
    "min":        3,
    "max":        4,
    "min_length": 5,
    "lookup":     6,
    "not_empty":  7,
}

# ── Template catalogue ─────────────────────────────────────────────────────────

# Friendly labels shown in the wizard template picker (contract name → label)
_TEMPLATE_LABELS: dict[str, str] = {
    "agriculture_batch":        "Agriculture            — harvests, batches, soil data",
    "automotive_vehicle":       "Automotive             — vehicle records, VIN, service",
    "banking_transaction":      "Banking                — transactions, accounts",
    "customer":                 "Customer               — generic customer record",
    "education_student":        "Education              — student records, GPA, enrolment",
    "ofgem_meter_reading":      "Energy & Utilities     — Ofgem-aligned meter readings, tariffs",
    "financial_trade":          "Financial Services     — trades, instruments",
    "consumer_goods_product":   "Consumer Goods         — products, sales, shelf life",
    "fmcg_product":             "Consumer Goods (FMCG)  — products, sales, shelf life",
    "social_media_age_compliance": "Social Media           — age compliance, DOB verification",
    "nhs_dsp_patient":          "Healthcare / NHS       — NHS DSP Toolkit-aligned patient records",
    "hr_employee":              "HR & Workforce         — employee records, payroll",
    "insurance_claim":          "Insurance              — claims, policies",
    "logistics_shipment":       "Logistics              — shipments, supply chain",
    "manufacturing_iot":        "Manufacturing & IoT    — sensor data, batches",
    "media_content":            "Media & Entertainment  — content, views, rights",
    "pharma_clinical_trial":    "Pharma & Life Sciences — clinical trials, GxP",
    "proof_of_play":            "Digital Advertising    — proof of play, impressions",
    "public_sector_service":    "Public Sector          — service records, government",
    "real_estate_property":     "Real Estate            — property listings, EPC",
    "retail_product":           "Retail & E-Commerce    — products, stock, pricing",
    "technology_event":         "Technology             — usage events, SaaS telemetry",
    "telecoms_cdr":             "Telecoms               — call detail records, CDR",
    "travel_booking":           "Travel & Hospitality   — bookings, PNR",
    "universal_benchmark":      "Universal Benchmark    — performance & load testing",
    "ppds_menu_item":           "QSR / Food Safety      — Natasha's Law PPDS allergen compliance",
    "martyns_law_venue":        "Public Safety / Venues — Martyn's Law terrorism preparedness",
    "martyns_law_event":        "Public Safety / Events — Martyn's Law qualifying events",
    "building_safety_golden_thread": "Building Safety        — Golden Thread, higher-risk buildings",
    "companies_house_filing":   "Corporate Compliance   — Companies House director/PSC identity verification",
    "gdpr_processing_record":   "Data Protection / GDPR — UK GDPR Article 30 ROPA",
    "gdpr_dsar_request":        "Data Protection / GDPR — UK GDPR Article 15 DSAR",
    "eu_gdpr_processing_record": "Data Protection / GDPR — EU GDPR Article 30 ROPA",
    "eu_gdpr_dsar_request":     "Data Protection / GDPR — EU GDPR Article 15 DSAR",
    "dora_ict_incident":        "Financial Resilience   — EU DORA ICT incident reporting (Jan 2025)",
    "hipaa_disclosure_accounting": "Healthcare / HIPAA  — US HIPAA PHI disclosure accounting (45 CFR 164.528)",
    "sox_control_test":         "Financial Controls     — US SOX 404 internal control test record",
    "mifid_transaction_report": "Capital Markets        — MiFID II/MiFIR Article 26 transaction reporting",
    "ofwat_meter_reading":      "Water Utilities        — Ofwat-aligned meter readings, consumption",
}

# Contracts that exist on disk but should not appear in the wizard picker
_EXCLUDED_TEMPLATES = {
    "sf_contact", "sf_lead", "financial_services_customer",
}


def infer_rule(field_name: str) -> dict:
    """Return a rule dict inferred from a field name."""
    lower = field_name.lower().strip()
    if lower in _FIELD_RULES:
        return {"field": field_name, **_FIELD_RULES[lower]}
    if any(lower.endswith(s) for s in _ID_SUFFIXES) or lower in ("id", "key", "ref", "uuid"):
        return {"type": "not_empty", "field": field_name, "error_message": f"{field_name} is required"}
    # Money-adjacent suffixes
    if any(lower.endswith(s) for s in ("_amount", "_price", "_cost", "_fee", "_total")):
        return {"type": "min", "min": 0, "field": field_name, "error_message": f"{field_name} must be >= 0"}
    # Date-adjacent suffixes
    if any(lower.endswith(s) for s in ("_date", "_at", "_time", "_on", "_dob")):
        return {"type": "date_format", "field": field_name, "error_message": f"{field_name} must be a valid date"}
    return {"type": "not_empty", "field": field_name, "error_message": f"{field_name} is required"}


def generate_contract_yaml(entity: str, fields: list[str]) -> str:
    """Generate a starter contract YAML string."""
    lines = [
        "contract:",
        f"  name: {entity}",
        '  version: "1.0"',
        '  description: "Generated by OpenDQV onboarding wizard"',
        '  owner: "Data Team"',
        "  status: active",
        "",
        "  rules:",
    ]
    for f in fields:
        rule = infer_rule(f)
        rule_suffix = "required" if rule["type"] == "not_empty" else rule["type"]
        lines += [
            f"    - name: {f}_{rule_suffix}",
            f"      field: {rule['field']}",
            f"      type: {rule['type']}",
        ]
        if rule["type"] == "regex":
            pattern_val = rule["pattern"]
            lines.append(f"      pattern: '{pattern_val}'")
        elif rule["type"] == "range":
            lines += [f"      min: {rule['min']}", f"      max: {rule['max']}"]
        elif rule["type"] == "min":
            lines.append(f"      min: {rule['min']}")
        elif rule["type"] == "min_length":
            lines.append(f"      min_length: {rule['min_length']}")
        lines += [
            "      severity: error",
            f'      error_message: "{rule["error_message"]}"',
            "",
        ]
    return "\n".join(lines)


def _build_valid_from_regex(pattern: str, error_msg: str) -> str:
    """Return a valid example value for a regex rule using multiple inference strategies."""
    import re as _re

    # 1. "e.g." hint in error message — most reliable signal
    m = _re.search(r'e\.g\.?\s+([^\s,)]+)', error_msg)
    if m:
        hint = m.group(1)
        # Replace placeholder X-runs (e.g. ADV-XXXXXXXX → ADV-123456)
        if _re.search(r'X{2,}', hint):
            hint = _re.sub(r'X+', lambda mm: '1' * len(mm.group()), hint)
        return hint

    # 2. Enum: pattern like ^(OPT_A|OPT_B|OPT_C)$ → first option
    em = _re.match(r'^\^?\(([A-Z][A-Z0-9_]*(?:\|[A-Z][A-Z0-9_]*)+)\)', pattern)
    if em:
        return em.group(1).split('|')[0]

    # 3. Structural pattern recognition
    if 'ADV-' in pattern:
        return "ADV-123456"
    if '@' in pattern:
        return "alice@example.com"
    # Date/datetime pattern: requires YYYY-style {4} digit group and a dash separator
    if (r'[0-9]{4}' in pattern or r'\d{4}' in pattern) and '-' in pattern:
        if 'T' in pattern and ':' in pattern:   # ISO 8601 datetime (with time component)
            return "1990-06-15T12:00:00Z"
        return "1990-06-15"
    # Strict E.164: ^\+[1-9]... OR ^\+?[1-9]\d{N}$ (digit-only, no spaces)
    if (r'\+[1-9]' in pattern
            or (r'\+?' in pattern and r'\d{' in pattern and r'[\d\s' not in pattern)):
        return "+447911123456"
    if r'[A-Z]{1,2}' in pattern and r'\d[A-Z]{2}' in pattern:  # UK postcode
        return "SW1A 1AA"
    if r'\+?' in pattern and r'[\d\s' in pattern:               # loose phone
        return "+44 7911 123456"

    # 4. Error-message keyword inference
    msg_lower = error_msg.lower()
    if 'postcode' in msg_lower or 'postal' in msg_lower:
        return "SW1A 1AA"
    if 'phone' in msg_lower or 'mobile' in msg_lower or 'msisdn' in msg_lower:
        return "+44 7911 123456"
    if 'email' in msg_lower:
        return "alice@example.com"

    return "SAMPLE"


def _load_first_lookup_value(lookup_file: str, field: str) -> str:
    """Return the first valid value from a lookup file, or a field-name-based fallback."""
    if lookup_file:
        for base in [Path("."), Path("contracts"), Path("examples/starter_contracts")]:
            path = base / lookup_file
            try:
                lines = [ln.strip() for ln in path.read_text().splitlines()
                         if ln.strip() and not ln.strip().startswith("#")]
                if lines:
                    return lines[0]
            except OSError:
                pass
    # Fallback by field name when file isn't readable
    f = field.lower()
    if "verified" in f:
        return "TRUE"
    if "method" in f or "type" in f:
        return "DOCUMENT_CHECK"
    if "status" in f:
        return "ACTIVE"
    if "currency" in f:
        return "GBP"
    return "VALID"


def build_sample_records_from_rules(rules: list[dict]) -> tuple[dict, dict]:
    """Build valid/invalid sample records using actual contract rule definitions.

    When a field appears in multiple rules (e.g. not_empty + regex), the most
    restrictive rule wins according to _RULE_PRIORITY (lower number = stricter).
    """
    # Pass 1: select the highest-priority (lowest number) rule per field
    best: dict[str, dict] = {}  # field → winning rule dict
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        f = rule.get("field")
        if not f:
            continue
        rtype = rule.get("type", "not_empty")
        priority = _RULE_PRIORITY.get(rtype, 8)
        if f not in best or priority < _RULE_PRIORITY.get(best[f].get("type", "not_empty"), 8):
            best[f] = rule

    # Pass 2: generate samples from the winning rule for each field
    valid: dict = {}
    invalid: dict = {}
    for f, rule in best.items():
        rtype = rule.get("type", "not_empty")
        if rtype == "not_empty":
            _v, _ = build_sample_records([f])
            valid[f], invalid[f] = _v[f], ""
        elif rtype == "date_format":
            valid[f], invalid[f] = "1990-06-15", "not-a-date"
        elif rtype == "regex":
            valid[f] = _build_valid_from_regex(rule.get("pattern", ""), rule.get("error_message", ""))
            invalid[f] = "INVALID"
        elif rtype == "range":
            mid = (rule.get("min", 0) + rule.get("max", 100)) // 2
            valid[f] = mid
            invalid[f] = rule.get("min", 0) - 1
        elif rtype == "min":
            min_val = rule.get("min", 1)
            lower_f = f.lower()
            if any(x in lower_f for x in ("price", "amount", "cost", "fee", "revenue", "salary", "total")):
                valid_val = max(99.99, min_val + 1)
            elif any(x in lower_f for x in ("quantity", "qty", "count", "volume", "units")):
                valid_val = max(10, min_val + 1)
            else:
                valid_val = max(min_val, 0) + 1
            valid[f], invalid[f] = valid_val, min_val - 1
        elif rtype == "max":
            max_val = rule.get("max", 100)
            valid[f], invalid[f] = max_val, max_val + 1
        elif rtype == "min_length":
            min_len = rule.get("min_length", 2)
            valid[f], invalid[f] = "A" * max(min_len, 2), ""
        elif rtype == "lookup":
            valid[f] = _load_first_lookup_value(rule.get("lookup_file", ""), f)
            invalid[f] = "INVALID_VALUE"
        else:
            # unique, required_if, compare, max_length — delegate to name inference
            _v, _ = build_sample_records([f])
            valid[f], invalid[f] = _v[f], ""

    # Post-process: fix age_match cross-field consistency.
    # age and dob are generated independently; recalculate age from the
    # generated dob so the age_match rule passes on the valid record.
    from datetime import date as _date
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "age_match":
            continue
        age_field = rule.get("field")
        dob_field = rule.get("dob_field")
        if age_field and dob_field and dob_field in valid:
            try:
                dob = _date.fromisoformat(str(valid[dob_field]))
                today = _date.today()
                valid[age_field] = (today - dob).days // 365
            except (ValueError, TypeError):
                pass

    return valid, invalid


def build_sample_records(fields: list[str]) -> tuple[dict, dict]:
    """Build a valid and an invalid sample record for the given fields."""
    valid: dict = {}
    invalid: dict = {}
    for f in fields:
        lower = f.lower()
        if "email" in lower:
            valid[f], invalid[f] = "alice@example.com", "not-an-email"
        elif any(x in lower for x in ("phone", "mobile", "telephone")):
            valid[f], invalid[f] = "+44 7911 123456", "abc"
        elif lower in ("first_name", "firstname", "given_name"):
            valid[f], invalid[f] = "Alice", ""
        elif lower in ("last_name", "lastname", "surname", "family_name"):
            valid[f], invalid[f] = "Smith", ""
        elif lower in _FIELD_EXAMPLES:
            valid[f], invalid[f] = _FIELD_EXAMPLES[lower], ""
        elif "name" in lower:
            valid[f], invalid[f] = "Alice Smith", ""
        elif lower == "age":
            valid[f], invalid[f] = 30, -1
        elif any(x in lower for x in ("date", "dob", "created_at", "updated_at", "birth")):
            valid[f], invalid[f] = "1990-06-15", "not-a-date"
        elif any(x in lower for x in ("amount", "price", "cost", "revenue", "salary", "fee", "total")):
            valid[f], invalid[f] = 99.99, -1
        elif "score" in lower or "rating" in lower:
            valid[f], invalid[f] = 85, -1
        elif "url" in lower or "website" in lower:
            valid[f], invalid[f] = "https://example.com", "not-a-url"
        elif "country" in lower:
            valid[f], invalid[f] = "GB", ""
        elif lower in ("available", "active", "enabled", "is_deleted", "is_active", "is_enabled"):
            valid[f], invalid[f] = "true", "maybe"
        elif "address" in lower or "street" in lower:
            valid[f], invalid[f] = "123 High Street", ""
        elif "city" in lower:
            valid[f], invalid[f] = "London", ""
        elif "colour" in lower or "color" in lower:
            valid[f], invalid[f] = "Blue", ""
        elif lower.endswith("_type"):
            valid[f], invalid[f] = "STANDARD", ""
        elif lower.endswith("_status"):
            valid[f], invalid[f] = "ACTIVE", ""
        elif any(f.lower().endswith(s) for s in _ID_SUFFIXES) or lower == "id":
            valid[f], invalid[f] = "DEMO-001", ""
        else:
            valid[f], invalid[f] = "sample_value", ""
    return valid, invalid


# ── Wizard ────────────────────────────────────────────────────────────────────

@dataclass
class WizardResult:
    entity: str = ""
    fields: list = field(default_factory=list)
    contract_path: Optional[Path] = None
    elapsed: float = 0.0
    success: bool = False


class OnboardingWizard:
    """Interactive setup wizard — zero to first validation in under 90 seconds."""

    def __init__(self, contracts_dir: Optional[Path] = None):
        self.contracts_dir = contracts_dir or (Path(__file__).resolve().parent.parent / "contracts")
        # Note: Path(__file__).resolve().parent is opendqv/core/, so parent.parent is opendqv/,
        # which now houses the bundled contracts/ directory.
        self.start = time.time()
        self.result = WizardResult()
        self.console = Console() if HAS_RICH else None
        self._base_url = "http://localhost:8000"

    # ── Output helpers ────────────────────────────────────────────────────────

    def _p(self, msg: str = "", style: str = "") -> None:
        if HAS_RICH:
            self.console.print(msg, style=style)
        else:
            print(msg)

    def _show_logo(self) -> None:
        if HAS_RICH:
            import random
            _DQV_POOL = [
                "bright_magenta",  # pink
                "bright_yellow",   # yellow
                "bright_green",    # green
                "bright_red",      # red
                "bright_blue",     # blue
                "orange1",         # orange
                "purple",          # purple
            ]
            d_col, q_col, v_col = random.sample(_DQV_POOL, 3)
            self.console.print()
            for line in LOGO.splitlines():
                t = Text()
                t.append(line[0:35],  style="bold cyan")        # O P E N
                t.append(line[35:43], style=f"bold {d_col}")    # D
                t.append(line[43:52], style=f"bold {q_col}")    # Q
                t.append(line[52:],   style=f"bold {v_col}")    # V
                self.console.print(t)
            self.console.print(Text(f"\n  {TAGLINE}\n", style="dim white"))
            self.console.print(Rule(style="cyan"))
        else:
            print()
            print(LOGO)
            print(f"\n  {TAGLINE}\n")
            print("─" * 64)

    def _step(self, n: int, total: int, label: str) -> None:
        if HAS_RICH:
            self.console.print(
                f"\n[bold bright_cyan]  [{n}/{total}][/bold bright_cyan]  "
                f"[bold white]{label}[/bold white]"
            )
        else:
            print(f"\n  [{n}/{total}]  {label}")

    def _ok(self, msg: str) -> None:
        if HAS_RICH:
            self.console.print(f"       [bold green]✓[/bold green]  {msg}")
        else:
            print(f"       ✓  {msg}")

    def _info(self, msg: str) -> None:
        if HAS_RICH:
            self.console.print(f"       [dim cyan]→[/dim cyan]  [dim]{msg}[/dim]")
        else:
            print(f"       →  {msg}")

    def _warn(self, msg: str) -> None:
        if HAS_RICH:
            self.console.print(f"       [bold yellow]⚠[/bold yellow]  {msg}")
        else:
            print(f"       ⚠  {msg}")

    def _fail(self, msg: str) -> None:
        if HAS_RICH:
            self.console.print(f"       [bold red]✗[/bold red]  {msg}")
        else:
            print(f"       ✗  {msg}")

    def _ask(self, prompt: str, default: str = "") -> str:
        if HAS_QUESTIONARY:
            try:
                response = questionary.text(
                    prompt,
                    default=default,
                    style=WIZARD_STYLE,
                ).ask()
                if response is None:  # Ctrl+C
                    sys.exit(0)
                return response
            except (KeyboardInterrupt, EOFError):
                sys.exit(0)
        # fallback — plain input
        hint = f" [{default}]" if default else ""
        if HAS_RICH:
            self.console.print(f"       [dim]{prompt}{hint}:[/dim] ", end="")
        else:
            print(f"       {prompt}{hint}: ", end="")
        try:
            response = input().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        return response if response else default

    # ── Environment detection ─────────────────────────────────────────────────

    def _is_inside_docker(self) -> bool:
        """Return True when this process is running inside a Docker container."""
        return Path("/.dockerenv").exists()

    def _has_docker(self) -> bool:
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    # ── Service startup ───────────────────────────────────────────────────────

    def _start_docker(self) -> bool:
        if not Path(".env").exists():
            try:
                import shutil
                shutil.copy(".env.example", ".env")
                self._info("Created .env from .env.example")
            except FileNotFoundError:
                pass
        try:
            subprocess.Popen(
                ["docker", "compose", "-f", "docker-compose.yml",
                 "-f", "docker-compose.dev.yml", "up", "--build", "-d"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            return False

    def _start_uvicorn(self) -> bool:
        # 1. Check lock — if our API is still alive, reuse it.
        lock = _read_api_lock()
        if lock is not None:
            _pid, port = lock
            self._base_url = f"http://localhost:{port}"
            return True

        # 2. Probe localhost:8000 for a legacy running instance (no lock file).
        try:
            with urllib.request.urlopen("http://localhost:8000/health", timeout=2) as r:
                body = r.read(4096).decode("utf-8", errors="ignore")
                if "opendqv_node_state" in body:
                    self._base_url = "http://localhost:8000"
                    return True
                # Foreign process on 8000 — fall through to find a free port.
        except Exception:
            pass   # nothing there — fall through to spawn

        # 3. Find a free port and spawn.
        port = self._find_free_port(8000)
        if port != 8000:
            self._info(f"Port 8000 unavailable; starting API on port {port} instead.")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "opendqv.main:app",
                 "--port", str(port), "--log-level", "error"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _write_api_lock(proc.pid, port)
            self._base_url = f"http://localhost:{port}"
            return True
        except Exception:
            return False

    def _find_free_port(self, preferred: int = 8501) -> int:
        import socket
        for port in range(preferred, preferred + 20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return preferred

    def _start_streamlit(self, api_port: int = 8000) -> int | None:
        # 1. Check lock — if our Streamlit is still alive, reuse it.
        lock = _read_workbench_lock()
        if lock is not None:
            _pid, port = lock
            return port

        # 2. Find a free port and spawn a fresh Streamlit process.
        preferred = 8501
        port = self._find_free_port(preferred)
        if port != preferred:
            self._info(
                f"Port {preferred} unavailable; starting workbench on port {port} instead."
            )
        try:
            env = os.environ.copy()
            if api_port != 8000:
                env["API_URL"] = f"http://localhost:{api_port}"
            proc = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", "ui/app.py",
                 "--server.port", str(port), "--server.headless", "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            _write_workbench_lock(proc.pid, port)
            return port
        except Exception:
            return None

    # ── Health check ──────────────────────────────────────────────────────────

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self._base_url}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def _wait_for_health(self, timeout: int = 60) -> bool:
        deadline = time.time() + timeout
        if HAS_RICH:
            with self.console.status(
                "[green]  Starting OpenDQV...[/green]", spinner="dots"
            ):
                while time.time() < deadline:
                    if self._health_ok():
                        return True
                    time.sleep(1)
        else:
            print("       Starting", end="", flush=True)
            while time.time() < deadline:
                if self._health_ok():
                    print(" ready.")
                    return True
                print(".", end="", flush=True)
                time.sleep(1)
            print()
        return False

    # ── API calls ─────────────────────────────────────────────────────────────

    def _demo_governance(self, entity: str) -> None:
        """Demonstrate the draft → review → active governance lifecycle.

        Shows the maker-checker workflow in the first 90 seconds.
        Silently skips if the API is in token mode (auth required).
        """
        try:
            # Get the current version
            req = urllib.request.Request(
                f"{self._base_url}/api/v1/contracts/{entity}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                detail = json.loads(r.read())
            version = detail.get("version", "1.0")
            status = detail.get("status", "draft")

            if status != "draft":
                return  # already active or archived — skip demo

            # Submit for review
            submit_payload = json.dumps({"proposed_by": "wizard-demo"}).encode()
            req = urllib.request.Request(
                f"{self._base_url}/api/v1/contracts/{entity}/{version}/submit-review",
                data=submit_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                if e.code == 401 or e.code == 403:
                    return  # auth required — skip governance demo silently
                raise

            if HAS_RICH:
                self.console.print("  [cyan]→[/cyan]  [dim]DRAFT[/dim] → [yellow]REVIEW[/yellow]   proposed by wizard-demo")
            else:
                print("  →  DRAFT → REVIEW   proposed by wizard-demo")

            # Approve
            approve_payload = json.dumps({"approved_by": "wizard-demo"}).encode()
            req = urllib.request.Request(
                f"{self._base_url}/api/v1/contracts/{entity}/{version}/approve",
                data=approve_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                if e.code == 401 or e.code == 403:
                    return
                raise

            if HAS_RICH:
                self.console.print("  [cyan]→[/cyan]  [yellow]REVIEW[/yellow] → [bold green]ACTIVE[/bold green]   approved by wizard-demo")
                self.console.print()
                self.console.print(
                    "  [dim]Every change is hash-chained and auditable. "
                    "In production, proposal and approval require different roles (editor / approver).[/dim]"
                )
            else:
                print("  →  REVIEW → ACTIVE   approved by wizard-demo")
                print()
                print("  Every change is hash-chained and auditable.")
                print("  In production, proposal and approval require different roles (editor / approver).")

        except Exception:
            pass  # governance demo is a nice-to-have — never block the wizard

    def _reload(self) -> None:
        try:
            req = urllib.request.Request(
                f"{self._base_url}/api/v1/contracts/reload", data=b"", method="POST"
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def _validate(self, entity: str, record: dict) -> dict:
        payload = json.dumps({"contract": entity, "record": record}).encode()
        req = urllib.request.Request(
            f"{self._base_url}/api/v1/validate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    # ── Result display ────────────────────────────────────────────────────────

    def _show_results(
        self,
        valid_rec: dict, valid_res: dict,
        invalid_rec: dict, invalid_res: dict,
    ) -> None:
        if HAS_RICH:
            self.console.print()
            self.console.print(Rule("  Your First Validation  ", style="green"))
            self.console.print()
            v_icon = "[bold green]✓ VALID  [/bold green]" if valid_res.get("valid") else "[bold red]✗ INVALID[/bold red]"
            self.console.print(f"  {v_icon}  {json.dumps(valid_rec)}")
            self.console.print()
            i_icon = "[bold green]✓ VALID  [/bold green]" if invalid_res.get("valid") else "[bold red]✗ INVALID[/bold red]"
            self.console.print(f"  {i_icon}  {json.dumps(invalid_rec)}")
            for err in invalid_res.get("errors", []):
                self.console.print(f"             [red]→ {err['field']}: {err['message']}[/red]")
            self.console.print()
        else:
            print()
            print("  ─── Your First Validation " + "─" * 36)
            icon = "✓ VALID  " if valid_res.get("valid") else "✗ INVALID"
            print(f"  {icon}  {json.dumps(valid_rec)}")
            print()
            icon = "✓ VALID  " if invalid_res.get("valid") else "✗ INVALID"
            print(f"  {icon}  {json.dumps(invalid_rec)}")
            for err in invalid_res.get("errors", []):
                print(f"             → {err['field']}: {err['message']}")
            print()

    def _show_next_steps(self, entity: str, elapsed: float, use_docker: bool = False, streamlit_port: int = 8501) -> None:
        mins, secs = int(elapsed // 60), int(elapsed % 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        import shutil as _shutil
        import os as _os
        # Use relative path — works from project root on host and /app inside container
        _yaml_rel = f"contracts/{entity}.yaml"
        _editor_cmd: str | None = None
        if _shutil.which("code"):
            _editor_cmd = f"code {_yaml_rel}"
        elif _os.environ.get("EDITOR"):
            _editor_cmd = f'{_os.environ["EDITOR"]} {_yaml_rel}'

        _status_msg = (
            "OpenDQV is running. Your contract is live."
            if use_docker else
            "OpenDQV API is running (Python mode). Your contract is live."
        )

        if HAS_RICH:
            self.console.print(Rule(style="green"))
            self.console.print()
            self.console.print(f"  [bold green]{_status_msg}[/bold green]")
            self.console.print()
            self.console.print("  [bold white]Next steps:[/bold white]")
            self.console.print(f"  [cyan]→[/cyan]  Edit your contract:  [cyan]{_yaml_rel}[/cyan]")
            if _editor_cmd:
                self.console.print(f"  [cyan]→[/cyan]  Open in editor:      [dim]{_editor_cmd}[/dim]")
            self.console.print(f"  [cyan]→[/cyan]  Reload after edits:  [dim]curl -X POST {self._base_url}/api/v1/contracts/reload[/dim]")
            self.console.print(f"  [cyan]→[/cyan]  Visual workbench:    [cyan]http://localhost:{streamlit_port}[/cyan]")
            self.console.print(f"  [cyan]→[/cyan]  API docs:            [cyan]{self._base_url}/docs[/cyan]")
            self.console.print("  [cyan]→[/cyan]  All rule types:      [dim]docs/rules/README.md[/dim]")
            self.console.print()
            self.console.print(f"  [dim]Time from start to first validation: {elapsed_str}[/dim]")
            self.console.print()
            self.console.print(Rule(style="dim"))
        else:
            print("─" * 64)
            print()
            print(f"  {_status_msg}")
            print()
            print("  Next steps:")
            print(f"  →  Edit your contract: {_yaml_rel}")
            if _editor_cmd:
                print(f"  →  Open in editor:     {_editor_cmd}")
            print(f"  →  Reload after edits: curl -X POST {self._base_url}/api/v1/contracts/reload")
            print(f"  →  Visual workbench:   http://localhost:{streamlit_port}")
            print(f"  →  API docs:           {self._base_url}/docs")
            print("  →  All rule types:     docs/rules/README.md")
            print()
            print(f"  Time from start to first validation: {elapsed_str}")
            print()

        # Write session file so the workbench auto-jumps to this contract on next open
        try:
            _SESSION_FILE.write_text(
                json.dumps({"contract": entity}), encoding="utf-8"
            )
        except Exception:
            pass  # non-critical

    # ── Template loader ───────────────────────────────────────────────────────

    def _list_templates(self) -> list[dict]:
        """Return available starter templates from the contracts directory."""
        import yaml as _yaml
        templates = []
        if not self.contracts_dir.exists():
            return templates
        for path in sorted(self.contracts_dir.glob("*.yaml")):
            name = path.stem
            if name in _EXCLUDED_TEMPLATES:
                continue
            try:
                data = _yaml.safe_load(path.read_text())
                if not data:
                    continue
                contract = data.get("contract") or data
                rules = contract.get("rules") or []
                fields = list(dict.fromkeys(
                    r["field"] for r in rules if isinstance(r, dict) and "field" in r
                ))
                label = _TEMPLATE_LABELS.get(name, name)
                contract_name = contract.get("name", name)
                templates.append({
                    "name": name,
                    "contract_name": contract_name,
                    "path": path,
                    "rule_count": len(rules),
                    "fields": fields,
                    "rules": rules,
                    "label": label,
                })
            except Exception:
                continue
        templates.sort(key=lambda t: t["label"].lower())
        return templates

    # ── Main flow ─────────────────────────────────────────────────────────────

    def run(self) -> WizardResult:
        self._show_logo()
        self._p()
        self._p("  Welcome. Let's get OpenDQV running in under 90 seconds.", style="dim")
        self._p('  "Trust is easier to build than to repair."', style="dim italic")
        self._p()

        if not HAS_RICH:
            self._warn("Install 'rich' for a better experience:  pip install rich")

        # ── [1/4] Environment ─────────────────────────────────────────────────
        self._step(1, 4, "Checking your environment")
        ver = sys.version.split()[0]
        self._ok(f"Python {ver}")
        use_docker = self._has_docker()
        if self._is_inside_docker():
            self._ok("Running inside Docker container")
        elif use_docker:
            self._ok("Docker available — using Docker (recommended)")
        else:
            print("Docker is not running. Start Docker Desktop and try again. (Falling back to local Python server.)")
            self._warn("Docker not found — starting API with built-in web server (uvicorn)")
        if Path(".env").exists():
            self._info(".env found — keeping existing configuration")

        # ── [2/4] What are you validating? ───────────────────────────────────
        self._step(2, 4, "What are you validating?")
        templates = self._list_templates()
        use_template = None

        if templates:
            self._info("Choose an industry starter or build your own:")
            self._p()
            if HAS_QUESTIONARY:
                choices = [
                    questionary.Choice(
                        title=f"{t['label']}  ({t['rule_count']} rules)",
                        value=t,
                    )
                    for t in templates
                ]
                choices.append(questionary.Choice(title="Build my own...", value=_BUILD_OWN))
                try:
                    selected = questionary.select(
                        "What are you validating?",
                        choices=choices,
                        use_shortcuts=False,
                        use_jk_keys=False,
                        use_search_filter=True,
                        style=WIZARD_STYLE,
                    ).ask()
                    if selected is None:  # Ctrl+C
                        sys.exit(0)
                    elif selected != _BUILD_OWN:
                        use_template = selected
                except (KeyboardInterrupt, EOFError):
                    sys.exit(0)
            else:
                for i, t in enumerate(templates, 1):
                    self._info(f"  {i:<3} {t['label']}  ({t['rule_count']} rules)")
                self._info(f"  {len(templates) + 1:<3} Build my own...")
                self._p()
                choice = self._ask(f"Choose [1-{len(templates) + 1}]", str(len(templates) + 1))
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(templates):
                        use_template = templates[idx]
                except ValueError:
                    pass

        if use_template:
            entity = use_template.get("contract_name", use_template["name"])
            fields = use_template["fields"]
            contract_path = use_template["path"]
            self.result.entity = entity
            self.result.fields = fields
            self.result.contract_path = contract_path
            self._ok(f"Template: {use_template['label']}")
        else:
            self._info("e.g.  customer  order  student  patient  transaction")
            raw_entity = self._ask("What type of data are you validating", "customer")
            import re as _re
            entity = _re.sub(r'[^A-Za-z0-9_-]', '', raw_entity.lower().strip().replace(" ", "_"))[:100] or "customer"
            self.result.entity = entity
            self._ok(f"Entity: {entity}")

        # ── [3/4] Fields (skipped when using a template) ─────────────────────
        if use_template:
            self._step(3, 4, "Contract ready")
            self._ok(f"{len(use_template['rules'])} rules loaded from template")
            self._info(f"Edit contracts/{entity}.yaml to customise")
        else:
            self._step(3, 4, "Which fields do you want to validate?")
            self._info("Comma-separated — e.g.  email, name, phone, age")
            raw_fields = self._ask("Fields", "email, name, phone, age")
            fields = [f.strip() for f in raw_fields.split(",") if f.strip()] or ["email", "name", "phone", "age"]
            self.result.fields = fields

            for f in fields:
                rule = infer_rule(f)
                self._info(f"{f:<22} → {rule['type']}")

            yaml_content = generate_contract_yaml(entity, fields)
            contract_path = self.contracts_dir / f"{entity}.yaml"
            if contract_path.exists():
                self._warn(f"contracts/{entity}.yaml already exists")
                if HAS_QUESTIONARY:
                    try:
                        overwrite = questionary.confirm(
                            f"contracts/{entity}.yaml already exists. Overwrite?",
                            default=False,
                            style=WIZARD_STYLE,
                        ).ask()
                        if overwrite is None:  # Ctrl+C
                            sys.exit(0)
                    except (KeyboardInterrupt, EOFError):
                        sys.exit(0)
                else:
                    raw = self._ask("Overwrite? [y/N]", "N")
                    overwrite = raw.lower() == "y"
                if not overwrite:
                    entity = f"{entity}_demo"
                    self.result.entity = entity
                    contract_path = self.contracts_dir / f"{entity}.yaml"
                    self._info(f"Writing to contracts/{entity}.yaml instead")
                    yaml_content = generate_contract_yaml(entity, fields)

            self.contracts_dir.mkdir(exist_ok=True)
            contract_path.write_text(yaml_content, encoding="utf-8")
            self.result.contract_path = contract_path
            self._ok(f"Written: contracts/{entity}.yaml ({len(fields)} rules)")

        # ── [4/4] Start and validate ───────────────────────────────────────────
        self._step(4, 4, "Starting OpenDQV and running your first validation")

        streamlit_port = 8501
        if self._is_inside_docker():
            # Running inside a container — the service is already up on localhost:8000.
            # Skip docker compose up (no socket) and connect directly.
            self._base_url = "http://localhost:8000"
            self._info("Running inside Docker — connecting to the live API")
            started = True
        elif use_docker:
            started = self._start_docker()
        else:
            started = self._start_uvicorn()
            if started:
                api_port = int(self._base_url.rsplit(":", 1)[-1])
                streamlit_port = self._start_streamlit(api_port=api_port) or 8501
        if not started:
            self._fail("Could not start the service.")
            self._info("Try manually: uvicorn opendqv.main:app --reload")
            return self.result

        health_timeout = 180 if use_docker else 60
        healthy = self._wait_for_health(timeout=health_timeout)
        if not healthy:
            secs = health_timeout
            self._fail(f"Service did not become healthy within {secs} seconds.")
            self._info(f"Check: curl {self._base_url}/health")
            self._info("Docker may still be building — wait a moment and try again")
            return self.result

        self._ok("Service is healthy")
        self._reload()

        if use_template and use_template.get("rules"):
            valid_rec, invalid_rec = build_sample_records_from_rules(use_template["rules"])
        else:
            valid_rec, invalid_rec = build_sample_records(fields)
        try:
            valid_res = self._validate(entity, valid_rec)
            invalid_res = self._validate(entity, invalid_rec)
        except Exception as exc:
            self._fail(f"Validation failed: {exc}")
            return self.result

        self._show_results(valid_rec, valid_res, invalid_rec, invalid_res)

        # ── Governance lifecycle demo ──────────────────────────────────────────
        if HAS_RICH:
            self.console.print()
            self.console.print("  [bold white]Governance lifecycle (draft → review → active):[/bold white]")
        else:
            print()
            print("  Governance lifecycle (draft → review → active):")
        self._demo_governance(entity)

        elapsed = time.time() - self.start
        self.result.elapsed = elapsed
        self.result.success = True
        self._show_next_steps(entity, elapsed, use_docker=use_docker, streamlit_port=streamlit_port)
        return self.result
