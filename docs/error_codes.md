# OpenDQV Error Code Catalogue

Every validation failure in OpenDQV carries a stable `error_code` field. Error codes are
derived deterministically from the rule type **and the rule name**:
`OPENDQV_{RULE_TYPE}_{RULE_NAME}`.

Use these codes to route failures in dead-letter queues, trigger PagerDuty/ServiceNow alerts,
and build retry logic — without parsing human-readable `message` strings that may change
between contract versions.

> **Breaking change in v2.3.6 (CRT170/J4).** Prior to v2.3.6, the suffix was a
> hard-coded `_001`, so two rules of the same type — e.g. `valid_email` and
> `valid_phone`, both `regex` — collapsed to the same `OPENDQV_REGEX_001` code,
> which made rule-instance routing impossible. Codes now encode the actual rule
> name. Clients matching exactly on `OPENDQV_REGEX_001` must update; clients
> matching the prefix `OPENDQV_REGEX_` continue to work. See CHANGELOG v2.3.6
> for migration notes.

---

## Format

```
OPENDQV_{RULE_TYPE}_{RULE_NAME}
```

- `OPENDQV` — namespace prefix, always present
- `{RULE_TYPE}` — rule type from the contract YAML (`regex`, `not_empty`, `range`, …),
  uppercased with underscores
- `{RULE_NAME}` — rule `name` from the contract YAML, uppercased with underscores

Both segments are derived from the contract YAML and are stable as long as the rule
keeps its name and type.

---

## Examples by Rule Type

| Contract YAML (`name` / `type`) | Resulting `error_code` |
|---|---|
| `name_required` / `not_empty` | `OPENDQV_NOT_EMPTY_NAME_REQUIRED` |
| `valid_email` / `regex` | `OPENDQV_REGEX_VALID_EMAIL` |
| `valid_phone` / `regex` | `OPENDQV_REGEX_VALID_PHONE` |
| `username_format` / `regex` | `OPENDQV_REGEX_USERNAME_FORMAT` |
| `age_range` / `range` | `OPENDQV_RANGE_AGE_RANGE` |
| `country_iso` / `lookup` | `OPENDQV_LOOKUP_COUNTRY_ISO` |
| `iban_check` / `checksum` | `OPENDQV_CHECKSUM_IBAN_CHECK` |
| `date_of_birth_format` / `date_format` | `OPENDQV_DATE_FORMAT_DATE_OF_BIRTH_FORMAT` |
| `unique_customer_id` / `unique` | `OPENDQV_UNIQUE_UNIQUE_CUSTOMER_ID` |
| `dob_matches_age` / `age_match` | `OPENDQV_AGE_MATCH_DOB_MATCHES_AGE` |

The `RULE_TYPE` segment lets you group failures by category (all regex failures, all
range failures, …); the `RULE_NAME` segment lets you route on the specific rule.

---

## Example Response

```json
{
  "valid": false,
  "errors": [
    {
      "field": "email",
      "rule": "valid_email",
      "message": "email must match pattern ^[\\w.+-]+@[\\w-]+\\.[\\w.]+$",
      "severity": "error",
      "error_code": "OPENDQV_REGEX_VALID_EMAIL"
    }
  ],
  "warnings": []
}
```

---

## Using Error Codes

**Kafka dead-letter routing — by rule type (prefix match):**
```python
if record["error_code"].startswith("OPENDQV_REGEX_"):
    producer.send("dlq-format-errors", record)
elif record["error_code"].startswith("OPENDQV_NOT_EMPTY_"):
    producer.send("dlq-missing-fields", record)
```

**Kafka dead-letter routing — by rule instance (exact match):**
```python
if record["error_code"] == "OPENDQV_REGEX_VALID_EMAIL":
    producer.send("dlq-bad-emails", record)
elif record["error_code"] == "OPENDQV_REGEX_VALID_PHONE":
    producer.send("dlq-bad-phones", record)
```

**PagerDuty / alerting rule:**
```
IF error_code STARTSWITH "OPENDQV_CHECKSUM_" AND count > 10 THEN page on-call
```

**ServiceNow auto-ticket:**
Use `error_code` as the ticket category — it is stable across contract versions, unlike
the `message` field which may change when error messages are improved.

---

## Stability Guarantee

Error codes are **stable as long as the rule keeps its name and type.** The code
`OPENDQV_REGEX_VALID_EMAIL` will always mean the rule named `valid_email` (a regex rule)
failed, regardless of what the pattern or error message says. Codes are additive — new
rules get new codes, existing codes are never silently re-mapped.

Renaming a rule in the contract YAML produces a new error_code (the segment after the
type changes). Treat rule names like API contract identifiers: stable in production,
versioned via the contract.

---

## Migration from v2.3.5 and earlier

Code matching `OPENDQV_<TYPE>_001` no longer exists.

| Before (v2.3.5 and earlier) | After (v2.3.6+) |
|---|---|
| `code == "OPENDQV_REGEX_001"` | `code.startswith("OPENDQV_REGEX_")` (rule type) |
| `code == "OPENDQV_REGEX_001"` | `code == "OPENDQV_REGEX_VALID_EMAIL"` (rule instance) |
| `code.endswith("_001")` | drop — the `_001` suffix is gone |
