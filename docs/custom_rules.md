# Adding a Custom Rule Type

> **Last reviewed:** 2026-03-17.
> Covers the four files you need to touch, a complete worked example (`phone_e164`), and pointers to existing rule implementations you can copy from.

OpenDQV's rule dispatch is an explicit `if/elif` chain. There is no plugin registry. Adding a new rule type means editing source files — the tradeoff is that every rule type is trivially grep-able and auditable.

---

## Overview

Adding a rule type requires changes to **three files**, or four if you want push-down code generation:

1. `core/rule_parser.py` — add optional config fields to the `Rule` Pydantic model.
2. `core/validator.py` (`_check_rule`) — add an `elif` branch for single-record validation.
3. `core/validator.py` (`_batch_check_rule`) — add a corresponding DuckDB branch for batch validation.
4. `core/code_generator.py` *(optional)* — add a branch to emit Apex / JS / Snowflake code.

All changes are additive. Existing behaviour is unaffected.

---

## Step 1: `core/rule_parser.py` — Add Model Fields

Open `core/rule_parser.py` and add optional fields to the `Rule` Pydantic model for any configuration parameters your rule needs. Use `Optional[<type>] = None` so that contracts that do not use your rule are unaffected.

Also add your rule type to the module-level docstring's supported types list so that `show` and the governance workbench display it correctly.

**Pattern to follow** (add alongside the existing optional fields):

```python
# Phone E.164 validation — type: phone_e164
# Validates that a string field matches E.164 international phone number format.
# No extra configuration fields are required; the format is fixed.
# Use allow_extensions: true to permit optional extensions (e.g. "+14155550100x123").
allow_extensions: Optional[bool] = None
```

Add to the docstring:

```
phone_e164         — field must be a valid E.164 phone number (+[1-3 digit cc][7-12 digits])
                     set allow_extensions: true to permit trailing extension suffixes
```

No Pydantic import changes are needed — `Optional` is already imported.

---

## Step 2: `core/validator.py` — `_check_rule()`

Open `core/validator.py` and add an `elif` branch inside `_check_rule()`. The function returns the rule's `error_message` string on failure and `None` on success.

Place your branch before the final catch-all warning block near line 720. The catch-all logs an `Unknown rule type` warning for any unrecognised type, so your new type must appear before it.

```python
if rule.type == "phone_e164":
    if value is None or (isinstance(value, str) and value.strip() == ""):
        # Treat missing/empty as a separate not_empty concern; phone_e164 passes silently.
        return None
    import re as _re
    str_val = str(value).strip()
    # E.164: + followed by 1-3 digit country code, then 7-12 digits (total 8-15 digits after +)
    base_pattern = r"^\+[1-9]\d{7,14}$"
    if rule.allow_extensions:
        # Allow optional extension suffix: +14155550100x123 or +14155550100 ext 123
        pattern = r"^\+[1-9]\d{7,14}(\s*(x|ext\.?)\s*\d{1,6})?$"
    else:
        pattern = base_pattern
    if not _re.match(pattern, str_val):
        return rule.error_message
    return None
```

**Contract:** return `None` on success, return `rule.error_message` (a `str`) on failure. Never raise. If required configuration is missing, log a warning and return `None` (pass silently) rather than raising — this prevents a misconfigured rule from blocking unrelated fields.

---

## Step 3: `core/validator.py` — `_batch_check_rule()`

The `_batch_check_rule()` function runs the same rule against a batch via DuckDB. Add a corresponding `elif` branch. Use parameterised queries where possible.

For rules whose logic is too complex for SQL (multi-step lookups, external calls, complex regex), fall back to a Python loop — as the `regex` and `compare` rules do. Never use f-string interpolation for user-supplied values in SQL; use DuckDB's `$param` binding or Python pre-computation.

```python
elif rule.type == "phone_e164":
    # Fall back to Python — regex with optional extension suffix is simpler to maintain here.
    import re as _re
    if rule.allow_extensions:
        pat = _re.compile(r"^\+[1-9]\d{7,14}(\s*(x|ext\.?)\s*\d{1,6})?$")
    else:
        pat = _re.compile(r"^\+[1-9]\d{7,14}$")
    for idx, val in enumerate(df[field]):
        if val is None:
            continue  # missing values pass; use not_empty to enforce presence
        if not pat.match(str(val).strip()):
            failing.add(idx)
```

Add this branch after the existing `elif rule.type == "max_length"` block and before the `elif rule.type == "date_format"` block to keep numeric/string/format rules grouped together.

Also add `"phone_e164"` to the exhaustive type list in the final catch-all block of `_check_rule()` (around line 720) so the unknown-type warning is not emitted:

