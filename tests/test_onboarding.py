"""
Tests for core/onboarding.py — wizard engine, field inference, contract generator.

Covers onboarding wizard engine, field inference, and contract generator.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import os

from opendqv.core.onboarding import (
    OnboardingWizard,
    WizardResult,
    _build_valid_from_regex,
    _load_first_lookup_value,
    _read_api_lock,
    _read_workbench_lock,
    _write_api_lock,
    _write_workbench_lock,
    build_sample_records,
    build_sample_records_from_rules,
    generate_contract_yaml,
    infer_rule,
)


# ── infer_rule ─────────────────────────────────────────────────────────────────

class TestInferRule:
    """infer_rule() maps field names to appropriate rule dicts."""

    def test_exact_email(self):
        rule = infer_rule("email")
        assert rule["type"] == "regex"
        assert rule["field"] == "email"
        assert "@" in rule["pattern"]

    def test_exact_phone(self):
        assert infer_rule("phone")["type"] == "regex"
        assert infer_rule("mobile")["type"] == "regex"
        assert infer_rule("telephone")["type"] == "regex"

    def test_exact_name_fields(self):
        for name in ("name", "first_name", "last_name", "full_name", "surname"):
            rule = infer_rule(name)
            assert rule["type"] == "not_empty", f"Expected not_empty for {name}"

    def test_exact_age(self):
        rule = infer_rule("age")
        assert rule["type"] == "range"
        assert rule["min"] == 0
        assert rule["max"] == 150

    def test_exact_dates(self):
        for fname in ("date", "dob", "birth_date", "date_of_birth", "start_date", "end_date"):
            assert infer_rule(fname)["type"] == "date_format", f"Expected date_format for {fname}"

    def test_created_updated_at(self):
        assert infer_rule("created_at")["type"] == "date_format"
        assert infer_rule("updated_at")["type"] == "date_format"

    def test_url_fields(self):
        for fname in ("url", "website", "link"):
            assert infer_rule(fname)["type"] == "regex"

    def test_postcode_zip(self):
        assert infer_rule("postcode")["type"] == "regex"
        assert infer_rule("zip")["type"] == "regex"
        assert infer_rule("zip_code")["type"] == "regex"

    def test_country_fields(self):
        assert infer_rule("country")["type"] == "min_length"
        assert infer_rule("country_code")["type"] == "regex"

    def test_money_fields(self):
        for fname in ("amount", "price", "cost", "revenue", "salary"):
            rule = infer_rule(fname)
            assert rule["type"] == "min", f"Expected min for {fname}"
            assert rule["min"] == 0

    def test_balance_has_range(self):
        rule = infer_rule("balance")
        assert rule["type"] == "range"

    def test_score_rating_percentage(self):
        assert infer_rule("score")["type"] == "range"
        assert infer_rule("rating")["type"] == "range"
        assert infer_rule("percentage")["type"] == "range"

    # Suffix inference

    def test_id_suffix(self):
        for fname in ("user_id", "order_ref", "account_key", "invoice_no",
                      "record_num", "item_uuid", "id"):
            rule = infer_rule(fname)
            assert rule["type"] == "not_empty", f"Expected not_empty for {fname}"
            assert rule["field"] == fname

    def test_money_suffix(self):
        for fname in ("order_amount", "line_price", "shipping_cost",
                      "platform_fee", "cart_total"):
            rule = infer_rule(fname)
            assert rule["type"] == "min", f"Expected min for {fname}"
            assert rule["min"] == 0

    def test_date_suffix(self):
        for fname in ("dispatch_date", "completed_at", "event_time",
                      "registered_on"):
            rule = infer_rule(fname)
            assert rule["type"] == "date_format", f"Expected date_format for {fname}"

    def test_boolean_fields(self):
        for fname in ("available", "active", "enabled", "is_deleted", "is_active", "is_enabled"):
            rule = infer_rule(fname)
            assert rule["type"] == "regex", f"Expected regex for {fname}"
            assert "true" in rule["pattern"]
            assert "false" in rule["pattern"]

    def test_unknown_field_defaults_to_not_empty(self):
        rule = infer_rule("foobarbaz")
        assert rule["type"] == "not_empty"
        assert rule["field"] == "foobarbaz"

    def test_case_insensitive_exact_match(self):
        # The lookup is lower-cased internally
        rule = infer_rule("EMAIL")
        assert rule["type"] == "regex"

    def test_field_key_always_matches_input(self):
        # The "field" key must match the original input, not the lowercased key
        rule = infer_rule("EmailAddress")
        assert rule["field"] == "EmailAddress"


# ── generate_contract_yaml ─────────────────────────────────────────────────────

class TestGenerateContractYaml:
    """generate_contract_yaml() produces valid, parseable YAML."""

    def _parse(self, entity, fields):
        raw = generate_contract_yaml(entity, fields)
        return yaml.safe_load(raw)

    def test_returns_string(self):
        result = generate_contract_yaml("customer", ["email"])
        assert isinstance(result, str)

    def test_top_level_key(self):
        doc = self._parse("customer", ["email"])
        assert "contract" in doc

    def test_contract_name(self):
        doc = self._parse("order", ["name"])
        assert doc["contract"]["name"] == "order"

    def test_contract_status_active(self):
        doc = self._parse("order", ["email"])
        assert doc["contract"]["status"] == "active"

    def test_rules_list(self):
        doc = self._parse("customer", ["email", "name"])
        assert isinstance(doc["contract"]["rules"], list)
        assert len(doc["contract"]["rules"]) == 2

    def test_rule_fields_correct(self):
        doc = self._parse("customer", ["email", "name"])
        rules = doc["contract"]["rules"]
        rule_fields = {r["field"] for r in rules}
        assert rule_fields == {"email", "name"}

    def test_regex_rule_has_pattern(self):
        doc = self._parse("customer", ["email"])
        rule = doc["contract"]["rules"][0]
        assert rule["type"] == "regex"
        assert "pattern" in rule

    def test_range_rule_has_min_max(self):
        doc = self._parse("person", ["age"])
        rule = doc["contract"]["rules"][0]
        assert rule["type"] == "range"
        assert "min" in rule
        assert "max" in rule

    def test_min_rule_has_min(self):
        doc = self._parse("sale", ["amount"])
        rule = doc["contract"]["rules"][0]
        assert rule["type"] == "min"
        assert "min" in rule

    def test_all_rules_have_severity(self):
        doc = self._parse("customer", ["email", "name", "age"])
        for rule in doc["contract"]["rules"]:
            assert rule.get("severity") == "error"

    def test_all_rules_have_error_message(self):
        doc = self._parse("customer", ["email", "name"])
        for rule in doc["contract"]["rules"]:
            assert rule.get("error_message")

    def test_multiple_fields_all_present(self):
        fields = ["email", "name", "phone", "age", "dob"]
        doc = self._parse("customer", fields)
        rule_fields = [r["field"] for r in doc["contract"]["rules"]]
        for f in fields:
            assert f in rule_fields

    def test_unknown_fields_produce_not_empty(self):
        doc = self._parse("record", ["foobar"])
        assert doc["contract"]["rules"][0]["type"] == "not_empty"


# ── build_sample_records ────────────────────────────────────────────────────────

class TestBuildSampleRecords:
    """build_sample_records() returns a (valid, invalid) tuple."""

    def test_returns_two_dicts(self):
        valid, invalid = build_sample_records(["email"])
        assert isinstance(valid, dict)
        assert isinstance(invalid, dict)

    def test_both_contain_same_keys(self):
        fields = ["email", "name", "age"]
        valid, invalid = build_sample_records(fields)
        assert set(valid.keys()) == set(fields)
        assert set(invalid.keys()) == set(fields)

    def test_email_valid_contains_at(self):
        valid, _ = build_sample_records(["email"])
        assert "@" in valid["email"]

    def test_email_invalid_no_at(self):
        _, invalid = build_sample_records(["email"])
        assert "@" not in invalid["email"]

    def test_name_valid_not_empty(self):
        valid, _ = build_sample_records(["name"])
        assert valid["name"] != ""

    def test_name_invalid_empty(self):
        _, invalid = build_sample_records(["name"])
        assert invalid["name"] == ""

    def test_age_valid_positive(self):
        valid, _ = build_sample_records(["age"])
        assert valid["age"] >= 0

    def test_age_invalid_negative(self):
        _, invalid = build_sample_records(["age"])
        assert invalid["age"] < 0

    def test_date_valid_format(self):
        valid, _ = build_sample_records(["dob"])
        # Should be a YYYY-MM-DD string
        parts = valid["dob"].split("-")
        assert len(parts) == 3

    def test_date_invalid_not_a_date(self):
        _, invalid = build_sample_records(["birth_date"])
        assert invalid["birth_date"] == "not-a-date"

    def test_amount_valid_positive(self):
        valid, _ = build_sample_records(["amount"])
        assert valid["amount"] >= 0

    def test_amount_invalid_negative(self):
        _, invalid = build_sample_records(["amount"])
        assert invalid["amount"] < 0

    def test_url_valid_starts_with_http(self):
        valid, _ = build_sample_records(["url"])
        assert valid["url"].startswith("http")

    def test_id_suffix_valid_non_empty(self):
        valid, _ = build_sample_records(["user_id"])
        assert valid["user_id"] != ""

    def test_id_suffix_invalid_empty(self):
        _, invalid = build_sample_records(["user_id"])
        assert invalid["user_id"] == ""

    def test_unknown_field_valid_has_value(self):
        valid, _ = build_sample_records(["foobarbaz"])
        assert valid["foobarbaz"] != ""

    def test_unknown_field_invalid_empty(self):
        _, invalid = build_sample_records(["foobarbaz"])
        assert invalid["foobarbaz"] == ""

    def test_first_name_returns_alice(self):
        valid, _ = build_sample_records(["first_name"])
        assert valid["first_name"] == "Alice"

    def test_last_name_returns_smith(self):
        valid, _ = build_sample_records(["last_name"])
        assert valid["last_name"] == "Smith"

    def test_surname_returns_smith(self):
        valid, _ = build_sample_records(["surname"])
        assert valid["surname"] == "Smith"

    def test_ward_returns_general(self):
        valid, _ = build_sample_records(["ward"])
        assert valid["ward"] == "General"

    def test_currency_returns_gbp(self):
        valid, _ = build_sample_records(["currency"])
        assert valid["currency"] == "GBP"

    def test_department_returns_value(self):
        valid, _ = build_sample_records(["department"])
        assert valid["department"] == "Engineering"

    def test_status_returns_active(self):
        valid, _ = build_sample_records(["status"])
        assert valid["status"] == "ACTIVE"

    def test_address_returns_street(self):
        valid, _ = build_sample_records(["supply_address"])
        assert "Street" in valid["supply_address"] or "High" in valid["supply_address"]

    def test_type_suffix_fallback(self):
        valid, _ = build_sample_records(["meter_type"])
        assert valid["meter_type"] == "Smart"

    def test_generic_type_suffix(self):
        valid, _ = build_sample_records(["vehicle_type"])
        assert valid["vehicle_type"] == "STANDARD"

    def test_product_name_not_alice(self):
        valid, _ = build_sample_records(["product_name"])
        assert valid["product_name"] == "Premium Widget"

    def test_score_valid_in_range(self):
        valid, _ = build_sample_records(["score"])
        assert 0 <= valid["score"] <= 100

    def test_multi_field_completeness(self):
        fields = ["email", "first_name", "phone", "age", "created_at",
                  "order_amount", "user_id"]
        valid, invalid = build_sample_records(fields)
        assert len(valid) == len(fields)
        assert len(invalid) == len(fields)


# ── build_sample_records_from_rules ───────────────────────────────────────────

class TestBuildSampleRecordsFromRules:
    """build_sample_records_from_rules() generates correct samples from actual rule dicts."""

    def _rules(self, *entries):
        return [{"field": f, "type": t, **extra} for f, t, extra in entries]

    def test_not_empty_valid_nonempty(self):
        valid, _ = build_sample_records_from_rules([{"field": "ward", "type": "not_empty"}])
        assert valid["ward"] != ""

    def test_not_empty_invalid_empty(self):
        _, invalid = build_sample_records_from_rules([{"field": "ward", "type": "not_empty"}])
        assert invalid["ward"] == ""

    def test_date_format_valid(self):
        valid, _ = build_sample_records_from_rules([{"field": "dob", "type": "date_format"}])
        assert valid["dob"] == "1990-06-15"

    def test_date_format_invalid(self):
        _, invalid = build_sample_records_from_rules([{"field": "dob", "type": "date_format"}])
        assert invalid["dob"] == "not-a-date"

    def test_regex_with_eg_hint_uses_example(self):
        rules = [{"field": "blood_type", "type": "regex",
                  "pattern": r"^(A|B|AB|O)[+-]$",
                  "error_message": "must be a valid blood group (e.g. A+, O-, AB+)"}]
        valid, invalid = build_sample_records_from_rules(rules)
        assert valid["blood_type"] == "A+"
        assert invalid["blood_type"] == "INVALID"

    def test_regex_nhs_number(self):
        rules = [{"field": "nhs_number", "type": "regex",
                  "pattern": r"^\d{3}[\s-]?\d{3}[\s-]?\d{4}$",
                  "error_message": "must be a valid NHS number (e.g. 123-456-7890)"}]
        valid, invalid = build_sample_records_from_rules(rules)
        assert valid["nhs_number"] == "123-456-7890"
        assert invalid["nhs_number"] == "INVALID"

    def test_range_valid_midpoint(self):
        rules = [{"field": "age", "type": "range", "min": 0, "max": 100}]
        valid, _ = build_sample_records_from_rules(rules)
        assert 0 <= valid["age"] <= 100

    def test_range_invalid_below_min(self):
        rules = [{"field": "age", "type": "range", "min": 0, "max": 100}]
        _, invalid = build_sample_records_from_rules(rules)
        assert invalid["age"] < 0

    def test_min_valid_zero(self):
        rules = [{"field": "amount", "type": "min", "min": 0}]
        valid, invalid = build_sample_records_from_rules(rules)
        assert valid["amount"] >= 0
        assert invalid["amount"] < 0

    def test_min_uses_actual_min_value(self):
        # dwell_seconds regression: min=1 must produce valid>=1, not valid=0
        rules = [{"field": "dwell_seconds", "type": "min", "min": 1}]
        valid, invalid = build_sample_records_from_rules(rules)
        assert valid["dwell_seconds"] >= 1
        assert invalid["dwell_seconds"] < 1

    def test_duplicate_field_priority_rule_wins(self):
        # date_format (priority 1) beats not_empty (priority 7)
        rules = [
            {"field": "x", "type": "not_empty"},
            {"field": "x", "type": "date_format"},
        ]
        valid, _ = build_sample_records_from_rules(rules)
        assert valid["x"] == "1990-06-15"  # date_format wins over not_empty

    def test_regex_wins_over_not_empty(self):
        # panel_type regression: regex (priority 0) beats not_empty (priority 7)
        rules = [
            {"field": "panel_type", "type": "not_empty"},
            {"field": "panel_type", "type": "regex",
             "pattern": r"^(CLASSIC|DIGITAL|LED|PROJECTOR|AIRPORT|TRANSIT)$"},
        ]
        valid, _ = build_sample_records_from_rules(rules)
        assert valid["panel_type"] == "CLASSIC"  # first enum option from regex

    def test_lookup_type_delegates_to_name_inference(self):
        # Universal Benchmark regression: lookup must not return "DEMO-001"
        rules = [{"field": "status", "type": "lookup", "values": ["ACTIVE", "INACTIVE"]}]
        valid, _ = build_sample_records_from_rules(rules)
        assert valid["status"] != "DEMO-001"

    def test_healthcare_patient_contract_valid_record(self):
        """Regression: healthcare_patient template produces a valid first record."""
        import yaml
        from pathlib import Path
        contract_path = Path(__file__).resolve().parent.parent / "contracts" / "healthcare_patient.yaml"
        if not contract_path.exists():
            pytest.skip("healthcare_patient.yaml not present")
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        rules = data["contract"]["rules"]
        valid, invalid = build_sample_records_from_rules(rules)
        # blood_type and nhs_number should be valid examples, not "sample_value"
        assert valid["blood_type"] == "A+"
        assert valid["nhs_number"] == "123-456-7890"
        assert invalid["blood_type"] == "INVALID"

    def test_build_valid_from_regex_extracts_eg(self):
        """_build_valid_from_regex extracts first 'e.g.' token (stops at space/comma/paren)."""
        assert _build_valid_from_regex(".*", "must be a valid code (e.g. GB, US)") == "GB"
        result = _build_valid_from_regex(".*", "e.g. hello world")
        assert result == "hello"

    def test_build_valid_from_regex_eg_placeholder_x(self):
        """X-placeholder hints are converted to digit strings."""
        result = _build_valid_from_regex(r"^ADV-[0-9]{6,10}$", "e.g. ADV-XXXXXXXX")
        assert result == "ADV-11111111"

    def test_build_valid_from_regex_enum_extraction(self):
        """Enum-style patterns return the first alternative."""
        assert _build_valid_from_regex(r"^(CLASSIC|DIGITAL|LED)$", "") == "CLASSIC"
        assert _build_valid_from_regex(r"^(CHARGE|CREDIT|ADJUSTMENT)$", "") == "CHARGE"

    def test_build_valid_from_regex_adv_prefix(self):
        """ADV- prefix pattern returns a valid ADV- value."""
        assert _build_valid_from_regex(r"^ADV-[0-9]{6,10}$", "") == "ADV-123456"

    def test_build_valid_from_regex_email_pattern(self):
        """Pattern containing '@' returns an email address."""
        result = _build_valid_from_regex(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$", "")
        assert "@" in result

    def test_build_valid_from_regex_e164_phone(self):
        """E.164 phone pattern returns a valid international number."""
        result = _build_valid_from_regex(r"^\+[1-9][0-9]{7,14}$", "")
        assert result.startswith("+")
        assert " " not in result  # strict E.164: no spaces

    def test_build_valid_from_regex_e164_optional_plus(self):
        r"""^\+?[1-9]\d{1,14}$ (customer template) -> strict no-space number."""
        result = _build_valid_from_regex(r"^\+?[1-9]\d{1,14}$", "")
        assert result.startswith("+")
        assert " " not in result

    def test_build_valid_from_regex_date_pattern(self):
        """YYYY-MM-DD regex (universal_benchmark created_date) → date string."""
        result = _build_valid_from_regex(
            r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$", "")
        assert result == "1990-06-15"

    def test_build_valid_from_regex_iso_datetime_pattern(self):
        """ISO 8601 datetime regex (proof_of_play impression_start) → datetime string."""
        result = _build_valid_from_regex(
            r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})$",
            "")
        assert "T" in result
        assert result.startswith("1990-06-15T")

    def test_build_valid_from_regex_postcode_error_msg(self):
        """Pattern with 'postcode' in error message returns a UK postcode."""
        result = _build_valid_from_regex(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$", "must be a valid postcode")
        assert result == "SW1A 1AA"

    def test_build_valid_from_regex_fallback(self):
        """_build_valid_from_regex returns 'SAMPLE' when no e.g. hint."""
        assert _build_valid_from_regex(".*", "must be a valid value") == "SAMPLE"

    def test_not_empty_first_name_delegates(self):
        """not_empty rule on first_name field returns 'Alice'."""
        valid, invalid = build_sample_records_from_rules([{"field": "first_name", "type": "not_empty"}])
        assert valid["first_name"] == "Alice"
        assert invalid["first_name"] == ""

    def test_not_empty_last_name_delegates(self):
        """not_empty rule on last_name field returns 'Smith'."""
        valid, _ = build_sample_records_from_rules([{"field": "last_name", "type": "not_empty"}])
        assert valid["last_name"] == "Smith"


# ── OnboardingWizard (unit — no real HTTP, no real subprocess) ─────────────────

class TestWizardResult:
    def test_defaults(self):
        r = WizardResult()
        assert r.entity == ""
        assert r.fields == []
        assert r.contract_path is None
        assert r.elapsed == 0.0
        assert r.success is False


class TestOnboardingWizardUnit:
    """Unit tests for wizard internals — all I/O is mocked."""

    # ── infer / contract helpers are already tested; here we test the wizard ──

    def _make_wizard(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        return wiz

    def test_has_docker_true(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert wiz._has_docker() is True

    def test_has_docker_false_on_nonzero(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert wiz._has_docker() is False

    def test_has_docker_false_on_not_found(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert wiz._has_docker() is False

    def test_health_ok_true(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert wiz._health_ok() is True

    def test_health_ok_false_on_error(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            assert wiz._health_ok() is False

    def test_start_uvicorn_starts_process(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("opendqv.core.onboarding._read_api_lock", return_value=None),
            patch("opendqv.core.onboarding._write_api_lock") as mock_write,
            patch("urllib.request.urlopen", side_effect=OSError),
            patch.object(wiz, "_find_free_port", return_value=8000),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_uvicorn()
            assert result is True
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert "uvicorn" in args
            mock_write.assert_called_once_with(12345, 8000)
            assert wiz._base_url == "http://localhost:8000"

    def test_start_uvicorn_returns_false_on_error(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch("opendqv.core.onboarding._read_api_lock", return_value=None),
            patch("urllib.request.urlopen", side_effect=OSError),
            patch.object(wiz, "_find_free_port", return_value=8000),
            patch("subprocess.Popen", side_effect=FileNotFoundError),
        ):
            assert wiz._start_uvicorn() is False

    def test_start_uvicorn_reuses_existing_api(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch("opendqv.core.onboarding._read_api_lock", return_value=(99999, 8000)),
            patch("subprocess.Popen") as mock_popen,
        ):
            result = wiz._start_uvicorn()
            assert result is True
            mock_popen.assert_not_called()
            assert wiz._base_url == "http://localhost:8000"

    def test_start_uvicorn_foreign_process_on_port(self, tmp_path):
        """Foreign process on 8000 — wizard finds free port 8001 and spawns there."""
        wiz = self._make_wizard(tmp_path)
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b"<html>Some other app</html>"
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        with (
            patch("opendqv.core.onboarding._read_api_lock", return_value=None),
            patch("opendqv.core.onboarding._write_api_lock") as mock_write,
            patch("urllib.request.urlopen", return_value=mock_resp),
            patch.object(wiz, "_find_free_port", return_value=8001),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_uvicorn()
            assert result is True
            mock_popen.assert_called_once()
            mock_write.assert_called_once_with(55555, 8001)
            assert wiz._base_url == "http://localhost:8001"

    def test_start_uvicorn_dead_pid_respawns(self, tmp_path):
        """Lock file has a dead PID (_read_api_lock returns None) — spawn fresh."""
        wiz = self._make_wizard(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 44444
        with (
            patch("opendqv.core.onboarding._read_api_lock", return_value=None),
            patch("opendqv.core.onboarding._write_api_lock") as mock_write,
            patch("urllib.request.urlopen", side_effect=OSError),
            patch.object(wiz, "_find_free_port", return_value=8000),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_uvicorn()
            assert result is True
            mock_popen.assert_called_once()
            mock_write.assert_called_once_with(44444, 8000)
            assert wiz._base_url == "http://localhost:8000"

    def test_find_free_port_returns_preferred_when_available(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        # The socket is released — _find_free_port should claim it as preferred
        result = wiz._find_free_port(free_port)
        assert result == free_port

    def test_find_free_port_skips_occupied_port(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            busy_port = occupied.getsockname()[1]
            result = wiz._find_free_port(busy_port)
        assert result != busy_port

    def test_start_streamlit_spawns_process(self, tmp_path):
        """No lock file and port 8501 is free — Streamlit should be spawned on it."""
        wiz = self._make_wizard(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("opendqv.core.onboarding._read_workbench_lock", return_value=None),
            patch("opendqv.core.onboarding._write_workbench_lock") as mock_write,
            patch.object(wiz, "_find_free_port", return_value=8501),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_streamlit()
            assert result == 8501
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert "streamlit" in args
            assert "ui/app.py" in args
            assert "8501" in args
            mock_write.assert_called_once_with(12345, 8501)

    def test_start_streamlit_returns_none_on_error(self, tmp_path):
        """No lock file, port 8501 is free, but Popen fails — return None."""
        wiz = self._make_wizard(tmp_path)
        with (
            patch("opendqv.core.onboarding._read_workbench_lock", return_value=None),
            patch("opendqv.core.onboarding._write_workbench_lock"),
            patch.object(wiz, "_find_free_port", return_value=8501),
            patch("subprocess.Popen", side_effect=FileNotFoundError),
        ):
            assert wiz._start_streamlit() is None

    def test_start_streamlit_reuses_existing_workbench(self, tmp_path):
        """Lock file records a live PID — return stored port, don't spawn."""
        wiz = self._make_wizard(tmp_path)
        with (
            patch("opendqv.core.onboarding._read_workbench_lock", return_value=(99999, 8501)),
            patch("subprocess.Popen") as mock_popen,
        ):
            result = wiz._start_streamlit()
            assert result == 8501
            mock_popen.assert_not_called()

    def test_start_streamlit_foreign_process_on_preferred_port(self, tmp_path):
        """No live lock, port 8501 occupied by foreign — spawn on next free port (8502)."""
        wiz = self._make_wizard(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 22222
        with (
            patch("opendqv.core.onboarding._read_workbench_lock", return_value=None),
            patch("opendqv.core.onboarding._write_workbench_lock") as mock_write,
            patch.object(wiz, "_find_free_port", return_value=8502),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_streamlit()
            assert result == 8502
            mock_popen.assert_called_once()
            mock_write.assert_called_once_with(22222, 8502)

    def test_start_streamlit_dead_pid_respawns(self, tmp_path):
        """Lock file has a dead PID — spawn fresh process and rewrite lock."""
        wiz = self._make_wizard(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 33333
        with (
            patch("opendqv.core.onboarding._read_workbench_lock", return_value=None),
            patch("opendqv.core.onboarding._write_workbench_lock") as mock_write,
            patch.object(wiz, "_find_free_port", return_value=8501),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_streamlit()
            assert result == 8501
            mock_popen.assert_called_once()
            mock_write.assert_called_once_with(33333, 8501)

    def test_start_streamlit_called_on_python_path(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True) as mock_uvicorn,
            patch.object(OnboardingWizard, "_start_streamlit", return_value=8501) as mock_streamlit,
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", return_value={"valid": True, "errors": [], "warnings": []}),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            wiz.run()
        mock_uvicorn.assert_called_once()
        mock_streamlit.assert_called_once()

    def test_start_streamlit_port_shown_in_next_steps(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_start_streamlit", return_value=8503),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", return_value={"valid": True, "errors": [], "warnings": []}),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                wiz.run()
        assert "8503" in buf.getvalue()

    def test_start_docker_copies_env_example(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        env_example = Path(".env.example")
        env_target = Path(".env")
        if env_target.exists():
            env_target.unlink()
        env_example_created = not env_example.exists()
        if env_example_created:
            env_example.write_text("SECRET_KEY=test\n")
        try:
            with patch("subprocess.Popen"):
                wiz._start_docker()
        finally:
            if env_example_created:
                env_example.unlink(missing_ok=True)
            env_target.unlink(missing_ok=True)

    def test_validate_success(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        response_data = json.dumps({"valid": True, "errors": [], "warnings": []}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = response_data
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = wiz._validate("customer", {"email": "a@b.com"})
        assert result["valid"] is True

    def test_validate_http_error_returns_body(self, tmp_path):
        import urllib.error
        wiz = self._make_wizard(tmp_path)
        error_body = json.dumps({"valid": False, "errors": [{"field": "email", "message": "bad"}]}).encode()
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            url="", code=422, msg="Unprocessable", hdrs=None, fp=MagicMock(read=lambda: error_body)
        )):
            result = wiz._validate("customer", {"email": "bad"})
        assert result["valid"] is False

    def test_docker_path_does_not_call_start_streamlit(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=True),
            patch.object(OnboardingWizard, "_start_docker", return_value=True),
            patch.object(OnboardingWizard, "_start_streamlit") as mock_streamlit,
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", return_value={"valid": True, "errors": [], "warnings": []}),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            wiz.run()
        mock_streamlit.assert_not_called()

    def test_docker_path_shows_8501_in_next_steps(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=True),
            patch.object(OnboardingWizard, "_start_docker", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", return_value={"valid": True, "errors": [], "warnings": []}),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                wiz.run()
        output = buf.getvalue()
        assert "8501" in output
        assert "localhost:8501" in output


class TestReadApiLock:
    """_read_api_lock() returns (pid, port) or None."""

    def test_missing_file_returns_none(self, tmp_path):
        lock_path = tmp_path / ".opendqv_api.lock"
        with patch("opendqv.core.onboarding._API_LOCK", lock_path):
            assert _read_api_lock() is None

    def test_dead_pid_returns_none(self, tmp_path):
        lock_path = tmp_path / ".opendqv_api.lock"
        lock_path.write_text(json.dumps({"pid": 99999999, "port": 8000}))
        with patch("opendqv.core.onboarding._API_LOCK", lock_path):
            assert _read_api_lock() is None

    def test_alive_pid_returns_tuple(self, tmp_path):
        lock_path = tmp_path / ".opendqv_api.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 8000}))
        with patch("opendqv.core.onboarding._API_LOCK", lock_path):
            result = _read_api_lock()
            assert result == (os.getpid(), 8000)


class TestOnboardingWizardRun:
    """Integration-level run() tests — stdin and service I/O are mocked."""

    def _mock_health_always_ok(self):
        """Return a patch that makes _wait_for_health succeed immediately."""
        return patch.object(OnboardingWizard, "_wait_for_health", return_value=True)

    def _mock_start(self):
        return patch.object(OnboardingWizard, "_start_uvicorn", return_value=True)

    def _mock_docker_unavailable(self):
        return patch.object(OnboardingWizard, "_has_docker", return_value=False)

    def _mock_reload(self):
        return patch.object(OnboardingWizard, "_reload")

    def _mock_validate(self, valid_res=None, invalid_res=None):
        valid_res = valid_res or {"valid": True, "errors": [], "warnings": []}
        invalid_res = invalid_res or {"valid": False, "errors": [{"field": "email", "message": "bad"}], "warnings": []}
        responses = [valid_res, invalid_res]
        return patch.object(OnboardingWizard, "_validate", side_effect=responses)

    def _simulate_run(self, tmp_path, inputs):
        """Run wizard with mocked stdin and service calls (questionary disabled)."""
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            self._mock_docker_unavailable(),
            self._mock_start(),
            self._mock_health_always_ok(),
            self._mock_reload(),
            self._mock_validate(),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=inputs),
        ):
            return wiz.run()

    def test_run_returns_wizard_result(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", "email, name"],
        )
        assert isinstance(result, WizardResult)

    def test_run_success_true(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", "email, name"],
        )
        assert result.success is True

    def test_run_entity_set(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["order", "email, name"],
        )
        assert result.entity == "order"

    def test_run_fields_set(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", "email, name, phone"],
        )
        assert result.fields == ["email", "name", "phone"]

    def test_run_contract_file_created(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", "email, name"],
        )
        assert result.contract_path is not None
        assert result.contract_path.exists()

    def test_run_contract_yaml_valid(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["myentity", "email, name"],
        )
        raw = result.contract_path.read_text()
        doc = yaml.safe_load(raw)
        assert "contract" in doc
        assert doc["contract"]["name"] == "myentity"

    def test_run_elapsed_positive(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", "email, name"],
        )
        assert result.elapsed > 0

    def test_run_entity_normalised(self, tmp_path):
        """Spaces and dashes in entity names are converted to underscores."""
        result = self._simulate_run(
            tmp_path,
            inputs=["my customer", "email"],
        )
        assert result.entity == "my_customer"

    def test_run_default_entity_on_empty_input(self, tmp_path):
        """Empty entity input falls back to 'customer'."""
        result = self._simulate_run(
            tmp_path,
            inputs=["", "email"],
        )
        assert result.entity == "customer"

    def test_run_default_fields_on_empty_input(self, tmp_path):
        result = self._simulate_run(
            tmp_path,
            inputs=["customer", ""],
        )
        assert result.fields == ["email", "name", "phone", "age"]

    def test_run_fails_when_service_does_not_start(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            self._mock_docker_unavailable(),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            result = wiz.run()
        assert result.success is False

    def test_run_fails_when_health_check_times_out(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            self._mock_docker_unavailable(),
            self._mock_start(),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            result = wiz.run()
        assert result.success is False

    def test_run_overwrites_on_y_answer(self, tmp_path):
        """When contract already exists and user answers 'y', it is overwritten."""
        entity = "customer"
        existing = tmp_path / f"{entity}.yaml"
        existing.write_text("contract:\n  name: customer\n")
        # tmp_path has one template (customer); "2" selects "Build my own"
        result = self._simulate_run(
            tmp_path,
            inputs=["2", entity, "email, name", "y"],
        )
        assert result.success is True
        assert result.entity == "customer"

    def test_run_renames_on_n_answer(self, tmp_path):
        """When contract already exists and user answers 'N', entity becomes entity_demo."""
        entity = "customer"
        existing = tmp_path / f"{entity}.yaml"
        existing.write_text("contract:\n  name: customer\n")
        # tmp_path has one template (customer); "2" selects "Build my own"
        result = self._simulate_run(
            tmp_path,
            inputs=["2", entity, "email, name", "N"],
        )
        assert result.success is True
        assert result.entity == "customer_demo"

    def test_run_validate_exception_returns_failure(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            self._mock_docker_unavailable(),
            self._mock_start(),
            self._mock_health_always_ok(),
            self._mock_reload(),
            patch.object(OnboardingWizard, "_validate", side_effect=Exception("timeout")),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            result = wiz.run()
        assert result.success is False


# ── Wizard UX regression tests ─────────────────────────────────────────────────────

class TestWizardUXRegressions:
    """Verify wizard UX improvements are preserved."""

    def test_entity_prompt_plain_english(self, tmp_path):
        """ACT-007: wizard prompt no longer uses jargon word 'Entity name'."""
        captured = []
        original_print = print

        def capturing_print(*args, **kwargs):
            captured.append(" ".join(str(a) for a in args))
            original_print(*args, **kwargs)

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [{"field": "email", "message": "bad"}], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["student", "email, name"]),
            patch("builtins.print", side_effect=capturing_print),
        ):
            wiz.run()

        all_output = "\n".join(captured)
        assert "What type of data are you validating" in all_output
        assert "Entity name" not in all_output

    def test_uvicorn_message_plain_english(self, tmp_path):
        """ACT-008: uvicorn fallback message explains what uvicorn is."""
        captured = []
        original_print = print

        def capturing_print(*args, **kwargs):
            captured.append(" ".join(str(a) for a in args))
            original_print(*args, **kwargs)

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
            patch("builtins.print", side_effect=capturing_print),
        ):
            wiz.run()

        all_output = "\n".join(captured)
        assert "built-in web server" in all_output
        # Old bare message must not appear
        assert "Docker not found — using local uvicorn" not in all_output

    def test_next_steps_contract_before_api_docs(self, tmp_path):
        """ACT-009: contract edit line appears before API docs URL in next steps."""
        captured = []
        original_print = print

        def capturing_print(*args, **kwargs):
            captured.append(" ".join(str(a) for a in args))
            original_print(*args, **kwargs)

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
            patch("builtins.print", side_effect=capturing_print),
        ):
            wiz.run()

        all_output = "\n".join(captured)
        contract_pos = all_output.find("contracts/")
        docs_pos = all_output.find("/docs")
        assert contract_pos != -1
        assert docs_pos != -1
        assert contract_pos < docs_pos, "Contract edit line should appear before API docs URL"

    def test_next_steps_reload_shows_cli_command(self, tmp_path):
        """ACT-009: next steps shows curl reload command."""
        captured = []
        original_print = print

        def capturing_print(*args, **kwargs):
            captured.append(" ".join(str(a) for a in args))
            original_print(*args, **kwargs)

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
            patch("builtins.print", side_effect=capturing_print),
        ):
            wiz.run()

        all_output = "\n".join(captured)
        assert "curl -X POST" in all_output
        assert "contracts/reload" in all_output

    def test_next_steps_writes_session_file(self, tmp_path):
        """Wizard writes .opendqv_session in the system temp dir after onboarding."""
        import json
        import pathlib
        import tempfile
        session_file = pathlib.Path(tempfile.gettempdir()) / ".opendqv_session"
        # Remove any leftover file before test
        if session_file.exists():
            session_file.unlink()

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email"]),
        ):
            wiz.run()

        assert session_file.exists(), "Session file should be written after wizard completes"
        data = json.loads(session_file.read_text())
        assert data["contract"] == "customer"

    def test_boolean_sample_record_valid(self, tmp_path):
        """ACT-010: boolean fields get a valid truthy value in sample records."""
        valid, invalid = build_sample_records(["available"])
        assert valid["available"] in ("true", "false", "yes", "no", "1", "0", "Y", "N", True, False)

    def test_boolean_infer_rule(self, tmp_path):
        """ACT-010: available/active/enabled infer as regex, not not_empty."""
        for fname in ("available", "active", "enabled"):
            rule = infer_rule(fname)
            assert rule["type"] == "regex", f"{fname} should infer as regex"

    def test_student_entity_fields_infer_correctly(self, tmp_path):
        """Scenario: student fields from Mia's session all infer correctly."""
        checks = {
            "student_id":      "not_empty",   # _id suffix
            "name":            "not_empty",
            "email":           "regex",
            "dob":             "date_format",
            "course":          "not_empty",
            "gpa":             "range",
        }
        for fname, expected_type in checks.items():
            rule = infer_rule(fname)
            assert rule["type"] == expected_type, (
                f"Expected {fname} → {expected_type}, got {rule['type']}"
            )

    def test_library_book_published_date_infers_date(self):
        """Scenario: library_book fields — published_date infers as date_format."""
        assert infer_rule("published_date")["type"] == "date_format"

    def test_patient_date_fields_infer_correctly(self):
        """Scenario: patient fields — both date fields infer correctly."""
        assert infer_rule("patient_dob")["type"] == "date_format"
        assert infer_rule("admission_date")["type"] == "date_format"


# ── Wizard output regression tests ─────────────────────────────────────────────────────

class TestWizardOutputRegressions:
    """ACT-020 — verify questionary integration: select, text, confirm, fallback."""

    import contextlib as _cl

    def _run_with_q(self, tmp_path, setup_mock_q, *, extra_patches=None):
        """Helper: run wizard with questionary enabled and a mock questionary module.

        setup_mock_q(mock_q) is called to configure the mock before run().
        Returns (result, mock_q).
        """
        import contextlib
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        base = [
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [{"field": "e", "message": "bad"}], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", True),
        ]
        if extra_patches:
            base.extend(extra_patches)

        with contextlib.ExitStack() as stack:
            for ctx in base:
                stack.enter_context(ctx)
            mock_q = stack.enter_context(patch("opendqv.core.onboarding.questionary"))
            setup_mock_q(mock_q)
            result = wiz.run()
        return result, mock_q

    def test_questionary_select_template_chosen(self, tmp_path):
        """ACT-016: questionary.select() returns a template dict — wizard uses it."""
        tmpl = tmp_path / "customer.yaml"
        tmpl.write_text("contract:\n  name: customer\n  rules:\n    - name: email_regex\n      field: email\n      type: regex\n      pattern: '.*'\n      severity: error\n      error_message: 'bad'\n")

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        templates = wiz._list_templates()
        assert len(templates) == 1
        chosen_template = templates[0]

        def setup(mock_q):
            mock_q.Choice = MagicMock(side_effect=lambda title, value: MagicMock(title=title, value=value))
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value=chosen_template))

        result, _ = self._run_with_q(tmp_path, setup)
        assert result.entity == "customer"
        assert result.success is True

    def test_questionary_select_build_own_sentinel(self, tmp_path):
        """ACT-016: choosing _BUILD_OWN sentinel falls through to custom field entry."""
        import opendqv.core.onboarding as mod
        tmpl = tmp_path / "customer.yaml"
        tmpl.write_text("contract:\n  name: customer\n  rules:\n    - name: r\n      field: email\n      type: not_empty\n      severity: error\n      error_message: 'req'\n")

        mock_text = MagicMock()
        mock_text.ask.side_effect = ["order", "email, name"]

        def setup(mock_q):
            mock_q.Choice = MagicMock(side_effect=lambda title, value: MagicMock(title=title, value=value))
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value=mod._BUILD_OWN))
            mock_q.text.return_value = mock_text

        result, _ = self._run_with_q(tmp_path, setup)
        assert result.entity == "order"
        assert result.success is True

    def test_questionary_text_used_for_entity_and_fields(self, tmp_path):
        """ACT-017: questionary.text() is called for entity and fields prompts."""
        mock_text = MagicMock()
        mock_text.ask.side_effect = ["myentity", "email, name"]

        def setup(mock_q):
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value=None))
            mock_q.text.return_value = mock_text

        _, mock_q = self._run_with_q(tmp_path, setup)
        assert mock_q.text.call_count >= 2

    def test_questionary_confirm_used_for_overwrite(self, tmp_path):
        """ACT-018: questionary.confirm() used when contract file already exists."""
        existing = tmp_path / "myent.yaml"
        existing.write_text("contract:\n  name: myent\n")

        mock_text = MagicMock()
        mock_text.ask.side_effect = ["myent", "email, name"]
        mock_confirm = MagicMock()
        mock_confirm.ask.return_value = True  # overwrite

        def setup(mock_q):
            # _BUILD_OWN so wizard falls through to custom field entry,
            # then "myent" entity triggers overwrite confirm
            mock_q.Choice = MagicMock(side_effect=lambda title, value: MagicMock(title=title, value=value))
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value="__build_own__"))
            mock_q.text.return_value = mock_text
            mock_q.confirm.return_value = mock_confirm

        result, mock_q = self._run_with_q(tmp_path, setup)
        mock_q.confirm.assert_called_once()
        assert result.entity == "myent"

    def test_questionary_confirm_rename_on_false(self, tmp_path):
        """ACT-018: confirm() returning False renames entity to entity_demo."""
        existing = tmp_path / "myent.yaml"
        existing.write_text("contract:\n  name: myent\n")

        mock_text = MagicMock()
        mock_text.ask.side_effect = ["myent", "email, name"]
        mock_confirm = MagicMock()
        mock_confirm.ask.return_value = False  # decline overwrite

        def setup(mock_q):
            mock_q.Choice = MagicMock(side_effect=lambda title, value: MagicMock(title=title, value=value))
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value="__build_own__"))
            mock_q.text.return_value = mock_text
            mock_q.confirm.return_value = mock_confirm

        result, _ = self._run_with_q(tmp_path, setup)
        assert result.entity == "myent_demo"

    def test_fallback_when_questionary_absent(self, tmp_path):
        """ACT-019: wizard degrades to numbered list when questionary is not installed."""
        import contextlib
        captured_prints = []
        orig_print = print

        def cap(*args, **kwargs):
            captured_prints.append(" ".join(str(a) for a in args))
            orig_print(*args, **kwargs)

        tmpl = tmp_path / "customer.yaml"
        tmpl.write_text("contract:\n  name: customer\n  rules:\n    - name: r\n      field: email\n      type: not_empty\n      severity: error\n      error_message: 'req'\n")

        wiz = OnboardingWizard(contracts_dir=tmp_path)
        contexts = [
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_validate", side_effect=[
                {"valid": True, "errors": [], "warnings": []},
                {"valid": False, "errors": [], "warnings": []},
            ]),
            patch("opendqv.core.onboarding.HAS_RICH", False),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["2", "order", "email, name"]),
            patch("builtins.print", side_effect=cap),
        ]
        with contextlib.ExitStack() as stack:
            for ctx in contexts:
                stack.enter_context(ctx)
            result = wiz.run()

        all_output = "\n".join(captured_prints)
        assert "1 " in all_output or "  1 " in all_output
        assert result.success is True

    def test_has_questionary_flag_importable(self):
        """ACT-014: HAS_QUESTIONARY flag is importable from opendqv.core.onboarding."""
        from opendqv.core.onboarding import HAS_QUESTIONARY
        assert isinstance(HAS_QUESTIONARY, bool)

    def test_wizard_style_defined_when_questionary_available(self):
        """ACT-015: WIZARD_STYLE is not None when questionary is importable."""
        import opendqv.core.onboarding as mod
        if mod.HAS_QUESTIONARY:
            assert mod.WIZARD_STYLE is not None

    def test_questionary_search_filter_enabled(self, tmp_path):
        """ACT-016: questionary.select() is called with use_search_filter=True."""
        tmpl = tmp_path / "customer.yaml"
        tmpl.write_text("contract:\n  name: customer\n  rules:\n    - name: r\n      field: email\n      type: not_empty\n      severity: error\n      error_message: 'req'\n")

        mock_text = MagicMock()
        mock_text.ask.side_effect = ["order", "email"]

        def setup(mock_q):
            mock_q.Choice = MagicMock(side_effect=lambda title, value: MagicMock(title=title, value=value))
            mock_q.select.return_value = MagicMock(ask=MagicMock(return_value="__build_own__"))
            mock_q.text.return_value = mock_text

        _, mock_q = self._run_with_q(tmp_path, setup)
        _, kwargs = mock_q.select.call_args
        assert kwargs.get("use_search_filter") is True


