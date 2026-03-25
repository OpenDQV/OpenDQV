#!/usr/bin/env python3
"""
Customer DORA demo — validates ICT incident records against dora_ict_incident
(DORA Regulation (EU) 2022/2554, Articles 17-19) and prints a pass/fail summary.

Usage:
    python scripts/customer_dora_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_dora_demo.py

Customer-specific records live in scripts/dora_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Incident Description", "INC-001", "minor", null, {}],
        ...
      ]
    }

fail_mode options: null, "late_early_warning", "late_notification",
                   "major_no_root_cause", "resolved_no_date"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date, timedelta

from _demo_utils import _load_menu, run_demo

_today      = date.today().isoformat()
_yesterday  = (date.today() - timedelta(days=1)).isoformat()
_3days_ago  = (date.today() - timedelta(days=3)).isoformat()
_5days_ago  = (date.today() - timedelta(days=5)).isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Core Banking System Outage",      "INC-001", "minor",        None,                  {}),
    ("Payment Processing Delay",        "INC-002", "significant",  None,                  {}),
    # Failures — four instructive DORA reporting gaps
    ("API Gateway Failure",             "INC-003", "minor",        "late_early_warning",   {}),
    ("Data Centre Failover Event",      "INC-004", "minor",        "late_notification",    {}),
    ("Third-Party Provider Outage",     "INC-005", "major",        "major_no_root_cause",  {}),
    ("Trading System Connectivity Loss","INC-006", "minor",        "resolved_no_date",     {}),
]

_FAIL_LABELS = {
    "late_early_warning":  "early_warning_timestamp: must be within 1 day of detection — Article 19(4)(a) DORA",
    "late_notification":   "initial_notification_timestamp: must be within 3 days of detection — Article 19(4)(b) DORA",
    "major_no_root_cause": "root_cause: required when incident_classification is major — Article 19(4)(e) DORA",
    "resolved_no_date":    "remediation_date: required when remediation_status is resolved",
}


def build_dora_record(description: str, incident_id: str, classification: str,
                      fail_mode, overrides: dict) -> dict:
    """Build a valid dora_ict_incident record then apply the requested failure mode."""
    # Base: detection today, early warning same day, notification next day, all good
    record: dict = {
        "incident_id":                    incident_id,
        "incident_title":                 description,
        "entity_name":                    "Demo Financial Services Ltd",
        "entity_type":                    "credit_institution",
        "incident_classification":        classification,
        "incident_description":           (
            f"{description} — service disruption detected and contained. "
            "Impact limited to non-critical systems."
        ),
        "affected_services":              "Online banking portal",
        "detection_timestamp":            _today,
        "early_warning_timestamp":        _today,        # same day = 0 days diff ✓
        "initial_notification_timestamp": _today,        # same day = 0 days diff ✓
        "remediation_status":             "contained",
        "reviewed_by":                    "ICT Risk Management Team",
        "review_date":                    _today,
    }
    if classification in ("major", "significant"):
        record["root_cause"] = "Third-party software defect identified and patched."

    record.update(overrides)

    if fail_mode == "late_early_warning":
        record["detection_timestamp"]      = _5days_ago
        record["early_warning_timestamp"]  = _3days_ago   # 2 days after detection > max 1
        record["initial_notification_timestamp"] = _3days_ago

    elif fail_mode == "late_notification":
        record["detection_timestamp"]                = _5days_ago
        record["early_warning_timestamp"]            = _5days_ago   # same day ✓
        record["initial_notification_timestamp"]     = _today       # 5 days after > max 3

    elif fail_mode == "major_no_root_cause":
        record["incident_classification"] = "major"
        record.pop("root_cause", None)

    elif fail_mode == "resolved_no_date":
        record["remediation_status"] = "resolved"
        record.pop("remediation_date", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("dora", customer, _DEFAULT_MENU)
    run_demo("DORA ICT Incident", "dora_ict_incident", customer, menu,
             build_dora_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV DORA ICT incident customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from dora_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
