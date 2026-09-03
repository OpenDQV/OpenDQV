"""
Validation engine — the core of OpenDQV.

Two modes:
  - validate_record(): Pure Python, single record, fast (sub-50ms target)
  - validate_batch(): DuckDB-powered, batch of records, high throughput

Both return structured results with per-field errors and severity.
"""

import csv
import math
import os
import re
import logging
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

# SEC-001: ReDoS protection — use `regex` library (drop-in re replacement)
# which supports a `timeout` parameter. Falls back to `re` if not installed.
try:
    import regex as _regex_lib
    try:
        _REGEX_TIMEOUT = float(os.environ.get("OPENDQV_REGEX_TIMEOUT", "0.5"))
    except (ValueError, TypeError):
        _REGEX_TIMEOUT = 0.5
    _HAS_REGEX_LIB = True
except ImportError:  # pragma: no cover
    _regex_lib = None  # type: ignore
    _REGEX_TIMEOUT = 0.5
    _HAS_REGEX_LIB = False


def _safe_match(compiled_pattern, str_val: str) -> bool:
    """
    Apply a compiled regex pattern to str_val with ReDoS protection.

    Semantics are UNANCHORED SEARCH (review round 2, S4): the pattern may
    match anywhere in the value, exactly as ODCS `pattern`, JSON Schema
    `pattern`, RE2 and every code-generation target read it. Anchor with
    `^` / `$` in the pattern to pin the ends; the linter names patterns
    that do not start with `^`. (Until v2.4.x this was `re.match`, which
    silently anchored the start and made every portable export looser than
    the engine.)

    If the `regex` library is available, enforces _REGEX_TIMEOUT seconds.
    On timeout, returns False (treat as no-match / validation failure) and
    logs a warning so operators can identify pathological patterns.
    Falls back to the standard `re` library if `regex` is not installed.
    """
    if _HAS_REGEX_LIB:
        try:
            if isinstance(compiled_pattern, _regex_lib.Pattern):
                return bool(compiled_pattern.search(str_val, timeout=_REGEX_TIMEOUT))
            # Fallback: re.Pattern passed in (e.g. from validator.py line 354) — match via regex lib
            return bool(_regex_lib.search(compiled_pattern.pattern, str_val, timeout=_REGEX_TIMEOUT))
        except TimeoutError:
            logger.warning(
                "regex_timeout pattern=%r input_length=%d — treating as no-match",
                compiled_pattern.pattern, len(str_val),
            )
            return False
    return bool(compiled_pattern.search(str_val))

import duckdb
import pandas as pd

from .rule_parser import RULE_TYPES, Rule, Severity, _BUILTIN_PATTERNS
from .trace_log import write_trace_entry

logger = logging.getLogger(__name__)

_REDOS_UNPROTECTED_WARNING = (
    "SEC-001 DEGRADED: the `regex` library is not installed — ReDoS timeout "
    "protection is DISABLED and regex rules run on stdlib `re` with no timeout. "
    "Reinstall OpenDQV with its dependencies (`pip install opendqv`) to restore it."
)


def _warn_if_redos_unprotected(has_regex_lib: bool) -> None:
    """SEC-001: `regex` is a hard runtime dependency precisely because it
    provides the per-match timeout that protects against ReDoS. If it is
    missing the engine still runs, but on the stdlib `re` fallback — which has
    NO timeout, so a pathological pattern can hang a worker. That degradation
    used to be silent; warn loudly so operators notice a broken/tampered
    install rather than discovering it under a ReDoS attack."""
    if not has_regex_lib:
        logger.warning(_REDOS_UNPROTECTED_WARNING)


_warn_if_redos_unprotected(_HAS_REGEX_LIB)

# ── Hot-path constants (allocated once) ─────────────────────────────

_COMPARE_OPS = {
    "gt": lambda x, y: x > y,
    "lt": lambda x, y: x < y,
    "gte": lambda x, y: x >= y,
    "lte": lambda x, y: x <= y,
    "eq": lambda x, y: x == y,
    "neq": lambda x, y: x != y,
}