# ── CLI onboard command ────────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# TestReadWorkbenchLock — lines 87–96 (parallel to TestReadApiLock)
# ---------------------------------------------------------------------------

class TestReadWorkbenchLock:
    """_read_workbench_lock() returns (pid, port) or None."""

    def test_missing_file_returns_none(self, tmp_path):
        lock_path = tmp_path / ".opendqv_workbench.lock"
        with patch("opendqv.core.onboarding._WORKBENCH_LOCK", lock_path):
            assert _read_workbench_lock() is None

    def test_dead_pid_returns_none(self, tmp_path):
        lock_path = tmp_path / ".opendqv_workbench.lock"
        lock_path.write_text(json.dumps({"pid": 99999999, "port": 8501}))
        with patch("opendqv.core.onboarding._WORKBENCH_LOCK", lock_path):
            assert _read_workbench_lock() is None  # line 93

    def test_exception_returns_none(self, tmp_path):
        lock_path = tmp_path / ".opendqv_workbench.lock"
        lock_path.write_text("not-valid-json")
        with patch("opendqv.core.onboarding._WORKBENCH_LOCK", lock_path):
            assert _read_workbench_lock() is None  # lines 95–96

    def test_alive_pid_returns_tuple(self, tmp_path):
        lock_path = tmp_path / ".opendqv_workbench.lock"
        lock_path.write_text(json.dumps({"pid": os.getpid(), "port": 8501}))
        with patch("opendqv.core.onboarding._WORKBENCH_LOCK", lock_path):
            result = _read_workbench_lock()
            assert result == (os.getpid(), 8501)


