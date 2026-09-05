# Core Rule Types Reference

Last reviewed: 2026-09-05 (engine 2.8.0)

OpenDQV ships 24 rule types (the `_RULE_HANDLERS` table in
`opendqv/core/validator.py`). The 14 core ones are documented on this page;
the rest have their own pages — see the [index](README.md). Each rule lives
under `contract.rules[]` in a YAML data contract.

Every rule requires these fields:

```yaml
- name: rule_name          # unique within the contract
  field: target_field      # the record field to validate
  type: rule_type          # one of the types below (or in README.md)
  severity: error          # error (block) or warning (allow but flag)
  error_message: "..."     # returned when validation fails
```

Optional on any rule: `description`, `condition` (conditional application —
vocabulary below), `optional` (accepted for round-trip with the managed
engine; inert on Core, see below).

---

## Presence is explicit (2.5.0+)

A format rule never implies that its field is present. The rules below are
the conformance decisions from `docs/contract_conformance.md` (D2, D6, D10)
and hold identically on the single-record and batch paths:

- **Blank is absent (D6).** Every rule that is not a presence rule skips a
  value that is missing, `null`, or a whitespace-only string — `min`, `max`,
  `range`, `regex`, `min_length`, `max_length`, `date_format`, `checksum`,
  `compare`, `allowed_values`, `lookup`, `date_diff`, and the rest all
  **pass** on an absent field. This is enforced structurally in
  `_check_rule()` before any handler runs, not handler by handler.
- **Presence is its own rule.** To require a field, add `not_empty`
  (any non-blank value), `not_empty_string` (must be a JSON string, and
  non-blank — section 14), or `required_if` (presence conditioned on another
  field). This is how the 41 bundled contracts do it.
- **`optional` is accepted but inert.** `optional: true` loads and
  round-trips so that contracts authored for the managed engine (where an
  error-severity single-field rule implies presence) are byte-identical on
  Core. Core already skips absent values, so the key changes nothing here.
- **Cross-field rules fail on a missing counterpart (D10).** `compare`,
  `date_diff`, `field_sum`, `cross_field_range`, `ratio_check`, `age_match` —
  when the *other* field the rule reads (the counterpart, not a `condition` trigger) is
  absent or blank the rule **fails** with the rule's `error_message`, and the
  error entry carries `counterpart_missing: true` (REST, GraphQL and MCP).
  The rule's own `field` being absent is still D6 (skipped).
- **Unknown rule types are refused at load (2.8.0).** `Rule()` raises with the
  list of known types (`opendqv.core.rule_parser.RULE_TYPES`, the same set the
  validator dispatches and the linter checks); `min_age`/`max_age` are keys on a
  `date_format` rule, not types. A stored YAML with a typo does not load — the
  refusal is logged and `opendqv lint` reports `UNKNOWN_RULE_TYPE`.
- **`regex` is an unanchored search** (since 2.5.0; it was `match`). A
  pattern without `^…$` matches anywhere in the value. Anchor deliberately;
  `opendqv lint` flags `REGEX_NOT_START_ANCHORED` as an advisory.

### `condition` vocabulary

`condition` can sit on any rule and gates whether it applies to a record. The
keys are a closed set — anything else is rejected at load:

```yaml
condition:
  field: transaction_type   # the field inspected (required)
  value: CHARGE             # apply only when field == value (string compare)
  not_value: CREDIT         # apply only when field != not_value
  present: true             # apply only when field is present / absent (bool)
```

`field` is required; the predicates conjoin, so `present: true` combined with
`value:` means "present *and* equal". `present` uses the D6 reading of absence
(missing, `null`, or blank), so `condition: {field: <counterpart>, present: true}`
is how to say "compare only when both fields are present". A rule whose
condition is not met is skipped for that record (no error, no warning).

---

## 1. not_empty

Field must be present, non-null, and (for strings) non-blank after trimming.

```yaml
- name: patient_id_required
  field: patient_id
  type: not_empty
  severity: error
  error_message: "patient_id is required"
```

**Pydantic fields read:** none beyond `field` -- checks value directly.

**Behaviour:** fails if value is `None` or if `str(value).strip() == ""`.

---

## 2. regex

Field must match (or not match) a regular expression pattern.

```yaml
- name: sort_code_format
  field: sort_code
  type: regex
  pattern: '^\d{2}-\d{2}-\d{2}$'
  severity: warning
  error_message: "sort_code must be in NN-NN-NN format"
```

**Pydantic fields:**

| YAML key    | Python field       | Purpose |
|-------------|--------------------|---------|
| `pattern`   | `rule.pattern`     | regex or builtin key |
| `negate`    | `rule.negate`      | if `true`, field must NOT match |

**Builtin shorthands:** instead of a raw regex, use a `builtin:` key:

```yaml
pattern: builtin:email      # ^[^@\s]+@[^@\s]+\.[^@\s]+$
pattern: builtin:uuid       # ^[0-9a-f]{8}-...-[0-9a-f]{12}$
pattern: builtin:ipv4
pattern: builtin:ipv6
pattern: builtin:url
pattern: builtin:semver
pattern: builtin:cve_id
pattern: builtin:smpte-timecode
pattern: builtin:did
pattern: builtin:ean13
pattern: builtin:isbn13
```

**Negated regex example:**

```yaml
- name: no_test_emails
  field: email
  type: regex
  pattern: '@example\.com$'
  negate: true
  severity: error
  error_message: "test email addresses are not permitted"
```

**Gotchas:**
- A regex rule with no `pattern` fails every record (by design -- fail visible, not silent).
- Absent/blank values are skipped before matching (D6) — add `not_empty` if the field is required.
- Matching is an unanchored **search**: `pattern: '\d{6}'` accepts `"ref-123456-x"`. Anchor with `^…$` when you mean the whole value; `opendqv lint` reports `REGEX_NOT_START_ANCHORED` on patterns that do not start with `^`.
- Uses the `regex` library (not `re`) for ReDoS timeout protection (SEC-001).

---

## 3. min

Numeric field must be >= a minimum value.

```yaml
- name: amount_min
  field: amount
  type: min
  min: 0.01
  severity: error
  error_message: "amount must be > 0"
```

**Pydantic fields:**

| YAML key | Python field     | Type  |
|----------|------------------|-------|
| `min`    | `rule.min_value` | float |

`min` is a YAML alias for `min_value`. Both are accepted.

**Behaviour:** fails if the value is non-numeric or `float(value) < min_value`.
Absent/blank values pass (D6) — add `not_empty` for presence.

---

## 4. max

Numeric field must be <= a maximum value.

```yaml
- name: amount_large_transaction_warning
  field: amount
  type: max
  max: 85000
  severity: warning
  error_message: "amount exceeds deposit protection limit"
```

**Pydantic fields:**

| YAML key | Python field     | Type  |
|----------|------------------|-------|
| `max`    | `rule.max_value` | float |

`max` is a YAML alias for `max_value`. Both are accepted.

**Behaviour:** fails if the value is non-numeric or `float(value) > max_value`.
Absent/blank values pass (D6) — add `not_empty` for presence.

---

## 5. range

Numeric field must be between min and max (inclusive).

```yaml
- name: temperature_range
  field: temperature_celsius
  type: range
  min: -40
  max: 60
  severity: error
  error_message: "temperature out of sensor range"
```

**Pydantic fields:**

| YAML key | Python field     | Type  |
|----------|------------------|-------|
| `min`    | `rule.min_value` | float |
| `max`    | `rule.max_value` | float |

Either bound can be omitted for a one-sided range, but then you should use
`type: min` or `type: max` instead for clarity.

**Behaviour:** fails if the value is non-numeric or outside `[min_value, max_value]`.
Absent/blank values pass (D6) — add `not_empty` for presence.

---

## 6. min_length

String length must be >= a minimum.

```yaml
- name: password_min_length
  field: password
  type: min_length
  min_length: 8
  severity: error
  error_message: "password must be at least 8 characters"
```

**Pydantic fields:**

| YAML key     | Python field       | Type |
|--------------|--------------------|------|
| `min_length` | `rule.min_length`  | int  |

**Behaviour:** absent/blank values pass (D6). Otherwise coerces the value to
string via `str(value)` and checks `len(str_val) < min_length`.

