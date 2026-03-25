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

Records persist with context='demo' during the run.
Run scripts/teardown_demo.py to clean up after the session.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

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

_FAIL_LABELS = {
    "no_reviewer":       "ppds_compliant_reviewed_by: required",
    "sulphites_no_ppm":  "sulphites_ppm: required when contains_sulphites=true",
    "blank_allergen":    "contains_tree_nuts: field missing — blank is not false",
    "gluten_no_cereal":  "gluten_cereal_types: required when contains_gluten=true",
}

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
        "ppds_compliant_reviewed_by": "Head Chef",
        "ppds_review_date":           date.today().isoformat(),
    }
    for allergen in _ALL_ALLERGENS:
        record[f"contains_{allergen}"] = "false"
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
        record.pop("sulphites_ppm", None)

    elif fail_mode == "blank_allergen":
        record.pop("contains_tree_nuts", None)

    elif fail_mode == "gluten_no_cereal":
        record["contains_gluten"] = "true"
        record.pop("gluten_cereal_types", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("ppds", customer, _DEFAULT_MENU)
    run_demo("PPDS", "ppds_menu_item", customer, menu, build_ppds_record, _FAIL_LABELS)


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
