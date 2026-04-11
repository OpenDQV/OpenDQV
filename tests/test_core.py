"""Tests for core validation engine — single record and batch."""

import pytest
from unittest.mock import patch
from opendqv.core.rule_parser import Rule, Severity, parse_rules
from opendqv.core.validator import validate_record, validate_batch


class TestRuleParser:
    def test_parse_rules(self):
        yaml_str = """rules:
  - name: email_check
    type: regex
    field: email
    pattern: "^.+@.+$"
    severity: error
"""
        rules = parse_rules(yaml_str)
        assert len(rules) == 1
        assert rules[0].name == "email_check"
        assert rules[0].severity == Severity.ERROR

    def test_min_alias(self):
        rules = parse_rules("rules:\n  - name: t\n    type: min\n    field: age\n    min: 18\n")
        assert rules[0].min_value == 18

    def test_max_alias(self):
        rules = parse_rules("rules:\n  - name: t\n    type: range\n    field: s\n    min: 0\n    max: 100\n")
        assert rules[0].min_value == 0
        assert rules[0].max_value == 100

    def test_empty_yaml(self):
        assert parse_rules("") == []

    def test_severity_default(self):
        rules = parse_rules("rules:\n  - name: t\n    type: not_empty\n    field: f\n")
        assert rules[0].severity == Severity.ERROR

    def test_severity_warning(self):
        rules = parse_rules("rules:\n  - name: t\n    type: not_empty\n    field: f\n    severity: warning\n")
        assert rules[0].severity == Severity.WARNING


class TestSingleRecordValidator:
    """Tests for validate_record() — pure Python, fast path."""

    def test_valid_record(self):
        rules = [Rule(name="email", type="regex", field="email", pattern="^.+@.+$")]
        result = validate_record({"email": "test@example.com"}, rules)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_invalid_record(self):
        rules = [Rule(name="email", type="regex", field="email", pattern="^.+@.+$")]
        result = validate_record({"email": "not-an-email"}, rules)
        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["field"] == "email"

    def test_not_empty_pass(self):
        rules = [Rule(name="name_req", type="not_empty", field="name")]
        assert validate_record({"name": "Alice"}, rules)["valid"] is True

    def test_not_empty_fail_empty(self):
        rules = [Rule(name="name_req", type="not_empty", field="name")]
        assert validate_record({"name": ""}, rules)["valid"] is False

    def test_not_empty_fail_none(self):
        rules = [Rule(name="name_req", type="not_empty", field="name")]
        assert validate_record({"name": None}, rules)["valid"] is False

    def test_not_empty_fail_missing(self):
        rules = [Rule(name="name_req", type="not_empty", field="name")]
        assert validate_record({}, rules)["valid"] is False

    def test_min_pass(self):
        rules = [Rule(name="age", type="min", field="age", min_value=18)]
        assert validate_record({"age": 25}, rules)["valid"] is True

    def test_min_fail(self):
        rules = [Rule(name="age", type="min", field="age", min_value=18)]
        assert validate_record({"age": 15}, rules)["valid"] is False

    def test_max_pass(self):
        rules = [Rule(name="age", type="max", field="age", max_value=150)]
        assert validate_record({"age": 30}, rules)["valid"] is True

    def test_max_fail(self):
        rules = [Rule(name="age", type="max", field="age", max_value=150)]
        assert validate_record({"age": 200}, rules)["valid"] is False

    def test_range_pass(self):
        rules = [Rule(name="score", type="range", field="score", min_value=0, max_value=100)]
        assert validate_record({"score": 85}, rules)["valid"] is True

    def test_range_fail_low(self):
        rules = [Rule(name="score", type="range", field="score", min_value=0, max_value=100)]
        assert validate_record({"score": -5}, rules)["valid"] is False

    def test_range_fail_high(self):
        rules = [Rule(name="score", type="range", field="score", min_value=0, max_value=100)]
        assert validate_record({"score": 150}, rules)["valid"] is False

    def test_min_length_pass(self):
        rules = [Rule(name="pw", type="min_length", field="pw", min_length=8)]
        assert validate_record({"pw": "longpassword"}, rules)["valid"] is True

    def test_min_length_fail(self):
        rules = [Rule(name="pw", type="min_length", field="pw", min_length=8)]
        assert validate_record({"pw": "short"}, rules)["valid"] is False

    def test_max_length_pass(self):
        rules = [Rule(name="code", type="max_length", field="code", max_length=5)]
        assert validate_record({"code": "ABC"}, rules)["valid"] is True

    def test_max_length_fail(self):
        rules = [Rule(name="code", type="max_length", field="code", max_length=5)]
        assert validate_record({"code": "TOOLONGCODE"}, rules)["valid"] is False

    def test_date_format_pass(self):
        rules = [Rule(name="date", type="date_format", field="date")]
        assert validate_record({"date": "2024-01-15"}, rules)["valid"] is True

    def test_date_format_fail(self):
        rules = [Rule(name="date", type="date_format", field="date")]
        assert validate_record({"date": "not-a-date"}, rules)["valid"] is False

    def test_unique_skipped_for_single(self):
        rules = [Rule(name="id", type="unique", field="id")]
        assert validate_record({"id": "123"}, rules)["valid"] is True

    def test_warning_doesnt_block(self):
        rules = [Rule(name="bal", type="min", field="balance", min_value=0, severity=Severity.WARNING)]
        result = validate_record({"balance": -10}, rules)
        assert result["valid"] is True  # warnings don't block
        assert len(result["warnings"]) == 1
        assert result["errors"] == []

    def test_multiple_rules_mixed(self):
        rules = [
            Rule(name="email", type="regex", field="email", pattern="^.+@.+$"),
            Rule(name="age", type="min", field="age", min_value=18),
            Rule(name="name", type="not_empty", field="name"),
        ]
        result = validate_record({"email": "bad", "age": 15, "name": "Alice"}, rules)
        assert result["valid"] is False
        assert len(result["errors"]) == 2  # email + age fail
        fields = {e["field"] for e in result["errors"]}
        assert fields == {"email", "age"}

    def test_per_field_error_structure(self):
        rules = [Rule(name="email_check", type="regex", field="email", pattern="^.+@.+$",
                       error_message="Bad email")]
        result = validate_record({"email": "bad"}, rules)
        err = result["errors"][0]
        assert err["field"] == "email"
        assert err["rule"] == "email_check"
        assert err["message"] == "Bad email"
        assert err["severity"] == "error"


