# Martyn's Law — Venue Terrorism Preparedness Compliance

> **Last reviewed:** 2026-03-21.

Martyn's Law (the Terrorism (Protection of Premises) Act 2025) received Royal Assent
on 3 April 2025. It requires venues and events above a capacity threshold to have
documented emergency procedures, trained staff, and — at the enhanced tier — a named
Senior Responsible Person, a formal Terrorism Protection Plan, and registration with
the Security Industry Authority (SIA).

OpenDQV enforces these obligations at the point of write — before a compliance record
reaches a safety management system, property management platform, or back-office tool —
making omission structurally impossible rather than relying on human review.

---

## Named after Martyn Hett

The law is named after **Martyn Hett** (22 November 1987 – 22 May 2017), one of 22
people killed in the Manchester Arena attack during an Ariana Grande concert. He was
29 years old. His mother, **Figen Murray**, campaigned for eight years to close the
preparedness gap the attack exposed. The Act received Royal Assent on 3 April 2025.

The parallel with Natasha's Law is exact: both are UK named-victim laws, both address
an **omission failure mode** (no allergen label / no evacuation plan), and both create
a clear point-of-write data obligation. OpenDQV's contract for each law uses the same
structural enforcement pattern — a missing field triggers a 422 before the record
enters the system.

---

## Who is in scope

Any venue or qualifying event with a **capacity of 200 or more persons**:

- Pubs, bars, restaurants (200–799: standard duty)
- Theatres, cinemas, museums, galleries (200–799: standard duty)
- Places of worship (200–799: standard duty)
- Nightclubs, hotels, conference centres
- Shopping centres, sports stadia, arenas (800+: enhanced duty)
- Temporary or qualifying events (e.g. festivals) where 800+ attend

If your premises or event can hold 200 or more people, you are in scope.

---

## The two-tier system

| Tier | Capacity threshold | Obligations |
|------|--------------------|-------------|
| **Standard duty** | 200–799 persons | Document evacuation, invacuation, and lockdown procedures; train all staff |
| **Enhanced duty** | 800+ persons | Standard duty PLUS: named Senior Responsible Person, Terrorism Protection Plan, SIA registration, formal audit |

### Standard duty obligations

All venues in scope must:

1. **Document an evacuation procedure** — how people leave the building safely
2. **Document an invacuation procedure** — shelter-in-place protocol (where evacuation
   is not possible or would increase risk)
3. **Document a lockdown procedure** — securing the premises during an active threat
4. **Train all staff** on those procedures, and record the date training was completed

### Enhanced duty obligations (in addition to standard)

Enhanced-duty venues must also:

1. **Appoint a Senior Responsible Person (SRP)** — a named senior individual who takes
   personal accountability for terrorism preparedness
2. **Create and maintain a Terrorism Protection Plan (TTP)** — a formal documented
   plan more comprehensive than the standard procedures
3. **Register with and notify the SIA** — the Security Industry Authority is the
   statutory regulator under the Act
4. **Meet more formal training and audit requirements**

---

## How OpenDQV enforces Martyn's Law

The enforcement model is structural, not advisory. The operator declares their
`duty_tier`; the contract enforces tier-conditional completeness:

```
Operator submits venue compliance record
              │
              ▼
POST /api/v1/validate/martyns_law_venue
              │
              ├── capacity < 200?                    → 422 (min)
              ├── duty_tier missing?                  → 422 (not_empty)
              ├── evacuation_procedure missing?        → 422 (not_empty)
              ├── invacuation_procedure missing?       → 422 (not_empty)
              ├── lockdown_procedure missing?          → 422 (not_empty)
              ├── staff_training_completed = true
              │   but staff_training_date missing?    → 422 (required_if)
              │
              │   [enhanced duty only]
              ├── senior_responsible_person missing?  → 422 (required_if)
              ├── sia_registration_number missing?    → 422 (required_if)
              ├── terrorism_protection_plan missing?  → 422 (required_if)
              └── all required fields present?        → valid: true
                          │
                          ▼
                   Record saved to safety system
```

This is the earliest possible intervention — the operator fixes the omission before
the record is saved, before the venue opens, before an inspection occurs. The failure
mode Martyn's Law was designed to prevent was **omission by oversight**: "we didn't
fill that in." OpenDQV prevents omission structurally.

---

## Why the operator declares the tier (not auto-derived from capacity)

The `duty_tier` field is explicitly declared by the operator rather than computed from
`capacity`. This is a deliberate design choice: OpenDQV's `condition` blocks support
exact string matching, not numeric range comparisons. The operator knows their tier;
the contract enforces tier-conditional completeness.

This also reflects how the law works in practice — a venue operator will know whether
they are standard or enhanced duty through their SIA registration process, not by
reading their capacity from a database field.

```yaml
# The operator declares:
"duty_tier": "enhanced"

# The contract then enforces:
- senior_responsible_person    (required_if duty_tier = enhanced)
- sia_registration_number      (required_if duty_tier = enhanced)
- terrorism_protection_plan_documented  (required_if duty_tier = enhanced)
```

---

## Implementation timeline — preparation window

The Act has received Royal Assent but **commencement orders** (which set the go-live
dates for each duty tier) are still being made by the Secretary of State. Based on
precedent, full implementation is expected approximately 18–24 months after Royal
Assent — placing it in the 2026–2027 window.

This gives venue operators a preparation window to build compliant records and
processes before the statutory deadline — exactly when they should be adopting this
contract. A 422 at write-time during the preparation window trains systems and
operators to declare every required field before the law comes into force.

