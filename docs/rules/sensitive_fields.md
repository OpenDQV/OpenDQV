# sensitive_fields contract header

**Contract header field:** `sensitive_fields`
**Released:** v1.0.0

## Overview

The `sensitive_fields` list in a contract header declares which fields contain personal or sensitive data. Fields listed here are suppressed from all log output, error response values, and inspection endpoints — the field name is preserved for routing and triage, but the field's value is never exposed.

This enables HR, healthcare, pharma, and financial services contracts to validate PII-bearing records without letting sensitive values rest in logs.

## Syntax

```yaml
contract:
  name: hr_employee_records
  version: "1.0"
  sensitive_fields:
    - salary
    - national_id
    - date_of_birth
    - ethnicity
  rules:
    - name: salary_range
      type: range
      field: salary
      min: 0
      max: 10000000
      error_message: "Salary is outside the permitted range"
      severity: error
```

## What is suppressed

Fields listed in `sensitive_fields` are suppressed from four surfaces:

| Surface | Suppression |
|---------|-------------|
| `TRACE_LOG` output | Field values are replaced with `[REDACTED]` |
| Validation error responses | Field name is shown, value is omitted |
| `/explain` endpoint | Field name is shown, no sample values or patterns are disclosed |
| `ContractHistory` diffs | Value changes to sensitive fields are recorded as `[REDACTED → REDACTED]` |

The field name itself is retained everywhere — it is needed to route errors to the correct team and to identify which field failed validation.

## What is NOT suppressed

- The validation result (`valid: true/false`) is unaffected.
- The rule name and error message are returned as normal. Write error messages that do not embed the field value (e.g. "Salary is outside the permitted range" not "Salary 1500000 is outside the permitted range").
- Prometheus metrics labels (contract, field, rule) are unaffected. Do not use sensitive values as metric label dimensions.

## Governance: adding and removing fields

Adding or removing a field from `sensitive_fields` is treated as a significant contract change:

- It requires a `REVIEW` cycle — the change must be submitted, reviewed, and approved before the contract transitions to `ACTIVE`.
- The approver's identity is recorded in the `ContractHistory` audit log.
- Removing a field from `sensitive_fields` (i.e. making it no longer suppressed) requires particular care: it must be approved by a data protection role before taking effect.

This ensures that the designation of a field as sensitive cannot be silently reversed by a contract editor.