class TestBatchValidator:
    """Tests for validate_batch() — DuckDB-powered."""

    def test_all_pass(self):
        records = [
            {"email": "a@b.com", "age": 25},
            {"email": "c@d.com", "age": 30},
        ]
        rules = [Rule(name="email", type="regex", field="email", pattern="^.+@.+$")]
        result = validate_batch(records, rules)
        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 0

    def test_some_fail(self):
        records = [
            {"email": "good@test.com"},
            {"email": "bad"},
        ]
        rules = [Rule(name="email", type="regex", field="email", pattern="^.+@.+$")]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False

    def test_unique_check(self):
        records = [{"id": "1"}, {"id": "1"}, {"id": "2"}]
        rules = [Rule(name="uniq", type="unique", field="id")]
        result = validate_batch(records, rules)
        assert result["summary"]["failed"] == 2  # both rows with "1" fail

    def test_empty_batch(self):
        result = validate_batch([], [Rule(name="t", type="not_empty", field="f")])
        assert result["summary"]["total"] == 0

    def test_batch_per_row_results(self):
        records = [{"age": 25}, {"age": 15}]
        rules = [Rule(name="age", type="min", field="age", min_value=18)]
        result = validate_batch(records, rules)
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False
        assert len(result["results"][1]["errors"]) == 1

    def test_batch_warnings(self):
        records = [{"bal": -5}, {"bal": 10}]
        rules = [Rule(name="bal", type="min", field="bal", min_value=0, severity=Severity.WARNING)]
        result = validate_batch(records, rules)
        # Warnings don't cause failure
        assert result["summary"]["passed"] == 2
        assert result["summary"]["warning_count"] == 1