# ---------------------------------------------------------------------------
# TestWriteLockFunctions — lines 101, 121
# ---------------------------------------------------------------------------

class TestWriteLockFunctions:
    """_write_workbench_lock and _write_api_lock write JSON to their respective paths."""

    def test_write_workbench_lock(self, tmp_path):
        lock_path = tmp_path / ".opendqv_workbench.lock"
        with patch("opendqv.core.onboarding._WORKBENCH_LOCK", lock_path):
            _write_workbench_lock(12345, 8501)  # line 101
        data = json.loads(lock_path.read_text())
        assert data == {"pid": 12345, "port": 8501}

    def test_write_api_lock(self, tmp_path):
        lock_path = tmp_path / ".opendqv_api.lock"
        with patch("opendqv.core.onboarding._API_LOCK", lock_path):
            _write_api_lock(99, 9000)  # line 121
        data = json.loads(lock_path.read_text())
        assert data == {"pid": 99, "port": 9000}


# ---------------------------------------------------------------------------
# TestLoadFirstLookupValue — lines 426–445
# ---------------------------------------------------------------------------

class TestLoadFirstLookupValue:
    """_load_first_lookup_value() returns first non-comment line or field-name fallback."""

    def test_file_found_returns_first_line(self, tmp_path):
        lookup = tmp_path / "gender.txt"
        lookup.write_text("MALE\nFEMALE\n")
        with patch("opendqv.core.onboarding.Path") as mock_path_cls:
            # Patch Path(".") to return tmp_path so "." / "gender.txt" resolves correctly
            real_path = __import__("pathlib").Path
            def _path_side(arg=""):
                if arg == ".":
                    return tmp_path
                return real_path(arg)
            mock_path_cls.side_effect = _path_side
            result = _load_first_lookup_value("gender.txt", "gender")
        assert result == "MALE"

    def test_file_with_comments_skips_to_first_real_line(self, tmp_path):
        lookup = tmp_path / "status.txt"
        lookup.write_text("# comment\n\nACTIVE\nINACTIVE\n")
        # Use contracts subdir
        (tmp_path / "contracts").mkdir()
        (tmp_path / "contracts" / "status.txt").write_text("# comment\n\nACTIVE\n")
        import pathlib
        real_path = pathlib.Path
        with patch("opendqv.core.onboarding.Path") as mock_path_cls:
            call_count = [0]
            def _path_side(arg=""):
                call_count[0] += 1
                if arg == "." and call_count[0] == 1:
                    return tmp_path / "nonexistent_dir"  # OSError on first base
                if arg == "contracts":
                    return tmp_path / "contracts"
                return real_path(arg)
            mock_path_cls.side_effect = _path_side
            result = _load_first_lookup_value("status.txt", "status")
        assert result == "ACTIVE"  # line 432

    def test_no_file_found_fallback_verified(self):
        result = _load_first_lookup_value("nonexistent_file_xyz.txt", "is_verified")
        assert result == "TRUE"  # line 438

    def test_no_file_found_fallback_method(self):
        result = _load_first_lookup_value("nonexistent_file_xyz.txt", "payment_method")
        assert result == "DOCUMENT_CHECK"  # line 440

    def test_no_file_found_fallback_status(self):
        result = _load_first_lookup_value("nonexistent_file_xyz.txt", "kyc_status")
        assert result == "ACTIVE"  # line 442

    def test_no_file_found_fallback_currency(self):
        result = _load_first_lookup_value("nonexistent_file_xyz.txt", "currency_code")
        assert result == "GBP"  # line 444

    def test_empty_lookup_file_uses_fallback(self):
        result = _load_first_lookup_value("", "unknown_field")
        assert result == "VALID"  # line 445


