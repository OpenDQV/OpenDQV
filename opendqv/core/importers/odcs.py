"""
Open Data Contract Standard (ODCS) 3.1 importer and exporter.

ODCS 3.1 is the emerging universal standard for data contracts, supported by:
  - OpenMetadata v1.8+ (DataContract entity, import/export)
  - Soda (quality checks alignment)
  - Monte Carlo (contract metadata)
  - Data Contract CLI (bitol-io/data-contract-cli)

This module provides bidirectional conversion:
  import_odcs(contract_data)     → OpenDQV contract dict
  odcs_to_yaml(contract_data)    → (contract_name, YAML string)
  export_odcs(name, rules, ...)  → ODCS 3.1 dict
  contract_to_odcs_yaml(...)     → ODCS 3.1 YAML string

ODCS 3.1 top-level structure (canonical):
  apiVersion: v3.0.0 | v3.1.0
  kind: DataContract
  info:
    title: <str>         → contract name
    version: <str>       → contract version
    status: active | draft | archived
    description: <str>
    owner: <str>
  schema:
    - name: <table/entity>
      properties:
        - name: <field>
          required: true           → not_empty rule (error)
          unique: true             → unique rule (error)
          minLength: N             → min_length rule
          maxLength: N             → max_length rule
          quality:
            - type: not_null | notNull   → not_empty
            - type: unique               → unique
            - type: regex
              pattern: <str>
              mustBeSatisfied: <bool>    → error if true, warning if false
            - type: range
              min: N
              max: N
              mustBeSatisfied: <bool>
            - type: min
              min: N
            - type: max
              max: N
            - type: min_length | minLength
              minLength: N
            - type: max_length | maxLength
              maxLength: N
            - type: date_format | dateFormat
              format: <str>

mustBeSatisfied absent / false → severity: warning
mustBeSatisfied true            → severity: error
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# ODCS quality type → OpenDQV type normalisation
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, str] = {
    "not_null":   "not_empty",
    "notnull":    "not_empty",
    "not_empty":  "not_empty",
    "unique":     "unique",
    "regex":      "regex",
    "range":      "range",
    "min":        "min",
    "max":        "max",
    "min_length": "min_length",
    "minlength":  "min_length",
    "max_length": "max_length",
    "maxlength":  "max_length",
    "date_format":"date_format",
    "dateformat": "date_format",
}

# OpenDQV type → canonical ODCS 3.1 type string
_ODCS_TYPE_MAP: dict[str, str] = {
    "not_empty":  "not_null",
    "unique":     "unique",
    "regex":      "regex",
    "range":      "range",
    "min":        "min",
    "max":        "max",
    "min_length": "min_length",
    "max_length": "max_length",
    "date_format":"date_format",
    "min_age":    "range",   # export as range with min constraint
    "max_age":    "range",   # export as range with max constraint
}


def _severity(must_be_satisfied) -> str:
    """Convert ODCS mustBeSatisfied → OpenDQV severity string."""
    if must_be_satisfied is True:
        return "error"
    return "warning"


def _normalize_type(odcs_type: str) -> Optional[str]:
    return _TYPE_MAP.get(odcs_type.lower().replace("-", "_"))


# ---------------------------------------------------------------------------
# Per-quality-check converter
# ---------------------------------------------------------------------------

def _quality_to_rule(field: str, q: dict, rule_idx: int) -> Optional[dict]:
    """Convert one ODCS quality check dict to an OpenDQV rule dict, or None to skip."""
    odcs_type = q.get("type", "")
    dqr_type = _normalize_type(odcs_type)
    if not dqr_type:
        return None  # unsupported type — skip

    severity = _severity(q.get("mustBeSatisfied"))
    rule_name = f"{field}_{dqr_type}_{rule_idx}"
    rule: dict[str, Any] = {
        "name": rule_name,
        "type": dqr_type,
        "field": field,
        "severity": severity,
        "error_message": q.get("description") or f"{field}: {odcs_type} check failed",
    }

    if dqr_type == "regex":
        pattern = q.get("pattern") or q.get("regex", "")
        if not pattern:
            return None
        rule["pattern"] = pattern

    elif dqr_type == "range":
        min_val = q.get("min") if q.get("min") is not None else q.get("minValue")
        max_val = q.get("max") if q.get("max") is not None else q.get("maxValue")
        if min_val is not None:
            rule["min_value"] = float(min_val)
        if max_val is not None:
            rule["max_value"] = float(max_val)

    elif dqr_type == "min":
        val = q.get("min") if q.get("min") is not None else q.get("minValue")
        if val is None:
            return None
        rule["min_value"] = float(val)

    elif dqr_type == "max":
        val = q.get("max") if q.get("max") is not None else q.get("maxValue")
        if val is None:
            return None
        rule["max_value"] = float(val)

    elif dqr_type == "min_length":
        val = q.get("minLength") or q.get("min_length") or q.get("min")
        if val is None:
            return None
        rule["min_length"] = int(val)

    elif dqr_type == "max_length":
        val = q.get("maxLength") or q.get("max_length") or q.get("max")
        if val is None:
            return None
        rule["max_length"] = int(val)

    elif dqr_type == "date_format":
        fmt = q.get("format", "%Y-%m-%d")
        rule["format"] = fmt

    return rule


# ---------------------------------------------------------------------------
# Field-level shortcut attributes → rules
# ---------------------------------------------------------------------------

def _field_shortcuts_to_rules(field: str, prop: dict) -> list[dict]:
    """Convert field-level ODCS shortcuts (required, unique, minLength, maxLength) to rules."""
    rules = []
    if prop.get("required") is True:
        rules.append({
            "name": f"{field}_not_empty",
            "type": "not_empty",
            "field": field,
            "severity": "error",
            "error_message": f"{field} is required",
        })
    if prop.get("unique") is True:
        rules.append({
            "name": f"{field}_unique",
            "type": "unique",
            "field": field,
            "severity": "error",
            "error_message": f"{field} must be unique",
        })
    if prop.get("minLength") is not None:
        rules.append({
            "name": f"{field}_min_length",
            "type": "min_length",
            "field": field,
            "severity": "error",
            "min_length": int(prop["minLength"]),
            "error_message": f"{field} must be at least {prop['minLength']} characters",
        })
    if prop.get("maxLength") is not None:
        rules.append({
            "name": f"{field}_max_length",
            "type": "max_length",
            "field": field,
            "severity": "error",
            "max_length": int(prop["maxLength"]),
            "error_message": f"{field} must be at most {prop['maxLength']} characters",
        })
    return rules


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

def import_odcs(contract_data: dict) -> dict:
    """
    Convert an ODCS 3.1 contract dict to an OpenDQV contract dict.

    Args:
        contract_data: Parsed ODCS 3.1 dict (from YAML or JSON).

    Returns:
        OpenDQV contract dict with keys: contract (name, version, status,
        description, owner, rules).
    """
    _KNOWN_ODCS_KEYS = {"apiVersion", "kind", "info", "schema"}
    odcs_metadata = {
        k: v for k, v in contract_data.items()
        if k not in _KNOWN_ODCS_KEYS
    }

    info = contract_data.get("info", {})
    name = info.get("title", "imported_contract")
    # Sanitise name: lowercase, replace spaces/hyphens with underscores
    name = name.lower().replace(" ", "_").replace("-", "_")
    version = str(info.get("version", "1.0"))
    status = info.get("status", "active")
    description = info.get("description", "")
    owner = info.get("owner", "")

    rules: list[dict] = []
    skipped: list[str] = []

    schema = contract_data.get("schema", [])
    for table in schema:
        properties = table.get("properties", [])
        for prop in properties:
            field = prop.get("name", "")
            if not field:
                continue

            # Field-level shortcuts first
            rules.extend(_field_shortcuts_to_rules(field, prop))

            # Inline quality checks
            for idx, q in enumerate(prop.get("quality", [])):
                rule = _quality_to_rule(field, q, idx)
                if rule:
                    rules.append(rule)
                else:
                    skipped.append(f"{field}.{q.get('type', '?')}")

    # Deduplicate by (type, field) — shortcuts take priority (added first).
    # Using name as part of the key never deduplicates because names are unique.
    seen_keys: set[tuple] = set()
    deduped: list[dict] = []
    for rule in rules:
        key = (rule["type"], rule["field"])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(rule)

    return {
        "contract": {
            "name": name,
            "version": version,
            "status": status,
            "description": description,
            "owner": owner,
            "rules": deduped,
        },
        "skipped_checks": skipped,
        "rule_count": len(deduped),
        "_odcs_metadata": odcs_metadata,   # sla, semantics, lineage, etc. — preserved, not evaluated
    }


def odcs_to_yaml(contract_data: dict, contract_name: Optional[str] = None) -> tuple[str, str]:
    """
    Import ODCS 3.1 and return (contract_name, OpenDQV YAML string).

    Args:
        contract_data: Parsed ODCS 3.1 dict.
        contract_name: Override the contract name (default: from info.title).

    Returns:
        (contract_name, yaml_string) tuple.
    """
    result = import_odcs(contract_data)
    name = contract_name or result["contract"]["name"]
    result["contract"]["name"] = name
    yaml_str = yaml.dump(
        {"contract": result["contract"]},
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return name, yaml_str


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

def export_odcs(
    contract_name: str,
    rules: list,
    version: str = "1.0",
    status: str = "active",
    description: str = "",
    owner: str = "",
    odcs_metadata: Optional[dict] = None,
) -> dict:
    """
    Export OpenDQV rules to an ODCS 3.1 contract dict.

    Args:
        contract_name: The contract name (becomes info.title).
        rules:         List of Rule objects or rule dicts.
        version:       Contract version string.
        status:        Contract status (active/draft/archived).
        description:   Human-readable description.
        owner:         Owning team or person.

    Returns:
        ODCS 3.1 dict ready for yaml.dump().
    """
    # Group rules by field
    by_field: dict[str, list] = defaultdict(list)
    for rule in rules:
        field = rule.field if hasattr(rule, "field") else rule.get("field", "unknown")
        by_field[field].append(rule)

    properties = []
    for field, field_rules in by_field.items():
        quality = []
        for rule in field_rules:
            r_type = rule.type if hasattr(rule, "type") else rule.get("type")
            r_sev  = (rule.severity.value if hasattr(rule, "severity") and hasattr(rule.severity, "value")
                      else rule.get("severity", "error"))
            odcs_type = _ODCS_TYPE_MAP.get(r_type)
            if not odcs_type:
                continue

            q: dict[str, Any] = {
                "type": odcs_type,
                "mustBeSatisfied": (r_sev == "error"),
            }

            if r_type == "regex":
                pattern = rule.pattern if hasattr(rule, "pattern") else rule.get("pattern")
                if pattern:
                    q["pattern"] = pattern

            elif r_type in ("range", "min", "min_age"):
                min_val = (rule.min_value if hasattr(rule, "min_value") else rule.get("min_value"))
                if min_val is not None:
                    q["min"] = min_val

            if r_type in ("range", "max", "max_age"):
                max_val = (rule.max_value if hasattr(rule, "max_value") else rule.get("max_value"))
                if max_val is not None:
                    q["max"] = max_val

            elif r_type == "date_format":
                fmt = rule.format if hasattr(rule, "format") else rule.get("format")
                if fmt:
                    q["format"] = fmt

            elif r_type == "min_length":
                v = rule.min_length if hasattr(rule, "min_length") else rule.get("min_length")
                if v is not None:
                    q["minLength"] = v

            elif r_type == "max_length":
                v = rule.max_length if hasattr(rule, "max_length") else rule.get("max_length")
                if v is not None:
                    q["maxLength"] = v

            err_msg = (rule.error_message if hasattr(rule, "error_message")
                       else rule.get("error_message"))
            if err_msg:
                q["description"] = err_msg

            quality.append(q)

        prop: dict[str, Any] = {"name": field}
        if quality:
            prop["quality"] = quality
        properties.append(prop)

    result = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "info": {
            "title": contract_name,
            "version": version,
            "status": status,
            "description": description,
            "owner": owner,
        },
        "schema": [
            {
                "name": contract_name,
                "properties": properties,
            }
        ],
    }
    if odcs_metadata:
        result.update(odcs_metadata)   # re-emit sla, semantics, lineage, etc.
    return result


def contract_to_odcs_yaml(
    contract_name: str,
    rules: list,
    version: str = "1.0",
    status: str = "active",
    description: str = "",
    owner: str = "",
    odcs_metadata: Optional[dict] = None,
) -> str:
    """Export OpenDQV contract to ODCS 3.1 YAML string."""
    doc = export_odcs(
        contract_name=contract_name,
        rules=rules,
        version=version,
        status=status,
        description=description,
        owner=owner,
        odcs_metadata=odcs_metadata,
    )
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)
