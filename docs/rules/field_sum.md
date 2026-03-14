# field_sum rule

**Rule type:** `field_sum`
**Released:** v1.0.0

## Overview

The `field_sum` rule validates that the sum of a named list of fields equals a target value, within an optional tolerance. It is used for integrity checks where multiple fields in the same record must add up to a known total — such as percentage allocations that must sum to 100%, or component weights that must equal a declared gross weight.

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `sum_fields` | Yes | — | List of field names to sum |
| `sum_equals` | Yes | — | The target value the sum must equal |
| `sum_tolerance` | No | `0.0` | Maximum allowed deviation from `sum_equals` (absolute, not relative) |
| `field` | Yes | — | The "anchor" field — any one of the fields in `sum_fields`. Used to attribute the error to a specific field in the response. |

## Syntax

```yaml
- name: allocations_sum_to_100
  type: field_sum
  field: allocation_equity
  sum_fields: [allocation_equity, allocation_bonds, allocation_cash]
  sum_equals: 100.0
  sum_tolerance: 0.01
  error_message: "Portfolio allocations must sum to 100%"
  severity: error
```

## Behaviour

- All fields listed in `sum_fields` are converted to floats and summed.
- The rule passes if `|sum - sum_equals| <= sum_tolerance`.
- If `sum_tolerance` is omitted or 0.0, the sum must equal `sum_equals` exactly. In practice, always set a small tolerance (e.g. `0.01`) to handle floating-point rounding.
- If any field in `sum_fields` is absent or null, it is treated as `0.0`.
- Errors are attributed to the `field` specified in the rule (the anchor field).

## Use cases by industry

### Financial Services — portfolio allocations sum to 100%

```yaml
- name: portfolio_sum_to_100
  type: field_sum
  field: allocation_equity
  sum_fields:
    - allocation_equity
    - allocation_bonds
    - allocation_cash
    - allocation_alternatives
  sum_equals: 100.0
  sum_tolerance: 0.01
  error_message: "Portfolio allocations must sum to 100%"
  severity: error
```

### Insurance — premium splits sum to total premium

```yaml
- name: premium_splits_match_total
  type: field_sum
  field: base_premium
  sum_fields:
    - base_premium
    - fire_loading
    - flood_loading
    - theft_loading
  sum_equals: 1200.00
  sum_tolerance: 0.01
  error_message: "Premium components must sum to the declared gross premium"
  severity: error
```

Replace `sum_equals: 1200.00` with the expected gross premium for your product. For contracts where the total varies per record, combine `field_sum` with a `compare` rule to validate the total field separately, or use a `cross_field_range` rule to bound each component.

### Media & Entertainment — rights percentage splits sum to 100%

```yaml
- name: rights_splits_sum_to_100
  type: field_sum
  field: publisher_rights_pct
  sum_fields:
    - publisher_rights_pct
    - artist_rights_pct
    - label_rights_pct
  sum_equals: 100.0
  sum_tolerance: 0.01
  error_message: "Rights percentage splits must sum to 100%"
  severity: error
```

### FMCG/Logistics — pack component weights sum to declared gross weight

```yaml
- name: pack_weight_components
  type: field_sum
  field: net_weight_g
  sum_fields:
    - net_weight_g
    - packaging_weight_g
    - void_fill_weight_g
  sum_equals: 500.0
  sum_tolerance: 5.0
  error_message: "Component weights must sum to declared gross weight of 500g (±5g)"
  severity: warning
```

For cases where the target sum comes from another field in the record (e.g. a declared gross weight field), combine `field_sum` with a `compare` rule to validate the total field separately.

### Energy — metering period allocation percentages

```yaml
- name: period_allocations_balance
  type: field_sum
  field: peak_allocation_pct
  sum_fields:
    - peak_allocation_pct
    - off_peak_allocation_pct
    - overnight_allocation_pct
  sum_equals: 100.0
  sum_tolerance: 0.001
  error_message: "Metering period allocations must sum to 100%"
  severity: error
```

### HR — FTE allocation across cost centres

```yaml
- name: fte_cost_centre_allocation
  type: field_sum
  field: cost_centre_a_fte
  sum_fields:
    - cost_centre_a_fte
    - cost_centre_b_fte
    - cost_centre_c_fte
  sum_equals: 1.0
  sum_tolerance: 0.001
  error_message: "FTE must be fully allocated across cost centres (must sum to 1.0)"
  severity: error
```

## Choosing sum_tolerance

| Data type | Recommended tolerance |
|-----------|----------------------|
| Integer percentages | `0.0` (exact) |
| Float percentages from UI | `0.01` |
| Weights from sensors | `1-5` (physical measurement error) |
| Financial amounts (2dp) | `0.01` |
| Financial amounts (4dp+) | `0.0001` |

Always set a non-zero tolerance when field values originate from floating-point arithmetic or user input, as rounding errors are common.

## See also

- `range` rule — bound a single field between min and max
- `cross_field_range` rule — bound a field between two other fields in the same record
- `compare` rule — compare two individual fields
- `condition` block — apply field_sum only for specific record types