For data retention obligations that apply to trace logs generated during governance reviews, see [What OpenDQV Retains](#what-opendqv-retains) and [Operator Retention Obligations](#operator-retention-obligations) below.

## Regulatory context

`sensitive_fields` is designed to support:

| Regulation | How sensitive_fields helps |
|------------|---------------------------|
| GDPR Article 5(1)(c) — data minimisation | Validation service processes PII but does not retain it in logs |
| GDPR Article 9 — special category data | Ethnic origin, health data, biometrics can flow through validation without log exposure |
| UK GDPR / DPA 2018 | Same as above; applies to UK deployments post-Brexit |
| FCA SYSC 9 / MAR — record keeping | Audit logs contain sufficient context (field names, rule outcomes) without embedding personal data |
| NHS DSP Toolkit | Patient identifiers (NHS number, DOB) are processed without being written to application logs |
| HIPAA (US Healthcare) | PHI fields can be validated without resting in log storage |

> Note: `sensitive_fields` suppression is a technical control, not a legal guarantee. It must be combined with appropriate network controls, access controls on log storage, and a Data Protection Impact Assessment (DPIA) for your specific deployment.

## Industry examples

### Healthcare — patient record

```yaml
contract:
  name: patient_admission
  version: "1.0"
  sensitive_fields:
    - nhs_number
    - date_of_birth
    - postcode
    - diagnosis_code
  rules:
    - name: nhs_number_format
      type: regex
      field: nhs_number
      pattern: "^\\d{10}$"
      error_message: "NHS number must be exactly 10 digits"
      severity: error

    - name: nhs_number_checksum
      type: checksum
      field: nhs_number
      checksum_algorithm: nhs_mod11
      error_message: "NHS number check digit is invalid"
      severity: error
```

### HR — employee record

```yaml
contract:
  name: employee_record
  version: "1.0"
  sensitive_fields:
    - salary
    - national_insurance_number
    - date_of_birth
    - ethnicity
    - disability_status
    - bank_account_number
    - bank_sort_code
  rules:
    - name: salary_range
      type: range
      field: salary
      min: 0
      max: 10000000
      error_message: "Salary is outside the permitted range"
      severity: error

    - name: ni_number_format
      type: regex
      field: national_insurance_number
      pattern: "^[A-CEGHJ-PR-TW-Z]{2}\\d{6}[ABCD]$"
      error_message: "National Insurance number format is invalid"
      severity: error
```

### Financial Services — KYC record

```yaml
contract:
  name: kyc_application
  version: "1.0"
  sensitive_fields:
    - date_of_birth
    - passport_number
    - national_id
    - tax_identification_number
    - cpf_number
  rules:
    - name: cpf_checksum
      type: checksum
      field: cpf_number
      checksum_algorithm: cpf_mod11
      error_message: "CPF check digits are invalid"
      severity: error
```

## What OpenDQV Retains

Records submitted for validation are processed **in-memory** and discarded immediately after the validation response is returned. OpenDQV does **not** write record field values to disk under any default configuration.

**Exception — TRACE_LOG:** When trace logging is enabled, the following are written to the trace log:

| Written | Not written |
|---------|-------------|
| `record_id` (if present) | Any field value listed in `sensitive_fields` |
| Contract name | Non-sensitive field values (unless `TRACE_LEVEL=full`) |
| Validation outcome (`valid: true/false`) | |
| Timestamp | |
| Rule names that failed | |

- With `TRACE_LEVEL=full`, non-sensitive field values are included in the trace log. Sensitive fields remain suppressed as `[REDACTED]` regardless of `TRACE_LEVEL`.
- The `contract_hash` in the audit chain is a SHA-256 hash of the **contract definition** (rules, metadata), not of any record data.

OpenDQV is a validation service that never stores submitted record values. It has no internal database of submitted records. The contract registry is local to your deployment. Nothing is transmitted to OpenDQV or any third party.

## Operator Retention Obligations

OpenDQV generates trace logs as part of its audit chain. **Operators are responsible for applying their own retention and deletion policies to these logs.**

Relevant obligations by regulation:

| Regulation | Operator obligation |
|------------|---------------------|
| GDPR Article 5(1)(e) — storage limitation | Personal data must not be kept longer than necessary. Operators must define a retention period for trace logs and enforce it (e.g., a 90-day TTL on the log storage layer, or as specified in your Data Processing Agreement). |
| UK GDPR / DPA 2018 | Same as above. Applies to UK deployments post-Brexit. |
| HIPAA (US Healthcare) | HIPAA requires retention of security audit records for a minimum of 6 years. This is the **operator's obligation** — OpenDQV generates the log; the operator must ensure it is retained and protected for the required period. |
| NHS DSP Toolkit | Information governance audit logs must be retained per NHS Data Security and Protection Toolkit requirements. Operators deploying OpenDQV for NHS data must configure log retention accordingly. |

**Recommended baseline:** pipe trace logs to a structured log manager (e.g., Loki, CloudWatch Logs, Elasticsearch) and configure a retention policy appropriate to your DPA or regulatory context. A 90-day TTL is a reasonable default for most commercial deployments.

> Note: Operators processing special-category data (health, biometrics, ethnicity) under GDPR Article 9 should conduct a Data Protection Impact Assessment (DPIA) covering the trace log configuration and retention policy.

For further detail on the audit chain and contract versioning, see the Governance section above and `review_lifecycle.md`.

## See also

- `review_lifecycle.md` — the REVIEW workflow required when changing sensitive_fields
- `explain_endpoint.md` — how sensitive_fields suppression applies to /explain output
- `trace_log.md` — full trace log specification including field suppression behaviour
- GDPR Article 5(1)(c) — principle of data minimisation
- GDPR Article 5(1)(e) — storage limitation principle
