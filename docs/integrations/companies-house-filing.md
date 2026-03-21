# Economic Crime and Corporate Transparency Act 2023 — Companies House Filing

> **Last reviewed:** 2026-03-21.

The Economic Crime and Corporate Transparency Act 2023 requires **identity
verification** for all individuals registering at Companies House — directors,
company secretaries, and Persons with Significant Control (PSCs). Identity
verification began rolling out from 2024 and is required for all new and existing
registrations.

OpenDQV enforces this obligation at the point of write — before a filing record
is submitted to Companies House, a filing system, or an ACSP — making the
omission of identity verification structurally impossible rather than discovered
at submission time or investigation time.

---

## Who is in scope

Anyone registering at Companies House as:

- A **director** of a UK company
- A **company secretary**
- A **Person with Significant Control (PSC)** — someone who owns or controls more
  than 25% of shares or voting rights, or has significant influence or control
- An **LLP member** or **designated LLP member**
- Any other officer registerable at Companies House

The Act applies to both new registrations and the re-verification of existing
registered individuals.

---

## The omission failure mode

Before the Act, individuals could be registered at Companies House without their
identity ever being verified. The Act closes this gap. The omission failure mode
OpenDQV prevents: a filing record created with `id_verification_completed` missing
or unrecorded — meaning the individual's identity has not been verified before
the record enters the system.

```
Filing system creates director/PSC record
              │
              ▼
POST /api/v1/validate/companies_house_filing
              │
              ├── individual_full_name / role missing?       → 422 (not_empty)
              ├── date_of_birth missing or invalid?          → 422 (not_empty / date_format)
              ├── id_verification_method missing?            → 422 (not_empty)
              ├── id_verification_date missing?              → 422 (not_empty)
              ├── id_verified_by missing?                    → 422 (not_empty)
              └── all fields present?                        → valid: true
                          │
                          ▼
                   Record submitted to Companies House
```

---

## Verification routes

The Act provides three routes to identity verification:

| Route | How it works |
|-------|-------------|
| **Companies House direct** | Via GOV.UK One Login — the individual verifies their own identity online |
| **Authorised Corporate Service Provider (ACSP)** | A regulated firm (solicitor, accountant, company formation agent) that has completed anti-money-laundering checks on the individual |
| **Digital identity service** | A service certified under the UK Digital Identity and Attributes Trust Framework (DIATF) |

The `id_verification_method` field records which route was used.

---

## Contract rules summary

| Pattern | Fields |
|---------|--------|
| `not_empty` | `company_number`, `company_name`, `individual_full_name`, `individual_role`, `date_of_birth`, `nationality`, `country_of_residence`, `id_verification_method`, `id_verification_date`, `id_verified_by`, audit trail |
| `lookup` against `companies_house_roles.txt` | `individual_role` |
| `lookup` against `companies_house_id_verification_methods.txt` | `id_verification_method` |
| `date_format` | `date_of_birth`, `id_verification_date`, `filing_date` |

---

## Filing system integration

Most organisations manage Companies House filings through:

- A **company secretarial platform** (Diligent Entities, Workday, Sage Company Secretarial)
- An **ACSP practice management system** (Actionstep, LEAP, Clio)
- A **back-office or ERP system** (Workday, SAP)

The integration point is wherever a new director or PSC record is written — at
appointment, at re-registration, or when the annual verification confirmation
is recorded.

### REST API example (Python)

```python
import os
import httpx

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

def validate_filing(record: dict) -> dict:
    """Validate a Companies House filing record before submission."""
    resp = httpx.post(
        f"{OPENDQV_URL}/api/v1/validate/companies_house_filing",
        json=record,
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
        timeout=5.0,
    )
    result = resp.json()
    if not result.get("valid"):
        errors = [e["message"] for e in result.get("errors", [])]
        raise ValueError(f"Companies House filing validation failed: {'; '.join(errors)}")
    return result


# Director appointment record
director_record = {
    "company_number": "12345678",
    "company_name": "Acme Technologies Ltd",
    "individual_full_name": "Priya Sharma",
    "individual_role": "director",
    "date_of_birth": "1985-04-12",
    "nationality": "British",
    "country_of_residence": "England",
    "id_verification_completed": "true",
    "id_verification_method": "uk_passport",
    "id_verification_date": "2026-01-10",
    "id_verified_by": "Companies House (GOV.UK One Login)",
    "filing_prepared_by": "Jane Watkins (Company Secretary)",
    "filing_date": "2026-03-21",
}

validate_filing(director_record)
```

---

## PSC filings

The contract handles PSC records identically to director records — `individual_role`
is set to `"person_with_significant_control"` and the same identity verification
fields apply. ACSPs verifying PSC identity under their anti-money-laundering
obligations can record themselves as `id_verified_by`.

---

## Limitations

OpenDQV validates the **declaration** of identity verification, not the outcome
of the verification itself. The contract enforces that:

- A verification was completed (`id_verification_completed = true`)
- The method is recorded
- The date and verifier are recorded

It cannot verify that the identity check was correctly performed, that the document
used was genuine, or that the individual's details match the Companies House record.
Those verifications are performed by Companies House, the ACSP, or the digital
identity service. The audit trail (`id_verified_by` + `id_verification_date`) creates
an accountability anchor.

---

## Related resources

- Contract: `contracts/companies_house_filing.yaml`
- Starter contract: `examples/companies_house/companies_house_filing.yaml`
- Sample records: `examples/companies_house/`
- Reference files: `contracts/ref/companies_house_roles.txt`,
  `contracts/ref/companies_house_id_verification_methods.txt`
- UK legislation: [legislation.gov.uk — Economic Crime and Corporate Transparency Act 2023](https://www.legislation.gov.uk/ukpga/2023/56)
- Companies House identity verification: [companieshouse.gov.uk](https://www.gov.uk/guidance/companies-house-identity-verification)
