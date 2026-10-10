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
from contextvars import ContextVar
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

from .rule_parser import RULE_TYPES, Rule, Severity, _BUILTIN_PATTERNS, compile_rule_pattern
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


# 3.0.5: white space is Unicode White_Space. Python's str.strip()/isspace()
# also take U+001C-U+001F (information separators), which are not white space
# on either engine — "\u001c" is a present value. Used wherever absence or
# padding is decided.
_WS_CHARS = "".join(c for c in map(chr, range(0x3001)) if c.isspace() and c not in "\x1c\x1d\x1e\x1f")


def _ws_strip(s: str) -> str:
    return s.strip(_WS_CHARS)


# 3.0.3: the one ISO reader (managed-engine parity). With no declared layout a
# date is YYYY-MM-DD, optionally Thh:mm:ss, a fraction of any length after
# either ISO 8601 decimal sign (. or ,), and Z or ±hh:mm — on
# every rule that reads a date. fromisoformat alone also reads a space
# separator, 20260110, week dates, T08:00 and +0100, so the shape is gated
# first; the bounded time fields keep T24:00:00 and +24:00 out on every path.
_ISO_DATE_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:[.,][0-9]+)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?)?"
)


def _read_iso(v) -> datetime:
    """Read ``v`` as an ISO 8601 date or datetime (the surface above, white
    space around it ignored); a day that does not exist is refused by
    fromisoformat. UTC when the value carries no zone. Raises ValueError."""
    s = _ws_strip(str(v))
    if not _ISO_DATE_RE.fullmatch(s):
        raise ValueError(f"Cannot parse date: {v!r}")
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Cannot parse date: {v!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# 3.0.5 (both engines): a declared format reads exactly one shape. Every
# directive is fixed width — %Y four digits; %y %m %d %H %M %S two; %f one to
# six after the format's own "." or "," — and every other character of the
# format is itself, exactly once (one space is one space, a tab is not a
# space, case counts). The value (white space around it ignored) must match
# that shape in full; the calendar is then checked (no 31 February, years
# 0001-9999). strptime alone reads 1/02/2026 for %d/%m/%Y, treats a space as
# any run of white space, and raises a regex error on a repeated directive.
_LAYOUT_DIRECTIVES = {
    "Y": ("[0-9]{4}", "year"), "y": ("[0-9]{2}", "year2"), "m": ("[0-9]{2}", "month"),
    "d": ("[0-9]{2}", "day"), "H": ("[0-9]{2}", "hour"), "M": ("[0-9]{2}", "minute"),
    "S": ("[0-9]{2}", "second"), "f": ("[0-9]{1,6}", "micro"),
}
# Two Go layouts a managed-engine dashboard once wrote into contracts. Refused
# when a contract is submitted; stored content reads them as aliases.
_GO_DATE_LAYOUT = "2006-01-02"
_GO_RFC3339_LAYOUT = "2006-01-02T15:04:05Z07:00"
_RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


@lru_cache(maxsize=512)
def _layout_gate(fmt: str):
    """(compiled shape, directive keys) for a strptime-style layout, or None
    when the layout uses something outside the supported set (an unknown
    directive, %f not after "." or ",", white space around it) — refused when
    a contract is submitted; stored content keeps its old strptime reading."""
    if fmt != _ws_strip(fmt):
        return None
    parts: list[str] = []
    keys: list[str] = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c != "%":
            parts.append(re.escape(c))
            i += 1
            continue
        d = fmt[i + 1] if i + 1 < len(fmt) else ""
        if d == "%":
            parts.append("%")
        elif d in _LAYOUT_DIRECTIVES and (d != "f" or (i > 0 and fmt[i - 1] in ".,")):
            pat, key = _LAYOUT_DIRECTIVES[d]
            parts.append(f"({pat})")
            keys.append(key)
        else:
            return None
        i += 2
    return re.compile("".join(parts)), tuple(keys)


def _read_layout(s: str, fmt: str) -> datetime:
    """Read ``s`` (already trimmed) in the declared layout ``fmt`` (strptime
    spelling, from _human_to_strptime). Naive; raises ValueError."""
    if fmt == _GO_RFC3339_LAYOUT:
        if not _RFC3339_RE.fullmatch(s):
            raise ValueError("not an RFC 3339 timestamp")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    gate = _layout_gate(fmt)
    if gate is None:
        return datetime.strptime(s, fmt)   # stored content: the old reading
    shape, keys = gate
    m = shape.fullmatch(s)
    if not m:
        raise ValueError("not in the declared layout")
    pairs = list(zip(keys, m.groups()))
    fields = [_YEAR_KEYS.get(k, k) for k in keys]   # %Y and %y both read the year
    got = _layout_fields(pairs)   # a field read twice: the last occurrence decides
    result = _layout_datetime(got)
    if len(set(fields)) != len(fields):
        # refused when a contract is submitted (3.0.6); stored content: every
        # occurrence must also be a valid value (raises ValueError if not)
        for i in range(len(pairs)):
            _layout_datetime(_layout_fields(pairs[:i] + pairs[i + 1:] + [pairs[i]]))
    return result


_YEAR_KEYS = {"year": "year", "year2": "year"}


def _layout_fields(pairs) -> dict:
    """{directive key: text}, later pairs winning; a later %Y / %y replaces an
    earlier %y / %Y (one field, the year)."""
    got: dict = {}
    for key, text in pairs:
        if key in _YEAR_KEYS:
            got.pop("year", None)
            got.pop("year2", None)
        got[key] = text
    return got


def _layout_datetime(got: dict) -> datetime:
    if "year" in got:
        year = int(got["year"])
    elif "year2" in got:
        yy = int(got["year2"])
        year = 1900 + yy if yy >= 69 else 2000 + yy   # strptime's %y pivot
    else:
        year = 1900   # a layout without a year (strptime's default)
    return datetime(year, int(got.get("month", 1)), int(got.get("day", 1)),
                    int(got.get("hour", 0)), int(got.get("minute", 0)), int(got.get("second", 0)),
                    int(got["micro"].ljust(6, "0")) if "micro" in got else 0)


def _parse_date(v, fmt: Optional[str] = None):
    """Parse a date or datetime into a timezone-aware datetime (UTC assumed
    when the value carries no zone; a bare date is midnight UTC).

    ``fmt`` (2.8.0): the strptime layout the field's ``date_format`` rule
    declares, resolved by :func:`resolve_date_layouts`. When given, the value
    is parsed with exactly that layout — a contract that declares
    ``DD/MM/YYYY`` is then internally consistent: what its format rule
    accepts, its ``compare`` / ``date_diff`` / ``age_match`` / ``min_age``
    rules can read. Without it, the ISO path below.

    2.7.0 (round 4, found by the regulatory-claims fixture): this used to
    return a *date*, so every ``date_diff`` was whole-day granular and a
    sub-day window (DORA's 4-hour initial-notification clock) could never
    fire here while it fired on the managed engine, and a timestamp with a
    zone offset (``+01:00``) or fractional seconds could not be parsed at
    all. Accepted shapes now match the managed engine's: date; datetime
    without zone; ``Z``; ``±hh:mm``; fractional seconds with either. 3.0.3:
    exactly those — :func:`_read_iso` gates ``fromisoformat``, which alone
    also read a space-separated datetime and ``20260110``.
    """
    s = _ws_strip(str(v))
    if fmt:
        try:
            dt = _read_layout(s, fmt)
        except ValueError:
            raise ValueError(f"Cannot parse date {v!r} with declared layout {fmt!r}") from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return _read_iso(v)


# ── Declared date layouts (2.8.0) ───────────────────────────────────────
# A field's ``date_format`` rule declares the layout its values carry. Every
# rule that *reads* that field as a date — compare, date_diff, age_match,
# and the min_age/max_age add-on — must parse with the same layout, or the
# contract is internally inconsistent (the format rule accepts ``11/12/2026``
# and the compare rule cannot read it; worse, compare used to fall back to
# string order, so an inverted DD/MM/YYYY pair passed silently). Layouts are
# resolved from the rule list once per distinct list and stamped onto the
# reading rules as excluded cached fields, so the hot path pays one pass.
_CROSS_FIELD_DATE_TYPES = frozenset({"compare", "date_diff", "age_match"})
_layout_conflicts_warned: set = set()
_NO_LAYOUTS: dict = {}
# The declared layouts for the validate call in progress. A ContextVar, not a
# stamp on the Rule objects: Rule objects are shared by every concurrent call
# on the same contract (and, before 3.0.0, across context rule lists), so
# per-call mutation of one is a lost-update race under threads (blind review
# of PR #166). The variable is
# thread- and task-local and reset when the call returns, so handlers called
# outside a validate call see no layouts (the ISO path).
_date_layouts_var: ContextVar[dict] = ContextVar("opendqv_date_layouts", default=_NO_LAYOUTS)


def _counterpart_field(rule: Rule) -> Optional[str]:
    if rule.type == "compare":
        return None if rule.compare_to in ("today", "now") else rule.compare_to
    if rule.type == "date_diff":
        return rule.date_diff_field
    if rule.type == "age_match":
        return rule.dob_field
    return None


def resolve_date_layouts(rules: list) -> dict[str, str]:
    """``{field: strptime layout}`` from each field's first ``date_format`` rule
    that declares a ``format``. First declared wins; a second, different layout
    on the same field is warned once and ignored (never rejected — the format
    rule itself still enforces its own layout on its own path)."""
    layouts: dict[str, str] = {}
    for r in rules:
        if r.type != "date_format" or not r.format:
            continue
        if r.condition:
            # A conditional format rule scopes its layout to the records its
            # condition selects; the cross-field rules cannot know that scope,
            # so it declares nothing for them (blind review: a US-branch ISO
            # value was being parsed with the UK-branch layout).
            continue
        fmt = _human_to_strptime(r.format)
        prev = layouts.get(r.field)
        if prev is None:
            layouts[r.field] = fmt
        elif prev != fmt:
            key = (r.field, prev, fmt)
            if key not in _layout_conflicts_warned:
                _layout_conflicts_warned.add(key)
                logger.warning(
                    "field '%s' declares two date_format layouts (%r then %r); the first "
                    "declared wins for cross-field date rules — run `opendqv lint`",
                    r.field, prev, fmt,
                )
    return layouts


def _layouts_for(rule: Rule) -> tuple[Optional[str], Optional[str]]:
    """(own-field layout, counterpart layout) for the validate call in progress."""
    layouts = _date_layouts_var.get()
    if not layouts:
        return None, None
    cp = _counterpart_field(rule)
    return layouts.get(rule.field), (layouts.get(cp) if cp else None)

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

    _layout_token = _date_layouts_var.set(resolve_date_layouts(rules))
    try:
        for rule in rules:
            value = record.get(rule.field)
            try:
                failure = _check_rule(value, rule, record)
                if (not failure and rule.cached_has_age_constraint
                        and (not rule.cached_has_condition or _check_condition(rule, record))):
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
                    message=_RULE_ERROR_MESSAGE,
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

    finally:
        _date_layouts_var.reset(_layout_token)
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
    if "value" in rule.condition:
        return _text_matches(raw, rule.condition["value"])
    if "not_value" in rule.condition:
        return not _text_matches(raw, rule.condition["not_value"])
    return True


def _text_matches(raw, want) -> bool:
    """Does a record value match a condition / trigger value? 3.0.5 (both
    engines): text as written, through the one rendering — a missing or null
    field, or a list or object (empty or not), matches no value; "" matches
    ``""`` only; no trimming (``present:`` alone reads absence). A null
    ``want`` is refused when a contract is submitted; stored content keeps its
    old reading (the text "None", a missing field reading as "")."""
    if want is None:
        return str(raw if raw is not None else "") == "None"
    if raw is None or isinstance(raw, (list, dict)):
        return False
    return _render_value(raw) == _render_value(want)


def _trigger_matches(trigger: dict, record: Optional[dict]) -> bool:
    """required_if / forbidden_if trigger map. A map without ``value`` is
    refused when a contract is submitted (3.0.5); stored content keeps its old
    reading, where a missing ``value`` was "" and a missing field read as ""."""
    raw = (record or {}).get(trigger.get("field"))
    if trigger.get("value") is None:
        # the pre-3.0.5 reading, kept for stored content only
        return str((record or {}).get(trigger.get("field"), "")) == str(trigger.get("value", ""))
    return _text_matches(raw, trigger["value"])


def _is_ascii_digits(s: str) -> bool:
    """True only for a non-empty run of ASCII 0-9.

    str.isdigit() also returns True for Unicode digit characters such as
    superscripts (U+00B2 '²') and other scripts' digits, but int() raises on
    the former — so an isdigit()-gated int() is an unhandled-ValueError (500)
    primitive. An identifier check-string is never validly non-ASCII, so
    reject those here and return a clean "invalid checksum" instead of crashing.
    """
    return s.isascii() and s.isdigit()


# The supported checksum algorithms (3.0.5: the managed engine's eleven).
CHECKSUM_ALGORITHMS = (
    "mod10_gs1", "iban_mod97", "isin_luhn", "lei_mod97", "vin_mod11", "isrc_luhn",
    "cpf_mod11", "nhs_mod11", "luhn", "figi_luhn", "verhoeff",
)
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def _luhn_sum(digits: str) -> int:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total


def _validate_checksum(value: str, algorithm: str) -> bool:
    """Validate identifier check digits. Returns True if checksum is valid."""
    if algorithm not in CHECKSUM_ALGORITHMS:
        # v2.3.25 (Pilot 2026-04-29): an unknown algorithm fails closed —
        # a typo'd key used to pass every record with only a log warning.
        logger.warning(
            "Unknown checksum algorithm '%s' — rule fails closed. "
            "Check the contract YAML for typos in checksum_algorithm "
            "(supported: %s).",
            algorithm, ", ".join(CHECKSUM_ALGORITHMS),
        )
        return False
    s = _ws_strip(str(value)).upper()
    if len(s) < 2:
        return False   # 3.0.5: no check digit to check (e.g. "0")

    if algorithm == "luhn":
        # ISO/IEC 7812 Luhn mod-10 over the whole digit string (payment cards etc.)
        if not _is_ascii_digits(s):
            return False
        return _luhn_sum(s) % 10 == 0

    if algorithm == "verhoeff":
        # Verhoeff dihedral-group check (e.g. Aadhaar); the last digit is the check
        if not _is_ascii_digits(s):
            return False
        c = 0
        for i, ch in enumerate(reversed(s)):
            c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
        return c == 0

    if algorithm == "figi_luhn":
        # OpenFIGI: 12 characters, letters valued A=10..Z=35; every second of
        # the first 11 values doubled, the digits of each summed; check digit
        # is (10 - sum % 10) % 10.
        if len(s) != 12 or not s.isascii() or not s.isalnum() or not s[-1].isdigit():
            return False
        total = 0
        for i, ch in enumerate(s[:-1]):
            v = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10
            if i % 2 == 1:
                v *= 2
            total += sum(int(d) for d in str(v))
        return (10 - total % 10) % 10 == int(s[-1])

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
            if ch in "0123456789":   # 3.0.5: ASCII only — '²'.isdigit() is True and int() raised
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

    return False   # unreachable: every name in CHECKSUM_ALGORITHMS has a branch above


# semver.org's suggested regular expression (SemVer 2.0.0 FAQ) after an
# optional lower-case v, every \d spelled [0-9] so only ASCII digits read
# (3.0.6, both engines; builtin:semver in rule_parser is the same grammar).
SEMVER_PATTERN = (
    r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
_SEMVER_RE = re.compile(SEMVER_PATTERN)


def _semver_tuple(v):
    """The SemVer 2.0.0 precedence key of a version (3.0.6, §11): major,
    minor and patch numerically at any size; a pre-release below its release;
    pre-release identifiers left to right, digit-only ones numerically and
    below alphanumeric ones (ASCII order), a longer set higher when the rest
    are equal; build metadata ignored. White space around the value is
    removed. Raises ValueError for anything that is not a version — a number,
    a boolean, a list, an object or None included."""
    if not isinstance(v, str):
        raise ValueError("not a version")
    m = _SEMVER_RE.fullmatch(_ws_strip(v))
    if not m:
        raise ValueError("not a version")
    major, minor, patch, pre, _build = m.groups()
    # a numeric part has no leading zero, so (length, digits) orders it as a
    # number at any size — int() refuses more than 4300 digits
    core = tuple((len(p), p) for p in (major, minor, patch))
    if pre is None:
        return (*core, 1, ())
    ids = tuple((0, len(i), i) if i.isdigit() else (1, 0, i) for i in pre.split("."))
    return (*core, 0, ids)


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
# ("must equal X" — absent is a violation on both engines), unique (a
# set-based rule evaluated on the batch frame, never on one value), and
# (3.0.5, both engines) field_sum / ratio_check, whose own `field` is
# attribution only and never read — they judge their operands whenever the
# rule applies, and an absent operand fails (D10).
_ABSENT_EXEMPT_RULE_TYPES = _PRESENCE_RULE_TYPES | frozenset({"conditional_value", "unique", "field_sum", "ratio_check"})


def _is_field_absent(value) -> bool:
    """Absent: None/missing, a blank or white-space-only string, or (3.0.5,
    both engines) an empty array or object. No rule but a presence rule runs
    on an absent value."""
    if value is None:
        return True
    if isinstance(value, str):
        return _ws_strip(value) == ""
    if isinstance(value, (list, dict)):
        return not value
    return False


def _batch_absent(val) -> bool:
    """Batch-path twin of _is_field_absent: None, NaN, or a blank string.

    The two must agree or validate_record and validate_batch drift on blank
    values (D6) — the conformance generator refuses to emit a corpus when
    they do.
    """
    if isinstance(val, float) and pd.isna(val):
        return True
    return _is_field_absent(val)


def _check_not_empty(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return rule.error_message
    return None


def _check_not_empty_string(value, rule: Rule, record: dict | None = None) -> str | None:
    """Presence + JSON-string type guard (CRT180 contract-format conformance).

    Unlike not_empty, a non-string value is never coerced: 0, false and a
    non-empty list or object are rejected — under this rule's own error
    code, with a typed message — rather than silently stringified and
    passed. Absent, null, whitespace-only and (3.0.5) empty []/{} values
    are absence and fail with the rule's own message.
    """
    if _is_field_absent(value):
        return rule.error_message
    if not isinstance(value, str):
        # Reported under the rule's own error code (not OPENDQV_TYPE_MISMATCH):
        # the type guard IS this rule's assertion, so the code stays routable
        # to the rule — matches the managed engine, which shipped this type
        # first (cross-engine fixture run, CRT180).
        return _not_empty_string_type_message(rule.field, value)
    if _ws_strip(value) == "":
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
    if isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    str_val = _render_value(value)   # 3.0.5: the one text rendering
    pattern = _BUILTIN_PATTERNS.get(rule.pattern, rule.pattern)
    compiled = rule.compiled_pattern or compile_rule_pattern(pattern)
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
# A checker that raised: the record is rejected under OPENDQV_RULE_ERROR with
# this generic message on both paths (CWE-209: never the exception detail).
_RULE_ERROR_PREFIX = "__OPENDQV_RULE_ERROR__::"
_RULE_ERROR_MESSAGE = "Rule could not be evaluated; record rejected (fail-closed)."


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
    if isinstance(value, int) and not isinstance(value, bool):
        # only reached for an integer beyond float64 (3.0.5, B9)
        return (
            f"{_TYPE_MISMATCH_PREFIX}"
            f"{rule.type} rule on field '{rule.field}' expected a numeric "
            f"value within the floating-point range, got a number beyond it"
        )
    return (
        f"{_TYPE_MISMATCH_PREFIX}"
        f"{rule.type} rule on field '{rule.field}' expected numeric "
        f"value, got {_json_type_name(value)}"
    )


# 3.0.6 (both engines): text is a number only when, after the white space
# around it is removed, it is a plain finite decimal — optional sign, digits,
# optional point and fraction, optional exponent, underscores between digits.
# ASCII digits only: float() alone reads "١٢" and "１２" as 12, and "NaN",
# "Infinity" and "1e400" as non-finite floats.
_DIGITS = r"[0-9](?:_?[0-9])*"
_NUMBER_TEXT_RE = re.compile(
    rf"[+-]?(?:{_DIGITS}(?:\.(?:{_DIGITS})?)?|\.{_DIGITS})(?:[eE][+-]?{_DIGITS})?"
)


def _num(value) -> float:
    """The one number reader (3.0.5, both engines) for every numeric rule. A
    JSON boolean is not a number (``float(True)`` is 1.0), nor is a list or
    object; an integer beyond float64 is no number (``float()`` raises
    OverflowError); NaN and infinity are not finite. Text must match
    _NUMBER_TEXT_RE once the white space around it is removed (3.0.6).
    Raises ValueError for anything else."""
    if value is None or isinstance(value, (bool, list, dict)):
        raise ValueError("not a number")
    if isinstance(value, str):
        value = _ws_strip(value)
        if not _NUMBER_TEXT_RE.fullmatch(value):
            raise ValueError("not a number")
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("not a number") from None
    if not math.isfinite(f):
        raise ValueError("not a finite number")
    return f


def _unreadable_number(value) -> bool:
    """A JSON number the engine cannot read (3.0.5): NaN, infinite, or an
    integer beyond float64. Never compared as text. 3.0.6: text is not a JSON
    number — "NaN", "Infinity" and "1e400" are text that is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        _num(value)
    except ValueError:
        return True
    return False


def _is_numeric_value(value) -> bool:
    """True if value is int/float (and not bool, which subclasses int).

    bool is excluded: True/False as a numeric is almost always a type
    contract violation, not a 0/1 the producer intended.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_min(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    # Strings that parse as numbers ("28.50") are read (CSV-pipeline
    # ergonomics); a boolean, list, object, NaN/inf or an integer beyond
    # float64 is a type mismatch (3.0.5: was read as 1 / raised).
    try:
        v = _num(value)
    except ValueError:
        return _type_mismatch_msg(rule, value)
    if v < rule.min_value:
        return rule.error_message
    return None


def _check_max(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    try:
        v = _num(value)
    except ValueError:
        return _type_mismatch_msg(rule, value)
    if v > rule.max_value:
        return rule.error_message
    return None


def _check_range(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    try:
        v = _num(value)
    except ValueError:
        return _type_mismatch_msg(rule, value)
    if rule.min_value is not None and v < rule.min_value:
        return rule.error_message
    if rule.max_value is not None and v > rule.max_value:
        return rule.error_message
    return None


def _collection_message(rule: Rule, value) -> str:
    """A non-empty list or object is not text (3.0.5, both engines): a rule
    that compares a single value fails it under its own code, naming the JSON
    type. Collections are never rendered — ``str()`` of a list leaks it."""
    return (
        f'{rule.type} rule on field "{rule.field}" compares a single value, '
        f"got {_json_type_name(value)} — send a string, number or boolean"
    )


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
    if rule.max_length is not None and len(str_val) > rule.max_length:   # 3.0.5: 0 means zero
        return rule.error_message
    return None


def _human_to_strptime(fmt: str) -> str:
    # Accept either Java/human-readable patterns (YYYY-MM-DD) or strftime
    # codes (%Y-%m-%d). MM is month before any HH; minute after. 3.0.5: the
    # two legacy Go layouts read as aliases (stored content only).
    if fmt == _GO_DATE_LAYOUT:
        return "%Y-%m-%d"
    if fmt == _GO_RFC3339_LAYOUT or "%" in fmt:
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
    # 3.0.2: a date ignores the white space around it (the batch path's
    # TRY_STRPTIME always did); a space inside the value is still not a date.
    str_val = _ws_strip(str(value))
    # Honour the contract's declared format strictly. When no format is
    # declared, default to ISO 8601 (date or datetime) — never accept
    # locale-ambiguous formats like DD/MM/YYYY or MM/DD/YYYY by default.
    # The persona-driven CRT173 finding: prior behaviour silently accepted
    # "26/04/2026" against rules whose error_message claimed "YYYY-MM-DD",
    # which is the worst kind of false-pass — the rule lied about what it
    # enforces.
    # 3.0.3: with no format declared, the one ISO reader every date-reading
    # rule uses (it was two strptime layouts: no Z, no offset, no fraction,
    # and an unpadded 2026-1-10 got through).
    try:
        if rule.format:
            _read_layout(str_val, _human_to_strptime(rule.format))
        else:
            _read_iso(str_val)
    except ValueError:
        return rule.error_message
    return None


def _check_unique(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    # Single-record mode cannot check uniqueness — skip silently
    return None


@lru_cache(maxsize=256)
def _warn_unknown_compare_op(rule_name: str, op) -> None:
    logger.warning("compare rule '%s' has unknown compare_op '%s'", rule_name, op)


_ORDERING_OPS = frozenset({"gt", "lt", "gte", "lte", ">", "<", ">=", "<="})


def _num_or_none(value) -> Optional[float]:
    try:
        return _num(value)
    except ValueError:
        return None


def _check_compare(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.compare_to or not rule.compare_op:
        logger.warning("compare rule '%s' missing compare_to or compare_op", rule.name)
        return None
    if _is_field_absent(value):
        return None
    op = rule.compare_op
    op_fn = _COMPARE_OPS.get(op)
    la, lb = _layouts_for(rule)

    # 3.0.5: `today` compares calendar dates (both sides truncated to
    # y/m/d, so a timestamp stamped today is not "after today"); `now` is the
    # instant. The value must read as a date: unreadable fails.
    if rule.compare_to in ("today", "now"):
        if op_fn is None and op != "same_date":
            _warn_unknown_compare_op(rule.name, op)
            return None
        try:
            a = _parse_date(value, la)
        except ValueError:
            return rule.error_message
        now = datetime.now(timezone.utc)
        if rule.compare_to == "today" or op == "same_date":
            a, b = a.date(), now.date()
        else:
            b = now
        if op == "same_date":
            return None if a == b else rule.error_message
        return None if op_fn(a, b) else rule.error_message

    other = (record or {}).get(rule.compare_to)
    if _is_field_absent(other):
        return _COUNTERPART_MISSING_PREFIX + rule.error_message  # D10: a missing/blank counterpart IS a comparison failure (both engines)
    if op_fn is None and op != "same_date":
        # Refused when a contract is submitted (3.0.5); stored content keeps
        # its old reading — the rule does not judge.
        _warn_unknown_compare_op(rule.name, op)
        return None

    # same_date (v2.3.20, MiFIR RTS 22 T+0): both operands are dates, each
    # read with its declared layout or the ISO reader; their calendar dates
    # (as written, in each value's own offset) must be equal. 3.0.5: an
    # operand that cannot be read as a date fails — it is not "not applicable".
    if op == "same_date":
        try:
            a = _parse_date(value, la)
            b = _parse_date(other, lb)
        except ValueError:
            return rule.error_message
        return None if a.date() == b.date() else rule.error_message

    if la or lb:
        # 2.8.0: a declared date layout on either operand — both sides are
        # dates. Parse each with its own layout (an undeclared operand keeps
        # the ISO path) and never fall through to the numeric or string
        # comparison; an operand the layout cannot read fails the rule.
        try:
            a = _parse_date(value, la)
            b = _parse_date(other, lb)
        except ValueError:
            return rule.error_message
        return None if op_fn(a, b) else rule.error_message

    # `algorithm: semver` orders SemVer 2.0.0 versions by SemVer precedence
    # (3.0.6; 3.0.5 compared the numeric triple only). A value that is not a
    # version fails — never a number or text comparison.
    if getattr(rule, "algorithm", None) == "semver":
        try:
            a, b = _semver_tuple(value), _semver_tuple(other)
        except ValueError:
            return rule.error_message
        return None if op_fn(a, b) else rule.error_message

    # Two numbers compare as numbers, two ISO dates as instants; anything
    # else compares as text — the one rendering (3.0.5), and a list or an
    # object is not text. 3.0.6: a boolean has no order — gt/lt/gte/lte with
    # a boolean on either side fails; eq/neq compare its text (true / false),
    # and _num never reads it as 1.
    ordering = op in _ORDERING_OPS
    if ordering and (isinstance(value, bool) or isinstance(other, bool)):
        return rule.error_message
    if _unreadable_number(value) or _unreadable_number(other):
        return rule.error_message   # NaN, infinity, an integer beyond float64: never compared as text
    num_a, num_b = _num_or_none(value), _num_or_none(other)
    if num_a is not None and num_b is not None:
        return None if op_fn(num_a, num_b) else rule.error_message
    try:
        a, b = _read_iso(value), _read_iso(other)
    except ValueError:
        for v in (value, other):
            if isinstance(v, (list, dict)):
                return _collection_message(rule, v)
        # 3.0.6 §4(b): a number is never ordered against a non-number ("abc"
        # gt 5 passed by character order). eq/neq keep the text reading.
        if ordering and (num_a is None) != (num_b is None):
            return rule.error_message
        a, b = _render_value(value), _render_value(other)
    return None if op_fn(a, b) else rule.error_message


def _check_required_if(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.required_if:
        return None
    if _trigger_matches(rule.required_if, record) and _is_field_absent(value):
        return rule.error_message
    return None


def _render_value(v) -> str:
    """Text a record value is compared as — 3.0.5: on EVERY text surface
    (regex, allowed/forbidden_values, lookup, must_equal, conditions, trigger
    maps, compare's text fallback); D12, 2.9.0. An integral float renders without the trailing ".0" so a JSON
    ``99999.0`` matches a listed ``"99999"`` — the managed engine's shortest
    float rendering. A boolean renders as its JSON spelling, lowercase
    ``true``/``false`` (2.9.1, D12 addendum: Python's ``str(True)`` is ``True``;
    the managed engine renders the spelling the record carried). Everything
    else renders as ``str()``."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _check_allowed_values(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.allowed_values:
        return None
    if _is_field_absent(value):
        return None  # D6: blank is absent — presence rules are the single catcher
    if isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    allowed = [_render_value(v) for v in rule.allowed_values]
    if _render_value(value) not in allowed:
        return rule.error_message
    return None


def _check_forbidden_values(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    """Exact sibling of allowed_values with the sense inverted (2.9.0, both
    engines): a PRESENT value equal to any listed value fails. Absence is not a
    violation — presence is not_empty's job. Exact rendered-text match,
    case-sensitive, no trimming."""
    if not rule.forbidden_values:
        return None
    if _is_field_absent(value):
        return None  # D6
    if isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    forbidden = [_render_value(v) for v in rule.forbidden_values]
    if _render_value(value) in forbidden:
        return rule.error_message
    return None


# 3.0.5: during validate_batch, each lookup's reference set — or its load
# failure — is fetched once for the whole batch. The per-record batch would
# otherwise retry a failed load (lru_cache does not cache exceptions) once per
# record: N timeouts against a blackholed host, N hits on a failing server.
_lookup_memo_var: ContextVar[Optional[dict]] = ContextVar("opendqv_lookup_memo", default=None)


def _lookup_values(rule: Rule) -> frozenset:
    key = (rule.lookup_file, rule.lookup_field or "", rule.cache_ttl, rule.lookup_auth_header)
    memo = _lookup_memo_var.get()
    if memo is not None and key in memo:
        got = memo[key]
        if isinstance(got, BaseException):
            raise got
        return got
    try:
        if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
            ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
            got = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
        else:
            got = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError) as exc:
        logger.error("%s rule '%s' could not load '%s': %s", rule.type, rule.name, rule.lookup_file, exc)
        if memo is not None:
            memo[key] = exc
        raise
    if memo is not None:
        memo[key] = got
    return got


def _check_lookup(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.lookup_file:
        logger.warning("lookup rule '%s' missing lookup_file", rule.name)
        return None
    if _is_field_absent(value):
        return None  # D6: blank is absent — presence rules are the single catcher
    try:
        valid_values = _lookup_values(rule)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError):
        return rule.error_message   # fails closed (logged once per batch by _lookup_values)
    if rule.all_of and isinstance(value, list):
        # all_of reads a list: every item must be in the reference set
        for item in value:
            if isinstance(item, (list, dict)) or _render_value(item) not in valid_values:
                return rule.error_message
    elif isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    elif _render_value(value) not in valid_values:
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
        v = _num(value)
        if rule.cross_min_field:
            low = rec.get(rule.cross_min_field)
            if _is_field_absent(low):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            if v < _num(low):
                return rule.error_message
        if rule.cross_max_field:
            high = rec.get(rule.cross_max_field)
            if _is_field_absent(high):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            if v > _num(high):
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
        total = sum(_num(rec.get(f)) for f in rule.sum_fields)
        tolerance = rule.sum_tolerance if rule.sum_tolerance is not None else 0.0
        if abs(total - rule.sum_equals) > tolerance:
            return rule.error_message
    except (TypeError, ValueError):
        return rule.error_message
    return None


def _check_forbidden_if(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if not rule.forbidden_if:
        return None
    if _trigger_matches(rule.forbidden_if, record) and not _is_field_absent(value):
        return rule.error_message
    return None


def _check_conditional_value(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if rule.must_equal is None:
        return None
    # Fires on an absent own field (must_equal cannot be met by nothing).
    if _is_field_absent(value):
        return rule.error_message
    if isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    if _render_value(value) != _render_value(rule.must_equal):
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
        la, lb = _layouts_for(rule)
        d1 = _parse_date(value, la)
        d2 = _parse_date(other_val, lb)
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
        num = _num(num_raw)
        den = _num(den_raw)
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
        valid_values = _lookup_values(rule)
    except (FileNotFoundError, KeyError, OSError, RuntimeError, ValueError):
        return rule.error_message   # fails closed (logged once per batch by _lookup_values)
    if isinstance(value, (list, dict)):
        return _collection_message(rule, value)
    if _render_value(value) not in valid_values:
        return rule.error_message
    return None


def _check_geospatial_bounds(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    if _is_field_absent(value):
        return None
    rec = record or {}
    try:
        lat = _num(value)

        if rule.geo_min_lat is not None and lat < rule.geo_min_lat:
            return rule.error_message
        if rule.geo_max_lat is not None and lat > rule.geo_max_lat:
            return rule.error_message

        if rule.geo_lon_field:
            lon_val = rec.get(rule.geo_lon_field)
            if _is_field_absent(lon_val):
                return _COUNTERPART_MISSING_PREFIX + rule.error_message   # D10 (#145)
            lon = _num(lon_val)
            if rule.geo_min_lon is not None and lon < rule.geo_min_lon:
                return rule.error_message
            if rule.geo_max_lon is not None and lon > rule.geo_max_lon:
                return rule.error_message

        if not (-90 <= lat <= 90):
            return rule.error_message
        if rule.geo_lon_field:
            lon_val = rec.get(rule.geo_lon_field)
            if lon_val is not None:
                lon = _num(lon_val)
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
        declared = int(_num(value))
        # 2.8.0: the dob field's declared layout; 3.0.3: the ISO reader when
        # undeclared (was the bare %Y-%m-%d, so a Z date of birth failed).
        dob = _parse_date(dob_val, _layouts_for(rule)[1])
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
    "forbidden_values": _check_forbidden_values,
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
if frozenset(_RULE_HANDLERS) != RULE_TYPES:  # a real raise, not an assert: python -O must not strip it
    raise RuntimeError(
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


def _age_layout(rule: Rule) -> str:
    """Layout for the min_age/max_age add-on: the rule's own ``format`` (a
    ``date_format`` rule), else the field's declared layout (the add-on on any
    other rule type), else None: the ISO reader (2.8.0; 3.0.3 — was the bare
    ``%Y-%m-%d``, so a ``Z`` date of birth was silently skipped)."""
    if rule.format:
        return _human_to_strptime(rule.format)
    return _layouts_for(rule)[0]


def _check_age(value, rule: Rule) -> Optional[str]:
    """Check min_age/max_age constraints. Runs after the type check passes.

    CRT170/J3: skip when field is absent — the not_empty/required_if rules
    are the catcher for absence. 3.0.4 (ruling 2026-10-09, absence skips;
    unreadable fails): a present value the add-on cannot read as a date
    fails under the carrying rule's code, on any carrying rule — as every
    other date reader already does. This reverses 2.8.0, which skipped it
    to match the batch SQL's ``__d__ IS NOT NULL``. The caller scopes the
    add-on by the rule's condition."""
    if rule.min_age is None and rule.max_age is None:
        return None
    if _is_field_absent(value):
        return None
    try:
        dob = _parse_date(value, _age_layout(rule))
        today = datetime.now(timezone.utc)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if rule.min_age is not None and age < rule.min_age:
            return rule.error_message
        if rule.max_age is not None and age > rule.max_age:
            return rule.error_message
    except (ValueError, TypeError):
        return rule.error_message  # present but unreadable: it was asked to read a date
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
    try:
        df = pd.DataFrame(records)
    except OverflowError:
        # An integer beyond int64/float64 (3.0.5, B9): only `unique` reads the
        # frame, so keep every cell as the Python object it is.
        df = pd.DataFrame(records, dtype=object)
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

        _layout_token = _date_layouts_var.set(resolve_date_layouts(rules))
        _lookup_token = _lookup_memo_var.set({})
        try:
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
                # 3.0.5: the single path's own test on the raw record — the frame
                # compare (astype(str)) read True as "True", a missing field as
                # "None"/"nan", and a list as its Python repr.
                if rule.condition and failing_indices:
                    failing_indices = {i for i in failing_indices if _check_condition(rule, records[i])}

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
                    elif failing_messages.get(idx) == _RULE_ERROR_PREFIX:
                        row_entry = {**entry_template, "message": _RULE_ERROR_MESSAGE,
                                     "error_code": "OPENDQV_RULE_ERROR", "severity": Severity.ERROR.value}
                    elif idx in failing_messages:
                        row_entry = {**entry_template, "message": failing_messages[idx]}
                    else:
                        row_entry = entry_template
                    if idx in failing_counterpart_missing:
                        row_entry = {**row_entry, "counterpart_missing": True}   # #145
                    if rule.severity == Severity.ERROR or row_entry["error_code"] == "OPENDQV_RULE_ERROR":
                        row_results[idx]["errors"].append(row_entry)
                    else:
                        row_results[idx]["warnings"].append(row_entry)
        finally:
            _date_layouts_var.reset(_layout_token)
            _lookup_memo_var.reset(_lookup_token)
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
# 3.0.5: only `unique` (set-based, judged on the whole column) — see
# _batch_check_rule_inner.
_BATCH_BRANCH_TYPES = frozenset({"unique"})


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
    def _evaluate_per_record() -> None:
        # Evaluate with the single-path handler so single/batch parity holds
        # by construction (used for every type without a native branch, and
        # for compare when a declared date layout is in play — 2.8.0).
        for idx in range(len(df)):
            rec = records[idx] if records is not None else {c: df[c].iloc[idx] for c in df.columns if c != "__idx__"}
            try:
                msg = _check_rule(rec.get(field), rule, rec)
            except Exception:
                # Fail closed for this record only, exactly as validate_record.
                logger.exception("validate_batch: checker raised on rule=%s field=%s record=%d — failing closed",
                                 rule.name, rule.field, idx)
                failing.add(idx)
                failing_messages[idx] = _RULE_ERROR_PREFIX
                continue
            if msg:
                failing.add(idx)
                if msg.startswith(_TYPE_MISMATCH_PREFIX):
                    failing_type_mismatches[idx] = msg
                    continue
                if msg.startswith(_COUNTERPART_MISSING_PREFIX):   # #145
                    failing_counterpart_missing.add(idx)
                    msg = msg[len(_COUNTERPART_MISSING_PREFIX):]
                if msg != rule.error_message:
                    failing_messages[idx] = msg

    if rule.type == "unique":
        # Set-based: judged on the whole column in DuckDB, never one value.
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

    else:
        # 3.0.5 (conformance sweep): every other rule type is evaluated per
        # record with the single-path handler on the RAW record. The native
        # branches read DataFrame cells, which pandas coerces (an integer
        # column with a None gap becomes float: 12 reads 12.0; booleans and
        # lists lose their JSON type), and re-implemented each handler — the
        # source of every single/batch split the sweeps found. Parity now
        # holds by construction.
        _evaluate_per_record()

    # The min_age/max_age add-on applies to every rule type, as on the single
    # path (_check_age after the rule's own check). 3.0.5: per record — a
    # declared layout is read through the fixed-width gate, which DuckDB's
    # TRY_STRPTIME cannot express (#203: it read '90' as the year 0090).
    if rule.min_age is not None or rule.max_age is not None:
        for idx in range(len(df)):
            raw = records[idx].get(field) if records is not None else df[field].iloc[idx]
            absent = _is_field_absent(raw) if records is not None else _batch_absent(raw)
            if absent or idx in failing:
                continue  # the rule's own failure is reported, as on the single path
            try:
                if _check_age(raw, rule):
                    failing.add(idx)
            except Exception:
                logger.exception("validate_batch: age check raised on rule=%s record=%d — failing closed",
                                 rule.name, idx)
                failing.add(idx)
                failing_messages[idx] = _RULE_ERROR_PREFIX

    return failing
