# cross_field_range rule

**Rule type:** `cross_field_range`
**Released:** v1.0.0

## Overview

The `cross_field_range` rule validates that a field's value falls between the values of two other fields in the same record. This is used when the valid range for a field is not fixed but depends on other data in the record.

Both bounds are optional. You can enforce just a lower bound, just an upper bound, or both.

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `cross_min_field` | No | Field name whose value is the lower bound (inclusive) |
| `cross_max_field` | No | Field name whose value is the upper bound (inclusive) |

At least one of `cross_min_field` or `cross_max_field` must be provided.

## Syntax

```yaml
- name: price_within_spread
  type: cross_field_range
  field: trade_price
  cross_min_field: bid_price
  cross_max_field: ask_price
  error_message: "Trade price must be within bid/ask spread"
  severity: error
```

## Behaviour

- The rule fails if the field value is less than the value of `cross_min_field` (when specified).
- The rule fails if the field value is greater than the value of `cross_max_field` (when specified).
- Bounds are inclusive on both sides.
- If either bound field is absent or null in the record, that bound is skipped (not enforced).
- Works with numeric fields. Field values are compared as floats.

## Use cases by industry

### Financial Services — trade price within bid/ask spread

```yaml
- name: trade_price_within_spread
  type: cross_field_range
  field: trade_price
  cross_min_field: bid_price
  cross_max_field: ask_price
  error_message: "Trade price must be within the bid/ask spread"
  severity: error
```

### Financial Services — settlement amount within contracted bounds

```yaml
- name: settlement_within_contracted_range
  type: cross_field_range
  field: settlement_amount
  cross_min_field: contracted_min_amount
  cross_max_field: contracted_max_amount
  error_message: "Settlement amount is outside contracted range"
  severity: error
```

### Energy — meter reading within expected consumption range

```yaml
- name: meter_reading_within_range
  type: cross_field_range
  field: current_reading
  cross_min_field: expected_min_read
  cross_max_field: expected_max_read
  error_message: "Meter reading is outside the expected consumption range for this period"
  severity: warning
```

### Insurance — claim amount within policy coverage limits

```yaml
- name: claim_within_coverage
  type: cross_field_range
  field: claimed_amount
  cross_min_field: policy_excess
  cross_max_field: policy_limit
  error_message: "Claimed amount must be between policy excess and policy limit"
  severity: error
```

### Banking — loan amount within approved range

```yaml
- name: loan_amount_within_approved_range
  type: cross_field_range
  field: disbursement_amount
  cross_min_field: min_approved_amount
  cross_max_field: max_approved_amount
  error_message: "Disbursement amount is outside the approved loan range"
  severity: error
```

### Logistics — shipment weight within vehicle capacity bounds

```yaml
- name: weight_within_vehicle_capacity
  type: cross_field_range
  field: total_shipment_weight_kg
  cross_max_field: vehicle_max_payload_kg
  error_message: "Shipment weight exceeds vehicle maximum payload"
  severity: error
```

## Using cross_field_range with condition

Apply the range check only for specific record types:

```yaml
- name: charge_amount_within_tariff
  type: cross_field_range
  field: charge_amount
  cross_min_field: tariff_floor
  cross_max_field: tariff_ceiling
  condition:
    field: record_type
    value: CHARGE
  error_message: "Charge amount must be within tariff floor and ceiling for CHARGE records"
  severity: error
```

## Difference from range rule

| Rule | Use when |
|------|----------|
| `range` | Bounds are fixed constants known at contract authoring time |
| `cross_field_range` | Bounds come from other fields in the same record and vary per record |

For example, use `range` to enforce that a price is between 0.01 and 999999. Use `cross_field_range` to enforce that a trade execution price is between the bid and ask prices present in the same record.

## See also

- `range` rule — fixed numeric bounds
- `compare` rule — direct comparison between two fields (equality, greater than, etc.)
- `field_sum` rule — sum of multiple fields must equal a target
- `condition` block — apply rules conditionally based on another field's value
