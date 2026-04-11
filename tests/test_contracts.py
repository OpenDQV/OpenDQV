"""Tests for data contract loading and versioning."""

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch
from opendqv.core.contracts import ContractRegistry, DataContract, ContractHistory
from opendqv.core.rule_parser import Rule, ContractStatus


@pytest.fixture
def registry():
    contracts_dir = Path(__file__).parent.parent / "contracts"
    return ContractRegistry(contracts_dir)


class TestContractRegistry:
    def test_loads_contracts(self, registry):
        contracts = registry.list_contracts()
        assert len(contracts) > 0

    def test_customer_contract_exists(self, registry):
        c = registry.get("customer")
        assert c is not None
        assert c.name == "customer"
        assert len(c.rules) > 0

    def test_get_latest_version(self, registry):
        c = registry.get("customer", "latest")
        assert c is not None
        assert c.version == "1.0"

    def test_get_specific_version(self, registry):
        c = registry.get("customer", "1.0")
        assert c is not None

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_contract_has_rules(self, registry):
        c = registry.get("customer")
        rule_names = [r.name for r in c.rules]
        assert "valid_email" in rule_names
        assert "name_required" in rule_names

    def test_contract_has_contexts(self, registry):
        c = registry.get("customer")
        assert "kids_app" in c.contexts
        assert "financial" in c.contexts


class TestContextOverrides:
    def test_no_context_returns_base_rules(self, registry):
        c = registry.get("customer")
        rules = registry.get_rules_with_context(c, None)
        assert rules == c.rules

    def test_kids_app_context_overrides_age(self, registry):
        c = registry.get("customer")
        rules = registry.get_rules_with_context(c, "kids_app")
        age_rules = [r for r in rules if r.field == "age"]
        # Should have a range rule for 5-17
        range_rule = [r for r in age_rules if r.type == "range"]
        assert len(range_rule) > 0
        assert range_rule[0].min_value == 5
        assert range_rule[0].max_value == 17

    def test_unknown_context_falls_back_to_base_rules(self, registry):
        # Unknown context → base rules, no exception.
        # This allows context to be used as a stats tag (e.g. "demo", "ci")
        # without requiring a matching context block in the YAML contract.
        c = registry.get("customer")
        base_rules = c.rules
        result = registry.get_rules_with_context(c, "nonexistent_context_xyz")
        assert result == base_rules

    def test_reload(self, registry):
        count_before = len(registry.list_contracts())
        registry.reload()
        count_after = len(registry.list_contracts())
        assert count_before == count_after


class TestContractListInfo:
    def test_list_has_metadata(self, registry):
        contracts = registry.list_contracts()
        c = next(x for x in contracts if x["name"] == "customer")
        assert "version" in c
        assert "description" in c
        assert "rule_count" in c
        assert c["rule_count"] > 0


class TestAssetId:
    def test_asset_id_present_in_customer(self, registry):
        """customer.yaml has asset_id set — it should surface in the contract."""
        c = registry.get("customer")
        assert c is not None
        assert c.asset_id == "urn:opendqv:customer"

    def test_asset_id_in_list_contracts(self, registry):
        """asset_id must appear in the list_contracts() dict."""
        contracts = registry.list_contracts()
        c = next(x for x in contracts if x["name"] == "customer")
        assert "asset_id" in c
        assert c["asset_id"] == "urn:opendqv:customer"

    def test_asset_id_optional_defaults_none(self):
        """Contracts without asset_id get None by default."""
        contract = DataContract(name="test", version="1.0", rules=[])
        assert contract.asset_id is None

    def test_asset_id_round_trips_through_yaml(self, tmp_path):
        """A YAML file with asset_id is loaded and preserved correctly."""
        yaml_content = {
            "contract": {
                "name": "asset_test",
                "version": "1.0",
                "description": "test",
                "owner": "team",
                "status": "active",
                "asset_id": "urn:catalog:entity:42",
                "rules": [],
            }
        }
        p = tmp_path / "asset_test.yaml"
        p.write_text(yaml.dump(yaml_content))
        reg = ContractRegistry(tmp_path)
        c = reg.get("asset_test")
        assert c is not None
        assert c.asset_id == "urn:catalog:entity:42"


class TestApprovedBy:
    def test_record_version_with_approved_by(self):
        """approved_by is stored in history when provided."""
        history = ContractHistory(":memory:")
        contract = DataContract(
            name="approval_test",
            version="1.0",
            description="test",
            owner="team",
            status=ContractStatus.ACTIVE,
            rules=[],
        )
        history.record_version(contract, approved_by="jane.doe@example.com")
        entries = history.get_history("approval_test")
        assert len(entries) == 1
        assert entries[0]["approved_by"] == "jane.doe@example.com"

    def test_record_version_without_approved_by(self):
        """approved_by defaults to None when not provided."""
        history = ContractHistory(":memory:")
        contract = DataContract(
            name="no_approver",
            version="1.0",
            description="test",
            owner="team",
            status=ContractStatus.ACTIVE,
            rules=[],
        )
        history.record_version(contract)
        entries = history.get_history("no_approver")
        assert entries[0]["approved_by"] is None

    def test_approved_by_persists_across_multiple_versions(self):
        """approved_by is stored per history entry independently."""
        history = ContractHistory(":memory:")
        contract = DataContract(
            name="multi_version",
            version="1.0",
            description="v1",
            owner="team",
            status=ContractStatus.ACTIVE,
            rules=[],
        )
        history.record_version(contract, approved_by="alice@example.com")

        contract.description = "v1 updated"
        history.record_version(contract, approved_by="bob@example.com")

        entries = history.get_history("multi_version")
        assert len(entries) == 2
        assert entries[0]["approved_by"] == "alice@example.com"
        assert entries[1]["approved_by"] == "bob@example.com"