---

## Contract rules summary

| Pattern | Fields |
|---------|--------|
| `not_empty` | `venue_id`, `venue_name`, `venue_address`, `capacity`, `venue_type`, `duty_tier`, all four standard-duty boolean fields, audit trail |
| `min: 200` | `capacity` — below threshold = out of scope |
| `lookup` against `martyns_law_venue_types.txt` | `venue_type` |
| `lookup` against `martyns_law_duty_tiers.txt` | `duty_tier` |
| `lookup` against `allergen_boolean.txt` | All `*_documented` and `*_completed` boolean fields |
| `required_if staff_training_completed = true` | `staff_training_date` |
| `required_if duty_tier = enhanced` | `senior_responsible_person`, `senior_responsible_person_role`, `sia_registration_number`, `terrorism_protection_plan_documented` |
| `required_if terrorism_protection_plan_documented = true` | `terrorism_protection_plan_review_date` |
| `date_format` | `staff_training_date`, `terrorism_protection_plan_review_date`, `compliance_review_date` |

---

## Venue safety management system integration

Most venue operators record compliance status in one of:

- A **venue safety management system** (Alcumus, Smartlog, Safeguard)
- A **property management platform** (Yardi, MRI Software, Planon)
- A **back-office spreadsheet or ERP**

The integration point is wherever a new venue compliance record is written or a
recertification is saved. Validate at that write event — not at inspection time.

### REST API example (Python)

```python
import os
import httpx

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

def validate_venue_compliance(record: dict) -> dict:
    """Validate a venue compliance record against the Martyn's Law contract.
    Raises ValueError if the record fails validation.
    """
    resp = httpx.post(
        f"{OPENDQV_URL}/api/v1/validate/martyns_law_venue",
        json=record,
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
        timeout=5.0,
    )
    result = resp.json()
    if not result.get("valid"):
        errors = [e["message"] for e in result.get("errors", [])]
        raise ValueError(f"Martyn's Law compliance validation failed: {'; '.join(errors)}")
    return result


# Example: enhanced-duty arena submitting a compliance record
arena_record = {
    "venue_id": "VEN-MCR-001",
    "venue_name": "Manchester Arena",
    "venue_address": "Victoria Station Approach, Manchester, M3 1AR",
    "capacity": 21000,
    "venue_type": "arena",
    "duty_tier": "enhanced",
    "evacuation_procedure_documented": "true",
    "invacuation_procedure_documented": "true",
    "lockdown_procedure_documented": "true",
    "staff_training_completed": "true",
    "staff_training_date": "2026-01-15",
    "senior_responsible_person": "David Clarke",
    "senior_responsible_person_role": "Head of Security",
    "sia_registration_number": "SIA-2026-MCR-00142",
    "terrorism_protection_plan_documented": "true",
    "terrorism_protection_plan_review_date": "2026-02-01",
    "compliance_reviewed_by": "Sarah Jennings (Safety Manager)",
    "compliance_review_date": "2026-03-21",
}

validate_venue_compliance(arena_record)  # raises on failure, returns result on success
```

### SDK example

```python
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
result = client.validate("martyns_law_venue", arena_record)

if not result.valid:
    for error in result.errors:
        print(f"  ✗ {error.field}: {error.message}")
```

---

## The audit trail

The contract includes two mandatory audit trail fields:

| Field | Purpose |
|-------|---------|
| `compliance_reviewed_by` | Name/role of the person who verified the compliance declarations |
| `compliance_review_date` | Date of the compliance review (YYYY-MM-DD) |

In the event of an SIA inspection or incident, the validated record provides a
timestamped, per-venue audit trail of who declared what and when.

---

## Scotland and Northern Ireland

**England and Wales:** The Terrorism (Protection of Premises) Act 2025 applies
directly.

**Scotland and Northern Ireland:** Equivalent legislation is expected. The Scottish
Government and Northern Ireland Executive are expected to introduce parallel regimes.
The `martyns_law_venue` contract is designed for UK-wide adoption — the same
two-tier model and field requirements are anticipated to apply across all four nations.

---

## Limitations

OpenDQV validates the **declaration**, not the **quality** of the actual plan.
The contract enforces that an operator has answered every required question before
saving the record. It cannot verify that:

- The documented evacuation procedure is adequate or tested
- The Terrorism Protection Plan meets SIA standards
- The Senior Responsible Person has the authority the Act requires
- The SIA registration number is valid

Those verifications require an SIA inspection or a human review step. The audit trail
(`compliance_reviewed_by` + `compliance_review_date`) creates an accountability
anchor — the named reviewer is asserting the accuracy of the declarations at the
time of review.

---

## Related resources

- Contract: `contracts/martyns_law_venue.yaml`
- Contract: `contracts/martyns_law_event.yaml`
- Starter contract: `examples/martyns_law/martyns_law_venue.yaml`
- Sample records: `examples/martyns_law/`, `examples/martyns_law_event/`
- Reference files: `contracts/ref/martyns_law_duty_tiers.txt`,
  `contracts/ref/martyns_law_venue_types.txt`
- UK legislation: [legislation.gov.uk — Terrorism (Protection of Premises) Act 2025](https://www.legislation.gov.uk/ukpga/2025/14)
- SIA: [sia.homeoffice.gov.uk](https://www.sia.homeoffice.gov.uk)
- Natasha's Law parallel: [docs/integrations/natasha-law-compliance.md](natasha-law-compliance.md)
