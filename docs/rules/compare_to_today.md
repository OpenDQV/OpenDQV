# compare_to: today / compare_to: now

**Rule type:** `compare`
**Released:** v1.0.0
**Applies to:** All 15 industry starter contracts

## Overview

The `compare` rule supports two sentinel values for `compare_to`:

- `today` — resolves to the current UTC date in `YYYY-MM-DD` format at validation time
- `now` — resolves to the current UTC datetime in ISO 8601 format at validation time

This allows rules to catch future-dated records, expired identifiers, and past-dated records without hardcoding a date.

## Syntax

```yaml
- name: no_future_dates
  type: compare
  field: transaction_date
  compare_to: today
  compare_op: lte          # gt | lt | gte | lte | eq | neq
  error_message: "Transaction date must not be in the future"
  severity: error
```

## Operators

| Operator | Meaning |
|----------|---------|
| `lte` | field must be on or before today |
| `lt` | field must be before today (strictly past) |
| `gte` | field must be today or in the future |
| `gt` | field must be in the future (strictly) |
| `eq` | field must be exactly today |
| `neq` | field must not be today |

Symbol aliases (`<=`, `<`, `>=`, `>`, `=`, `!=`) are also accepted and normalised at parse time.

## Examples by industry

### Healthcare — future-dated observations

```yaml
- name: no_future_observations
  type: compare
  field: observation_date
  compare_to: today
  compare_op: lte
  error_message: "Observation date cannot be in the future"
  severity: error
```

### Banking — expired card

```yaml
- name: card_not_expired
  type: compare
  field: expiry_date
  compare_to: today
  compare_op: gte
  error_message: "Card has expired"
  severity: error
```

### Energy — future meter reads

```yaml
- name: no_future_reads
  type: compare
  field: read_timestamp
  compare_to: now
  compare_op: lte
  error_message: "Meter read timestamp cannot be in the future"
  severity: error
```

### Insurance — FNOL not future-dated

```yaml
- name: fnol_after_today
  type: compare
  field: fnol_date
  compare_to: today
  compare_op: lte
  error_message: "FNOL date cannot be future-dated"
  severity: error
```

### Retail/FMCG — expiry date must be in the future

```yaml
- name: product_not_expired
  type: compare
  field: best_before_date
  compare_to: today
  compare_op: gte
  error_message: "Product has passed its best-before date"
  severity: warning
```

### Financial Services — settlement not before today

```yaml
- name: settlement_not_past
  type: compare
  field: settlement_date
  compare_to: today
  compare_op: gte
  error_message: "Settlement date must not be in the past"
  severity: error
```

## Timezone handling

All datetime values are treated as UTC. If your datetime includes a timezone offset (e.g. `+01:00`, `Z`), OpenDQV normalises it to UTC before comparison. If your datetime is naive (no offset), it is assumed to be UTC. Use UTC throughout your source systems for predictable results. DST is not a factor when operating in UTC.

## Notes

- `compare_to: today` compares date strings (`YYYY-MM-DD`). Both sides are resolved as ISO 8601 date strings.
- `compare_to: now` compares datetime strings. Use this for timestamp fields that include a time component.
- ISO 8601 string comparison is lexicographic and correct for dates in `YYYY-MM-DD` and `YYYY-MM-DDTHH:MM:SS` format.
- The sentinel is resolved once at validation time — every record in a batch uses the same `now` value, ensuring consistency across a batch run.
- Combine with a `date_format` rule on the same field to guarantee the field is parseable before the `compare` rule runs.
- The `compare` rule also supports cross-field comparisons (e.g. `compare_to: impression_start`). The `today`/`now` sentinels are a special case of the same rule type.

## See also

- `date_format` rule — validate date parsing before comparing
- `required_if` rule — require a date field only when a condition is met
- `condition` block — apply date rules only to specific record types (e.g. skip for CREDIT notes)
