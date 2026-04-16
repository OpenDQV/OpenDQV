"""
Tests for core/linter.py — contract static analysis.
"""

from opendqv.core.linter import lint_contract_yaml, LintResult


def _lint(yaml_str: str) -> LintResult:
    return lint_contract_yaml(yaml_str, contract_name="test")


def _rule(**kwargs) -> str:
    """Build a minimal YAML rules block with one rule dict."""
    parts = ["rules:"]
    parts.append("  - name: r1")
    for k, v in kwargs.items():
        if isinstance(v, list):
            parts.append(f"    {k}:")
            for item in v:
                parts.append(f"      - {item}")
        elif isinstance(v, dict):
            parts.append(f"    {k}:")
            for dk, dv in v.items():
                parts.append(f"      {dk}: {dv}")
        else:
            parts.append(f"    {k}: {v}")
    return "\n".join(parts)


# ── Happy path ────────────────────────────────────────────────────────────────

class TestCleanContracts:
    def test_empty_rules_passes(self):
        result = _lint("rules: []")
        assert result.passed
        assert result.issues == []

    def test_valid_not_empty_passes(self):
        result = _lint(_rule(type="not_empty", field="email"))
        assert result.passed

    def test_valid_regex_passes(self):
        result = _lint(_rule(type="regex", field="email", pattern=r"^[\w]+$"))
        assert result.passed

    def test_valid_range_passes(self):
        result = _lint(_rule(type="range", field="amount", min=0, max=1000))
        assert result.passed

    def test_valid_compare_passes(self):
        result = _lint(_rule(type="compare", field="end_date",
                             compare_to="start_date", compare_op="gt"))
        assert result.passed

    def test_valid_checksum_passes(self):
        result = _lint(_rule(type="checksum", field="iban",
                             checksum_algorithm="iban_mod97"))
        assert result.passed


# ── Duplicate rule names ──────────────────────────────────────────────────────

class TestDuplicateRuleNames:
    def test_duplicate_name_is_error(self):
        yaml = """
rules:
  - name: check_email
    type: not_empty
    field: email
  - name: check_email
    type: regex
    field: email
    pattern: "^\\\\w+$"
"""
        result = _lint(yaml)
        codes = [i.code for i in result.issues]
        assert "DUPLICATE_RULE_NAME" in codes
        assert not result.passed

    def test_unique_names_ok(self):
        yaml = """
rules:
  - name: rule_a
    type: not_empty
    field: email
  - name: rule_b
    type: not_empty
    field: name
"""
        result = _lint(yaml)
        assert result.passed


# ── Unknown rule type ─────────────────────────────────────────────────────────

class TestUnknownRuleType:
    def test_unknown_type_is_error(self):
        result = _lint(_rule(type="magic_check", field="foo"))
        codes = [i.code for i in result.issues]
        assert "UNKNOWN_RULE_TYPE" in codes

    def test_known_types_ok(self):
        for rtype in ("not_empty", "regex", "range", "min", "max", "lookup",
                      "checksum", "unique", "date_format"):
            yaml = f"rules:\n  - name: r1\n    type: {rtype}\n    field: f"
            result = _lint(yaml)
            assert "UNKNOWN_RULE_TYPE" not in [i.code for i in result.issues], \
                f"Type '{rtype}' was incorrectly flagged as unknown"


# ── Range checks ──────────────────────────────────────────────────────────────

class TestRangeChecks:
    def test_min_gt_max_is_error(self):
        result = _lint(_rule(type="range", field="score", min=100, max=10))
        codes = [i.code for i in result.issues]
        assert "RANGE_MIN_GT_MAX" in codes

    def test_min_eq_max_is_ok(self):
        result = _lint(_rule(type="range", field="score", min=50, max=50))
        assert result.passed

    def test_length_min_gt_max_is_error(self):
        result = _lint(_rule(type="min_length", field="name",
                             min_length=20, max_length=5))
        codes = [i.code for i in result.issues]
        assert "LENGTH_MIN_GT_MAX" in codes


# ── Regex checks ──────────────────────────────────────────────────────────────

