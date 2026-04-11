"""
Dataset profiler and rule suggester.

Analyzes a list of records and auto-generates an OpenDQV contract
with suggested validation rules based on observed data patterns.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

import duckdb
import pandas as pd


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_PHONE_RE = re.compile(r"^\+?1?\d{10,15}$")
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
]


def _is_numeric(value: Any) -> bool:
    """Check if a value is numeric (int or float, not bool)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        stripped = value.strip()
        # Reject phone-like strings (e.g. "+12025551001")
        if stripped.startswith("+") and len(stripped) > 1 and stripped[1:].isdigit():
            return False
        try:
            float(stripped)
            return True
        except (ValueError, TypeError):
            return False
    return False


def _to_number(value: Any) -> float:
    """Convert a value to float."""
    return float(value)


def _is_boolean(value: Any) -> bool:
    """Check if a value looks like a boolean."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return True
    return False


def _is_date(value: Any) -> bool:
    """Check if a string value is parseable as a date."""
    if not isinstance(value, str):
        return False
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _detect_date_format(values: list[str]) -> str | None:
    """Detect the most common date format from a list of string values."""
    fmt_counts: Counter[str] = Counter()
    for v in values:
        for fmt in _DATE_FORMATS:
            try:
                datetime.strptime(v, fmt)
                fmt_counts[fmt] += 1
                break
            except ValueError:
                continue
    if fmt_counts:
        return fmt_counts.most_common(1)[0][0]
    return None


def _is_null_or_empty(value: Any) -> bool:
    """Check if a value is null or an empty string."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


# ---------------------------------------------------------------------------
# Field profiling
# ---------------------------------------------------------------------------

def _profile_field(field_name: str, values: list[Any]) -> dict:
    """Profile a single field across all records."""
    total = len(values)
    non_null_values = [v for v in values if not _is_null_or_empty(v)]
    null_count = total - len(non_null_values)
    null_pct = null_count / total if total > 0 else 0.0

    unique_values = set()
    for v in non_null_values:
        if isinstance(v, (list, dict)):
            unique_values.add(str(v))
        else:
            unique_values.add(v)
    unique_count = len(unique_values)
    unique_pct = unique_count / len(non_null_values) if non_null_values else 0.0

    profile: dict[str, Any] = {
        "null_count": null_count,
        "null_pct": round(null_pct, 4),
        "unique_count": unique_count,
        "unique_pct": round(unique_pct, 4),
    }

    # Detect type
    if not non_null_values:
        profile["type"] = "string"
        profile["sample_values"] = []
        return profile

    numeric_count = sum(1 for v in non_null_values if _is_numeric(v))
    bool_count = sum(1 for v in non_null_values if _is_boolean(v))
    date_count = sum(1 for v in non_null_values if _is_date(v))

    nn = len(non_null_values)

    if bool_count == nn:
        field_type = "boolean"
    elif numeric_count == nn:
        field_type = "numeric"
    elif date_count == nn:
        field_type = "date"
    else:
        field_type = "string"

    profile["type"] = field_type

    # Type-specific stats
    if field_type == "numeric":
        nums = [_to_number(v) for v in non_null_values]
        profile["min"] = min(nums)
        profile["max"] = max(nums)
    elif field_type == "string":
        lengths = [len(str(v)) for v in non_null_values]
        profile["min_length"] = min(lengths) if lengths else 0
        profile["max_length"] = max(lengths) if lengths else 0

    # Sample values (up to 5 unique)
    samples = list(unique_values)[:5]
    profile["sample_values"] = samples

    return profile


# ---------------------------------------------------------------------------
# Rule suggestion
# ---------------------------------------------------------------------------

