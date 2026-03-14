"""
Validation engine — the core of OpenDQV.

Two modes:
  - validate_record(): Pure Python, single record, fast (sub-50ms target)
  - validate_batch(): DuckDB-powered, batch of records, high throughput

Both return structured results with per-field errors and severity.
"""

import csv
import os
import re
import logging
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

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

    If the `regex` library is available, enforces _REGEX_TIMEOUT seconds.
    On timeout, returns False (treat as no-match / validation failure) and
    logs a warning so operators can identify pathological patterns.
    Falls back to the standard `re` library if `regex` is not installed.
    """
    if _HAS_REGEX_LIB:
        try:
            return bool(_regex_lib.match(compiled_pattern.pattern, str_val, timeout=_REGEX_TIMEOUT))
        except _regex_lib.TimeoutError:
            logger.warning(
                "regex_timeout pattern=%r input_length=%d — treating as no-match",
                compiled_pattern.pattern, len(str_val),
            )
            return False
    return bool(compiled_pattern.match(str_val))

import duckdb
import pandas as pd

from .rule_parser import Rule, Severity, _BUILTIN_PATTERNS
from .trace_log import write_trace_entry

logger = logging.getLogger(__name__)


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
    __slots__ = ("field", "rule", "message", "severity")

    def __init__(self, field: str, rule: str, message: str, severity: str):
        self.field = field
        self.rule = rule
        self.message = message
        self.severity = severity

    def to_dict(self) -> dict:
        return {"field": self.field, "rule": self.rule, "message": self.message, "severity": self.severity}


# ── Single-record validation (pure Python, no DuckDB) ───────────────

def validate_record(
    record: dict,
    rules: list[Rule],
    contract_name: str = "",
    context: Optional[str] = None,
    record_index: int = 0,
    sensitive_fields: Optional[list] = None,
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

    for rule in rules:
        value = record.get(rule.field)
        failure = _check_rule(value, rule, record)
        if not failure:
            failure = _check_age(value, rule)

        if failure:
            entry = FieldError(
                field=rule.field,
                rule=rule.name,
                message=failure,
                severity=rule.severity.value,
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
    actual = str((record or {}).get(cond_field, ""))
    if "value" in rule.condition:
        return actual == str(rule.condition["value"])
    if "not_value" in rule.condition:
        return actual != str(rule.condition["not_value"])
    return True


def _validate_checksum(value: str, algorithm: str) -> bool:
    """Validate identifier check digits. Returns True if checksum is valid."""
    s = str(value).strip().upper()

    if algorithm == "mod10_gs1":
        # GS1 Mod-10 — used for GTIN-8, GTIN-12, GTIN-13, GTIN-14, GLN, SSCC
        digits = s.replace(" ", "").replace("-", "")
        if not digits.isdigit():
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
            elif ch.isdigit():
                numeric += ch
            else:
                return False
        try:
            return int(numeric) % 97 == 1
        except ValueError:
            return False

    elif algorithm == "isin_mod11":
        # ISIN: country code (2 alpha) + 9 alphanum + check digit; Luhn mod-10 over expanded digits
        if len(s) != 12:
            return False
        # Expand: letters → digits (A=10..Z=35)
        expanded = ""
        for ch in s[:-1]:
            if ch.isalpha():
                expanded += str(ord(ch) - ord('A') + 10)
            elif ch.isdigit():
                expanded += ch
            else:
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
            elif ch.isdigit():
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
        if len(digits) != 10 or not digits.isdigit():
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
        if len(digits) != 11 or not digits.isdigit():
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
        logger.warning("Unknown checksum algorithm '%s'", algorithm)
        return True  # unknown algorithm — pass through


def _check_rule(value, rule: Rule, record: Optional[dict] = None) -> Optional[str]:
    """
    Check a single value against a single rule.
    Returns the error message string if failed, None if passed.
    record is required for cross-field rule types (compare, required_if, condition).
    """
    if not _check_condition(rule, record):
        return None  # condition not met — rule is inapplicable for this record

    if rule.type == "not_empty":
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return rule.error_message
        return None

    if rule.type == "regex":
        if not rule.pattern:
            return None
        str_val = str(value) if value is not None else ""
        # Expand built-in pattern shorthands
        pattern = _BUILTIN_PATTERNS.get(rule.pattern, rule.pattern)
        compiled = rule.compiled_pattern or re.compile(pattern)
        matched = _safe_match(compiled, str_val)
        if rule.negate:
            if matched:    # negate=True means field must NOT match
                return rule.error_message
        else:
            if not matched:
                return rule.error_message
        return None

    if rule.type == "min":
        if value is None:
            return rule.error_message
        try:
            if float(value) < rule.min_value:
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "max":
        if value is None:
            return rule.error_message
        try:
            if float(value) > rule.max_value:
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "range":
        if value is None:
            return rule.error_message
        try:
            v = float(value)
            if rule.min_value is not None and v < rule.min_value:
                return rule.error_message
            if rule.max_value is not None and v > rule.max_value:
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "min_length":
        str_val = str(value) if value is not None else ""
        if len(str_val) < (rule.min_length or 0):
            return rule.error_message
        return None

    if rule.type == "max_length":
        str_val = str(value) if value is not None else ""
        if len(str_val) > (rule.max_length or 99999):
            return rule.error_message
        return None

    if rule.type == "date_format":
        if value is None:
            return rule.error_message
        str_val = str(value)
        # Try common date formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                datetime.strptime(str_val, fmt)
                return None
            except ValueError:
                continue
        return rule.error_message

    if rule.type == "unique":
        # Single-record mode cannot check uniqueness — skip silently
        return None

    if rule.type == "compare":
        # Cross-field comparison: field <op> compare_to
        # Works with numbers, ISO date strings, and plain strings.
        if not rule.compare_to or not rule.compare_op:
            logger.warning("compare rule '%s' missing compare_to or compare_op", rule.name)
            return None
        if value is None:
            return rule.error_message
        if rule.compare_to in ("today", "now"):
            if rule.compare_to == "today":
                other = datetime.today().strftime("%Y-%m-%d")
            else:
                other = datetime.now().isoformat()
        else:
            other = (record or {}).get(rule.compare_to)
            if other is None:
                return rule.error_message
        try:
            a, b = float(value), float(other)
        except (TypeError, ValueError):
            try:
                a = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                b = datetime.fromisoformat(str(other).replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                if getattr(rule, 'algorithm', None) == 'semver':
                    try:
                        def _semver_tuple(v):
                            parts = str(v).lstrip('v').split('.')
                            return tuple(int(x) for x in parts[:3])
                        a = _semver_tuple(value)
                        b = _semver_tuple(other)
                    except (ValueError, TypeError):
                        a, b = str(value), str(other)
                else:
                    a, b = str(value), str(other)
        _ops = {
            "gt": lambda x, y: x > y,
            "lt": lambda x, y: x < y,
            "gte": lambda x, y: x >= y,
            "lte": lambda x, y: x <= y,
            "eq": lambda x, y: x == y,
            "neq": lambda x, y: x != y,
        }
        op_fn = _ops.get(rule.compare_op)
        if op_fn is None:
            logger.warning("compare rule '%s' has unknown compare_op '%s'", rule.name, rule.compare_op)
            return None
        if not op_fn(a, b):
            return rule.error_message
        return None

    if rule.type == "required_if":
        # Field is required when another field equals a specific value.
        if not rule.required_if:
            return None
        trigger_field = rule.required_if.get("field")
        trigger_value = str(rule.required_if.get("value", ""))
        actual = str((record or {}).get(trigger_field, ""))
        if actual == trigger_value:
            if value is None or (isinstance(value, str) and value.strip() == ""):
                return rule.error_message
        return None

    if rule.type == "lookup":
        # Value must appear in a reference list — local file or HTTP endpoint.
        # A missing / null field silently passes — use not_empty to enforce presence.
        if not rule.lookup_file:
            logger.warning("lookup rule '%s' missing lookup_file", rule.name)
            return None
        if value is None:
            return None
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
            # Validate each element in the list
            for item in value:
                if str(item) not in valid_values:
                    return rule.error_message
        elif str(value) not in valid_values:
            return rule.error_message
        return None

    if rule.type == "checksum":
        if not rule.checksum_algorithm:
            logger.warning("checksum rule '%s' missing checksum_algorithm", rule.name)
            return None
        if value is None:
            return rule.error_message
        if not _validate_checksum(str(value), rule.checksum_algorithm):
            return rule.error_message
        return None

    if rule.type == "cross_field_range":
        if value is None:
            return rule.error_message
        rec = record or {}
        try:
            v = float(value)
            if rule.cross_min_field:
                low = rec.get(rule.cross_min_field)
                if low is None or v < float(low):
                    return rule.error_message
            if rule.cross_max_field:
                high = rec.get(rule.cross_max_field)
                if high is None or v > float(high):
                    return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "field_sum":
        if not rule.sum_fields or rule.sum_equals is None:
            logger.warning("field_sum rule '%s' missing sum_fields or sum_equals", rule.name)
            return None
        rec = record or {}
        try:
            total = sum(float(rec.get(f, 0) or 0) for f in rule.sum_fields)
            tolerance = rule.sum_tolerance if rule.sum_tolerance is not None else 0.0
            if abs(total - rule.sum_equals) > tolerance:
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "forbidden_if":
        if not rule.forbidden_if:
            return None
        trigger_field = rule.forbidden_if.get("field")
        trigger_value = str(rule.forbidden_if.get("value", ""))
        actual = str((record or {}).get(trigger_field, ""))
        if actual == trigger_value:
            # Field must be absent/empty when condition is met
            if value is not None and not (isinstance(value, str) and value.strip() == ""):
                return rule.error_message
        return None

    if rule.type == "conditional_value":
        # Field must equal rule.must_equal when the condition block is met.
        # condition is already checked at the top of _check_rule, so if we get here the condition passed.
        if rule.must_equal is None:
            return None
        if value is None or str(value) != str(rule.must_equal):
            return rule.error_message
        return None

    if rule.type == "date_diff":
        # Difference between this field and date_diff_field, in days or years.
        if not rule.date_diff_field:
            logger.warning("date_diff rule '%s' missing date_diff_field", rule.name)
            return None
        if value is None:
            return rule.error_message
        other_val = (record or {}).get(rule.date_diff_field)
        if other_val is None:
            return rule.error_message
        try:
            def _parse_date(v):
                s = str(v).strip()
                for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                    try:
                        return datetime.strptime(s, fmt).date()
                    except ValueError:
                        continue
                raise ValueError(f"Cannot parse date: {v!r}")

            d1 = _parse_date(value)
            d2 = _parse_date(other_val)
            delta = (d1 - d2).days  # signed: positive if d1 is later

            unit = rule.date_diff_unit or "days"
            if unit == "years":
                diff = abs(delta) / 365.25
            else:
                diff = float(delta)

            if rule.min_value is not None and diff < rule.min_value:
                return rule.error_message
            if rule.max_value is not None and diff > rule.max_value:
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type == "ratio_check":
        # field_a / field_b within range — LTV, occupancy rate, NRW%
        if not rule.ratio_numerator or not rule.ratio_denominator:
            logger.warning("ratio_check rule '%s' missing ratio_numerator or ratio_denominator", rule.name)
            return None
        rec = record or {}
        try:
            num = float(rec.get(rule.ratio_numerator, 0) or 0)
            den = float(rec.get(rule.ratio_denominator, 0) or 0)
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

    if rule.type == "conditional_lookup":
        # Lookup list depends on the value of another field.
        # Uses: condition_field, lookup_map (in rule.lookup_file as JSON path or inline)
        # For now: route to lookup with the condition already handled by _check_condition.
        # The YAML pattern uses multiple rules with condition blocks.
        # This type is an alias that documents intent.
        # Fall through to lookup logic:
        if not rule.lookup_file:
            logger.warning("conditional_lookup rule '%s' missing lookup_file", rule.name)
            return None
        if value is None:
            return rule.error_message
        try:
            if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
                ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
                valid_values = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
            else:
                valid_values = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
        except (FileNotFoundError, KeyError, OSError, RuntimeError) as exc:
            logger.error("conditional_lookup rule '%s' could not load '%s': %s", rule.name, rule.lookup_file, exc)
            return rule.error_message
        if str(value) not in valid_values:
            return rule.error_message
        return None

    if rule.type == "geospatial_bounds":
        # Validates that a lat/lon pair falls within a bounding box.
        # The field being checked is treated as latitude.
        # rule.geo_lon_field contains the longitude field name.
        if value is None:
            return rule.error_message
        rec = record or {}
        try:
            lat = float(value)

            # Check latitude bounds
            if rule.geo_min_lat is not None and lat < rule.geo_min_lat:
                return rule.error_message
            if rule.geo_max_lat is not None and lat > rule.geo_max_lat:
                return rule.error_message

            # Check longitude bounds if lon_field specified
            if rule.geo_lon_field:
                lon_val = rec.get(rule.geo_lon_field)
                if lon_val is None:
                    return rule.error_message
                lon = float(lon_val)
                if rule.geo_min_lon is not None and lon < rule.geo_min_lon:
                    return rule.error_message
                if rule.geo_max_lon is not None and lon > rule.geo_max_lon:
                    return rule.error_message

            # Basic validity: lat in [-90, 90], lon in [-180, 180]
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

    if rule.type == "age_match":
        if not rule.dob_field:
            logger.warning("age_match rule '%s' missing dob_field", rule.name)
            return None
        if value is None:
            return rule.error_message
        dob_val = (record or {}).get(rule.dob_field)
        if dob_val is None:
            return None  # dob_required covers absence
        try:
            declared = int(float(value))
            dob = datetime.strptime(str(dob_val), "%Y-%m-%d")
            today = datetime.today()
            computed = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            tol = rule.age_tolerance if rule.age_tolerance is not None else 0
            if not (computed - tol <= declared <= computed):
                return rule.error_message
        except (TypeError, ValueError):
            return rule.error_message
        return None

    if rule.type not in ("not_empty", "regex", "min", "max", "range", "min_length",
                          "max_length", "date_format", "unique", "compare",
                          "required_if", "lookup", "checksum", "cross_field_range",
                          "field_sum", "forbidden_if", "conditional_value",
                          "date_diff", "ratio_check", "conditional_lookup",
                          "geospatial_bounds", "age_match"):
        logger.warning("Unknown rule type '%s' for rule '%s'", rule.type, rule.name)

    return None


def _check_lookup_path_safe(file_path: str) -> Path:
    """
    SEC-002: Path traversal protection for local lookup_file paths.

    Resolves the path and verifies it lies within the configured contracts
    directory. Raises ValueError on traversal attempts (e.g. ../../etc/passwd).
    """
    import config as _cfg
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

    # Fetch outside the lock to avoid holding it during network I/O
    try:
        headers = {"User-Agent": "OpenDQV-lookup/1.0"}
        if auth_header:
            import os
            import re as _re
            auth_value = _re.sub(r'\$\{([^}]+)\}', lambda m: os.environ.get(m.group(1), ""), auth_header)
            headers["Authorization"] = auth_value
        req = urllib.request.Request(url, headers=headers)
        _MAX_LOOKUP_BYTES = 10_485_760  # 10 MB
        with urllib.request.urlopen(req, timeout=10) as resp:
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
    """Check min_age/max_age constraints. Runs after the type check passes."""
    if rule.min_age is None and rule.max_age is None:
        return None
    if value is None:
        return rule.error_message
    try:
        dob = datetime.strptime(str(value), "%Y-%m-%d")
        today = datetime.today()
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
    df["__idx__"] = range(total)

    con = duckdb.connect()
    try:
        con.register("data", df)

        # Per-row results: index -> {"errors": [], "warnings": []}
        row_results = {i: {"errors": [], "warnings": []} for i in range(total)}

        for rule in rules:
            if rule.field not in df.columns:
                logger.info("Skipping rule '%s' — field '%s' not in data", rule.name, rule.field)
                continue

            try:
                failing_indices = _batch_check_rule(con, df, rule)
            except Exception as e:
                # Log only rule metadata — never include record field values.
                logger.error("Error evaluating rule '%s' (field='%s'): %s", rule.name, rule.field, e)
                failing_indices = set(range(total))

            # Apply condition filter: exclude rows where the condition is not met.
            if rule.condition and failing_indices:
                cond_field = rule.condition.get("field", "")
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

            entry_template = {
                "field": rule.field,
                "rule": rule.name,
                "message": rule.error_message,
                "severity": rule.severity.value,
            }

            for idx in failing_indices:
                if rule.severity == Severity.ERROR:
                    row_results[idx]["errors"].append(entry_template)
                else:
                    row_results[idx]["warnings"].append(entry_template)
    finally:
        con.close()

    # Build results
    results = []
    passed = 0
    total_errors = 0
    total_warnings = 0
    rule_failure_counts: dict = {}  # rule_name → count of records failing that rule

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
        fields_validated = [rule.field for rule in rules]
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


def _batch_check_rule(con, df: pd.DataFrame, rule: Rule) -> set[int]:
    """Run a single rule against the batch via DuckDB. Returns set of failing row indices."""
    field = rule.field
    failing = set()

    if rule.type == "regex" and rule.pattern:
        # DuckDB doesn't support \w, \s, \d shorthand classes — fall back to Python.
        # Log at DEBUG so operators can identify which contracts use the slower path.
        logger.debug(
            "regex_python_fallback field=%s pattern=%r batch_size=%d rule=%s",
            field, rule.pattern, len(df), rule.name,
        )
        compiled = rule.compiled_pattern or re.compile(rule.pattern)
        for idx, val in enumerate(df[field]):
            str_val = str(val) if val is not None else ""
            matched = _safe_match(compiled, str_val)
            if rule.negate:
                if matched:
                    failing.add(idx)
            else:
                if not matched:
                    failing.add(idx)

    elif rule.type == "min" and rule.min_value is not None:
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR CAST("{field}" AS DOUBLE) < {rule.min_value}"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "max" and rule.max_value is not None:
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR CAST("{field}" AS DOUBLE) > {rule.max_value}"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "range" and rule.min_value is not None and rule.max_value is not None:
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR NOT (CAST("{field}" AS DOUBLE) BETWEEN {rule.min_value} AND {rule.max_value})"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "not_empty":
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR CAST("{field}" AS VARCHAR) = ''"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "min_length" and rule.min_length is not None:
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR LENGTH(CAST("{field}" AS VARCHAR)) < {rule.min_length}"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "max_length" and rule.max_length is not None:
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NOT NULL AND LENGTH(CAST("{field}" AS VARCHAR)) > {rule.max_length}"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "date_format":
        query = f"""SELECT __idx__ FROM data WHERE "{field}" IS NULL OR TRY_CAST("{field}" AS DATE) IS NULL"""
        for r in con.execute(query).fetchall():
            failing.add(r[0])

    elif rule.type == "unique":
        if rule.group_by:
            # Unique within groups — duplicates within same group_by values
            valid_cols = [g for g in rule.group_by if g in df.columns]
            if valid_cols:
                # Fall back to Python for grouped unique (DuckDB group_by with dynamic cols)
                for idx in range(len(df)):
                    val = df[field].iloc[idx]
                    group_key = tuple(str(df[g].iloc[idx]) if g in df.columns else "" for g in rule.group_by)
                    # Count how many records have same field value within same group
                    count = sum(
                        1 for j in range(len(df))
                        if str(df[field].iloc[j]) == str(val) and
                           tuple(str(df[g].iloc[j]) if g in df.columns else "" for g in rule.group_by) == group_key
                    )
                    if count > 1:
                        failing.add(idx)
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
        else:
            _ops = {
                "gt": lambda a, b: a > b,
                "lt": lambda a, b: a < b,
                "gte": lambda a, b: a >= b,
                "lte": lambda a, b: a <= b,
                "eq": lambda a, b: a == b,
                "neq": lambda a, b: a != b,
            }
            op_fn = _ops.get(rule.compare_op)
            if op_fn:
                for idx in range(len(df)):
                    a_raw = df[field].iloc[idx]
                    if is_temporal_sentinel:
                        if rule.compare_to == "today":
                            b_raw = datetime.today().strftime("%Y-%m-%d")
                        else:
                            b_raw = datetime.now().isoformat()
                    else:
                        b_raw = df[rule.compare_to].iloc[idx]
                    if a_raw is None or (isinstance(a_raw, float) and pd.isna(a_raw)):
                        failing.add(idx)
                        continue
                    if not is_temporal_sentinel and (b_raw is None or (isinstance(b_raw, float) and pd.isna(b_raw))):
                        failing.add(idx)
                        continue
                    try:
                        a, b = float(a_raw), float(b_raw)
                    except (TypeError, ValueError):
                        try:
                            a = datetime.fromisoformat(str(a_raw).replace("Z", "+00:00"))
                            b = datetime.fromisoformat(str(b_raw).replace("Z", "+00:00"))
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
            query = (
                f'SELECT __idx__ FROM data '
                f'WHERE CAST("{trigger_field}" AS VARCHAR) = $trigger_val '
                f'AND ("{field}" IS NULL OR CAST("{field}" AS VARCHAR) = \'\')'
            )
            for r in con.execute(query, {"trigger_val": trigger_value}).fetchall():
                failing.add(r[0])

    elif rule.type == "lookup" and rule.lookup_file:
        try:
            if rule.lookup_file.startswith("http://") or rule.lookup_file.startswith("https://"):
                ttl = rule.cache_ttl if rule.cache_ttl is not None else _HTTP_LOOKUP_DEFAULT_TTL
                valid_values = _load_http_lookup_set(rule.lookup_file, rule.lookup_field or "", ttl, auth_header=rule.lookup_auth_header)
            else:
                valid_values = _load_lookup_set(rule.lookup_file, rule.lookup_field or "")
            for idx in range(len(df)):
                val = df[field].iloc[idx]
                if val is None or (isinstance(val, float) and pd.isna(val)) or str(val) not in valid_values:
                    failing.add(idx)
        except (FileNotFoundError, KeyError, OSError, RuntimeError) as exc:
            logger.warning("lookup rule '%s' skipped (infrastructure error, not failing batch): %s", rule.name, exc)

    elif rule.type == "checksum" and rule.checksum_algorithm:
        for idx, val in enumerate(df[field]):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                failing.add(idx)
            elif not _validate_checksum(str(val), rule.checksum_algorithm):
                failing.add(idx)

    elif rule.type == "cross_field_range":
        for idx in range(len(df)):
            val = df[field].iloc[idx]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                failing.add(idx)
                continue
            try:
                v = float(val)
                fail = False
                if rule.cross_min_field and rule.cross_min_field in df.columns:
                    low = df[rule.cross_min_field].iloc[idx]
                    if low is None or (isinstance(low, float) and pd.isna(low)) or v < float(low):
                        fail = True
                if not fail and rule.cross_max_field and rule.cross_max_field in df.columns:
                    high = df[rule.cross_max_field].iloc[idx]
                    if high is None or (isinstance(high, float) and pd.isna(high)) or v > float(high):
                        fail = True
                if fail:
                    failing.add(idx)
            except (TypeError, ValueError):
                failing.add(idx)

    elif rule.type == "field_sum" and rule.sum_fields and rule.sum_equals is not None:
        tolerance = rule.sum_tolerance if rule.sum_tolerance is not None else 0.0
        for idx in range(len(df)):
            try:
                total = 0.0
                for f in rule.sum_fields:
                    if f in df.columns:
                        v = df[f].iloc[idx]
                        total += float(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else 0.0
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
                f'AND CAST("{field}" AS VARCHAR) != \'\''
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
                if val is None or other_val is None or (isinstance(val, float) and pd.isna(val)):
                    failing.add(idx)
                    continue
                try:
                    def _parse_date(v):
                        s = str(v).strip()
                        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
                            try:
                                return datetime.strptime(s, fmt).date()
                            except ValueError:
                                continue
                        raise ValueError(f"Cannot parse: {v!r}")
                    d1 = _parse_date(val)
                    d2 = _parse_date(other_val)
                    delta = (d1 - d2).days
                    diff = abs(delta) / 365.25 if unit == "years" else float(delta)
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
                    if den is None or (isinstance(den, float) and pd.isna(den)) or float(den) == 0:
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
            if val is None or (isinstance(val, float) and pd.isna(val)):
                failing.add(idx)
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

                if not fail and rule.geo_lon_field and rule.geo_lon_field in df.columns:
                    lon_val = df[rule.geo_lon_field].iloc[idx]
                    if lon_val is None or (isinstance(lon_val, float) and pd.isna(lon_val)):
                        fail = True
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

    # Age checks — apply to any rule with min_age/max_age (typically date fields)
    if rule.min_age is not None or rule.max_age is not None:
        age_conditions = []
        age_conditions.append(f'"{field}" IS NULL')
        age_conditions.append(f'TRY_CAST("{field}" AS DATE) IS NULL')
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
        age_query = f"SELECT __idx__ FROM data WHERE {' OR '.join(age_conditions)}"
        for r in con.execute(age_query).fetchall():
            failing.add(r[0])

    return failing