class TestRegexChecks:
    def test_invalid_pattern_is_error(self):
        yaml = """
rules:
  - name: r1
    type: regex
    field: email
    pattern: "[unclosed"
"""
        result = _lint(yaml)
        codes = [i.code for i in result.issues]
        assert "REGEX_INVALID_PATTERN" in codes

    def test_missing_pattern_is_error(self):
        result = _lint(_rule(type="regex", field="email"))
        codes = [i.code for i in result.issues]
        assert "REGEX_MISSING_PATTERN" in codes

    def test_builtin_pattern_skips_compile(self):
        result = _lint(_rule(type="regex", field="email", pattern="builtin:email"))
        assert result.passed


# ── Compare checks ────────────────────────────────────────────────────────────

class TestCompareChecks:
    def test_missing_compare_to_is_error(self):
        result = _lint(_rule(type="compare", field="end", compare_op="gt"))
        codes = [i.code for i in result.issues]
        assert "COMPARE_MISSING_COMPARE_TO" in codes

    def test_missing_compare_op_is_error(self):
        result = _lint(_rule(type="compare", field="end", compare_to="start"))
        codes = [i.code for i in result.issues]
        assert "COMPARE_MISSING_COMPARE_OP" in codes

    def test_invalid_compare_op_is_error(self):
        result = _lint(_rule(type="compare", field="end",
                             compare_to="start", compare_op="bigger_than"))
        codes = [i.code for i in result.issues]
        assert "COMPARE_INVALID_OP" in codes

    def test_symbol_op_is_ok(self):
        yaml = """
rules:
  - name: r1
    type: compare
    field: end
    compare_to: start
    compare_op: ">"
"""
        result = _lint(yaml)
        assert result.passed


# ── cross_field_range checks ──────────────────────────────────────────────────

class TestCrossFieldRange:
    def test_missing_min_field_is_error(self):
        result = _lint(_rule(type="cross_field_range", field="value",
                             cross_max_field="upper"))
        codes = [i.code for i in result.issues]
        assert "CROSS_FIELD_RANGE_MISSING_MIN" in codes

    def test_missing_max_field_is_error(self):
        result = _lint(_rule(type="cross_field_range", field="value",
                             cross_min_field="lower"))
        codes = [i.code for i in result.issues]
        assert "CROSS_FIELD_RANGE_MISSING_MAX" in codes


# ── field_sum checks ──────────────────────────────────────────────────────────

class TestFieldSum:
    def test_missing_sum_fields_is_error(self):
        result = _lint(_rule(type="field_sum", field="total", sum_equals=100))
        codes = [i.code for i in result.issues]
        assert "FIELD_SUM_MISSING_SUM_FIELDS" in codes

    def test_missing_sum_equals_is_error(self):
        yaml = """
rules:
  - name: r1
    type: field_sum
    field: total
    sum_fields:
      - a
      - b
"""
        result = _lint(yaml)
        codes = [i.code for i in result.issues]
        assert "FIELD_SUM_MISSING_SUM_EQUALS" in codes


# ── Checksum checks ───────────────────────────────────────────────────────────

class TestChecksumChecks:
    def test_missing_algorithm_is_error(self):
        result = _lint(_rule(type="checksum", field="iban"))
        codes = [i.code for i in result.issues]
        assert "CHECKSUM_MISSING_ALGORITHM" in codes

    def test_unknown_algorithm_is_error(self):
        result = _lint(_rule(type="checksum", field="iban",
                             checksum_algorithm="luhn_v99"))
        codes = [i.code for i in result.issues]
        assert "CHECKSUM_UNKNOWN_ALGORITHM" in codes


# ── lookup checks ─────────────────────────────────────────────────────────────

class TestLookupChecks:
    def test_missing_lookup_file_is_error(self):
        result = _lint(_rule(type="lookup", field="country_code"))
        codes = [i.code for i in result.issues]
        assert "LOOKUP_MISSING_FILE" in codes


# ── geospatial checks ─────────────────────────────────────────────────────────

class TestGeospatialChecks:
    def test_missing_geo_fields_are_errors(self):
        result = _lint(_rule(type="geospatial_bounds", field="lat"))
        codes = [i.code for i in result.issues]
        assert "GEOSPATIAL_MISSING_FIELD" in codes
        # All 5 required fields missing — should be 5 errors
        geo_errors = [i for i in result.issues if i.code == "GEOSPATIAL_MISSING_FIELD"]
        assert len(geo_errors) == 5


# ── ratio_check checks ────────────────────────────────────────────────────────

