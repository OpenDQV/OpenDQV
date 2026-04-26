"""Emit JSON Schema (draft 2020-12) from a DataContract's rules.

Cross-field and stateful rules (compare, unique, required_if, lookup) cannot be
expressed in plain JSON Schema. They are surfaced in `x-opendqv-unmapped` for
callers that want to know what was lost in translation.
"""

from typing import Any

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_NUMERIC_TYPES = {"number", "integer"}


def contract_to_jsonschema(contract) -> dict:
    """Convert a DataContract into a JSON Schema (draft 2020-12) document."""
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    unmapped: list[dict[str, Any]] = []

    for rule in contract.rules:
        field = rule.field
        if not field:
            unmapped.append({"rule": rule.name, "reason": "no field bound"})
            continue
        prop = properties.setdefault(field, {})
        _apply_rule(prop, rule, field, required, unmapped)

    schema = {
        "$schema": JSON_SCHEMA_DIALECT,
        "title": contract.name,
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if (contract.description or "").strip():
        schema["description"] = contract.description
    if required:
        schema["required"] = sorted(set(required))
    if unmapped:
        schema["x-opendqv-unmapped"] = unmapped
    return schema


def _apply_rule(prop: dict, rule, field: str, required: list, unmapped: list) -> None:
    rt = rule.type

    if rt == "not_empty":
        if field not in required:
            required.append(field)
        return

    if rt == "regex" and rule.pattern:
        prop["type"] = "string"
        prop["pattern"] = rule.pattern
        return

    if rt == "min" and rule.min_value is not None:
        if "type" not in prop or prop["type"] not in _NUMERIC_TYPES:
            prop["type"] = "number"
        prop["minimum"] = rule.min_value
        return

    if rt == "max" and rule.max_value is not None:
        if "type" not in prop or prop["type"] not in _NUMERIC_TYPES:
            prop["type"] = "number"
        prop["maximum"] = rule.max_value
        return

    if rt == "range":
        if "type" not in prop or prop["type"] not in _NUMERIC_TYPES:
            prop["type"] = "number"
        if rule.min_value is not None:
            prop["minimum"] = rule.min_value
        if rule.max_value is not None:
            prop["maximum"] = rule.max_value
        return

    if rt == "min_length" and rule.min_length is not None:
        prop["type"] = "string"
        prop["minLength"] = rule.min_length
        return

    if rt == "max_length" and rule.max_length is not None:
        prop["type"] = "string"
        prop["maxLength"] = rule.max_length
        return

    if rt == "allowed_values" and rule.allowed_values is not None:
        prop["enum"] = list(rule.allowed_values)
        return

    if rt == "date_format":
        prop["type"] = "string"
        fmt = (rule.format or "").upper()
        prop["format"] = "date-time" if "HH" in fmt or "%H" in fmt else "date"
        if rule.format:
            prop["x-opendqv-date-format"] = rule.format
        return

    unmapped.append({
        "rule": rule.name,
        "field": field,
        "type": rt,
        "reason": (
            "rule type cannot be expressed in plain JSON Schema; "
            "OpenDQV runtime enforces it"
        ),
    })
