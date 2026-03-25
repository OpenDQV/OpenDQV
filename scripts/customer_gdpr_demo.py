#!/usr/bin/env python3
"""
Customer GDPR demo — validates DSAR records against gdpr_dsar_request
(UK GDPR Article 15 / DPA 2018) and prints a narration-ready pass/fail summary.

Usage:
    python scripts/customer_gdpr_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_gdpr_demo.py

Customer-specific records live in scripts/gdpr_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Request Description", "DSAR-001", "subject_access", null, {}],
        ...
      ]
    }

fail_mode options: null, "id_check_incomplete", "extension_no_reason",
                   "no_handler", "closed_no_outcome"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date, timedelta

from _demo_utils import _load_menu, run_demo

_today     = date.today().isoformat()
_due_date  = (date.today() + timedelta(days=28)).isoformat()

_DEFAULT_MENU = [
    # Passes
    ("New Customer Data Request",       "DSAR-001", "subject_access",  None,            {}),
    ("Employee Right of Access",        "DSAR-002", "subject_access",  None,            {}),
    # Failures — four instructive compliance gaps
    ("Marketing Opt-Out Request",       "DSAR-003", "objection",       "id_check_incomplete",  {}),
    ("Data Deletion Request",           "DSAR-004", "erasure",         "extension_no_reason",  {}),
    ("Subject Access — Third Party",    "DSAR-005", "subject_access",  "no_handler",           {}),
    ("Rectification — Address Change",  "DSAR-006", "rectification",   "closed_no_outcome",    {}),
]

_FAIL_LABELS = {
    "id_check_incomplete":  "id_verification_method: required when id_verification_completed=true",
    "extension_no_reason":  "extension_reason: required when extension_applied=true",
    "no_handler":           "assigned_to: required — every DSAR must have a named handler",
    "closed_no_outcome":    "outcome: required when status=closed",
}


def build_gdpr_record(description: str, request_id: str, request_type: str,
                      fail_mode, overrides: dict) -> dict:
    """Build a valid gdpr_dsar_request record then apply the requested failure mode."""
    record: dict = {
        "request_id":               request_id,
        "requester_name":           "J Smith",
        "requester_email":          f"requester-{request_id.lower()}@example.com",
        "receipt_date":             _today,
        "response_due_date":        _due_date,
        "request_channel":          "email",
        "request_type":             request_type,
        "id_verification_completed": "true",
        "id_verification_date":     _today,
        "id_verification_method":   "uk_passport",
        "extension_applied":        "false",
        "status":                   "in_progress",
        "assigned_to":              "Data Protection Officer",
        "reviewed_by":              "DPO",
        "review_date":              _today,
    }
    record.update(overrides)

    if fail_mode == "id_check_incomplete":
        # ID check declared complete but method not recorded
        record["id_verification_completed"] = "true"
        record.pop("id_verification_method", None)
        record.pop("id_verification_date", None)

    elif fail_mode == "extension_no_reason":
        # Extension granted but no reason recorded
        record["extension_applied"] = "true"
        record.pop("extension_reason", None)
        record.pop("extended_due_date", None)

    elif fail_mode == "no_handler":
        record.pop("assigned_to", None)

    elif fail_mode == "closed_no_outcome":
        record["status"] = "closed"
        record.pop("outcome", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("gdpr", customer, _DEFAULT_MENU)
    run_demo("GDPR DSAR", "gdpr_dsar_request", customer, menu,
             build_gdpr_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV GDPR DSAR customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from gdpr_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