# ---------------------------------------------------------------------------
# TestBuildValidFromRegexMissingBranches — lines 409, 414, 416, 418
# ---------------------------------------------------------------------------

class TestBuildValidFromRegexMissingBranches:
    """Cover the branches in _build_valid_from_regex not yet exercised."""

    def test_loose_phone_pattern(self):
        # Pattern with r'\+?' and r'[\d\s' → loose phone → line 409 branch
        result = _build_valid_from_regex(r"^\+?[\d\s\-]{10,15}$", "must be a phone number")
        assert result == "+44 7911 123456"  # line 409 branch

    def test_postcode_keyword_in_error_message(self):
        # No structural match but 'postcode' in error message → line 414
        result = _build_valid_from_regex(r"^[A-Z0-9 ]+$", "must be a valid postcode or postal code")
        assert result == "SW1A 1AA"

    def test_postal_keyword_in_error_message(self):
        result = _build_valid_from_regex(r"^[A-Z0-9 ]+$", "enter your postal code")
        assert result == "SW1A 1AA"

    def test_phone_keyword_in_error_message(self):
        # 'phone' in msg_lower → line 416
        result = _build_valid_from_regex(r"^[0-9]+$", "must be a valid phone number")
        assert result == "+44 7911 123456"

    def test_mobile_keyword_in_error_message(self):
        result = _build_valid_from_regex(r"^[0-9]+$", "must be a valid mobile number")
        assert result == "+44 7911 123456"

    def test_msisdn_keyword_in_error_message(self):
        result = _build_valid_from_regex(r"^[0-9]+$", "must be a valid msisdn")
        assert result == "+44 7911 123456"

    def test_email_keyword_in_error_message(self):
        # 'email' in msg_lower → line 418
        # Pattern must NOT contain '@' (structural '@' check fires first and returns early)
        result = _build_valid_from_regex(r"^[A-Za-z0-9_]+$", "must be a valid email address")
        assert result == "alice@example.com"


