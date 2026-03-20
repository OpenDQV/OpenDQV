"""
Convert a dbt schema.yml dict into OpenDQV YAML contract(s).

Supports both ``models:`` and ``sources:`` sections, handling built-in dbt
tests as well as common dbt_utils and dbt_expectations tests.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# dbt test -> handler
# Each handler receives the test config dict (may be empty for simple tests)
# and the column name, and returns a list of partial rule dicts.
# ---------------------------------------------------------------------------

def _handle_unique(_cfg: dict, column: str) -> list[dict]:
    return [{
        "type": "unique",
        "field": column,
        "error_message": f"{column} must be unique",
    }]


def _handle_not_null(_cfg: dict, column: str) -> list[dict]:
    return [{
        "type": "not_empty",
        "field": column,
        "error_message": f"{column} must not be empty",
    }]


def _handle_accepted_values(cfg: dict, column: str) -> list[dict]:
    values = cfg.get("values", [])
    escaped = [re.escape(str(v)) for v in values]
    pattern = "^(" + "|".join(escaped) + ")$"
    # OpenDQV `lookup` requires a lookup_file (file or URL); inline values are not
    # supported. We map accepted_values to `regex` as the closest equivalent.
    # Round-trip limitation: this re-exports as dbt_expectations.expect_column_values_to_match_regex
    # (requires dbt_expectations >=0.8) rather than the original accepted_values test.
    return [{
        "type": "regex",
        "field": column,
        "pattern": pattern,
        "error_message": f"{column} must be one of {values}",
    }]


def _handle_accepted_range(cfg: dict, column: str) -> list[dict]:
    min_val = cfg.get("min_value")
    max_val = cfg.get("max_value")
    parts = []
    if min_val is not None:
        parts.append(str(min_val))
    if max_val is not None:
        parts.append(str(max_val))
    range_desc = " and ".join(parts) if parts else "valid range"
    rule: dict[str, Any] = {
        "type": "range",
        "field": column,
        "error_message": f"{column} must be between {range_desc}",
    }
    if min_val is not None:
        rule["min_value"] = min_val
    if max_val is not None:
        rule["max_value"] = max_val
    return [rule]


def _handle_regex(cfg: dict, column: str) -> list[dict]:
    pattern = cfg.get("regex_pattern") or cfg.get("regex", "")
    return [{
        "type": "regex",
        "field": column,
        "pattern": pattern,
        "error_message": f"{column} must match pattern {pattern}",
    }]


def _handle_length_between(cfg: dict, column: str) -> list[dict]:
    min_val = cfg.get("min_value")
    max_val = cfg.get("max_value")
    rules: list[dict] = []
    if min_val is not None:
        rules.append({
            "type": "min_length",
            "field": column,
            "min_length": int(min_val),
            "error_message": f"{column} must be at least {int(min_val)} characters",
        })
    if max_val is not None:
        rules.append({
            "type": "max_length",
            "field": column,
            "max_length": int(max_val),
            "error_message": f"{column} must be at most {int(max_val)} characters",
        })
    return rules


# ---------------------------------------------------------------------------
# Handler registry keyed by dbt test name (with and without package prefix)
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "unique": _handle_unique,
    "not_null": _handle_not_null,
    "accepted_values": _handle_accepted_values,
    "dbt_utils.accepted_range": _handle_accepted_range,
    "dbt_expectations.expect_column_values_to_match_regex": _handle_regex,
    "dbt_expectations.expect_column_value_lengths_to_be_between": _handle_length_between,
}

# Tests that are intentionally skipped (not applicable to single-record validation).
_SKIP_TESTS: set[str] = {
    "relationships",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_test(test: str | dict) -> tuple[str, dict]:
    """
    Normalise a dbt test entry into (test_name, config_dict).

    Simple tests are plain strings (e.g. ``"unique"``).
    Configured tests are single-key dicts (e.g. ``{"accepted_values": {"values": [...]}``).
    """
    if isinstance(test, str):
        return test, {}
    if isinstance(test, dict):
        # Single-key dict: key is test name, value is config
        test_name = next(iter(test))
        cfg = test[test_name]
        if cfg is None:
            cfg = {}
        return test_name, cfg
    return str(test), {}


def _process_column(column: dict, name_counter: Counter) -> tuple[list[dict], list[dict]]:
    """
    Process a single column entry from a dbt model/source, returning
    (rules, skipped) lists.
    """
    col_name = column.get("name", "unknown")
    # dbt >=1.0 renamed column-level `tests:` to `data_tests:`; support both
    tests = column.get("data_tests") or column.get("tests", [])
    rules: list[dict] = []
    skipped: list[dict] = []

    for test_entry in tests:
        test_name, cfg = _parse_test(test_entry)

        # Intentionally skipped tests
        if test_name in _SKIP_TESTS:
            skipped.append({
                "test": test_name,
                "column": col_name,
                "reason": "not applicable to single-record validation",
            })
            continue

        handler = _HANDLERS.get(test_name)
        if handler is None:
            skipped.append({
                "test": test_name,
                "column": col_name,
                "reason": "unsupported test type",
            })
            continue

        try:
            partial_rules = handler(cfg, col_name)
        except Exception as exc:
            skipped.append({
                "test": test_name,
                "column": col_name,
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
            rule["severity"] = "error"
            rules.append(rule)

    return rules, skipped


def _fixup_duplicate_names(rules: list[dict]) -> None:
    """
    If any base name appeared more than once, rename the first occurrence
    to include ``_1`` for consistency.
    """
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


def _build_contract(entity_name: str, entity_type: str, columns: list[dict]) -> dict:
    """
    Build a single import result dict for a model or source.
    """
    name_counter: Counter[str] = Counter()
    all_rules: list[dict] = []
    all_skipped: list[dict] = []
    total_tests = 0

    for column in columns:
        tests = column.get("data_tests") or column.get("tests", [])
        total_tests += len(tests)
        rules, skipped = _process_column(column, name_counter)
        all_rules.extend(rules)
        all_skipped.extend(skipped)

    _fixup_duplicate_names(all_rules)

    contract = {
        "name": entity_name,
        "version": "1.0",
        "description": f"Imported from dbt {entity_type}: {entity_name}",
        "owner": "imported-from-dbt",
        "status": "draft",
        "source": "import",
        "asset_id": f"dbt::{entity_name}",
        "rules": all_rules,
    }

    return {
        "contract": contract,
        "stats": {
            "total_tests": total_tests,
            "imported": len(all_rules),
            "skipped": len(all_skipped),
        },
        "skipped": all_skipped,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_dbt_schema(schema: dict) -> dict:
    """
    Convert a dbt schema.yml dict into OpenDQV contract(s).

    Since a schema.yml can contain multiple models and sources, returns a
    list of contracts.

    Returns::

        {
            "contracts": [
                {
                    "contract": {...},   # OpenDQV contract dict
                    "stats": {"total_tests": N, "imported": N, "skipped": N},
                    "skipped": [...]
                },
                ...
            ]
        }
    """
    contracts: list[dict] = []

    for model in schema.get("models", []):
        model_name = model.get("name", "unnamed_model")
        columns = model.get("columns", [])
        contracts.append(_build_contract(model_name, "model", columns))

    for source in schema.get("sources", []):
        source_name = source.get("name", "unnamed_source")
        # Sources can have tables with their own columns
        tables = source.get("tables", [])
        for table in tables:
            table_name = table.get("name", "unnamed_table")
            full_name = f"{source_name}__{table_name}"
            columns = table.get("columns", [])
            contracts.append(_build_contract(full_name, "source", columns))

    return {"contracts": contracts}


def dbt_schema_to_yaml(schema: dict) -> list[tuple[str, str]]:
    """Convert dbt schema dict to list of ``(contract_name, yaml_string)`` tuples."""
    result = import_dbt_schema(schema)
    output: list[tuple[str, str]] = []
    for entry in result["contracts"]:
        contract = entry["contract"]
        yaml_str = yaml.dump(
            {"contract": contract},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        output.append((contract["name"], yaml_str))
    return output


# ---------------------------------------------------------------------------
# Exporter: OpenDQV → dbt schema.yml
# ---------------------------------------------------------------------------

def _get(rule, attr: str, default=None):
    """Access rule attribute from either an object or a dict."""
    if hasattr(rule, attr):
        return getattr(rule, attr)
    return rule.get(attr, default) if isinstance(rule, dict) else default


def export_dbt_schema(contract_name: str, rules: list, description: str = "") -> dict:
    """
    Export OpenDQV rules to a dbt schema.yml dict.

    Returns a Python dict ready for ``yaml.dump()``.
    Unsupported rule types are silently collected and reported via the
    ``_skipped`` key on the returned dict (removed before serialisation).
    """
    from collections import defaultdict as _defaultdict

    by_field: dict[str, list] = _defaultdict(list)
    for rule in rules:
        field = _get(rule, "field", "unknown")
        by_field[field].append(rule)

    columns = []
    skipped: list[dict] = []

    for field, field_rules in by_field.items():
        tests: list = []
        for rule in field_rules:
            r_type = _get(rule, "type")

            if r_type == "not_empty":
                tests.append("not_null")

            elif r_type == "unique":
                tests.append("unique")

            elif r_type == "regex":
                pattern = _get(rule, "pattern")
                if not pattern:
                    # Fall back to compiled pattern string (built-in aliases)
                    compiled = _get(rule, "compiled_pattern")
                    if compiled is not None:
                        pattern = compiled.pattern
                if not pattern:
                    skipped.append({
                        "rule": _get(rule, "name", "?"),
                        "type": r_type,
                        "field": field,
                        "reason": "regex rule has no pattern",
                    })
                    continue
                tests.append({
                    "dbt_expectations.expect_column_values_to_match_regex": {
                        "regex": pattern,
                    }
                })

            elif r_type == "range":
                kwargs: dict = {}
                min_val = _get(rule, "min_value")
                max_val = _get(rule, "max_value")
                if min_val is not None:
                    kwargs["min_value"] = min_val
                if max_val is not None:
                    kwargs["max_value"] = max_val
                tests.append({"dbt_utils.accepted_range": kwargs})

            elif r_type == "min":
                min_val = _get(rule, "min_value")
                tests.append({"dbt_utils.accepted_range": {"min_value": min_val}})

            elif r_type == "max":
                max_val = _get(rule, "max_value")
                tests.append({"dbt_utils.accepted_range": {"max_value": max_val}})

            elif r_type == "min_length":
                min_len = _get(rule, "min_length")
                tests.append({
                    "dbt_expectations.expect_column_value_lengths_to_be_between": {
                        "min_value": min_len,
                    }
                })

            elif r_type == "max_length":
                max_len = _get(rule, "max_length")
                tests.append({
                    "dbt_expectations.expect_column_value_lengths_to_be_between": {
                        "max_value": max_len,
                    }
                })

            else:
                skipped.append({
                    "rule": _get(rule, "name", "?"),
                    "type": r_type,
                    "field": field,
                    "reason": "unsupported rule type for dbt export",
                })

        col: dict = {"name": field}
        if tests:
            col["tests"] = tests
        columns.append(col)

    doc = {
        "version": 2,
        "models": [
            {
                "name": contract_name,
                "description": description,
                "columns": columns,
            }
        ],
        "_skipped": skipped,
    }
    return doc


def contract_to_dbt_yaml(
    contract_name: str,
    rules: list,
    description: str = "",
) -> str:
    """Export OpenDQV contract to dbt schema.yml YAML string."""
    doc = export_dbt_schema(contract_name, rules, description=description)
    skipped = doc.pop("_skipped", [])
    if skipped:
        import sys
        for s in skipped:
            print(
                f"[export-dbt] skipped rule '{s['rule']}' (type={s['type']}, field={s['field']}): {s['reason']}",
                file=sys.stderr,
            )
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)
