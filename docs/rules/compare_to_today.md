# compare_to: today / compare_to: now

**Rule type:** `compare`
**Released:** v1.0.0
**Applies to:** Most starter contracts (14 of the 17 in `examples/starter_contracts/`)

## Overview

The `compare` rule supports two sentinel values for `compare_to`:

- `today` — the current UTC **calendar date**; the comparison is date against date
- `now` — the current UTC **instant**; the comparison is instant against instant

This allows rules to catch future-dated records, expired identifiers, and past-dated records without hardcoding a date.

## Syntax

```yaml
- name: no_future_dates
  type: compare
  field: transaction_date
  compare_to: today
  compare_op: lte          # gt | lt | gte | lte | eq | neq | same_date
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
| `same_date` | field's calendar date must be today (same as `eq` with `today`) |

Symbol aliases (`<=`, `<`, `>=`, `>`, `=`, `!=`) are also accepted and normalised at parse time. No other spelling is accepted: an unknown `compare_op` is refused when a contract is submitted (3.0.5).

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

With `now`, a datetime carrying an offset (e.g. `+01:00`, `Z`) is compared as the instant it names; a naive datetime (no offset) is assumed to be UTC. With `today`, the value's calendar date **as written** (in its own offset) is compared with the current UTC date — `2026-01-10T23:30:00-05:00` is 10 January. Use UTC throughout your source systems for predictable results. DST is not a factor when operating in UTC.

## Notes

- `compare_to: today` compares calendar dates (3.0.5, both engines): both sides are truncated to year/month/day, so a timestamp stamped today is not "after today" and `lte today` passes it. (Before 3.0.5 Core compared the instant to midnight, so `lte today` failed every timestamp after 00:00.)
- `compare_to: now` compares the instant. Use this for timestamp fields where the time of day matters.
- The value is read as a date — with the field's declared `date_format` layout if it has one, else ISO 8601. A value that cannot be read as a date **fails** the rule; it is never compared as text.
- The sentinel is read from the clock each time the rule is evaluated — in a batch, once per record (batch runs the single-record handler on each record), so a long batch near midnight or a `now` boundary can see the clock move between records.
- Combine with a `date_format` rule on the same field to guarantee the field is parseable before the `compare` rule runs.
- The `compare` rule also supports cross-field comparisons (e.g. `compare_to: impression_start`). The `today`/`now` sentinels are a special case of the same rule type.

## See also

- `date_format` rule — validate date parsing before comparing
- `required_if` rule — require a date field only when a condition is met
- `condition` block — apply date rules only to specific record types (e.g. skip for CREDIT notes)
