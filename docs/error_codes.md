# OpenDQV Error Code Catalogue

Every validation failure in OpenDQV carries a stable `error_code` field. Error codes are
derived deterministically from the rule type: `OPENDQV_{RULE_TYPE}_001`.

Use these codes to route failures in dead-letter queues, trigger PagerDuty/ServiceNow alerts,
and build retry logic — without parsing human-readable `message` strings that may change
between contract versions.

---

## Format

```
OPENDQV_{RULE_TYPE}_001
```

- `OPENDQV` — namespace prefix, always present
- `{RULE_TYPE}` — rule type from the contract YAML, uppercased with underscores
- `001` — severity suffix, reserved for future severity variants

---

## Code Catalogue

| Error Code | Rule Type | Description |
|-----------|-----------|-------------|
| `OPENDQV_NOT_EMPTY_001` | `not_empty` | Field is null, missing, or blank |
| `OPENDQV_REGEX_001` | `regex` | Field value does not match the required pattern |
| `OPENDQV_MIN_001` | `min` | Numeric value is below the minimum threshold |
| `OPENDQV_MAX_001` | `max` | Numeric value exceeds the maximum threshold |
| `OPENDQV_RANGE_001` | `range` | Numeric value is outside the allowed range |
| `OPENDQV_MIN_LENGTH_001` | `min_length` | String is shorter than the minimum length |
| `OPENDQV_MAX_LENGTH_001` | `max_length` | String exceeds the maximum length |
| `OPENDQV_DATE_FORMAT_001` | `date_format` | Value does not match the expected date/datetime format |
| `OPENDQV_UNIQUE_001` | `unique` | Duplicate value detected in batch |
| `OPENDQV_COMPARE_001` | `compare` | Cross-field comparison failed |
| `OPENDQV_REQUIRED_IF_001` | `required_if` | Conditionally required field is missing |
| `OPENDQV_FORBIDDEN_IF_001` | `forbidden_if` | Conditionally forbidden field is present |
| `OPENDQV_CONDITIONAL_VALUE_001` | `conditional_value` | Field has an unexpected value given the condition |
| `OPENDQV_CROSS_FIELD_RANGE_001` | `cross_field_range` | Cross-field numeric range violation |
| `OPENDQV_ALLOWED_VALUES_001` | `allowed_values` | Value is not in the allowed set |
| `OPENDQV_LOOKUP_001` | `lookup` | Value not found in the reference lookup file |
| `OPENDQV_CHECKSUM_001` | `checksum` | Checksum / check-digit validation failed |
| `OPENDQV_FIELD_SUM_001` | `field_sum` | Sum of fields does not equal the expected value |
| `OPENDQV_DATE_DIFF_001` | `date_diff` | Date difference is outside the allowed range |
| `OPENDQV_RATIO_CHECK_001` | `ratio_check` | Ratio between fields is outside the allowed range |
| `OPENDQV_GEOSPATIAL_BOUNDS_001` | `geospatial_bounds` | Lat/lon coordinates are outside the allowed bounds |
| `OPENDQV_AGE_MATCH_001` | `age_match` | Age derived from date of birth does not match stated age |

---

## Example Response

```json
{
  "valid": false,
  "errors": [
    {
      "field": "email",
      "rule": "email_format",
      "message": "email must match pattern ^[\\w.+-]+@[\\w-]+\\.[\\w.]+$",
      "severity": "error",
      "error_code": "OPENDQV_REGEX_001"
    }
  ],
  "warnings": []
}
```

---

## Using Error Codes

**Kafka dead-letter routing:**
```python
if record["error_code"] == "OPENDQV_REGEX_001":
    producer.send("dlq-format-errors", record)
elif record["error_code"] == "OPENDQV_NOT_EMPTY_001":
    producer.send("dlq-missing-fields", record)
```

**PagerDuty / alerting rule:**
```
IF error_code = "OPENDQV_CHECKSUM_001" AND count > 10 THEN page on-call
```

**ServiceNow auto-ticket:**
Use `error_code` as the ticket category — it is stable across contract versions, unlike
the `message` field which may change when error messages are improved.

---

## Stability Guarantee

Error codes are **stable across contract versions** for the same rule type. The code
`OPENDQV_REGEX_001` will always mean a regex rule failed, regardless of what the pattern
or error message says. Codes are additive — new rule types get new codes, existing codes
are never removed or renamed.
