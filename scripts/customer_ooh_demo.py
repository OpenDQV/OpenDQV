#!/usr/bin/env python3
"""
Customer OOH Proof of Play demo — validates panel impression records against
proof_of_play (out-of-home advertising) and prints a pass/fail summary.

Usage:
    python scripts/customer_ooh_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_ooh_demo.py

Customer-specific records live in scripts/ooh_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Location Name", "IMP-001", "CLASSIC", null, {}],
        ...
      ]
    }

fail_mode options: null, "bad_panel_id", "inverted_timestamps",
                   "digital_no_refresh", "negative_charge"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_today = date.today().isoformat()
_start = f"{_today}T09:00:00Z"
_end   = f"{_today}T09:10:00Z"
_early = f"{_today}T08:50:00Z"   # before _start — inverted timestamp

_DEFAULT_MENU = [
    # Passes
    ("Waterloo Station Billboard",   "IMP-001", "CLASSIC",  None,                {}),
    ("Heathrow T5 Digital Screen",   "IMP-002", "DIGITAL",  None,                {}),
    # Failures — four instructive OOH billing and data-quality gaps
    ("Piccadilly Roadside Panel",    "IMP-003", "CLASSIC",  "bad_panel_id",      {}),
    ("Canary Wharf Transit Display", "IMP-004", "TRANSIT",  "inverted_timestamps", {}),
    ("Oxford Street DOOH Screen",    "IMP-005", "DIGITAL",  "digital_no_refresh", {}),
    ("Victoria Coach Station",       "IMP-006", "CLASSIC",  "negative_charge",   {}),
]

_FAIL_LABELS = {
    "bad_panel_id":         "panel_id must match format e.g. LGM-UK-00001",
    "inverted_timestamps":  "impression_end must be later than impression_start (inverted timestamps cause phantom billing)",
    "digital_no_refresh":   "refresh_rate_hz is required when panel_type is DIGITAL",
    "negative_charge":      "revenue_gbp must be >= 0 for CHARGE records (credits are exempt)",
}


def build_ooh_record(location: str, imp_id: str, panel_type: str,
                     fail_mode, overrides: dict) -> dict:
    """Build a valid proof_of_play record then apply the requested failure mode."""
    seq = int(imp_id.split("-")[-1])

    record: dict = {
        "panel_id":         f"LGM-UK-{seq:05d}",
        "market":           "UK",
        "panel_type":       panel_type,
        "impression_start": _start,
        "impression_end":   _end,
        "transaction_type": "CHARGE",
        "revenue_gbp":      1250.00,
        "advertiser_id":    "ADV-00123456",
        "creative_id":      f"CRE-{seq:04d}",
        "campaign_ref":     f"CAMP-DEMO-{seq:03d}",
        "dwell_seconds":    10,
    }

    if panel_type == "DIGITAL":
        record["refresh_rate_hz"] = 30

    record.update(overrides)

    if fail_mode == "bad_panel_id":
        record["panel_id"] = "GLOBAL-123"           # fails regex — missing market segment

    elif fail_mode == "inverted_timestamps":
        record["impression_end"] = _early            # before start → phantom billing

    elif fail_mode == "digital_no_refresh":
        record["panel_type"] = "DIGITAL"
        record.pop("refresh_rate_hz", None)          # required_if DIGITAL

    elif fail_mode == "negative_charge":
        record["transaction_type"] = "CHARGE"
        record["revenue_gbp"]      = -500.00         # negative revenue on CHARGE record

    return record


def run(customer: str) -> None:
    menu = _load_menu("ooh", customer, _DEFAULT_MENU)
    run_demo("OOH Proof of Play", "proof_of_play", customer, menu,
             build_ooh_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV OOH Proof of Play customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from ooh_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