# ---------------------------------------------------------------------------
# TestBuildSampleRecordsMissingFields — lines 557, 565, 569
# ---------------------------------------------------------------------------

class TestBuildSampleRecordsMissingFields:
    """Cover build_sample_records field branches not yet exercised."""

    def test_country_field(self):
        valid, _ = build_sample_records(["country_code"])
        assert valid["country_code"] == "GB"  # line 557

    def test_colour_field(self):
        valid, _ = build_sample_records(["favourite_colour"])
        assert valid["favourite_colour"] == "Blue"  # line 565

    def test_color_field(self):
        valid, _ = build_sample_records(["text_color"])
        assert valid["text_color"] == "Blue"

    def test_status_suffix(self):
        valid, _ = build_sample_records(["order_status"])
        assert valid["order_status"] == "ACTIVE"  # line 569


# ---------------------------------------------------------------------------
# TestBuildSampleRecordsFromRulesMissingTypes — lines 490, 495–496, 498–499, 515–523
# ---------------------------------------------------------------------------

class TestBuildSampleRecordsFromRulesMissingTypes:
    """Cover rule types not yet exercised in build_sample_records_from_rules."""

    def test_max_rule(self):
        valid, invalid = build_sample_records_from_rules([
            {"field": "quantity", "type": "max", "max": 50}
        ])
        assert valid["quantity"] == 50   # line 495
        assert invalid["quantity"] == 51  # line 496

    def test_min_length_rule(self):
        valid, invalid = build_sample_records_from_rules([
            {"field": "reference", "type": "min_length", "min_length": 5}
        ])
        assert valid["reference"] == "AAAAA"   # line 498
        assert invalid["reference"] == ""       # line 499

    def test_lookup_rule(self):
        valid, invalid = build_sample_records_from_rules([
            {"field": "kyc_status", "type": "lookup", "lookup_file": "nonexistent_xyz.txt"}
        ])
        assert valid["kyc_status"] == "ACTIVE"  # fallback via _load_first_lookup_value
        assert invalid["kyc_status"] == "INVALID_VALUE"

    def test_min_quantity_field(self):
        # quantity/qty/count/volume/units path → max(10, min_val + 1) — line 490
        valid, invalid = build_sample_records_from_rules([
            {"field": "quantity", "type": "min", "min": 1}
        ])
        assert valid["quantity"] == 10   # max(10, 1+1) = 10

    def test_age_match_cross_field_fix(self):
        # age_match post-process recalculates age from dob — lines 515–521
        rules = [
            {"field": "dob", "type": "date_format", "format": "%Y-%m-%d"},
            {"field": "age", "type": "range", "min": 0, "max": 120},
            {"field": "age", "type": "age_match", "dob_field": "dob"},
        ]
        valid, _ = build_sample_records_from_rules(rules)
        # dob is "1990-06-15"; age should be recalculated from dob
        from datetime import date
        dob = date.fromisoformat(valid["dob"])
        expected_age = (date.today() - dob).days // 365
        assert valid["age"] == expected_age  # line 521

    def test_age_match_invalid_dob_doesnt_crash(self):
        # ValueError/TypeError in age_match fix → lines 522–523 (pass)
        rules = [
            {"field": "dob", "type": "not_empty"},   # valid["dob"] = "sample_value" (non-date)
            {"field": "age", "type": "age_match", "dob_field": "dob"},
        ]
        valid, _ = build_sample_records_from_rules(rules)  # must not raise
        assert "dob" in valid

    def test_non_dict_rule_skipped(self):
        # line 457–458: non-dict entries in rules list are skipped
        valid, _ = build_sample_records_from_rules(["not-a-dict", {"field": "name", "type": "not_empty"}])
        assert "name" in valid

    def test_rule_without_field_skipped(self):
        # line 459–461: rules with no field key are skipped
        valid, _ = build_sample_records_from_rules([{"type": "not_empty"}])
        assert valid == {}


