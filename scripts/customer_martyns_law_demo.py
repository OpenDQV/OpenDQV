#!/usr/bin/env python3
"""
Customer Martyn's Law demo — validates venue records against martyns_law_venue
(Terrorism (Protection of Premises) Act 2025) and prints a pass/fail summary.

Usage:
    python scripts/customer_martyns_law_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_martyns_law_demo.py

Customer-specific records live in scripts/martyns_law_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Venue Name", "VEN-001", "standard", null, {}],
        ...
      ]
    }

fail_mode options: null, "below_capacity", "enhanced_no_srp", "enhanced_no_sia",
                   "enhanced_no_plan"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_today = date.today().isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Concert Hall",            "VEN-001", "standard",  None,                 {"capacity": 2500}),
    ("Conference Centre",       "VEN-002", "enhanced",  None,                 {"capacity": 800}),
    # Failures — four instructive Martyn's Law compliance gaps
    ("Community Sports Hall",   "VEN-003", "standard",  "below_capacity",     {"capacity": 150}),
    ("Hotel Ballroom",          "VEN-004", "enhanced",  "enhanced_no_srp",    {"capacity": 450}),
    ("Open-Air Market",         "VEN-005", "enhanced",  "enhanced_no_sia",    {"capacity": 1200}),
    ("Theatre Auditorium",      "VEN-006", "enhanced",  "enhanced_no_plan",   {"capacity": 600}),
]

_FAIL_LABELS = {
    "below_capacity":   "capacity: must be ≥ 200 — Martyn's Law registration threshold",
    "enhanced_no_srp":  "senior_responsible_person: required when duty_tier=enhanced",
    "enhanced_no_sia":  "sia_registration_number: required when duty_tier=enhanced",
    "enhanced_no_plan": "terrorism_protection_plan_documented: required when duty_tier=enhanced",
}


def build_martyns_law_record(venue_name: str, venue_id: str, tier: str,
                              fail_mode, overrides: dict) -> dict:
    """Build a valid martyns_law_venue record then apply the requested failure mode."""
    capacity = overrides.pop("capacity", 500)

    record: dict = {
        "venue_id":                             venue_id,
        "venue_name":                           venue_name,
        "venue_address":                        "1 Demo Street, London, EC1A 1BB",
        "capacity":                             capacity,
        "duty_tier":                            tier,
        "venue_type":                           "other",
        "evacuation_procedure_documented":      "true",
        "invacuation_procedure_documented":     "true",
        "lockdown_procedure_documented":        "true",
        "staff_training_completed":             "true",
        "staff_training_date":                  _today,
        "compliance_reviewed_by":               "Venue Safety Manager",
        "compliance_review_date":               _today,
    }

    # Enhanced tier requires additional fields
    if tier == "enhanced" and fail_mode not in (
        "enhanced_no_srp", "enhanced_no_sia", "enhanced_no_plan"
    ):
        record["senior_responsible_person"]             = "J Demo"
        record["senior_responsible_person_role"]        = "General Manager"
        record["sia_registration_number"]               = "SIA-DEMO-001"
        record["terrorism_protection_plan_documented"]  = "true"
        record["terrorism_protection_plan_review_date"] = _today

    record.update(overrides)

    if fail_mode == "below_capacity":
        record["capacity"] = 150  # below minimum of 200

    elif fail_mode == "enhanced_no_srp":
        record["duty_tier"]                             = "enhanced"
        record["senior_responsible_person_role"]        = "General Manager"
        record["sia_registration_number"]               = "SIA-DEMO-001"
        record["terrorism_protection_plan_documented"]  = "true"
        record["terrorism_protection_plan_review_date"] = _today
        # Omit senior_responsible_person — required for enhanced

    elif fail_mode == "enhanced_no_sia":
        record["duty_tier"]                             = "enhanced"
        record["senior_responsible_person"]             = "J Demo"
        record["senior_responsible_person_role"]        = "General Manager"
        record["terrorism_protection_plan_documented"]  = "true"
        record["terrorism_protection_plan_review_date"] = _today
        # Omit sia_registration_number — required for enhanced

    elif fail_mode == "enhanced_no_plan":
        record["duty_tier"]                             = "enhanced"
        record["senior_responsible_person"]             = "J Demo"
        record["senior_responsible_person_role"]        = "General Manager"
        record["sia_registration_number"]               = "SIA-DEMO-001"
        # Omit terrorism_protection_plan_documented — required for enhanced

    return record


def run(customer: str) -> None:
    menu = _load_menu("martyns_law", customer, _DEFAULT_MENU)
    run_demo("Martyn's Law Venue", "martyns_law_venue", customer, menu,
             build_martyns_law_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV Martyn's Law venue customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from martyns_law_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
