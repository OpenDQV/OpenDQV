#!/usr/bin/env python3
"""
Customer PPDS demo — validates branded menu items against ppds_menu_item
(Natasha's Law) and prints a narration-ready pass/fail summary.

Usage:
    python scripts/customer_ppds_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> OPENDQV_TOKEN=<token> python scripts/customer_ppds_demo.py

Customer-specific menu items are loaded from scripts/ppds_demo_customers.local.json
(gitignored — never committed). If the file doesn't exist or the customer name isn't
in it, the generic _default menu is used instead.

To add a customer, create scripts/ppds_demo_customers.local.json:
    {
      "ACME": [
        ["Item Name", "SKU-001", "sandwich", null, {"contains_gluten": "true", "gluten_cereal_types": "wheat"}],
        ["Another Item", "SKU-002", "side", "no_reviewer", {}]
      ]
    }

fail_mode options: null, "no_reviewer", "sulphites_no_ppm", "blank_allergen", "gluten_no_cereal"
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")
TOKEN    = os.environ.get("OPENDQV_TOKEN", "")

_SCRIPT_DIR = Path(__file__).parent
_LOCAL_CONFIG = _SCRIPT_DIR / "ppds_demo_customers.local.json"

# ── Customer menu definitions ─────────────────────────────────────────────────
#
# Each entry: (item_name, sku, category, fail_mode, allergen_overrides)
#
# fail_mode options:
#   None             — valid record, passes
#   "no_reviewer"    — ppds_compliant_reviewed_by and ppds_review_date omitted
#   "sulphites_no_ppm" — contains_sulphites="true" but sulphites_ppm absent
#   "blank_allergen" — one allergen field left absent (blank ≠ false)
#   "gluten_no_cereal" — contains_gluten="true" but gluten_cereal_types absent
#
# Customer-specific menus live in ppds_demo_customers.local.json (gitignored).
# Only the generic fallback is shipped in the repo.

_DEFAULT_MENU = [
    # Passes — complete, correctly declared records
    ("Crispy Chicken Sandwich",   "DEMO-001", "sandwich", None,
     {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
      "gluten_cereal_types": "wheat"}),
    ("Sweetcorn Side",            "DEMO-002", "side",     None,    {}),
    ("Still Mineral Water",       "DEMO-003", "beverage", None,    {}),
    ("Grilled Salmon Fillet",     "DEMO-004", "hot_food", None,
     {"contains_fish": "true"}),
    # Failures — four instructive real-world gaps
    ("Classic Cheeseburger",      "DEMO-005", "sandwich", "no_reviewer",
     {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
      "contains_mustard": "true", "gluten_cereal_types": "wheat"}),
    ("Creamy Coleslaw",           "DEMO-006", "side",     "sulphites_no_ppm",
     {"contains_eggs": "true", "contains_milk": "true", "contains_sulphites": "true",
      "contains_mustard": "true"}),
    ("Chocolate Fudge Brownie",   "DEMO-007", "dessert",  "blank_allergen",
     {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
      "gluten_cereal_types": "wheat"}),
    ("Garlic Flatbread",          "DEMO-008", "hot_food", "gluten_no_cereal",
     {"contains_gluten": "true", "contains_milk": "true"}),
]


def _load_menu(customer: str) -> list:
    """Load menu for the named customer from local config, fall back to default."""
    if _LOCAL_CONFIG.exists():
        try:
            data = json.loads(_LOCAL_CONFIG.read_text(encoding="utf-8"))
            # Try exact match, then case-insensitive
            menu_raw = data.get(customer) or data.get(customer.upper()) or data.get(customer.lower())
            if menu_raw:
                # Convert JSON arrays to tuples, None strings to None
                return [
                    (row[0], row[1], row[2], row[3] or None, row[4])
                    for row in menu_raw
                ]
        except Exception:
            pass
    return _DEFAULT_MENU


# ── Record builder ────────────────────────────────────────────────────────────

_ALL_ALLERGENS = [
    "celery", "gluten", "crustaceans", "eggs", "fish", "lupin",
    "milk", "molluscs", "mustard", "peanuts", "sesame", "soybeans",
    "sulphites", "tree_nuts",
]


def _base_record(item_name: str, sku: str, category: str,
                 allergen_overrides: dict) -> dict:
    """Build a fully valid PPDS record."""
    record: dict = {
        "item_id":       sku,
        "item_name":     item_name,
        "sku":           sku,
        "category":      category,
        "ingredients_text": (
            f"{item_name} ingredients: flour (wheat), water, salt, sugar, "
            "sunflower oil, natural flavourings."
        ),
        # Audit trail
        "ppds_compliant_reviewed_by": "Head Chef",
        "ppds_review_date":           date.today().isoformat(),
    }

    # Start with all 14 allergens false
    for allergen in _ALL_ALLERGENS:
        record[f"contains_{allergen}"] = "false"

    # Apply realistic allergen profile
    for field, value in allergen_overrides.items():
        record[field] = value

    return record


def build_ppds_record(item_name: str, sku: str, category: str,
                      fail_mode, allergen_overrides: dict) -> dict:
    """Build the record, then apply the requested failure mode."""
    record = _base_record(item_name, sku, category, allergen_overrides)

    if fail_mode == "no_reviewer":
        del record["ppds_compliant_reviewed_by"]
        del record["ppds_review_date"]

    elif fail_mode == "sulphites_no_ppm":
        record["contains_sulphites"] = "true"
        record.pop("sulphites_ppm", None)  # omit the required sub-field

    elif fail_mode == "blank_allergen":
        # Remove tree_nuts entirely — blank ≠ false
        record.pop("contains_tree_nuts", None)

    elif fail_mode == "gluten_no_cereal":
        record["contains_gluten"] = "true"
        record.pop("gluten_cereal_types", None)  # omit the required sub-field

    return record


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _validate(contract: str, record: dict) -> dict:
    url     = f"{BASE_URL}/api/v1/validate"
    payload = json.dumps({"contract": contract, "record": record, "dry_run": True}).encode()
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


# ── Failure summary extractor ─────────────────────────────────────────────────

_FAIL_LABELS = {
    "no_reviewer":       "ppds_compliant_reviewed_by: required",
    "sulphites_no_ppm":  "sulphites_ppm: required when contains_sulphites=true",
    "blank_allergen":    "contains_tree_nuts: field missing — blank is not false",
    "gluten_no_cereal":  "gluten_cereal_types: required when contains_gluten=true",
}


def _first_error(result: dict, fail_mode) -> str:
    """Extract the first error message from a validation result."""
    if fail_mode and fail_mode in _FAIL_LABELS:
        return _FAIL_LABELS[fail_mode]
    errors = result.get("errors") or result.get("violations") or []
    if errors:
        msg = errors[0].get("message") or errors[0].get("error_message") or str(errors[0])
        return msg[:80]
    return "unknown error"


# ── Main ──────────────────────────────────────────────────────────────────────

def run(customer: str) -> None:
    menu = _load_menu(customer)

    print(f"\nOpenDQV PPDS demo — {customer}")
    print("─" * 56)

    passed = 0
    failed = 0
    width  = max(len(item[0]) for item in menu)

    for item_name, sku, category, fail_mode, allergen_overrides in menu:
        record = build_ppds_record(item_name, sku, category, fail_mode, allergen_overrides)
        result = _validate("ppds_menu_item", record)

        if result.get("valid") or result.get("passed"):
            status = "PASS"
            detail = ""
            passed += 1
        else:
            status = "FAIL"
            detail = f"  ({_first_error(result, fail_mode)})"
            failed += 1

        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon}  {item_name:<{width}}  {status}{detail}")

    total = passed + failed
    print("─" * 56)
    print(f"  {passed} passed  /  {failed} failed  ({total} records)")
    print("Done.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV PPDS customer demo — validates branded menu items"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from ppds_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
