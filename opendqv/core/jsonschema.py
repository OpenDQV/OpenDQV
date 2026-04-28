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
    # v2.3.23 P1-4 (Sonnet aa97290c8fc2e575f): conditional rules emit
    # if/then[/else] blocks at root. Unconditional rules stay in
    # `properties` exactly as before — the response shape only grows
    # `allOf` when conditional rules exist.
    all_of: list[dict[str, Any]] = []

    for rule in contract.rules:
        field = rule.field
        if not field:
            unmapped.append({"rule": rule.name, "reason": "no field bound"})
            continue
        if rule.condition:
            block = _build_conditional_block(rule, field, unmapped)
            if block is not None:
                all_of.append(block)
        else:
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
    if all_of:
        schema["allOf"] = all_of
    if unmapped:
        schema["x-opendqv-unmapped"] = unmapped
    return schema


def _build_conditional_block(rule, field: str, unmapped: list) -> dict | None:
    """Emit a JSON Schema if/then[/else] block for a rule with
    `condition`. Returns None for unsupported condition shapes (caller
    falls back to unconditional path or unmapped).

    Supported condition shapes (mirror what validator._check_condition
    actually applies):
      - {field, value}      → if {F == V} then constraint
      - {field, not_value}  → if {F == V} then nothing else constraint

    The "not_value" case uses if/then-empty/else-with-constraint to
    avoid the "field absent" trap of using `not` on an `if` schema —
    matches the runtime behaviour where the constraint applies when
    the field's value is NOT the not_value (including absent).
    """
    cond = rule.condition or {}
    cond_field = cond.get("field")
    if not cond_field:
        unmapped.append({
            "rule": rule.name, "field": field, "type": rule.type,
            "reason": "condition missing 'field' key — cannot express in JSON Schema",
        })
        return None

    # Build the inner constraint subschema by reusing _apply_rule
    # against a temporary prop dict.
    inner_prop: dict[str, Any] = {}
    inner_required: list[str] = []
    inner_unmapped: list[dict[str, Any]] = []
    _apply_rule(inner_prop, rule, field, inner_required, inner_unmapped)
    # If the rule's constraint can't be expressed (inner_unmapped),
    # bubble up to top-level unmapped and skip the conditional block.
    if inner_unmapped and not inner_prop:
        for u in inner_unmapped:
            u["reason"] = (
                "rule has condition but the constraint itself is not "
                "expressible in JSON Schema; OpenDQV runtime enforces it"
            )
            unmapped.append(u)
        return None

    constraint_subschema = {"properties": {field: inner_prop}}
    # required from the conditional inner rule (e.g. not_empty inside
    # a conditional) attaches inside the then/else branch, not at
    # contract root — the rule only requires the field when condition
    # matches.
    if inner_required:
        constraint_subschema["required"] = inner_required

    if "value" in cond:
        return {
            "if": {
                "properties": {cond_field: {"const": cond["value"]}},
                "required": [cond_field],
            },
            "then": constraint_subschema,
        }
    if "not_value" in cond:
        return {
            "if": {
                "properties": {cond_field: {"const": cond["not_value"]}},
                "required": [cond_field],
            },
            "then": {},
            "else": constraint_subschema,
        }

    # Unknown condition shape — surface in unmapped, don't emit the
    # rule at all (matches validator's fall-through to True for
    # unrecognised conditions, which means "rule applies always").
    unmapped.append({
        "rule": rule.name, "field": field, "type": rule.type,
        "reason": (
            f"condition shape {sorted(cond.keys())!r} not expressible in "
            "JSON Schema (supported: {field, value} or {field, not_value}). "
            "OpenDQV runtime enforces."
        ),
    })
    return None


def _apply_rule(prop: dict, rule, field: str, required: list, unmapped: list) -> None:
    rt = rule.type

    if rt == "not_empty":
        if field not in required:
            required.append(field)
        # v2.3.22 Cluster K (N-9): emit minLength: 1 so downstream
        # JSON-Schema validators reject the empty string. Without it,
        # `required` alone allows "" to pass — weaker structural
        # validation than the OpenDQV runtime enforces.
        prop["type"] = "string"
        if "minLength" not in prop or prop.get("minLength", 0) < 1:
            prop["minLength"] = 1
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

    # v2.3.22 Cluster K (N-9 honesty): give the unmapped reason enough
    # detail that a JSON Schema consumer knows whether the rule could
    # be expressed if values were inlined (lookup against a ref file)
    # vs. structurally inexpressible (compare, unique, required_if).
    if rt == "lookup":
        reason = (
            "lookup rule references an external reference file "
            f"({rule.lookup_file or 'unspecified'}); values are not "
            "inlined as `enum` at export time. OpenDQV runtime "
            "enforces the lookup. v2.4 may inline the reference list "
            "when accessible at export time."
        )
    else:
        reason = (
            "rule type cannot be expressed in plain JSON Schema; "
            "OpenDQV runtime enforces it"
        )
    unmapped.append({
        "rule": rule.name,
        "field": field,
        "type": rt,
        "reason": reason,
    })