```python
if rule.type not in ("not_empty", "regex", "min", "max", "range", "min_length",
                      "max_length", "date_format", "unique", "compare",
                      "required_if", "lookup", "checksum", "cross_field_range",
                      "field_sum", "forbidden_if", "conditional_value",
                      "date_diff", "ratio_check", "conditional_lookup",
                      "geospatial_bounds", "age_match", "phone_e164"):  # <- add here
    logger.warning("Unknown rule type '%s' for rule '%s'", rule.type, rule.name)
```

---

## Step 4 (Optional): `core/code_generator.py`

To emit the rule as push-down code, add a branch to `_js_rule_check()` (shared by the `js` and `snowflake` targets) and a separate branch in `_generate_salesforce()`.

**JavaScript / Snowflake UDF (`_js_rule_check`):**

```python
elif rtype == "phone_e164":
    allow_ext = rule.get("allow_extensions", False)
    if allow_ext:
        pat = r"^\\+[1-9]\\d{7,14}(\\s*(x|ext\\.?)\\s*\\d{1,6})?$"
    else:
        pat = r"^\\+[1-9]\\d{7,14}$"
    safe_pat = _escape_pattern(pat)
    snippet += (
        f"{indent}if (row['{field}'] && "
        f"!new RegExp('{safe_pat}').test(row['{field}'].toString().trim())) "
        f"errors.push('{error}');\n"
    )
```

**Salesforce Apex (`_generate_salesforce` loop):**

```python
elif rtype == "phone_e164":
    allow_ext = rule.get("allow_extensions", False)
    if allow_ext:
        pat = r"^\\+[1-9]\\d{7,14}(\\s*(x|ext\\.?)\\s*\\d{1,6})?$"
    else:
        pat = r"^\\+[1-9]\\d{7,14}$"
    safe_pat = _escape_pattern(pat)
    code += (
        f"            if (row.get('{field}') != null && "
        f"!Pattern.matches('{safe_pat}', (String)row.get('{field}'))) "
        f"{target_list}.add('{error}');\n"
    )
```

---

## Worked Example: `phone_e164`

This section collects all four steps into a single complete example that could be pasted directly into the source files.

### What it validates

E.164 is the international standard phone number format: a `+` sign, 1–3 digit country code, then 7–12 subscriber digits, for a total of 8–15 digits after the `+`. Examples: `+14155550100` (US), `+447911123456` (UK), `+81312345678` (Japan).

### YAML contract usage

```yaml
rules:
  - name: phone_number_e164
    type: phone_e164
    field: phone
    severity: error
    error_message: "Phone number must be in E.164 format (e.g. +14155550100)"

  - name: support_phone_with_ext
    type: phone_e164
    field: support_phone
    allow_extensions: true
    severity: warning
    error_message: "Support phone should be E.164 format; extensions permitted"
```

### `core/rule_parser.py` — model field

```python
# Phone E.164 validation — type: phone_e164
allow_extensions: Optional[bool] = None
```

Add to the module docstring:

```
phone_e164         — field must be a valid E.164 phone number (+[1-3 digit cc][7-12 digits])
                     set allow_extensions: true to permit trailing extension suffixes
```

### `core/validator.py` — `_check_rule()`

```python
if rule.type == "phone_e164":
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    import re as _re
    str_val = str(value).strip()
    if rule.allow_extensions:
        pattern = r"^\+[1-9]\d{7,14}(\s*(x|ext\.?)\s*\d{1,6})?$"
    else:
        pattern = r"^\+[1-9]\d{7,14}$"
    if not _re.match(pattern, str_val):
        return rule.error_message
    return None
```

### `core/validator.py` — `_batch_check_rule()`

```python
elif rule.type == "phone_e164":
    import re as _re
    if rule.allow_extensions:
        pat = _re.compile(r"^\+[1-9]\d{7,14}(\s*(x|ext\.?)\s*\d{1,6})?$")
    else:
        pat = _re.compile(r"^\+[1-9]\d{7,14}$")
    for idx, val in enumerate(df[field]):
        if val is None:
            continue
        if not pat.match(str(val).strip()):
            failing.add(idx)
```

### `core/code_generator.py` (optional)

```python
# In _js_rule_check():
elif rtype == "phone_e164":
    allow_ext = rule.get("allow_extensions", False)
    pat = (r"^\\+[1-9]\\d{7,14}(\\s*(x|ext\\.?)\\s*\\d{1,6})?$"
           if allow_ext else r"^\\+[1-9]\\d{7,14}$")
    safe_pat = _escape_pattern(pat)
    snippet += (
        f"{indent}if (row['{field}'] && "
        f"!new RegExp('{safe_pat}').test(row['{field}'].toString().trim())) "
        f"errors.push('{error}');\n"
    )
```