# ---------------------------------------------------------------------------
# TestGenerateContractYamlMinLength — line 365
# ---------------------------------------------------------------------------

class TestGenerateContractYamlMinLength:
    """generate_contract_yaml includes min_length key for min_length rule type."""

    def test_min_length_field_in_yaml(self):
        # Trigger line 365: infer_rule("country") returns min_length type
        yaml_str = generate_contract_yaml("test", ["country"])
        # min_length key should appear in the output
        assert "min_length" in yaml_str  # line 365


# ---------------------------------------------------------------------------
# TestListTemplatesEdgeCases — lines 1030, 1034, 1038, 1055–1056
# ---------------------------------------------------------------------------

class TestListTemplatesEdgeCases:
    """_list_templates() edge cases when contracts dir is missing or has bad YAML."""

    def test_missing_contracts_dir_returns_empty(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path / "nonexistent")
        result = wiz._list_templates()
        assert result == []  # line 1030

    def test_excluded_template_is_skipped(self, tmp_path):
        import opendqv.core.onboarding as _ob
        excluded = list(_ob._EXCLUDED_TEMPLATES)
        if not excluded:
            pytest.skip("No excluded templates defined")
        (tmp_path / f"{excluded[0]}.yaml").write_text("contract:\n  name: skip_me\n  rules: []\n")
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        result = wiz._list_templates()
        names = [t["name"] for t in result]
        assert excluded[0] not in names  # line 1034

    def test_yaml_with_null_data_skipped(self, tmp_path):
        (tmp_path / "empty.yaml").write_text("")  # safe_load returns None
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        result = wiz._list_templates()
        assert all(t["name"] != "empty" for t in result)  # line 1038

    def test_bad_yaml_silently_skipped(self, tmp_path):
        (tmp_path / "corrupt.yaml").write_text(": invalid: [unclosed")
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        result = wiz._list_templates()  # must not raise; lines 1055–1056
        assert all(t["name"] != "corrupt" for t in result)


