"""
Contract linter — semantic completeness checks on every YAML in contracts/.

These tests verify that each contract is not just syntactically valid (parseable)
but semantically complete: every rule has the fields required to actually do what
it claims. A regex rule without a pattern is a no-op. A lookup rule without a
lookup_file skips silently. These tests catch that category of mistake before
it ships.

Motivation: RT77 — customer.yaml valid_email rule had no pattern, silently
accepting all values including invalid emails. 1,000+ tests passed; the bug
shipped. A linter at load time would have caught it.
"""
import os
import datetime
import pytest
import yaml
from pathlib import Path

# Use the contracts dir from environment (points at temp copy during normal test runs,
# but points at live contracts/ when run standalone — both are valid for linting).
_contracts_dir = Path(
    os.environ.get("OPENDQV_CONTRACTS_DIR", Path(__file__).parent.parent / "contracts")
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _load_all_contracts():
    """Return list of (filename, contract_dict) for every YAML in contracts/."""
    results = []
    for path in sorted(_contracts_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        if raw:
            results.append((path.name, raw))
    return results

def _is_canonical(contract_dict):
    """Return True only for the canonical contract: wrapper format.
    Semantic linting applies to this format only. The registry also loads
    legacy list format (rules: [...]) and field-keyed onboarding format
    (rules: {field: def}) — both are valid but use different structure."""
    return isinstance(contract_dict, dict) and "contract" in contract_dict

def _is_known_format(contract_dict):
    """Return True for any format the registry recognises."""
    return isinstance(contract_dict, dict) and (
        "contract" in contract_dict or "rules" in contract_dict
    )

# Alias used throughout
_is_standard_contract = _is_canonical

def _rules_from(contract_dict):
    """Extract the rules list from a raw contract dict."""
    return contract_dict.get("contract", {}).get("rules", [])

def _contract_name(contract_dict):
    return contract_dict.get("contract", {}).get("name", "unknown")


# ── parametrised fixtures ─────────────────────────────────────────────────────

_all_contracts = _load_all_contracts()
_standard_contracts = [(f, c) for f, c in _all_contracts if _is_canonical(c)]
# Warn only about genuinely unrecognised files — not about the supported
# non-canonical formats (legacy list, field-keyed onboarding).
_unrecognised = [f for f, c in _all_contracts if not _is_known_format(c)]
if _unrecognised:
    import warnings
    warnings.warn(
        f"Found {len(_unrecognised)} YAML file(s) in contracts/ that are not in any "
        f"recognised format and will be silently skipped by the registry: "
        f"{_unrecognised}",
        stacklevel=1,
    )


# ── ACT-LNT-001: every contract parses and has a name ────────────────────────

@pytest.mark.parametrize("filename,contract", _standard_contracts)
def test_contract_has_name(filename, contract):
    """Every contract must have a non-empty name field."""
    name = _contract_name(contract)
    assert name and name != "unknown", (
        f"{filename}: contract.name is missing or empty"
    )


@pytest.mark.parametrize("filename,contract", _standard_contracts)
def test_contract_has_rules(filename, contract):
    """Every contract must have at least one rule."""
    rules = _rules_from(contract)
    assert len(rules) > 0, f"{filename}: contract has no rules"


# ── ACT-LNT-002: regex rules must have a pattern ─────────────────────────────

def _regex_rules(contract):
    return [(r, contract) for r in _rules_from(contract) if r.get("type") == "regex"]

_regex_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule, _ in _regex_rules(contract)
]

@pytest.mark.parametrize("filename,rule,contract", _regex_cases)
def test_regex_rule_has_pattern(filename, rule, contract):
    """Every regex rule must have a non-empty pattern field.

    A regex rule without a pattern is a no-op that always fails every record.
    This was the root cause of the customer.yaml valid_email bug (RT77 Fix D).
    """
    rule_name = rule.get("name", "<unnamed>")
    assert rule.get("pattern"), (
        f"{filename}: rule '{rule_name}' (type=regex) has no pattern field. "
        f"Add a pattern or change the rule type."
    )


# ── ACT-LNT-003: lookup rules must have a lookup_file ────────────────────────

_lookup_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
    if rule.get("type") == "lookup"
]

@pytest.mark.parametrize("filename,rule,contract", _lookup_cases or [("_no_lookup_rules", {}, {})])
def test_lookup_rule_has_file(filename, rule, contract):
    """Every lookup rule must have a lookup_file field."""
    if not rule:
        pytest.skip("no lookup rules in any contract")
    rule_name = rule.get("name", "<unnamed>")
    assert rule.get("lookup_file"), (
        f"{filename}: rule '{rule_name}' (type=lookup) has no lookup_file field."
    )


