"""
Targeted tests for validator rule types not covered by existing tests.

Covers: field_sum, forbidden_if, conditional_value, date_diff,
        checksum algorithms (lei_mod97, nhs_mod11, cpf_mod11, vin_mod11),
        profiler file-upload endpoint (save=True).
"""
import io
from core.rule_parser import Rule
from core.validator import validate_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(**kwargs):
    defaults = dict(
        name="r", field="value", severity="error",
        error_message="failed", type="not_empty",
    )
    defaults.update(kwargs)
    return Rule(**defaults)


def _validate(record, **rule_kwargs):
    rule = _rule(**rule_kwargs)
    result = validate_record(record, [rule], contract_name="test")
    return result


# ---------------------------------------------------------------------------
# field_sum rule
# ---------------------------------------------------------------------------

class TestFieldSum:

    def test_sum_equals_passes(self):
        result = _validate(
            {"value": 0, "a": 3.0, "b": 7.0},
            type="field_sum", name="s",
            sum_fields=["a", "b"], sum_equals=10.0,
        )
        assert result["valid"] is True

    def test_sum_mismatch_fails(self):
        result = _validate(
            {"value": 0, "a": 3.0, "b": 3.0},
            type="field_sum", name="s",
            sum_fields=["a", "b"], sum_equals=10.0,
        )
        assert result["valid"] is False

    def test_sum_within_tolerance_passes(self):
        result = _validate(
            {"value": 0, "a": 3.0, "b": 7.01},
            type="field_sum", name="s",
            sum_fields=["a", "b"], sum_equals=10.0, sum_tolerance=0.05,
        )
        assert result["valid"] is True

    def test_missing_sum_fields_config_passes(self):
        """field_sum with no sum_fields configured is a no-op (warns and passes)."""
        result = _validate(
            {"value": 1},
            type="field_sum", name="s",
        )
        assert result["valid"] is True

    def test_non_numeric_field_fails(self):
        result = _validate(
            {"value": 0, "a": "bad", "b": 5.0},
            type="field_sum", name="s",
            sum_fields=["a", "b"], sum_equals=5.0,
        )
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# forbidden_if rule
# ---------------------------------------------------------------------------

class TestForbiddenIf:

    def test_condition_met_field_absent_passes(self):
        result = _validate(
            {"trigger": "no"},
            type="forbidden_if", name="fi",
            field="value",
            forbidden_if={"field": "trigger", "value": "no"},
        )
        assert result["valid"] is True

    def test_condition_met_field_present_fails(self):
        result = _validate(
            {"value": "something", "trigger": "no"},
            type="forbidden_if", name="fi",
            field="value",
            forbidden_if={"field": "trigger", "value": "no"},
        )
        assert result["valid"] is False

    def test_condition_not_met_field_present_passes(self):
        result = _validate(
            {"value": "something", "trigger": "yes"},
            type="forbidden_if", name="fi",
            field="value",
            forbidden_if={"field": "trigger", "value": "no"},
        )
        assert result["valid"] is True

    def test_no_forbidden_if_config_passes(self):
        result = _validate({"value": "x"}, type="forbidden_if", name="fi")
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# conditional_value rule
# ---------------------------------------------------------------------------

class TestConditionalValue:

    def test_value_matches_must_equal_passes(self):
        result = _validate(
            {"value": "approved"},
            type="conditional_value", name="cv", must_equal="approved",
        )
        assert result["valid"] is True

    def test_value_mismatch_fails(self):
        result = _validate(
            {"value": "rejected"},
            type="conditional_value", name="cv", must_equal="approved",
        )
        assert result["valid"] is False

    def test_no_must_equal_passes(self):
        result = _validate(
            {"value": "anything"},
            type="conditional_value", name="cv",
        )
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# date_diff rule
# ---------------------------------------------------------------------------