# ---------------------------------------------------------------------------
# TestDemoGovernance — lines 858–903
# ---------------------------------------------------------------------------

class TestDemoGovernance:
    """_demo_governance() mocked HTTP paths."""

    def _make_wizard(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        wiz._base_url = "http://localhost:19999"
        return wiz

    def _contract_response(self, status="draft", version="1.0"):
        body = json.dumps({"status": status, "version": version}).encode()
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = body
        mock_resp.status = 200
        return mock_resp

    def test_happy_path_draft_to_active(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        call_count = [0]
        responses = [
            self._contract_response("draft", "1.0"),  # GET contract
            self._contract_response("review", "1.0"),  # POST submit-review
            self._contract_response("active", "1.0"),  # POST approve
        ]
        def _urlopen(req, timeout=5):
            r = responses[call_count[0]]
            call_count[0] += 1
            return r
        with patch("opendqv.core.onboarding.urllib.request.urlopen", side_effect=_urlopen):
            wiz._demo_governance("customer")  # must not raise

    def test_already_active_returns_early(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("opendqv.core.onboarding.urllib.request.urlopen",
                   return_value=self._contract_response("active", "1.0")):
            wiz._demo_governance("customer")  # line 855 → return early

    def test_submit_401_skips_silently(self, tmp_path):
        import urllib.error
        wiz = self._make_wizard(tmp_path)
        call_count = [0]
        def _urlopen(req, timeout=5):
            if call_count[0] == 0:
                call_count[0] += 1
                return self._contract_response("draft", "1.0")
            raise urllib.error.HTTPError(None, 401, "Unauthorized", {}, None)
        with patch("opendqv.core.onboarding.urllib.request.urlopen", side_effect=_urlopen):
            wiz._demo_governance("customer")  # lines 868–869 → return

    def test_approve_403_skips_silently(self, tmp_path):
        import urllib.error
        wiz = self._make_wizard(tmp_path)
        call_count = [0]
        def _urlopen(req, timeout=5):
            n = call_count[0]
            call_count[0] += 1
            if n == 0:
                return self._contract_response("draft", "1.0")
            if n == 1:
                return self._contract_response("review", "1.0")
            raise urllib.error.HTTPError(None, 403, "Forbidden", {}, None)
        with patch("opendqv.core.onboarding.urllib.request.urlopen", side_effect=_urlopen):
            wiz._demo_governance("customer")  # lines 888–889 → return

    def test_exception_is_swallowed(self, tmp_path):
        wiz = self._make_wizard(tmp_path)
        with patch("opendqv.core.onboarding.urllib.request.urlopen", side_effect=Exception("network error")):
            wiz._demo_governance("customer")  # line 905 → pass, no exception raised


# ---------------------------------------------------------------------------
# TestReload — lines 909–915
# ---------------------------------------------------------------------------

class TestReload:
    """_reload() fires a POST and swallows all exceptions."""

    def test_reload_success(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        wiz._base_url = "http://localhost:19999"
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("opendqv.core.onboarding.urllib.request.urlopen", return_value=mock_resp):
            wiz._reload()  # lines 910–913

    def test_reload_exception_swallowed(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        wiz._base_url = "http://localhost:19999"
        with patch("opendqv.core.onboarding.urllib.request.urlopen", side_effect=Exception("timeout")):
            wiz._reload()  # line 914–915, must not raise


# ---------------------------------------------------------------------------
# TestStartDockerMissingBranches — lines 715–716, 725–726
# ---------------------------------------------------------------------------

class TestStartDockerMissingBranches:

    def test_env_example_not_found_no_crash(self, tmp_path):
        # Lines 715–716: .env doesn't exist, .env.example also doesn't exist → FileNotFoundError swallowed
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch("os.path.exists", return_value=False),
            patch("pathlib.Path.exists", return_value=False),
            patch("shutil.copy", side_effect=FileNotFoundError),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock()
            result = wiz._start_docker()
        assert result is True

    def test_popen_file_not_found_returns_false(self, tmp_path):
        # Lines 725–726: docker not installed → FileNotFoundError in Popen
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch("pathlib.Path.exists", return_value=True),  # .env exists — skip copy
            patch("subprocess.Popen", side_effect=FileNotFoundError),
        ):
            result = wiz._start_docker()
        assert result is False  # line 726


# ---------------------------------------------------------------------------
# TestWizardRunInsideDocker — lines 1078, 1201–1203
# ---------------------------------------------------------------------------

class TestWizardRunInsideDocker:
    """Wizard run() when _is_inside_docker() returns True."""

    def test_run_inside_docker_connects_directly(self, tmp_path):
        # Lines 1078, 1201–1203: skip docker-compose, connect to localhost:8000 directly
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=True),
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_demo_governance"),
            patch.object(OnboardingWizard, "_validate",
                         side_effect=[
                             {"valid": True, "errors": [], "warnings": []},
                             {"valid": False, "errors": [{"field": "x", "message": "bad"}], "warnings": []},
                         ]),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email, name"]),
        ):
            result = wiz.run()
        assert wiz._base_url == "http://localhost:8000"  # line 1201
        assert result.success is True


# ---------------------------------------------------------------------------
# TestWizardRunSessionFileException — lines 1020–1021
# ---------------------------------------------------------------------------

class TestWizardRunSessionFileException:
    """_show_next_steps() swallows session file write errors."""

    def test_session_file_write_exception_swallowed(self, tmp_path):
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=False),
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_demo_governance"),
            patch.object(OnboardingWizard, "_validate",
                         side_effect=[
                             {"valid": True, "errors": [], "warnings": []},
                             {"valid": False, "errors": [{"field": "x", "message": "bad"}], "warnings": []},
                         ]),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email, name"]),
            patch("opendqv.core.onboarding._SESSION_FILE",
                  tmp_path / "no_such_dir" / "session.json"),  # unwritable path → exception
        ):
            result = wiz.run()  # must not raise
        assert result.success is True


