"""
Extended linter tests — edge cases not covered by test_contract_linter.py.

Focused on: LintIssue.to_dict(), rules-not-a-list, non-dict rule entries,
geospatial_bounds, ratio_check, forbidden_if, conditional_value missing fields,
file read error, and warn() branches.
"""

from core.linter import LintIssue, LintResult, lint_contract_yaml, lint_contract_file


# ---------------------------------------------------------------------------
# TestLintIssueToDict
# ---------------------------------------------------------------------------

class TestLintIssueToDict:
    """LintIssue.to_dict() — covers line 92."""

    def test_to_dict_structure(self):
        issue = LintIssue(
            severity="error",
            rule_name="my_rule",
            code="TEST_CODE",
            message="Test message",
        )
        d = issue.to_dict()
        assert d == {
            "severity": "error",
            "rule_name": "my_rule",
            "code": "TEST_CODE",
            "message": "Test message",
        }

    def test_to_dict_no_rule_name(self):
        issue = LintIssue(severity="warning", rule_name=None, code="W001", message="warning msg")
        d = issue.to_dict()
        assert d["rule_name"] is None
        assert d["severity"] == "warning"


# ---------------------------------------------------------------------------
# TestLintResultToDict
# ---------------------------------------------------------------------------

class TestLintResultToDict:
    def test_to_dict_passed(self):
        result = LintResult(contract_name="test")
        d = result.to_dict()
        assert d["passed"] is True
        assert d["error_count"] == 0
        assert d["warning_count"] == 0
        assert d["issues"] == []

    def test_to_dict_with_issues(self):
        result = LintResult(contract_name="test")
        result.issues.append(LintIssue(severity="error", rule_name=None, code="E001", message="err"))
        result.issues.append(LintIssue(severity="warning", rule_name=None, code="W001", message="warn"))
        d = result.to_dict()
        assert d["passed"] is False
        assert d["error_count"] == 1
        assert d["warning_count"] == 1


# ---------------------------------------------------------------------------
# TestLintRulesNotAList
# ---------------------------------------------------------------------------

class TestLintRulesNotAList:
    """rules: must be a list — covers lines 163-174."""

    def test_rules_is_string(self):
        yaml_str = "rules: not_a_list\n"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "INVALID_RULES_STRUCTURE" in codes

    def test_rules_is_dict(self):
        yaml_str = "rules:\n  key: value\n"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "INVALID_RULES_STRUCTURE" in codes


# ---------------------------------------------------------------------------
# TestLintRuleNotADict
# ---------------------------------------------------------------------------

class TestLintRuleNotADict:
    """Rule entries that are not dicts — covers lines 180-186."""

    def test_rule_entry_is_string(self):
        yaml_str = "rules:\n  - just_a_string\n"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "INVALID_RULE_ENTRY" in codes

    def test_rule_entry_is_int(self):
        yaml_str = "rules:\n  - 42\n"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "INVALID_RULE_ENTRY" in codes


# ---------------------------------------------------------------------------
# TestLintMinGreaterThanMax
# ---------------------------------------------------------------------------

