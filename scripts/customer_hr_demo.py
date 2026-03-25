#!/usr/bin/env python3
"""
Customer HR demo — validates employee records against hr_employee
(UK employment law, HMRC RTI, UK GDPR) and prints a narration-ready pass/fail summary.

Usage:
    python scripts/customer_hr_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_hr_demo.py

Customer-specific records live in scripts/hr_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Job Title", "EMP-001", "contract_type", null, {"field": "value"}],
        ...
      ]
    }

fail_mode options: null, "invalid_ni", "invalid_contract", "bad_email", "invalid_rtw"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_DEFAULT_MENU = [
    # Passes — complete valid records
    ("Warehouse Operative",   "EMP-001", "permanent",   None,
     {}),
    ("Shift Supervisor",      "EMP-002", "zero_hours",  None,
     {}),
    ("Finance Manager",       "EMP-003", "permanent",   None,
     {}),
    # Failures — three instructive compliance gaps
    ("Customer Assistant",    "EMP-004", "permanent",   "invalid_ni",
     {}),
    ("HR Administrator",      "EMP-005", "permanent",   "invalid_contract",
     {}),
    ("Logistics Coordinator", "EMP-006", "fixed_term",  "bad_email",
     {}),
    ("IT Support Analyst",    "EMP-007", "contractor",  "invalid_rtw",
     {}),
]

_FAIL_LABELS = {
    "invalid_ni":       "ni_number: invalid UK NI format — HMRC RTI will reject",
    "invalid_contract": "contract_type: 'freelancer' not a valid employment classification",
    "bad_email":        "email: must be a valid email address",
    "invalid_rtw":      "right_to_work_status: 'not_checked' not in allowed values — civil penalty risk",
}


def build_hr_record(job_title: str, emp_id: str, contract_type: str,
                    fail_mode, overrides: dict) -> dict:
    """Build a valid hr_employee record then apply the requested failure mode."""
    record: dict = {
        "employee_id":        emp_id,
        "first_name":         "Alex",
        "last_name":          "Morgan",
        "email":              f"{emp_id.lower()}@demo-company.example",
        "ni_number":          "AB123456C",
        "department":         "Operations",
        "job_title":          job_title,
        "start_date":         "2024-01-15",
        "salary":             32000,
        "salary_currency":    "GBP",
        "contract_type":      contract_type,
        "employment_status":  "active",
        "right_to_work_status": "verified_british_irish",
    }
    record.update(overrides)

    if fail_mode == "invalid_ni":
        record["ni_number"] = "ZZ123456C"  # ZZ is an excluded prefix

    elif fail_mode == "invalid_contract":
        record["contract_type"] = "freelancer"  # not in allowed values

    elif fail_mode == "bad_email":
        record["email"] = "not-a-valid-email"  # fails regex

    elif fail_mode == "invalid_rtw":
        record["right_to_work_status"] = "not_checked"  # not in allowed values

    return record


def run(customer: str) -> None:
    menu = _load_menu("hr", customer, _DEFAULT_MENU)
    run_demo("HR Employee", "hr_employee", customer, menu, build_hr_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV HR employee customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from hr_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