---

## Reference Implementations

When implementing a new rule, start by reading the nearest existing rule as a template:

| Pattern | Rule type | Location |
|---|---|---|
| Simple pattern match | `regex` | `_check_rule()` ~line 346; `_batch_check_rule()` ~line 1014 |
| Numeric bounds | `range` | `_check_rule()` ~line 382; `_batch_check_rule()` ~line 1042 |
| Cross-field access | `compare` | `_check_rule()` ~line 424; `_batch_check_rule()` ~line 1104 |
| Complex domain logic | `checksum` | `_check_rule()` ~line 513; calls `_validate_checksum()` |

**`regex`** is the simplest pattern to copy: validate the string value against a compiled pattern, return `None` on match, return `rule.error_message` otherwise. The `negate` flag inverts the test.

**`range`** shows how to handle `min_value` / `max_value` with `float()` coercion and graceful handling of non-numeric values.

**`compare`** shows how to read a second field from the `record` dict, handle the `today`/`now` sentinel values, and fall back through numeric → ISO date → string comparison.

**`checksum`** shows how to implement complex domain-specific validation (GS1 Mod-10 GTIN check digits, IBAN Mod-97, NHS Mod-11, ISIN Mod-11, LEI Mod-97, VIN Mod-11, ISRC Luhn, CPF Mod-11) without external libraries. Read `_validate_checksum()` in `core/validator.py` for the full implementation.

---

## Testing

Tests live in `tests/test_core.py`. Follow the existing pattern: create `Rule` objects directly, call `validate_record()` for single-record tests and `validate_batch()` for batch tests.

### Test file setup

The `tests/conftest.py` fixture pattern:

```python
# conftest.py copies contracts/ to a temp dir at session start.
# All file I/O by tests goes to the temp copy — never the live contracts/.
# AUTH_MODE=token is set; use the auth_headers / approver_headers fixtures
# when testing API endpoints.
```

You do not need any special fixture for unit tests of `validate_record()` — it takes plain dicts and `Rule` objects.

### Example tests for `phone_e164`

```python
# tests/test_core.py  (add to TestSingleRecordValidator)

from core.rule_parser import Rule, Severity
from core.validator import validate_record, validate_batch


class TestPhoneE164Rule:
    """Tests for the phone_e164 rule type."""

    def _rule(self, **kwargs):
        defaults = {
            "name": "phone_check",
            "type": "phone_e164",
            "field": "phone",
            "error_message": "Invalid E.164 phone number",
        }
        return Rule(**{**defaults, **kwargs})

    def test_valid_us_number(self):
        rules = [self._rule()]
        result = validate_record({"phone": "+14155550100"}, rules)
        assert result["valid"] is True

    def test_valid_uk_number(self):
        rules = [self._rule()]
        result = validate_record({"phone": "+447911123456"}, rules)
        assert result["valid"] is True

    def test_missing_plus_fails(self):
        rules = [self._rule()]
        result = validate_record({"phone": "14155550100"}, rules)
        assert result["valid"] is False
        assert result["errors"][0]["message"] == "Invalid E.164 phone number"

    def test_too_short_fails(self):
        rules = [self._rule()]
        result = validate_record({"phone": "+123"}, rules)
        assert result["valid"] is False

    def test_none_passes_silently(self):
        # Missing values pass — use not_empty to enforce presence separately.
        rules = [self._rule()]
        result = validate_record({"phone": None}, rules)
        assert result["valid"] is True

    def test_extension_rejected_by_default(self):
        rules = [self._rule()]
        result = validate_record({"phone": "+14155550100x123"}, rules)
        assert result["valid"] is False

    def test_extension_allowed_when_flag_set(self):
        rules = [self._rule(allow_extensions=True)]
        result = validate_record({"phone": "+14155550100x123"}, rules)
        assert result["valid"] is True

    def test_batch_validation(self):
        rules = [self._rule()]
        records = [
            {"phone": "+14155550100"},   # pass
            {"phone": "not-a-phone"},    # fail
            {"phone": "+447911123456"},  # pass
        ]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 1
        assert result["results"][1]["valid"] is False
```

Run the new tests in isolation:

```bash
python -m pytest tests/test_core.py::TestPhoneE164Rule -v --tb=short
```

Run the full suite to confirm nothing is broken:

```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -p no:playwright --tb=short --timeout=60 -q
```
