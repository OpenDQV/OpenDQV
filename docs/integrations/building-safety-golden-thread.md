# Building Safety Act 2022 — Golden Thread Compliance

> **Last reviewed:** 2026-03-21.

The Building Safety Act 2022 requires higher-risk buildings to maintain a
**golden thread** of accurate, up-to-date building information throughout the
building lifecycle. The law's own phrase is "accurate and up-to-date information"
— it is explicitly a data quality obligation, not just a procedural one.

OpenDQV enforces this obligation at the point of write — before a golden thread
record reaches a building management system, property management platform, or
asset register — making incomplete or inaccurate records structurally impossible
rather than discovered at inspection time.

---

## What is a higher-risk building

A building is higher-risk under the Act if it is:

- **18 metres or more in height**, OR
- **7 or more storeys above ground**

AND is one of:
- Residential (containing at least 2 dwellings)
- A hospital
- A care home

Buildings that meet the height or storeys criterion but are purely commercial
are not in scope. Mixed-use buildings containing any residential component are
in scope.

---

## What is the golden thread

The golden thread is the information that allows someone to understand a building
and keep it safe — not just at the time of construction but throughout its entire
life. It includes:

- Who is responsible for the building (Accountable Person, Building Safety Manager)
- The physical facts of the building (height, storeys, layout, construction)
- The Safety Case — the structured argument that the building is safe to occupy
- Fire and emergency information
- How residents are engaged on safety matters

The Act requires this information to be **accurate, up-to-date, and accessible**.
OpenDQV enforces the "accurate and up-to-date" part at the point of write — a
record that is missing a required field cannot enter the system.

---

## Key roles under the Act

| Role | Obligation |
|------|------------|
| **Accountable Person (AP)** | The person or body with legal control of or responsibility for the common parts. Must be registered with BSR, maintain the Safety Case, and engage residents. |
| **Principal Accountable Person (PAP)** | Where there are multiple APs, one must be designated PAP — the primary contact with BSR. |
| **Building Safety Manager (BSM)** | Appointed by the AP to manage day-to-day building safety. Must be a competent individual. |

---

## How OpenDQV enforces the golden thread

```
Building manager submits golden thread record
              │
              ▼
POST /api/v1/validate/building_safety_golden_thread
              │
              ├── building_id / name / address missing?     → 422 (not_empty)
              ├── height_metres missing?                     → 422 (not_empty)
              ├── storeys_above_ground missing?              → 422 (not_empty)
              ├── accountable_person_name missing?           → 422 (not_empty)
              ├── building_safety_manager_name missing?      → 422 (not_empty)
              ├── bsr_registration_number missing?           → 422 (not_empty)
              ├── safety_case_documented missing?            → 422 (not_empty)
              ├── safety_case_documented = true
              │   but safety_case_report_date missing?       → 422 (required_if)
              ├── fire_and_emergency_file missing?           → 422 (not_empty)
              ├── residents_engagement_strategy missing?     → 422 (not_empty)
              └── all fields present?                        → valid: true
                          │
                          ▼
                   Record saved to building management system
```

---

## BSR registration deadline

All higher-risk buildings occupied before 6 April 2023 were required to register
with the Building Safety Regulator by **1 October 2023**. New higher-risk buildings
must be registered before occupation. The `bsr_registration_number` field is
mandatory — a record without it cannot enter the system.

---

## Contract rules summary

| Pattern | Fields |
|---------|--------|
| `not_empty` | `building_id`, `building_name`, `building_address`, `height_metres`, `storeys_above_ground`, `primary_use`, all accountable person fields, BSR fields, boolean flags, audit trail |
| `lookup` against `building_safety_primary_uses.txt` | `primary_use` |
| `lookup` against `allergen_boolean.txt` | `safety_case_documented`, `fire_and_emergency_file_maintained`, `residents_engagement_strategy_documented` |
| `required_if safety_case_documented = true` | `safety_case_report_date` |
| `date_format` | `bsr_registration_date`, `safety_case_report_date`, `golden_thread_last_updated` |

---

## Building management system integration

Most higher-risk building operators record compliance data in one of:

- A **building management system** (Planon, FSI, Maintenance Manager)
- A **property management platform** (Yardi, MRI Software, Qube)
- A **facilities management tool** (Concept Evolution, Archibus)

The integration point is wherever a golden thread record is created or updated
during the building lifecycle — at registration, at planned review, or when
responsible persons change.

### REST API example (Python)

```python
import os
import httpx

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

def validate_golden_thread(record: dict) -> dict:
    """Validate a golden thread record against the Building Safety Act contract."""
    resp = httpx.post(
        f"{OPENDQV_URL}/api/v1/validate/building_safety_golden_thread",
        json=record,
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
        timeout=5.0,
    )
    result = resp.json()
    if not result.get("valid"):
        errors = [e["message"] for e in result.get("errors", [])]
        raise ValueError(f"Golden thread validation failed: {'; '.join(errors)}")
    return result


record = {
    "building_id": "BSA-LDN-00042",
    "building_name": "Grenfell Point",
    "building_address": "Lancaster West Estate, London, W11 1AX",
    "height_metres": 67.3,
    "storeys_above_ground": 24,
    "primary_use": "residential",
    "accountable_person_name": "Judith Clarke",
    "accountable_person_organisation": "Royal Borough of Kensington and Chelsea",
    "accountable_person_contact": "j.clarke@rbkc.gov.uk",
    "building_safety_manager_name": "Thomas Reid",
    "building_safety_manager_contact": "t.reid@rbkc.gov.uk",
    "bsr_registration_number": "BSR-2023-LDN-00412",
    "bsr_registration_date": "2023-09-28",
    "safety_case_documented": "true",
    "safety_case_report_date": "2024-03-15",
    "fire_and_emergency_file_maintained": "true",
    "residents_engagement_strategy_documented": "true",
    "golden_thread_maintained_by": "Thomas Reid (Building Safety Manager)",
    "golden_thread_last_updated": "2026-03-21",
}

validate_golden_thread(record)
```

---

## Why height and storeys are both required

Either criterion alone qualifies a building as higher-risk: 18m+ height OR
7+ storeys. The contract requires both fields to be present (not_empty) but
does not enforce a minimum value on either — because the operator applies
this contract to buildings they know are higher-risk. Recording both the
height and the storey count is part of the golden thread obligation regardless
of which criterion was met.

---

## Limitations

OpenDQV validates the **declaration**, not the **adequacy** of the Safety Case
or the competence of the Building Safety Manager. The contract enforces that
a record is complete before it is saved. It cannot verify that:

- The Safety Case meets BSR standards
- The Accountable Person has the legal authority the Act requires
- The BSR registration number is valid
- The fire and emergency file actually exists and is current

Those verifications require BSR inspection or a human review step.

---

## Related resources

- Contract: `contracts/building_safety_golden_thread.yaml`
- Starter contract: `examples/building_safety/building_safety_golden_thread.yaml`
- Sample records: `examples/building_safety/`
- Reference files: `contracts/ref/building_safety_primary_uses.txt`
- UK legislation: [legislation.gov.uk — Building Safety Act 2022](https://www.legislation.gov.uk/ukpga/2022/30)
- BSR: [hse.gov.uk/building-safety](https://www.hse.gov.uk/building-safety/)
