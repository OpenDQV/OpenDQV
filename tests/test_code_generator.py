"""Tests for core/code_generator.py — push-down validation code generation."""

import pytest
from core.code_generator import generate_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(type_, name="r1", field="value", **kwargs):
    base = {"type": type_, "name": name, "field": field, "error_message": f"{name} failed"}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------

class TestTargetValidation:
    def test_unsupported_target_raises(self):
        with pytest.raises(ValueError, match="Unsupported target"):
            generate_code([], target="ruby")


# ---------------------------------------------------------------------------
# Implementable rule types — no TODO, no NOTE
# ---------------------------------------------------------------------------

class TestImplementableRules:
    TARGETS = ["snowflake", "js", "salesforce"]

    @pytest.mark.parametrize("target", TARGETS)
    def test_regex_no_todo(self, target):
        out = generate_code([_rule("regex", pattern=r"^\d+$")], target=target)
        assert "TODO" not in out
        assert "NOTE" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_min_no_todo(self, target):
        out = generate_code([_rule("min", min_value=0)], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_max_no_todo(self, target):
        out = generate_code([_rule("max", max_value=100)], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_range_no_todo(self, target):
        out = generate_code([_rule("range", min_value=0, max_value=100)], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_not_empty_no_todo(self, target):
        out = generate_code([_rule("not_empty")], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_min_length_no_todo(self, target):
        out = generate_code([_rule("min_length", min_length=3)], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_date_format_no_todo(self, target):
        out = generate_code([_rule("date_format")], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_unique_emits_comment_not_todo(self, target):
        out = generate_code([_rule("unique")], target=target)
        assert "TODO" not in out
        assert "Unique" in out or "unique" in out.lower()


# ---------------------------------------------------------------------------
# age_match — must NOT emit TODO; age code must be present
# ---------------------------------------------------------------------------

class TestAgeMatchRule:
    TARGETS = ["snowflake", "js", "salesforce"]

    @pytest.mark.parametrize("target", TARGETS)
    def test_age_match_no_todo(self, target):
        rule = _rule("age_match", field="date_of_birth", min_age=18)
        out = generate_code([rule], target=target)
        assert "TODO" not in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_age_match_generates_age_code(self, target):
        rule = _rule("age_match", field="date_of_birth", min_age=18)
        out = generate_code([rule], target=target)
        assert "18" in out   # min_age value appears
        assert "age" in out.lower()

    @pytest.mark.parametrize("target", TARGETS)
    def test_age_match_max_age(self, target):
        rule = _rule("age_match", field="dob", max_age=65)
        out = generate_code([rule], target=target)
        assert "TODO" not in out
        assert "65" in out

    @pytest.mark.parametrize("target", TARGETS)
    def test_age_match_both_bounds(self, target):
        rule = _rule("age_match", field="dob", min_age=18, max_age=65)
        out = generate_code([rule], target=target)
        assert "TODO" not in out
        assert "18" in out
        assert "65" in out

    def test_age_match_deduplicated_across_rules(self):
        """Two age_match rules on the same field should only generate age code once."""
        r1 = _rule("age_match", name="r1", field="dob", min_age=18)
        r2 = _rule("age_match", name="r2", field="dob", max_age=65)
        out = generate_code([r1, r2], target="js")
        # The age calculation for 'dob' should appear once, not twice
        assert out.count("new Date(row['dob'])") == 1


# ---------------------------------------------------------------------------
# API-only rule types — emit NOTE, not TODO
# ---------------------------------------------------------------------------

class TestApiOnlyRules:
    API_ONLY_TYPES = [
        "required_if", "lookup", "compare", "date_diff", "checksum",
        "cross_field_range", "field_sum", "forbidden_if", "conditional_value",
        "ratio_check", "geospatial_bounds", "conditional_lookup", "allowed_values",
    ]
    TARGETS = ["snowflake", "js", "salesforce"]

    @pytest.mark.parametrize("target", TARGETS)
    @pytest.mark.parametrize("rtype", API_ONLY_TYPES)
    def test_api_only_emits_note_not_todo(self, target, rtype):
        out = generate_code([_rule(rtype)], target=target)
        assert "TODO" not in out
        assert "NOTE" in out or "requires API" in out


# ---------------------------------------------------------------------------
# Unknown rule type — must emit TODO
# ---------------------------------------------------------------------------

class TestUnknownRuleType:
    @pytest.mark.parametrize("target", ["snowflake", "js", "salesforce"])
    def test_unknown_type_emits_todo(self, target):
        out = generate_code([_rule("future_rule_type_xyz")], target=target)
        assert "TODO" in out


# ---------------------------------------------------------------------------
# Header generation
# ---------------------------------------------------------------------------

class TestHeaderGeneration:
    def test_snowflake_header_with_contract(self):
        out = generate_code([], target="snowflake", contract_name="customer", contract_version="1")
        assert "Contract: customer v1" in out
        assert "opendqv generate customer snowflake" in out

    def test_salesforce_header_with_contract(self):
        out = generate_code([], target="salesforce", contract_name="customer", contract_version="2")
        assert "Contract: customer v2" in out
        assert "opendqv generate customer salesforce" in out

    def test_js_header_with_contract(self):
        out = generate_code([], target="js", contract_name="customer", contract_version="3")
        assert "Contract: customer v3" in out

    def test_no_header_without_contract_name(self):
        out = generate_code([], target="js")
        assert "Contract:" not in out
        assert "Generated by OpenDQV" not in out


# ---------------------------------------------------------------------------
# Rule dict passthrough (not just Rule objects)
# ---------------------------------------------------------------------------

class TestRuleDictPassthrough:
    def test_accepts_raw_dicts(self):
        rule = {"type": "min", "name": "min_age", "field": "age",
                "min_value": 0, "error_message": "age must be >= 0"}
        out = generate_code([rule], target="js")
        assert "TODO" not in out
        assert "parseFloat" in out


# ---------------------------------------------------------------------------
# Snowflake output structure
# ---------------------------------------------------------------------------

class TestSnowflakeStructure:
    def test_snowflake_creates_udf(self):
        out = generate_code([], target="snowflake")
        assert "CREATE OR REPLACE FUNCTION opendqv_validate" in out
        assert "LANGUAGE JAVASCRIPT" in out
        assert "$$" in out

    def test_snowflake_empty_rules(self):
        out = generate_code([], target="snowflake")
        assert "errors.length === 0" in out


# ---------------------------------------------------------------------------
# Salesforce output structure
# ---------------------------------------------------------------------------

class TestSalesforceStructure:
    def test_salesforce_creates_class(self):
        out = generate_code([], target="salesforce")
        assert "public class OpenDQVValidator" in out
        assert "List<Map<String, Object>>" in out

    def test_salesforce_empty_rules(self):
        out = generate_code([], target="salesforce")
        assert "errors.isEmpty()" in out


# ---------------------------------------------------------------------------
# JS output structure
# ---------------------------------------------------------------------------

class TestJsStructure:
    def test_js_creates_function(self):
        out = generate_code([], target="js")
        assert "function opendqvValidate(data)" in out

    def test_js_empty_rules(self):
        out = generate_code([], target="js")
        assert "errors.length === 0" in out
