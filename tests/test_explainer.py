"""
Tests for core/explainer.py — all rule type handlers and quick_fix().

explain_rule() dispatches to per-type template functions; these tests exercise
every branch. All functions are pure (no HTTP, no DB, no fixtures).
"""
import pytest

from core.rule_parser import Rule
from core.explainer import explain_rule, quick_fix


def _rule(**kwargs) -> Rule:
    """Construct a minimal Rule with given overrides."""
    defaults = {
        "name": "test_rule",
        "type": "not_empty",
        "field": "test_field",
        "severity": "error",
        "error_message": "Field failed",
    }
    defaults.update(kwargs)
    return Rule(**defaults)


def _assert_explain_structure(result):
    """Every explain_rule() result must have these keys."""
    assert isinstance(result, dict)
    assert "rule_type" in result
    assert "explanation" in result
    assert "valid_examples" in result
    assert "invalid_examples" in result
    assert "constraint" in result
    assert isinstance(result["explanation"], str)
    assert len(result["explanation"]) > 0


# ---------------------------------------------------------------------------
# TestExplainRuleAllTypes
# ---------------------------------------------------------------------------

class TestExplainRuleAllTypes:
    """Cover every elif branch in explain_rule()."""

    def test_not_empty(self):
        r = _rule(type="not_empty")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "not_empty"
        assert "test_field" in result["explanation"]

    def test_min(self):
        r = _rule(type="min", min_value=18.0)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "18" in result["explanation"]

    def test_min_none_value(self):
        r = _rule(type="min")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_max(self):
        r = _rule(type="max", max_value=100.0)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "100" in result["explanation"]

    def test_max_none_value(self):
        r = _rule(type="max")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_range(self):
        r = _rule(type="range", min_value=0.0, max_value=150.0)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "0" in result["explanation"]
        assert "150" in result["explanation"]

    def test_min_length(self):
        r = _rule(type="min_length", min_length=5)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "5" in result["explanation"]

    def test_max_length(self):
        r = _rule(type="max_length", max_length=255)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "max_length"

    def test_max_length_none(self):
        r = _rule(type="max_length")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_regex(self):
        r = _rule(type="regex", pattern=r"^\d{4}$", negate=False)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert r"^\d{4}$" in result["explanation"]

    def test_regex_negate(self):
        r = _rule(type="regex", pattern=r"^\d{4}$", negate=True)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "NOT" in result["explanation"]
        assert result["constraint"]["negate"] is True

    def test_email(self):
        r = _rule(type="email")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "email"
        assert "@" in result["explanation"]

    def test_date_format(self):
        r = _rule(type="date_format", format="%Y-%m-%d")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "%Y-%m-%d" in result["explanation"]

    def test_date_format_none(self):
        r = _rule(type="date_format")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_enum(self):
        r = _rule(type="enum", pattern="^(active|inactive|pending)$")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "enum"
        assert "active" in result["explanation"] or "active" in str(result["valid_examples"])

    def test_enum_none_pattern(self):
        r = _rule(type="enum")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_lookup(self):
        r = _rule(type="lookup", lookup_file="/path/to/ids.txt")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "ids.txt" in result["explanation"]

    def test_lookup_none(self):
        r = _rule(type="lookup")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_min_age(self):
        r = _rule(type="min_age", min_age=18)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "min_age"
        assert "18" in result["explanation"]

    def test_min_age_none(self):
        r = _rule(type="min_age")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_max_age(self):
        r = _rule(type="max_age", max_age=120)
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "max_age"
        assert "120" in result["explanation"]

    def test_max_age_none(self):
        r = _rule(type="max_age")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_unique(self):
        r = _rule(type="unique")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "unique" in result["explanation"].lower()

    def test_compare(self):
        r = _rule(type="compare", compare_to="end_date", compare_op="lt")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "end_date" in result["explanation"]
        assert "<" in result["explanation"]

    def test_compare_all_ops(self):
        for op in ("gt", "lt", "gte", "lte", "eq", "neq"):
            r = _rule(type="compare", compare_to="other_field", compare_op=op)
            result = explain_rule(r)
            _assert_explain_structure(result)

    def test_required_if(self):
        r = _rule(type="required_if", required_if={"field": "status", "value": "premium"})
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "status" in result["explanation"]
        assert "premium" in result["explanation"]

    def test_required_if_empty_condition(self):
        r = _rule(type="required_if", required_if={})
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_checksum(self):
        r = _rule(type="checksum", checksum_algorithm="iban_mod97")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "iban_mod97" in result["explanation"]

    def test_checksum_none(self):
        r = _rule(type="checksum")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_cross_field_range(self):
        r = _rule(type="cross_field_range", cross_min_field="min_val", cross_max_field="max_val")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "min_val" in result["explanation"]
        assert "max_val" in result["explanation"]

    def test_field_sum(self):
        r = _rule(
            type="field_sum",
            sum_fields=["amount", "tax"],
            sum_equals=100.0,
            sum_tolerance=0.01,
        )
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "100" in result["explanation"]

    def test_field_sum_no_fields(self):
        r = _rule(type="field_sum")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_forbidden_if(self):
        r = _rule(type="forbidden_if", forbidden_if={"field": "status", "value": "CANCELLED"})
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "forbidden_if"
        assert "CANCELLED" in result["explanation"]

    def test_forbidden_if_empty_condition(self):
        r = _rule(type="forbidden_if", forbidden_if={})
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_conditional_value(self):
        r = _rule(type="conditional_value", must_equal="PENDING")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert "PENDING" in result["explanation"]
        assert "PENDING" in result["valid_examples"]

    def test_conditional_value_none(self):
        r = _rule(type="conditional_value")
        result = explain_rule(r)
        _assert_explain_structure(result)

    def test_generic_unknown_type(self):
        r = _rule(type="unknown_rule_type_zzz")
        result = explain_rule(r)
        _assert_explain_structure(result)
        assert result["rule_type"] == "unknown_rule_type_zzz"


# ---------------------------------------------------------------------------
# TestQuickFix
# ---------------------------------------------------------------------------

class TestQuickFix:
    """Cover quick_fix() for all known rule types and fallbacks."""

    @pytest.mark.parametrize("rule_type", [
        "not_empty", "email", "date_format", "min", "max", "range",
        "min_length", "max_length", "regex", "enum", "lookup",
        "allowed_values", "required_if", "forbidden_if", "checksum",
        "unique", "compare", "cross_field_range", "field_sum",
        "min_age", "max_age", "conditional_value", "age_match",
    ])
    def test_known_rule_type(self, rule_type):
        result = quick_fix(rule_type)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_rule_type_with_error_message(self):
        result = quick_fix("custom_rule", "Value must be positive. Additional details here.")
        assert "positive" in result.lower() or len(result) > 0

    def test_unknown_rule_type_no_error_message(self):
        result = quick_fix("custom_rule_zzz")
        assert "custom_rule_zzz" in result

    def test_long_error_message_truncated(self):
        long_msg = "A" * 200
        result = quick_fix("custom_rule", long_msg)
        assert len(result) <= 120
