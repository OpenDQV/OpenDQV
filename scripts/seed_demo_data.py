#!/usr/bin/env python3
"""
OpenDQV demo environment seeder.

Runs once after the API is healthy. Idempotent — safe to run multiple times.
If total_validations > 500 it exits immediately without re-seeding.

Seeds ~690 validation events across 6 contracts and creates a full
draft → review → active lifecycle demo using a demo_order contract.

Usage:
    python scripts/seed_demo_data.py              # local dev
    OPENDQV_URL=http://api:8000 python ...        # inside demo Docker Compose
"""

import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")

# ── Curated UK names ─────────────────────────────────────────────────
FIRST_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Edward", "Fiona", "George", "Hannah",
    "Ivan", "Julia", "Kevin", "Laura", "Mohammed", "Natasha", "Oliver", "Priya",
    "Quinn", "Rachel", "Samuel", "Tanya", "Usman", "Vivien", "William", "Xia",
    "Yasmin", "Zara", "Aiden", "Beth", "Callum", "Deepa",
]
LAST_NAMES = [
    "Smith", "Jones", "Williams", "Taylor", "Brown", "Davies", "Evans", "Wilson",
    "Thomas", "Roberts", "Johnson", "Lewis", "Walker", "Robinson", "Wood", "Thompson",
    "White", "Watson", "Jackson", "Wright", "Green", "Harris", "Martin", "Clarke",
    "Cooper", "Ward", "Morris", "Moore", "King", "Scott",
]

random.seed(42)  # deterministic — same seed every run


# ── HTTP helpers (no SDK dependency — works in any container) ─────────