class TestCrossFieldRules:
    """Tests for compare and required_if rule types."""

    def test_compare_gt_pass(self):
        rules = [Rule(name="end_after_start", type="compare", field="end",
                      compare_to="start", compare_op="gt",
                      error_message="end must be after start")]
        assert validate_record({"start": 10, "end": 20}, rules)["valid"] is True

    def test_compare_gt_fail(self):
        rules = [Rule(name="end_after_start", type="compare", field="end",
                      compare_to="start", compare_op="gt",
                      error_message="end must be after start")]
        result = validate_record({"start": 20, "end": 10}, rules)
        assert result["valid"] is False
        assert result["errors"][0]["field"] == "end"

    def test_compare_gt_equal_fails(self):
        rules = [Rule(name="r", type="compare", field="b", compare_to="a", compare_op="gt")]
        assert validate_record({"a": 5, "b": 5}, rules)["valid"] is False

    def test_compare_gte_equal_passes(self):
        rules = [Rule(name="r", type="compare", field="b", compare_to="a", compare_op="gte")]
        assert validate_record({"a": 5, "b": 5}, rules)["valid"] is True

    def test_compare_lt_pass(self):
        rules = [Rule(name="r", type="compare", field="low", compare_to="high", compare_op="lt")]
        assert validate_record({"low": 1, "high": 10}, rules)["valid"] is True

    def test_compare_lt_fail(self):
        rules = [Rule(name="r", type="compare", field="low", compare_to="high", compare_op="lt")]
        assert validate_record({"low": 10, "high": 1}, rules)["valid"] is False

    def test_compare_iso_datetime_gt_pass(self):
        rules = [Rule(name="end_after_start", type="compare", field="impression_end",
                      compare_to="impression_start", compare_op="gt")]
        record = {
            "impression_start": "2024-06-01T09:00:00Z",
            "impression_end": "2024-06-01T09:30:00Z",
        }
        assert validate_record(record, rules)["valid"] is True

    def test_compare_iso_datetime_gt_fail(self):
        rules = [Rule(name="end_after_start", type="compare", field="impression_end",
                      compare_to="impression_start", compare_op="gt")]
        record = {
            "impression_start": "2024-06-01T10:00:00Z",
            "impression_end": "2024-06-01T09:00:00Z",
        }
        assert validate_record(record, rules)["valid"] is False

    def test_compare_missing_field_fails(self):
        rules = [Rule(name="r", type="compare", field="end", compare_to="start", compare_op="gt")]
        assert validate_record({"end": 10}, rules)["valid"] is False

    def test_compare_null_field_fails(self):
        rules = [Rule(name="r", type="compare", field="end", compare_to="start", compare_op="gt")]
        assert validate_record({"start": 5, "end": None}, rules)["valid"] is False

    def test_required_if_triggers(self):
        rules = [Rule(name="refresh_required", type="required_if", field="refresh_rate_hz",
                      required_if={"field": "panel_type", "value": "DIGITAL"},
                      error_message="refresh_rate_hz required for DIGITAL panels")]
        # DIGITAL with missing refresh_rate_hz → fails
        result = validate_record({"panel_type": "DIGITAL"}, rules)
        assert result["valid"] is False
        assert result["errors"][0]["field"] == "refresh_rate_hz"

    def test_required_if_not_triggered(self):
        rules = [Rule(name="refresh_required", type="required_if", field="refresh_rate_hz",
                      required_if={"field": "panel_type", "value": "DIGITAL"})]
        # CLASSIC panel — refresh_rate_hz not required
        assert validate_record({"panel_type": "CLASSIC"}, rules)["valid"] is True

    def test_required_if_satisfied(self):
        rules = [Rule(name="refresh_required", type="required_if", field="refresh_rate_hz",
                      required_if={"field": "panel_type", "value": "DIGITAL"})]
        # DIGITAL panel with refresh_rate_hz present → passes
        assert validate_record({"panel_type": "DIGITAL", "refresh_rate_hz": 60}, rules)["valid"] is True

    def test_required_if_empty_string_fails(self):
        rules = [Rule(name="r", type="required_if", field="code",
                      required_if={"field": "type", "value": "X"})]
        assert validate_record({"type": "X", "code": ""}, rules)["valid"] is False

    def test_compare_batch_gt_pass(self):
        records = [
            {"start": 10, "end": 20},
            {"start": 5, "end": 15},
        ]
        rules = [Rule(name="r", type="compare", field="end", compare_to="start", compare_op="gt")]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 2

    def test_compare_batch_gt_fail(self):
        records = [
            {"start": 10, "end": 20},   # pass
            {"start": 20, "end": 10},   # fail
        ]
        rules = [Rule(name="r", type="compare", field="end", compare_to="start", compare_op="gt")]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1

    def test_required_if_batch(self):
        records = [
            {"panel_type": "DIGITAL", "refresh_rate_hz": 60},  # pass
            {"panel_type": "DIGITAL"},                           # fail
            {"panel_type": "CLASSIC"},                           # pass (not triggered)
        ]
        rules = [Rule(name="r", type="required_if", field="refresh_rate_hz",
                      required_if={"field": "panel_type", "value": "DIGITAL"})]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 1


