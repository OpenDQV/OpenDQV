#!/usr/bin/env python3
"""
Shared utilities for OpenDQV customer demo scripts.
Imported by customer_<contract>_demo.py scripts — not run directly.

Records are persisted with context='demo' so they appear in quality metrics
and in Marmot while the demo runs. Use scripts/teardown_demo.py to clean up
after the session.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")
TOKEN    = os.environ.get("OPENDQV_TOKEN", "")

_SCRIPT_DIR = Path(__file__).parent


def _validate(contract: str, record: dict) -> dict:
    """POST /api/v1/validate with context='demo'. Returns API response dict."""
    url     = f"{BASE_URL}/api/v1/validate"
    payload = json.dumps({
        "contract": contract,
        "record":   record,
        "context":  "demo",
    }).encode()
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"valid": False, "errors": [{"message": f"HTTP {exc.code}: {body[:200]}"}]}


def _first_error(result: dict, fail_label: str | None = None) -> str:
    """Return a short error description for display."""
    if fail_label:
        return fail_label
    errors = result.get("errors") or result.get("violations") or []
    if errors:
        msg = errors[0].get("message") or errors[0].get("error_message") or str(errors[0])
        return msg[:80]
    return "unknown error"


def _load_menu(contract_key: str, customer: str, default_menu: list) -> list:
    """
    Load from scripts/<contract_key>_demo_customers.local.json if present.
    Falls back to default_menu.
    """
    local_config = _SCRIPT_DIR / f"{contract_key}_demo_customers.local.json"
    if local_config.exists():
        try:
            data = json.loads(local_config.read_text(encoding="utf-8"))
            menu_raw = (
                data.get(customer)
                or data.get(customer.upper())
                or data.get(customer.lower())
            )
            if menu_raw:
                return [
                    (row[0], row[1], row[2], row[3] or None, row[4])
                    for row in menu_raw
                ]
        except Exception:
            pass
    return default_menu


def run_demo(
    title: str,
    contract: str,
    customer: str,
    menu: list,
    build_record_fn,
    fail_labels: dict | None = None,
) -> None:
    """
    Print a narration-ready pass/fail table and summary.

    Records are persisted with context='demo' — the analytics dashboard updates
    in real time as each item validates. Data stays up after the script ends.
    Run scripts/teardown_demo.py to clean up after the session.
    """
    print(f"\nOpenDQV {title} demo — {customer}")
    print("─" * 56)
    passed = 0
    failed = 0
    width  = max(len(row[0]) for row in menu)

    for item_name, rec_id, rec_type, fail_mode, extras in menu:
        record = build_record_fn(item_name, rec_id, rec_type, fail_mode, extras)
        result = _validate(contract, record)

        if result.get("valid") or result.get("passed"):
            passed += 1
            print(f"  ✓  {item_name:<{width}}  PASS")
        else:
            failed += 1
            label = (fail_labels or {}).get(fail_mode) if fail_mode else None
            err   = _first_error(result, label)
            print(f"  ✗  {item_name:<{width}}  FAIL  ({err})")

    total = passed + failed
    print("─" * 56)
    print(f"  {passed} passed  /  {failed} failed  ({total} records)")
    print("Done.\n")
