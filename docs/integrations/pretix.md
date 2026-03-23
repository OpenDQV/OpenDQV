# Pretix Integration — Martyn's Law Event Compliance Enforcement

> **Pretix:** [pretix.eu](https://pretix.eu) — open-source (AGPL v3), Django-based
> event ticketing platform deployed at thousands of conferences, festivals, and public
> events worldwide. Built for complete self-hosting with strong privacy defaults.

---

## The gap this integration closes

Pretix's `Event` model stores location and ticketing data but has no venue capacity
field and no emergency procedure data model:

```python
# pretix/base/models/event.py (simplified)
class Event(LoggedModel):
    name = I18nCharField(max_length=200)
    slug = models.SlugField()
    location = I18nTextField(blank=True)
    # ... quotas, dates, currency, plugins
    # ← no expected_attendance
    # ← no evacuation_procedure_documented
    # ← no staff_briefing_completed
    # ← no compliance_reviewed_by
```

This means a Pretix event can be created and published with no record of how many
people are expected, whether emergency procedures exist, or whether any Martyn's Law
compliance review has ever taken place. That is the structural gap this integration
closes.

**Martyn's Law** (Terrorism (Protection of Premises) Act 2025) applies to any event
where 200 or more persons are expected to attend. The obligation is on the event
organiser — not the venue — to ensure documented evacuation, invacuation, and lockdown
procedures are in place and that all staff are briefed before the event opens.

OpenDQV closes it with a compliance record enforced at the point of write.

---

## What this integration adds

### Seven new fields on `Event` (standard duty — all qualifying events)

| Field | Type | Purpose |
|-------|------|---------|
| `expected_attendance` | `PositiveIntegerField` | Declared total expected attendance — triggers Martyn's Law scope (200+) |
| `duty_tier` | `CharField(choices)` | `standard` (200–799) or `enhanced` (800+) — explicitly declared |
| `evacuation_procedure_documented` | `BooleanField` | Evacuation procedure documented for this event site |
| `invacuation_procedure_documented` | `BooleanField` | Shelter-in-place procedure documented |
| `lockdown_procedure_documented` | `BooleanField` | Lockdown procedure documented |
| `staff_briefing_completed` | `BooleanField` | All staff and volunteers briefed before event opens |
| `staff_briefing_date` | `DateField(null=True)` | Date the pre-event briefing was completed |

### Five additional fields on `Event` (enhanced duty — 800+ attendance only)

| Field | Type | Purpose |
|-------|------|---------|
| `senior_responsible_person` | `CharField(null=True)` | Named individual with personal accountability |
| `senior_responsible_person_role` | `CharField(null=True)` | Their organisational role |
| `sia_notification_reference` | `CharField(null=True)` | SIA notification reference (events notify, not register) |
| `terrorism_protection_plan_documented` | `BooleanField(null=True)` | Formal Terrorism Protection Plan prepared |
| `terrorism_protection_plan_review_date` | `DateField(null=True)` | Date the plan was last reviewed |

### Two audit trail fields

| Field | Type | Purpose |
|-------|------|---------|
| `compliance_reviewed_by` | `CharField(null=True)` | Name of the person who reviewed the compliance record |
| `compliance_review_date` | `DateField(null=True)` | Date the compliance review was completed |

### Django signal enforcement

When `ENABLE_OPENDQV_VALIDATION=true`, a `pre_save` signal validates the compliance
fields via OpenDQV's `LocalValidator` before any qualifying event is saved. A missing
or invalid field raises `ValidationError` before the record reaches the database.

Events below 200 expected attendance are not affected — no compliance obligations apply.

### Zero breaking changes

- All new fields are `null=True` / `blank=True` — existing events are not affected
- Validation is off by default — set `ENABLE_OPENDQV_VALIDATION=true` to enable
- `opendqv` not installed? The signal skips silently

---

## Setup

### 1. Get the code

Apply the integration patch to your Pretix installation:

```bash
# From your pretix source directory
git apply pretix-opendqv-martyns-law.patch
```

Or cherry-pick from the integration branch (see Related Resources below).

### 2. Install opendqv

```bash
pip install opendqv>=1.4.0
```

### 3. Run the migration

```bash
python -m pretix migrate
```

### 4. Enable validation

```bash
export ENABLE_OPENDQV_VALIDATION=true
```

Or in your `pretix.cfg` / environment:

```ini
[opendqv]
enabled = true
```

### 5. Verify

Create a new event in the Pretix control panel with an `expected_attendance` of 300
and leave `evacuation_procedure_documented` unchecked. The save should be blocked with:

```
Martyn's Law compliance check failed:
  evacuation_procedure_documented: must be declared (true or false) — a documented
    evacuation procedure is required for all qualifying events under Martyn's Law
  invacuation_procedure_documented: must be declared (true or false)
  lockdown_procedure_documented: must be declared (true or false)
  staff_briefing_completed: must be declared (true or false)
  compliance_reviewed_by: is required
  compliance_review_date: is required
```

---

## How it works

The `pretix_event` contract (`contracts/pretix_event.yaml`) defines what "valid"
means for a qualifying event compliance record:

```yaml
rules:
  - name: expected_attendance_minimum
    field: expected_attendance
    type: min
    min: 200
    error_message: "expected_attendance must be >= 200 — Martyn's Law applies only
      to qualifying events with 200 or more persons expected to attend"

  - name: evacuation_procedure_documented_required
    field: evacuation_procedure_documented
    type: not_empty
    error_message: "evacuation_procedure_documented must be declared ..."

  - name: senior_responsible_person_required_if_enhanced
    field: senior_responsible_person
    type: required_if
    required_if:
      field: duty_tier
      value: "enhanced"
    error_message: "senior_responsible_person is required for enhanced-duty events ..."
```

A Django `pre_save` signal serialises the compliance fields into a flat dict and
passes them to `LocalValidator`:

```python
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError

from pretix.base.models import Event

@receiver(pre_save, sender=Event)
def validate_martyns_law_compliance(sender, instance, **kwargs):
    import os
    if not os.environ.get("ENABLE_OPENDQV_VALIDATION"):
        return

    expected = instance.expected_attendance
    if expected is None or expected < 200:
        return  # not a qualifying event

    try:
        from opendqv.sdk.local import LocalValidator
    except ImportError:
        return  # opendqv not installed — skip silently

    record = {
        "event_name": str(instance.name),
        "event_slug": instance.slug or "",
        "expected_attendance": expected,
        "duty_tier": instance.duty_tier or "",
        "evacuation_procedure_documented": str(instance.evacuation_procedure_documented).lower(),
        "invacuation_procedure_documented": str(instance.invacuation_procedure_documented).lower(),
        "lockdown_procedure_documented": str(instance.lockdown_procedure_documented).lower(),
        "staff_briefing_completed": str(instance.staff_briefing_completed).lower(),
        "staff_briefing_date": (
            instance.staff_briefing_date.isoformat()
            if instance.staff_briefing_date else ""
        ),
        "compliance_reviewed_by": instance.compliance_reviewed_by or "",
        "compliance_review_date": (
            instance.compliance_review_date.isoformat()
            if instance.compliance_review_date else ""
        ),
    }

    # Enhanced duty fields (only validate if enhanced tier)
    if instance.duty_tier == "enhanced":
        record.update({
            "senior_responsible_person": instance.senior_responsible_person or "",
            "senior_responsible_person_role": instance.senior_responsible_person_role or "",
            "sia_notification_reference": instance.sia_notification_reference or "",
            "terrorism_protection_plan_documented": str(
                instance.terrorism_protection_plan_documented
            ).lower() if instance.terrorism_protection_plan_documented is not None else "",
            "terrorism_protection_plan_review_date": (
                instance.terrorism_protection_plan_review_date.isoformat()
                if instance.terrorism_protection_plan_review_date else ""
            ),
        })

    validator = LocalValidator()
    result = validator.validate(record, contract="pretix_event")
    if not result["valid"]:
        errors = "; ".join(
            f"{e['field']}: {e['message']}" for e in result.get("errors", [])
        )
        raise ValidationError(f"Martyn's Law compliance check failed: {errors}")
```

---

## Contract placement

`LocalValidator()` with no arguments looks for contracts in `./contracts/` relative
to the working directory (or `OPENDQV_CONTRACTS_DIR` env var). The `pretix_event.yaml`
contract is included in the integration patch at `contracts/pretix_event.yaml` — no
extra configuration needed when running from Pretix's root.

The same contract also ships with `pip install opendqv` at `contracts/pretix_event.yaml`.

---

## OpenDQV vs martyns_law_event

OpenDQV ships two Martyn's Law event-focused contracts. They address different
integration points:

| Contract | Use case | Integration pattern |
|----------|----------|---------------------|
| `martyns_law_event` | Custom event management apps, REST API validation | Full event record with organiser and type fields, `POST /api/v1/validate` |
| `pretix_event` | Pretix ticketing platform | Compliance audit trail fields added to Pretix's Event model, `LocalValidator` in `pre_save` signal |

If you are integrating OpenDQV directly with a custom event management system (not
Pretix), use `martyns_law_event`. See
[docs/integrations/martyns-law-compliance.md](martyns-law-compliance.md).

---

## Why Martyn's Law matters for event ticketing platforms

Pretix is used at concerts, festivals, conferences, and community events — many of
which will cross the 200-person threshold that triggers Martyn's Law obligations.
The platform currently has no mechanism to:

- Record that a capacity assessment has been performed
- Enforce that emergency procedures exist before an event goes live
- Create a timestamped compliance audit trail for SIA inspection

The omission failure mode Martyn's Law was created to prevent — "we didn't fill that
field in" — is structurally present in the current Pretix data model. This integration
closes that gap at the point of write, before the event record enters the database.

Named after Martyn Hett (1987–2017), killed in the Manchester Arena bombing on
22 May 2017.

---

## Related resources

- Pretix: [pretix.eu](https://pretix.eu)
- Pretix GitHub: [github.com/pretix/pretix](https://github.com/pretix/pretix)
- Contract: `contracts/pretix_event.yaml`
- Contract: `contracts/martyns_law_event.yaml`
- Martyn's Law guide: [docs/integrations/martyns-law-compliance.md](martyns-law-compliance.md)
- UK legislation: [legislation.gov.uk — Terrorism (Protection of Premises) Act 2025](https://www.legislation.gov.uk/ukpga/2025/14)
- SIA: [sia.homeoffice.gov.uk](https://www.sia.homeoffice.gov.uk)