class TestConditionBlock:
    """Tests for the condition block — conditional application of any rule type."""

    def test_condition_not_value_skips_rule(self):
        # Revenue floor skipped for CREDIT records
        rules = [Rule(name="rev_floor", type="min", field="revenue_gbp", min_value=0,
                      condition={"field": "transaction_type", "not_value": "CREDIT"},
                      error_message="revenue must be >= 0")]
        # CREDIT with negative revenue → rule skipped → passes
        assert validate_record({"transaction_type": "CREDIT", "revenue_gbp": -500}, rules)["valid"] is True

    def test_condition_not_value_applies_rule(self):
        rules = [Rule(name="rev_floor", type="min", field="revenue_gbp", min_value=0,
                      condition={"field": "transaction_type", "not_value": "CREDIT"})]
        # CHARGE with negative revenue → rule applies → fails
        assert validate_record({"transaction_type": "CHARGE", "revenue_gbp": -500}, rules)["valid"] is False

    def test_condition_value_applies_rule(self):
        # Rule only for EU region
        rules = [Rule(name="eu_only", type="not_empty", field="gdpr_consent",
                      condition={"field": "region", "value": "EU"})]
        assert validate_record({"region": "EU", "gdpr_consent": ""}, rules)["valid"] is False

    def test_condition_value_skips_rule(self):
        rules = [Rule(name="eu_only", type="not_empty", field="gdpr_consent",
                      condition={"field": "region", "value": "EU"})]
        # US region — rule skipped
        assert validate_record({"region": "US", "gdpr_consent": ""}, rules)["valid"] is True

    def test_condition_missing_trigger_field_skips(self):
        # No condition field in record — condition treated as not met for value match
        rules = [Rule(name="r", type="not_empty", field="code",
                      condition={"field": "type", "value": "X"})]
        # type is missing → condition not met → rule skipped
        assert validate_record({"code": ""}, rules)["valid"] is True

    def test_condition_not_value_missing_trigger_applies(self):
        # not_value: missing field → actual="" != "CREDIT" → condition met → rule applies
        rules = [Rule(name="r", type="min", field="amount", min_value=0,
                      condition={"field": "type", "not_value": "CREDIT"})]
        # type missing → "" != "CREDIT" → rule applies
        assert validate_record({"amount": -5}, rules)["valid"] is False

    def test_condition_batch_not_value(self):
        records = [
            {"type": "CHARGE", "amount": -10},    # rule applies → fail
            {"type": "CREDIT", "amount": -10},    # rule skipped → pass
            {"type": "CHARGE", "amount": 100},    # rule applies → pass
        ]
        rules = [Rule(name="r", type="min", field="amount", min_value=0,
                      condition={"field": "type", "not_value": "CREDIT"})]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 1

    def test_condition_batch_value(self):
        records = [
            {"region": "EU", "consent": ""},   # applies → fail
            {"region": "US", "consent": ""},   # skipped → pass
        ]
        rules = [Rule(name="r", type="not_empty", field="consent",
                      condition={"field": "region", "value": "EU"})]
        result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1


class TestCompareOpAliases:
    """Tests for compare_op symbol normalisation at parse time."""

    @pytest.mark.parametrize("symbol,expected", [
        (">",  "gt"),
        ("<",  "lt"),
        (">=", "gte"),
        ("<=", "lte"),
        ("=",  "eq"),
        ("!=", "neq"),
    ])
    def test_symbol_aliases(self, symbol, expected):
        rule = Rule(name="r", type="compare", field="b", compare_to="a", compare_op=symbol)
        assert rule.compare_op == expected

    def test_symbol_gt_validates(self):
        # Symbol form should work end-to-end in validation
        rules = [Rule(name="r", type="compare", field="end", compare_to="start", compare_op=">")]
        assert validate_record({"start": 5, "end": 10}, rules)["valid"] is True
        assert validate_record({"start": 10, "end": 5}, rules)["valid"] is False

    def test_word_form_unchanged(self):
        rule = Rule(name="r", type="compare", field="b", compare_to="a", compare_op="gt")
        assert rule.compare_op == "gt"


