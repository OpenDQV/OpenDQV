"""Tests for core/code_generator.py — push-down validation code generation."""

import pytest
from opendqv.core.code_generator import generate_code


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


# ---------------------------------------------------------------------------
# Spark SQL generator
# ---------------------------------------------------------------------------

class TestSparkGenerator:
    def test_spark_produces_with_clause(self):
        out = generate_code([], target="spark")
        assert "WITH _dqv_checks AS" in out
        assert "_dqv_errors" in out
        assert "_dqv_valid" in out
        assert "__SOURCE_TABLE__" in out

    def test_spark_header_with_contract(self):
        out = generate_code([], target="spark", contract_name="customer", contract_version="1.0")
        assert "Contract: customer v1.0" in out
        assert "opendqv generate customer spark" in out

    def test_spark_not_empty(self):
        out = generate_code([_rule("not_empty", field="email")], target="spark")
        assert "email IS NULL OR TRIM" in out
        assert "CASE WHEN" in out

    def test_spark_regex(self):
        out = generate_code([_rule("regex", field="email", pattern=r"^[\w]+@[\w]+$")], target="spark")
        assert "regexp_like" in out
        assert "TODO" not in out

    def test_spark_min(self):
        out = generate_code([_rule("min", field="age", min_value=0)], target="spark")
        assert "CAST(age AS DOUBLE) < 0" in out

    def test_spark_max(self):
        out = generate_code([_rule("max", field="score", max_value=100)], target="spark")
        assert "CAST(score AS DOUBLE) > 100" in out

    def test_spark_range(self):
        out = generate_code([_rule("range", field="amount", min_value=0, max_value=1000)], target="spark")
        assert "CAST(amount AS DOUBLE) < 0" in out
        assert "CAST(amount AS DOUBLE) > 1000" in out

    def test_spark_min_length(self):
        out = generate_code([_rule("min_length", field="code", min_length=3)], target="spark")
        assert "LENGTH(CAST(code AS STRING)) < 3" in out

    def test_spark_max_length(self):
        out = generate_code([_rule("max_length", field="code", max_length=20)], target="spark")
        assert "LENGTH(CAST(code AS STRING)) > 20" in out

    def test_spark_date_format(self):
        out = generate_code([_rule("date_format", field="created_date")], target="spark")
        assert "to_date" in out
        assert "yyyy-MM-dd" in out

    def test_spark_allowed_values(self):
        out = generate_code([_rule("allowed_values", field="status", allowed_values=["A", "B", "C"])], target="spark")
        assert "NOT IN" in out
        assert "'A'" in out

    def test_spark_unique_emits_comment(self):
        out = generate_code([_rule("unique", field="id")], target="spark")
        assert "window function" in out.lower() or "window" in out.lower() or "PARTITION BY" in out

    def test_spark_api_only_emits_comment(self):
        out = generate_code([_rule("lookup", field="country")], target="spark")
        assert "requires API" in out
        assert "CASE WHEN" not in out

    def test_spark_multi_rule_array(self):
        rules = [
            _rule("not_empty", name="r1", field="email"),
            _rule("min", name="r2", field="age", min_value=18),
        ]
        out = generate_code(rules, target="spark")
        assert out.count("CASE WHEN") == 2

    def test_spark_sql_injection_safe(self):
        """Single quotes in error messages are escaped for SQL."""
        rule = _rule("not_empty", field="email", error_message="It's required")
        out = generate_code([rule], target="spark")
        assert "It''s required" in out

    def test_spark_strftime_conversion(self):
        """Python strftime format is converted to Spark SQL date format."""
        out = generate_code([_rule("date_format", field="dt", format="%d/%m/%Y")], target="spark")
        assert "dd/MM/yyyy" in out


# ---------------------------------------------------------------------------
# BigQuery JS UDF generator
# ---------------------------------------------------------------------------

class TestBigQueryGenerator:
    def test_bigquery_creates_udf(self):
        out = generate_code([], target="bigquery")
        assert "CREATE OR REPLACE FUNCTION" in out
        assert "LANGUAGE js" in out
        assert "STRUCT<valid BOOL, errors ARRAY<STRING>>" in out

    def test_bigquery_header_with_contract(self):
        out = generate_code([], target="bigquery", contract_name="customer", contract_version="2.0")
        assert "Contract: customer v2.0" in out
        assert "opendqv generate customer bigquery" in out

    def test_bigquery_usage_comment(self):
        out = generate_code([], target="bigquery")
        assert "TO_JSON_STRING" in out

    def test_bigquery_regex_rule(self):
        out = generate_code([_rule("regex", field="email", pattern=r"^[\w]+$")], target="bigquery")
        assert "RegExp" in out
        assert "TODO" not in out

    def test_bigquery_not_empty(self):
        out = generate_code([_rule("not_empty", field="name")], target="bigquery")
        assert "row['name']" in out

    def test_bigquery_range(self):
        out = generate_code([_rule("range", field="score", min_value=0, max_value=100)], target="bigquery")
        assert "parseFloat" in out
        assert "0" in out and "100" in out

    def test_bigquery_api_only_emits_note(self):
        out = generate_code([_rule("lookup", field="country")], target="bigquery")
        assert "NOTE" in out or "requires API" in out

    def test_bigquery_returns_valid_and_errors(self):
        out = generate_code([], target="bigquery")
        assert "errors.length === 0" in out
        assert "return" in out

    def test_bigquery_json_parse(self):
        out = generate_code([], target="bigquery")
        assert "JSON.parse" in out


class TestCodeGeneratorEdgeCases:
    """Cover missed lines in code_generator.py."""

    def test_js_rule_check_default_age_checked(self):
        """Calling _js_rule_check without age_checked initialises it to set() (line 212)."""
        from opendqv.core.code_generator import _js_rule_check
        result = _js_rule_check({"type": "not_empty", "field": "email", "name": "r"})
        assert isinstance(result, str)

    def test_spark_unknown_rule_type_returns_todo_note(self):
        """Spark generator: rule type not handled → todo_note (line 340)."""
        out = generate_code([_rule("age_match", field="dob", min_age=18)], target="spark")
        # age_match falls through to else → todo_note in the output
        assert "not yet implemented" in out or "NOTE" in out or "age" in out.lower()
