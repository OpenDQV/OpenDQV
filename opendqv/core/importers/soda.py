"""
Convert Soda Core checks YAML into OpenDQV YAML contract(s).

Soda checks files use a ``checks for <dataset>:`` structure with
inline check expressions like ``missing_count(email) = 0``.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Check expression parser
# ---------------------------------------------------------------------------

# Matches: metric(field) operator value
_CHECK_RE = re.compile(
    r"^(missing_count|duplicate_count|invalid_count|min|max|min_length|max_length|avg_length|row_count)"
    r"\((\w+)\)\s*(=|<|<=|>|>=)\s*(\d+)$"
)

# Well-known email regex used when Soda specifies ``valid format: email``
_EMAIL_REGEX = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"

# BUG-SODA-1: Map of Soda valid format names to regex patterns.
# Unsupported formats produce a clear skip reason rather than silently dropping.
_FORMAT_PATTERNS: dict[str, str] = {
    "email":       _EMAIL_REGEX,
    "date":        r"^\d{4}-\d{2}-\d{2}$",
    "time":        r"^\d{2}:\d{2}(:\d{2})?$",
    "uuid":        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "phone":       r"^\+?[\d\s\-().]{7,20}$",
    "url":         r"^https?://[^\s/$.?#].[^\s]*$",
    "ip_address":  r"^(\d{1,3}\.){3}\d{1,3}$",
    "credit_card": r"^\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}$",
    "iban":        r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$",
}

# Metrics that are not applicable to single-record validation
_SKIP_METRICS = {"row_count", "avg_length"}


# ---------------------------------------------------------------------------
# Individual check handlers
# ---------------------------------------------------------------------------

def _handle_missing_count(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    severity = "warning" if (operator in ("<", "<=", ">", ">=") and value > 0) or (operator == "=" and value > 0) else "error"
    # missing_count = 0 means strict not_empty; anything tolerant is a warning
    if operator == "=" and value > 0:
        severity = "warning"
    return [{
        "type": "not_empty",
        "field": field,
        "severity": severity,
        "error_message": f"{field} must not be empty",
    }]


def _handle_duplicate_count(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    severity = "error" if (operator == "=" and value == 0) else "warning"
    return [{
        "type": "unique",
        "field": field,
        "severity": severity,
        "error_message": f"{field} must be unique",
    }]


def _handle_invalid_count(field: str, operator: str, value: int, config: dict) -> list[dict]:
    """Handle invalid_count checks — requires a config block with valid format or valid regex."""
    valid_format = config.get("valid format")
    valid_regex = config.get("valid regex")

    if valid_format:
        pattern = _FORMAT_PATTERNS.get(valid_format)
        if pattern is None:
            # Unsupported format — return sentinel so caller emits a clear skip reason
            return [{"__skip_reason": f"valid format '{valid_format}' not yet mapped"}]
    elif valid_regex:
        pattern = valid_regex
    else:
        # No config we can map — skip
        return []

    return [{
        "type": "regex",
        "field": field,
        "pattern": pattern,
        "severity": "error",
        "error_message": f"{field} must match pattern {pattern}",
    }]


def _handle_min(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    rule: dict[str, Any] = {
        "type": "min",
        "field": field,
        "min_value": value,
        "severity": "error",
        "error_message": f"{field} must be at least {value}",
    }
    return [rule]


def _handle_max(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    rule: dict[str, Any] = {
        "type": "max",
        "field": field,
        "max_value": value,
        "severity": "error",
        "error_message": f"{field} must be at most {value}",
    }
    return [rule]


def _handle_min_length(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    return [{
        "type": "min_length",
        "field": field,
        "min_length": value,
        "severity": "error",
        "error_message": f"{field} must be at least {value} characters",
    }]


def _handle_max_length(field: str, operator: str, value: int, _config: dict) -> list[dict]:
    return [{
        "type": "max_length",
        "field": field,
        "max_length": value,
        "severity": "error",
        "error_message": f"{field} must be at most {value} characters",
    }]


_METRIC_HANDLERS: dict[str, Any] = {
    "missing_count": _handle_missing_count,
    "duplicate_count": _handle_duplicate_count,
    "invalid_count": _handle_invalid_count,
    "min": _handle_min,
    "max": _handle_max,
    "min_length": _handle_min_length,
    "max_length": _handle_max_length,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_check(check_entry) -> tuple[str | None, str | None, str | None, int | None, dict]:
    """
    Parse a single check entry from the Soda YAML.

    A check entry can be:
      - A plain string: ``"missing_count(email) = 0"``
      - A dict with a single key (check expression) mapping to config:
        ``{"invalid_count(email) = 0": {"valid format": "email"}}``

    Returns (metric, field, operator, value, config) or (None, ...) if unparseable.
    """
    config: dict = {}

    if isinstance(check_entry, str):
        check_str = check_entry
    elif isinstance(check_entry, dict):
        check_str = next(iter(check_entry))
        config = check_entry[check_str] or {}
    else:
        return None, None, None, None, {}

    m = _CHECK_RE.match(check_str.strip())
    if not m:
        return None, None, None, None, {}

    metric = m.group(1)
    field = m.group(2)
    operator = m.group(3)
    value = int(m.group(4))
    return metric, field, operator, value, config


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

def import_soda_checks(checks_yaml: dict) -> dict:
    """
    Convert a Soda checks YAML dict into OpenDQV contract(s).

    The input dict has keys like ``"checks for <dataset>"``, each mapping
    to a list of check expressions.

    Returns same structure as dbt importer::

        {
            "contracts": [
                {
                    "contract": {...},
                    "stats": {"total_checks": N, "imported": N, "skipped": N},
                    "skipped": [...]
                }
            ]
        }
    """
    contracts: list[dict] = []

    for key, checks in checks_yaml.items():
        # Only process "checks for <dataset>" keys
        if not key.startswith("checks for "):
            continue

        dataset = key[len("checks for "):]
        if not isinstance(checks, list):
            continue

        name_counter: Counter[str] = Counter()
        rules: list[dict] = []
        skipped: list[dict] = []
        total = len(checks)

        for check_entry in checks:
            # Handle special dict-only checks like ``schema:``
            if isinstance(check_entry, dict):
                first_key = next(iter(check_entry))
                if first_key in ("schema",):
                    skipped.append({
                        "check": first_key,
                        "reason": "not applicable to single-record validation",
                    })
                    # Only skip if it's truly a non-metric check
                    # If the key matches _CHECK_RE, fall through
                    m = _CHECK_RE.match(first_key.strip())
                    if not m:
                        continue

            metric, field, operator, value, config = _parse_check(check_entry)

            if metric is None:
                check_repr = str(check_entry) if not isinstance(check_entry, str) else check_entry
                skipped.append({
                    "check": check_repr,
                    "reason": "could not parse check expression",
                })
                continue

            if metric in _SKIP_METRICS:
                skipped.append({
                    "check": f"{metric}({field}) {operator} {value}",
                    "reason": "not applicable to single-record validation",
                })
                continue

            handler = _METRIC_HANDLERS.get(metric)
            if handler is None:
                skipped.append({
                    "check": f"{metric}({field}) {operator} {value}",
                    "reason": "unsupported metric",
                })
                continue

            try:
                partial_rules = handler(field, operator, value, config)
            except Exception as exc:
                skipped.append({
                    "check": f"{metric}({field}) {operator} {value}",
                    "reason": f"handler error: {exc}",
                })
                continue

            if not partial_rules:
                skipped.append({
                    "check": f"{metric}({field}) {operator} {value}",
                    "reason": "no config for invalid_count (missing valid format/regex)",
                })
                continue

            # Handle skip sentinel from _handle_invalid_count for unknown formats
            if len(partial_rules) == 1 and "__skip_reason" in partial_rules[0]:
                skipped.append({
                    "check": f"{metric}({field}) {operator} {value}",
                    "reason": partial_rules[0]["__skip_reason"],
                })
                continue

            for rule in partial_rules:
                base_name = f"{rule['field']}_{rule['type']}"
                name_counter[base_name] += 1
                count = name_counter[base_name]
                rule_name = base_name if count == 1 else f"{base_name}_{count}"
                rule["name"] = rule_name
                # severity already set by handler
                rules.append(rule)

        _fixup_duplicate_names(rules)

        contract = {
            "name": dataset,
            "version": "1.0",
            "description": f"Imported from Soda checks for: {dataset}",
            "owner": "imported-from-soda",
            "status": "draft",
            "source": "import",
            "asset_id": f"soda::{dataset}",
            "rules": rules,
        }

        contracts.append({
            "contract": contract,
            "stats": {
                "total_checks": total,
                "imported": len(rules),
                "skipped": len(skipped),
            },
            "skipped": skipped,
        })

    return {"contracts": contracts}


def soda_checks_to_yaml(checks_yaml: dict) -> list[tuple[str, str]]:
    """Convert Soda checks to list of ``(contract_name, yaml_string)`` tuples."""
    result = import_soda_checks(checks_yaml)
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
