#!/usr/bin/env python3
"""
Customer Building Safety demo — validates golden thread records against
building_safety_golden_thread (Building Safety Act 2022) and prints a pass/fail summary.

Usage:
    python scripts/customer_building_safety_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_building_safety_demo.py

Customer-specific records live in scripts/building_safety_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Building Name", "BLD-001", 22.5, null, {}],
        ...
      ]
    }

fail_mode options: null, "safety_case_no_date", "no_golden_thread_date",
                   "no_accountable_person", "no_bsr_number"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_today = date.today().isoformat()

_DEFAULT_MENU = [
    # Passes
    ("High-Rise Residential Block",  "BLD-001", 22.5,  None,                    {}),
    ("Mixed-Use Development",        "BLD-002", 19.0,  None,                    {}),
    # Failures — four instructive Building Safety Act compliance gaps
    ("Residential Tower",            "BLD-003", 34.0,  "safety_case_no_date",   {}),
    ("Social Housing Block",         "BLD-004", 21.0,  "no_golden_thread_date", {}),
    ("Student Accommodation",        "BLD-005", 25.5,  "no_accountable_person", {}),
    ("Care Home Annex",              "BLD-006", 18.5,  "no_bsr_number",         {}),
]

_FAIL_LABELS = {
    "safety_case_no_date":    "safety_case_report_date: required when safety_case_documented=true — Building Safety Act 2022",
    "no_golden_thread_date":  "golden_thread_last_updated: required — Building Safety Act 2022 s.88",
    "no_accountable_person":  "accountable_person_name: required — Building Safety Act 2022 s.72",
    "no_bsr_number":          "bsr_registration_number: required — Building Safety Regulator registration",
}


def build_building_safety_record(building_name: str, building_id: str, height: float,
                                  fail_mode, overrides: dict) -> dict:
    """Build a valid building_safety_golden_thread record then apply the requested failure mode."""
    record: dict = {
        "building_id":                          building_id,
        "building_name":                        building_name,
        "building_address":                     "1 Demo Road, London, E1 1AA",
        "height_metres":                        height,
        "storeys_above_ground":                 int(height / 3),
        "primary_use":                          "residential",
        "accountable_person_name":              "Demo Accountable Person Ltd",
        "accountable_person_organisation":      "Demo Property Management",
        "accountable_person_contact":           "ap@demo.example.com",
        "building_safety_manager_name":         "J Demo",
        "building_safety_manager_contact":      "bsm@demo.example.com",
        "bsr_registration_number":              "BSR-DEMO-001",
        "bsr_registration_date":                "2024-04-01",
        "safety_case_documented":               "true",
        "safety_case_report_date":              _today,
        "fire_and_emergency_file_maintained":   "true",
        "residents_engagement_strategy_documented": "true",
        "golden_thread_maintained_by":          "Building Safety Manager",
        "golden_thread_last_updated":           _today,
    }
    record.update(overrides)

    if fail_mode == "safety_case_no_date":
        record["safety_case_documented"] = "true"
        record.pop("safety_case_report_date", None)

    elif fail_mode == "no_golden_thread_date":
        record.pop("golden_thread_last_updated", None)

    elif fail_mode == "no_accountable_person":
        record.pop("accountable_person_name", None)

    elif fail_mode == "no_bsr_number":
        record.pop("bsr_registration_number", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("building_safety", customer, _DEFAULT_MENU)
    run_demo("Building Safety Golden Thread", "building_safety_golden_thread", customer, menu,
             build_building_safety_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV Building Safety golden thread customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from building_safety_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
