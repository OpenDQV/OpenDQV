"""
NDC (National Drug Code) importer for OpenDQV.

NDC codes identify drug products in the US (FDA). They follow a 10- or 11-digit
format with hyphens separating labeler, product, and package segments.

Common formats:
  4-4-2: LLLL-PPPP-PP (10 digits with hyphens)
  5-3-2: LLLLL-PPP-PP (10 digits with hyphens)
  5-4-1: LLLLL-PPPP-P (10 digits with hyphens)
  11-digit: LLLLLPPPPPP (11 digits, no hyphens — FDA standard)

This importer generates OpenDQV rules for validating NDC fields in datasets.
It also provides a reference dataset loader for validating against the FDA NDC directory.
"""

import yaml as _yaml


def _scan_rules_for_lookup_file(rules: list) -> None:
    """
    Scan generated rules for lookup_file values and validate each path for safety.

    Raises ValueError if any lookup_file would fail the path traversal check.
    """
    from opendqv.core.validator import _check_lookup_path_safe
    for rule in rules:
        lookup_file = rule.get("lookup_file")
        if lookup_file:
            try:
                _check_lookup_path_safe(lookup_file)
            except ValueError as exc:
                raise ValueError(
                    f"Importer security: unsafe lookup_file in generated rule '{rule.get('name', '?')}': {exc}"
                ) from exc


NDC_PATTERN_10 = r"^\d{4}-\d{4}-\d{2}$|^\d{5}-\d{3}-\d{2}$|^\d{5}-\d{4}-\d{1}$"
NDC_PATTERN_11 = r"^\d{11}$"
NDC_PATTERN_HYPHENATED = r"^\d{4,5}-\d{3,4}-\d{1,2}$"
NDC_PATTERN_ANY = r"^(\d{4}-\d{4}-\d{2}|\d{5}-\d{3}-\d{2}|\d{5}-\d{4}-\d{1}|\d{11})$"


def generate_ndc_rules(
    field_name: str = "ndc_code",
    allow_11_digit: bool = True,
    allow_hyphenated: bool = True,
    severity: str = "error",
) -> list:
    """
    Generate OpenDQV rules for validating an NDC code field.

    Returns a list of rule dicts ready for use in a contract.
    """
    rules = [
        {
            "name": f"{field_name}_required",
            "type": "not_empty",
            "field": field_name,
            "severity": severity,
            "error_message": f"NDC code ({field_name}) is required",
        },
        {
            "name": f"{field_name}_format",
            "type": "regex",
            "field": field_name,
            "pattern": NDC_PATTERN_ANY,
            "severity": severity,
            "error_message": (
                "NDC code must be in a valid format: "
                "4-4-2, 5-3-2, 5-4-1 (with hyphens) or 11 digits (no hyphens)"
            ),
            "description": "NDC (National Drug Code) format validation per FDA standard",
        },
    ]
    return rules


def import_ndc(config: dict = None) -> dict:
    """
    Generate OpenDQV rules for NDC fields based on configuration.

    config: {
        "fields": ["ndc_code", "dispensed_ndc"],  # field names to validate
        "severity": "error",
        "allow_11_digit": true,
    }
    Returns: {"rules": [...], "metadata": {...}}
    """
    config = config or {}
    fields = config.get("fields", ["ndc_code"])
    severity = config.get("severity", "error")
    allow_11 = config.get("allow_11_digit", True)
    allow_hyph = config.get("allow_hyphenated", True)

    rules = []
    for field in fields:
        rules.extend(generate_ndc_rules(field, allow_11, allow_hyph, severity))

    return {
        "rules": rules,
        "metadata": {
            "source": "ndc",
            "fields": fields,
            "note": (
                "NDC format validation only. For registry lookup, configure "
                "lookup_file pointing to FDA NDC directory export."
            ),
        },
    }


def ndc_to_yaml(config: dict = None, contract_name: str = "pharma_dispense") -> str:
    """Generate a YAML contract with NDC validation rules."""
    parsed = import_ndc(config)
    # SEC-006: validate any generated lookup_file paths for path traversal
    _scan_rules_for_lookup_file(parsed["rules"])
    contract = {
        "contract": {
            "name": contract_name,
            "version": "1.0",
            "description": "Pharmaceutical dispensing record validation with NDC code checks",
            "status": "draft",
            "rules": parsed["rules"],
        }
    }
    return _yaml.dump(contract, default_flow_style=False, sort_keys=False)
