# date_diff

Measures the signed difference between two date fields and validates it falls within an allowed range.

## Fields

| Field | Required | Description |
|---|---|---|
| `field` | Yes | The primary date field (minuend). |
| `date_diff_field` | Yes | The other date field to subtract (subtrahend). |
| `date_diff_unit` | No | Unit for the result: `"days"` (default) or `"years"`. |
| `min_value` | No | Minimum allowed difference (inclusive). |
| `max_value` | No | Maximum allowed difference (inclusive). |
| `error_message` | No | Custom message when the rule fails. |
| `severity` | No | `error` (default) or `warning`. |

## How the difference is computed

```
diff = field - date_diff_field
```

The result is **signed**:

- Positive when `field` is **later** than `date_diff_field`.
- Negative when `field` is **earlier** than `date_diff_field`.
- Zero when both dates are identical.

At least one of `min_value` or `max_value` must be specified.

## Use cases

| Domain | Rule | Expected range |
|---|---|---|
| Contract management | `end_date` - `start_date` = contract term | 1–365 days |
| Pharmacy | `dispensed_date` - `prescribed_date` = prescription validity | 0–28 days |
| Finance | `settlement_date` - `trade_date` = T+2 settlement | exactly 2 days |
| Real estate | today - `listing_date` = listing age | 0–180 days |
| Clinical trials | `visit_2_date` - `visit_1_date` = inter-visit window | 25–35 days |

## Example YAML

```yaml
- name: prescription_validity
  type: date_diff
  field: dispensed_date
  date_diff_field: prescribed_date
  date_diff_unit: days
  min_value: 0
  max_value: 28
  error_message: "Prescription dispensed more than 28 days after issue"
  severity: error

- name: settlement_t2
  type: date_diff
  field: settlement_date
  date_diff_field: trade_date
  date_diff_unit: days
  min_value: 2
  max_value: 2
  error_message: "Settlement must be exactly T+2"
  severity: error
```

## Notes

- Both fields must be parseable dates. If either is null or unparseable the record fails validation.
- Use `date_diff_unit: years` for age-style checks (e.g., employee tenure, policy duration).
- For single-sided checks, omit the bound you do not need (e.g., omit `min_value` to enforce only a maximum).
- To compare a date field against today rather than another field, use the `compare` rule type with `compare_to: today`.
