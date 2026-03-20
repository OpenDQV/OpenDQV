# age_match

Validates that a declared age field is consistent with a date-of-birth field.

## When to use

Use this rule when a record carries both a self-declared `age` and a `dob` field. It catches cases where the two are inconsistent — either a data entry error or a falsified age declaration.

## Parameters

| Parameter | Required | Description |
|---|---|---|
| `field` | yes | The field holding the declared age (integer or numeric string) |
| `dob_field` | yes | The field holding the date of birth (`YYYY-MM-DD`) |
| `age_tolerance` | no | Allowed downward deviation in years. Default: **0** (exact match). See note below. |
| `severity` | yes | `error` or `warning` |

## How the check works

The engine computes the exact age from `dob_field` relative to today's date, then checks:

```
computed_age - age_tolerance  <=  declared_age  <=  computed_age
```

**The tolerance is one-directional (downward only).** A declared age above the computed age always fails — a person cannot be older than their date of birth allows. A declared age below the computed age fails unless it falls within the tolerance window.

## Default behaviour (no age_tolerance specified)

With the default of `age_tolerance: 0`, declared age must exactly match the age computed from `dob_field`. This is the right choice for any age-gating or compliance context (e.g., UK Online Safety Act, COPPA) where the declared age carries legal weight.

## When to use age_tolerance

Only set a non-zero tolerance for legacy or self-service systems where age is a manually maintained field that may genuinely lag behind reality — for example, an HR record where an employee last updated their profile a year ago. Even then, a tolerance of 1 is the maximum that makes sense.

**Do not use age_tolerance in age-gating contracts.** An `age_tolerance: 1` on a 13-year minimum age gate means a 12-year-old with a falsified DOB could pass.

## Example — strict age gate (compliance)

```yaml
- name: age_dob_consistent
  type: age_match
  field: age
  dob_field: dob
  severity: error
  error_message: Declared age is inconsistent with date of birth
```

No `age_tolerance` — defaults to 0. Age must exactly match the age computed from `dob_field` as of today's date. For example, with DOB `1990-01-01`, the computed age passes and any age one year above or below fails.

## Example — lenient (legacy self-service HR)

```yaml
- name: age_dob_consistent
  type: age_match
  field: age
  dob_field: dob
  age_tolerance: 1
  severity: warning
  error_message: Declared age may be out of date — please verify
```

Age 35 or 36 both pass with DOB `1990-01-01`. Age 37 still fails (future age is never valid).