def _parse_date(v):
    """Parse an ISO-8601 date or datetime into a timezone-aware datetime (UTC
    assumed when the value carries no zone; a bare date is midnight UTC).

    2.7.0 (round 4, found by the regulatory-claims fixture): this used to
    return a *date*, so every ``date_diff`` was whole-day granular and a
    sub-day window (DORA's 4-hour initial-notification clock) could never
    fire here while it fired on the managed engine, and a timestamp with a
    zone offset (``+01:00``) or fractional seconds could not be parsed at
    all. Accepted shapes now match the managed engine's: date; datetime
    without zone; ``Z``; ``±hh:mm``; fractional seconds with either. Because
    this is ``datetime.fromisoformat`` (3.11+), a space-separated datetime
    (``2026-01-10 08:00:00``) and a basic-format date (``20260110``) parse
    too — a superset of the managed engine's layouts, harmless for parity
    since a bundled contract's ``regex`` format rule decides the shape first.
    """
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Cannot parse date: {v!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _delta_days(d1, d2) -> float:
    """Signed difference d1 − d2 in fractional days (both paths use this)."""
    return (d1 - d2).total_seconds() / 86400.0


def _sanitise_record_keys(record: dict) -> dict:
    """
    Return a log-safe summary of a record — only field names and value types,
    never the field values themselves.

    This wrapper MUST be used whenever exception handlers need to log any
    context about the failing record. Raw field values must never appear in
    log output at WARNING level or above.
    """
    return {k: type(v).__name__ for k, v in record.items()}


# ── Response structures ──────────────────────────────────────────────

class FieldError:
    """A single field-level validation failure."""
    __slots__ = ("field", "rule", "message", "severity", "error_code", "counterpart_missing")

    def __init__(self, field: str, rule: str, message: str, severity: str, error_code: str = "",
                 counterpart_missing: bool = False):
        self.field = field
        self.rule = rule
        self.message = message
        self.severity = severity
        self.error_code = error_code
        self.counterpart_missing = counterpart_missing

    def to_dict(self) -> dict:
        d = {"field": self.field, "rule": self.rule, "message": self.message, "severity": self.severity, "error_code": self.error_code}
        if self.counterpart_missing:
            d["counterpart_missing"] = True   # #145: only present on D10 failures — shape unchanged otherwise
        return d


# ── Single-record validation (pure Python, no DuckDB) ───────────────

# ── CRT180: strict schema — declared-field set + kwargs helper ───────────
_CROSS_FIELD_ATTRS = (
    "compare_to", "date_diff_field", "cross_min_field", "cross_max_field",
    "ratio_numerator", "ratio_denominator", "geo_lon_field", "dob_field",
)
_CONDITION_ATTRS = ("required_if", "forbidden_if", "condition")


def declared_field_set(rules: list, extra_fields: list | None = None) -> set[str]:
    """Every field name a contract declares — the strict-schema allow-list.

    A field is declared when any rule targets it, references it across
    fields (compare_to, date_diff_field, cross_min/max_field, ratio
    numerator/denominator, geo_lon_field, dob_field, sum_fields, group_by,
    or the `field` key of required_if / forbidden_if / condition), or when
    it is listed in the contract's `allowed_fields:` allow-list. The calendar
    sentinels `today` / `now` are not field names.
    """
    declared: set[str] = set()

    def add(name):
        if isinstance(name, str) and name and name not in ("today", "now"):
            declared.add(name)

    for rule in rules or []:
        add(getattr(rule, "field", None))
        for attr in _CROSS_FIELD_ATTRS:
            add(getattr(rule, attr, None))
        for attr in ("sum_fields", "group_by"):
            for name in getattr(rule, attr, None) or []:
                add(name)
        for attr in _CONDITION_ATTRS:
            spec = getattr(rule, attr, None)
            if isinstance(spec, dict):
                add(spec.get("field"))
    for name in extra_fields or []:
        add(name)
    return declared


def strict_schema_kwargs(contract, rules: list) -> dict:
    """Keyword arguments for validate_record / validate_batch from a contract.

    Empty for non-strict contracts, so call sites can splat it unconditionally.
    """
    if not getattr(contract, "strict_schema", False):
        return {}
    return {
        "strict_schema": True,
        "declared_fields": declared_field_set(rules, getattr(contract, "allowed_fields", None)),
    }


def _additional_properties_error(record: dict, declared_fields: set | None) -> dict | None:
    unknown = sorted(k for k in record if k not in (declared_fields or set()))
    if not unknown:
        return None
    shown = unknown[:10]
    quoted = ", ".join(f'"{u}"' for u in shown)
    if len(unknown) > len(shown):
        quoted += f" and {len(unknown) - len(shown)} more"
    entry = FieldError(
        field="",
        rule="additional_properties",
        message=(
            f"record contains {len(unknown)} unknown field(s) not declared in the contract: {quoted}"
        ),
        severity=Severity.ERROR.value,
        error_code="OPENDQV_ADDITIONAL_PROPERTIES",
    ).to_dict()
    entry["unknown_fields"] = unknown  # full list, structured (review S2)
    return entry


def validate_record(
    record: dict,
    rules: list[Rule],
    contract_name: str = "",
    context: Optional[str] = None,
    record_index: int = 0,
    sensitive_fields: Optional[list] = None,
    strict_schema: bool = False,
    declared_fields: set | None = None,
) -> dict:
    """
    Validate a single record against rules. Pure Python — no DataFrame, no DuckDB.

    Returns:
        {
            "valid": bool,          # True if no errors (warnings don't block)
            "errors": [...],        # severity=error items
            "warnings": [...],      # severity=warning items
        }
    """
    errors = []
    warnings = []

    # CRT180: strict schema — undeclared fields are rejected before any rule
    # runs, naming every unknown field so the producer sees all of them at once.
    if strict_schema:
        extra = _additional_properties_error(record, declared_fields)
        if extra:
            errors.append(extra)

    for rule in rules:
        value = record.get(rule.field)
        try:
            failure = _check_rule(value, rule, record)
            if not failure and rule.cached_has_age_constraint:
                failure = _check_age(value, rule)
        except Exception:
            # Fail closed. An unexpected error inside a checker must never
            # crash the worker — an unhandled exception surfaces as HTTP 500,
            # a remotely-triggerable DoS. The batch path already fails closed
            # on exception; bring the single-record path to parity by
            # rejecting the record with a generic message (CWE-209: never
            # echo the exception detail to the caller).
            logger.exception(
                "validate_record: checker raised on rule=%s field=%s — failing closed",
                rule.name, rule.field,
            )
            errors.append(FieldError(
                field=rule.field,
                rule=rule.name,
                message="Rule could not be evaluated; record rejected (fail-closed).",
                severity=Severity.ERROR.value,
                error_code="OPENDQV_RULE_ERROR",
            ).to_dict())
            continue

        if failure:
            # v2.3.23 outside-review #3: detect type-mismatch sentinel.
            # Numeric checkers prefix the message when the value is
            # non-numeric. Strip the prefix and override error_code so
            # consumers can branch on a real type-error vs a real
            # value-violation.
            counterpart_missing = False
            if failure.startswith(_TYPE_MISMATCH_PREFIX):
                entry_message = failure[len(_TYPE_MISMATCH_PREFIX):]
                entry_error_code = "OPENDQV_TYPE_MISMATCH"
            elif failure.startswith(_COUNTERPART_MISSING_PREFIX):
                entry_message = failure[len(_COUNTERPART_MISSING_PREFIX):]
                entry_error_code = rule.cached_error_code
                counterpart_missing = True
            else:
                entry_message = failure
                entry_error_code = rule.cached_error_code
            entry = FieldError(
                field=rule.field,
                rule=rule.name,
                message=entry_message,
                severity=rule.cached_severity_value,
                error_code=entry_error_code,
                counterpart_missing=counterpart_missing,
            ).to_dict()

            if rule.severity == Severity.ERROR:
                errors.append(entry)
            else:
                warnings.append(entry)

    # TRACE_LOG — write audit entry if enabled
    fields_validated = [r.field for r in rules]
    failed_rule_fields = [e["field"] for e in errors + warnings]
    write_trace_entry(
        contract_name=contract_name,
        context=context,
        record_index=record_index,
        valid=len(errors) == 0,
        error_count=len(errors),
        warning_count=len(warnings),
        fields_validated=fields_validated,
        sensitive_fields=sensitive_fields or [],
        failed_rules=failed_rule_fields,
    )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _check_condition(rule: Rule, record: Optional[dict]) -> bool:
    """
    Evaluate a rule's condition block against the record.
    Returns True if the rule should be applied, False if it should be skipped.

    condition: {field: transaction_type, not_value: CREDIT}  → skip when field == CREDIT
    condition: {field: region, value: EU}                    → apply only when field == EU
    """
    if not rule.condition:
        return True
    cond_field = rule.condition.get("field")
    raw = (record or {}).get(cond_field)
    # #144: `present: true|false` — apply the rule only when the condition field
    # is present (not None / blank / whitespace) or only when it is absent.
    # Uses the D6 absence reading, so "compare only when both are present" is
    # `condition: {field: <counterpart>, present: true}`. Predicates conjoin.
    if "present" in rule.condition:
        if (not _is_field_absent(raw)) != bool(rule.condition["present"]):
            return False
    actual = str(raw if raw is not None else "")
    if "value" in rule.condition:
        return actual == str(rule.condition["value"])
    if "not_value" in rule.condition:
        return actual != str(rule.condition["not_value"])
    return True


def _is_ascii_digits(s: str) -> bool:
    """True only for a non-empty run of ASCII 0-9.

    str.isdigit() also returns True for Unicode digit characters such as
    superscripts (U+00B2 '²') and other scripts' digits, but int() raises on
    the former — so an isdigit()-gated int() is an unhandled-ValueError (500)
    primitive. An identifier check-string is never validly non-ASCII, so
    reject those here and return a clean "invalid checksum" instead of crashing.
    """
    return s.isascii() and s.isdigit()


def _validate_checksum(value: str, algorithm: str) -> bool:
    """Validate identifier check digits. Returns True if checksum is valid."""
    s = str(value).strip().upper()

    if algorithm == "mod10_gs1":
        # GS1 Mod-10 — used for GTIN-8, GTIN-12, GTIN-13, GTIN-14, GLN, SSCC
        digits = s.replace(" ", "").replace("-", "")
        if not _is_ascii_digits(digits):
            return False
        total = 0
        for i, d in enumerate(reversed(digits[:-1])):
            total += int(d) * (3 if i % 2 == 0 else 1)
        check = (10 - (total % 10)) % 10
        return check == int(digits[-1])

    elif algorithm == "iban_mod97":
        # IBAN ISO 13616 mod-97-10
        iban = s.replace(" ", "")
        if len(iban) < 4:
            return False
        rearranged = iban[4:] + iban[:4]
        # Replace letters with digits: A=10, B=11, ..., Z=35
        numeric = ""
        for ch in rearranged:
            if ch.isalpha():
                numeric += str(ord(ch) - ord('A') + 10)
            elif ch in "0123456789":
                numeric += ch
            else:
                return False
        try:
            return int(numeric) % 97 == 1
        except ValueError:
            return False

    elif algorithm == "isin_luhn":
        # v2.3.25 (Mac BT outside-review defect): renamed from
        # `isin_mod11` — the algorithm IS Luhn mod-10 over the expanded
        # numeric encoding (A=10..Z=35), not mod-11. Original key was
        # mathematically misnamed in v2.3.23. Hard-renamed at v2.3.25
        # because no production callers existed (only the bundled
        # mifid_transaction_report YAML used the old key, and we control
        # that). No alias kept — anyone copying from a 4-day-old example
        # gets a clean failure with the canonical key in the error.
        # ISIN: country code (2 alpha) + 9 alphanum + check digit; Luhn mod-10 over expanded digits
        if len(s) != 12:
            return False
        # Expand: letters → digits (A=10..Z=35)
        expanded = ""
        for ch in s[:-1]:
            if ch.isalpha():
                expanded += str(ord(ch) - ord('A') + 10)
            elif ch in "0123456789":
                expanded += ch
            else:
                # Reject Unicode digits (str.isdigit() True but int() raises)
                # and any other non-alphanumeric — invalid, not a crash.
                return False
        # Luhn on expanded digits
        total = 0
        for i, d in enumerate(reversed(expanded)):
            n = int(d)
            if i % 2 == 0:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        check = (10 - (total % 10)) % 10
        try:
            return check == int(s[-1])
        except ValueError:
            return False

    elif algorithm == "lei_mod97":
        # LEI: 20-char alphanumeric, mod-97 same as IBAN
        if len(s) != 20:
            return False
        numeric = ""
        for ch in s:
            if ch.isalpha():
                numeric += str(ord(ch) - ord('A') + 10)
            elif ch in "0123456789":
                numeric += ch
            else:
                return False
        try:
            return int(numeric) % 97 == 1
        except ValueError:
            return False

    elif algorithm == "nhs_mod11":
        # NHS Number: 10 digits, weights 10..2, check digit is last
        digits = s.replace(" ", "")
        if len(digits) != 10 or not _is_ascii_digits(digits):
            return False
        total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
        remainder = total % 11
        check = 11 - remainder
        if check == 11:
            check = 0
        if check == 10:
            return False  # invalid NHS number
        return check == int(digits[9])

    elif algorithm == "cpf_mod11":
        # Brazilian CPF: 11 digits, two check digits
        digits = s.replace(".", "").replace("-", "")
        if len(digits) != 11 or not _is_ascii_digits(digits):
            return False
        if len(set(digits)) == 1:
            return False  # all same digit is invalid
        # First check digit
        total = sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1)))
        r = total % 11
        c1 = 0 if r < 2 else 11 - r
        if c1 != int(digits[9]):
            return False
        # Second check digit
        total = sum(int(d) * w for d, w in zip(digits[:10], range(11, 1, -1)))
        r = total % 11
        c2 = 0 if r < 2 else 11 - r
        return c2 == int(digits[10])

    elif algorithm == "vin_mod11":
        # VIN: 17-char alphanumeric, position 9 is check digit
        TRANSLITERATION = {
            'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8,
            'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'P': 7, 'R': 9,
            'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9,
        }
        POSITION_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
        if len(s) != 17:
            return False
        # I, O, Q are not valid VIN characters
        if any(ch in ('I', 'O', 'Q') for ch in s):
            return False
        total = 0
        for i, ch in enumerate(s):
            if i == 8:
                continue  # skip check digit position
            if ch.isdigit():
                val = int(ch)
            elif ch in TRANSLITERATION:
                val = TRANSLITERATION[ch]
            else:
                return False
            total += val * POSITION_WEIGHTS[i]
        remainder = total % 11
        check_char = str(remainder) if remainder < 10 else 'X'
        return s[8] == check_char

    elif algorithm == "isrc_luhn":
        # ISRC: CC-XXX-YY-NNNNN — validate structural format (Luhn not standard for ISRC)
        # ISRC uses format validation rather than Luhn; we validate the standard format
        import re as _re
        isrc_clean = s.replace("-", "")
        return bool(_re.match(r'^[A-Z]{2}[A-Z0-9]{3}\d{7}$', isrc_clean))

    else:
        # v2.3.25 (Pilot 2026-04-29): tighten the unknown-algorithm
        # fallback from pass-through to fail-closed. Pre-fix: a typo'd
        # YAML key (e.g. `ibn_mod97` for `iban_mod97`) silently passed
        # every record with a warning in the log. The warning could be
        # missed; the records flowed downstream as if validated. Now
        # the rule fails records under an unknown algorithm so the
        # error surfaces at the place a regulated firm actually
        # watches — the validation response, not the engine log.
        # Log the warning AND return False.
        logger.warning(
            "Unknown checksum algorithm '%s' — rule fails closed. "
            "Check the contract YAML for typos in checksum_algorithm "
            "(supported: mod10_gs1, iban_mod97, isin_luhn, lei_mod97, "
            "vin_mod11, isrc_luhn, cpf_mod11, nhs_mod11).",
            algorithm,
        )
        return False


def _semver_tuple(v):
    """Parse a semantic version string into a comparable tuple."""
    parts = str(v).lstrip('v').split('.')
    return tuple(int(x) for x in parts[:3])


# ── Single-record rule handlers ─────────────────────────────────────────
# Each handler: (value, rule, record) -> Optional[str]
# Returns error message on failure, None on pass.
#
# CRT170/J3: format-class rules (regex, min, max, range, *_length,
# date_format, compare, checksum, lookup, geospatial_bounds, age_match,
# cross_field_range, conditional_lookup) skip when the field is absent
# (None or whitespace-only string). The presence-class rules (not_empty,
# not_empty_string, required_if) are the single catcher for absence — this prevents
# double-firing on missing fields.

# Presence-class rules are the ONLY rules that fire on an absent or blank
# value; every other rule skips it (D6, normative — review round 2 B2 made
# this structural rather than per-handler). Shared with the linter.
_PRESENCE_RULE_TYPES = frozenset({"not_empty", "not_empty_string", "required_if"})
# Rules that must still see an absent value: presence rules, conditional_value
# ("must equal X" — absent is a violation on both engines) and unique (a
# set-based rule evaluated on the batch frame, never on one value).
_ABSENT_EXEMPT_RULE_TYPES = _PRESENCE_RULE_TYPES | frozenset({"conditional_value", "unique"})


def _is_field_absent(value) -> bool:
    """Field has no meaningful value to characterize for format-class rules."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _batch_absent(val) -> bool:
    """Batch-path twin of _is_field_absent: None, NaN, or a blank string.

    The two must agree or validate_record and validate_batch drift on blank
    values (D6) — the conformance generator refuses to emit a corpus when
    they do.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    return isinstance(val, str) and val.strip() == ""