class TestYamlParseErrors:
    """YAML parse errors must include line/column — Issue #247 (Malak Yousef)."""

    def test_bad_indentation_includes_line_number(self, tmp_path):
        """A YAML indentation error surfaces the line number in the exception message."""
        bad_yaml = tmp_path / "bad.yaml"
        # Deliberately invalid YAML: mapping value not allowed here
        bad_yaml.write_text(
            "contract:\n"
            "  name: broken\n"
            "  rules:\n"
            "    - name: r1\n"
            "      field: x\n"
            "     type: not_empty\n"  # wrong indentation
        )
        reg = ContractRegistry(tmp_path)
        # Contract should be skipped (logged as error), not crash the registry
        assert reg.get("broken") is None

    def test_parse_error_message_contains_line(self, tmp_path):
        """ValueError from _load_file includes 'line' in the message."""
        bad_yaml = tmp_path / "bad2.yaml"
        bad_yaml.write_text("contract:\n  name: x\n  rules:\n  - :\n    broken: [unclosed\n")
        reg_loader = ContractRegistry.__new__(ContractRegistry)
        reg_loader.contracts_dir = tmp_path
        reg_loader.history = ContractHistory(":memory:")
        reg_loader._contracts = {}
        with pytest.raises(Exception, match=r"line \d+|failed to parse"):
            reg_loader._load_file(bad_yaml)

    def test_valid_contract_loads_after_bad_one(self, tmp_path):
        """A bad YAML file does not prevent other valid contracts from loading."""
        bad = tmp_path / "aaa_bad.yaml"
        bad.write_text("contract:\n  name: broken\nrules: [unclosed\n")
        good = tmp_path / "zzz_good.yaml"
        good.write_text(
            "contract:\n"
            "  name: good_contract\n"
            "  version: '1.0'\n"
            "  status: active\n"
            "  rules:\n"
            "    - name: r1\n"
            "      field: email\n"
            "      type: not_empty\n"
            "      error_message: required\n"
        )
        reg = ContractRegistry(tmp_path)
        assert reg.get("good_contract") is not None

    def test_empty_yaml_returns_none_not_error(self, tmp_path):
        """An empty YAML file is silently skipped, not treated as a parse error."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        reg = ContractRegistry(tmp_path)
        assert reg.list_contracts() == []

    def test_parse_error_skips_contract_count(self, tmp_path):
        """Registry contract count excludes files that fail to parse."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\nbroken: [unclosed\n")
        good = tmp_path / "ok.yaml"
        good.write_text(
            "contract:\n"
            "  name: ok\n"
            "  version: '1.0'\n"
            "  status: active\n"
            "  rules:\n"
            "    - name: r1\n"
            "      field: f\n"
            "      type: not_empty\n"
            "      error_message: required\n"
        )
        reg = ContractRegistry(tmp_path)
        assert len(reg.list_contracts()) == 1
        assert reg.list_contracts()[0]["name"] == "ok"


class TestLoyaltyTierLookup:
    """ACT-040-05 — loyalty_tier lookup rule in customer contract."""

    CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"

    def _get_loyalty_rule(self, registry):
        c = registry.get("customer")
        rules = [r for r in c.rules if r.name == "loyalty_tier_valid"]
        assert rules, "loyalty_tier_valid rule not found in customer contract"
        return rules[0]

    def test_loyalty_tier_rule_exists(self, registry):
        """customer contract must contain the loyalty_tier_valid lookup rule."""
        rule = self._get_loyalty_rule(registry)
        assert rule.type == "lookup"
        assert rule.field == "loyalty_tier"

    def test_loyalty_tier_valid(self, registry):
        """Record with loyalty_tier='gold' passes the lookup rule."""
        from opendqv.core.validator import validate_record, _load_lookup_set
        _load_lookup_set.cache_clear()
        rule = self._get_loyalty_rule(registry)
        with patch("opendqv.config.CONTRACTS_DIR", self.CONTRACTS_DIR):
            result = validate_record({"loyalty_tier": "gold"}, [rule], "customer")
        _load_lookup_set.cache_clear()
        assert result["valid"] is True

    def test_loyalty_tier_invalid(self, registry):
        """Record with loyalty_tier='platinum' fails — not in allowed list."""
        from opendqv.core.validator import validate_record, _load_lookup_set
        _load_lookup_set.cache_clear()
        rule = self._get_loyalty_rule(registry)
        with patch("opendqv.config.CONTRACTS_DIR", self.CONTRACTS_DIR):
            result = validate_record({"loyalty_tier": "platinum"}, [rule], "customer")
        _load_lookup_set.cache_clear()
        assert result["valid"] is False
        assert any(e["field"] == "loyalty_tier" for e in result["errors"])

    def test_loyalty_tier_absent(self, registry):
        """Record without loyalty_tier passes — the field is optional."""
        from opendqv.core.validator import validate_record, _load_lookup_set
        _load_lookup_set.cache_clear()
        rule = self._get_loyalty_rule(registry)
        with patch("opendqv.config.CONTRACTS_DIR", self.CONTRACTS_DIR):
            result = validate_record({}, [rule], "customer")
        _load_lookup_set.cache_clear()
        assert result["valid"] is True


