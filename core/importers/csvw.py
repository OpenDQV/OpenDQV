"""
CSVW (CSV on the Web) importer for OpenDQV.

Converts W3C CSVW metadata (JSON-LD) to OpenDQV contract YAML.
Reference: https://www.w3.org/TR/tabular-metadata/

Supported mappings:
  required: true                  → not_empty rule
  constraints.pattern             → regex rule
  constraints.minimum/maximum     → range rule
  constraints.minLength/maxLength → min_length/max_length rules
  constraints.enum                → lookup rule (inline values)
  datatype: date/datetime         → date_format rule
"""

import json
import re
import yaml
from typing import Union


def _scan_rules_for_lookup_file(rules: list) -> None:
    """
    Scan generated rules for lookup_file values and validate each path for safety.

    Raises ValueError if any lookup_file would fail the path traversal check.
    This prevents a malicious CSVW source from injecting a traversal path.
    """
    from core.validator import _check_lookup_path_safe
    for rule in rules:
        lookup_file = rule.get("lookup_file")
        if lookup_file:
            try:
                _check_lookup_path_safe(lookup_file)
            except ValueError as exc:
                raise ValueError(
                    f"Importer security: unsafe lookup_file in generated rule '{rule.get('name', '?')}': {exc}"
                ) from exc


def import_csvw(source: Union[str, dict]) -> dict:
    """
    Parse a CSVW metadata document and return a list of OpenDQV Rule dicts.

    source: JSON string or already-parsed dict
    Returns: {"rules": [...], "metadata": {"source": "csvw", "url": ...}}
    """
    if isinstance(source, str):
        data = json.loads(source)
    else:
        data = source

    schema = data.get("tableSchema", data.get("table_schema", {}))
    columns = schema.get("columns", [])
    url = data.get("url", "unknown.csv")

    rules = []

    for col in columns:
        name = col.get("name") or col.get("titles") or "unknown"
        field = name.replace(" ", "_").lower()
        titles = col.get("titles", name)
        datatype = col.get("datatype", "string")
        if isinstance(datatype, dict):
            datatype = datatype.get("base", "string")
        required = col.get("required", False)
        constraints = col.get("constraints", {})

        rule_base = {
            "field": field,
            "severity": "error",
            "description": str(titles),
        }

        # Required → not_empty
        if required:
            rules.append({
                **rule_base,
                "name": f"{field}_required",
                "type": "not_empty",
                "error_message": f"{titles} is required",
            })

        # Date/datetime → date_format
        if datatype in ("date", "datetime", "dateTime", "time"):
            rules.append({
                **rule_base,
                "name": f"{field}_date_format",
                "type": "date_format",
                "error_message": f"{titles} must be a valid date",
            })

        # Pattern → regex
        pattern = constraints.get("pattern")
        if pattern:
            rules.append({
                **rule_base,
                "name": f"{field}_format",
                "type": "regex",
                "pattern": pattern,
                "error_message": f"{titles} must match pattern: {pattern}",
            })

        # Numeric range — handles inclusive and exclusive bounds (exclusive treated as inclusive approx)
        min_val = next(
            (constraints[k] for k in ("minimum", "minInclusive", "minExclusive") if k in constraints),
            None,
        )
        max_val = next(
            (constraints[k] for k in ("maximum", "maxInclusive", "maxExclusive") if k in constraints),
            None,
        )
        if min_val is not None or max_val is not None:
            range_rule = {
                **rule_base,
                "name": f"{field}_range",
                "type": "range" if (min_val is not None and max_val is not None) else (
                    "min" if min_val is not None else "max"
                ),
                "error_message": f"{titles} out of range",
            }
            if min_val is not None:
                range_rule["min_value"] = float(min_val)
            if max_val is not None:
                range_rule["max_value"] = float(max_val)
            rules.append(range_rule)

        # String length
        min_len = constraints.get("minLength")
        max_len = constraints.get("maxLength")
        if min_len is not None:
            rules.append({
                **rule_base,
                "name": f"{field}_min_length",
                "type": "min_length",
                "min_length": int(min_len),
                "error_message": f"{titles} too short",
            })
        if max_len is not None:
            rules.append({
                **rule_base,
                "name": f"{field}_max_length",
                "type": "max_length",
                "max_length": int(max_len),
                "error_message": f"{titles} too long",
            })

        # Enum → regex (validator requires lookup_file; no inline lookup support)
        enum_vals = constraints.get("enum")
        if enum_vals and isinstance(enum_vals, list):
            escaped = [re.escape(str(v)) for v in enum_vals]
            pattern = "^(" + "|".join(escaped) + ")$"
            rules.append({
                **rule_base,
                "name": f"{field}_values",
                "type": "regex",
                "pattern": pattern,
                "error_message": f"{titles} must be one of: {', '.join(str(v) for v in enum_vals)}",
            })

    return {
        "rules": rules,
        "metadata": {"source": "csvw", "url": url, "column_count": len(columns)},
    }


def csvw_to_yaml(csvw_data: Union[str, dict], contract_name: str = "imported") -> str:
    """Convert CSVW metadata to OpenDQV contract YAML string."""
    parsed = import_csvw(csvw_data)
    # SEC-006: validate any generated lookup_file paths for path traversal
    _scan_rules_for_lookup_file(parsed["rules"])
    contract = {
        "contract": {
            "name": contract_name,
            "version": "1.0",
            "description": f"Imported from CSVW metadata ({parsed['metadata'].get('url', 'unknown')})",
            "status": "draft",
            "rules": parsed["rules"],
        }
    }
    return yaml.dump(contract, default_flow_style=False, sort_keys=False)