class TestBatchSummaryRuleFailureCounts:
    """Tests for rule_failure_counts in batch summary."""

    def test_rule_failure_counts_populated(self):
        records = [
            {"email": "bad", "age": 15},
            {"email": "good@test.com", "age": 15},
            {"email": "bad2", "age": 25},
        ]
        rules = [
            Rule(name="email_check", type="regex", field="email", pattern="^.+@.+$"),
            Rule(name="age_check", type="min", field="age", min_value=18),
        ]
        result = validate_batch(records, rules)
        counts = result["summary"]["rule_failure_counts"]
        assert counts["email_check"] == 2   # 2 bad emails
        assert counts["age_check"] == 2     # 2 underage

    def test_rule_failure_counts_empty_when_all_pass(self):
        records = [{"age": 25}, {"age": 30}]
        rules = [Rule(name="age_check", type="min", field="age", min_value=18)]
        result = validate_batch(records, rules)
        assert result["summary"]["rule_failure_counts"] == {}

    def test_rule_failure_counts_includes_warnings(self):
        records = [{"balance": -5}, {"balance": 10}]
        rules = [Rule(name="bal", type="min", field="balance", min_value=0, severity=Severity.WARNING)]
        result = validate_batch(records, rules)
        counts = result["summary"]["rule_failure_counts"]
        assert counts.get("bal") == 1


class TestBatchResponseKeyStructure:
    """ACT-048-07: validate_batch response must use 'results' key, not per_row_errors/row_errors."""

    def test_batch_response_key_structure(self):
        records = [
            {"email": "good@test.com"},
            {"email": "bad"},
            {"email": "also@good.com"},
        ]
        rules = [Rule(name="email_check", type="regex", field="email", pattern="^.+@.+$")]
        result = validate_batch(records, rules)

        # Top-level keys
        assert "summary" in result
        assert "results" in result
        assert "per_row_errors" not in result
        assert "row_errors" not in result

        # Each item in results has the expected keys
        for item in result["results"]:
            assert "index" in item
            assert "valid" in item
            assert "errors" in item
            assert "warnings" in item


