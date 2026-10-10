# Core Rule Types Reference

Last reviewed: 2026-10-10 (engine 3.0.5)

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
  value that is missing, `null`, a whitespace-only string, or (3.0.5) an
  empty array `[]` or object `{}` — `min`, `max`,
  `range`, `regex`, `min_length`, `max_length`, `date_format`, `checksum`,
  `compare`, `allowed_values`, `lookup`, `date_diff`, and the rest all
  **pass** on an absent field. This is enforced structurally in
  `_check_rule()` before any handler runs, not handler by handler.
  White space is Unicode White_Space (3.0.5): U+001C–U+001F (the information
  separators, which Python's `str.strip()` also removes) are **not** white
  space, so `"\u001c"` is a present value.
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
  The rule's own `field` being absent is still D6 (skipped) — except for
  `field_sum` and `ratio_check`, whose own `field` is attribution only and
  never read (3.0.5): they judge their operands whenever the rule applies,
  and an absent operand fails. `conditional_value` also fires on an absent own
  field (`must_equal` cannot be met by nothing).
- **A non-empty list or object is not text (3.0.5).** A rule that compares a
  single value — `regex`, `allowed_values`, `forbidden_values`, `lookup`
  without `all_of`, `conditional_lookup`, `conditional_value`, and `compare`'s
  text fallback — fails it under its own code with the engine message
  `<type> rule on field "<field>" compares a single value, got array|object —
  send a string, number or boolean`. Collections are never rendered as text.
  `lookup` with `all_of: true` reads a list.
- **One text rendering (3.0.5).** Every text comparison — `regex`,
  `allowed_values` / `forbidden_values`, `lookup`, `conditional_lookup`,
  `must_equal`, `condition` values, `required_if` / `forbidden_if` trigger values
  and `compare`'s text fallback — reads a value the same way: a boolean as
  `true` / `false`, an integral float as its digits (`12.0` → `12`, `1e21` →
  `1000000000000000000000`), an integer with every digit. A JSON boolean is
  **not a number**: `min` / `max` / `range` give `OPENDQV_TYPE_MISMATCH`, and the
  other numeric rules (`compare`, `cross_field_range`, `field_sum`,
  `ratio_check`, `geospatial_bounds`, `age_match`) fail; nothing reads `true` as
  1. A `compare` with a boolean on either side fails, whatever the operator.
  An integer beyond the float64 range is a type mismatch on
  `min` / `max` / `range` and fails the other numeric rules.
- **`$` is the end of the value (2.10.4).** Python's `$` would also match just
  before a final newline; Core rewrites `$` to `\Z` at compile time so a
  `$`-anchored pattern gives the RE2 verdict — `"…\n"` fails. Escaped `\$`,
  `$` inside a class, and inline-MULTILINE patterns are untouched; the authored
  pattern is what every export and digest sees.
- **Unknown rule keys are refused at load (2.9.0).** A key the engine does not
  read (`date_diff_feild`, `banana`) is a load error naming the rule, the key
  and the nearest known key; likewise an unknown key at the top of the
  (flat, 3.0.0) contract document. `opendqv lint`: `UNKNOWN_RULE_KEY`,
  `UNKNOWN_CONTRACT_KEY`. The values of `condition`, `required_if`,
  `forbidden_if` and `provenance` are the author's maps and are not walked.
- **Unknown rule types are refused at load (2.8.0).** `Rule()` raises with the
  list of known types (`opendqv.core.rule_parser.RULE_TYPES`, the same set the
  validator dispatches and the linter checks); `min_age`/`max_age` are keys on a
  `date_format` rule, not types. A stored YAML with a typo does not load — the
  refusal is logged and `opendqv lint` reports `UNKNOWN_RULE_TYPE`.
- **`regex` is an unanchored search** (since 2.5.0; it was `match`). A
  pattern without `^…$` matches anywhere in the value. Anchor deliberately;
  `opendqv lint` flags `REGEX_NOT_START_ANCHORED` as an advisory.
- **Shapes refused when a contract is submitted (3.0.5).** Each of these used
  to load and then fail every record or never fire. They are refused (as
  `contract_rule_invalid`, 422 on REST) on every way a contract is submitted —
  the MCP draft tool, the REST rule add/update routes, `/import/*` and
  `opendqv fork` — and `opendqv lint` reports each as a `CONTRACT_RULE_INVALID`
  error:
  - a `compare_op` outside `gt lt gte lte eq neq same_date` and
    `> < >= <= = !=` (no other aliases);
  - a `condition` with both `value` and `not_value`, or with `value: null` /
    `not_value: null` (write `present: false` / `present: true`);
  - `negate: true` on any type but `regex` (use `forbidden_values`);
  - a `required_if` / `forbidden_if` map using `equals:` (write `value:`), with
    no `value`, or with `value: null`;
  - a date `format` with an unsupported directive (`%b`, `%z`, `%j`, `%p`, `%T`,
    `%-d`, …), with no directive at all (`dd/mm/yyyy` in lower case), with `%f`
    not after `.` or `,`, with white space around it, or one of the two legacy
    Go layouts `2006-01-02` / `2006-01-02T15:04:05Z07:00` (see `date_format`);
  - `range` missing a bound; `regex` without `pattern`; empty `allowed_values`;
    `lookup` without `lookup_file`; `checksum` without an algorithm or with one
    outside the eleven (names are lower case — `LUHN` is refused);
  - a numeric bound written as a string or boolean (`min: "10"`).

  A **stored** contract carrying one of these still loads, with a logged
  warning per problem, and keeps its old reading — a refusal at load would
  delist the contract at boot. (Contrast unknown rule types, refused at load:
  they have no old reading.)
- **Batch and single give one verdict by construction (3.0.5).**
  `validate_batch` evaluates every rule type except `unique` per record with
  the same handler `validate_record` uses, on the raw record. Only `unique`, a
  set-based rule, is judged on the whole column (DuckDB / Python grouping).

### `condition` vocabulary

`condition` can sit on any rule and gates whether it applies to a record. The
keys are a closed set — anything else is rejected at load:

```yaml
condition:
  field: transaction_type   # the field inspected (required)
  value: CHARGE             # apply only when field == value (text as written)
  not_value: CREDIT         # apply only when field != not_value
  present: true             # apply only when field is present / absent (bool)
```

`field` is required; the predicates conjoin, so `present: true` combined with
`value:` means "present *and* equal". `present` uses the D6 reading of absence
(missing, `null`, or blank), so `condition: {field: <counterpart>, present: true}`
is how to say "compare only when both fields are present". A rule whose
condition is not met is skipped for that record (no error, no warning).

`value` / `not_value` compare **text as written** (3.0.5), through the one text
rendering: a missing or `null` field, or a list or object (empty or not),
matches no `value` and differs from every `not_value`; `""` matches `value: ""`
only (a missing field does not); there is no trimming (`" A"` is not `A`).
Absence is read only by `present:`. A `null` value and `value` together with
`not_value` are refused when a contract is submitted (above).

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

**Behaviour:** fails if the value is absent — missing, `null`, a string that
is empty or Unicode White_Space only, or (3.0.5) an empty `[]` / `{}`. A
non-empty list or object is present.

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
| `negate`    | `rule.negate`      | if `true`, field must NOT match (regex only — refused on other types when a contract is submitted; use `forbidden_values`) |

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
- A regex rule with no `pattern` is refused when a contract is submitted (3.0.5); a stored one fails every record (fail visible, not silent).
- Absent/blank values are skipped before matching (D6) — add `not_empty` if the field is required.
- A non-empty list or object fails with the typed "compares a single value" message; numbers and booleans are matched on their one text rendering (`12.0` is `12`, `true` is `true`).
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

**Behaviour:** fails if `value < min_value`. A numeric string (`"28.50"`) is
read; a value that is not a number — a non-numeric string, a JSON boolean, a
list or object, NaN/infinity, an integer beyond float64 — gives
`OPENDQV_TYPE_MISMATCH`. Absent/blank values pass (D6) — add `not_empty` for presence.

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

**Behaviour:** fails if `value > max_value`; non-numbers (including JSON
booleans) give `OPENDQV_TYPE_MISMATCH`, as for `min`. Absent/blank values pass
(D6) — add `not_empty` for presence.

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

Both bounds are required (3.0.5): a `range` missing one is refused when a
contract is submitted — use `type: min` or `type: max` for a single bound. (A
stored one-bound `range` still loads, with a warning, and checks the bound it
has.)

**Behaviour:** fails if the value is outside `[min_value, max_value]`;
non-numbers (including JSON booleans) give `OPENDQV_TYPE_MISMATCH`, as for `min`.
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

**Behaviour:** absent/blank values pass (D6). A non-string value (a number,
boolean, list or object) fails with a typed message under the rule's own code
(D9) — a length rule never measures the rendering of a number. Otherwise
checks `len(value) < min_length`.

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

**Behaviour:** absent/blank values pass (D6). A non-string value fails with a
typed message, as for `min_length` (D9). Otherwise checks `len(value) > max_length`. `max_length: 0` means zero
(3.0.5): any present value fails. If `max_length` is not set the rule does not
limit.

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
| `format` | `rule.format` | str  | declared layout, `%` codes or `YYYY-MM-DD` spelling (optional) |

**Behaviour:** white space around the value is ignored; a space inside it is not.

- **`format` declared:** the value must have exactly that layout's one shape
  (`YYYY-MM-DD`-style or strptime `%` codes) — no other shape, no ISO fallback.
  Every rule that reads the field as a date (`compare`, `date_diff`,
  `age_match`, `min_age`/`max_age`) uses the same layout, on both validate
  paths; a conditional `date_format` declares no field layout. The layout is
  read like this (3.0.5, both engines):
  - **Every directive is fixed width.** `%Y` is four digits; `%y %m %d %H %M %S`
    are two; `%f` is one to six digits and must follow the format's own `.` or
    `,`. So `1/02/2026` is not `%d/%m/%Y` and `8:05` is not `%H:%M`.
  - **Every other character is itself, exactly once.** One space is one space
    (a doubled space or a tab does not match it), and case counts (`t` is not
    the format's `T`). `%%` is a literal `%`.
  - **The supported directives** are `%Y %y %m %d %H %M %S %f`, or the human
    spellings `YYYY YY MM DD HH MM SS` (`MM` is the month before any `HH` and
    minutes after it). Anything else — `%b`, `%z`, `%j`, `%p`, `%T`, `%-d`, … —
    is refused when a contract is submitted; so are a format with no directive
    (`dd/mm/yyyy` in lower case), `%f` not after `.`/`,`, and white space
    around the format. A stored contract with one still loads and keeps its old
    (`strptime`) reading.
  - **The calendar is checked after the shape**: no 31 February, years
    0001–9999 (`01/01/0000` fails `%d/%m/%Y`). A layout with no year
    (`%H:%M:%S`, `%d/%m`) still reads values. `%y` pivots like `strptime`
    (69–99 → 19xx, 00–68 → 20xx).
  - **Fractions are read to microseconds** — a known Core limit; the managed
    engine reads to nanoseconds. Under a declared `%f`, more than six digits
    fail the shape.
  - **Two legacy Go layouts.** A managed-engine dashboard once wrote
    `format: 2006-01-02` and `format: 2006-01-02T15:04:05Z07:00` into
    contracts. Both are refused when a contract is submitted (the refusal names
    the replacement: `%Y-%m-%d`, or omit `format` for ISO 8601). A stored
    contract reads them as aliases: `2006-01-02` is `%Y-%m-%d`, and
    `2006-01-02T15:04:05Z07:00` is an ISO datetime with a **required** `Z` or
    `±hh:mm` and no fraction.
- **No `format`:** the value must be an ISO 8601 date or datetime — the same
  reader every date-reading rule uses (3.0.3): `YYYY-MM-DD`, optionally followed
  by `Thh:mm:ss`, an optional fraction of any length (`.123` or `,123`), and an optional `Z` or
  `±hh:mm`. The hour is two digits 00–23, minutes and seconds 00–59, an
  offset hour 00–23 and offset minute 00–59 (`+24:00`, `+01:60` fail); years
  are 0001–9999 (`0000-01-01` fails). A fraction of any length is accepted and
  read to microseconds (digits beyond six are dropped — the known Core limit
  above). Nothing looser: no space separator, no `20260110`, no unpadded
  `2026-1-10` or `T8:00:00`, no week dates, no `T08:00`, no `+0100`, no
  lowercase `t`/`z`. Locale-ambiguous dates (`DD/MM/YYYY`) need a declared
  `format`.

Absent/blank values pass (D6) — add `not_empty` for presence.

`min_age` / `max_age` may be added to a `date_format` rule (or any rule) as an
add-on check on the age implied by the parsed date; they are keys, not rule
types. They read the value with the carrying rule's own `format`, else the
field's declared layout, else the ISO surface above. **Absence skips;
unreadable fails** (3.0.4): an absent or blank value is skipped, while a
present value they cannot read as a date fails under the carrying rule's code
and message — even beside a `date_format` rule that also reports it. On a
conditional rule the add-on applies only where the rule's condition is met.

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

**Behaviour:** compares the value and the list entries through the one text
rendering (a boolean is `true`/`false`, `12.0` is `12`; see "One text
rendering" above). A non-empty list or object fails with the typed "compares a
single value" message. Absent/blank values (including `[]` / `{}`) pass (D6) —
use `not_empty` to catch missing values separately.

**Gotcha:** an empty or missing `allowed_values` list is refused when a
contract is submitted (3.0.5); a stored rule with one passes all records.

---

## 9a. forbidden_values (2.9.0)

The exact sibling of `allowed_values` with the sense inverted — a negative set
for placeholder junk that passes `not_empty` and often `regex`/`date_format`
(`N/A`, `12345`, `test@test.com`, `asdf`). Both engines carry it.

```yaml
- name: no_placeholder_email
  type: forbidden_values
  field: email
  forbidden_values: ["N/A", "test@test.com", "asdf"]
  error_message: "email is a placeholder, not a real address"
```

| YAML key | Model field | Type | Notes |
|---|---|---|---|
| `forbidden_values` | `rule.forbidden_values` | list | non-empty; listing them under `allowed_values` is refused with a hint |

**Semantics**
- A **present** value equal to any listed value fails. Absence or blank is not
  a violation — presence is `not_empty`'s job (blank-is-absent applies).
- Exact rendered-text match, case-sensitive, no trimming — identical to
  `allowed_values`. List the variants you mean (`N/A`, `n/a`, `NA`).
- **D12 numeric rendering (both rule types):** an integral float renders without
  the trailing `.0`, so a JSON `99999.0` matches a listed `"99999"` on both
  engines. `1.5` renders `1.5`; a boolean renders as its JSON spelling
  `true`/`false` (2.9.1) — list those spellings, not `True`.
- Error code `OPENDQV_FORBIDDEN_VALUES_<RULE_NAME>`; both validate paths.
- Explain/wizard: the forbidden set feeds **invalid** examples only — a negative
  set says nothing about what is valid.
- JSON Schema: `not: {enum: [...]}` on the property, type-neutral.
- ODCS: no construct for a negative set — carried verbatim in the
  `custom/opendqv` implementation (dimension `conformity`), never as
  `validValues`.

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
- Without `all_of`, a non-empty list or object fails with the typed "compares a single value" message; with `all_of: true` the rule reads a list and every item must be in the reference set.
- A lookup whose file (or URL) cannot be read **fails closed** on both validate paths: every present value fails the rule.
- Missing `lookup_file` is refused when a contract is submitted (3.0.5); a stored rule without one skips validation (logged as warning).
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
| `compare_op` | `rule.compare_op` | str  | `gt`, `lt`, `gte`, `lte`, `eq`, `neq`, `same_date` |
| `algorithm`  | `rule.algorithm`  | str  | `semver` for semantic version comparison |

**Operators:** word form (`gt`) or symbol form (`>`, `<`, `>=`, `<=`, `=`, `!=`) --
symbols are normalised to word form at parse time. That is the whole set: any
other spelling is refused when a contract is submitted (3.0.5); a stored rule
with one logs a warning and does not judge.

**`same_date`:** both operands are read as dates (declared layout or ISO 8601)
and their calendar dates, as written in each value's own offset, must be
equal. An operand that cannot be read as a date **fails** the rule (3.0.5) — it
is not "not applicable".

**Type coercion order:**
0. If either field carries a `date_format` rule with a `format:` (2.8.0), both
   sides are parsed as dates — each with its own field's declared layout, an
   undeclared side with ISO 8601 — and never reach the steps below. An operand
   the layout cannot read fails the rule (the format rule names the shape), so
   comparing a declared date field against a non-date field always fails. A
   `date_format` rule with a `condition:` declares no layout here.
1. If `algorithm: semver`, both sides are read as the numeric
   `major.minor.patch` triple (3.0.5, both paths): pre-release and build parts
   are ignored (`1.2.3-rc1` eq `1.2.3`), ordering is numeric (`1.10.0` gt
   `1.9.0`), and a value that is not a version fails the rule.
2. A JSON boolean on either side fails the rule (it is not a number, and is
   never compared as the text `true`). Otherwise, both values read as numbers
   → numeric comparison.
3. Both read as ISO 8601 dates (compared as instants).
4. Fallback: text comparison through the one text rendering; a non-empty list
   or object fails with the typed "compares a single value" message.

**Sentinels (3.0.5):** the value must read as a date (declared layout or ISO
8601); a value that cannot be read fails the rule.
- `today` -- compares **calendar dates**: the value's date (as written, in its
  own offset) against the current UTC date, both truncated to year/month/day.
  A timestamp stamped today is not "after today", and `lte today` passes it.
- `now` -- compares the **instant** against the current UTC time.

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

**Behaviour:** if `record[required_if.field]` matches `required_if.value`, then
the target field must be present (not missing, `null`, blank, `[]` or `{}`). If
the trigger is not met, the rule passes regardless of the target field's value.

**Trigger matching (3.0.5):** the same as a `condition` `value` — text as
written through the one text rendering (`true` matches the JSON boolean
`true`, `12` matches `12.0`); a missing or `null` trigger field, or a list or
object, never matches; no trimming. `equals:` instead of `value:`, a map
without `value`, and `value: null` are refused when a contract is submitted.

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

**Behaviour:** fails if the value is absent — missing, `null`, blank after
trimming, or (3.0.5) an empty `[]` / `{}` — with `error_message`, and fails
with an engine message naming the JSON type received when the value is
present but not a string (e.g. `12345` sent as a number, or a non-empty list).

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
