# /explain endpoint

**Endpoint:** `GET /api/v1/contracts/{name}/explain`
**Released:** v1.0.0

## Overview

The `/explain` endpoint returns a plain-English description of all validation rules in a contract. It is designed for compliance officers, data stewards, and auditors who need to understand what a contract does without reading YAML.

This is the primary tool for making the REVIEW lifecycle meaningful: a contract approver can read the `/explain` output to understand exactly what they are approving before signing off.

## Endpoint

```
GET /api/v1/contracts/{name}/explain?version=latest
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `name` | Yes | — | Contract name (path parameter) |
| `version` | No | `latest` | Contract version. Use `latest` for the current active version. |

**Auth:** Depends on deployment mode.
- `AUTH_MODE=open` (default for local dev): no token required.
- `AUTH_MODE=token` (production): a valid Bearer token is required **unless** `OPENDQV_EXPLAIN_PUBLIC=true` is set in the environment, which makes `/explain` publicly readable without a token.

Sensitive field values are suppressed regardless of auth mode.

## Response

```json
{
  "contract": "hr_employee_records",
  "version": "1.0",
  "description": "HR employee record validation for UK payroll",
  "owner": "People Data Team",
  "status": "active",
  "rule_count": 12,
  "rules": [
    {
      "name": "salary_range",
      "field": "salary",
      "summary": "salary must be between 0 and 10,000,000",
      "severity": "error",
      "sensitive": true
    },
    {
      "name": "ni_number_format",
      "field": "national_insurance_number",
      "summary": "national_insurance_number must match the pattern for a UK National Insurance number",
      "severity": "error",
      "sensitive": true
    },
    {
      "name": "employment_status_valid",
      "field": "employment_status",
      "summary": "employment_status must be one of: ACTIVE, SUSPENDED, TERMINATED",
      "severity": "error",
      "sensitive": false
    }
  ]
}
```

Each rule entry includes:
- `name` — the rule identifier
- `field` — the field being validated
- `summary` — a plain-English description of the constraint
- `severity` — `error` (blocks the record) or `warning` (flags but allows)
- `sensitive` — `true` if the field is listed in `sensitive_fields`

## sensitive_fields suppression

Fields listed in the contract's `sensitive_fields` header are handled as follows in `/explain` output:

- The field name is shown (so approvers know which fields are being validated).
- The `sensitive: true` flag is set so reviewers know the field is designated as sensitive.
- Pattern strings, regex expressions, and example values are **not** included for sensitive fields.
- Range bounds (min/max) for sensitive fields are included, as these are rule parameters — not data values.

For example, a `regex` rule on `national_insurance_number` will appear as:

```json
{
  "name": "ni_number_format",
  "field": "national_insurance_number",
  "summary": "national_insurance_number must match the format for a UK National Insurance number",
  "severity": "error",
  "sensitive": true
}
```

The regex pattern itself is not disclosed, preventing the explain output from becoming a guide to constructing synthetic identifiers.

## Using /explain in the REVIEW workflow

The standard REVIEW workflow for a regulated contract:

1. Editor submits the contract for review (`POST /submit-review`).
2. Approver opens the `/explain` output for the contract in `REVIEW` status:
   ```
   GET /api/v1/contracts/hr_employee_records/explain?version=1.0
   ```
3. Approver reads the plain-English summaries to confirm the rules match the agreed data quality specification.
4. Approver approves or rejects with a written reason.

Approvers are not expected to read YAML or understand rule syntax. `/explain` provides the narrative they need to make an informed decision.

## Querying via the Streamlit Workbench

The `/explain` output is surfaced in the Streamlit Workbench on the Contracts tab. Select a contract and click "Explain" to see the plain-English rule descriptions alongside the YAML source.

## Querying programmatically

```python
from sdk import OpenDQVClient

client = OpenDQVClient("http://opendqv.internal:8000", token="...")
explanation = client.explain("hr_employee_records", version="latest")

for rule in explanation["rules"]:
    flag = " [SENSITIVE]" if rule["sensitive"] else ""
    print(f"  {rule['field']}{flag}: {rule['summary']} ({rule['severity']})")
```

## Example plain-English summaries

| Rule type | Generated summary |
|-----------|------------------|
| `not_empty` | "employee_id must be present and non-empty" |
| `regex` | "email must match the format of a valid email address" |
| `range` | "age must be between 18 and 100" |
| `date_format` | "start_date must be a valid date in YYYY-MM-DD format" |
| `compare` (cross-field) | "end_date must be on or after start_date" |
| `compare` (today) | "transaction_date must not be in the future" |
| `required_if` | "refresh_rate_hz is required when panel_type is DIGITAL" |
| `forbidden_if` | "rejection_code must be absent when application_status is APPROVED" |
| `lookup` | "country_code must be a recognised ISO 3166-1 alpha-2 country code" |
| `checksum` | "iban must have valid IBAN check digits (ISO 13616)" |
| `field_sum` | "allocation_equity, allocation_bonds, allocation_cash must sum to 100.0 (tolerance: 0.01)" |
| `cross_field_range` | "trade_price must be between bid_price and ask_price" |
| `unique` | "order_id must be unique within the batch" |

## See also

- `review_lifecycle.md` — the REVIEW workflow that /explain supports
- `sensitive_fields.md` — how sensitive field suppression applies to /explain
