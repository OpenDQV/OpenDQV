#!/usr/bin/env python3
"""
Customer PPDS demo — validates branded menu items against ppds_menu_item
(Natasha's Law) and prints a narration-ready pass/fail summary.

Usage:
    python scripts/customer_ppds_demo.py --customer DEMO_CUSTOMER
    OPENDQV_CUSTOMER=DEMO_CUSTOMER OPENDQV_TOKEN=<token> python scripts/customer_ppds_demo.py

Produces a mix of passes and instructive failures that demonstrate the four
most common real-world allergen data quality gaps.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import date

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")
TOKEN    = os.environ.get("OPENDQV_TOKEN", "")

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
# allergen_overrides: dict of contains_<name> → "true"/"false" overriding defaults.
# Realistic profiles — not official QSR_COMPANY data, illustrative only.

CUSTOMER_MENUS = {
    DEMO_CUSTOMER: [
        (
            "Original Recipe Chicken Burger",
            "QSR-001", "sandwich", None,
            {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
             "gluten_cereal_types": "wheat"},
        ),
        (
            "Zinger Tower Burger",
            "QSR-002", "sandwich", "no_reviewer",
            {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
             "contains_mustard": "true", "gluten_cereal_types": "wheat"},
        ),
        (
            "Corn on the Cob",
            "QSR-003", "side", None,
            {},
        ),
        (
            "Coleslaw",
            "QSR-004", "side", "sulphites_no_ppm",
            {"contains_eggs": "true", "contains_milk": "true", "contains_sulphites": "true",
             "contains_mustard": "true"},
        ),
        (
            "Chocolate Mousse",
            "QSR-005", "dessert", "blank_allergen",
            {"contains_milk": "true", "contains_eggs": "true", "contains_gluten": "true",
             "gluten_cereal_types": "wheat"},
        ),
        (
            "Crispy Strips (3 pc)",
            "QSR-006", "hot_food", None,
            {"contains_gluten": "true", "contains_eggs": "true",
             "gluten_cereal_types": "wheat"},
        ),
        (
            "Fries (Large)",
            "QSR-007", "side", None,
            {},
        ),
        (
            "BBQ Dipping Sauce",
            "QSR-008", "sauce", "gluten_no_cereal",
            {"contains_gluten": "true", "contains_soybeans": "true",
             "contains_sulphites": "true", "sulphites_ppm": 15},
        ),
        (
            "Pepsi Max (500ml)",
            "QSR-009", "beverage", None,
            {},
        ),
        (
            "Krushems Strawberry",
            "QSR-010", "dessert", "no_reviewer",
            {"contains_milk": "true"},
        ),
    ],
    "_default": [
        ("House Special Burger",  "ITEM-001", "sandwich", None,
         {"contains_gluten": "true", "contains_eggs": "true", "gluten_cereal_types": "wheat"}),
        ("Garden Salad",          "ITEM-002", "salad",    None,    {}),
        ("Cheese Sauce",          "ITEM-003", "sauce",    "no_reviewer",
         {"contains_milk": "true"}),
        ("Chocolate Brownie",     "ITEM-004", "dessert",  "blank_allergen",
         {"contains_gluten": "true", "contains_eggs": "true", "contains_milk": "true",
          "gluten_cereal_types": "wheat"}),
        ("Sparkling Water",       "ITEM-005", "beverage", None,    {}),
        ("Garlic Bread",          "ITEM-006", "hot_food", "gluten_no_cereal",
         {"contains_gluten": "true", "contains_milk": "true"}),
        ("Seafood Platter",       "ITEM-007", "hot_food", None,
         {"contains_crustaceans": "true", "contains_molluscs": "true", "contains_fish": "true"}),
        ("Coleslaw",              "ITEM-008", "side",     "sulphites_no_ppm",
         {"contains_eggs": "true", "contains_milk": "true", "contains_sulphites": "true",
          "contains_mustard": "true"}),
    ],
}

# ── Record builder ────────────────────────────────────────────────────────────

_ALL_ALLERGENS = [
    "celery", "gluten", "crustaceans", "eggs", "fish", "lupin",
    "milk", "molluscs", "mustard", "peanuts", "sesame", "soybeans",
    "sulphites", "tree_nuts",
]

_BLANK_TARGETS = {
    # fail_mode → which allergen field to leave absent
    "blank_allergen": "contains_tree_nuts",
}


def _base_record(customer: str, item_name: str, sku: str, category: str,
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


def build_ppds_record(customer: str, item_name: str, sku: str, category: str,
                      fail_mode, allergen_overrides: dict) -> dict:
    """Build the record, then apply the requested failure mode."""
    record = _base_record(customer, item_name, sku, category, allergen_overrides)

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
            return {"passed": False, "errors": [{"message": f"HTTP {exc.code}: {body[:200]}"}]}


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
    menu = CUSTOMER_MENUS.get(customer.upper(), CUSTOMER_MENUS.get(customer)) \
        or CUSTOMER_MENUS["_default"]

    print(f"\nOpenDQV PPDS demo — {customer}")
    print("─" * 56)

    passed = 0
    failed = 0
    width  = max(len(item[0]) for item in menu)

    for item_name, sku, category, fail_mode, allergen_overrides in menu:
        record = build_ppds_record(
            customer, item_name, sku, category, fail_mode, allergen_overrides
        )
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
        default=os.environ.get("OPENDQV_CUSTOMER", DEMO_CUSTOMER),
        help="Customer name (default: DEMO_CUSTOMER). Add entries to CUSTOMER_MENUS for new customers.",
    )
    args = parser.parse_args()
    run(args.customer)