**WARNING:** do NOT use `min:` here. See [Common Pitfalls](#common-pitfalls).

---

## 7. max_length

String length must be <= a maximum.

```yaml
- name: reference_max_length
  field: reference
  type: max_length
  max_length: 18
  severity: error
  error_message: "reference must not exceed 18 characters"
```

**Pydantic fields:**

| YAML key     | Python field       | Type |
|--------------|--------------------|------|
| `max_length` | `rule.max_length`  | int  |

**Behaviour:** absent/blank values pass (D6). Otherwise coerces the value to
string and checks `len(str_val) > max_length`.
If `max_length` is not set, defaults to 99999 (effectively no limit).

**WARNING:** do NOT use `max:` here. See [Common Pitfalls](#common-pitfalls).

---

## 8. date_format

Field must be a parseable date or datetime string.

```yaml
- name: transaction_date_format
  field: transaction_date
  type: date_format
  severity: error
  error_message: "transaction_date must be a valid date"
```

**Pydantic fields:**

| YAML key | Python field  | Type | Purpose |
|----------|---------------|------|---------|
| `format` | `rule.format` | str  | custom strptime format (optional) |

**Behaviour:** tries to parse the value against these formats, in order:
1. `rule.format` (if provided)
2. `%Y-%m-%d`
3. `%Y-%m-%dT%H:%M:%S`
4. `%d/%m/%Y`
5. `%m/%d/%Y`

Passes on the first successful parse. Fails if none match. Absent/blank values
pass (D6) — add `not_empty` for presence.

`min_age` / `max_age` may be added to a `date_format` rule (or any rule) as an
add-on check on the age implied by the parsed date; they are keys, not rule
types.

**Custom format example:**

```yaml
- name: uk_date_format
  field: event_date
  type: date_format
  format: "%d/%m/%Y"
  severity: error
  error_message: "event_date must be DD/MM/YYYY"
```

---

## 9. allowed_values

Field value must be one of an inline list. Use this for short, stable
enumerations; use `lookup` for external reference lists.

```yaml
- name: transaction_type_valid
  field: transaction_type
  type: allowed_values
  allowed_values: [debit, credit, transfer, payment, refund]
  severity: error
  error_message: "invalid transaction_type"
```

**Pydantic fields:**

| YAML key         | Python field           | Type |
|------------------|------------------------|------|
| `allowed_values` | `rule.allowed_values`  | list |

**Behaviour:** coerces both the value and list entries to strings before comparison.
Absent/blank values pass (D6) — use `not_empty` to catch missing values separately.

**Gotcha:** a rule with an empty or missing `allowed_values` list silently passes
all records (logged as a warning).

---

## 10. lookup

Field value must appear in an external reference list (file or HTTP endpoint).

```yaml
# Local text file (one value per line)
- name: country_code_valid
  field: country_code
  type: lookup
  lookup_file: contracts/ref/iso_3166_alpha2.txt
  severity: error
  error_message: "invalid country code"

# Local CSV (specific column)
- name: airport_code_valid
  field: airport
  type: lookup
  lookup_file: contracts/ref/airports.csv
  lookup_field: iata_code
  severity: error
  error_message: "invalid airport code"

# HTTP endpoint (JSON array or newline text)
- name: sanctioned_entity_check
  field: entity_id
  type: lookup
  lookup_file: https://api.example.com/sanctioned-ids
  cache_ttl: 600
  lookup_auth_header: "Bearer ${SANCTIONS_API_KEY}"
  severity: error
  error_message: "entity is sanctioned"
```

**Pydantic fields:**

| YAML key              | Python field              | Type | Purpose |
|-----------------------|---------------------------|------|---------|
| `lookup_file`         | `rule.lookup_file`        | str  | path or URL |
| `lookup_field`        | `rule.lookup_field`       | str  | CSV column name |
| `cache_ttl`           | `rule.cache_ttl`          | int  | HTTP cache seconds (default 300) |
| `lookup_auth_header`  | `rule.lookup_auth_header` | str  | auth header with `${ENV_VAR}` substitution |
| `all_of`              | `rule.all_of`             | bool | validate each element in a list field |

**all_of example** (list field where every element must be in the lookup):

```yaml
- name: all_tags_valid
  field: tags
  type: lookup
  lookup_file: contracts/ref/valid_tags.txt
  all_of: true
  severity: error
  error_message: "one or more tags are invalid"
```

**Gotchas:**
- Absent/blank values pass (D6) -- combine with `not_empty` if the field is required.
- Missing `lookup_file` skips validation (logged as warning).
- Local file paths are subject to path traversal protection (SEC-002).
- `lookup_auth_header` performs env var substitution at runtime (`${VAR}` syntax).

---

## 11. compare

Compare this field's value against another field or a sentinel (`today`, `now`).

```yaml
# Cross-field comparison
- name: discharge_after_admission
  field: discharge_date
  type: compare
  compare_to: admission_date
  compare_op: gte
  severity: error
  error_message: "discharge_date must be on or after admission_date"

# Compare against current date
- name: expiry_in_future
  field: expiry_date
  type: compare
  compare_to: today
  compare_op: gte
  severity: error
  error_message: "expiry_date must be today or later"
```

**Pydantic fields:**

| YAML key     | Python field      | Type | Purpose |
|--------------|-------------------|------|---------|
| `compare_to` | `rule.compare_to` | str  | other field name, or `today` / `now` |
| `compare_op` | `rule.compare_op` | str  | `gt`, `lt`, `gte`, `lte`, `eq`, `neq` |
| `algorithm`  | `rule.algorithm`  | str  | `semver` for semantic version comparison |

**Operators:** word form (`gt`) or symbol form (`>`, `<`, `>=`, `<=`, `=`, `!=`) --
symbols are normalised to word form at parse time.

**Type coercion order:**
0. If either field carries a `date_format` rule with a `format:` (2.8.0), both
   sides are parsed as dates — each with its own field's declared layout, an
   undeclared side with ISO 8601 — and never reach the steps below. An operand
   the layout cannot read fails the rule (the format rule names the shape).
1. Both values parsed as `float` (numeric comparison)
2. Both parsed as ISO 8601 datetime (date comparison)
3. If `algorithm: semver`, parsed as semver tuples
4. Fallback: string comparison

**Sentinels:**
- `today` -- current UTC date as `YYYY-MM-DD`
- `now` -- current UTC datetime as ISO 8601

**Gotchas:**
- An absent/blank value in `field` passes (D6) — add `not_empty` for presence.
- If `compare_to` names a field and that field is absent or blank, the rule **fails**
  (D10) and the error entry carries `counterpart_missing: true`.
- Naive datetimes (no timezone offset) are treated as UTC.
- Missing `compare_to` or `compare_op` skips the rule (logged as warning).

---

## 12. required_if

Field is required (non-empty) only when another field has a specific value.

```yaml
- name: media_url_required_for_digital
  field: media_url
  type: required_if
  required_if:
    field: panel_type
    value: DIGITAL
  severity: error
  error_message: "media_url is required when panel_type is DIGITAL"
```

**Pydantic fields:**

| YAML key      | Python field       | Type | Purpose |
|---------------|-------------------|------|---------|
| `required_if` | `rule.required_if` | dict | `{field: str, value: str}` |

**Behaviour:** if `record[required_if.field]` equals `required_if.value` (string
comparison), then the target field must be non-null and non-blank. If the
condition is not met, the rule passes regardless of the target field's value.

**Gotcha:** both the trigger field value and the condition value are coerced to
strings for comparison.

---

## 13. unique

Field value must be unique across all records in a batch. Only enforced in
batch validation (`/validate/batch`); single-record mode silently skips this rule.

```yaml
# Global uniqueness
- name: transaction_id_unique
  field: transaction_id
  type: unique
  severity: error
  error_message: "duplicate transaction_id"

# Unique within groups
- name: slot_unique_per_period
  field: slot_id
  type: unique
  group_by: [settlement_period]
  severity: error
  error_message: "duplicate slot_id within settlement_period"
```

**Pydantic fields:**

| YAML key   | Python field    | Type | Purpose |
|------------|-----------------|------|---------|
| `group_by` | `rule.group_by` | list | field names defining uniqueness scope |

**Behaviour (batch only):**
- Without `group_by`: flags all records that share a duplicate value in the target field.
- With `group_by`: flags duplicates only within records that share the same values
  in all `group_by` fields.
- Uses DuckDB for global uniqueness; O(n) Python grouping for `group_by`.

**Gotcha:** this rule is a no-op in single-record `/validate` calls. If you need
uniqueness enforcement, use `/validate/batch`.

---

## 14. not_empty_string

Like `not_empty`, but the value must also be a JSON **string**. Use it for
identifiers whose canonical form is lost by numeric coercion (leading zeros,
account numbers, postcodes).

```yaml
- name: account_number_present
  field: account_number
  type: not_empty_string
  severity: error
  error_message: "account_number is required"
```

**Behaviour:** fails if the value is missing, `null`, or blank after trimming
(with `error_message`), and fails with an engine message naming the JSON type
received when the value is present but not a string (e.g. `12345` sent as a
number).

---

## Common Pitfalls

### `min:` / `max:` vs `min_length:` / `max_length:` confusion

This is the most common contract authoring mistake. `min:` and `max:` are YAML
aliases for `min_value` and `max_value` (numeric bounds). They are **not** the
same as `min_length` and `max_length` (string length bounds).

Using `min:` on a `type: min_length` rule sets `rule.min_value` (a float) but
leaves `rule.min_length` as `None`. The validator reads `rule.min_length`, finds
`None`, defaults to `0`, and the check `len(str_val) < 0` never fails. The rule
loads without error, passes every record, and never fires.

**WRONG -- silently broken:**

```yaml
- name: account_number_min_length
  field: account_number
  type: min_length
  min: 6                    # WRONG: sets min_value (numeric), not min_length
  severity: error
  error_message: "account_number must be at least 6 characters"
```

**RIGHT:**

```yaml
- name: account_number_min_length
  field: account_number
  type: min_length
  min_length: 6             # CORRECT: sets the field that _check_min_length reads
  severity: error
  error_message: "account_number must be at least 6 characters"
```

The same applies to `max:` and `max_length:`:

**WRONG:**

```yaml
- name: reference_max_length
  field: reference
  type: max_length
  max: 18                   # WRONG: sets max_value (numeric), not max_length
```

**RIGHT:**

```yaml
- name: reference_max_length
  field: reference
  type: max_length
  max_length: 18            # CORRECT
```

**Rule of thumb:** `min:` / `max:` are for `type: min`, `type: max`, and
`type: range`. `min_length:` / `max_length:` are for `type: min_length` and
`type: max_length`. Never mix them.
