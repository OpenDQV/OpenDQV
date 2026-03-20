"""
Convert a Great Expectations (GX) expectation suite JSON into an OpenDQV YAML contract.

Supports both GX 0.x and 1.x suite formats.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# GX expectation type -> handler
# Each handler receives kwargs (dict) and returns a list of partial rule dicts.
# ---------------------------------------------------------------------------

def _handle_not_null(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    return [{
        "type": "not_empty",
        "field": field,
        "error_message": f"{field} must not be empty",
    }]


def _handle_regex(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    pattern = kwargs.get("regex", "")
    return [{
        "type": "regex",
        "field": field,
        "pattern": pattern,
        "error_message": f"{field} must match pattern {pattern}",
    }]


def _handle_between(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    min_val = kwargs.get("min_value")
    max_val = kwargs.get("max_value")
    parts = []
    if min_val is not None:
        parts.append(str(min_val))
    if max_val is not None:
        parts.append(str(max_val))
    range_desc = " and ".join(parts) if parts else "valid range"
    rule: dict[str, Any] = {
        "type": "range",
        "field": field,
        "error_message": f"{field} must be between {range_desc}",
    }
    if min_val is not None:
        rule["min_value"] = min_val
    if max_val is not None:
        rule["max_value"] = max_val
    return [rule]


def _handle_length_between(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    min_val = kwargs.get("min_value")
    max_val = kwargs.get("max_value")
    rules: list[dict] = []
    if min_val is not None:
        rules.append({
            "type": "min_length",
            "field": field,
            "min_length": int(min_val),
            "error_message": f"{field} must be at least {int(min_val)} characters",
        })
    if max_val is not None:
        rules.append({
            "type": "max_length",
            "field": field,
            "max_length": int(max_val),
            "error_message": f"{field} must be at most {int(max_val)} characters",
        })
    return rules


def _handle_unique(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    return [{
        "type": "unique",
        "field": field,
        "error_message": f"{field} must be unique",
    }]


def _handle_column_min(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    min_val = kwargs.get("min_value")
    rule: dict[str, Any] = {
        "type": "min",
        "field": field,
        "error_message": f"{field} must be at least {min_val}",
    }
    if min_val is not None:
        rule["min_value"] = min_val
    return [rule]


def _handle_column_max(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    max_val = kwargs.get("max_value")
    rule: dict[str, Any] = {
        "type": "max",
        "field": field,
        "error_message": f"{field} must be at most {max_val}",
    }
    if max_val is not None:
        rule["max_value"] = max_val
    return [rule]


def _handle_be_in_set(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    value_set = kwargs.get("value_set") or []
    if not value_set:
        return []
    # No inline lookup support — map to regex like dbt accepted_values
    escaped = [re.escape(str(v)) for v in value_set]
    pattern = "^(" + "|".join(escaped) + ")$"
    return [{
        "type": "regex",
        "field": field,
        "pattern": pattern,
        "error_message": f"{field} must be one of: {', '.join(str(v) for v in value_set)}",
        # Note: OpenDQV has no native set/lookup with inline values; regex approximation used.
    }]


def _handle_dateutil_parseable(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    return [{
        "type": "date_format",
        "field": field,
        "error_message": f"{field} must be a parseable date",
    }]


def _handle_strftime(kwargs: dict) -> list[dict]:
    field = kwargs["column"]
    fmt = kwargs.get("strftime_format", "")
    rule: dict[str, Any] = {
        "type": "date_format",
        "field": field,
        "error_message": f"{field} must match date format {fmt}" if fmt else f"{field} must be a valid date",
    }
    if fmt:
        rule["format"] = fmt
    return [rule]


_HANDLERS: dict[str, Any] = {
    "expect_column_values_to_not_be_null": _handle_not_null,
    "expect_column_values_to_match_regex": _handle_regex,
    "expect_column_values_to_be_between": _handle_between,
    "expect_column_value_lengths_to_be_between": _handle_length_between,
    "expect_column_values_to_be_unique": _handle_unique,
    "expect_column_min_to_be_between": _handle_column_min,
    "expect_column_max_to_be_between": _handle_column_max,
    "expect_column_values_to_be_dateutil_parseable": _handle_dateutil_parseable,
    "expect_column_values_to_match_strftime_format": _handle_strftime,
    # MISS-GX-1: be_in_set mapped to regex (no native inline set/lookup support)
    "expect_column_values_to_be_in_set": _handle_be_in_set,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_gx_suite(suite_json: dict) -> dict:
    """
    Convert a GX expectation suite JSON dict into an OpenDQV contract dict.

    Returns:
        {
            "contract": {  # Ready to write as YAML
                "name": str,
                "version": "1.0",
                "description": str,
                "owner": "imported-from-gx",
                "status": "active",
                "rules": [...]
            },
            "stats": {
                "total_expectations": int,
                "imported": int,
                "skipped": int,
            },
            "skipped": [  # List of skipped expectations with reason
                {"expectation_type": str, "reason": str}
            ]
        }
    """
    # Detect GX version: 0.x uses expectation_suite_name, 1.x uses name
    suite_name = suite_json.get("expectation_suite_name") or suite_json.get("name") or "unnamed_suite"

    expectations = suite_json.get("expectations", [])
    total = len(expectations)

    rules: list[dict] = []
    skipped: list[dict] = []
    name_counter: Counter[str] = Counter()

    for exp in expectations:
        # 0.x: expectation_type, 1.x: type
        exp_type = exp.get("expectation_type") or exp.get("type", "")
        kwargs = exp.get("kwargs", {})
        meta = exp.get("meta", {})

        handler = _HANDLERS.get(exp_type)
        if handler is None:
            skipped.append({
                "expectation_type": exp_type,
                "reason": "unsupported expectation type",
            })
            continue

        if "column" not in kwargs:
            skipped.append({
                "expectation_type": exp_type,
                "reason": "missing column in kwargs",
            })
            continue

        # Determine severity from mostly
        mostly = kwargs.get("mostly")
        severity = "warning" if (mostly is not None and mostly < 1.0) else "error"

        # Extract description from meta.notes
        description = meta.get("notes", "")

        try:
            partial_rules = handler(kwargs)
        except Exception as exc:
            skipped.append({
                "expectation_type": exp_type,
                "reason": f"handler error: {exc}",
            })
            continue

        for rule in partial_rules:
            # Build unique name
            base_name = f"{rule['field']}_{rule['type']}"
            name_counter[base_name] += 1
            count = name_counter[base_name]
            rule_name = base_name if count == 1 else f"{base_name}_{count}"

            rule["name"] = rule_name
            rule["severity"] = severity
            if description:
                rule["description"] = description

            rules.append(rule)

    # Deduplicate names: if any base_name appeared more than once, rename the
    # first occurrence to include _1 for consistency.
    seen_bases: dict[str, list[dict]] = {}
    for rule in rules:
        # Strip any trailing _N to find the base
        name = rule["name"]
        # Check if this base has duplicates
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            base = parts[0]
        else:
            base = name

        seen_bases.setdefault(base, []).append(rule)

    # Rename first occurrences when there are duplicates
    for base, group in seen_bases.items():
        if len(group) > 1 and not group[0]["name"].endswith("_1"):
            group[0]["name"] = f"{base}_1"

    contract = {
        "name": suite_name,
        "version": "1.0",
        "description": f"Imported from Great Expectations suite: {suite_name}",
        "owner": "imported-from-gx",
        "status": "draft",
        "source": "import",
        "asset_id": f"gx::{suite_name}",
        "rules": rules,
    }

    return {
        "contract": contract,
        "stats": {
            "total_expectations": total,
            "imported": len(rules),
            "skipped": len(skipped),
        },
        "skipped": skipped,
    }


def gx_suite_to_yaml(suite_json: dict) -> str:
    """Convert GX suite to OpenDQV YAML string."""
    result = import_gx_suite(suite_json)
    output = {"contract": result["contract"]}
    return yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Export: OpenDQV rules -> GX expectation suite
# ---------------------------------------------------------------------------

def _rule_to_gx_expectation(rule) -> dict | None:
    """Convert a single OpenDQV Rule to a GX expectation dict."""
    kwargs = {"column": rule.field}

    if rule.type == "not_empty":
        exp_type = "expect_column_values_to_not_be_null"
    elif rule.type == "regex":
        exp_type = "expect_column_values_to_match_regex"
        kwargs["regex"] = rule.pattern or ""
    elif rule.type == "range":
        exp_type = "expect_column_values_to_be_between"
        if rule.min_value is not None:
            kwargs["min_value"] = rule.min_value
        if rule.max_value is not None:
            kwargs["max_value"] = rule.max_value
    elif rule.type == "min":
        exp_type = "expect_column_min_to_be_between"
        if rule.min_value is not None:
            kwargs["min_value"] = rule.min_value
    elif rule.type == "max":
        exp_type = "expect_column_max_to_be_between"
        if rule.max_value is not None:
            kwargs["max_value"] = rule.max_value
    elif rule.type == "min_length":
        exp_type = "expect_column_value_lengths_to_be_between"
        if rule.min_length is not None:
            kwargs["min_value"] = rule.min_length
    elif rule.type == "max_length":
        exp_type = "expect_column_value_lengths_to_be_between"
        if rule.max_length is not None:
            kwargs["max_value"] = rule.max_length
    elif rule.type == "unique":
        exp_type = "expect_column_values_to_be_unique"
    elif rule.type == "date_format":
        fmt = rule.format if hasattr(rule, "format") else rule.get("format") if isinstance(rule, dict) else None
        if fmt:
            exp_type = "expect_column_values_to_match_strftime_format"
            kwargs["strftime_format"] = fmt
        else:
            exp_type = "expect_column_values_to_be_dateutil_parseable"
    else:
        return None

    if rule.severity.value == "warning":
        kwargs["mostly"] = 0.95

    # BUG-GX-1: emit GX 1.x format ("type" key, not "expectation_type")
    return {
        "type": exp_type,
        "kwargs": kwargs,
        "meta": {"notes": rule.description} if rule.description else {},
    }


def export_gx_suite(contract_name: str, rules: list) -> dict:
    """
    Export a list of OpenDQV Rule objects as a Great Expectations expectation suite dict.

    Args:
        contract_name: Name of the contract (used as suite name).
        rules: List of Rule objects to convert.

    Returns:
        A GX-compatible expectation suite dict.
    """
    expectations = []
    skipped = []
    for rule in rules:
        exp = _rule_to_gx_expectation(rule)
        if exp:
            expectations.append(exp)
        else:
            skipped.append({
                "rule_name": rule.name,
                "rule_type": rule.type,
                "field": rule.field,
                "reason": "no GX equivalent",
            })

    # BUG-GX-1: GX 1.x uses "name" (not "expectation_suite_name")
    return {
        "name": contract_name,
        "expectations": expectations,
        "skipped": skipped,
        "meta": {
            "exported_from": "OpenDQV",
        },
    }
