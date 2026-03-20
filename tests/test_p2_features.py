"""Tests for P2 features: date_diff, ratio_check, conditional_lookup, all_of lookup, semver compare, validate_in_states."""

from unittest.mock import patch
from core.rule_parser import Rule
from core.validator import validate_record, validate_batch


class TestDateDiff:
    """P2 — date_diff rule."""

    def test_date_diff_days_within_range_passes(self):
        # d1 - d2 = 30 days, allowed 0..60
        rule = Rule(name="r", type="date_diff", field="end_date",
                    date_diff_field="start_date",
                    date_diff_unit="days",
                    min_value=0, max_value=60,
                    error_message="Duration must be 0-60 days")
        result = validate_record({"end_date": "2026-02-01", "start_date": "2026-01-02"}, [rule])
        assert result["valid"] is True

    def test_date_diff_days_exceeds_max_fails(self):
        rule = Rule(name="r", type="date_diff", field="end_date",
                    date_diff_field="start_date",
                    date_diff_unit="days",
                    min_value=0, max_value=60,
                    error_message="Duration must be 0-60 days")
        result = validate_record({"end_date": "2026-06-01", "start_date": "2026-01-01"}, [rule])
        assert result["valid"] is False  # 151 days > 60

    def test_date_diff_years_within_range_passes(self):
        rule = Rule(name="r", type="date_diff", field="end_date",
                    date_diff_field="start_date",
                    date_diff_unit="years",
                    min_value=0, max_value=5,
                    error_message="Must be within 5 years")
        result = validate_record({"end_date": "2028-01-01", "start_date": "2026-01-01"}, [rule])
        assert result["valid"] is True  # ~2 years

    def test_date_diff_missing_field_fails(self):
        rule = Rule(name="r", type="date_diff", field="end_date",
                    date_diff_field="start_date",
                    date_diff_unit="days",
                    min_value=0, max_value=60,
                    error_message="Missing date")
        result = validate_record({"end_date": "2026-02-01"}, [rule])
        assert result["valid"] is False

    def test_date_diff_batch_mode(self):
        rule = Rule(name="r", type="date_diff", field="end_date",
                    date_diff_field="start_date",
                    date_diff_unit="days",
                    min_value=1, max_value=90,
                    error_message="Invalid duration")
        records = [
            {"end_date": "2026-02-01", "start_date": "2026-01-01"},  # 31 days — valid
            {"end_date": "2026-07-01", "start_date": "2026-01-01"},  # 181 days — invalid
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 1
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False


class TestRatioCheck:
    """P2 — ratio_check rule."""

    def test_ratio_within_range_passes(self):
        # LTV: loan / property_value = 0.75, max 0.95
        rule = Rule(name="r", type="ratio_check", field="loan_amount",
                    ratio_numerator="loan_amount",
                    ratio_denominator="property_value",
                    max_value=0.95,
                    error_message="LTV exceeds 95%")
        result = validate_record({"loan_amount": 750000, "property_value": 1000000}, [rule])
        assert result["valid"] is True

    def test_ratio_above_max_fails(self):
        rule = Rule(name="r", type="ratio_check", field="loan_amount",
                    ratio_numerator="loan_amount",
                    ratio_denominator="property_value",
                    max_value=0.95,
                    error_message="LTV exceeds 95%")
        result = validate_record({"loan_amount": 980000, "property_value": 1000000}, [rule])
        assert result["valid"] is False  # LTV=0.98 > 0.95

    def test_ratio_zero_denominator_fails(self):
        rule = Rule(name="r", type="ratio_check", field="numerator",
                    ratio_numerator="numerator",
                    ratio_denominator="denominator",
                    max_value=1.0,
                    error_message="Division by zero")
        result = validate_record({"numerator": 100, "denominator": 0}, [rule])
        assert result["valid"] is False

    def test_ratio_batch_mode(self):
        rule = Rule(name="r", type="ratio_check", field="losses",
                    ratio_numerator="losses",
                    ratio_denominator="total_input",
                    max_value=0.25,
                    error_message="NRW exceeds 25%")
        records = [
            {"losses": 200, "total_input": 1000},  # NRW=20% — valid
            {"losses": 300, "total_input": 1000},  # NRW=30% — invalid
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 1
        assert result["results"][1]["valid"] is False


class TestAllOfLookup:
    """P2 — all_of for list-type lookup."""

    def test_all_elements_valid_passes(self, tmp_path):
        from core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        lookup_file = tmp_path / "codes.txt"
        lookup_file.write_text("A\nB\nC\n")
        with patch("config.CONTRACTS_DIR", tmp_path):
            rule = Rule(name="r", type="lookup", field="tags",
                        lookup_file=str(lookup_file),
                        all_of=True,
                        error_message="Invalid tag")
            result = validate_record({"tags": ["A", "B"]}, [rule])
        assert result["valid"] is True
        _load_lookup_set.cache_clear()

    def test_one_invalid_element_fails(self, tmp_path):
        from core.validator import _load_lookup_set
        _load_lookup_set.cache_clear()
        lookup_file = tmp_path / "codes.txt"
        lookup_file.write_text("A\nB\nC\n")
        with patch("config.CONTRACTS_DIR", tmp_path):
            rule = Rule(name="r", type="lookup", field="tags",
                        lookup_file=str(lookup_file),
                        all_of=True,
                        error_message="Invalid tag")
            result = validate_record({"tags": ["A", "X"]}, [rule])
        assert result["valid"] is False
        _load_lookup_set.cache_clear()


class TestSemverCompare:
    """P2 — algorithm: semver for compare rule."""

    def test_semver_gte_passes(self):
        rule = Rule(name="r", type="compare", field="sdk_version",
                    compare_to="min_sdk_version",
                    compare_op="gte",
                    algorithm="semver",
                    error_message="SDK version too old")
        result = validate_record({"sdk_version": "2.1.0", "min_sdk_version": "1.5.0"}, [rule])
        assert result["valid"] is True

    def test_semver_gte_fails(self):
        rule = Rule(name="r", type="compare", field="sdk_version",
                    compare_to="min_sdk_version",
                    compare_op="gte",
                    algorithm="semver",
                    error_message="SDK version too old")
        result = validate_record({"sdk_version": "1.0.0", "min_sdk_version": "2.0.0"}, [rule])
        assert result["valid"] is False


class TestValidateInStates:
    """P2 — validate_in_states enforcement."""

    def test_data_contract_validate_in_states_default(self):
        from core.contracts import DataContract
        dc = DataContract(name="test", rules=[])
        assert "active" in dc.validate_in_states

    def test_data_contract_validate_in_states_custom(self):
        from core.contracts import DataContract
        dc = DataContract(name="test", rules=[], validate_in_states=["draft", "active"])
        assert "draft" in dc.validate_in_states
        assert "active" in dc.validate_in_states
