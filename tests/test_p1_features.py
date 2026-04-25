"""Tests for P0 and P1 features added to OpenDQV.

Covers:
  - compare_to: today/now sentinels          (P0)
  - regex negate: true                       (P1)
  - checksum rule (multiple algorithms)      (P1)
  - cross_field_range rule                   (P1)
  - field_sum rule                           (P1)
  - forbidden_if rule                        (P1)
  - conditional_value rule                   (P1)
  - grouped unique (unique with group_by)    (P1)
  - sensitive_fields on DataContract         (P1)
  - REVIEW lifecycle state machine           (P1)
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_record, validate_batch


# ── P0: compare_to: today / now ──────────────────────────────────────────────

class TestCompareTodayNow:
    """P0 — compare_to: today/now sentinel support."""

    def test_compare_to_today_lte_passes_past_date(self):
        # A date in the past is <= today — should pass
        rule = Rule(name="r", type="compare", field="event_date",
                    compare_to="today", compare_op="lte",
                    error_message="Must not be future-dated")
        result = validate_record({"event_date": "2020-01-01"}, [rule])
        assert result["valid"] is True

    def test_compare_to_today_lte_fails_future_date(self):
        rule = Rule(name="r", type="compare", field="event_date",
                    compare_to="today", compare_op="lte",
                    error_message="Must not be future-dated")
        result = validate_record({"event_date": "2099-01-01"}, [rule])
        assert result["valid"] is False

    def test_compare_to_today_gte_passes_future_date(self):
        # "must be in the future" — >= today
        rule = Rule(name="r", type="compare", field="expiry_date",
                    compare_to="today", compare_op="gte",
                    error_message="Must be today or later")
        result = validate_record({"expiry_date": "2099-12-31"}, [rule])
        assert result["valid"] is True

    def test_compare_to_today_gte_fails_past_date(self):
        rule = Rule(name="r", type="compare", field="expiry_date",
                    compare_to="today", compare_op="gte",
                    error_message="Must be today or later")
        result = validate_record({"expiry_date": "2000-01-01"}, [rule])
        assert result["valid"] is False

    def test_compare_to_today_none_field_skipped(self):
        # CRT170/J3: target field absent — compare skips (not_empty is the catcher).
        rule = Rule(name="r", type="compare", field="event_date",
                    compare_to="today", compare_op="lte",
                    error_message="Required")
        result = validate_record({"event_date": None}, [rule])
        assert result["valid"] is True

    def test_compare_to_now_passes_past_datetime(self):
        rule = Rule(name="r", type="compare", field="created_at",
                    compare_to="now", compare_op="lte",
                    error_message="Must not be future")
        result = validate_record({"created_at": "2020-01-01T00:00:00"}, [rule])
        assert result["valid"] is True

    def test_compare_to_now_utc_aware_passes(self):
        """UTC-aware datetime should compare correctly against 'now' sentinel."""
        rule = Rule(name="r", type="compare", field="ts",
                    compare_to="now", compare_op="lte", error_message="Must not be future")
        result = validate_record({"ts": "2020-01-01T00:00:00+00:00"}, [rule])
        assert result["valid"] is True

    def test_compare_to_now_offset_aware_passes(self):
        """Non-UTC offset datetime should be normalised to UTC and compare correctly."""
        rule = Rule(name="r", type="compare", field="ts",
                    compare_to="now", compare_op="lte", error_message="Must not be future")
        result = validate_record({"ts": "2020-01-01T00:00:00+02:00"}, [rule])
        assert result["valid"] is True

    def test_compare_to_now_z_suffix_passes(self):
        """Z-suffix datetime should be treated as UTC and compare correctly."""
        rule = Rule(name="r", type="compare", field="ts",
                    compare_to="now", compare_op="lte", error_message="Must not be future")
        result = validate_record({"ts": "2020-01-01T00:00:00Z"}, [rule])
        assert result["valid"] is True

    def test_compare_to_now_future_utc_fails(self):
        """Future UTC-aware datetime should fail lte against now."""
        rule = Rule(name="r", type="compare", field="ts",
                    compare_to="now", compare_op="lte", error_message="Must not be future")
        result = validate_record({"ts": "2099-01-01T00:00:00+00:00"}, [rule])
        assert result["valid"] is False

    def test_compare_to_today_batch_mode(self):
        rule = Rule(name="r", type="compare", field="trade_date",
                    compare_to="today", compare_op="lte",
                    error_message="Future-dated trade")
        records = [
            {"trade_date": "2020-06-15"},  # valid
            {"trade_date": "2099-06-15"},  # invalid
            {"trade_date": "2021-01-01"},  # valid
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 1
        assert result["results"][1]["valid"] is False


# ── P1: regex negate ──────────────────────────────────────────────────────────

class TestNegateRegex:
    """P1 — regex with negate: true."""

    def test_negate_regex_pass_when_no_match(self):
        # Field must NOT contain "test" — "prod" does not match, so passes
        rule = Rule(name="r", type="regex", field="env",
                    pattern="^test", negate=True,
                    error_message="Must not be test environment")
        result = validate_record({"env": "production"}, [rule])
        assert result["valid"] is True

    def test_negate_regex_fail_when_match(self):
        rule = Rule(name="r", type="regex", field="env",
                    pattern="^test", negate=True,
                    error_message="Must not be test environment")
        result = validate_record({"env": "test-server"}, [rule])
        assert result["valid"] is False

    def test_negate_regex_batch(self):
        rule = Rule(name="r", type="regex", field="status",
                    pattern="^DELETED$", negate=True,
                    error_message="Deleted records not allowed")
        records = [{"status": "ACTIVE"}, {"status": "DELETED"}, {"status": "PENDING"}]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 2
        assert result["results"][1]["valid"] is False


# ── P1: checksum rule ─────────────────────────────────────────────────────────

class TestChecksumRule:
    """P1 — checksum rule with multiple algorithms."""

    def test_gtin_valid(self):
        # GTIN-13: 5901234123457 (mod-10 GS1)
        rule = Rule(name="r", type="checksum", field="barcode",
                    checksum_algorithm="mod10_gs1", error_message="Invalid GTIN")
        result = validate_record({"barcode": "5901234123457"}, [rule])
        assert result["valid"] is True

    def test_gtin_invalid(self):
        rule = Rule(name="r", type="checksum", field="barcode",
                    checksum_algorithm="mod10_gs1", error_message="Invalid GTIN")
        result = validate_record({"barcode": "5901234123458"}, [rule])  # wrong check digit
        assert result["valid"] is False

    def test_iban_valid(self):
        # GB82WEST12345698765432 — real IBAN
        rule = Rule(name="r", type="checksum", field="iban",
                    checksum_algorithm="iban_mod97", error_message="Invalid IBAN")
        result = validate_record({"iban": "GB82WEST12345698765432"}, [rule])
        assert result["valid"] is True

    def test_iban_invalid(self):
        rule = Rule(name="r", type="checksum", field="iban",
                    checksum_algorithm="iban_mod97", error_message="Invalid IBAN")
        result = validate_record({"iban": "GB00WEST12345698765432"}, [rule])
        assert result["valid"] is False

    def test_nhs_valid(self):
        # NHS number 943 476 5919
        rule = Rule(name="r", type="checksum", field="nhs_number",
                    checksum_algorithm="nhs_mod11", error_message="Invalid NHS number")
        result = validate_record({"nhs_number": "9434765919"}, [rule])
        assert result["valid"] is True

    def test_nhs_invalid(self):
        rule = Rule(name="r", type="checksum", field="nhs_number",
                    checksum_algorithm="nhs_mod11", error_message="Invalid NHS number")
        result = validate_record({"nhs_number": "9434765910"}, [rule])  # wrong check digit
        assert result["valid"] is False

    def test_vin_valid(self):
        # Valid VIN: 1HGBH41JXMN109186
        rule = Rule(name="r", type="checksum", field="vin",
                    checksum_algorithm="vin_mod11", error_message="Invalid VIN")
        result = validate_record({"vin": "1HGBH41JXMN109186"}, [rule])
        assert result["valid"] is True

    def test_vin_invalid_length(self):
        rule = Rule(name="r", type="checksum", field="vin",
                    checksum_algorithm="vin_mod11", error_message="Invalid VIN")
        result = validate_record({"vin": "1HGBH41JX"}, [rule])  # too short
        assert result["valid"] is False

    def test_isin_valid(self):
        # Apple ISIN: US0378331005
        rule = Rule(name="r", type="checksum", field="isin",
                    checksum_algorithm="isin_mod11", error_message="Invalid ISIN")
        result = validate_record({"isin": "US0378331005"}, [rule])
        assert result["valid"] is True

    def test_isin_invalid(self):
        rule = Rule(name="r", type="checksum", field="isin",
                    checksum_algorithm="isin_mod11", error_message="Invalid ISIN")
        result = validate_record({"isin": "US0378331006"}, [rule])  # wrong check digit
        assert result["valid"] is False

    def test_checksum_none_value_skipped(self):
        # CRT170/J3: target field absent — checksum skips (not_empty is the catcher).
        rule = Rule(name="r", type="checksum", field="barcode",
                    checksum_algorithm="mod10_gs1", error_message="Required")
        result = validate_record({"barcode": None}, [rule])
        assert result["valid"] is True

    def test_checksum_batch_mode(self):
        rule = Rule(name="r", type="checksum", field="iban",
                    checksum_algorithm="iban_mod97", error_message="Invalid IBAN")
        records = [
            {"iban": "GB82WEST12345698765432"},  # valid
            {"iban": "GB00WEST12345698765432"},  # invalid
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 1
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False

    def test_unknown_algorithm_passes(self):
        rule = Rule(name="r", type="checksum", field="code",
                    checksum_algorithm="unknown_algo", error_message="Failed")
        result = validate_record({"code": "anything"}, [rule])
        assert result["valid"] is True  # unknown algorithms pass through


# ── P1: cross_field_range rule ────────────────────────────────────────────────

class TestCrossFieldRange:
    """P1 — cross_field_range rule."""

    def test_value_within_range_passes(self):
        rule = Rule(name="r", type="cross_field_range", field="trade_price",
                    cross_min_field="min_price", cross_max_field="max_price",
                    error_message="Price out of range")
        result = validate_record({"trade_price": 50, "min_price": 10, "max_price": 100}, [rule])
        assert result["valid"] is True

    def test_value_below_min_fails(self):
        rule = Rule(name="r", type="cross_field_range", field="trade_price",
                    cross_min_field="min_price", cross_max_field="max_price",
                    error_message="Price out of range")
        result = validate_record({"trade_price": 5, "min_price": 10, "max_price": 100}, [rule])
        assert result["valid"] is False

    def test_value_above_max_fails(self):
        rule = Rule(name="r", type="cross_field_range", field="trade_price",
                    cross_min_field="min_price", cross_max_field="max_price",
                    error_message="Price out of range")
        result = validate_record({"trade_price": 150, "min_price": 10, "max_price": 100}, [rule])
        assert result["valid"] is False

    def test_value_equals_boundary_passes(self):
        rule = Rule(name="r", type="cross_field_range", field="settlement_amount",
                    cross_min_field="low", cross_max_field="high",
                    error_message="Out of range")
        result = validate_record({"settlement_amount": 10, "low": 10, "high": 100}, [rule])
        assert result["valid"] is True

    def test_min_field_only(self):
        rule = Rule(name="r", type="cross_field_range", field="bid",
                    cross_min_field="reserve_price",
                    error_message="Bid below reserve")
        result = validate_record({"bid": 100, "reserve_price": 50}, [rule])
        assert result["valid"] is True

    def test_batch_cross_field_range(self):
        rule = Rule(name="r", type="cross_field_range", field="actual",
                    cross_min_field="low", cross_max_field="high",
                    error_message="Out of range")
        records = [
            {"actual": 50, "low": 10, "high": 100},   # valid
            {"actual": 5, "low": 10, "high": 100},    # below min
            {"actual": 200, "low": 10, "high": 100},  # above max
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 1
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False
        assert result["results"][2]["valid"] is False


# ── P1: field_sum rule ────────────────────────────────────────────────────────

class TestFieldSum:
    """P1 — field_sum rule."""

    def test_exact_sum_passes(self):
        rule = Rule(name="r", type="field_sum", field="allocation_a",
                    sum_fields=["allocation_a", "allocation_b", "allocation_c"],
                    sum_equals=100.0,
                    error_message="Allocations must sum to 100")
        result = validate_record({"allocation_a": 50, "allocation_b": 30, "allocation_c": 20}, [rule])
        assert result["valid"] is True

    def test_sum_not_equal_fails(self):
        rule = Rule(name="r", type="field_sum", field="allocation_a",
                    sum_fields=["allocation_a", "allocation_b"],
                    sum_equals=100.0,
                    error_message="Must sum to 100")
        result = validate_record({"allocation_a": 60, "allocation_b": 30}, [rule])
        assert result["valid"] is False

    def test_sum_with_tolerance_passes(self):
        rule = Rule(name="r", type="field_sum", field="weight_a",
                    sum_fields=["weight_a", "weight_b"],
                    sum_equals=1.0,
                    sum_tolerance=0.01,
                    error_message="Weights must sum to 1.0")
        result = validate_record({"weight_a": 0.505, "weight_b": 0.496}, [rule])
        assert result["valid"] is True  # 1.001, within 0.01 tolerance

    def test_sum_batch_mode(self):
        rule = Rule(name="r", type="field_sum", field="p1",
                    sum_fields=["p1", "p2"],
                    sum_equals=100.0,
                    error_message="Must sum to 100")
        records = [
            {"p1": 60, "p2": 40},  # valid
            {"p1": 50, "p2": 40},  # invalid: 90
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 1
        assert result["results"][0]["valid"] is True
        assert result["results"][1]["valid"] is False


# ── P1: forbidden_if rule ─────────────────────────────────────────────────────

class TestForbiddenIf:
    """P1 — forbidden_if rule."""

    def test_field_absent_when_condition_met_passes(self):
        rule = Rule(name="r", type="forbidden_if", field="suspension_reason",
                    forbidden_if={"field": "status", "value": "ACTIVE"},
                    error_message="suspension_reason must be absent for ACTIVE cases")
        result = validate_record({"status": "ACTIVE", "suspension_reason": None}, [rule])
        assert result["valid"] is True

    def test_field_present_when_condition_met_fails(self):
        rule = Rule(name="r", type="forbidden_if", field="suspension_reason",
                    forbidden_if={"field": "status", "value": "ACTIVE"},
                    error_message="suspension_reason must be absent for ACTIVE cases")
        result = validate_record({"status": "ACTIVE", "suspension_reason": "Late payment"}, [rule])
        assert result["valid"] is False

    def test_condition_not_met_allows_field(self):
        rule = Rule(name="r", type="forbidden_if", field="suspension_reason",
                    forbidden_if={"field": "status", "value": "ACTIVE"},
                    error_message="suspension_reason must be absent for ACTIVE cases")
        result = validate_record({"status": "SUSPENDED", "suspension_reason": "Non-payment"}, [rule])
        assert result["valid"] is True

    def test_forbidden_if_batch(self):
        rule = Rule(name="r", type="forbidden_if", field="rejection_code",
                    forbidden_if={"field": "approved", "value": "Y"},
                    error_message="Cannot have rejection_code when approved")
        records = [
            {"approved": "Y", "rejection_code": None},      # valid
            {"approved": "Y", "rejection_code": "E001"},    # invalid
            {"approved": "N", "rejection_code": "E002"},    # valid (condition not met)
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 2
        assert result["results"][1]["valid"] is False


# ── P1: conditional_value rule ────────────────────────────────────────────────

class TestConditionalValue:
    """P1 — conditional_value (must_equal_if) rule."""

    def test_correct_value_when_condition_met_passes(self):
        rule = Rule(name="r", type="conditional_value", field="status",
                    must_equal="PENDING",
                    condition={"field": "workflow_stage", "value": "INTAKE"},
                    error_message="Status must be PENDING during INTAKE")
        result = validate_record({"workflow_stage": "INTAKE", "status": "PENDING"}, [rule])
        assert result["valid"] is True

    def test_wrong_value_when_condition_met_fails(self):
        rule = Rule(name="r", type="conditional_value", field="status",
                    must_equal="PENDING",
                    condition={"field": "workflow_stage", "value": "INTAKE"},
                    error_message="Status must be PENDING during INTAKE")
        result = validate_record({"workflow_stage": "INTAKE", "status": "ACTIVE"}, [rule])
        assert result["valid"] is False

    def test_condition_not_met_allows_any_value(self):
        rule = Rule(name="r", type="conditional_value", field="status",
                    must_equal="PENDING",
                    condition={"field": "workflow_stage", "value": "INTAKE"},
                    error_message="Status must be PENDING during INTAKE")
        result = validate_record({"workflow_stage": "REVIEW", "status": "ACTIVE"}, [rule])
        assert result["valid"] is True


# ── P1: grouped unique ────────────────────────────────────────────────────────

class TestGroupedUnique:
    """P1 — unique with group_by."""

    def test_unique_within_group_passes(self):
        rule = Rule(name="r", type="unique", field="settlement_period",
                    group_by=["meter_id"],
                    error_message="Duplicate settlement_period per meter")
        records = [
            {"meter_id": "M1", "settlement_period": 1},
            {"meter_id": "M1", "settlement_period": 2},
            {"meter_id": "M2", "settlement_period": 1},  # different group — ok
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 3

    def test_duplicate_within_group_fails(self):
        rule = Rule(name="r", type="unique", field="settlement_period",
                    group_by=["meter_id"],
                    error_message="Duplicate settlement_period per meter")
        records = [
            {"meter_id": "M1", "settlement_period": 1},
            {"meter_id": "M1", "settlement_period": 1},  # duplicate in same group
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["failed"] == 2  # both flagged


# ── P1: sensitive_fields on DataContract ─────────────────────────────────────

class TestSensitiveFields:
    """P1 — sensitive_fields on DataContract."""

    def test_data_contract_sensitive_fields_default_empty(self):
        from opendqv.core.contracts import DataContract
        dc = DataContract(name="test", rules=[])
        assert dc.sensitive_fields == []

    def test_data_contract_sensitive_fields_set(self):
        from opendqv.core.contracts import DataContract
        dc = DataContract(name="test", rules=[], sensitive_fields=["salary", "national_id"])
        assert "salary" in dc.sensitive_fields
        assert "national_id" in dc.sensitive_fields


# ── P1: REVIEW lifecycle state machine ───────────────────────────────────────

class TestREVIEWLifecycle:
    """P1 — REVIEW lifecycle state machine."""

    def _make_registry(self, tmp_path):
        import yaml
        from opendqv.core.contracts import ContractRegistry

        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()

        contract_data = {
            "contract": {
                "name": "test_lifecycle",
                "version": "1.0",
                "status": "draft",
                "description": "Test",
                "owner": "test",
                "rules": [
                    {
                        "name": "r1",
                        "type": "not_empty",
                        "field": "name",
                        "error_message": "Required",
                    }
                ],
            }
        }
        (contracts_dir / "test_lifecycle.yaml").write_text(yaml.dump(contract_data))

        # Point the DB at an in-memory store for this test
        os.environ["OPENDQV_DB_PATH"] = ":memory:"
        import importlib
        import opendqv.config as _config
        importlib.reload(_config)

        return ContractRegistry(contracts_dir)

    def test_submit_for_review(self, tmp_path):
        from opendqv.core.rule_parser import ContractStatus
        registry = self._make_registry(tmp_path)
        contract = registry.submit_for_review("test_lifecycle", "1.0", "alice@example.com")
        assert contract is not None
        assert contract.status == ContractStatus.REVIEW
        assert contract.proposed_by == "alice@example.com"

    def test_approve_contract(self, tmp_path):
        from opendqv.core.rule_parser import ContractStatus
        registry = self._make_registry(tmp_path)
        registry.submit_for_review("test_lifecycle", "1.0", "alice@example.com")
        contract = registry.approve_contract("test_lifecycle", "1.0", "bob@example.com")
        assert contract is not None
        assert contract.status == ContractStatus.ACTIVE
        assert contract.approved_by == "bob@example.com"

    def test_reject_contract(self, tmp_path):
        from opendqv.core.rule_parser import ContractStatus
        registry = self._make_registry(tmp_path)
        registry.submit_for_review("test_lifecycle", "1.0", "alice@example.com")
        contract = registry.reject_contract("test_lifecycle", "1.0", "bob@example.com", "Needs revision")
        assert contract is not None
        assert contract.status == ContractStatus.DRAFT
        assert contract.rejection_reason == "Needs revision"

    def test_cannot_submit_active_for_review(self, tmp_path):
        registry = self._make_registry(tmp_path)
        registry.submit_for_review("test_lifecycle", "1.0", "alice")
        registry.approve_contract("test_lifecycle", "1.0", "bob")
        with pytest.raises(ValueError):
            registry.submit_for_review("test_lifecycle", "1.0", "charlie")

    def test_cannot_approve_draft(self, tmp_path):
        registry = self._make_registry(tmp_path)
        with pytest.raises(ValueError):
            registry.approve_contract("test_lifecycle", "1.0", "bob")


# ─────────────────────────────────────────────────────────────────────────────
# Conference P1 — F25: engine_version in validate response
# ─────────────────────────────────────────────────────────────────────────────

class TestEngineVersionInResponse:
    """Conference F25 — engine_version present in both single and batch validate responses."""

    def test_single_validate_has_engine_version(self, client, auth_headers):
        import opendqv.config as config
        body = {"record": {"email": "a@example.com", "age": 25, "name": "Alice"}, "contract": "customer"}
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "engine_version" in data
        assert data["engine_version"] == config.ENGINE_VERSION

    def test_batch_validate_has_engine_version(self, client, auth_headers):
        import opendqv.config as config
        body = {
            "records": [{"email": "a@example.com", "age": 25, "name": "Alice"}],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "engine_version" in data
        assert data["engine_version"] == config.ENGINE_VERSION

    def test_engine_version_is_nonempty_string(self, client, auth_headers):
        body = {"record": {"email": "a@example.com"}, "contract": "customer"}
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        ev = r.json()["engine_version"]
        assert isinstance(ev, str) and len(ev) > 0

    def test_engine_version_constant_matches_pyproject(self):
        import tomllib
        import opendqv.config as config
        pyproject = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        )
        expected = pyproject["tool"]["poetry"]["version"]
        assert config.ENGINE_VERSION == expected, (
            f"ENGINE_VERSION {config.ENGINE_VERSION!r} does not match "
            f"pyproject.toml {expected!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Conference P1 — F9: ?as_of=<timestamp> point-in-time validation
# ─────────────────────────────────────────────────────────────────────────────

class TestAsOf:
    """Conference F9 — validate against the contract version active at a historical timestamp."""

    def test_as_of_not_found_returns_404(self, client, auth_headers):
        """No history before year 2000 — should return 404."""
        body = {"record": {"email": "a@example.com"}, "contract": "customer"}
        r = client.post(
            "/api/v1/validate?as_of=2000-01-01T00:00:00Z",
            json=body,
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_as_of_future_timestamp_returns_200_when_history_exists(self, client, auth_headers):
        """Activate customer to ensure a snapshot, then query as_of far future."""
        # Activate to ensure at least one history snapshot exists
        client.post("/api/v1/contracts/customer/activate", headers=auth_headers)

        body = {"record": {"email": "a@example.com", "age": 25, "name": "Alice"}, "contract": "customer"}
        r = client.post(
            "/api/v1/validate?as_of=2099-12-31T23:59:59Z",
            json=body,
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "valid" in data
        assert data["contract"] == "customer"

    def test_as_of_result_not_422_from_status_check(self, client, auth_headers):
        """Point-in-time queries must not be rejected due to contract status checks."""
        client.post("/api/v1/contracts/customer/activate", headers=auth_headers)
        body = {"record": {"email": "a@example.com", "age": 25, "name": "Alice"}, "contract": "customer"}
        r = client.post(
            "/api/v1/validate?as_of=2099-12-31T23:59:59Z",
            json=body,
            headers=auth_headers,
        )
        assert r.status_code != 422

    def test_as_of_param_in_openapi_spec(self, client):
        """The as_of query parameter should be documented in the OpenAPI spec."""
        import json as _json
        r = client.get("/openapi.json")
        assert r.status_code == 200
        assert "as_of" in _json.dumps(r.json())

    def test_as_of_get_as_of_returns_none_for_no_history(self):
        """ContractHistory.get_as_of returns None when no snapshot exists."""
        from opendqv.core.contracts import ContractHistory
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            ch = ContractHistory(db_path)
            result = ch.get_as_of("nonexistent", "2099-01-01T00:00:00Z")
            assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Conference P1 — F21: Federation sync-status endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestFederationSyncStatus:
    """Conference F21 — GET /api/v1/federation/sync-status"""

    def test_requires_auth(self, client):
        r = client.get("/api/v1/federation/sync-status")
        assert r.status_code == 401

    def test_no_peer_returns_local_inventory(self, client, auth_headers):
        r = client.get("/api/v1/federation/sync-status", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "opendqv_node_id" in data
        assert "local_contracts" in data
        assert "diverged" in data
        assert "peer_contracts" in data
        assert "peer_error" in data
        assert data["peer"] is None

    def test_local_contracts_non_empty(self, client, auth_headers):
        r = client.get("/api/v1/federation/sync-status", headers=auth_headers)
        assert len(r.json()["local_contracts"]) > 0

    def test_local_contracts_have_name_and_version(self, client, auth_headers):
        r = client.get("/api/v1/federation/sync-status", headers=auth_headers)
        for c in r.json()["local_contracts"]:
            assert "name" in c
            assert "version" in c

    def test_no_diverged_without_peer(self, client, auth_headers):
        r = client.get("/api/v1/federation/sync-status", headers=auth_headers)
        assert r.json()["diverged"] == []

    def test_with_unreachable_peer_returns_peer_error(self, client, auth_headers):
        r = client.get(
            "/api/v1/federation/sync-status?peer=http://localhost:19999",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["peer"] == "http://localhost:19999"
        assert data["peer_error"] is not None

    def test_node_id_matches_config(self, client, auth_headers):
        import opendqv.config as config
        r = client.get("/api/v1/federation/sync-status", headers=auth_headers)
        assert r.json()["opendqv_node_id"] == config.OPENDQV_NODE_ID


# ─────────────────────────────────────────────────────────────────────────────
# Conference P1 — F6: SDK local contract cache
# ─────────────────────────────────────────────────────────────────────────────

class TestSDKContractCache:
    """Conference F6 — contract_cache_dir for degraded/offline mode."""

    def test_contract_written_to_cache_on_success(self, client, auth_headers):
        """Successful contract() call writes a JSON file to cache dir."""
        import tempfile
        import json as _json
        from opendqv.sdk.client import OpenDQVClient
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = OpenDQVClient.__new__(OpenDQVClient)
            sdk.base_url = "http://testserver"
            sdk.contract_cache_dir = tmpdir

            mock_http = MagicMock()
            def mock_get(path, params=None, **kwargs):
                resp_obj = client.get(path, params=params, headers=auth_headers)
                mock_resp = MagicMock()
                mock_resp.status_code = resp_obj.status_code
                mock_resp.json.return_value = resp_obj.json()
                mock_resp.raise_for_status.side_effect = (
                    None if resp_obj.status_code < 400 else Exception()
                )
                return mock_resp
            mock_http.get = mock_get
            sdk._client = mock_http

            result = sdk.contract("customer")
            assert result["name"] == "customer"

            cache_file = os.path.join(tmpdir, "customer.json")
            assert os.path.exists(cache_file)
            cached = _json.loads(open(cache_file).read())
            assert cached["name"] == "customer"

    def test_contract_falls_back_to_cache_on_network_error(self):
        """When API is unreachable, contract() returns cached file."""
        import httpx as _httpx
        import tempfile
        import json as _json
        from opendqv.sdk.client import OpenDQVClient
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_data = {
                "name": "customer", "version": "1.0",
                "description": "Cached", "status": "active",
                "rules": [], "contexts": [],
            }
            with open(os.path.join(tmpdir, "customer.json"), "w") as f:
                _json.dump(cache_data, f)

            sdk = OpenDQVClient.__new__(OpenDQVClient)
            sdk.base_url = "http://unreachable.invalid"
            sdk.contract_cache_dir = tmpdir

            mock_http = MagicMock()
            mock_http.get.side_effect = _httpx.ConnectError("Connection refused")
            sdk._client = mock_http

            result = sdk.contract("customer")
            assert result["name"] == "customer"
            assert result["version"] == "1.0"

    def test_contract_raises_when_no_cache_and_unreachable(self):
        """No cache + no API = raises RequestError."""
        import httpx as _httpx
        import tempfile
        from opendqv.sdk.client import OpenDQVClient
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = OpenDQVClient.__new__(OpenDQVClient)
            sdk.base_url = "http://unreachable.invalid"
            sdk.contract_cache_dir = tmpdir

            mock_http = MagicMock()
            mock_http.get.side_effect = _httpx.ConnectError("Connection refused")
            sdk._client = mock_http

            with pytest.raises(_httpx.RequestError):
                sdk.contract("nonexistent")

    def test_no_cache_dir_does_not_write_files(self, client, auth_headers):
        """Without contract_cache_dir, no files are written anywhere."""
        import tempfile
        from opendqv.sdk.client import OpenDQVClient
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = OpenDQVClient.__new__(OpenDQVClient)
            sdk.base_url = "http://testserver"
            sdk.contract_cache_dir = None

            mock_http = MagicMock()
            def mock_get(path, params=None, **kwargs):
                resp_obj = client.get(path, params=params, headers=auth_headers)
                mock_resp = MagicMock()
                mock_resp.status_code = resp_obj.status_code
                mock_resp.json.return_value = resp_obj.json()
                mock_resp.raise_for_status.side_effect = (
                    None if resp_obj.status_code < 400 else Exception()
                )
                return mock_resp
            mock_http.get = mock_get
            sdk._client = mock_http

            sdk.contract("customer")
            assert len(os.listdir(tmpdir)) == 0


# ── Age rules: min_age, max_age, age_match ───────────────────────────────────

class TestAgeRules:
    """min_age and max_age constraints on date fields, and the age_match rule type."""

    def test_min_age_passes_when_old_enough(self):
        rule = Rule(name="r", type="not_empty", field="dob", min_age=18,
                    error_message="Must be 18+")
        result = validate_record({"dob": "1990-01-01"}, [rule])
        assert result["valid"] is True

    def test_min_age_fails_when_too_young(self):
        rule = Rule(name="r", type="not_empty", field="dob", min_age=18,
                    error_message="Must be 18+")
        result = validate_record({"dob": "2020-01-01"}, [rule])
        assert result["valid"] is False

    def test_max_age_passes_when_young_enough(self):
        rule = Rule(name="r", type="not_empty", field="dob", max_age=65,
                    error_message="Must be under 65")
        result = validate_record({"dob": "1990-01-01"}, [rule])
        assert result["valid"] is True

    def test_max_age_fails_when_too_old(self):
        rule = Rule(name="r", type="not_empty", field="dob", max_age=65,
                    error_message="Must be under 65")
        result = validate_record({"dob": "1950-01-01"}, [rule])
        assert result["valid"] is False

    def test_age_match_passes_when_consistent(self):
        from datetime import date
        today = date.today()
        # Use Jan 1 DOB so birthday has always passed by any test run date after Jan 1
        dob = f"{today.year - 30}-01-01"
        rule = Rule(name="r", type="age_match", field="declared_age", dob_field="dob",
                    age_tolerance=1, error_message="Age inconsistent with DOB")
        result = validate_record({"declared_age": "30", "dob": dob}, [rule])
        assert result["valid"] is True

    def test_age_match_fails_when_inconsistent(self):
        rule = Rule(name="r", type="age_match", field="declared_age", dob_field="dob",
                    error_message="Age inconsistent with DOB")
        result = validate_record({"declared_age": "30", "dob": "1950-06-01"}, [rule])
        assert result["valid"] is False


# ── conditional_lookup rule type ─────────────────────────────────────────────

class TestConditionalLookup:
    """conditional_lookup — documented-intent alias for a lookup with a condition block."""

    def test_conditional_lookup_passes_valid_value(self):
        import os
        import pathlib
        # Lookup files must live inside the contracts directory (path traversal guard)
        contracts_dir = pathlib.Path(os.environ["OPENDQV_CONTRACTS_DIR"])
        lookup_file = contracts_dir / "ref" / "test_statuses.txt"
        lookup_file.write_text("active\ninactive\npending\n")
        try:
            rule = Rule(name="r", type="conditional_lookup", field="status",
                        lookup_file="ref/test_statuses.txt", error_message="Invalid status")
            result = validate_record({"status": "active"}, [rule])
            assert result["valid"] is True
        finally:
            lookup_file.unlink(missing_ok=True)

    def test_conditional_lookup_fails_invalid_value(self):
        import os
        import pathlib
        contracts_dir = pathlib.Path(os.environ["OPENDQV_CONTRACTS_DIR"])
        lookup_file = contracts_dir / "ref" / "test_statuses.txt"
        lookup_file.write_text("active\ninactive\npending\n")
        try:
            rule = Rule(name="r", type="conditional_lookup", field="status",
                        lookup_file="ref/test_statuses.txt", error_message="Invalid status")
            result = validate_record({"status": "unknown"}, [rule])
            assert result["valid"] is False
        finally:
            lookup_file.unlink(missing_ok=True)