# ---------------------------------------------------------------------------
# TestWizardRunEnvExists — line 1085
# ---------------------------------------------------------------------------

class TestWizardRunEnvExists:
    """Wizard run() prints info when .env exists."""

    def test_env_exists_info_printed(self, tmp_path):
        (tmp_path / ".env").write_text("# env")
        wiz = OnboardingWizard(contracts_dir=tmp_path)
        with (
            patch.object(OnboardingWizard, "_is_inside_docker", return_value=False),
            patch.object(OnboardingWizard, "_has_docker", return_value=False),
            patch.object(OnboardingWizard, "_start_uvicorn", return_value=True),
            patch.object(OnboardingWizard, "_wait_for_health", return_value=True),
            patch.object(OnboardingWizard, "_reload"),
            patch.object(OnboardingWizard, "_demo_governance"),
            patch.object(OnboardingWizard, "_validate",
                         side_effect=[
                             {"valid": True, "errors": [], "warnings": []},
                             {"valid": False, "errors": [{"field": "x", "message": "bad"}], "warnings": []},
                         ]),
            patch("opendqv.core.onboarding.HAS_QUESTIONARY", False),
            patch("builtins.input", side_effect=["customer", "email, name"]),
            patch("pathlib.Path.exists", lambda p: str(p).endswith(".env")),
        ):
            result = wiz.run()
        assert result.success is True  # line 1085 path was exercised


# ---------------------------------------------------------------------------
# TestCliOnboard — original class kept intact below
# ---------------------------------------------------------------------------

class TestCliOnboard:
    """Smoke test: 'onboard' command is registered in cli.py."""

    def test_onboard_in_cli_commands(self):

        # Import cli module and check that 'onboard' is a known subcommand
        # We patch OnboardingWizard.run to avoid side effects
        with patch("opendqv.core.onboarding.OnboardingWizard.run", return_value=WizardResult(success=True)):
            import opendqv.cli as cli_module
            # Re-reading the source is enough; just ensure the subcommand exists in the
            # commands dict by importing and calling main with onboard arg
            with patch.object(cli_module, "cmd_onboard") as mock_cmd:
                test_args = ["onboard"]
                with patch("sys.argv", ["opendqv"] + test_args):
                    try:
                        cli_module.main()
                    except SystemExit:
                        pass
                mock_cmd.assert_called_once()