class TestRatioCheck:
    def test_missing_numerator_is_error(self):
        result = _lint(_rule(type="ratio_check", field="ratio",
                             ratio_denominator="total"))
        codes = [i.code for i in result.issues]
        assert "RATIO_MISSING_NUMERATOR" in codes

    def test_missing_denominator_is_error(self):
        result = _lint(_rule(type="ratio_check", field="ratio",
                             ratio_numerator="part"))
        codes = [i.code for i in result.issues]
        assert "RATIO_MISSING_DENOMINATOR" in codes


# ── YAML error handling ───────────────────────────────────────────────────────

class TestYamlErrors:
    def test_invalid_yaml_returns_error(self):
        result = _lint("rules: [unclosed")
        codes = [i.code for i in result.issues]
        assert "YAML_PARSE_ERROR" in codes

    def test_non_mapping_yaml_returns_error(self):
        result = _lint("- just a list")
        codes = [i.code for i in result.issues]
        assert "INVALID_STRUCTURE" in codes


# ── max:/min: alias confusion on length rules ───────────────────────────────

class TestLengthAliasConfusion:
    """Catch the silent bug where `max: 18` on a max_length rule maps to
    max_value (numeric), leaving max_length=None so the rule never fires."""

    def test_max_on_max_length_rule_warns(self):
        result = _lint(_rule(type="max_length", field="name", max=18))
        codes = [i.code for i in result.issues]
        assert "MAX_LENGTH_ALIAS_CONFUSION" in codes
        issue = [i for i in result.issues if i.code == "MAX_LENGTH_ALIAS_CONFUSION"][0]
        assert issue.severity == "warning"
        assert "use `max_length:` instead" in issue.message

    def test_max_value_on_max_length_rule_warns(self):
        result = _lint(_rule(type="max_length", field="name", max_value=18))
        codes = [i.code for i in result.issues]
        assert "MAX_LENGTH_ALIAS_CONFUSION" in codes

    def test_max_length_on_max_length_rule_clean(self):
        result = _lint(_rule(type="max_length", field="name", max_length=18))
        codes = [i.code for i in result.issues]
        assert "MAX_LENGTH_ALIAS_CONFUSION" not in codes

    def test_max_with_max_length_present_no_warn(self):
        """If both max: and max_length: are set, no alias confusion."""
        result = _lint(_rule(type="max_length", field="name", max=100, max_length=18))
        codes = [i.code for i in result.issues]
        assert "MAX_LENGTH_ALIAS_CONFUSION" not in codes

    def test_min_on_min_length_rule_warns(self):
        result = _lint(_rule(type="min_length", field="name", min=3))
        codes = [i.code for i in result.issues]
        assert "MIN_LENGTH_ALIAS_CONFUSION" in codes
        issue = [i for i in result.issues if i.code == "MIN_LENGTH_ALIAS_CONFUSION"][0]
        assert issue.severity == "warning"
        assert "use `min_length:` instead" in issue.message

    def test_min_value_on_min_length_rule_warns(self):
        result = _lint(_rule(type="min_length", field="name", min_value=3))
        codes = [i.code for i in result.issues]
        assert "MIN_LENGTH_ALIAS_CONFUSION" in codes

    def test_min_length_on_min_length_rule_clean(self):
        result = _lint(_rule(type="min_length", field="name", min_length=3))
        codes = [i.code for i in result.issues]
        assert "MIN_LENGTH_ALIAS_CONFUSION" not in codes

    def test_min_with_min_length_present_no_warn(self):
        """If both min: and min_length: are set, no alias confusion."""
        result = _lint(_rule(type="min_length", field="name", min=1, min_length=3))
        codes = [i.code for i in result.issues]
        assert "MIN_LENGTH_ALIAS_CONFUSION" not in codes

    def test_max_on_non_length_rule_no_warn(self):
        """max: on a range rule is perfectly normal — no warning."""
        result = _lint(_rule(type="range", field="score", min=0, max=100))
        codes = [i.code for i in result.issues]
        assert "MAX_LENGTH_ALIAS_CONFUSION" not in codes

    def test_min_on_non_length_rule_no_warn(self):
        """min: on a min rule is perfectly normal — no warning."""
        result = _lint(_rule(type="min", field="score", min=0))
        codes = [i.code for i in result.issues]
        assert "MIN_LENGTH_ALIAS_CONFUSION" not in codes

    def test_alias_confusion_still_passes_lint(self):
        """Warnings don't block — passed should still be True."""
        result = _lint(_rule(type="max_length", field="name", max=18))
        assert result.passed  # warnings don't count as errors
