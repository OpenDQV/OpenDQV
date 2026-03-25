#!/usr/bin/env python3
"""
Customer SOX demo — validates internal control test records against sox_control_test
(Sarbanes-Oxley Act 2002 s.302 / s.404, PCAOB AS 2201) and prints a pass/fail summary.

Usage:
    python scripts/customer_sox_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_sox_demo.py

Customer-specific records live in scripts/sox_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Control Name", "CTRL-001", "control_activities", null, {}],
        ...
      ]
    }

fail_mode options: null, "ineffective_no_deficiency", "material_not_escalated",
                   "no_remediation_date", "missing_assertion"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date, timedelta

from _demo_utils import _load_menu, run_demo

_today    = date.today().isoformat()
_q_end    = "2025-12-31"
_rem_date = (date.today() + timedelta(days=90)).isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Financial Close Reconciliation",   "CTRL-001", "control_activities",            None,                        {}),
    ("Journal Entry Approval",           "CTRL-002", "control_activities",            None,                        {}),
    # Failures — four instructive SOX compliance gaps
    ("Revenue Recognition Review",       "CTRL-003", "information_and_communication", "ineffective_no_deficiency", {}),
    ("Access Control Quarterly",         "CTRL-004", "control_environment",           "material_not_escalated",    {}),
    ("Segregation of Duties Check",      "CTRL-005", "risk_assessment",               "no_remediation_date",       {}),
    ("Vendor Payment Authorisation",     "CTRL-006", "control_activities",            "missing_assertion",         {}),
]

_FAIL_LABELS = {
    "ineffective_no_deficiency": "deficiency_classification: required when test_result=ineffective — PCAOB AS 2201",
    "material_not_escalated":    "escalated_to_audit_committee: required when deficiency_classification=material_weakness — SOX 302",
    "no_remediation_date":       "remediation_target_date: required when deficiency_classification=significant_deficiency",
    "missing_assertion":         "management_assertion_by: required — SOX 302 requires CEO/CFO certification",
}


def build_sox_record(control_name: str, control_id: str, category: str,
                     fail_mode, overrides: dict) -> dict:
    """Build a valid sox_control_test record then apply the requested failure mode."""
    record: dict = {
        "control_id":              control_id,
        "control_name":            control_name,
        "control_owner":           "Finance Controller",
        "control_objective":       (
            f"Ensure {control_name.lower()} is performed accurately and "
            "completely, preventing material misstatement of financial statements."
        ),
        "control_category":        category,
        "financial_assertion":     "completeness",
        "fiscal_period_end":       _q_end,
        "test_date":               _today,
        "test_type":               "inspection_of_documents",
        "test_result":             "effective",
        "management_assertion_by": "CFO",
        "assertion_date":          _today,
        "reviewed_by":             "Internal Audit",
        "review_date":             _today,
    }
    record.update(overrides)

    if fail_mode == "ineffective_no_deficiency":
        record["test_result"] = "ineffective"
        # Omit deficiency_classification — this is the key gap

    elif fail_mode == "material_not_escalated":
        record["test_result"]                  = "ineffective"
        record["deficiency_classification"]    = "material_weakness"
        record["remediation_plan"]             = "Immediate control redesign required."
        record["remediation_target_date"]      = _rem_date
        record["remediation_owner"]            = "Finance Controller"
        # Omit escalated_to_audit_committee — required for material_weakness

    elif fail_mode == "no_remediation_date":
        record["test_result"]               = "ineffective"
        record["deficiency_classification"] = "significant_deficiency"
        record["remediation_plan"]          = "Process improvement underway."
        record["remediation_owner"]         = "Finance Controller"
        # Omit remediation_target_date — required for significant_deficiency

    elif fail_mode == "missing_assertion":
        record.pop("management_assertion_by", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("sox", customer, _DEFAULT_MENU)
    run_demo("SOX Control Test", "sox_control_test", customer, menu,
             build_sox_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV SOX internal control test customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from sox_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
