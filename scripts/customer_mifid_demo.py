#!/usr/bin/env python3
"""
Customer MiFID II demo — validates transaction reports against mifid_transaction_report
(MiFIR Article 26) and prints a narration-ready pass/fail summary.

Usage:
    python scripts/customer_mifid_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_mifid_demo.py

Customer-specific records live in scripts/mifid_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Trade Description", "TXN-001", "buy", null, {}],
        ...
      ]
    }

fail_mode options: null, "invalid_lei", "bad_isin", "missing_reviewed_by",
                   "invalid_transaction_type"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

# Valid 20-character LEI (18 alphanumeric + 2 check digits)
_DEMO_LEI = "DEMO00000000000000" + "12"  # 20 chars: 18 + "12"
_today    = date.today().isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Equity Buy — Large Cap",       "TXN-001", "buy",  None,                        {}),
    ("Bond Sale — Government Gilt",  "TXN-002", "sell", None,                        {}),
    # Failures — four instructive reporting gaps
    ("ETF Purchase — Tracker Fund",  "TXN-003", "buy",  "invalid_lei",               {}),
    ("FX Spot Trade — EUR/GBP",      "TXN-004", "buy",  "bad_isin",                  {}),
    ("Equity Sale — Mid Cap",        "TXN-005", "sell", "missing_reviewed_by",       {}),
    ("Derivative Contract — Index",  "TXN-006", "buy",  "invalid_transaction_type",  {}),
]

_FAIL_LABELS = {
    "invalid_lei":               "reporting_firm_lei: must be a valid 20-character LEI per ISO 17442",
    "bad_isin":                  "instrument_isin: must be a valid 12-character ISIN per ISO 6166",
    "missing_reviewed_by":       "reviewed_by: required",
    "invalid_transaction_type":  "transaction_type: must be 'buy' or 'sell' — per MiFIR Article 26(3)(d)",
}


def build_mifid_record(description: str, txn_ref: str, txn_type: str,
                       fail_mode, overrides: dict) -> dict:
    """Build a valid mifid_transaction_report record then apply the requested failure mode."""
    record: dict = {
        "transaction_reference": txn_ref,
        "execution_timestamp":   f"{_today}T10:00:00",
        "reporting_firm_lei":    _DEMO_LEI,
        "executing_entity_lei":  _DEMO_LEI,
        "venue_mic":             "XLON",
        "instrument_isin":       "GB00B0SWJX34",  # valid 12-char ISIN
        "buyer_id_type":         "national_client_identifier",
        "buyer_id":              "DEMO-BUYER-001",
        "seller_id_type":        "national_client_identifier",
        "seller_id":             "DEMO-SELLER-001",
        "price":                 142.50,
        "price_type":            "monetary",
        "quantity":              1000,
        "currency":              "GBP",
        "transaction_type":      txn_type,
        "trade_date":            _today,
        "reviewed_by":           "Regulatory Reporting Team",
        "review_date":           _today,
    }
    record.update(overrides)

    if fail_mode == "invalid_lei":
        record["reporting_firm_lei"] = "TOOSHORT0012"  # only 12 chars — fails regex

    elif fail_mode == "bad_isin":
        record["instrument_isin"] = "BADISIN"  # 7 chars — fails 12-char ISIN regex

    elif fail_mode == "missing_reviewed_by":
        record.pop("reviewed_by", None)

    elif fail_mode == "invalid_transaction_type":
        record["transaction_type"] = "HOLD"  # not in: buy, sell

    return record


def run(customer: str) -> None:
    menu = _load_menu("mifid", customer, _DEFAULT_MENU)
    run_demo("MiFID II", "mifid_transaction_report", customer, menu,
             build_mifid_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV MiFID II transaction report customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from mifid_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
