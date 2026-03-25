#!/usr/bin/env python3
"""
Customer Companies House demo — validates filing records against companies_house_filing
(Economic Crime and Corporate Transparency Act 2023) and prints a pass/fail summary.

Usage:
    python scripts/customer_companies_house_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_companies_house_demo.py

Customer-specific records live in scripts/companies_house_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Filing Description", "FIL-001", "director", null, {}],
        ...
      ]
    }

fail_mode options: null, "no_id_verification", "dob_invalid", "invalid_role",
                   "missing_id_date"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_today = date.today().isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Director Appointment",          "FIL-001", "director",                    None,                  {}),
    ("Confirmation Statement",        "FIL-002", "director",                    None,                  {}),
    # Failures — four instructive compliance gaps
    ("PSC Register Update",           "FIL-003", "person_with_significant_control", "no_id_verification",  {}),
    ("Annual Accounts Filing",        "FIL-004", "company_secretary",           "dob_invalid",         {}),
    ("Registered Office Change",      "FIL-005", "director",                    "invalid_role",        {}),
    ("LLP Member Registration",       "FIL-006", "llp_member",                  "missing_id_date",     {}),
]

_FAIL_LABELS = {
    "no_id_verification":  "id_verification_method: required — Economic Crime and Corporate Transparency Act 2023",
    "dob_invalid":         "date_of_birth: must be a valid date (YYYY-MM-DD)",
    "invalid_role":        "individual_role: 'silent_partner' not in allowed values",
    "missing_id_date":     "id_verification_date: required",
}


def build_companies_house_record(description: str, filing_id: str, role: str,
                                  fail_mode, overrides: dict) -> dict:
    """Build a valid companies_house_filing record then apply the requested failure mode."""
    record: dict = {
        "company_number":          "12345678",
        "company_name":            "Demo Holdings Ltd",
        "individual_full_name":    "J Demo",
        "individual_role":         role,
        "date_of_birth":           "1980-03-15",
        "nationality":             "British",
        "country_of_residence":    "England",
        "id_verification_method":  "uk_passport",
        "id_verification_date":    _today,
        "id_verified_by":          "Companies House Digital Identity Service",
        "filing_prepared_by":      "Company Secretary",
        "filing_date":             _today,
    }
    record.update(overrides)

    if fail_mode == "no_id_verification":
        record.pop("id_verification_method", None)
        record.pop("id_verification_date", None)
        record.pop("id_verified_by", None)

    elif fail_mode == "dob_invalid":
        record["date_of_birth"] = "N/A"  # not a valid date

    elif fail_mode == "invalid_role":
        record["individual_role"] = "silent_partner"  # not in allowed values

    elif fail_mode == "missing_id_date":
        record.pop("id_verification_date", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("companies_house", customer, _DEFAULT_MENU)
    run_demo("Companies House", "companies_house_filing", customer, menu,
             build_companies_house_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV Companies House filing customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from companies_house_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