def _suggest_rules(field_name: str, profile: dict) -> list[dict]:
    """Generate suggested rules for a field based on its profile."""
    rules: list[dict] = []
    field_type = profile.get("type", "string")
    null_pct = profile.get("null_pct", 0.0)
    unique_pct = profile.get("unique_pct", 0.0)

    # 1. Not-empty rule: if no nulls observed, enforce it
    if null_pct == 0.0:
        rules.append({
            "name": f"{field_name}_not_empty",
            "type": "not_empty",
            "field": field_name,
            "severity": "error",
            "error_message": f"{field_name} must not be empty",
        })

    # 2. Uniqueness: if all values are unique
    if unique_pct == 1.0 and profile.get("unique_count", 0) > 1:
        rules.append({
            "name": f"{field_name}_unique",
            "type": "unique",
            "field": field_name,
            "severity": "error",
            "error_message": f"{field_name} must be unique",
        })

    # 3. Numeric range with 10% buffer
    if field_type == "numeric":
        observed_min = profile.get("min", 0)
        observed_max = profile.get("max", 0)
        span = observed_max - observed_min
        buffer = span * 0.1 if span > 0 else abs(observed_max) * 0.1 if observed_max != 0 else 1.0
        suggested_min = math.floor(observed_min - buffer)
        suggested_max = math.ceil(observed_max + buffer)
        rules.append({
            "name": f"{field_name}_range",
            "type": "range",
            "field": field_name,
            "min": suggested_min,
            "max": suggested_max,
            "severity": "warning",
            "error_message": f"{field_name} must be between {suggested_min} and {suggested_max}",
        })

    # 4. String length rules
    if field_type == "string":
        min_len = profile.get("min_length", 0)
        max_len = profile.get("max_length", 0)
        if min_len > 0:
            rules.append({
                "name": f"{field_name}_min_length",
                "type": "min_length",
                "field": field_name,
                "min_length": min_len,
                "severity": "warning",
                "error_message": f"{field_name} must be at least {min_len} characters",
            })
        if max_len > 0:
            rules.append({
                "name": f"{field_name}_max_length",
                "type": "max_length",
                "field": field_name,
                "max_length": max_len,
                "severity": "warning",
                "error_message": f"{field_name} must be at most {max_len} characters",
            })

    # 5. Date format rule
    if field_type == "date":
        sample_values = profile.get("sample_values", [])
        detected_fmt = _detect_date_format([str(v) for v in sample_values])
        rule: dict[str, Any] = {
            "name": f"{field_name}_date_format",
            "type": "date_format",
            "field": field_name,
            "severity": "error",
            "error_message": f"{field_name} must be a valid date",
        }
        if detected_fmt:
            rule["format"] = detected_fmt
        rules.append(rule)

    # 6. Email detection
    field_lower = field_name.lower()
    if "email" in field_lower or (
        field_type == "string"
        and profile.get("sample_values")
        and all(_EMAIL_RE.match(str(v)) for v in profile["sample_values"])
    ):
        rules.append({
            "name": f"{field_name}_email_format",
            "type": "regex",
            "field": field_name,
            "pattern": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$",
            "severity": "error",
            "error_message": f"{field_name} must be a valid email address",
        })

    # 7. Phone detection
    if "phone" in field_lower or "mobile" in field_lower:
        rules.append({
            "name": f"{field_name}_phone_format",
            "type": "regex",
            "field": field_name,
            "pattern": r"^\+?1?\d{10,15}$",
            "severity": "error",
            "error_message": f"{field_name} must be a valid phone number",
        })

    return rules


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def profile_records(records: list[dict], contract_name: str = "profiled") -> dict:
    """
    Analyze a list of records and suggest OpenDQV rules.

    Returns:
        {
            "contract": {
                "name": contract_name,
                "version": "1.0",
                "description": "Auto-profiled contract",
                "owner": "profiler",
                "status": "draft",
                "rules": [...]
            },
            "profile": {
                "record_count": N,
                "fields": {
                    "field_name": {
                        "type": "string|numeric|date|boolean|mixed",
                        "null_count": N,
                        "null_pct": float,
                        "unique_count": N,
                        "unique_pct": float,
                        "min": value,
                        "max": value,
                        "min_length": N,
                        "max_length": N,
                        "sample_values": [...]
                    }
                }
            }
        }
    """
    if not records:
        return {
            "contract": {
                "name": contract_name,
                "version": "1.0",
                "description": "Auto-profiled contract",
                "owner": "profiler",
                "status": "draft",
                "rules": [],
            },
            "profile": {
                "record_count": 0,
                "fields": {},
            },
        }

    # Collect all field names (preserve order of first appearance)
    all_fields: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            if key not in seen:
                all_fields.append(key)
                seen.add(key)

    # Gather values per field
    field_values: dict[str, list[Any]] = {f: [] for f in all_fields}
    for record in records:
        for f in all_fields:
            field_values[f].append(record.get(f))

    # Profile each field and suggest rules
    field_profiles: dict[str, dict] = {}
    all_rules: list[dict] = []

    for f in all_fields:
        profile = _profile_field(f, field_values[f])
        field_profiles[f] = profile
        rules = _suggest_rules(f, profile)
        all_rules.extend(rules)

    # DuckDB pass: enrich numeric fields with mean/stddev/percentiles,
    # and low-cardinality string fields with top_values frequency counts.
    try:
        df = pd.DataFrame(records)
        con = duckdb.connect()
        try:
            con.register("data", df)
            for f, profile in field_profiles.items():
                fq = '"' + f.replace('"', '""') + '"'
                if profile.get("type") == "numeric":
                    row = con.execute(
                        f"SELECT AVG(TRY_CAST({fq} AS DOUBLE)), "
                        f"STDDEV_SAMP(TRY_CAST({fq} AS DOUBLE)), "
                        f"quantile_cont(TRY_CAST({fq} AS DOUBLE), 0.25), "
                        f"quantile_cont(TRY_CAST({fq} AS DOUBLE), 0.50), "
                        f"quantile_cont(TRY_CAST({fq} AS DOUBLE), 0.75), "
                        f"quantile_cont(TRY_CAST({fq} AS DOUBLE), 0.95) "
                        f"FROM data WHERE {fq} IS NOT NULL"
                    ).fetchone()
                    if row:
                        avg, stddev, p25, p50, p75, p95 = row
                        if avg is not None:
                            profile["mean"] = round(float(avg), 4)
                        if stddev is not None:
                            profile["stddev"] = round(float(stddev), 4)
                        if p25 is not None:
                            profile["p25"] = round(float(p25), 4)
                        if p50 is not None:
                            profile["p50"] = round(float(p50), 4)
                        if p75 is not None:
                            profile["p75"] = round(float(p75), 4)
                        if p95 is not None:
                            profile["p95"] = round(float(p95), 4)
                elif profile.get("type") == "string":
                    if profile.get("unique_pct", 1.0) < 0.9 and profile.get("unique_count", 0) <= 50:
                        rows = con.execute(
                            f"SELECT CAST({fq} AS VARCHAR), COUNT(*) cnt "
                            f"FROM data WHERE {fq} IS NOT NULL "
                            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
                        ).fetchall()
                        profile["top_values"] = {str(r[0]): int(r[1]) for r in rows}
        finally:
            con.close()
    except Exception:
        pass  # DuckDB enrichment is best-effort; pure-Python profile is still returned

    return {
        "contract": {
            "name": contract_name,
            "version": "1.0",
            "description": "Auto-profiled contract",
            "owner": "profiler",
            "status": "draft",
            "rules": all_rules,
        },
        "profile": {
            "record_count": len(records),
            "fields": field_profiles,
        },
    }
