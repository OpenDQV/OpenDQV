# ratio_check

Validates that the ratio of two numeric fields falls within an allowed range.

## Fields

| Field | Required | Description |
|---|---|---|
| `field` | Yes | Primary field (used for error reporting and row identification). |
| `ratio_numerator` | Yes | Field name for the numerator. |
| `ratio_denominator` | Yes | Field name for the denominator. |
| `min_value` | No | Minimum allowed ratio (inclusive). |
| `max_value` | No | Maximum allowed ratio (inclusive). |
| `error_message` | No | Custom message when the rule fails. |
| `severity` | No | `error` (default) or `warning`. |

## How the ratio is computed

```
ratio = ratio_numerator / ratio_denominator
```

The computed ratio is then checked against `min_value` and/or `max_value`. At least one bound must be specified.

## Use cases

| Domain | Numerator | Denominator | Constraint |
|---|---|---|---|
| Mortgage lending (LTV) | `loan_amount` | `property_value` | <= 0.95 |
| Water utilities (NRW) | `losses_m3` | `total_input_m3` | <= 0.25 |
| Real estate (occupancy) | `occupied_units` | `total_units` | >= 0.60 |
| Retail (inventory turnover) | `cogs` | `avg_inventory` | >= 4.0 |
| Media (premium conversion) | `premium_subscribers` | `total_subscribers` | >= 0.10 |

## Example YAML

```yaml
- name: ltv_check
  type: ratio_check
  field: loan_amount
  ratio_numerator: loan_amount
  ratio_denominator: property_value
  max_value: 0.95
  error_message: "Loan-to-Value ratio exceeds 95% — regulatory maximum"
  severity: error

- name: nrw_threshold
  type: ratio_check
  field: losses_m3
  ratio_numerator: losses_m3
  ratio_denominator: total_input_m3
  max_value: 0.25
  error_message: "Non-Revenue Water exceeds Ofwat PR24 25% threshold"
  severity: error
```

## Notes

- If `ratio_denominator` is zero or null the record fails validation (division by zero is treated as a rule failure, not an error).
- If either field is null the record fails validation.
- Both fields must be numeric. Non-numeric values cause the record to fail.
- For single-sided checks, omit the bound you do not need.
- `field` does not have to be the same as `ratio_numerator`; it is used solely to identify the failing record in the validation report.