class TestDateDiff:

    def test_date_diff_days_passes(self):
        result = _validate(
            {"value": "2024-01-10", "other_date": "2024-01-01"},
            type="date_diff", name="dd",
            field="value",
            date_diff_field="other_date",
            date_diff_unit="days",
            min_value=5.0,
            max_value=30.0,
        )
        assert result["valid"] is True

    def test_date_diff_too_small_fails(self):
        result = _validate(
            {"value": "2024-01-02", "other_date": "2024-01-01"},
            type="date_diff", name="dd",
            field="value",
            date_diff_field="other_date",
            date_diff_unit="days",
            min_value=5.0,
        )
        assert result["valid"] is False

    def test_date_diff_missing_date_diff_field_config_passes(self):
        """date_diff rule missing date_diff_field config is a no-op."""
        result = _validate(
            {"value": "2024-01-01"},
            type="date_diff", name="dd",
        )
        assert result["valid"] is True

    def test_date_diff_field_absent_passes(self):
        """Field absent — date_diff skips (required check is separate)."""
        result = _validate(
            {"other_date": "2024-01-01"},
            type="date_diff", name="dd",
            field="value",
            date_diff_field="other_date",
        )
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# Checksum algorithms — lei_mod97, nhs_mod11, cpf_mod11, vin_mod11
# ---------------------------------------------------------------------------

class TestChecksumAlgorithms:

    def _checksum_rule(self, algorithm):
        return dict(type="checksum", name="cs", checksum_algorithm=algorithm)

    def test_lei_valid(self):
        # Fabricated valid LEI (20 char, mod-97 == 1)
        # Use a known-valid LEI format
        result = _validate(
            {"value": "529900T8BM49AURSDO55"},
            **self._checksum_rule("lei_mod97"),
        )
        assert result["valid"] is True

    def test_lei_invalid(self):
        result = _validate(
            {"value": "00000000000000000000"},
            **self._checksum_rule("lei_mod97"),
        )
        assert result["valid"] is False

    def test_lei_wrong_length_fails(self):
        result = _validate(
            {"value": "TOOSHORT"},
            **self._checksum_rule("lei_mod97"),
        )
        assert result["valid"] is False

    def test_nhs_valid(self):
        # NHS number: 4010232137 is a known test number
        result = _validate(
            {"value": "4010232137"},
            **self._checksum_rule("nhs_mod11"),
        )
        assert result["valid"] is True

    def test_nhs_invalid(self):
        result = _validate(
            {"value": "1234567890"},
            **self._checksum_rule("nhs_mod11"),
        )
        assert result["valid"] is False

    def test_nhs_wrong_length_fails(self):
        result = _validate(
            {"value": "12345"},
            **self._checksum_rule("nhs_mod11"),
        )
        assert result["valid"] is False

    def test_cpf_valid(self):
        # Brazilian CPF: 529.982.247-25 is a known valid test CPF
        result = _validate(
            {"value": "52998224725"},
            **self._checksum_rule("cpf_mod11"),
        )
        assert result["valid"] is True

    def test_cpf_invalid(self):
        result = _validate(
            {"value": "12345678901"},
            **self._checksum_rule("cpf_mod11"),
        )
        assert result["valid"] is False

    def test_cpf_all_same_digits_fails(self):
        result = _validate(
            {"value": "11111111111"},
            **self._checksum_rule("cpf_mod11"),
        )
        assert result["valid"] is False

    def test_vin_valid(self):
        # Known valid VIN
        result = _validate(
            {"value": "1HGBH41JXMN109186"},
            **self._checksum_rule("vin_mod11"),
        )
        assert result["valid"] is True

    def test_vin_invalid_length_fails(self):
        result = _validate(
            {"value": "TOOSHORT"},
            **self._checksum_rule("vin_mod11"),
        )
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# Profiler file upload endpoint — save=True branch (routes_profiler.py:66-74)
# ---------------------------------------------------------------------------

