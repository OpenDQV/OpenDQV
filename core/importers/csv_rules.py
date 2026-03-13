"""
Convert a CSV rule definition into an OpenDQV YAML contract.

Designed for non-technical users who prefer spreadsheet-style rule authoring.

Expected CSV columns::

    field, rule_type, value, severity, error_message
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Supported rule types and their value semantics
# ---------------------------------------------------------------------------

# rule_type -> handler(field, value_str) -> partial rule dict
# value_str may be empty for types that don't need a value.

def _handle_not_empty(field: str, _value: str) -> dict:
    return {
        "type": "not_empty",
        "field": field,
        "error_message": f"{field} must not be empty",
    }


def _handle_regex(field: str, value: str) -> dict:
    return {
        "type": "regex",
        "field": field,
        "pattern": value,
        "error_message": f"{field} must match pattern {value}",
    }


def _handle_min(field: str, value: str) -> dict:
    num = _to_number(value)
    rule: dict[str, Any] = {
        "type": "min",
        "field": field,
        "error_message": f"{field} must be at least {num}",
    }
    if num is not None:
        rule["min"] = num
    return rule


def _handle_max(field: str, value: str) -> dict:
    num = _to_number(value)
    rule: dict[str, Any] = {
        "type": "max",
        "field": field,
        "error_message": f"{field} must be at most {num}",
    }
    if num is not None:
        rule["max"] = num
    return rule


def _handle_range(field: str, value: str) -> dict:
    """Value format: ``"min,max"``."""
    parts = [p.strip() for p in value.split(",")]
    min_val = _to_number(parts[0]) if len(parts) > 0 else None
    max_val = _to_number(parts[1]) if len(parts) > 1 else None
    desc_parts = []
    if min_val is not None:
        desc_parts.append(str(min_val))
    if max_val is not None:
        desc_parts.append(str(max_val))
    range_desc = " and ".join(desc_parts) if desc_parts else "valid range"
    rule: dict[str, Any] = {
        "type": "range",
        "field": field,
        "error_message": f"{field} must be between {range_desc}",
    }
    if min_val is not None:
        rule["min"] = min_val
    if max_val is not None:
        rule["max"] = max_val
    return rule


def _handle_min_length(field: str, value: str) -> dict:
    num = int(value) if value else 0
    return {
        "type": "min_length",
        "field": field,
        "min_length": num,
        "error_message": f"{field} must be at least {num} characters",
    }


def _handle_max_length(field: str, value: str) -> dict:
    num = int(value) if value else 0
    return {
        "type": "max_length",
        "field": field,
        "max_length": num,
        "error_message": f"{field} must be at most {num} characters",
    }


def _handle_unique(field: str, _value: str) -> dict:
    return {
        "type": "unique",
        "field": field,
        "error_message": f"{field} must be unique",
    }


def _handle_date_format(field: str, _value: str) -> dict:
    return {
        "type": "date_format",
        "field": field,
        "error_message": f"{field} must be a valid date",
    }


_RULE_HANDLERS: dict[str, Any] = {
    "not_empty": _handle_not_empty,
    "regex": _handle_regex,
    "min": _handle_min,
    "max": _handle_max,
    "range": _handle_range,
    "min_length": _handle_min_length,
    "max_length": _handle_max_length,
    "unique": _handle_unique,
    "date_format": _handle_date_format,
}


def _to_number(s: str) -> int | float | None:
    """Try to parse a string as int, then float, else None."""
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fixup_duplicate_names(rules: list[dict]) -> None:
    """Rename first occurrence to include ``_1`` when duplicates exist."""
    seen_bases: dict[str, list[dict]] = {}
    for rule in rules:
        name = rule["name"]
        parts = name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            base = parts[0]
        else:
            base = name
        seen_bases.setdefault(base, []).append(rule)

    for base, group in seen_bases.items():
        if len(group) > 1 and not group[0]["name"].endswith("_1"):
            group[0]["name"] = f"{base}_1"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_csv_rules(csv_content: str, contract_name: str = "csv_import") -> dict:
    """
    Convert a CSV rule definition into an OpenDQV contract.

    CSV columns: field, rule_type, value, severity, error_message

    - value meaning depends on rule_type:
      - regex: the pattern
      - min/max: the number
      - range: "min,max"
      - min_length/max_length: the number
      - not_empty/unique/date_format: ignored

    Returns same structure as GX importer::

        {
            "contract": {...},
            "stats": {"total_rules": N, "imported": N, "skipped": N},
            "skipped": [...]
        }
    """
    reader = csv.DictReader(io.StringIO(csv_content))

    name_counter: Counter[str] = Counter()
    rules: list[dict] = []
    skipped: list[dict] = []
    total = 0

    for row in reader:
        total += 1

        field = row.get("field", "").strip()
        rule_type = row.get("rule_type", "").strip()
        value = row.get("value", "").strip()
        severity = row.get("severity", "error").strip().lower()
        error_message = row.get("error_message", "").strip()

        if not field or not rule_type:
            skipped.append({
                "row": total,
                "reason": "missing field or rule_type",
            })
            continue

        handler = _RULE_HANDLERS.get(rule_type)
        if handler is None:
            skipped.append({
                "row": total,
                "field": field,
                "rule_type": rule_type,
                "reason": f"unsupported rule_type: {rule_type}",
            })
            continue

        try:
            rule = handler(field, value)
        except Exception as exc:
            skipped.append({
                "row": total,
                "field": field,
                "rule_type": rule_type,
                "reason": f"handler error: {exc}",
            })
            continue

        # Override default error_message if provided
        if error_message:
            rule["error_message"] = error_message

        # Severity
        if severity not in ("error", "warning"):
            severity = "error"
        rule["severity"] = severity

        # Build unique name
        base_name = f"{rule['field']}_{rule['type']}"
        name_counter[base_name] += 1
        count = name_counter[base_name]
        rule_name = base_name if count == 1 else f"{base_name}_{count}"
        rule["name"] = rule_name

        rules.append(rule)

    _fixup_duplicate_names(rules)

    contract = {
        "name": contract_name,
        "version": "1.0",
        "description": "Imported from CSV rules",
        "owner": "imported-from-csv",
        "status": "active",
        "rules": rules,
    }

    return {
        "contract": contract,
        "stats": {
            "total_rules": total,
            "imported": len(rules),
            "skipped": len(skipped),
        },
        "skipped": skipped,
    }


def csv_rules_to_yaml(csv_content: str, contract_name: str = "csv_import") -> str:
    """Convert CSV rules to OpenDQV YAML string."""
    result = import_csv_rules(csv_content, contract_name)
    output = {"contract": result["contract"]}
    return yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)
