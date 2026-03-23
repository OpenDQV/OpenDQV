# AllerEasy Integration — Natasha's Law Allergen Audit Enforcement

> **AllerEasy:** [allereasy.co.uk](https://www.allereasy.co.uk/) — open-source (GPL v3),
> Django-based allergen management for UK/EU hospitality businesses. Built by a solo
> developer to give small cafés, delis, and school kitchens a free path to Natasha's Law
> compliance.

---

## The gap this integration closes

AllerEasy stores allergens as a Django ManyToMany relationship with `blank=True`:

```python
allergens = models.ManyToManyField(Allergen, blank=True)
```

This means a dish can be set to `active` and served with no allergen status ever
recorded. The system cannot distinguish **"reviewed and confirmed allergen-free"**
from **"never reviewed at all"**. That is the structural gap Natasha's Law exists to
close — the failure mode that killed Natasha Ednan-Laperouse in 2016.

OpenDQV closes it with a three-field audit trail and hard enforcement at the point
of write.

---

## What this integration adds

### Three new fields on `Dish`

| Field | Type | Purpose |
|-------|------|---------|
| `allergen_review_confirmed` | `BooleanField` | Explicit confirmation that allergen status has been reviewed |
| `allergen_reviewed_by` | `CharField(100)` | Name of the person who performed the review |
| `allergen_review_date` | `DateField` | Date the review was completed (ISO 8601) |

### `Dish.clean()` enforcement

When `ENABLE_OPENDQV_VALIDATION=true` and a dish is saved with `status='active'`,
the `clean()` method validates the three fields via OpenDQV's `LocalValidator` and
the `allereasy_dish` contract. Missing or invalid fields raise a `ValidationError`
before the record reaches the database.

Dishes with `status='inactive'` or `status='archive'` are not affected — staff can
work on drafts freely.

### Zero breaking changes

- All new fields are nullable/blank — existing records are not affected
- Validation is off by default — set `ENABLE_OPENDQV_VALIDATION=true` to enable
- `opendqv` not installed? The `clean()` method skips silently

---

## Setup

### 1. Get the code

The integration is available as a PR on the AllerEasy repository:
[github.com/rhcompbuilds/Allereasy/pull/1](https://github.com/rhcompbuilds/Allereasy/pull/1)

### 2. Install opendqv

```bash
pip install opendqv>=1.3.3
```

### 3. Run the migration

```bash
python manage.py migrate
```

### 4. Enable validation

```bash
export ENABLE_OPENDQV_VALIDATION=true
```

Or permanently in `allergens/settings.py`:

```python
ENABLE_OPENDQV_VALIDATION = True
```

### 5. Verify

Start the server and open a dish in the dashboard. Set `status=active` without
completing the allergen review. The save should be blocked with:

```
Allergen compliance check failed:
  allergen_review_confirmed: must be 'true' — tick the confirmation box to
    record that allergen status has been reviewed (Natasha's Law PPDS compliance)
  allergen_reviewed_by: is required — record who verified the allergen information
  allergen_review_date: is required
```

---

## How it works

The `allereasy_dish` contract (`contracts/allereasy_dish.yaml`) defines what
"valid" means for the audit trail fields:

```yaml
rules:
  - name: allergen_review_confirmed_true
    field: allergen_review_confirmed
    type: allowed_values
    allowed_values: ["true"]
    error_message: "allergen_review_confirmed must be 'true' ..."

  - name: allergen_reviewed_by_required
    field: allergen_reviewed_by
    type: not_empty
    error_message: "allergen_reviewed_by is required ..."

  - name: allergen_review_date_format
    field: allergen_review_date
    type: date_format
    format: "%Y-%m-%d"
    error_message: "allergen_review_date must be a valid ISO 8601 date ..."
```

The `Dish.clean()` method serialises the three fields into a flat dict and passes
them to `LocalValidator`:

```python
def clean(self):
    from django.conf import settings as django_settings
    from django.core.exceptions import ValidationError

    if not getattr(django_settings, 'ENABLE_OPENDQV_VALIDATION', False):
        return
    if self.status != 'active':
        return

    try:
        from opendqv.sdk.local import LocalValidator
    except ImportError:
        return  # opendqv not installed — skip silently

    validator = LocalValidator()
    record = {
        "dish_name": self.name or "",
        "allergen_review_confirmed": str(self.allergen_review_confirmed).lower(),
        "allergen_reviewed_by": self.allergen_reviewed_by or "",
        "allergen_review_date": (
            self.allergen_review_date.isoformat()
            if self.allergen_review_date else ""
        ),
    }
    result = validator.validate(record, contract="allereasy_dish")
    if not result["valid"]:
        errors = "; ".join(
            f"{e['field']}: {e['message']}" for e in result.get("errors", [])
        )
        raise ValidationError(f"Allergen compliance check failed: {errors}")
```

---

## Contract placement

`LocalValidator()` with no arguments looks for contracts in `./contracts/` relative
to the working directory (or `OPENDQV_CONTRACTS_DIR` env var). The `allereasy_dish.yaml`
contract is included in the AllerEasy PR at `contracts/allereasy_dish.yaml` — no
extra configuration needed when running from AllerEasy's root.

The same contract also ships with `pip install opendqv` in
`contracts/allereasy_dish.yaml`.

---

## OpenDQV vs qsr_menu_item

OpenDQV ships two Natasha's Law contracts. They address different integration points:

| Contract | Use case | Integration pattern |
|----------|----------|---------------------|
| `qsr_menu_item` | POS systems, custom apps, REST API validation | 14 boolean allergen fields, `POST /api/v1/validate` |
| `allereasy_dish` | AllerEasy Django app | Audit trail fields, `LocalValidator` in `Dish.clean()` |

If you are integrating OpenDQV directly with a POS or custom recipe management system
(not AllerEasy), use `qsr_menu_item`. See
[docs/integrations/natasha-law-compliance.md](natasha-law-compliance.md).

---

## Related resources

- AllerEasy: [allereasy.co.uk](https://www.allereasy.co.uk/)
- AllerEasy GitHub: [github.com/rhcompbuilds/Allereasy](https://github.com/rhcompbuilds/Allereasy)
- Integration PR: [github.com/rhcompbuilds/Allereasy/pull/1](https://github.com/rhcompbuilds/Allereasy/pull/1)
- Contract: `contracts/allereasy_dish.yaml`
- Contract: `contracts/qsr_menu_item.yaml`
- Natasha's Law guide: [docs/integrations/natasha-law-compliance.md](natasha-law-compliance.md)
- UK legislation: [food.gov.uk/business-guidance/natashas-law](https://www.food.gov.uk/business-guidance/introduction-to-allergen-labelling-changes-ppds)