class TestProfilerFileUpload:

    def test_profile_csv_file_no_save(self, client, auth_headers):
        csv_content = b"name,age,city\nAlice,30,London\nBob,25,Manchester\n"
        resp = client.post(
            "/api/v1/profile/file?contract_name=test_file_profile",
            files={"file": ("data.csv", io.BytesIO(csv_content), "text/csv")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "contract" in body
        assert "profile" in body
        assert body["rows"] == 2

    def test_profile_csv_file_save_true(self, client, editor_headers):
        csv_content = b"sensor_id,temperature,status\ns001,22.5,ok\ns002,24.1,ok\n"
        resp = client.post(
            "/api/v1/profile/file?contract_name=test_file_saved_prof&save=true",
            files={"file": ("sensors.csv", io.BytesIO(csv_content), "text/csv")},
            headers=editor_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "saved_to" in body
        assert "test_file_saved_prof" in body["message"]
        assert body["filename"] == "sensors.csv"

    def test_profile_file_requires_auth(self, client):
        csv_content = b"a,b\n1,2\n"
        resp = client.post(
            "/api/v1/profile/file?contract_name=unauth_test",
            files={"file": ("data.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Additional checksum algorithms — mod10_gs1, iban_mod97, isin_mod11, isrc_luhn
# ---------------------------------------------------------------------------

class TestMoreChecksumAlgorithms:

    def _cs(self, algorithm):
        return dict(type="checksum", name="cs", checksum_algorithm=algorithm)

    # GS1 Mod-10 (GTIN-13)
    def test_mod10_gs1_valid_gtin13(self):
        result = _validate({"value": "5901234123457"}, **self._cs("mod10_gs1"))
        assert result["valid"] is True

    def test_mod10_gs1_invalid(self):
        result = _validate({"value": "5901234123456"}, **self._cs("mod10_gs1"))
        assert result["valid"] is False

    def test_mod10_gs1_non_numeric_fails(self):
        result = _validate({"value": "ABC123"}, **self._cs("mod10_gs1"))
        assert result["valid"] is False

    # IBAN mod-97
    def test_iban_valid(self):
        result = _validate({"value": "GB82WEST12345698765432"}, **self._cs("iban_mod97"))
        assert result["valid"] is True

    def test_iban_invalid(self):
        result = _validate({"value": "GB82WEST12345698765433"}, **self._cs("iban_mod97"))
        assert result["valid"] is False

    def test_iban_too_short_fails(self):
        result = _validate({"value": "GB"}, **self._cs("iban_mod97"))
        assert result["valid"] is False

    # ISIN mod-11
    def test_isin_valid(self):
        result = _validate({"value": "US0231351067"}, **self._cs("isin_mod11"))
        assert result["valid"] is True

    def test_isin_invalid(self):
        result = _validate({"value": "US0231351068"}, **self._cs("isin_mod11"))
        assert result["valid"] is False

    def test_isin_wrong_length_fails(self):
        result = _validate({"value": "US123"}, **self._cs("isin_mod11"))
        assert result["valid"] is False

    # ISRC (format-based)
    def test_isrc_valid(self):
        result = _validate({"value": "GBAYE0601498"}, **self._cs("isrc_luhn"))
        assert result["valid"] is True

    def test_isrc_invalid_format(self):
        result = _validate({"value": "INVALID"}, **self._cs("isrc_luhn"))
        assert result["valid"] is False

    # Unknown algorithm — passes through with warning
    def test_unknown_algorithm_passes(self):
        result = _validate({"value": "anything"}, **self._cs("__unknown_algo__"))
        assert result["valid"] is True


# ---------------------------------------------------------------------------
# compare rule edge cases
# ---------------------------------------------------------------------------

class TestCompareRuleEdgeCases:

    def test_compare_today_gte_past_date_passes(self):
        result = _validate(
            {"value": "2000-01-01"},
            type="compare", name="c",
            compare_to="today", compare_op="lte",
        )
        assert result["valid"] is True

    def test_compare_missing_config_passes(self):
        """compare rule with no compare_to is a no-op."""
        result = _validate(
            {"value": "x"},
            type="compare", name="c",
        )
        assert result["valid"] is True

    def test_compare_null_value_fails(self):
        result = _validate(
            {"other": "10"},
            type="compare", name="c",
            field="value",
            compare_to="other", compare_op="gt",
        )
        assert result["valid"] is False

    def test_compare_string_values(self):
        result = _validate(
            {"value": "b", "other": "a"},
            type="compare", name="c",
            compare_to="other", compare_op="gt",
        )
        assert result["valid"] is True

    def test_compare_missing_other_field_fails(self):
        """compare_to field absent → error."""
        result = _validate(
            {"value": "10"},
            type="compare", name="c",
            compare_to="missing_field", compare_op="gt",
        )
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# DuckDB batch validation — exercises _batch_check_rule() paths (lines 1059-1395)
# ---------------------------------------------------------------------------

from core.validator import validate_batch  # noqa: E402


def _batch(records, **rule_kwargs):
    rule = _rule(**rule_kwargs)
    return validate_batch(records, [rule], contract_name="batch_test")


class TestBatchValidation:
    """validate_batch() + _batch_check_rule() — all rule type DuckDB paths."""

    def test_empty_records(self):
        result = validate_batch([], [], contract_name="test")
        assert result["summary"]["total"] == 0
        assert result["results"] == []

    def test_min_passes(self):
        result = _batch([{"value": 25}, {"value": 30}], type="min", min_value=18)
        assert result["summary"]["failed"] == 0

    def test_min_fails(self):
        result = _batch([{"value": 5}, {"value": 30}], type="min", min_value=18)
        assert result["summary"]["failed"] == 1

    def test_max_passes(self):
        result = _batch([{"value": 50}, {"value": 99}], type="max", max_value=100)
        assert result["summary"]["failed"] == 0

    def test_max_fails(self):
        result = _batch([{"value": 200}, {"value": 50}], type="max", max_value=100)
        assert result["summary"]["failed"] == 1

    def test_range_passes(self):
        result = _batch([{"value": 5}, {"value": 10}], type="range", min_value=1, max_value=20)
        assert result["summary"]["failed"] == 0

    def test_range_fails(self):
        result = _batch([{"value": 0}, {"value": 25}], type="range", min_value=1, max_value=20)
        assert result["summary"]["failed"] == 2

    def test_not_empty_passes(self):
        result = _batch([{"value": "hello"}, {"value": "world"}], type="not_empty")
        assert result["summary"]["failed"] == 0

    def test_not_empty_fails(self):
        result = _batch([{"value": ""}, {"value": None}], type="not_empty")
        assert result["summary"]["failed"] == 2

    def test_min_length_passes(self):
        result = _batch([{"value": "hello"}], type="min_length", min_length=3)
        assert result["summary"]["failed"] == 0

    def test_min_length_fails(self):
        result = _batch([{"value": "hi"}, {"value": "a"}], type="min_length", min_length=3)
        assert result["summary"]["failed"] == 2

    def test_max_length_passes(self):
        result = _batch([{"value": "hi"}], type="max_length", max_length=5)
        assert result["summary"]["failed"] == 0

    def test_max_length_fails(self):
        result = _batch([{"value": "toolongstring"}], type="max_length", max_length=5)
        assert result["summary"]["failed"] == 1

    def test_date_format_passes(self):
        result = _batch([{"value": "2026-01-15"}], type="date_format")
        assert result["summary"]["failed"] == 0

    def test_date_format_fails(self):
        result = _batch([{"value": "not-a-date"}, {"value": None}], type="date_format")
        assert result["summary"]["failed"] == 2

    def test_unique_global_passes(self):
        result = _batch([{"value": "a"}, {"value": "b"}, {"value": "c"}], type="unique")
        assert result["summary"]["failed"] == 0

    def test_unique_global_fails(self):
        result = _batch([{"value": "a"}, {"value": "a"}, {"value": "b"}], type="unique")
        assert result["summary"]["failed"] == 2

    def test_unique_with_group_by_passes(self):
        rule = _rule(type="unique", group_by=["cat"])
        records = [
            {"value": "x", "cat": "A"},
            {"value": "x", "cat": "B"},  # same value but different group — OK
        ]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_unique_with_group_by_fails(self):
        rule = _rule(type="unique", group_by=["cat"])
        records = [
            {"value": "x", "cat": "A"},
            {"value": "x", "cat": "A"},  # duplicate within same group
        ]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 2

    def test_unique_group_by_invalid_col_fallback(self):
        """group_by references a column not in the data → falls back to global unique."""
        rule = _rule(type="unique", group_by=["nonexistent_col"])
        records = [{"value": "a"}, {"value": "a"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 2

    def test_compare_batch_numeric_passes(self):
        rule = _rule(type="compare", compare_to="other", compare_op="gt")
        records = [{"value": 10, "other": 5}, {"value": 20, "other": 15}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_compare_batch_numeric_fails(self):
        rule = _rule(type="compare", compare_to="other", compare_op="gt")
        records = [{"value": 3, "other": 10}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_compare_batch_date_strings(self):
        rule = _rule(type="compare", compare_to="end_date", compare_op="lt")
        records = [{"value": "2025-01-01", "end_date": "2026-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_compare_batch_today_sentinel(self):
        rule = _rule(type="compare", compare_to="today", compare_op="lte")
        records = [{"value": "2000-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_compare_batch_null_value_fails(self):
        rule = _rule(type="compare", compare_to="other", compare_op="gt")
        records = [{"value": None, "other": 5}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_compare_batch_null_other_fails(self):
        rule = _rule(type="compare", compare_to="other", compare_op="gt")
        records = [{"value": 10, "other": None}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_compare_batch_missing_field_warns(self):
        """compare_to references a field not in data → warning, no crash."""
        rule = _rule(type="compare", compare_to="ghost_field", compare_op="gt")
        records = [{"value": 10}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["total"] == 1

    def test_required_if_batch_passes(self):
        rule = _rule(
            type="required_if",
            required_if={"field": "status", "value": "premium"},
        )
        records = [{"value": "gold@email.com", "status": "premium"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_required_if_batch_fails(self):
        rule = _rule(
            type="required_if",
            required_if={"field": "status", "value": "premium"},
        )
        records = [{"value": "", "status": "premium"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_required_if_batch_missing_trigger_warns(self):
        rule = _rule(
            type="required_if",
            required_if={"field": "ghost_trigger", "value": "x"},
        )
        records = [{"value": "something"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["total"] == 1

    def test_allowed_values_batch_passes(self):
        rule = _rule(type="allowed_values", allowed_values=["active", "inactive"])
        records = [{"value": "active"}, {"value": "inactive"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_allowed_values_batch_fails(self):
        rule = _rule(type="allowed_values", allowed_values=["active", "inactive"])
        records = [{"value": "unknown"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_checksum_batch_iban_passes(self):
        rule = _rule(type="checksum", checksum_algorithm="iban_mod97")
        records = [{"value": "GB82WEST12345698765432"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_checksum_batch_iban_fails(self):
        rule = _rule(type="checksum", checksum_algorithm="iban_mod97")
        records = [{"value": "BADIBAN"}, {"value": None}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 2

    def test_cross_field_range_batch_passes(self):
        rule = _rule(
            type="cross_field_range",
            cross_min_field="lo",
            cross_max_field="hi",
        )
        records = [{"value": 5, "lo": 1, "hi": 10}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_cross_field_range_batch_fails(self):
        rule = _rule(
            type="cross_field_range",
            cross_min_field="lo",
            cross_max_field="hi",
        )
        records = [{"value": 15, "lo": 1, "hi": 10}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_cross_field_range_batch_null_value(self):
        rule = _rule(type="cross_field_range", cross_min_field="lo", cross_max_field="hi")
        records = [{"value": None, "lo": 1, "hi": 10}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_field_sum_batch_passes(self):
        rule = _rule(
            type="field_sum",
            sum_fields=["a", "b"],
            sum_equals=10.0,
        )
        records = [{"value": 0, "a": 3.0, "b": 7.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_field_sum_batch_fails(self):
        rule = _rule(
            type="field_sum",
            sum_fields=["a", "b"],
            sum_equals=10.0,
        )
        records = [{"value": 0, "a": 1.0, "b": 2.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_forbidden_if_batch_passes(self):
        rule = _rule(
            type="forbidden_if",
            forbidden_if={"field": "status", "value": "CANCELLED"},
        )
        records = [{"value": None, "status": "CANCELLED"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_forbidden_if_batch_fails(self):
        rule = _rule(
            type="forbidden_if",
            forbidden_if={"field": "status", "value": "CANCELLED"},
        )
        records = [{"value": "some_discount", "status": "CANCELLED"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_conditional_value_batch_passes(self):
        rule = _rule(type="conditional_value", must_equal="PENDING")
        records = [{"value": "PENDING"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_conditional_value_batch_fails(self):
        rule = _rule(type="conditional_value", must_equal="PENDING")
        records = [{"value": "APPROVED"}, {"value": None}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 2

    def test_date_diff_batch_passes(self):
        rule = _rule(
            type="date_diff",
            date_diff_field="end_date",
            min_value=1.0,
        )
        records = [{"value": "2026-03-01", "end_date": "2026-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_date_diff_batch_fails(self):
        rule = _rule(
            type="date_diff",
            date_diff_field="end_date",
            min_value=100.0,
        )
        records = [{"value": "2026-01-02", "end_date": "2026-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_date_diff_batch_null_fails(self):
        rule = _rule(type="date_diff", date_diff_field="end_date", min_value=1.0)
        records = [{"value": None, "end_date": "2026-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_date_diff_batch_missing_field_warns(self):
        rule = _rule(type="date_diff", date_diff_field="ghost_field", min_value=1.0)
        records = [{"value": "2026-01-02"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["total"] == 1

    def test_ratio_check_batch_passes(self):
        rule = _rule(
            type="ratio_check",
            ratio_numerator="num",
            ratio_denominator="den",
            min_value=0.1,
            max_value=1.0,
        )
        records = [{"value": 0, "num": 5.0, "den": 10.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_ratio_check_batch_fails_too_low(self):
        rule = _rule(
            type="ratio_check",
            ratio_numerator="num",
            ratio_denominator="den",
            min_value=0.5,
        )
        records = [{"value": 0, "num": 1.0, "den": 10.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_ratio_check_batch_zero_denominator(self):
        rule = _rule(
            type="ratio_check",
            ratio_numerator="num",
            ratio_denominator="den",
        )
        records = [{"value": 0, "num": 5.0, "den": 0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_ratio_check_batch_missing_fields_warns(self):
        rule = _rule(
            type="ratio_check",
            ratio_numerator="ghost_num",
            ratio_denominator="ghost_den",
        )
        records = [{"value": 0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["total"] == 1

    def test_geospatial_batch_passes(self):
        rule = _rule(
            type="geospatial_bounds",
            geo_min_lat=49.0,
            geo_max_lat=60.0,
            geo_lon_field="lon",
            geo_min_lon=-8.0,
            geo_max_lon=2.0,
        )
        records = [{"value": 51.5, "lon": -0.1}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_geospatial_batch_lat_out_of_range(self):
        rule = _rule(
            type="geospatial_bounds",
            geo_min_lat=49.0,
            geo_max_lat=60.0,
        )
        records = [{"value": 40.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_geospatial_batch_lon_out_of_range(self):
        rule = _rule(
            type="geospatial_bounds",
            geo_lon_field="lon",
            geo_min_lon=-8.0,
            geo_max_lon=2.0,
        )
        records = [{"value": 51.5, "lon": 50.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_geospatial_batch_null_value(self):
        rule = _rule(type="geospatial_bounds")
        records = [{"value": None}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_geospatial_batch_invalid_lat_range(self):
        """Latitude outside -90..90 fails."""
        rule = _rule(type="geospatial_bounds")
        records = [{"value": 95.0}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_min_age_batch_passes(self):
        rule = _rule(type="min_age", min_age=18, field="dob")
        records = [{"dob": "2000-01-01"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_min_age_batch_fails(self):
        rule = _rule(type="min_age", min_age=18, field="dob")
        records = [{"dob": "2025-01-01"}]  # too young
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 1

    def test_field_not_in_data_skipped(self):
        """Rule field absent from records → skipped (no crash, no failures)."""
        rule = _rule(type="not_empty", field="ghost_field")
        records = [{"value": "x"}]
        result = validate_batch(records, [rule], contract_name="test")
        assert result["summary"]["failed"] == 0

    def test_multiple_rules_mixed(self):
        """Multiple rules — one passes, one fails per record."""
        rules = [
            _rule(name="age", type="min", min_value=18),
            _rule(name="email", type="not_empty", field="email"),
        ]
        records = [
            {"value": 25, "email": "a@b.com"},  # pass both
            {"value": 10, "email": "a@b.com"},  # fail age
            {"value": 25, "email": ""},          # fail email
        ]
        result = validate_batch(records, rules, contract_name="test")
        assert result["summary"]["total"] == 3
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 2