def _check_not_empty(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return rule.error_message
    return None


def _check_not_empty_string(value, rule: Rule, record: dict | None = None) -> str | None:
    """Presence + JSON-string type guard (CRT180 contract-format conformance).

    Unlike not_empty, a non-string value is never coerced: 0, false, [] and
    {} are rejected — under this rule's own error code, with a typed
    message — rather than silently stringified into "0" / "False" / "[]"
    and passed. Absent, null and
    whitespace-only values fail with the rule's own message.
    """
    if value is None:
        return rule.error_message
    if not isinstance(value, str):
        # Reported under the rule's own error code (not OPENDQV_TYPE_MISMATCH):
        # the type guard IS this rule's assertion, so the code stays routable
        # to the rule — matches the managed engine, which shipped this type
        # first (cross-engine fixture run, CRT180).
        return _not_empty_string_type_message(rule.field, value)
    if value.strip() == "":
        return rule.error_message
    return None


def _not_empty_string_type_message(field: str, value) -> str:
    return (
        f'field "{field}" must be a JSON string, got {_json_type_name(value)} — '
        "send the value as a quoted string to preserve canonical form "
        "(e.g. leading zeros: \"00012345\", not 12345)"
    )


def _json_type_name(value) -> str:
    """JSON type name of a Python value (string/number/boolean/array/object/null).

    Engine-generated messages name the JSON type, never the Python type, so
    the wording is the same whichever engine produced it.
    """
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null" if value is None else type(value).__name__


def _check_regex(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.pattern:
        return rule.error_message  # misconfigured — fail visible rather than silently pass
    if _is_field_absent(value):
        return None
    str_val = str(value) if value is not None else ""
    pattern = _BUILTIN_PATTERNS.get(rule.pattern, rule.pattern)
    compiled = rule.compiled_pattern or re.compile(pattern)
    matched = _safe_match(compiled, str_val)
    if rule.negate:
        if matched:
            return rule.error_message
    else:
        if not matched:
            return rule.error_message
    return None


# v2.3.23 outside-review #3 (Sonnet aec401d0381905d97):
# Type-mismatch sentinel for numeric-coercing rules. Persona B
# 2026-04-28 caught: `price: "not a number"` (str) fired
# `price_min "must be >= 0"` because the previous handler caught
# the float() ValueError and returned the rule's value-violation
# error_message. Producer fixes the wrong thing — looks at numeric
# values that already pass instead of fixing the type contract.
#
# Fix: checkers return _TYPE_MISMATCH_PREFIX + generated_message
# when the value can't be coerced to numeric. Caller in
# validate_record / validate_batch detects the prefix, swaps the
# error_code to OPENDQV_TYPE_MISMATCH, strips the prefix.
#
# Generated message includes field name and Python type but NEVER
# the value itself (PII risk on free-text fields).
_TYPE_MISMATCH_PREFIX = "__OPENDQV_TYPE_MISMATCH__::"
# #145: a cross-field rule that fails because its counterpart is absent or
# blank (D10) is marked so remediation loops can tell it from a real
# comparison failure. Same code, same severity, same message; one extra
# structured key (`counterpart_missing: true`) on the entry, both paths.
_COUNTERPART_MISSING_PREFIX = "__OPENDQV_COUNTERPART_MISSING__::"


def _type_mismatch_msg(rule: Rule, value) -> str:
    # A NaN or infinity is not a valid finite measurement — the canonical
    # "corrupt number" a broken upstream pipeline emits. It parses as a float
    # but must never satisfy a numeric bound (NaN compares False to every
    # bound; ±inf slips single-sided min/max rules), so it surfaces here as a
    # type/domain mismatch with an accurate, non-generic message.
    if isinstance(value, float) and not math.isfinite(value):
        got = "NaN" if math.isnan(value) else "infinity"
        return (
            f"{_TYPE_MISMATCH_PREFIX}"
            f"{rule.type} rule on field '{rule.field}' expected a finite "
            f"numeric value, got {got}"
        )
    return (
        f"{_TYPE_MISMATCH_PREFIX}"
        f"{rule.type} rule on field '{rule.field}' expected numeric "
        f"value, got {_json_type_name(value)}"
    )


def _is_numeric_value(value) -> bool:
    """True if value is int/float (and not bool, which subclasses int).

    bool is excluded: True/False as a numeric is almost always a type
    contract violation, not a 0/1 the producer intended.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_min(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    if not _is_numeric_value(value):
        # Strings that happen to parse as numeric ("28.50") still go
        # through float() — preserves CSV-pipeline ergonomics. Only
        # genuinely non-numeric types (dict, list, non-parseable str)
        # surface as type mismatch.
        try:
            float(value)
        except (TypeError, ValueError):
            return _type_mismatch_msg(rule, value)
    try:
        v = float(value)
        if not math.isfinite(v):
            return _type_mismatch_msg(rule, value)
        if v < rule.min_value:
            return rule.error_message
    except (TypeError, ValueError):
        return _type_mismatch_msg(rule, value)
    return None


def _check_max(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    if not _is_numeric_value(value):
        try:
            float(value)
        except (TypeError, ValueError):
            return _type_mismatch_msg(rule, value)
    try:
        v = float(value)
        if not math.isfinite(v):
            return _type_mismatch_msg(rule, value)
        if v > rule.max_value:
            return rule.error_message
    except (TypeError, ValueError):
        return _type_mismatch_msg(rule, value)
    return None


def _check_range(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    if not _is_numeric_value(value):
        try:
            float(value)
        except (TypeError, ValueError):
            return _type_mismatch_msg(rule, value)
    try:
        v = float(value)
        if not math.isfinite(v):
            return _type_mismatch_msg(rule, value)
        if rule.min_value is not None and v < rule.min_value:
            return rule.error_message
        if rule.max_value is not None and v > rule.max_value:
            return rule.error_message
    except (TypeError, ValueError):
        return _type_mismatch_msg(rule, value)
    return None


def _length_type_message(field: str, value) -> str:
    return (
        f'length rule on field "{field}" expects a JSON string, got {_json_type_name(value)} — '
        "use min/max for numeric bounds, or send the value as a quoted string"
    )


def _check_min_length(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    if not isinstance(value, str):
        return _length_type_message(rule.field, value)  # D9: refuse, never coerce
    str_val = str(value)
    if len(str_val) < (rule.min_length or 0):
        return rule.error_message
    return None


def _check_max_length(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    if not isinstance(value, str):
        return _length_type_message(rule.field, value)  # D9: refuse, never coerce
    str_val = str(value)
    if len(str_val) > (rule.max_length or 99999):
        return rule.error_message
    return None


def _human_to_strptime(fmt: str) -> str:
    # Accept either Java/human-readable patterns (YYYY-MM-DD) or strftime
    # codes (%Y-%m-%d). MM is month before any HH; minute after.
    if "%" in fmt:
        return fmt
    out = []
    i = 0
    seen_h = False
    while i < len(fmt):
        chunk2 = fmt[i:i + 2]
        chunk4 = fmt[i:i + 4]
        if chunk4 == "YYYY":
            out.append("%Y")
            i += 4
        elif chunk2 == "YY":
            out.append("%y")
            i += 2
        elif chunk2 == "DD":
            out.append("%d")
            i += 2
        elif chunk2 == "HH":
            out.append("%H")
            i += 2
            seen_h = True
        elif chunk2 == "MM":
            out.append("%M" if seen_h else "%m")
            i += 2
        elif chunk2 == "SS":
            out.append("%S")
            i += 2
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


def _check_date_format(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    str_val = str(value)
    # Honour the contract's declared format strictly. When no format is
    # declared, default to ISO 8601 (date or datetime) — never accept
    # locale-ambiguous formats like DD/MM/YYYY or MM/DD/YYYY by default.
    # The persona-driven CRT173 finding: prior behaviour silently accepted
    # "26/04/2026" against rules whose error_message claimed "YYYY-MM-DD",
    # which is the worst kind of false-pass — the rule lied about what it
    # enforces.
    if rule.format:
        formats_to_try = [_human_to_strptime(rule.format)]
    else:
        formats_to_try = ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]
    for fmt in formats_to_try:
        try:
            datetime.strptime(str_val, fmt)
            return None
        except ValueError:
            continue
    return rule.error_message


def _check_unique(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    # Single-record mode cannot check uniqueness — skip silently
    return None


def _check_compare(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.compare_to or not rule.compare_op:
        logger.warning("compare rule '%s' missing compare_to or compare_op", rule.name)
        return None
    if _is_field_absent(value):
        return None
    if rule.compare_to in ("today", "now"):
        if rule.compare_to == "today":
            other = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        else:
            other = datetime.now(timezone.utc).isoformat()
    else:
        other = (record or {}).get(rule.compare_to)
        if _is_field_absent(other):
            return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: a missing/blank counterpart IS a comparison failure (both engines)

    # v2.3.20 Cluster C (P1.2): same_date compare_op extracts the
    # YYYY-MM-DD portion from each side before comparing. Catches the
    # MiFIR Article 26 / RTS 22 invariant that trade_date must equal the
    # date portion of execution_timestamp — a check the v2.3.17 Q14 rule
    # claimed to enforce but actually only validated as a regex on
    # trade_date alone. Reviewer's exact repro: trade_date=2024-01-15
    # with execution_timestamp=2026-04-25T... PASSED a "matches" rule.
    # Slice [:10] works for "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS..." —
    # both yield the same date portion. Bare-string compare without a
    # datetime parse keeps the implementation honest and small.
    if rule.compare_op == "same_date":
        a_str = str(value)[:10]
        b_str = str(other)[:10]
        # Sanity: only proceed if both look like YYYY-MM-DD shape, else
        # the rule isn't applicable and we return None (the dedicated
        # format rule on the field is responsible for shape).
        import re as _re
        date_re = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if not (date_re.match(a_str) and date_re.match(b_str)):
            return None
        return None if a_str == b_str else rule.error_message

    try:
        a, b = float(value), float(other)
    except (TypeError, ValueError):
        try:
            a = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(other).replace("Z", "+00:00"))
            if isinstance(a, datetime) and a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            if isinstance(b, datetime) and b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            if getattr(rule, 'algorithm', None) == 'semver':
                try:
                    a = _semver_tuple(value)
                    b = _semver_tuple(other)
                except (ValueError, TypeError):
                    a, b = str(value), str(other)
            else:
                a, b = str(value), str(other)
    op_fn = _COMPARE_OPS.get(rule.compare_op)
    if op_fn is None:
        logger.warning("compare rule '%s' has unknown compare_op '%s'", rule.name, rule.compare_op)
        return None
    if not op_fn(a, b):
        return rule.error_message
    return None


def _check_required_if(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.required_if:
        return None
    trigger_field = rule.required_if.get("field")
    trigger_value = str(rule.required_if.get("value", ""))
    actual = str((record or {}).get(trigger_field, ""))
    if actual == trigger_value:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return rule.error_message
    return None


def _check_allowed_values(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.allowed_values:
        return None
    if _is_field_absent(value):
        return None  # D6: blank is absent — presence rules are the single catcher
    allowed = [str(v) for v in rule.allowed_values]
    if str(value) not in allowed:
        return rule.error_message
    return None


def _check_lookup(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.lookup_file:
        logger.warning("lookup rule '%s' missing lookup_file", rule.name)
        return None
    if _is_field_absent(value):
        return None  # D6: blank is absent — presence rules are the single catcher
    try:
        if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
            ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
            valid_values = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
        else:
            valid_values = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as exc:
        logger.error("lookup rule '%s' could not load '%s': %s", rule.name, rule.lookup_file, exc)
        return rule.error_message
    if rule.all_of and isinstance(value, list):
        for item in value:
            if str(item) not in valid_values:
                return rule.error_message
    elif str(value) not in valid_values:
        return rule.error_message
    return None


def _checksum_type_message(field: str, value) -> str:
    return (
        f'checksum field "{field}" must be a JSON string, got {_json_type_name(value)} — '
        "send the identifier as a quoted string to preserve canonical form (e.g. leading zeros)"
    )


def _check_checksum(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.checksum_algorithm:
        logger.warning("checksum rule '%s' missing checksum_algorithm", rule.name)
        return None
    if _is_field_absent(value):
        return None
    if not isinstance(value, str):
        return _checksum_type_message(rule.field, value)  # D9 family: refuse, never coerce
    if not _validate_checksum(str(value), rule.checksum_algorithm):
        return rule.error_message
    return None


def _check_cross_field_range(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    rec = record or {}
    try:
        v = float(value)
        if rule.cross_min_field:
            low = rec.get(rule.cross_min_field)
            if _is_field_absent(low):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            if v < float(low):
                return rule.error_message
        if rule.cross_max_field:
            high = rec.get(rule.cross_max_field)
            if _is_field_absent(high):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            if v > float(high):
                return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


def _check_field_sum(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.sum_fields or rule.sum_equals is None:
        logger.warning("field_sum rule '%s' missing sum_fields or sum_equals", rule.name)
        return None
    rec = record or {}
    try:
        if any(_is_field_absent(rec.get(f)) for f in rule.sum_fields):
            return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: absent/blank counterpart → the rule fails
        total = sum(float(rec.get(f)) for f in rule.sum_fields)
        tolerance = rule.sum_tolerance if rule.sum_tolerance is not None else 0.0
        if abs(total - rule.sum_equals) > tolerance:
            return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


def _check_forbidden_if(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.forbidden_if:
        return None
    trigger_field = rule.forbidden_if.get("field")
    trigger_value = str(rule.forbidden_if.get("value", ""))
    actual = str((record or {}).get(trigger_field, ""))
    if actual == trigger_value:
        if value is not None and not (isinstance(value, str) and value.strip() == ""):
            return rule.error_message
    return None


def _check_conditional_value(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if rule.must_equal is None:
        return None
    if value is None or str(value) != str(rule.must_equal):
        return rule.error_message
    return None


def _check_date_diff(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.date_diff_field:
        logger.warning("date_diff rule '%s' missing date_diff_field", rule.name)
        return None
    if _is_field_absent(value):
        return None  # field absent or blank (D6) — skip date_diff; required check is a separate rule
    other_val = (record or {}).get(rule.date_diff_field)
    if _is_field_absent(other_val):
        return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: a missing/blank counterpart IS a date_diff failure (both engines)
    try:
        d1 = _parse_date(value)
        d2 = _parse_date(other_val)
        delta = _delta_days(d1, d2)  # signed, fractional: positive if d1 is later

        unit = rule.date_diff_unit or "days"
        if unit == "years":
            # Signed in BOTH units (2.7.0, matching the managed engine's
            # 2026-06-12 reading): "end must be ≥ 1 year after start" must fail
            # when end is years BEFORE start; abs() used to hide that.
            diff = delta / 365.25
        else:
            diff = delta

        if rule.min_value is not None and diff < rule.min_value:
            return rule.error_message
        if rule.max_value is not None and diff > rule.max_value:
            return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


def _check_ratio_check(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.ratio_numerator or not rule.ratio_denominator:
        logger.warning("ratio_check rule '%s' missing ratio_numerator or ratio_denominator", rule.name)
        return None
    rec = record or {}
    try:
        num_raw = rec.get(rule.ratio_numerator)
        den_raw = rec.get(rule.ratio_denominator)
        if _is_field_absent(num_raw) or _is_field_absent(den_raw):
            return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: absent/blank counterpart → the rule fails
        num = float(num_raw)
        den = float(den_raw)
        if den == 0:
            return rule.error_message
        ratio = num / den
        if rule.min_value is not None and ratio < rule.min_value:
            return rule.error_message
        if rule.max_value is not None and ratio > rule.max_value:
            return rule.error_message
    except (TypeError, ValueError, ZeroDivisionError):
        return rule.error_message
    return None


def _check_conditional_lookup(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.lookup_file:
        logger.warning("conditional_lookup rule '%s' missing lookup_file", rule.name)
        return None
    if _is_field_absent(value):
        return None
    try:
        if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
            ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
            valid_values = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
        else:
            valid_values = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as exc:
        logger.error("conditional_lookup rule '%s' could not load '%s': %s", rule.name, rule.lookup_file, exc)
        return rule.error_message
    if str(value) not in valid_values:
        return rule.error_message
    return None


def _check_geospatial_bounds(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    rec = record or {}
    try:
        lat = float(value)

        if rule.geo_min_lat is not None and lat < rule.geo_min_lat:
            return rule.error_message
        if rule.geo_max_lat is not None and lat > rule.geo_max_lat:
            return rule.error_message

        if rule.geo_lon_field:
            lon_val = rec.get(rule.geo_lon_field)
            if _is_field_absent(lon_val):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            lon = float(lon_val)
            if rule.geo_min_lon is not None and lon < rule.geo_min_lon:
                return rule.error_message
            if rule.geo_max_lon is not None and lon > rule.geo_max_lon:
                return rule.error_message

        if not (-90 <= lat <= 90):
            return rule.error_message
        if rule.geo_lon_field:
            lon_val = rec.get(rule.geo_lon_field)
            if lon_val is not None:
                lon = float(lon_val)
                if not (-180 <= lon <= 180):
                    return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


def _check_age_match(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.dob_field:
        logger.warning("age_match rule '%s' missing dob_field", rule.name)
        return None
    if _is_field_absent(value):
        return None
    dob_val = (record or {}).get(rule.dob_field)
    if _is_field_absent(dob_val):
        return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: absent/blank counterpart → the rule fails (both engines)
    try:
        declared = int(float(value))
        dob = datetime.strptime(str(dob_val), "%Y-%m-%d")
        today = datetime.now(timezone.utc)
        computed = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        tol = rule.age_tolerance if rule.age_tolerance is not None else 0
        if not (computed - tol <= declared <= computed):
            return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


# ── Dispatch table — single source of truth for known rule types ────────
_RULE_HANDLERS: dict[str, Callable] = {
    "not_empty": _check_not_empty,
    "not_empty_string": _check_not_empty_string,
    "regex": _check_regex,
    "min": _check_min,
    "max": _check_max,
    "range": _check_range,
    "min_length": _check_min_length,
    "max_length": _check_max_length,
    "date_format": _check_date_format,
    "unique": _check_unique,
    "compare": _check_compare,
    "required_if": _check_required_if,
    "allowed_values": _check_allowed_values,
    "lookup": _check_lookup,
    "checksum": _check_checksum,
    "cross_field_range": _check_cross_field_range,
    "field_sum": _check_field_sum,
    "forbidden_if": _check_forbidden_if,
    "conditional_value": _check_conditional_value,
    "date_diff": _check_date_diff,
    "ratio_check": _check_ratio_check,
    "conditional_lookup": _check_conditional_lookup,
    "geospatial_bounds": _check_geospatial_bounds,
    "age_match": _check_age_match,
}


# 2.8.0: the dispatch table and the model's closed set must agree, or a type
# could exist in one and not the other (issue #163). Fails at import.
assert frozenset(_RULE_HANDLERS) == RULE_TYPES, (
    f"_RULE_HANDLERS/RULE_TYPES drift: only-handlers={sorted(set(_RULE_HANDLERS) - RULE_TYPES)} "
    f"only-types={sorted(RULE_TYPES - set(_RULE_HANDLERS))}"
)

def _check_rule(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    """
    Check a single value against a single rule.
    Returns the error message string if failed, None if passed.
    record is required for cross-field rule types (compare, required_if, condition).
    """
    if rule.cached_has_condition and not _check_condition(rule, record):
        return None  # condition not met — rule is inapplicable for this record

    # D6, structural: no non-presence rule ever fires on an absent/blank value.
    # Handlers may still guard individually; this is the guarantee.
    if rule.type not in _ABSENT_EXEMPT_RULE_TYPES and _is_field_absent(value):
        return None

    handler = _RULE_HANDLERS.get(rule.type)
    if handler is None:
        # Unreachable for a Rule built through the model (RULE_TYPES gate);
        # kept as a hard failure rather than a silent pass for any bypass.
        raise ValueError(f"Unknown rule type '{rule.type}' for rule '{rule.name}'")
    return handler(value, rule, record)


def _check_lookup_path_safe(file_path: str) -> Path:
    """
    SEC-002: Path traversal protection for local lookup_file paths.

    Resolves the path and verifies it lies within the configured contracts
    directory. Raises ValueError on traversal attempts (e.g. ../../etc/passwd).
    """
    # Null byte injection — Linux pathlib raises this automatically; Windows does not.
    if "\x00" in file_path:
        raise ValueError("null byte in lookup_file path — rejected")
    import opendqv.config as _cfg
    base = Path(_cfg.CONTRACTS_DIR).resolve()
    # Support both absolute paths and paths relative to CONTRACTS_DIR
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    # Ensure the resolved path is under the allowed base directory
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f"lookup_file path '{file_path}' resolves outside the contracts "
            f"directory — path traversal rejected"
        )
    return resolved


@lru_cache(maxsize=256)
def _load_lookup_set(file_path: str, lookup_field: str) -> frozenset:
    """
    Load a set of valid values from a local reference file. Cached per (file_path, lookup_field).

    If lookup_field is non-empty, treats the file as CSV and reads that column.
    Otherwise, reads one value per line.

    Call _load_lookup_set.cache_clear() to invalidate after file updates.
    """
    path = _check_lookup_path_safe(file_path)
    values: set = set()
    if lookup_field:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                val = row.get(lookup_field)
                if val is not None:
                    values.add(val.strip())
    else:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    values.add(stripped)
    return frozenset(values)


# ── HTTP lookup cache ──────────────────────────────────────────────────
# Stores (frozenset, expires_at) keyed by (url, lookup_field, cache_ttl).
# Thread-safe: protected by _http_lookup_lock.
_http_lookup_cache: dict = {}
_http_lookup_lock = threading.Lock()
_HTTP_LOOKUP_DEFAULT_TTL = 300  # seconds


class _SSRFSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """SEC-008: re-validate every redirect hop before following it.

    urllib's default opener follows 3xx redirects transparently, so validating
    only the initial URL is not enough — a public URL that the guard approves
    can 302-redirect to http://169.254.169.254/ (cloud metadata) or any
    RFC-1918 host, bypassing the check entirely. This handler runs the same
    assert_url_public guard on each Location before allowing the redirect; a
    private/reserved target raises ValueError, which propagates out of urlopen
    and is caught by the lookup handlers (fail-closed).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from .webhooks import assert_url_public
        assert_url_public(newurl, label="Lookup URL (redirect target)")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Opener that enforces the SSRF guard on redirects. Module-level so it is built
# once; stateless and thread-safe for concurrent lookup fetches.
_ssrf_safe_opener = urllib.request.build_opener(_SSRFSafeRedirectHandler)


class LookupAuthPolicyError(ValueError):
    """SEC-011: a lookup auth-header ${VAR} substitution violated policy.

    A subclass of ValueError so the single-record lookup handlers (which already
    catch ValueError and fail closed → error_message) treat it as a load failure.
    The batch handler catches it explicitly to fail closed too, distinct from the
    transient-infra errors it otherwise skips fail-open.
    """


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """SEC-011 / OWASP: refuse redirects for credential-bearing lookups.

    urllib re-sends the Authorization header across 3xx redirects, so an
    allowlisted host that 302s to any other host would forward the resolved
    secret onward, defeating the egress allowlist. For secret-bearing fetches we
    do not follow redirects at all — a legitimate auth lookup returns 200 with
    its list, not a redirect. Fail closed.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            "Redirect not permitted for an authenticated (secret-bearing) lookup",
            headers, fp,
        )


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)

# SEC-011: matches a single ${VAR} reference. Applied once (re.sub does not
# rescan its own replacements), so a resolved value cannot be re-expanded.
_LOOKUP_SECRET_REF_RE = re.compile(r'\$\{([^}]+)\}')

# Generic denial message. Deliberately does NOT echo the env var value, nor
# distinguish "var unset" from "var disallowed" — avoids an env-var existence
# oracle. The three policy reasons below are separate strings because each names
# operator-controlled POLICY (open-mode opt-in, name prefix, host allowlist),
# never the secret itself or whether any particular var is set.
_LOOKUP_SECRET_OPEN_MODE_MSG = (
    "SEC-011: lookup auth-header secret substitution is disabled under "
    "AUTH_MODE=open; set OPENDQV_ALLOW_LOOKUP_SECRETS to opt in"
)
_LOOKUP_SECRET_PREFIX_MSG = (
    "SEC-011: lookup auth-header may only reference env vars named with the "
    "OPENDQV_LOOKUP_ prefix"
)
_LOOKUP_SECRET_HOST_MSG = (
    "SEC-011: secret-bearing lookup URL host is not on "
    "OPENDQV_LOOKUP_EGRESS_ALLOWLIST (fail-closed)"
)


def _canonical_host(hostname: str) -> Optional[str]:
    """Canonicalise a hostname for strict allowlist comparison.

    Lowercases, strips a trailing dot, and IDNA-encodes so a Unicode homoglyph
    (e.g. Cyrillic 'а') or punycode form cannot masquerade as an ASCII host.
    Returns None if the host cannot be canonicalised (⇒ caller rejects).
    """
    if not hostname:
        return None
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return None
    try:
        # str.encode("idna") rejects empty labels / over-length / bad chars.
        return host.encode("idna").decode("ascii").lower()
    except Exception:
        return None


def _assert_lookup_host_allowed(url: str) -> None:
    """SEC-011 control #3: the URL host must be on the egress allowlist.

    Uses urlparse().hostname (so userinfo `user@evil.example` resolves to the
    real host, not the userinfo) and canonicalises both sides. Fail-closed:
    an empty allowlist rejects every secret-bearing lookup.
    """
    import opendqv.config as config

    parsed = urllib.parse.urlparse(url)
    # Defense-in-depth: reject userinfo (user@host). urllib's connect path
    # (http.client via the legacy host split) parses the netloc differently from
    # urlparse().hostname, so a "allowed.example@evil.example" form could diverge
    # between the host we check and the host that is dialled. Refuse it outright
    # rather than rely on the divergent form failing DNS.
    if parsed.username is not None or "@" in (parsed.netloc or ""):
        raise LookupAuthPolicyError(_LOOKUP_SECRET_HOST_MSG)

    allow = {_canonical_host(h) for h in config.LOOKUP_EGRESS_ALLOWLIST}
    allow.discard(None)
    host = _canonical_host(parsed.hostname or "")
    if host is None or host not in allow:
        raise LookupAuthPolicyError(_LOOKUP_SECRET_HOST_MSG)


def _resolve_lookup_auth_header(auth_header: Optional[str], url: str):
    """SEC-011: resolve a lookup auth header, enforcing the three-layer policy.

    Returns (resolved_header_or_None, is_secret_bearing). Raises
    LookupAuthPolicyError (fail-closed) on any policy violation. A header with no
    ${VAR} reference is a literal credential known to the contract author already
    — it carries no server secret, so it is returned unguarded (the SSRF guard
    still applies to the URL upstream).
    """
    if not auth_header:
        return None, False

    refs = _LOOKUP_SECRET_REF_RE.findall(auth_header)
    if not refs:
        return auth_header, False

    import opendqv.config as config

    # Control #2 — no trust boundary under AUTH_MODE=open.
    if config.IS_OPEN_MODE and not config.ALLOW_LOOKUP_SECRETS_IN_OPEN_MODE:
        raise LookupAuthPolicyError(_LOOKUP_SECRET_OPEN_MODE_MSG)

    # Control #1 — only OPENDQV_LOOKUP_-prefixed names may be substituted.
    for name in refs:
        if not name.startswith(config.LOOKUP_SECRET_ENV_PREFIX):
            raise LookupAuthPolicyError(_LOOKUP_SECRET_PREFIX_MSG)

    # Control #3 — destination host must be explicitly allowlisted.
    _assert_lookup_host_allowed(url)

    resolved = _LOOKUP_SECRET_REF_RE.sub(
        lambda m: os.environ.get(m.group(1), ""), auth_header
    )
    return resolved, True


def _load_http_lookup_set(url: str, lookup_field: str, cache_ttl: int, auth_header: Optional[str] = None) -> frozenset:
    """
    Fetch a set of valid values from an HTTP endpoint. Results are cached for cache_ttl seconds.

    The endpoint must return either:
      - A JSON array of strings:    ["val1", "val2", ...]
      - Newline-delimited plain text: one value per line

    lookup_field is ignored for HTTP endpoints (no CSV column support over HTTP).
    """
    import json as _json

    cache_key = (url, lookup_field, cache_ttl, auth_header)
    now = time.monotonic()

    with _http_lookup_lock:
        cached = _http_lookup_cache.get(cache_key)
        if cached is not None:
            values, expires_at = cached
            if now < expires_at:
                return values

    # SEC-008: SSRF guard. A lookup rule's URL comes from a contract author
    # (editor role), so it is attacker-influenced input exactly like a webhook
    # URL — without this check an `editor` could point a lookup at cloud
    # metadata (169.254.169.254), loopback, or RFC-1918 hosts and, via the
    # ${ENV} auth-header substitution below, exfiltrate server secrets to it.
    # Reuse the webhook dispatcher's guard so both surfaces enforce one policy.
    # Runs on every cache miss (including TTL refresh) to catch DNS rebinding.
    from .webhooks import assert_url_public
    assert_url_public(url, label="Lookup URL")

    # SEC-011: resolve + policy-gate the auth header BEFORE any network I/O.
    # Raises LookupAuthPolicyError (fail-closed) if substitution is disallowed.
    # Runs on every cache miss so a policy-violating config never populates the
    # cache and never fetches. A secret-bearing header additionally forbids
    # redirects (the credential must not be forwarded to a 3xx target).
    resolved_auth, is_secret_bearing = _resolve_lookup_auth_header(auth_header, url)

    # Fetch outside the lock to avoid holding it during network I/O
    try:
        headers = {"User-Agent": "OpenDQV-lookup/1.0"}
        if resolved_auth is not None:
            headers["Authorization"] = resolved_auth
        req = urllib.request.Request(url, headers=headers)
        _MAX_LOOKUP_BYTES = 10_485_760  # 10 MB
        # Secret-bearing lookups: no-redirect opener (OWASP — don't forward the
        # credential). Otherwise the SSRF-safe opener re-validates each hop.
        opener = _no_redirect_opener if is_secret_bearing else _ssrf_safe_opener
        with opener.open(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(_MAX_LOOKUP_BYTES + 1)
            if len(body) > _MAX_LOOKUP_BYTES:
                raise RuntimeError(f"HTTP lookup response from '{url}' exceeds 10 MB limit")
            body = body.decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP lookup fetch failed for '{url}': {exc}") from exc

    values: set = set()
    if "application/json" in content_type or body.lstrip().startswith("["):
        try:
            items = _json.loads(body)
            if isinstance(items, list):
                for item in items:
                    if item is not None:
                        values.add(str(item).strip())
        except _json.JSONDecodeError:
            # Fall through to newline parsing
            for line in body.splitlines():
                stripped = line.strip()
                if stripped:
                    values.add(stripped)
    else:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                values.add(stripped)

    result = frozenset(values)
    with _http_lookup_lock:
        _http_lookup_cache[cache_key] = (result, now + cache_ttl)
    return result


def _check_age(value, rule: Rule) -> Optional[str]:
    """Check min_age/max_age constraints. Runs after the type check passes.

    CRT170/J3: skip when field is absent — the not_empty/required_if rules
    are the catcher for absence."""
    if rule.min_age is None and rule.max_age is None:
        return None
    if _is_field_absent(value):
        return None
    try:
        dob = datetime.strptime(str(value), "%Y-%m-%d")
        today = datetime.now(timezone.utc)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if rule.min_age is not None and age < rule.min_age:
            return rule.error_message
        if rule.max_age is not None and age > rule.max_age:
            return rule.error_message
    except (ValueError, TypeError):
        return rule.error_message
    return None


# ── Batch validation (DuckDB) ───────────────────────────────────────

def validate_batch(
    records: list[dict],
    rules: list[Rule],
    contract_name: str = "",
    context: Optional[str] = None,
    sensitive_fields: Optional[list] = None,
    strict_schema: bool = False,
    declared_fields: set | None = None,
) -> dict:
    """
    Validate a batch of records using DuckDB for performance.

    Returns:
        {
            "summary": {"total": N, "passed": N, "failed": N, "error_count": N, "warning_count": N},
            "results": [
                {"index": 0, "valid": True, "errors": [], "warnings": []},
                ...
            ]
        }
    """
    if not records:
        return {
            "summary": {"total": 0, "passed": 0, "failed": 0, "error_count": 0, "warning_count": 0},
            "results": [],
        }

    total = len(records)
    df = pd.DataFrame(records)
    # CRT180 review B6 (K1): a rule whose field no record carries used to be
    # skipped entirely — a batch that omitted a required field validated
    # clean while each record single-validated was rejected. Materialise the
    # column as NULL so every rule sees exactly what validate_record sees:
    # presence-class rules fail, format-class rules skip the absent value.
    # Review round 2 B4: cross-field counterparts (compare_to, date_diff_field,
    # …) must be materialised too, or a batch missing the counterpart column
    # validates clean where the single path fails. declared_field_set collects
    # exactly the declared names; the sentinels are not columns.
    synthesised: set[str] = set()
    for _f in declared_field_set(rules) - {"today", "now"}:
        if _f not in df.columns:
            df[_f] = None
            synthesised.add(_f)
    df["__idx__"] = range(total)

    con = duckdb.connect()
    try:
        con.register("data", df)

        # Per-row results: index -> {"errors": [], "warnings": []}
        row_results = {i: {"errors": [], "warnings": []} for i in range(total)}

        # CRT180: strict schema — same per-record check as the single path.
        if strict_schema:
            for i, rec in enumerate(records):
                extra = _additional_properties_error(rec if isinstance(rec, dict) else {}, declared_fields)
                if extra:
                    row_results[i]["errors"].append(extra)

        for rule in rules:
            # (round-2 S7) every declared field is materialised above, so a
            # rule's field is always a column here; the old "skip if column
            # missing" branch was the K1 fail-open and is gone.

            # v2.3.23 outside-review #3: type-mismatch indices populated
            # by min/max/range branches of _batch_check_rule.
            failing_type_mismatches: dict[int, str] = {}
            failing_messages: dict[int, str] = {}
            failing_counterpart_missing: set[int] = set()   # #145
            try:
                failing_indices = _batch_check_rule(
                    con, df, rule, failing_type_mismatches=failing_type_mismatches,
                    records=records, failing_messages=failing_messages, synthesised=synthesised,
                    failing_counterpart_missing=failing_counterpart_missing)
            except Exception as e:
                # Log only rule metadata — never include record field values.
                logger.error("Error evaluating rule '%s' (field='%s'): %s", rule.name, rule.field, e)
                failing_indices = set(range(total))

            # Apply condition filter: exclude rows where the condition is not met.
            if rule.condition and failing_indices:
                cond_field = rule.condition.get("field", "")
                if "present" in rule.condition:
                    # #144: presence judged from the raw record (D6 absence reading),
                    # exactly as _check_condition does on the single path.
                    want = bool(rule.condition["present"])
                    eligible = {
                        i for i in range(total)
                        if (not _is_field_absent((records[i] if records is not None else {}).get(cond_field))) == want
                    }
                    failing_indices = failing_indices & eligible
                if cond_field in df.columns:
                    cond_series = df[cond_field].astype(str)
                    if "value" in rule.condition:
                        # Keep only rows where condition field == value
                        eligible = set(df.index[cond_series == str(rule.condition["value"])])
                        failing_indices = failing_indices & eligible
                    elif "not_value" in rule.condition:
                        # Exclude rows where condition field == not_value
                        excluded = set(df.index[cond_series == str(rule.condition["not_value"])])
                        failing_indices = failing_indices - excluded
                elif "value" in rule.condition:
                    # condition field absent from every record: actual == "" on the
                    # single path, so `value: X` never matches → nothing fails.
                    failing_indices = set()

            entry_template = {
                "field": rule.field,
                "rule": rule.name,
                "message": rule.error_message,
                "severity": rule.severity.value,
                # CRT170/J4: reuse the cached, rule-instance-shaped code so
                # the batch path and single-record path always agree.
                "error_code": rule.cached_error_code,
            }

            for idx in failing_indices:
                # v2.3.23 outside-review #3: per-row type-mismatch override.
                # If this index was flagged as a type mismatch by the min/
                # max/range branch, swap the error_code + message to the
                # type-mismatch shape. Other rows on the same rule (real
                # value violations) keep the rule's own code.
                if idx in failing_type_mismatches:
                    raw_msg = failing_type_mismatches[idx]
                    if raw_msg.startswith(_TYPE_MISMATCH_PREFIX):
                        type_msg = raw_msg[len(_TYPE_MISMATCH_PREFIX):]
                    else:
                        type_msg = raw_msg
                    row_entry = {
                        "field": rule.field,
                        "rule": rule.name,
                        "message": type_msg,
                        "severity": rule.severity.value,
                        "error_code": "OPENDQV_TYPE_MISMATCH",
                    }
                elif idx in failing_messages:
                    row_entry = {**entry_template, "message": failing_messages[idx]}
                else:
                    row_entry = entry_template
                if idx in failing_counterpart_missing:
                    row_entry = {**row_entry, "counterpart_missing": True}   # #145
                if rule.severity == Severity.ERROR:
                    row_results[idx]["errors"].append(row_entry)
                else:
                    row_results[idx]["warnings"].append(row_entry)
    finally:
        con.close()

    # Build results
    results = []
    passed = 0
    total_errors = 0
    total_warnings = 0
    rule_failure_counts: dict = {}  # rule_name → count of records failing that rule

    fields_validated = [rule.field for rule in rules]
    for i in range(total):
        r = row_results[i]
        valid = len(r["errors"]) == 0
        if valid:
            passed += 1
        total_errors += len(r["errors"])
        total_warnings += len(r["warnings"])
        for entry in r["errors"] + r["warnings"]:
            rule_name = entry["rule"]
            rule_failure_counts[rule_name] = rule_failure_counts.get(rule_name, 0) + 1
        results.append({
            "index": i,
            "valid": valid,
            "errors": r["errors"],
            "warnings": r["warnings"],
        })

        # TRACE_LOG — write per-record audit entry if enabled
        failed_rule_fields = [e["field"] for e in r["errors"] + r["warnings"]]
        write_trace_entry(
            contract_name=contract_name,
            context=context,
            record_index=i,
            valid=valid,
            error_count=len(r["errors"]),
            warning_count=len(r["warnings"]),
            fields_validated=fields_validated,
            sensitive_fields=sensitive_fields or [],
            failed_rules=failed_rule_fields,
        )

    return {
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "error_count": total_errors,
            "warning_count": total_warnings,
            "rule_failure_counts": rule_failure_counts,
        },
        "results": results,
    }


# Rule types with a native DuckDB/pandas branch in _batch_check_rule_inner.
# Anything else goes through the per-record fallback (single-path handler).
_BATCH_BRANCH_TYPES = frozenset({
    "allowed_values", "checksum", "compare", "conditional_value", "cross_field_range", "date_diff",
    "date_format", "field_sum", "forbidden_if", "geospatial_bounds", "lookup", "max_length", "max",
    "min_length", "min", "not_empty_string", "not_empty", "range", "ratio_check", "regex",
    "required_if", "unique",
})


def _batch_check_rule(con, df: pd.DataFrame, rule: Rule, failing_type_mismatches: dict | None = None, records: list[dict] | None = None, failing_messages: dict | None = None, synthesised: set | None = None, failing_counterpart_missing: set | None = None) -> set[int]:
    """Batch twin of _check_rule with the same structural guarantee (D6):
    rows whose value for the rule's field is absent or blank never fail a
    non-presence rule, whatever the per-type branch did. Set-based rules
    (unique) on a synthesised column are skipped — a column nobody sent
    cannot carry duplicates (review round 2, B2/B5)."""
    synthesised = synthesised or set()
    if rule.type == "unique" and rule.field in synthesised:
        return set()  # a column nobody sent cannot carry duplicates
    failing = _batch_check_rule_inner(con, df, rule, failing_type_mismatches, records, failing_messages, synthesised, failing_counterpart_missing)
    if rule.type not in _ABSENT_EXEMPT_RULE_TYPES and rule.field and rule.field in df.columns:
        # Judge absence from the RAW record when available: pandas stores a
        # missing key and an explicit NaN identically, and an explicit NaN/inf
        # on a numeric rule must stay a rejection (test_crt177).
        if records is not None:
            absent = {i for i in range(len(df)) if rule.field not in records[i] or _is_field_absent(records[i].get(rule.field))}
        else:
            col = df[rule.field]
            absent = {i for i in range(len(df)) if _batch_absent(col.iloc[i])}
        if absent:
            failing = {i for i in failing if i not in absent}
            for d in (failing_type_mismatches, failing_messages):
                if d:
                    for i in absent:
                        d.pop(i, None)
    return failing


def _batch_check_rule_inner(con, df: pd.DataFrame, rule: Rule, failing_type_mismatches: dict | None = None, records: list[dict] | None = None, failing_messages: dict | None = None, synthesised: set | None = None, failing_counterpart_missing: set | None = None) -> set[int]:
    """Run a single rule against the batch via DuckDB. Returns set of failing row indices.

    v2.3.23 outside-review #3 (Sonnet aec401d0381905d97): for numeric
    rules (min/max/range), the optional `failing_type_mismatches` dict
    is populated as `{idx: type_mismatch_message}` so the caller can
    swap the error_code to OPENDQV_TYPE_MISMATCH on those rows. Other
    rule types are unaffected.

    `records` is the original list of dicts (row idx aligned with df).
    The numeric branches read the raw value from it rather than the
    DataFrame cell: pandas collapses a missing key AND an explicit NaN
    both to np.nan, which would make an absent optional numeric field
    (legitimately skipped) indistinguishable from a corrupt NaN value
    (must be rejected). Reading the original dict restores the single-
    record semantics exactly — missing key → None → absent → pass;
    explicit NaN/inf → rejected as non-finite.
    """
    synthesised = synthesised or set()
    field = rule.field
    failing = set()
    if failing_type_mismatches is None:
        failing_type_mismatches = {}
    if failing_messages is None:
        failing_messages = {}
    if failing_counterpart_missing is None:
        failing_counterpart_missing = set()
    if rule.type not in _BATCH_BRANCH_TYPES:
        # Round-2 pattern-closer: a rule type with no native batch branch is
        # evaluated per record with the single-path handler, so single/batch
        # parity holds by construction for every present and future type.
        # (age_match and conditional_lookup had no branch and passed silently.)
        for idx in range(len(df)):
            rec = records[idx] if records is not None else {c: df[c].iloc[idx] for c in df.columns if c != "__idx__"}
            msg = _check_rule(rec.get(field), rule, rec)
            if msg:
                failing.add(idx)
                if msg.startswith(_COUNTERPART_MISSING_PREFIX):   # #145
                    failing_counterpart_missing.add(idx)
                    msg = msg[len(_COUNTERPART_MISSING_PREFIX):]
                if msg != rule.error_message:
                    failing_messages[idx] = msg
        # no early return: the min_age/max_age add-on at the tail applies to
        # every rule type, exactly as validate_record applies _check_age after
        # the type check.

    def _orig_val(idx):
        # Numeric branches read the raw record value so a missing key
        # (absent → skip) stays distinct from an explicit NaN/inf
        # (non-finite → reject). Defensive fallback to the DataFrame cell
        # if records was not supplied (records is always passed today).
        if records is not None:
            return records[idx].get(field)
        return df[field].iloc[idx]

    if rule.type == "regex" and rule.pattern:
        # DuckDB doesn't support \w, \s, \d shorthand classes — fall back to Python.
        # Log at DEBUG so operators can identify which contracts use the slower path.
        logger.debug(
            "regex_python_fallback field=%s pattern=%r batch_size=%d rule=%s",
            field, rule.pattern, len(df), rule.name,
        )
        compiled = rule.compiled_pattern or re.compile(rule.pattern)
        for idx, val in enumerate(df[field]):
            # CRT170/J3: skip absent fields (not_empty is the catcher).
            if _batch_absent(val):  # D6: absent or blank
                continue
            str_val = str(val)
            if str_val.strip() == "":
                continue
            matched = _safe_match(compiled, str_val)
            if rule.negate:
                if matched:
                    failing.add(idx)
            else:
                if not matched:
                    failing.add(idx)

    elif rule.type == "min" and rule.min_value is not None:
        # v2.3.23 outside-review #3 (Sonnet aec401d0381905d97):
        # Python iteration so the type-mismatch sentinel from
        # _check_min flows to validate_batch's caller. The previous
        # DuckDB CAST-AS-DOUBLE either raised on the first non-numeric
        # row (failing the whole batch) or silently coerced — either
        # way, customers couldn't distinguish type mismatch from value
        # violation. Per-row check is slower (~10x) but correct;
        # v2.4 may reintroduce a hybrid TRY_CAST + per-row fallback.
        for idx in range(len(df)):
            failure = _check_min(_orig_val(idx), rule)
            if failure is not None:
                failing.add(idx)
                if failure.startswith(_TYPE_MISMATCH_PREFIX):
                    failing_type_mismatches[idx] = failure

    elif rule.type == "max" and rule.max_value is not None:
        for idx in range(len(df)):
            failure = _check_max(_orig_val(idx), rule)
            if failure is not None:
                failing.add(idx)
                if failure.startswith(_TYPE_MISMATCH_PREFIX):
                    failing_type_mismatches[idx] = failure

    elif rule.type == "range" and rule.min_value is not None and rule.max_value is not None:
        for idx in range(len(df)):
            failure = _check_range(_orig_val(idx), rule)
            if failure is not None:
                failing.add(idx)
                if failure.startswith(_TYPE_MISMATCH_PREFIX):
                    failing_type_mismatches[idx] = failure

    elif rule.type == "not_empty":
        # Read the raw record value and use the single path's absence test:
        # SQL TRIM strips spaces only, so a tab/newline-only value passed the
        # batch check while the single path rejected it (round-2 B2 matrix).
        for idx in range(len(df)):
            if _batch_absent(_orig_val(idx)):
                failing.add(idx)

    elif rule.type == "not_empty_string":
        # CRT180: presence + string-type guard. Read the raw record value —
        # pandas collapses types inside object columns. Non-string values fail
        # under the rule's own code (single-record parity).
        for idx in range(len(df)):
            val = _orig_val(idx)
            if _batch_absent(val):  # D6: absent or blank
                failing.add(idx)
            elif not isinstance(val, str):
                failing.add(idx)
                # rule's own code, typed message — identical to the single path (review B5)
                failing_messages[idx] = _not_empty_string_type_message(field, val)
            elif val.strip() == "":
                failing.add(idx)

    elif rule.type == "min_length" and rule.min_length is not None:
        # D9: a non-string value is refused under the rule's own code with a
        # typed message (never coerced to its decimal rendering) — same on
        # the single path. Strings go through the SQL length check.
        typed = set()
        for idx in range(len(df)):
            raw = _orig_val(idx)
            if raw is not None and not _batch_absent(raw) and not isinstance(raw, str):
                typed.add(idx)
                if failing_messages is not None:
                    failing_messages[idx] = _length_type_message(field, raw)
        failing.update(typed)
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NOT NULL AND TRIM(CAST("{field}" AS VARCHAR)) != '' AND LENGTH(CAST("{field}" AS VARCHAR)) < {rule.min_length}"""
        for r in con.execute(query).fetchall():
            if r[0] not in typed:
                failing.add(r[0])

    elif rule.type == "max_length" and rule.max_length is not None:
        # D9: a non-string value is refused under the rule's own code with a
        # typed message (never coerced to its decimal rendering) — same on
        # the single path. Strings go through the SQL length check.
        typed = set()
        for idx in range(len(df)):
            raw = _orig_val(idx)
            if raw is not None and not _batch_absent(raw) and not isinstance(raw, str):
                typed.add(idx)
                if failing_messages is not None:
                    failing_messages[idx] = _length_type_message(field, raw)
        failing.update(typed)
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NOT NULL AND TRIM(CAST("{field}" AS VARCHAR)) != '' AND LENGTH(CAST("{field}" AS VARCHAR)) > {rule.max_length}"""
        for r in con.execute(query).fetchall():
            if r[0] not in typed:
                failing.add(r[0])

    elif rule.type == "date_format":
        # Parity with the single-record path: honour the contract's
        # declared format strictly; default to ISO 8601 (date or datetime)
        # when no format is declared. Do NOT use TRY_CAST AS DATE — it
        # accepts locale-ambiguous formats like DD/MM/YYYY.
        params: dict = {}
        if rule.format:
            # CRT178 #9: the format string used to be f-strung into the query;
            # a quote in a caller-supplied `format` was SQL injection into an
            # engine with filesystem reach. Bind it instead.
            params["fmt"] = _human_to_strptime(rule.format)
            fmt_clause = f"TRY_STRPTIME(CAST(\"{field}\" AS VARCHAR), $fmt) IS NULL"
        else:
            fmt_clause = (
                f"TRY_STRPTIME(CAST(\"{field}\" AS VARCHAR), '%Y-%m-%d') IS NULL "
                f"AND TRY_STRPTIME(CAST(\"{field}\" AS VARCHAR), '%Y-%m-%dT%H:%M:%S') IS NULL"
            )
        query = f"""
            SELECT __idx__ FROM data
            WHERE "{field}" IS NOT NULL
              AND TRIM(CAST("{field}" AS VARCHAR)) != ''
              AND ({fmt_clause})
        """
        for r in con.execute(query, params).fetchall():
            failing.add(r[0])

    elif rule.type == "unique":
        if rule.group_by:
            # Unique within groups — duplicates within same group_by values
            # A synthesised (never sent) group_by column is not a valid group key —
            # the pre-existing fallback to global uniqueness applies (round-2 B5).
            valid_cols = [g for g in rule.group_by if g in df.columns and g not in synthesised]
            if valid_cols:
                # Single-pass grouping — O(n) instead of O(n²)
                groups: dict[tuple, list[int]] = defaultdict(list)
                for idx in range(len(df)):
                    field_val = str(df[field].iloc[idx])
                    group_key = tuple(str(df[g].iloc[idx]) if g in df.columns else "" for g in rule.group_by)
                    groups[(group_key, field_val)].append(idx)
                for indices in groups.values():
                    if len(indices) > 1:
                        failing.update(indices)
            else:
                # Fall back to global unique if no valid group_by cols
                dup_query = f"""
                    SELECT __idx__ FROM data WHERE "{field}" IN (
                        SELECT "{field}" FROM data GROUP BY "{field}" HAVING COUNT(*) > 1
                    )
                """
                for r in con.execute(dup_query).fetchall():
                    failing.add(r[0])
        else:
            # Original global unique
            dup_query = f"""
                SELECT __idx__ FROM data WHERE "{field}" IN (
                    SELECT "{field}" FROM data
                    GROUP BY "{field}" HAVING COUNT(*) > 1
                )
            """
            for r in con.execute(dup_query).fetchall():
                failing.add(r[0])

    elif rule.type == "compare" and rule.compare_to and rule.compare_op:
        # Cross-field comparison — fall back to Python to handle numeric/date/string types.
        is_temporal_sentinel = rule.compare_to in ("today", "now")
        if not is_temporal_sentinel and rule.compare_to not in df.columns:
            logger.warning("compare rule '%s' references missing field '%s'", rule.name, rule.compare_to)
        elif rule.compare_op == "same_date":
            # v2.3.22 post-release B1: mirror single-record same_date branch
            # (line 555) into the batch path. v2.3.20 added same_date for
            # the T+0 invariant on trade_date_matches_execution_date but
            # only patched the single-record path; the batch path silently
            # skipped because `same_date` is not in `_COMPARE_OPS`. Result:
            # any batch path through MiFIR reporting let T+0 violations
            # through. Persona B inside-view 2026-04-28 reproduced.
            import re as _re
            _date_re = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
            for idx in range(len(df)):
                a_raw = df[field].iloc[idx]
                if is_temporal_sentinel:
                    if rule.compare_to == "today":
                        b_raw = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    else:
                        b_raw = datetime.now(timezone.utc).isoformat()
                else:
                    b_raw = df[rule.compare_to].iloc[idx]
                if a_raw is None or (isinstance(a_raw, float) and pd.isna(a_raw)):
                    continue
                if not is_temporal_sentinel and _batch_absent(b_raw):
                    failing.add(idx)
                    failing_counterpart_missing.add(idx)   # D10 (#145: marked)
                    continue
                a_str = str(a_raw)[:10]
                b_str = str(b_raw)[:10]
                # Both sides must look like YYYY-MM-DD; otherwise the
                # date-format rule on the field is responsible for
                # shape — same_date isn't applicable.
                if not (_date_re.match(a_str) and _date_re.match(b_str)):
                    continue
                if a_str != b_str:
                    failing.add(idx)
        else:
            op_fn = _COMPARE_OPS.get(rule.compare_op)
            if op_fn:
                for idx in range(len(df)):
                    a_raw = df[field].iloc[idx]
                    if is_temporal_sentinel:
                        if rule.compare_to == "today":
                            b_raw = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        else:
                            b_raw = datetime.now(timezone.utc).isoformat()
                    else:
                        b_raw = df[rule.compare_to].iloc[idx]
                    if a_raw is None or (isinstance(a_raw, float) and pd.isna(a_raw)):
                        # CRT170/J3: absent field — skip (not_empty is the catcher).
                        continue
                    if not is_temporal_sentinel and _batch_absent(b_raw):
                        failing.add(idx)
                        failing_counterpart_missing.add(idx)   # D10 (#145: marked)
                        continue
                    try:
                        a, b = float(a_raw), float(b_raw)
                    except (TypeError, ValueError):
                        try:
                            a = datetime.fromisoformat(str(a_raw).replace("Z", "+00:00"))
                            b = datetime.fromisoformat(str(b_raw).replace("Z", "+00:00"))
                            # Normalise: treat naive datetimes as UTC before comparison
                            if isinstance(a, datetime) and a.tzinfo is None:
                                a = a.replace(tzinfo=timezone.utc)
                            if isinstance(b, datetime) and b.tzinfo is None:
                                b = b.replace(tzinfo=timezone.utc)
                        except (ValueError, AttributeError):
                            a, b = str(a_raw), str(b_raw)
                    if not op_fn(a, b):
                        failing.add(idx)

    elif rule.type == "required_if" and rule.required_if:
        trigger_field = rule.required_if.get("field", "")
        trigger_value = str(rule.required_if.get("value", ""))
        if trigger_field not in df.columns:
            logger.warning("required_if rule '%s' references missing trigger field '%s'",
                           rule.name, trigger_field)
        else:
            # Single-path parity: the trigger is compared as a string and the
            # target's absence uses _batch_absent (blank/whitespace = missing),
            # not a SQL empty-string compare (round-2 B2 matrix).
            for idx in range(len(df)):
                trig = df[trigger_field].iloc[idx]
                if trig is None or (isinstance(trig, float) and pd.isna(trig)):
                    continue
                if str(trig) == trigger_value and _batch_absent(_orig_val(idx)):
                    failing.add(idx)
            for r in ():
                failing.add(r[0])

    elif rule.type == "allowed_values" and rule.allowed_values:
        allowed = {str(v) for v in rule.allowed_values}
        for idx in range(len(df)):
            val = df[field].iloc[idx]
            if not _batch_absent(val):  # D6: blank is absent
                if str(val) not in allowed:
                    failing.add(idx)

    elif rule.type == "lookup" and rule.lookup_file:
        try:
            if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
                ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
                valid_values = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
            else:
                valid_values = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
            for idx in range(len(df)):
                val = df[field].iloc[idx]
                if _batch_absent(val):
                    # CRT170/J3 + D6: absent or blank field — skip (not_empty is the catcher).
                    continue
                elif rule.all_of and isinstance(val, list):
                    if any(str(item) not in valid_values for item in val):
                        failing.add(idx)
                elif str(val) not in valid_values:
                    failing.add(idx)
        except LookupAuthPolicyError as exc:
            # SEC-011: a policy violation is not a transient infra error — fail
            # CLOSED (unlike the infra handler below). Every record subject to
            # this rule fails, mirroring the single-record path's error_message.
            logger.error("lookup rule '%s' blocked by SEC-011 policy: %s", rule.name, exc)
            for idx in range(len(df)):
                val = df[field].iloc[idx]
                if _batch_absent(val):  # D6: absent or blank
                    continue
                failing.add(idx)
        except (FileNotFoundError, KeyError, OSError, RuntimeError) as exc:
            logger.warning("lookup rule '%s' skipped (infrastructure error, not failing batch): %s", rule.name, exc)

    elif rule.type == "checksum" and rule.checksum_algorithm:
        for idx in range(len(df)):
            raw = _orig_val(idx)
            if _batch_absent(raw):  # D6: absent or blank
                continue
            if not isinstance(raw, str):
                # D9 family: a non-string identifier is refused with a typed
                # message under the rule's own code (single-path parity).
                failing.add(idx)
                if failing_messages is not None:
                    failing_messages[idx] = _checksum_type_message(field, raw)
                continue
            if not _validate_checksum(raw, rule.checksum_algorithm):
                failing.add(idx)

    elif rule.type == "cross_field_range":
        for idx in range(len(df)):
            val = df[field].iloc[idx]
            if _batch_absent(val):  # D6: absent or blank
                # CRT170/J3: absent field — skip.
                continue
            try:
                v = float(val)
                fail = False
                for bound_field, cmp in ((rule.cross_min_field, lambda b: v < float(b)),
                                         (rule.cross_max_field, lambda b: v > float(b))):
                    if fail or not bound_field:
                        continue
                    bound = df[bound_field].iloc[idx] if bound_field in df.columns else None
                    if _batch_absent(bound):
                        failing.add(idx)
                        failing_counterpart_missing.add(idx)   # D10 (#145)
                        fail = True
                    elif cmp(bound):
                        fail = True
                if fail:
                    failing.add(idx)
            except (TypeError, ValueError):
                failing.add(idx)

    elif rule.type == "field_sum" and rule.sum_fields and rule.sum_equals is not None:
        tolerance = rule.sum_tolerance if rule.sum_tolerance is not None else 0.0
        for idx in range(len(df)):
            # D10 (#145): an absent/blank operand is a counterpart-missing
            # failure, as on the single path — never silently zeroed (a record
            # whose remaining operands happened to sum correctly passed here).
            operands = [(df[f].iloc[idx] if f in df.columns else None) for f in rule.sum_fields]
            if any(_batch_absent(v) for v in operands):
                failing.add(idx)
                failing_counterpart_missing.add(idx)
                continue
            try:
                total = sum(float(v) for v in operands)
                if abs(total - rule.sum_equals) > tolerance:
                    failing.add(idx)
            except (TypeError, ValueError):
                failing.add(idx)

    elif rule.type == "forbidden_if" and rule.forbidden_if:
        trigger_field = rule.forbidden_if.get("field", "")
        trigger_value = str(rule.forbidden_if.get("value", ""))
        if trigger_field in df.columns:
            query = (
                f'SELECT __idx__ FROM data '
                f'WHERE CAST("{trigger_field}" AS VARCHAR) = $trigger_val '
                f'AND "{field}" IS NOT NULL '
                f'AND TRIM(CAST("{field}" AS VARCHAR)) != \'\''
            )
            for r in con.execute(query, {"trigger_val": trigger_value}).fetchall():
                failing.add(r[0])

    elif rule.type == "conditional_value" and rule.must_equal is not None:
        # Condition filtering is applied after this function returns
        query = (
            f'SELECT __idx__ FROM data '
            f'WHERE "{field}" IS NULL OR CAST("{field}" AS VARCHAR) != $must_equal_val'
        )
        for r in con.execute(query, {"must_equal_val": str(rule.must_equal)}).fetchall():
            failing.add(r[0])

    elif rule.type == "date_diff" and rule.date_diff_field:
        unit = rule.date_diff_unit or "days"
        if rule.date_diff_field not in df.columns:
            logger.warning("date_diff rule '%s' references missing field '%s'", rule.name, rule.date_diff_field)
        else:
            for idx in range(len(df)):
                val = df[field].iloc[idx]
                other_val = df[rule.date_diff_field].iloc[idx]
                if _batch_absent(val):
                    # CRT170/J3 + D6: target field absent or blank — skip (not_empty is the catcher).
                    continue
                if _batch_absent(other_val):
                    # D10: missing/blank counterpart → fail, as the single path (both engines).
                    failing.add(idx)
                    failing_counterpart_missing.add(idx)   # #145
                    continue
                try:
                    d1 = _parse_date(val)
                    d2 = _parse_date(other_val)
                    delta = _delta_days(d1, d2)  # signed, fractional — identical to the single path
                    diff = delta / 365.25 if unit == "years" else delta
                    fail = False
                    if rule.min_value is not None and diff < rule.min_value:
                        fail = True
                    if rule.max_value is not None and diff > rule.max_value:
                        fail = True
                    if fail:
                        failing.add(idx)
                except (TypeError, ValueError):
                    failing.add(idx)

    elif rule.type == "ratio_check" and rule.ratio_numerator and rule.ratio_denominator:
        if rule.ratio_numerator not in df.columns or rule.ratio_denominator not in df.columns:
            logger.warning("ratio_check rule '%s' references missing fields", rule.name)
        else:
            for idx in range(len(df)):
                try:
                    num = df[rule.ratio_numerator].iloc[idx]
                    den = df[rule.ratio_denominator].iloc[idx]
                    if _batch_absent(num) or _batch_absent(den):
                        failing.add(idx)
                        failing_counterpart_missing.add(idx)   # D10 (#145)
                        continue
                    if float(den) == 0:
                        failing.add(idx)
                        continue
                    ratio = float(num) / float(den)
                    fail = False
                    if rule.min_value is not None and ratio < rule.min_value:
                        fail = True
                    if rule.max_value is not None and ratio > rule.max_value:
                        fail = True
                    if fail:
                        failing.add(idx)
                except (TypeError, ValueError, ZeroDivisionError):
                    failing.add(idx)

    elif rule.type == "geospatial_bounds":
        for idx in range(len(df)):
            val = df[field].iloc[idx]
            if _batch_absent(val):  # D6: absent or blank
                # CRT170/J3: absent field — skip (not_empty is the catcher).
                continue
            try:
                lat = float(val)
                fail = False

                if not (-90 <= lat <= 90):
                    fail = True
                elif rule.geo_min_lat is not None and lat < rule.geo_min_lat:
                    fail = True
                elif rule.geo_max_lat is not None and lat > rule.geo_max_lat:
                    fail = True

                if not fail and rule.geo_lon_field:
                    lon_val = df[rule.geo_lon_field].iloc[idx] if rule.geo_lon_field in df.columns else None
                    if _batch_absent(lon_val):
                        fail = True
                        failing_counterpart_missing.add(idx)   # D10 (#145)
                    else:
                        lon = float(lon_val)
                        if not (-180 <= lon <= 180):
                            fail = True
                        elif rule.geo_min_lon is not None and lon < rule.geo_min_lon:
                            fail = True
                        elif rule.geo_max_lon is not None and lon > rule.geo_max_lon:
                            fail = True

                if fail:
                    failing.add(idx)
            except (TypeError, ValueError):
                failing.add(idx)

    # Age checks — apply to any rule with min_age/max_age (typically date fields).
    # CRT170/J3: skip when field is absent (not_empty is the catcher) or unparseable
    # as a date. Only present + parseable rows are evaluated against the age bounds.
    if rule.min_age is not None or rule.max_age is not None:
        age_conditions = []
        if rule.min_age is not None:
            age_conditions.append(
                f'DATE_DIFF(\'year\', TRY_CAST("{field}" AS DATE), CURRENT_DATE) '
                f'- CASE WHEN (MONTH(CURRENT_DATE), DAY(CURRENT_DATE)) < (MONTH(TRY_CAST("{field}" AS DATE)), DAY(TRY_CAST("{field}" AS DATE))) THEN 1 ELSE 0 END '
                f'< {rule.min_age}'
            )
        if rule.max_age is not None:
            age_conditions.append(
                f'DATE_DIFF(\'year\', TRY_CAST("{field}" AS DATE), CURRENT_DATE) '
                f'- CASE WHEN (MONTH(CURRENT_DATE), DAY(CURRENT_DATE)) < (MONTH(TRY_CAST("{field}" AS DATE)), DAY(TRY_CAST("{field}" AS DATE))) THEN 1 ELSE 0 END '
                f'> {rule.max_age}'
            )
        if age_conditions:
            age_query = (
                f'SELECT __idx__ FROM data '
                f'WHERE "{field}" IS NOT NULL '
                f'AND TRIM(CAST("{field}" AS VARCHAR)) != \'\' '
                f'AND TRY_CAST("{field}" AS DATE) IS NOT NULL '
                f"AND ({' OR '.join(age_conditions)})"
            )
            for r in con.execute(age_query).fetchall():
                failing.add(r[0])

    return failing