class TestLintMinGreaterThanMax:
    """min > max — covers RANGE_MIN_GT_MAX."""

    def test_min_gt_max(self):
        yaml_str = (
            "rules:\n"
            "  - name: age_check\n"
            "    type: range\n"
            "    field: age\n"
            "    min: 100\n"
            "    max: 10\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "RANGE_MIN_GT_MAX" in codes

    def test_min_length_gt_max_length(self):
        yaml_str = (
            "rules:\n"
            "  - name: len_check\n"
            "    type: min_length\n"
            "    field: name\n"
            "    min_length: 50\n"
            "    max_length: 10\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "LENGTH_MIN_GT_MAX" in codes


# ---------------------------------------------------------------------------
# TestLintGeospatialBounds
# ---------------------------------------------------------------------------

class TestLintGeospatialBounds:
    """geospatial_bounds missing required fields — covers lines 298-303."""

    def test_geospatial_missing_all_fields(self):
        yaml_str = (
            "rules:\n"
            "  - name: geo_check\n"
            "    type: geospatial_bounds\n"
            "    field: location\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "GEOSPATIAL_MISSING_FIELD" in codes

    def test_geospatial_partial_fields(self):
        yaml_str = (
            "rules:\n"
            "  - name: geo_check\n"
            "    type: geospatial_bounds\n"
            "    field: location\n"
            "    geo_lon_field: lon\n"
            "    geo_min_lat: -90\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "GEOSPATIAL_MISSING_FIELD" in codes


# ---------------------------------------------------------------------------
# TestLintRatioCheck
# ---------------------------------------------------------------------------

class TestLintRatioCheck:
    """ratio_check missing fields — covers lines 306-312."""

    def test_ratio_check_missing_both(self):
        yaml_str = (
            "rules:\n"
            "  - name: ratio\n"
            "    type: ratio_check\n"
            "    field: value\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "RATIO_MISSING_NUMERATOR" in codes
        assert "RATIO_MISSING_DENOMINATOR" in codes


# ---------------------------------------------------------------------------
# TestLintForbiddenIf
# ---------------------------------------------------------------------------

class TestLintForbiddenIf:
    """forbidden_if validation — covers lines 360-371."""

    def test_forbidden_if_not_dict(self):
        yaml_str = (
            "rules:\n"
            "  - name: forbid\n"
            "    type: forbidden_if\n"
            "    field: discount\n"
            "    forbidden_if: not_a_dict\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "FORBIDDEN_IF_INVALID" in codes

    def test_forbidden_if_missing_field_key(self):
        yaml_str = (
            "rules:\n"
            "  - name: forbid\n"
            "    type: forbidden_if\n"
            "    field: discount\n"
            "    forbidden_if:\n"
            "      value: CANCELLED\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "FORBIDDEN_IF_MISSING_FIELD" in codes

    def test_forbidden_if_missing_value_key(self):
        yaml_str = (
            "rules:\n"
            "  - name: forbid\n"
            "    type: forbidden_if\n"
            "    field: discount\n"
            "    forbidden_if:\n"
            "      field: status\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "FORBIDDEN_IF_MISSING_VALUE" in codes


# ---------------------------------------------------------------------------
# TestLintConditionalValue
# ---------------------------------------------------------------------------

class TestLintConditionalValue:
    """conditional_value validation — covers lines 374-381."""

    def test_conditional_value_missing_must_equal(self):
        yaml_str = (
            "rules:\n"
            "  - name: cond\n"
            "    type: conditional_value\n"
            "    field: status\n"
            "    condition:\n"
            "      field: type\n"
            "      value: premium\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "CONDITIONAL_VALUE_MISSING_MUST_EQUAL" in codes

    def test_conditional_value_missing_condition(self):
        yaml_str = (
            "rules:\n"
            "  - name: cond\n"
            "    type: conditional_value\n"
            "    field: status\n"
            "    must_equal: APPROVED\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "CONDITIONAL_VALUE_MISSING_CONDITION" in codes


# ---------------------------------------------------------------------------
# TestLintFileReadError
# ---------------------------------------------------------------------------

class TestLintFileReadError:
    """lint_contract_file() with non-existent path — covers lines 393-401."""

    def test_file_not_found(self):
        result = lint_contract_file("/tmp/nonexistent_contract_zzz_123.yaml")
        codes = [i.code for i in result.issues]
        assert "FILE_READ_ERROR" in codes
        assert not result.passed


# ---------------------------------------------------------------------------
# TestLintInvalidTopLevel
# ---------------------------------------------------------------------------

class TestLintContractNameFromYaml:
    """Lines 163-164: extract contract_name from contract.name in YAML."""

    def test_contract_name_extracted_from_yaml(self):
        yaml_str = "contract:\n  name: my_auto_contract\nrules:\n  []\n"
        result = lint_contract_yaml(yaml_str, "")
        # contract_name should be auto-extracted from YAML
        assert result.contract_name == "my_auto_contract"


class TestLintNonNumericBounds:
    """Lines 227-228, 237-238: non-numeric min/max → pass without error."""

    def test_non_numeric_min_max_no_crash(self):
        yaml_str = (
            "rules:\n"
            "  - name: price_check\n"
            "    type: range\n"
            "    field: price\n"
            "    min: not_a_number\n"
            "    max: also_not_a_number\n"
            "    error_message: bad\n"
        )
        # Should not raise; non-numeric bounds are ignored by linter
        result = lint_contract_yaml(yaml_str, "test")
        # No RANGE_MIN_GT_MAX error (non-numeric values skipped)
        codes = [i.code for i in result.issues]
        assert "RANGE_MIN_GT_MAX" not in codes

    def test_non_numeric_min_length_max_length_no_crash(self):
        yaml_str = (
            "rules:\n"
            "  - name: name_len\n"
            "    type: min_length\n"
            "    field: name\n"
            "    min_length: abc\n"
            "    max_length: xyz\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "LENGTH_MIN_GT_MAX" not in codes


class TestLintAgeMinGtMax:
    """Lines 243-248: min_age > max_age → AGE_MIN_GT_MAX."""

    def test_min_age_gt_max_age(self):
        yaml_str = (
            "rules:\n"
            "  - name: age_check\n"
            "    type: min_age\n"
            "    field: dob\n"
            "    min_age: 120\n"
            "    max_age: 18\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "AGE_MIN_GT_MAX" in codes


class TestLintDateDiff:
    """Lines 316-317: date_diff missing date_diff_field."""

    def test_date_diff_missing_field(self):
        yaml_str = (
            "rules:\n"
            "  - name: date_diff_check\n"
            "    type: date_diff\n"
            "    field: start_date\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "DATE_DIFF_MISSING_FIELD" in codes


class TestLintAllowedValues:
    """Lines 340-342: allowed_values empty."""

    def test_allowed_values_empty(self):
        yaml_str = (
            "rules:\n"
            "  - name: status_check\n"
            "    type: allowed_values\n"
            "    field: status\n"
            "    allowed_values: []\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "ALLOWED_VALUES_EMPTY" in codes

    def test_allowed_values_missing(self):
        yaml_str = (
            "rules:\n"
            "  - name: status_check\n"
            "    type: allowed_values\n"
            "    field: status\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "ALLOWED_VALUES_EMPTY" in codes


class TestLintRequiredIf:
    """Lines 347-356: required_if validation."""

    def test_required_if_not_dict(self):
        yaml_str = (
            "rules:\n"
            "  - name: req_if\n"
            "    type: required_if\n"
            "    field: discount\n"
            "    required_if: not_a_dict\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "REQUIRED_IF_INVALID" in codes

    def test_required_if_missing_field_key(self):
        yaml_str = (
            "rules:\n"
            "  - name: req_if\n"
            "    type: required_if\n"
            "    field: discount\n"
            "    required_if:\n"
            "      value: premium\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "REQUIRED_IF_MISSING_FIELD" in codes

    def test_required_if_missing_value_key(self):
        yaml_str = (
            "rules:\n"
            "  - name: req_if\n"
            "    type: required_if\n"
            "    field: discount\n"
            "    required_if:\n"
            "      field: status\n"
            "    error_message: bad\n"
        )
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "REQUIRED_IF_MISSING_VALUE" in codes


class TestLintInvalidTopLevel:
    """YAML that parses to a non-dict — covers lines 151-158."""

    def test_yaml_is_a_list(self):
        yaml_str = "- item1\n- item2\n"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "INVALID_STRUCTURE" in codes

    def test_yaml_parse_error(self):
        yaml_str = "key: :\n  bad: : yaml"
        result = lint_contract_yaml(yaml_str, "test")
        codes = [i.code for i in result.issues]
        assert "YAML_PARSE_ERROR" in codes
