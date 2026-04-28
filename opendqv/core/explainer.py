"""
Explainer — generates plain-English remediation guidance for validation rule failures.

Designed for LLM agents: given a contract, field, and rule name, returns a structured
explanation with valid/invalid examples so an agent can self-correct without reading YAML.

All explanations are deterministic — pre-computed from contract definitions using
per-rule-type templates. No LLM dependency.
"""

from __future__ import annotations


def explain_rule(rule) -> dict:
    """
    Build an explanation dict for a Rule object.

    Returns:
        {
            "rule_type": str,
            "explanation": str,
            "valid_examples": list,
            "invalid_examples": list,
            "constraint": dict,
        }
    """
    rt = rule.type
    field = rule.field

    if rt == "not_empty":
        return _not_empty(field)
    elif rt == "min":
        return _min(field, rule.min_value)
    elif rt == "max":
        return _max(field, rule.max_value)
    elif rt == "range":
        return _range(field, rule.min_value, rule.max_value)
    elif rt == "min_length":
        return _min_length(field, rule.min_length)
    elif rt == "max_length":
        return _max_length(field, rule.max_length)
    elif rt == "regex":
        return _regex(field, rule.pattern, rule.negate)
    elif rt == "email":
        return _email(field)
    elif rt == "date_format":
        return _date_format(field, rule.format)
    elif rt == "enum":
        return _enum(field, rule.pattern)
    elif rt == "allowed_values":
        return _allowed_values(field, rule.allowed_values)
    elif rt == "lookup":
        return _lookup(field, rule.lookup_file)
    elif rt == "min_age":
        return _min_age(field, rule.min_age)
    elif rt == "max_age":
        return _max_age(field, rule.max_age)
    elif rt == "unique":
        return _unique(field)
    elif rt == "compare":
        return _compare(field, rule.compare_to, rule.compare_op)
    elif rt == "required_if":
        cond = rule.required_if or {}
        return _required_if(field, cond.get("field", "?"), cond.get("value", "?"))
    elif rt == "checksum":
        return _checksum(field, rule.checksum_algorithm)
    elif rt == "cross_field_range":
        return _cross_field_range(field, rule.cross_min_field, rule.cross_max_field)
    elif rt == "field_sum":
        return _field_sum(field, rule.sum_fields, rule.sum_equals, rule.sum_tolerance)
    elif rt == "forbidden_if":
        cond = rule.forbidden_if or {}
        return _forbidden_if(field, cond.get("field", "?"), cond.get("value", "?"))
    elif rt == "conditional_value":
        return _conditional_value(field, rule.must_equal)
    else:
        return _generic(field, rt, rule.error_message)


# ── Per-type template functions ───────────────────────────────────────

def _not_empty(field: str) -> dict:
    return {
        "rule_type": "not_empty",
        "explanation": (
            f"The '{field}' field is required and must not be null, empty string, or missing. "
            "Check that the field is present in the record and has a non-empty value before submitting."
        ),
        "valid_examples": ["any_non_empty_string", 1, True],
        "invalid_examples": [None, "", "   "],
        "constraint": {},
    }


def _min(field: str, min_val) -> dict:
    v = min_val if min_val is not None else 0
    return {
        "rule_type": "min",
        "explanation": (
            f"The '{field}' field must be a number greater than or equal to {v}. "
            f"Check that the value is a numeric type (int or float) and is not null, "
            f"empty string, or less than {v}. Common causes: failed type coercion from a "
            f"string (e.g. '£{v}' instead of {v}), or a zero value where a positive number is required."
        ),
        "valid_examples": [v, round(v * 2, 2) if v else 1, round(v * 10, 2) if v else 100],
        "invalid_examples": [round(v - 1, 2) if v else -1, None, ""],
        "constraint": {"min": v},
    }


def _max(field: str, max_val) -> dict:
    v = max_val if max_val is not None else 100
    return {
        "rule_type": "max",
        "explanation": (
            f"The '{field}' field must be a number less than or equal to {v}. "
            f"Check that the value does not exceed the maximum of {v}."
        ),
        "valid_examples": [0, round(v / 2, 2), v],
        "invalid_examples": [round(v + 1, 2), round(v * 10, 2), None],
        "constraint": {"max": v},
    }