class TestLookupRules:
    """Tests for file-based lookup rule type."""

    def test_lookup_pass(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("LGM-UK-00001\nLGM-UK-00002\nLGM-UK-00003\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file=str(f))]
            assert validate_record({"panel_id": "LGM-UK-00001"}, rules)["valid"] is True
        _load_lookup_set.cache_clear()

    def test_lookup_fail(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("LGM-UK-00001\nLGM-UK-00002\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file=str(f))]
            result = validate_record({"panel_id": "LGM-UK-99999"}, rules)
        assert result["valid"] is False
        assert result["errors"][0]["field"] == "panel_id"
        _load_lookup_set.cache_clear()

    def test_lookup_csv_pass(self, tmp_path):
        f = tmp_path / "panels.csv"
        f.write_text("panel_id,status\nLGM-UK-00001,active\nLGM-UK-00002,active\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file=str(f), lookup_field="panel_id")]
            assert validate_record({"panel_id": "LGM-UK-00001"}, rules)["valid"] is True
        _load_lookup_set.cache_clear()

    def test_lookup_csv_fail(self, tmp_path):
        f = tmp_path / "panels.csv"
        f.write_text("panel_id,status\nLGM-UK-00001,active\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file=str(f), lookup_field="panel_id")]
            assert validate_record({"panel_id": "LGM-UK-99999"}, rules)["valid"] is False
        _load_lookup_set.cache_clear()

    def test_lookup_missing_file_fails(self, tmp_path):
        # A path outside the contracts directory is rejected as a path traversal attempt,
        # which also results in valid=False (rule fails closed).
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file="/nonexistent/file.txt")]
            result = validate_record({"panel_id": "anything"}, rules)
        assert result["valid"] is False
        _load_lookup_set.cache_clear()

    def test_lookup_null_value_passes(self, tmp_path):
        # Lookup is optional-by-default: a missing/null field passes.
        # Use a not_empty rule alongside lookup to require the field to be present.
        f = tmp_path / "ids.txt"
        f.write_text("LGM-UK-00001\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            rules = [Rule(name="panel_check", type="lookup", field="panel_id",
                          lookup_file=str(f))]
            assert validate_record({"panel_id": None}, rules)["valid"] is True
        _load_lookup_set.cache_clear()

    def test_lookup_batch(self, tmp_path):
        f = tmp_path / "ids.txt"
        f.write_text("AAA\nBBB\nCCC\n")
        from opendqv.core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        with patch("opendqv.config.CONTRACTS_DIR", tmp_path):
            records = [{"code": "AAA"}, {"code": "BBB"}, {"code": "ZZZ"}]
            rules = [Rule(name="r", type="lookup", field="code", lookup_file=str(f))]
            result = validate_batch(records, rules)
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 1
        _load_lookup_set.cache_clear()


class TestHttpLookupRules:
    """Tests for REST-based lookup rule type (HTTP endpoint)."""

    def _make_mock_urlopen(self, body: str, content_type: str = "application/json"):
        """Return a context manager mock for urllib.request.urlopen."""
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": content_type}
        mock_resp.read.return_value = body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return patch("urllib.request.urlopen", return_value=mock_resp)

    def setup_method(self):
        from opendqv.core.validator import _http_lookup_cache
        _http_lookup_cache.clear()

    def test_http_lookup_json_array_pass(self):
        import json
        from opendqv.core.validator import validate_record
        body = json.dumps(["PANEL_001", "PANEL_002", "PANEL_003"])
        with self._make_mock_urlopen(body):
            rules = [Rule(name="r", type="lookup", field="panel_id",
                          lookup_file="https://example.com/panels", cache_ttl=60)]
            result = validate_record({"panel_id": "PANEL_001"}, rules)
        assert result["valid"] is True

    def test_http_lookup_json_array_fail(self):
        import json
        from opendqv.core.validator import validate_record
        body = json.dumps(["PANEL_001", "PANEL_002"])
        with self._make_mock_urlopen(body):
            rules = [Rule(name="r", type="lookup", field="panel_id",
                          lookup_file="https://example.com/panels", cache_ttl=60)]
            result = validate_record({"panel_id": "PANEL_999"}, rules)
        assert result["valid"] is False

    def test_http_lookup_plain_text_pass(self):
        from opendqv.core.validator import validate_record
        body = "PANEL_001\nPANEL_002\nPANEL_003\n"
        with self._make_mock_urlopen(body, content_type="text/plain"):
            rules = [Rule(name="r", type="lookup", field="panel_id",
                          lookup_file="https://example.com/panels.txt", cache_ttl=60)]
            result = validate_record({"panel_id": "PANEL_002"}, rules)
        assert result["valid"] is True

    def test_http_lookup_is_cached(self):
        import json
        from unittest.mock import patch, MagicMock
        from opendqv.core.validator import _load_http_lookup_set, _http_lookup_cache
        _http_lookup_cache.clear()

        body = json.dumps(["A", "B"])

        call_count = 0

        def mock_urlopen(req, timeout=10):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.read.return_value = body.encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            _load_http_lookup_set("https://example.com/items", "", 300)
            _load_http_lookup_set("https://example.com/items", "", 300)

        assert call_count == 1, "Second call should have used cache, not fetched again"

    def test_http_lookup_network_error_fails_record(self):
        import urllib.error
        from unittest.mock import patch
        from opendqv.core.validator import validate_record
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            rules = [Rule(name="r", type="lookup", field="panel_id",
                          lookup_file="https://example.com/panels", cache_ttl=60)]
            result = validate_record({"panel_id": "PANEL_001"}, rules)
        assert result["valid"] is False

    def test_http_lookup_default_ttl_used_when_not_set(self):
        import json
        import time
        from unittest.mock import patch, MagicMock
        from opendqv.core.validator import _load_http_lookup_set, _http_lookup_cache, _HTTP_LOOKUP_DEFAULT_TTL
        _http_lookup_cache.clear()

        body = json.dumps(["X"])
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _load_http_lookup_set("https://example.com/x", "", _HTTP_LOOKUP_DEFAULT_TTL)

        key = ("https://example.com/x", "", _HTTP_LOOKUP_DEFAULT_TTL, None)
        assert key in _http_lookup_cache
        _, expires_at = _http_lookup_cache[key]
        assert expires_at > time.monotonic() + _HTTP_LOOKUP_DEFAULT_TTL - 2


class TestInheritanceInvariant:
    """Tests for check_inheritance_invariant — the OSS foundation of the federation contract enforcement."""

    def _make_inherited_rule(self, **overrides):
        from opendqv.core.rule_parser import Rule, Severity
        defaults = dict(
            name="min_age_check",
            field="age",
            type="min",
            min_value=18.0,
            severity=Severity.ERROR,
            severity_floor=Severity.ERROR,
            inherited=True,
            provenance={"authority_node": "global", "lsn": 1},
            error_message="Age must be >= 18",
        )
        defaults.update(overrides)
        return Rule(**defaults)

    def test_non_inherited_rule_always_passes(self):
        from opendqv.core.contracts import check_inheritance_invariant
        from opendqv.core.rule_parser import Rule, Severity
        base = Rule(name="r", field="f", type="min", min_value=10.0,
                    severity=Severity.ERROR, inherited=False)
        proposed = Rule(name="r", field="f", type="min", min_value=5.0,
                        severity=Severity.WARNING, inherited=False)
        assert check_inheritance_invariant(base, proposed) == []

    def test_tightening_is_allowed(self):
        from opendqv.core.contracts import check_inheritance_invariant
        base = self._make_inherited_rule()
        proposed = self._make_inherited_rule(min_value=21.0, error_message="Age must be >= 21")
        assert check_inheritance_invariant(base, proposed) == []

    def test_severity_downgrade_rejected(self):
        from opendqv.core.contracts import check_inheritance_invariant
        from opendqv.core.rule_parser import Severity
        base = self._make_inherited_rule()
        proposed = self._make_inherited_rule(severity=Severity.WARNING)
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 1
        assert "severity" in violations[0]
        assert "global" in violations[0]

    def test_min_lowering_rejected(self):
        from opendqv.core.contracts import check_inheritance_invariant
        base = self._make_inherited_rule()
        proposed = self._make_inherited_rule(min_value=10.0)
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 1
        assert "min" in violations[0]

    def test_max_raising_rejected(self):
        from opendqv.core.contracts import check_inheritance_invariant
        from opendqv.core.rule_parser import Rule, Severity
        base = Rule(name="r", field="score", type="max", max_value=100.0,
                    severity=Severity.ERROR, severity_floor=Severity.ERROR,
                    inherited=True, provenance={"authority_node": "global", "lsn": 1},
                    error_message="Score <= 100")
        proposed = Rule(name="r", field="score", type="max", max_value=150.0,
                        severity=Severity.ERROR, error_message="Score <= 150")
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 1
        assert "max" in violations[0]

    def test_pattern_alteration_rejected(self):
        from opendqv.core.contracts import check_inheritance_invariant
        from opendqv.core.rule_parser import Rule, Severity
        base = Rule(name="r", field="email", type="regex",
                    pattern=r"^[^@]+@[^@]+\.[^@]+$",
                    severity=Severity.ERROR, severity_floor=Severity.ERROR,
                    inherited=True, provenance={"authority_node": "global", "lsn": 1},
                    error_message="Invalid email")
        proposed = Rule(name="r", field="email", type="regex",
                        pattern=r".*",
                        severity=Severity.ERROR, error_message="Any value ok")
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 1
        assert "pattern" in violations[0]

    def test_type_change_rejected(self):
        from opendqv.core.contracts import check_inheritance_invariant
        base = self._make_inherited_rule(type="min")
        proposed = self._make_inherited_rule(type="range")
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 1
        assert "type" in violations[0]

    def test_multiple_violations_returned(self):
        from opendqv.core.contracts import check_inheritance_invariant
        from opendqv.core.rule_parser import Severity
        base = self._make_inherited_rule(min_value=18.0)
        proposed = self._make_inherited_rule(
            min_value=10.0,
            severity=Severity.WARNING,
            type="range",
        )
        violations = check_inheritance_invariant(base, proposed)
        assert len(violations) == 3  # severity, min, type


# ---------------------------------------------------------------------------
# Code generator — header content tests
# ---------------------------------------------------------------------------

from opendqv.core.code_generator import generate_code  # noqa: E402


class TestCodeGeneratorHeaders:
    """Verify that generated code includes the correct header when a contract name is supplied."""

    _rules = [{"name": "id_required", "type": "not_empty", "field": "id"}]

    def test_snowflake_header_present(self):
        code = generate_code(self._rules, "snowflake", contract_name="my_contract", contract_version="2.0")
        assert "-- Generated by OpenDQV" in code
        assert "my_contract" in code
        assert "v2.0" in code
        assert "T" in code and "Z" in code  # ISO 8601 UTC timestamp
        assert "opendqv generate my_contract snowflake" in code

    def test_salesforce_header_present(self):
        code = generate_code(self._rules, "salesforce", contract_name="sf_contact", contract_version="1.0")
        assert "// Generated by OpenDQV" in code
        assert "sf_contact" in code
        assert "v1.0" in code
        assert "opendqv generate sf_contact salesforce" in code

    def test_js_header_present(self):
        code = generate_code(self._rules, "js", contract_name="events", contract_version="3.1")
        assert "// Generated by OpenDQV" in code
        assert "events" in code
        assert "v3.1" in code
        assert "opendqv generate events js" in code

    def test_no_header_when_contract_name_empty(self):
        """Existing callers that pass no contract_name must not get a header — backward compat."""
        for target in ("snowflake", "salesforce", "js"):
            code = generate_code(self._rules, target)
            assert "Generated by OpenDQV" not in code, f"Unexpected header in {target} output"

    def test_header_precedes_function_body_snowflake(self):
        code = generate_code(self._rules, "snowflake", contract_name="customer", contract_version="1.0")
        header_pos = code.find("Generated by OpenDQV")
        function_pos = code.find("CREATE OR REPLACE FUNCTION")
        assert header_pos != -1 and function_pos != -1
        assert header_pos < function_pos


import pytest as _pytest


class TestCodeGeneratorRuleCoverage:
    """Every rule type must produce non-empty output (a check or an explicit comment).
    Silent drops are correctness bugs — a user deploying generated code must know
    which rules are enforced and which are not.
    """

    # All rule types the engine supports
    _ALL_RULE_TYPES = [
        ("regex",              {"pattern": "^\\d+$"}),
        ("min",                {"min_value": 0}),
        ("max",                {"max_value": 100}),
        ("range",              {"min_value": 0, "max_value": 100}),
        ("not_empty",          {}),
        ("min_length",         {"min_length": 2}),
        ("max_length",         {"max_length": 50}),
        ("date_format",        {}),
        ("unique",             {}),
        ("required_if",        {"required_if": {"field": "other", "value": "yes"}}),
        ("lookup",             {"lookup_file": "ref/test.txt"}),
        ("compare",            {"compare_to": "other_field", "compare_op": "gt"}),
        ("date_diff",          {"date_diff_field": "other_date", "date_diff_unit": "days"}),
        ("checksum",           {"checksum_algorithm": "luhn"}),
        ("cross_field_range",  {"cross_field_min": "low", "cross_field_max": "high"}),
        ("field_sum",          {"field_sum_fields": ["a", "b"]}),
        ("forbidden_if",       {"forbidden_if": {"field": "other", "value": "no"}}),
        ("conditional_value",  {"conditional_value": "yes"}),
        ("ratio_check",        {"ratio_numerator": "a", "ratio_denominator": "b"}),
    ]

    @_pytest.mark.parametrize("target", ["snowflake", "salesforce", "js"])
    @_pytest.mark.parametrize("rule_type,extra", _ALL_RULE_TYPES)
    def test_no_silent_drop(self, target, rule_type, extra):
        """Every rule type must produce at least one line of output — never empty string."""
        rule = {"name": f"test_{rule_type}", "type": rule_type, "field": "value",
                "severity": "error", "error_message": "test error", **extra}
        code = generate_code([rule], target)
        # Extract just the rule body (strip header/wrapper boilerplate)
        # Any non-empty line referencing the field or containing a comment counts
        lines = [ln for ln in code.splitlines()
                 if "value" in ln or "// " in ln or "//NOTE" in ln]
        assert len(lines) >= 1, (
            f"Rule type '{rule_type}' produces no output for target '{target}'. "
            f"Silent drops are correctness bugs — add a // TODO or // NOTE comment."
        )

    def test_salesforce_has_max(self):
        """Salesforce target must implement the max rule (was missing — latent bug)."""
        rule = {"name": "age_max", "type": "max", "field": "age",
                "max_value": 150, "severity": "warning", "error_message": "Age too high"}
        code = generate_code([rule], "salesforce")
        assert "> 150" in code, "Salesforce max rule not generating comparison"
        assert "age" in code