def _call(method: str, path: str, body: dict = None, token: str = "") -> dict:
    """Minimal HTTP client. Returns parsed JSON or raises on HTTP error."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode()
        print(f"  HTTP {exc.code} on {method} {path}: {body_text[:200]}")
        return {}


def get(path: str) -> dict:
    return _call("GET", path)


def post(path: str, body: dict = None) -> dict:
    return _call("POST", path, body)


# ── Idempotency check ─────────────────────────────────────────────────

def already_seeded() -> bool:
    try:
        stats = get("/api/v1/stats")
        count = stats.get("total_validations", 0)
        if count > 500:
            print(f"Demo data already seeded ({count} validations). Skipping.")
            return True
    except Exception:
        pass
    return False


# ── Data generators ───────────────────────────────────────────────────

def uk_phone() -> str:
    return f"+4477{random.randint(10000000, 99999999)}"


def bad_phone() -> str:
    return random.choice(["07911", "0123", "+1234", "notaphone"])


def random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def random_email(valid: bool = True) -> str:
    first = random.choice(FIRST_NAMES).lower()
    last = random.choice(LAST_NAMES).lower()
    if valid:
        domain = random.choice(["example.com", "demo.co.uk", "test.org", "sample.net"])
        return f"{first}.{last}@{domain}"
    return random.choice([
        f"{first}.{last}",          # missing @
        f"{first}.{last}@",         # missing domain
        f"{first} {last}@example.com",  # space in local part
        f"{first}@example",         # missing TLD
    ])


def random_date(days_back: int = 365) -> str:
    d = date.today() - timedelta(days=random.randint(0, days_back))
    return d.isoformat()


def future_date(days_ahead: int = 30) -> str:
    d = date.today() + timedelta(days=random.randint(1, days_ahead))
    return d.isoformat()


# ── Contract seeders ──────────────────────────────────────────────────

def seed_customer(n: int = 200):
    print(f"  Seeding customer ({n} records, ~85% pass rate) …")
    sent = 0
    for i in range(n):
        valid = i % 7 != 0  # ~85% valid
        record = {
            "email": random_email(valid=valid or (i % 7 != 1)),
            "name": random_name() if valid or i % 7 != 2 else "",
            "phone": uk_phone() if valid else bad_phone(),
            "age": random.randint(18, 85) if valid else random.choice([200, -1, 999]),
            "score": random.randint(0, 100) if valid else random.choice([9999, -50]),
            "date": random_date() if valid else "15-06-2024",
            "username": f"user_{random.randint(1000, 9999)}",
            "password": "Str0ngP@ss!" if valid else "weak",
            "loyalty_tier": random.choice(["bronze", "silver", "gold", "standard"]) if valid
                           else "diamond",
        }
        post("/api/v1/validate", {"contract": "customer", "record": record})
        sent += 1
    print(f"    ✓ {sent} customer records validated")


def seed_sf_contact(n: int = 100):
    print(f"  Seeding sf_contact ({n} records, ~90% pass rate) …")
    titles = ["Head of Data", "VP Engineering", "CTO", "Data Engineer", "Analyst", "Director"]
    countries = ["GB", "DE", "FR", "US", "NL", "SE", "IE"]
    sent = 0
    for i in range(n):
        valid = i % 10 != 0
        record = {
            "FirstName": random.choice(FIRST_NAMES),
            "LastName": random.choice(LAST_NAMES),
            "Email": random_email(valid=valid),
            "Phone": uk_phone() if valid else bad_phone(),
            "Title": random.choice(titles),
            "AccountName": f"Acme {random.choice(['Corp', 'Ltd', 'Inc', 'GmbH'])}",
            "Birthdate": random_date(365 * 40) if valid else "not-a-date",
            "MailingCountry": random.choice(countries) if valid else "UK",  # UK not valid ISO 3166
        }
        post("/api/v1/validate", {"contract": "sf_contact", "record": record})
        sent += 1
    print(f"    ✓ {sent} sf_contact records validated")


def seed_proof_of_play(n: int = 150):
    print(f"  Seeding proof_of_play ({n} records, ~80% pass rate) …")
    panel_types = ["digital_6sheet", "digital_48sheet", "digital_96sheet", "classic_48sheet"]
    sent = 0
    for i in range(n):
        valid = i % 5 != 0
        panel_num = random.randint(1, 9999)
        start_dt = f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}T{random.randint(6,22):02d}:00:00Z"
        end_dt = start_dt[:13].replace("T", "T") + f"{random.randint(0, 59):02d}:00Z"  # same day
        record = {
            "panel_id": f"LGM-UK-{panel_num:05d}",
            "market": "GB" if valid else "United Kingdom",
            "panel_type": random.choice(panel_types),
            "impression_start": start_dt,
            "impression_end": end_dt if valid else "not-a-date",
            "revenue_gbp": round(random.uniform(10, 500), 2),
            "advertiser_id": f"ADV-{random.randint(10000, 99999)}",
        }
        post("/api/v1/validate", {"contract": "proof_of_play", "record": record})
        sent += 1
    print(f"    ✓ {sent} proof_of_play records validated")


def seed_banking(n: int = 100):
    print(f"  Seeding banking_transaction ({n} records, ~88% pass rate) …")
    currencies = ["GBP", "EUR", "USD", "JPY", "CHF"]
    tx_types = ["credit", "debit", "transfer", "payment", "refund"]
    channels = ["online", "mobile", "branch", "atm", "pos"]
    sent = 0
    for i in range(n):
        valid = i % 8 != 0
        tx_date = random_date(180)
        record = {
            "transaction_id": f"TXN-{random.randint(100000, 999999)}",
            "account_number": f"{random.randint(10000000, 99999999)}",
            "transaction_date": tx_date if valid else "01/06/2024",
            "amount": round(random.uniform(10, 50000), 2),
            "currency": random.choice(currencies) if valid else "POUNDS",
            "transaction_type": random.choice(tx_types),
            "channel": random.choice(channels),
        }
        post("/api/v1/validate", {"contract": "banking_transaction", "record": record})
        sent += 1
    print(f"    ✓ {sent} banking_transaction records validated")


def seed_logistics(n: int = 80):
    print(f"  Seeding logistics_shipment ({n} records, ~92% pass rate) …")
    incoterms = ["EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"]
    modes = ["road", "sea", "air", "rail", "multimodal"]
    carriers = ["DHL", "FedEx", "UPS", "DPD", "Evri", "Royal Mail", "DB Schenker", "Maersk"]
    countries = ["GB", "DE", "FR", "US", "NL", "SE", "CN", "JP", "SG"]
    statuses = ["pending", "in_transit", "delivered", "customs_hold", "returned"]
    sent = 0
    for i in range(n):
        valid = i % 13 != 0
        dispatch = random_date(90)
        delivery = future_date(14)
        record = {
            "shipment_id": f"SHP-{random.randint(100000, 999999)}",
            "origin_country": random.choice(countries),
            "destination_country": random.choice(countries),
            "dispatch_date": dispatch,
            "estimated_delivery_date": delivery,
            "weight_kg": round(random.uniform(0.1, 500), 2),
            "incoterms": random.choice(incoterms) if valid else "DAT",  # old Incoterms 2010
            "hs_code": f"{random.randint(100000, 999999)}",
            "shipment_mode": random.choice(modes),
            "carrier": random.choice(carriers),
            "status": random.choice(statuses),
        }
        post("/api/v1/validate", {"contract": "logistics_shipment", "record": record})
        sent += 1
    print(f"    ✓ {sent} logistics_shipment records validated")


def seed_healthcare(n: int = 60):
    print(f"  Seeding nhs_dsp_patient ({n} records, ~85% pass rate) …")
    sexes = ["male", "female", "indeterminate", "not_known"]
    admission_types = ["elective", "emergency", "maternity", "other"]
    wards = ["cardiology", "oncology", "orthopaedics", "general_medicine", "paediatrics"]
    icd10_valid = ["J45.0", "A09", "Z00.0", "I10", "E11.9", "M54.5", "K21.0", "F32.1"]
    icd10_invalid = ["INVALID", "X99.99.99", "ZZZ", "123ABC"]
    blood_types = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]
    discharge_reasons = ["discharged_home", "transferred", "died", "self_discharged", "still_admitted"]
    sent = 0
    for i in range(n):
        valid = i % 7 != 0
        dob = random_date(365 * 70)
        admission = random_date(90)
        discharge = future_date(7) if random.random() > 0.3 else random_date(30)
        # Build a valid 10-digit NHS number (format only — not Modulus 11 verified)
        nhs_number = f"{random.randint(100, 999)} {random.randint(100, 999)} {random.randint(1000, 9999)}"
        record = {
            "patient_id": f"PAT-{random.randint(10000, 99999)}",
            "first_name": random.choice(FIRST_NAMES),
            "last_name": random.choice(LAST_NAMES),
            "date_of_birth": dob,
            "nhs_number": nhs_number if valid else f"{random.randint(10000000, 99999999)}",
            "sex": random.choice(sexes),
            "ethnicity": "A" if valid else "white_british",  # A = White British in 16+1 coding
            "admission_date": admission,
            "admission_type": random.choice(admission_types),
            "ward": random.choice(wards),
            "diagnosis_code": random.choice(icd10_valid) if valid else random.choice(icd10_invalid),
            "blood_type": random.choice(blood_types),
            "discharge_date": discharge,
            "discharge_reason": random.choice(discharge_reasons),
        }
        post("/api/v1/validate", {"contract": "nhs_dsp_patient", "record": record})
        sent += 1
    print(f"    ✓ {sent} nhs_dsp_patient records validated")


# ── Lifecycle demo ────────────────────────────────────────────────────

DEMO_ORDER_CONTRACT = {
    "name": "demo_order",
    "rules": [
        {"field": "order_id", "type": "not_empty"},
        {"field": "customer_email", "type": "regex", "pattern": "^[\\w.+-]+@[\\w-]+\\.[\\w.]+$",
         "error_message": "customer_email must be a valid email address"},
        {"field": "amount_gbp", "type": "min", "min": 0.01,
         "error_message": "amount_gbp must be greater than 0"},
        {"field": "status", "type": "allowed_values",
         "values": ["pending", "confirmed", "shipped", "delivered", "cancelled"],
         "error_message": "status must be one of: pending, confirmed, shipped, delivered, cancelled"},
        {"field": "order_date", "type": "date_format",
         "error_message": "order_date must be in YYYY-MM-DD format"},
    ],
}


def create_demo_lifecycle():
    print("  Creating demo_order contract (lifecycle demo: draft → review → active) …")

    # Create draft contract
    result = post("/api/v1/contracts", DEMO_ORDER_CONTRACT)
    if not result:
        print("    ! Could not create demo_order contract (may already exist — continuing)")

    # Submit for review
    post("/api/v1/contracts/demo_order/submit-review")
    print("    → submitted for review")

    # Approve (AUTH_MODE=open means all callers have admin role)
    post("/api/v1/contracts/demo_order/approve")
    print("    → approved and now ACTIVE")

    # Seed 50 validations against the new contract
    statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
    for i in range(50):
        valid = i % 6 != 0
        record = {
            "order_id": f"ORD-{random.randint(10000, 99999)}",
            "customer_email": random_email(valid=valid),
            "amount_gbp": round(random.uniform(1, 5000), 2),
            "status": random.choice(statuses) if valid else "processing",  # not in allowed_values
            "order_date": random_date(90) if valid else "2024/06/15",
        }
        post("/api/v1/validate", {"contract": "demo_order", "record": record})

    print("    ✓ 50 demo_order records validated")


# ── Banner ────────────────────────────────────────────────────────────

def print_banner(total: int):
    width = 60
    print()
    print("=" * width)
    print("  OpenDQV demo environment ready")
    print("=" * width)
    print(f"  ~{total} validation events seeded across 7 contracts")
    print()
    print("  API:      http://localhost:8080")
    print("  Docs:     http://localhost:8080/docs")
    print("  GraphQL:  http://localhost:8080/graphql")
    print("  UI:       http://localhost:8502")
    print()
    print("  Suggested first steps:")
    print("  1. Open the UI → Monitoring tab (live charts)")
    print("  2. Open Postman → folder 3 → 'Invalid customer record'")
    print("  3. Open the UI → Contracts tab → demo_order (active lifecycle)")
    print("=" * width)
    print()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    print(f"OpenDQV demo seeder — target: {BASE_URL}")

    # Wait for API to be fully ready (should already be healthy via Docker depends_on)
    for attempt in range(10):
        try:
            h = get("/health")
            if h.get("status") == "healthy":
                break
        except Exception:
            pass
        print(f"  Waiting for API ({attempt + 1}/10) …")
        time.sleep(3)
    else:
        print("ERROR: API not reachable after 30 seconds. Exiting.")
        sys.exit(1)

    if already_seeded():
        return

    print("Seeding demo data …")
    seed_customer(200)
    seed_sf_contact(100)
    seed_proof_of_play(150)
    seed_banking(100)
    seed_logistics(80)
    seed_healthcare(60)
    create_demo_lifecycle()

    total = 200 + 100 + 150 + 100 + 80 + 60 + 50
    print_banner(total)


if __name__ == "__main__":
    main()
