# forbidden_if rule

**Rule type:** `forbidden_if`
**Released:** v1.0.0

## Overview

The `forbidden_if` rule validates that a field is absent (null, empty, or not present) when another field in the same record has a specific value.

It is the complement of `required_if`: where `required_if` makes a field mandatory under a condition, `forbidden_if` makes the field illegal under a condition.

This is useful for enforcing business rules such as:
- A `suspension_reason` must not be present if a case is `ACTIVE`
- A `rejection_code` must not be present when an application is `APPROVED`
- A `cancellation_date` must not be present for a live policy

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `forbidden_if` | Yes | Condition: `{field: <field_name>, value: <value>}` |

## Syntax

```yaml
- name: no_suspension_reason_if_active
  type: forbidden_if
  field: suspension_reason
  forbidden_if:
    field: case_status
    value: ACTIVE
  error_message: "suspension_reason must be absent for active cases"
  severity: error
```

## Behaviour

- The rule fails if the target `field` is present and non-empty AND the condition `field` equals the condition `value`.
- "Absent" means the field is `null`, an empty string `""`, or not present in the record at all.
- If the condition is not met (the controlling field does not equal the specified value), the rule passes regardless of whether the target field is present.

## Use cases by industry

### Healthcare — suspended reason not allowed for active cases

```yaml
- name: no_suspension_reason_if_active
  type: forbidden_if
  field: suspension_reason
  forbidden_if:
    field: case_status
    value: ACTIVE
  error_message: "suspension_reason must be absent for active cases"
  severity: error
```

### Financial Services — rejection code must be absent for approved applications

```yaml
- name: no_rejection_code_if_approved
  type: forbidden_if
  field: rejection_code
  forbidden_if:
    field: application_status
    value: APPROVED
  error_message: "rejection_code must not be populated for an approved application"
  severity: error
```

### Insurance — cancellation date must be absent for live policies

```yaml
- name: no_cancellation_date_if_live
  type: forbidden_if
  field: cancellation_date
  forbidden_if:
    field: policy_status
    value: LIVE
  error_message: "cancellation_date must be absent for a live policy"
  severity: error
```

### HR — termination reason must be absent for active employees

```yaml
- name: no_termination_reason_if_active
  type: forbidden_if
  field: termination_reason
  forbidden_if:
    field: employment_status
    value: ACTIVE
  error_message: "termination_reason must not be set for an active employee"
  severity: error
```

### Banking — settlement account must be absent for rejected transactions

```yaml
- name: no_settlement_account_if_rejected
  type: forbidden_if
  field: settlement_account_number
  forbidden_if:
    field: transaction_status
    value: REJECTED
  error_message: "settlement_account_number must not be populated for rejected transactions"
  severity: warning
```

### Retail — discount code must be absent for non-promotional orders

```yaml
- name: no_discount_code_if_standard
  type: forbidden_if
  field: discount_code
  forbidden_if:
    field: order_type
    value: STANDARD
  error_message: "discount_code must be absent for standard (non-promotional) orders"
  severity: error
```

## Pairing with required_if

`forbidden_if` and `required_if` are often used together to enforce mutual exclusivity within a set of states:

```yaml
# rejection_code is required when REJECTED
- name: rejection_code_required_if_rejected
  type: required_if
  field: rejection_code
  required_if:
    field: application_status
    value: REJECTED
  error_message: "rejection_code is required when application is rejected"
  severity: error

# rejection_code must be absent when APPROVED
- name: no_rejection_code_if_approved
  type: forbidden_if
  field: rejection_code
  forbidden_if:
    field: application_status
    value: APPROVED
  error_message: "rejection_code must be absent for approved applications"
  severity: error
```

## Comparison with related rules

| Rule | Behaviour |
|------|-----------|
| `required_if` | Field MUST be present when condition is met |
| `forbidden_if` | Field MUST be absent when condition is met |
| `conditional_value` | Field must equal a specific value when condition is met |
| `not_empty` | Field must always be present (unconditional) |

## See also

- `required_if` rule — conditional presence requirement
- `conditional_value` rule — field must equal a specific value when a condition is met
- `condition` block — apply any rule conditionally (different from `forbidden_if`: `condition` gates rule evaluation; `forbidden_if` is the rule itself)