def _range(field: str, min_val, max_val) -> dict:
    lo = min_val if min_val is not None else 0
    hi = max_val if max_val is not None else 100
    mid = round((lo + hi) / 2, 2)
    return {
        "rule_type": "range",
        "explanation": (
            f"The '{field}' field must be a number between {lo} and {hi} (inclusive). "
            f"Values below {lo} or above {hi} will fail. "
            "Check the value is numeric and within the allowed range."
        ),
        "valid_examples": [lo, mid, hi],
        "invalid_examples": [round(lo - 1, 2), round(hi + 1, 2), None],
        "constraint": {"min": lo, "max": hi},
    }


def _min_length(field: str, min_len) -> dict:
    n = min_len if min_len is not None else 1
    return {
        "rule_type": "min_length",
        "explanation": (
            f"The '{field}' field must be a string with at least {n} character(s). "
            f"Empty strings and strings shorter than {n} character(s) will fail. "
            "Check the value is a non-empty string of sufficient length."
        ),
        "valid_examples": ["a" * n, "a" * (n + 5), "example_value"],
        "invalid_examples": ["a" * (n - 1) if n > 1 else "", None],
        "constraint": {"min_length": n},
    }


def _max_length(field: str, max_len) -> dict:
    n = max_len if max_len is not None else 255
    return {
        "rule_type": "max_length",
        "explanation": (
            f"The '{field}' field must be a string with no more than {n} character(s). "
            f"Strings longer than {n} characters will fail. "
            "Truncate or shorten the value before submitting."
        ),
        "valid_examples": ["short", "a" * (n // 2), "a" * n],
        "invalid_examples": ["a" * (n + 1), "a" * (n * 2)],
        "constraint": {"max_length": n},
    }


def _regex(field: str, pattern, negate: bool) -> dict:
    if negate:
        return {
            "rule_type": "regex",
            "explanation": (
                f"The '{field}' field must NOT match the pattern: {pattern}. "
                "The value will fail if it matches the forbidden pattern."
            ),
            "valid_examples": [],
            "invalid_examples": [],
            "constraint": {"pattern": pattern, "negate": True},
        }
    # v2.3.23 round-3 #7 (Sonnet a96411b104c1e7e18): mini regex walker
    # synthesises a real example for character-class + quantifier
    # patterns. Falls back to None for patterns we can't safely parse.
    # invalid_examples dropped — generating a guaranteed-non-match is
    # either fragile or circular; the rule's error_message conveys the
    # constraint already.
    sample = _synthesise_regex_example(pattern)
    valid_examples = [sample] if sample is not None else []
    return {
        "rule_type": "regex",
        "explanation": (
            f"The '{field}' field must match the regular expression: {pattern}. "
            "Check that the value conforms to the expected format. "
            "Common issues: missing required prefix/suffix, wrong character set, "
            "or incorrect length."
            + (
                f" Example value matching this pattern: {sample!r}"
                if sample is not None else
                " (No example auto-generated for this pattern. Test your data "
                "directly against the pattern.)"
            )
        ),
        "valid_examples": valid_examples,
        "invalid_examples": [],
        "constraint": {"pattern": pattern},
    }


def _synthesise_regex_example(pattern: str, max_len: int = 64):
    """v2.3.23 round-3 #7: walk a regex pattern and emit one valid
    sample. Handles the patterns OpenDQV's bundled contracts use:
    character classes (`[A-Z]`, `[0-9]`, `[A-Z0-9]`), shortcuts (`\\d`,
    `\\w`), fixed `{n}` and ranged `{n,m}` quantifiers, anchors `^`
    `$`, literal characters, escaped specials. Returns None for
    anything more exotic (alternation, groups, backrefs, lookarounds)
    so the caller can fall back to honest "no example" rather than a
    misleading stub.

    Self-validates via regex.search before returning — a walker bug
    that emits a wrong example is caught here, not at the caller.
    """
    if pattern is None or not isinstance(pattern, str):
        return None
    try:
        out, idx = [], 0
        # Strip outer anchors but record their presence (purely
        # cosmetic — the walker emits a string that the unanchored
        # match should accept too).
        if pattern.startswith("^"):
            idx = 1
        end_anchor = pattern.endswith("$") and not pattern.endswith("\\$")
        end = len(pattern) - 1 if end_anchor else len(pattern)

        while idx < end:
            ch = pattern[idx]
            # Character class [...]
            if ch == "[":
                close = pattern.find("]", idx + 1)
                if close == -1:
                    return None
                cls = pattern[idx + 1:close]
                token = _first_member_of_class(cls)
                if token is None:
                    return None
                idx = close + 1
            # Shortcut classes
            elif ch == "\\" and idx + 1 < end:
                nxt = pattern[idx + 1]
                if nxt == "d":
                    token = "0"
                elif nxt == "w":
                    token = "a"
                elif nxt == "s":
                    token = " "
                else:
                    # Escaped literal (\\., \\$, \\\\, etc.)
                    token = nxt
                idx += 2
            # Bare ., *, +, ?, |, (, ) are too permissive / structural
            # for our walker scope — fall back.
            elif ch in (".", "|", "(", ")"):
                return None
            else:
                token = ch
                idx += 1

            # Quantifier following the token (if any)
            quant_count = 1
            if idx < end and pattern[idx] in ("{", "*", "+", "?"):
                if pattern[idx] == "{":
                    close = pattern.find("}", idx + 1)
                    if close == -1:
                        return None
                    body = pattern[idx + 1:close]
                    if "," in body:
                        lo, _hi = body.split(",", 1)
                        try:
                            quant_count = int(lo) if lo else 1
                        except ValueError:
                            return None
                    else:
                        try:
                            quant_count = int(body)
                        except ValueError:
                            return None
                    idx = close + 1
                elif pattern[idx] == "*":
                    quant_count = 0
                    idx += 1
                elif pattern[idx] == "+":
                    quant_count = 1
                    idx += 1
                elif pattern[idx] == "?":
                    quant_count = 0
                    idx += 1

            out.append(token * quant_count)
            if sum(len(p) for p in out) > max_len:
                return None

        sample = "".join(out)
        # Self-validate — if the walker emits something that doesn't
        # match the original pattern, return None rather than a wrong
        # example. Use the `regex` library to honour ReDoS timeout.
        import regex as _re
        if _re.fullmatch(pattern, sample, timeout=0.1):
            return sample
        # Some patterns lack outer anchors but still describe a full
        # field — fall back to .search.
        if _re.search(pattern, sample, timeout=0.1):
            return sample
        return None
    except Exception:
        return None


def _first_member_of_class(cls: str):
    """Pick a representative character from a character class body
    (the part inside `[...]`). Handles ranges (`A-Z`), single chars,
    and combined classes (`A-Z0-9`). Returns the first valid member
    or None if the class is negated / unparseable for our scope."""
    if not cls or cls.startswith("^"):
        return None
    i = 0
    while i < len(cls):
        if i + 2 < len(cls) and cls[i + 1] == "-":
            return cls[i]
        return cls[i]
    return None


def _email(field: str) -> dict:
    return {
        "rule_type": "email",
        "explanation": (
            f"The '{field}' field must be a valid email address in the format local@domain.tld. "
            "It must contain exactly one '@' symbol, a non-empty local part, and a domain with at least one dot. "
            "Whitespace, missing domain, or missing TLD will fail."
        ),
        "valid_examples": ["user@example.com", "first.last@company.org", "user+tag@domain.co.uk"],
        "invalid_examples": ["not-an-email", "@domain.com", "user@", None, ""],
        "constraint": {},
    }


def _date_format(field: str, fmt) -> dict:
    fmt_str = fmt or "YYYY-MM-DD"
    example = "2024-01-15" if not fmt or "Y" in fmt.upper() else fmt_str
    return {
        "rule_type": "date_format",
        "explanation": (
            f"The '{field}' field must be a valid date/datetime string parseable as '{fmt_str}'. "
            f"Check that the value is a string in the correct format, not null, and represents a real calendar date. "
            "Common issues: wrong separator, missing leading zeros, or month/day transposition."
        ),
        "valid_examples": [example, "2024-12-31", "2025-06-01"],
        "invalid_examples": ["not-a-date", "2024/01/15", "15-01-2024", None, ""],
        "constraint": {"format": fmt_str},
    }


def _enum(field: str, pattern) -> dict:
    # pattern for enum is a pipe-separated list wrapped in ^(...)$
    values = []
    if pattern:
        import re
        m = re.match(r'^\^?\(?(.+?)\)?\$?$', pattern)
        raw = m.group(1) if m else pattern
        values = [v.strip() for v in raw.split("|")]
    return {
        "rule_type": "enum",
        "explanation": (
            f"The '{field}' field must be one of the allowed values: {values}. "
            "The value must match exactly — check capitalisation and spacing. "
            "Any value not in the list will fail."
        ),
        "valid_examples": values[:3] if values else ["(one of the allowed values)"],
        "invalid_examples": ["other_value", None, ""],
        "constraint": {"allowed_values": values},
    }


def _logical_lookup_source(lookup_file) -> str:
    """Derive the audit-friendly logical name of a lookup reference.

    Strips internal filesystem details (`ref/` prefix, `.txt` suffix) so
    user-facing copy never exposes the server's directory layout.
    External URLs collapse to "external reference" — the URL itself stays
    available on the rule for callers who need to fetch it.
    """
    if not lookup_file:
        return "reference list"
    if isinstance(lookup_file, str) and lookup_file.startswith(("http://", "https://")):
        return "external reference"
    name = str(lookup_file)
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    if name.endswith(".txt"):
        name = name[:-4]
    return name or "reference list"


def _allowed_values(field: str, allowed_values) -> dict:
    """v2.3.23 P2-12 (Sonnet a8d40b8f5784fb653): real-value synthesis
    for allowed_values rules. Mirrors _enum's slice-3 pattern."""
    values = list(allowed_values) if allowed_values else []
    return {
        "rule_type": "allowed_values",
        "explanation": (
            f"The '{field}' field must be one of the allowed values: {values}. "
            "The value must match exactly — check capitalisation and spacing. "
            "Any value not in the list will fail."
        ),
        "valid_examples": values[:3] if values else ["(one of the allowed values)"],
        "invalid_examples": ["other_value", None, ""],
        "constraint": {"allowed_values": values},
    }


def _read_lookup_file_examples(lookup_file: str, limit: int = 3) -> list[str]:
    """v2.3.23 P2-12: read the first `limit` non-blank lines from a
    lookup file. Returns [] on any read failure (path traversal,
    missing file, IO error). HTTP URLs and empty paths short-circuit
    to []. Caller falls back to placeholder.
    """
    if not lookup_file:
        return []
    if isinstance(lookup_file, str) and lookup_file.startswith(("http://", "https://")):
        return []  # caller emits HTTP-explicit placeholder
    try:
        from opendqv.core.validator import _check_lookup_path_safe
        path = _check_lookup_path_safe(str(lookup_file))
        examples: list[str] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                v = line.strip()
                if not v or v.startswith("#"):
                    continue
                examples.append(v)
                if len(examples) >= limit:
                    break
        return examples
    except (FileNotFoundError, OSError, ValueError, ImportError):
        # ValueError covers _check_lookup_path_safe's traversal/null-byte
        # rejection and any path-shape parse failure. ImportError guards
        # against test environments that import explainer without the
        # full validator module.
        return []


def _lookup(field: str, lookup_file) -> dict:
    source = _logical_lookup_source(lookup_file)
    # v2.3.23 P2-12: try to inline real values from the file. HTTP URLs
    # short-circuit to a placeholder with explicit note (no network
    # call at explain time).
    is_http = (
        isinstance(lookup_file, str)
        and lookup_file.startswith(("http://", "https://"))
    )
    if is_http:
        valid_examples = [
            f"(HTTP lookup — values not inlined at explain time; fetch {lookup_file} for the list)"
        ]
    else:
        real = _read_lookup_file_examples(lookup_file or "")
        valid_examples = real if real else ["(a value present in the reference list)"]
    return {
        "rule_type": "lookup",
        "explanation": (
            f"The '{field}' field must match a value from the '{source}' reference list. "
            "Check that the value exists in the list and matches exactly (case-sensitive)."
        ),
        "valid_examples": valid_examples,
        "invalid_examples": ["(a value not in the reference list)", None, ""],
        "lookup_source": source,
        "constraint": {"lookup_file": lookup_file or "(reference list)"},
    }


def _min_age(field: str, min_age) -> dict:
    n = min_age if min_age is not None else 18
    return {
        "rule_type": "min_age",
        "explanation": (
            f"The '{field}' field must be a date that implies the subject is at least {n} years old. "
            f"The date must be at least {n} years before today. "
            "Check the date is in ISO 8601 format (YYYY-MM-DD) and represents a sufficiently old birthdate."
        ),
        "valid_examples": [f"(a date at least {n} years ago, e.g. 1990-01-01)"],
        "invalid_examples": [f"(a date less than {n} years ago)", None],
        "constraint": {"min_age": n},
    }


def _max_age(field: str, max_age) -> dict:
    n = max_age if max_age is not None else 120
    return {
        "rule_type": "max_age",
        "explanation": (
            f"The '{field}' field must be a date that implies the subject is no older than {n} years. "
            f"The date must be no more than {n} years before today."
        ),
        "valid_examples": [f"(a date no more than {n} years ago)"],
        "invalid_examples": [f"(a date more than {n} years ago)", None],
        "constraint": {"max_age": n},
    }


def _unique(field: str) -> dict:
    return {
        "rule_type": "unique",
        "explanation": (
            f"The '{field}' field must be unique across all records in the batch. "
            "Duplicate values will cause all affected records to fail. "
            "This rule only applies in batch validation — single-record validation always passes."
        ),
        "valid_examples": ["id-001", "id-002", "id-003"],
        "invalid_examples": ["(same value appearing more than once in the batch)"],
        "constraint": {},
    }


def _compare(field: str, compare_to, compare_op) -> dict:
    op_map = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "eq": "==", "neq": "!="}
    op_str = op_map.get(compare_op or "", compare_op or "?")
    return {
        "rule_type": "compare",
        "explanation": (
            f"The '{field}' field must satisfy: {field} {op_str} {compare_to}. "
            f"Both fields must be present and comparable (numeric or ISO date). "
            f"Check that '{field}' is {op_str} the value of '{compare_to}' in the same record."
        ),
        "valid_examples": [f"(value where {field} {op_str} {compare_to})"],
        "invalid_examples": [f"(value where {field} does NOT satisfy {op_str} {compare_to})"],
        "constraint": {"compare_to": compare_to, "compare_op": compare_op},
    }


def _required_if(field: str, cond_field: str, cond_value: str) -> dict:
    return {
        "rule_type": "required_if",
        "explanation": (
            f"The '{field}' field is required when '{cond_field}' equals '{cond_value}'. "
            f"If '{cond_field}' is set to '{cond_value}', '{field}' must be present and non-empty. "
            "If the condition is not met, this field is optional."
        ),
        "valid_examples": [f"(any non-empty value when {cond_field}={cond_value})"],
        "invalid_examples": [f"(null or missing when {cond_field}={cond_value})"],
        "constraint": {"required_if": {"field": cond_field, "value": cond_value}},
    }


def _checksum(field: str, algorithm) -> dict:
    algo = algorithm or "checksum"
    return {
        "rule_type": "checksum",
        "explanation": (
            f"The '{field}' field must pass a {algo} check digit validation. "
            "The value must be a correctly formatted identifier with valid check digits. "
            "Common causes of failure: transcription errors, missing leading zeros, or invalid characters."
        ),
        "valid_examples": [f"(a valid {algo} identifier)"],
        "invalid_examples": ["(an identifier with incorrect check digits)", None, ""],
        "constraint": {"checksum_algorithm": algo},
    }


def _cross_field_range(field: str, cross_min_field, cross_max_field) -> dict:
    return {
        "rule_type": "cross_field_range",
        "explanation": (
            f"The '{field}' field must be between the values of '{cross_min_field}' and "
            f"'{cross_max_field}' in the same record. "
            "All three fields must be present and numeric (or ISO dates). "
            f"Check that {cross_min_field} <= {field} <= {cross_max_field}."
        ),
        "valid_examples": [f"(a value between {cross_min_field} and {cross_max_field})"],
        "invalid_examples": [f"(a value outside the range of {cross_min_field} to {cross_max_field})"],
        "constraint": {"cross_min_field": cross_min_field, "cross_max_field": cross_max_field},
    }


def _field_sum(field: str, sum_fields, sum_equals, sum_tolerance) -> dict:
    fields_str = ", ".join(sum_fields or [])
    target = sum_equals if sum_equals is not None else "?"
    tol = sum_tolerance or 0.0
    return {
        "rule_type": "field_sum",
        "explanation": (
            f"The sum of fields [{fields_str}] must equal {target} (tolerance: ±{tol}). "
            "Check that all component fields are present, numeric, and sum to the required total."
        ),
        "valid_examples": [f"(values that sum to {target})"],
        "invalid_examples": [f"(values that do not sum to {target})"],
        "constraint": {"sum_fields": sum_fields, "sum_equals": sum_equals, "sum_tolerance": tol},
    }


def _forbidden_if(field: str, cond_field: str, cond_value: str) -> dict:
    return {
        "rule_type": "forbidden_if",
        "explanation": (
            f"The '{field}' field must be absent or null when '{cond_field}' equals '{cond_value}'. "
            f"Remove or null out '{field}' when the condition is met."
        ),
        "valid_examples": [f"(field absent or null when {cond_field}={cond_value})"],
        "invalid_examples": [f"(field present with a value when {cond_field}={cond_value})"],
        "constraint": {"forbidden_if": {"field": cond_field, "value": cond_value}},
    }


def _conditional_value(field: str, must_equal) -> dict:
    return {
        "rule_type": "conditional_value",
        "explanation": (
            f"The '{field}' field must equal '{must_equal}' when the specified condition is met. "
            f"Check that '{field}' is set to exactly '{must_equal}' when the condition applies."
        ),
        "valid_examples": [must_equal] if must_equal else ["(the required value)"],
        "invalid_examples": ["(any other value)", None],
        "constraint": {"must_equal": must_equal},
    }


def quick_fix(rule_type: str, error_message: str = "", compare_to: str = "") -> str:
    """Return a concise one-line fix hint for a rule type.

    Used to populate suggested_fix on FieldErrorResponse — lets agents
    self-correct without a separate explain_error round trip.

    `compare_to` is consulted only for the `compare` rule type so the
    fix template can branch between cross-field and cross-time
    (compare_to in {"today", "now"}) sub-cases.
    """
    if rule_type == "compare":
        if compare_to in ("today", "now"):
            return (
                "Adjust the date/time so it satisfies the comparison "
                f"to {compare_to} (e.g. ensure it is in the past, present, or future as required by the error message)."
            )
        if compare_to:
            return (
                f"Adjust this field's value relative to the '{compare_to}' field "
                "so the comparison in the error message holds."
            )
        return "Adjust the value so it satisfies the comparison in the error message."
    _fixes = {
        "not_empty": "Provide a non-empty value.",
        "email": "Use a valid email address, e.g. user@example.com",
        "date_format": "Use ISO 8601 format: YYYY-MM-DD (e.g. 2026-03-24)",
        "min": "Increase the value to meet the minimum threshold.",
        "max": "Decrease the value to stay within the maximum.",
        "range": "Set a value within the allowed numeric range.",
        "min_length": "Provide a longer string value.",
        "max_length": "Shorten the string value.",
        "regex": "Check the expected format in the error message and match it exactly.",
        "enum": "Use one of the allowed values listed in the error message.",
        "lookup": "Value must exactly match an entry in the reference list (case-sensitive).",
        "allowed_values": "Use one of the allowed values listed in the error message.",
        "required_if": "This field is required given the current value of another field — provide a non-empty value.",
        "forbidden_if": "Remove or null out this field given the current state of the record.",
        "checksum": "Check the identifier for transcription errors — the check digit is invalid.",
        "unique": "Remove duplicate values — this field must be unique across all records in the batch.",
        "cross_field_range": "Set a value that falls between the referenced min and max fields.",
        "field_sum": "Ensure the fields sum to the required total.",
        "min_age": "Use a date of birth far enough in the past to meet the minimum age requirement.",
        "max_age": "Use a date of birth recent enough to meet the maximum age requirement.",
        "conditional_value": "Set this field to the required value given the current record state.",
        "age_match": "Check that the age field matches the date of birth.",
    }
    hint = _fixes.get(rule_type)
    if hint:
        return hint
    # Fall back to the first sentence of the error_message if available
    if error_message:
        sentence = error_message.split(".")[0].split(" — ")[0]
        return sentence[:120] if len(sentence) > 120 else sentence
    return f"Review the '{rule_type}' rule constraint in the contract."


def _generic(field: str, rule_type: str, error_message: str) -> dict:
    return {
        "rule_type": rule_type,
        "explanation": (
            f"The '{field}' field failed a '{rule_type}' rule. "
            f"Rule message: {error_message}. "
            "Review the contract definition for the specific constraint requirements."
        ),
        "valid_examples": [],
        "invalid_examples": [],
        "constraint": {},
    }
