# Natasha's Law — QSR Allergen Compliance

> **Last reviewed:** 2026-03-23.

Natasha's Law (The Food Information (Amendment) (England) Regulations 2019, in force
1 October 2021) requires food businesses that prepare and sell food on the same premises
to label **Pre-Packed for Direct Sale (PPDS)** food with a full ingredients list, with
all 14 major allergens **explicitly highlighted** within it.

OpenDQV enforces this obligation at the point of write — before a menu item record
reaches the labelling system, POS, or kitchen — making allergen omission structurally
impossible rather than relying on human review.

---

## Who is in scope

Any **Quick Service Restaurant (QSR)** that makes and sells
food on the same premises:

- Sandwich shops and delis
- Bakeries and coffee shops
- Fast food chains
- Staff canteens and school kitchens
- Supermarket deli and bakery counters

If you make it and sell it on-site wrapped, you are in scope.

---

## The 14 declarable allergens

Under UK food information law (retained EU Regulation 1169/2011):

| Field | Allergen |
|-------|----------|
| `contains_celery` | Celery |
| `contains_gluten` | Cereals containing gluten (wheat, rye, barley, oats) |
| `contains_crustaceans` | Crustaceans |
| `contains_eggs` | Eggs |
| `contains_fish` | Fish |
| `contains_lupin` | Lupin |
| `contains_milk` | Milk |
| `contains_molluscs` | Molluscs |
| `contains_mustard` | Mustard |
| `contains_peanuts` | Peanuts |
| `contains_sesame` | Sesame |
| `contains_soybeans` | Soybeans |
| `contains_sulphites` | Sulphur dioxide / sulphites |
| `contains_tree_nuts` | Tree nuts (with specific nut type required) |

Each field accepts `"true"` or `"false"`. Every field is **mandatory**. A record with
any allergen field missing is rejected with HTTP 422 before it enters the system.

---

## How OpenDQV enforces Natasha's Law

The enforcement model is structural, not advisory:

```
Operator enters menu item
          │
          ▼
POST /api/v1/validate/ppds_menu_item
          │
          ├── contains_celery missing?       → 422 (not_empty)
          ├── contains_peanuts missing?      → 422 (not_empty)
          ├── contains_tree_nuts = true
          │   but tree_nut_types missing?    → 422 (required_if)
          ├── contains_gluten = true
          │   but gluten_cereal_types missing? → 422 (required_if)
          └── all 14 declarations present?  → valid: true
                    │
                    ▼
             Record saved / label generated
```

This is the earliest possible intervention — the operator fixes the omission before the
item is saved, printed, or served. The failure mode Natasha's Law was designed to prevent
was **omission by oversight**, not malice. OpenDQV prevents omission structurally.

---

## Why 14 boolean fields, not a single list

A single `allergens_present` list field is compact but cannot detect dangerous omissions.
If "peanuts" is absent from the list, OpenDQV cannot determine whether peanuts are
genuinely absent or were accidentally forgotten. The only way to enforce the obligation
to declare is to make each of the 14 allergens a mandatory field.

| Approach | Catches invalid values | Catches omission | Natasha's Law safe |
|----------|----------------------|------------------|--------------------|
| 14 boolean fields | Yes | **Yes** | **Yes** |
| Single list field | Yes | **No** | **No** |

The risk asymmetry is severe — a false negative on an allergen declaration can be fatal.

---

## Contract rules summary

The `ppds_menu_item` contract applies the following rule types:

| Pattern | Rules |
|---------|-------|
| `not_empty` on each allergen field | All 14 allergen declarations are mandatory |
| `lookup` against `ref/allergen_boolean.txt` | Values must be `true` or `false` |
| `required_if contains_gluten = true` | `gluten_cereal_types` must name the cereal(s) |
| `required_if contains_tree_nuts = true` | `tree_nut_types` must name the nut(s) |
| `min: 10` with `condition: contains_sulphites = true` | `sulphites_ppm` must be ≥ 10ppm when declared |
| `not_empty` + `date_format` | `ppds_review_date` audit trail |

---

## Sulphite threshold (the one quantitative rule)

Sulphur dioxide and sulphites only require declaration above 10ppm. The contract
enforces this with a conditional `min` rule:

```yaml
- name: sulphites_ppm_required_if_sulphites
  field: sulphites_ppm
  type: required_if
  required_if:
    field: contains_sulphites
    value: "true"
  severity: error
  error_message: "sulphites_ppm is required when contains_sulphites is true — record the concentration in ppm (must be >= 10)"

- name: sulphites_ppm_min
  field: sulphites_ppm
  type: min
  min: 10
  condition:
    field: contains_sulphites
    value: "true"
  severity: error
  error_message: "sulphites_ppm must be >= 10 — sulphites only require declaration above the 10ppm statutory threshold"
```