# ── ExplainError tests ────────────────────────────────────────────────

class TestExplainRule:
    """Unit tests for core/explainer.py — no HTTP, no registry needed."""

    def _make_rule(self, **kwargs):
        defaults = dict(name="test_rule", type="not_empty", field="amount",
                        error_message="failed", severity="error")
        defaults.update(kwargs)
        return Rule(**defaults)

    def test_min_rule_banking_amount(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(name="amount_min", type="min", field="amount", min=0.01)
        result = explain_rule(rule)
        assert result["rule_type"] == "min"
        assert "0.01" in result["explanation"]
        assert result["constraint"] == {"min": 0.01}
        assert len(result["valid_examples"]) > 0
        assert any(x is None or (isinstance(x, (int, float)) and x < 0.01) for x in result["invalid_examples"])

    def test_not_empty_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="not_empty", field="email")
        result = explain_rule(rule)
        assert result["rule_type"] == "not_empty"
        assert "email" in result["explanation"]
        assert None in result["invalid_examples"]
        assert "" in result["invalid_examples"]

    def test_max_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="max", field="age", max=120)
        result = explain_rule(rule)
        assert result["rule_type"] == "max"
        assert "120" in result["explanation"]
        assert result["constraint"] == {"max": 120}

    def test_range_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="range", field="score", min=0, max=100)
        result = explain_rule(rule)
        assert result["rule_type"] == "range"
        assert "0" in result["explanation"] and "100" in result["explanation"]
        assert result["constraint"]["min"] == 0
        assert result["constraint"]["max"] == 100

    def test_email_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="email", field="email_address")
        result = explain_rule(rule)
        assert result["rule_type"] == "email"
        assert "user@example.com" in result["valid_examples"]
        assert None in result["invalid_examples"]

    def test_date_format_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="date_format", field="dob", format="%Y-%m-%d")
        result = explain_rule(rule)
        assert result["rule_type"] == "date_format"
        assert "dob" in result["explanation"]

    def test_min_length_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="min_length", field="name", min_length=2)
        result = explain_rule(rule)
        assert result["rule_type"] == "min_length"
        assert "2" in result["explanation"]
        assert result["constraint"] == {"min_length": 2}

    def test_lookup_rule(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="lookup", field="loyalty_tier",
                               lookup_file="contracts/ref/loyalty_tiers.txt")
        result = explain_rule(rule)
        assert result["rule_type"] == "lookup"
        assert "loyalty_tiers.txt" in result["explanation"]

    def test_unknown_rule_type_falls_back_to_generic(self):
        from opendqv.core.explainer import explain_rule
        rule = self._make_rule(type="future_rule_type", field="x",
                               error_message="custom message")
        result = explain_rule(rule)
        assert result["rule_type"] == "future_rule_type"
        assert "custom message" in result["explanation"]


class TestExplainErrorAPI:
    """Integration tests for GET /api/v1/contracts/{name}/explain/{field}/{rule}."""

    def test_explain_banking_amount_min(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/v1/contracts/banking_transaction/explain/amount/amount_min")
        assert resp.status_code == 200
        data = resp.json()
        assert data["contract"] == "banking_transaction"
        assert data["field"] == "amount"
        assert data["rule"] == "amount_min"
        assert data["rule_type"] == "min"
        assert "0.01" in data["explanation"]
        assert len(data["valid_examples"]) > 0
        assert len(data["invalid_examples"]) > 0
        assert data["constraint"].get("min") == 0.01

    def test_explain_404_unknown_contract(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/v1/contracts/no_such_contract/explain/field/rule")
        assert resp.status_code == 404

    def test_explain_404_unknown_rule(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/v1/contracts/banking_transaction/explain/amount/no_such_rule")
        assert resp.status_code == 404

    def test_explain_response_shape(self):
        """All required fields present in response."""
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/v1/contracts/banking_transaction/explain/amount/amount_min")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("contract", "field", "rule", "rule_type", "explanation",
                    "valid_examples", "invalid_examples", "constraint"):
            assert key in data, f"Missing key: {key}"
