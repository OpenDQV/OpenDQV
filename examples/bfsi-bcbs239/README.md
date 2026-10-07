# BFSI Customer Data Quality Contract

A production-ready OpenDQV data contract for UK regulated financial services.

## What this covers

`bfsi_customer.yaml` enforces demographic plausibility and identity data quality rules
that are required or strongly implied by:

| Regulation | Obligation | Rules enforced |
|------------|-----------|----------------|
| **BCBS 239** Principle 3 | Data accuracy and integrity — risk data must be accurate, complete, and reliable | DOB plausibility, sentinel value detection, format validation |
| **FCA Consumer Duty** (PS22/9) | Firms must understand customer information to deliver appropriate outcomes | DOB max_age (120 years), NI number format, postcode validation |
| **AML / MLR 2017 / CDD** | Customer Due Diligence records must be accurate; sentinel dates indicate incomplete CDD | `dob_plausible_year` rule blocks 1900-01-01 and similar placeholder dates |

## The sentinel value problem

Large financial institutions have discovered tens of thousands of active customer accounts
with dates of birth implying ages of 110+ years. These are **sentinel/placeholder dates**
(`1900-01-01`, `1800-01-01`, `0001-01-01`) inserted during legacy system migrations when
the true date of birth was unknown, and never remediated.

Consequences of undetected sentinel dates:
- **KYC failures**: Age-gated product checks pass incorrectly
- **Sanctions screening misses**: Customer identity cannot be verified against watchlists
- **AML lookback exposure**: CDD records are provably incomplete — a regulatory finding
- **Mis-selling liability**: Age-inappropriate products sold to customers with incorrect DOB

The `dob_plausible_year` rule in this contract explicitly rejects any date of birth outside
the range 1905–2099, flagging the record for CDD remediation before it enters any risk or
compliance system. The upper bound covers the full 21st century and does not require
annual maintenance — age enforcement is handled by the `dob_max_age` rule using calendar
arithmetic.

## Rule inventory

| Rule | Field | Regulatory anchor |
|------|-------|-------------------|
| `account_number_required` | account_number | BCBS 239 — record completeness |
| `full_name_required` | full_name | CDD — identity verification |
| `valid_email` | email | CDD — contact data integrity |
| `valid_phone` | phone | CDD — E.164 international format |
| `dob_not_empty` | date_of_birth | BCBS 239 — mandatory field |
| `dob_format` | date_of_birth | BCBS 239 — format standardisation |
| `dob_min_age` | date_of_birth | Plausibility — no future birth dates |
| `dob_max_age` | date_of_birth | Plausibility — max age 120 years |
| `dob_plausible_year` | date_of_birth | **AML/CDD** — sentinel date rejection |
| `valid_uk_postcode` | postcode | CDD — address verification |
| `valid_ni_number` | ni_number | HMRC format — identity document validation |

## Variants

One contract per use (OpenDQV 3.0.0 removed the `contexts:` block these used to live in —
see [docs/contexts.md](../../docs/contexts.md)):

- **`bfsi_customer_retail_kyc.yaml`** (`bfsi_customer_retail_kyc`): all failures block onboarding
  (severity: error), with KYC-specific messages on the DOB plausibility, NI number and postcode rules.
  Use in the live KYC pipeline.
- **`bfsi_customer_internal_review.yaml`** (`bfsi_customer_internal_review`): date of birth, NI number
  and postcode failures raise warnings only (account number, name, email and phone stay errors).
  Use for data quality monitoring and AML lookback triage dashboards.

Each variant has its own version history and audit lineage. Select one by contract name; the
optional `context` field on a validate request is only a tag for stats, the audit event and
metrics, and never changes which rules run.

## Quick start

```bash
# Copy to your contracts directory (the variants too, if you use them)
cp bfsi_customer*.yaml /path/to/your/contracts/

# Validate a record
curl -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "contract": "bfsi_customer_retail_kyc",
    "record": {
      "account_number": "GB123456",
      "full_name": "Jane Smith",
      "email": "jane@example.com",
      "phone": "+447911123456",
      "date_of_birth": "1900-01-01",
      "postcode": "SW1A 1AA",
      "ni_number": "AB123456C"
    }
  }'
```

Expected response: `valid: false` — the `dob_plausible_year` rule rejects the 1900-01-01 sentinel date.

## Extending this contract

Add additional rules for your institution's specific requirements:

```yaml
# Example: DORA Article 10 — operational event data field
- name: event_timestamp_required
  type: not_empty
  field: event_timestamp
  severity: error
  error_message: "Operational event timestamp required for DORA incident reporting"
```

See the [OpenDQV rule reference](../../README.md#rule-types) for all available rule types.