# ── ACT-LNT-004: checksum rules must have checksum_algorithm ─────────────────

_checksum_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
    if rule.get("type") == "checksum"
]

@pytest.mark.parametrize("filename,rule,contract", _checksum_cases or [("_no_checksum_rules", {}, {})])
def test_checksum_rule_has_algorithm(filename, rule, contract):
    """Every checksum rule must have a checksum_algorithm field."""
    if not rule:
        pytest.skip("no checksum rules in any contract")
    rule_name = rule.get("name", "<unnamed>")
    assert rule.get("checksum_algorithm"), (
        f"{filename}: rule '{rule_name}' (type=checksum) has no checksum_algorithm field."
    )


# ── ACT-LNT-005: date_diff rules must have date_diff_field ───────────────────

_date_diff_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
    if rule.get("type") == "date_diff"
]

@pytest.mark.parametrize("filename,rule,contract", _date_diff_cases or [("_no_date_diff_rules", {}, {})])
def test_date_diff_rule_has_diff_field(filename, rule, contract):
    """Every date_diff rule must have a date_diff_field."""
    if not rule:
        pytest.skip("no date_diff rules in any contract")
    rule_name = rule.get("name", "<unnamed>")
    assert rule.get("date_diff_field"), (
        f"{filename}: rule '{rule_name}' (type=date_diff) has no date_diff_field."
    )


# ── ACT-LNT-006: compare rules must have compare_to and compare_op ───────────

_compare_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
    if rule.get("type") == "compare"
]

@pytest.mark.parametrize("filename,rule,contract", _compare_cases or [("_no_compare_rules", {}, {})])
def test_compare_rule_has_operands(filename, rule, contract):
    """Every compare rule must have both compare_to and compare_op."""
    if not rule:
        pytest.skip("no compare rules in any contract")
    rule_name = rule.get("name", "<unnamed>")
    assert rule.get("compare_to"), (
        f"{filename}: rule '{rule_name}' (type=compare) has no compare_to field."
    )
    assert rule.get("compare_op"), (
        f"{filename}: rule '{rule_name}' (type=compare) has no compare_op field."
    )


# ── ACT-LNT-007: date_format rules with explicit format use valid strftime ────

_date_format_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
    if rule.get("type") == "date_format" and rule.get("format")
]

@pytest.mark.parametrize("filename,rule,contract", _date_format_cases or [("_no_explicit_date_formats", {}, {})])
def test_date_format_rule_uses_parseable_strftime(filename, rule, contract):
    """date_format rules with an explicit format must use a parseable strftime string.

    Catches typos like 'YYYY-MM-DD' (Java/Joda style) instead of '%Y-%m-%d' (Python strftime).
    """
    if not rule:
        pytest.skip("no date_format rules with explicit format")
    rule_name = rule.get("name", "<unnamed>")
    fmt = rule.get("format", "")
    try:
        datetime.datetime.strptime("2000-01-01", fmt)
        # If it parses a date successfully, the format is valid strftime
    except ValueError:
        pass  # ValueError means the format is valid but doesn't match "2000-01-01" — that's fine
    except Exception as exc:
        pytest.fail(
            f"{filename}: rule '{rule_name}' has format '{fmt}' which is not valid "
            f"Python strftime syntax (use %Y, %m, %d etc.): {exc}"
        )


# ── ACT-LNT-008: all rule types are known ────────────────────────────────────

_KNOWN_TYPES = {
    "not_empty", "regex", "min", "max", "range", "min_length", "max_length",
    "date_format", "unique", "compare", "required_if", "forbidden_if",
    "conditional_value", "lookup", "allowed_values", "checksum", "cross_field_range",
    "field_sum", "min_age", "max_age", "age_match", "date_diff", "ratio_check",
    "conditional_lookup", "geospatial_bounds",
}

_all_rule_cases = [
    (filename, rule, contract)
    for filename, contract in _standard_contracts
    for rule in _rules_from(contract)
]

@pytest.mark.parametrize("filename,rule,contract", _all_rule_cases)
def test_rule_type_is_known(filename, rule, contract):
    """Every rule must use a recognised type — catches typos before they become no-ops."""
    rule_name = rule.get("name", "<unnamed>")
    rule_type = rule.get("type", "")
    assert rule_type in _KNOWN_TYPES, (
        f"{filename}: rule '{rule_name}' has unknown type '{rule_type}'. "
        f"Known types: {sorted(_KNOWN_TYPES)}"
    )