If `contains_sulphites` is `"false"`, `sulphites_ppm` is optional and neither rule
fires. If `contains_sulphites` is `"true"`, `sulphites_ppm` is **required** (first
rule) and must be ≥ 10 (second rule).

---

## POS and recipe management integration

Most QSRs maintain menu items in one of:

- A **POS system** (Lightspeed, Square, Toast, Clover, EPOS Now)
- A **recipe management tool** (Nutritics, ChefDesk, Apicbase, FoodIQ)
- A back-office **spreadsheet or ERP**

The integration point is wherever a new menu item record is written. Validate at
that write event — not at label print time.

### REST API example (Python)

```python
import os
import httpx

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

def validate_menu_item(record: dict) -> dict:
    """Validate a PPDS menu item against the Natasha's Law contract.
    Raises ValueError if the record fails validation.
    """
    resp = httpx.post(
        f"{OPENDQV_URL}/api/v1/validate/ppds_menu_item",
        json=record,
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
        timeout=5.0,
    )
    result = resp.json()
    if not result.get("valid"):
        errors = [e["message"] for e in result.get("errors", [])]
        raise ValueError(f"PPDS allergen validation failed: {'; '.join(errors)}")
    return result


# Example: new menu item from POS system webhook
new_item = {
    "item_id": "ITEM-00042",
    "item_name": "BLT Sandwich on Malted Wheat",
    "sku": "SKU-BLT-MWHT-001",
    "category": "sandwich",
    "ingredients_text": "Malted wheat bread (wheat flour, water, yeast, salt), bacon, lettuce, tomato, mayonnaise (eggs, rapeseed oil, mustard).",
    "contains_celery": "false",
    "contains_gluten": "true",
    "gluten_cereal_types": ["wheat"],
    "contains_crustaceans": "false",
    "contains_eggs": "true",
    "contains_fish": "false",
    "contains_lupin": "false",
    "contains_milk": "false",
    "contains_molluscs": "false",
    "contains_mustard": "true",
    "contains_peanuts": "false",
    "contains_sesame": "false",
    "contains_soybeans": "false",
    "contains_sulphites": "false",
    "contains_tree_nuts": "false",
    "ppds_compliant_reviewed_by": "J. Patel (Head Chef)",
    "ppds_review_date": "2026-03-21",
}

validate_menu_item(new_item)  # raises on failure, returns result on success
```

### SDK example

```python
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
result = client.validate("ppds_menu_item", new_item)

if not result.valid:
    for error in result.errors:
        print(f"  ✗ {error.field}: {error.message}")
```

---

## The audit trail

The contract includes two audit trail fields:

| Field | Purpose |
|-------|---------|
| `ppds_compliant_reviewed_by` | Name/role of the person who verified allergen declarations |
| `ppds_review_date` | Date of the allergen compliance review (YYYY-MM-DD) |

These fields are **mandatory**. In the event of an allergen incident or FSA inspection,
the validated record provides a timestamped, per-item audit trail of who declared what
and when.

---

## Scope: Scotland, Wales, Northern Ireland, and the EU

**UK-wide:** Natasha's Law applies in England. Equivalent regulations are in force in
Scotland (The Food Information (Amendment) (Scotland) Regulations 2021), Wales, and
Northern Ireland. The same 14 allergens apply across all four nations.

**EU equivalent:** Food Information to Consumers Regulation (EU) No 1169/2011 (FIC)
applies in all EU member states and covers the same 14 allergens with broadly
equivalent labelling requirements. The `ppds_menu_item` contract is compliant with FIC
as well as Natasha's Law.

---

## Limitations

OpenDQV validates the **declaration**, not the **accuracy** of the declaration. The
contract enforces that an operator has answered every allergen question before saving
the record. It cannot verify that the declared values accurately reflect the actual
ingredients — that remains the responsibility of the chef, recipe owner, and supplier
chain. Ingredient verification (cross-checking declared allergens against recipe
components) requires a cross-contract rule or a human review step.

The audit trail (`ppds_compliant_reviewed_by` + `ppds_review_date`) creates an
accountability anchor — the named reviewer is asserting the accuracy of the
declarations at the time of review.

---

## Related resources

- Contract: `contracts/ppds_menu_item.yaml` — 14 boolean allergen fields (REST API / bulk validation)
- Starter contract: `examples/ppds/ppds_menu_item.yaml`
- Sample records: `examples/ppds/`
- Reference files: `contracts/ref/allergen_boolean.txt`, `allergen_gluten_cereals.txt`,
  `allergen_tree_nut_types.txt`, `qsr_item_categories.txt`
- UK legislation: [food.gov.uk/business-guidance/natashas-law](https://www.food.gov.uk/business-guidance/introduction-to-allergen-labelling-changes-ppds)
- EU FIC: Regulation (EU) No 1169/2011
