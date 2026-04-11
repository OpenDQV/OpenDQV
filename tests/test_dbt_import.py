"""Tests for the dbt schema importer (unit tests)."""

import os

import yaml

from opendqv.core.importers.dbt import import_dbt_schema, dbt_schema_to_yaml, export_dbt_schema, contract_to_dbt_yaml

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")


def _load_dbt_sample(filename: str) -> dict:
    with open(os.path.join(SAMPLE_DIR, filename)) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Unit tests for the dbt importer module
# ---------------------------------------------------------------------------


class TestDbtImporter:
    """Unit tests that call import_dbt_schema / dbt_schema_to_yaml directly."""

    def test_import_models(self):
        schema = _load_dbt_sample("dbt_schema_sample.yml")
        result = import_dbt_schema(schema)

        contracts = result["contracts"]
        assert len(contracts) > 0

        # The first contract should be the 'customers' model
        customers = contracts[0]
        assert customers["contract"]["name"] == "customers"
        assert customers["contract"]["status"] == "draft"
        assert customers["contract"]["source"] == "import"
        assert customers["contract"]["asset_id"] == "dbt::customers"

        rule_types = {r["type"] for r in customers["contract"]["rules"]}
        assert "unique" in rule_types
        assert "not_empty" in rule_types
        assert "regex" in rule_types      # accepted_values -> regex
        assert "range" in rule_types      # accepted_range -> range

        # BUG-1: range rule must use min_value/max_value (not min/max)
        range_rules = [r for r in customers["contract"]["rules"] if r["type"] == "range"]
        assert len(range_rules) >= 1
        rr = range_rules[0]
        assert "min_value" in rr or "max_value" in rr, "range rule must use min_value/max_value keys"
        assert "min" not in rr, "range rule must not use legacy 'min' key"
        assert "max" not in rr, "range rule must not use legacy 'max' key"

    def test_import_sources(self):
        schema = _load_dbt_sample("dbt_schema_sample.yml")
        result = import_dbt_schema(schema)

        contracts = result["contracts"]
        # Should have at least 2 contracts: 'customers' model + source table
        assert len(contracts) >= 2

        source_names = [c["contract"]["name"] for c in contracts]
        assert "raw_payments__transactions" in source_names

    def test_accepted_values_to_regex(self):
        schema = {
            "models": [
                {
                    "name": "test_model",
                    "columns": [
                        {
                            "name": "color",
                            "tests": [
                                {"accepted_values": {"values": ["a", "b", "c"]}},
                            ],
                        },
                    ],
                },
            ],
        }
        result = import_dbt_schema(schema)
        rules = result["contracts"][0]["contract"]["rules"]

        regex_rules = [r for r in rules if r["type"] == "regex"]
        assert len(regex_rules) == 1
        assert regex_rules[0]["pattern"] == "^(a|b|c)$"

    def test_range_round_trip(self):
        """Import a range test then export it — min_value/max_value must survive."""
        schema = {
            "models": [
                {
                    "name": "orders",
                    "columns": [
                        {
                            "name": "amount",
                            "tests": [
                                {"dbt_utils.accepted_range": {"min_value": 0, "max_value": 9999}},
                            ],
                        },
                    ],
                },
            ],
        }
        imported = import_dbt_schema(schema)
        rules = imported["contracts"][0]["contract"]["rules"]
        range_rules = [r for r in rules if r["type"] == "range"]
        assert len(range_rules) == 1
        assert range_rules[0]["min_value"] == 0
        assert range_rules[0]["max_value"] == 9999

        # Export back to dbt and verify values survive
        doc = export_dbt_schema("orders", rules)
        col = doc["models"][0]["columns"][0]
        range_tests = [
            t for t in col["tests"]
            if isinstance(t, dict) and "dbt_utils.accepted_range" in t
        ]
        assert len(range_tests) == 1
        assert range_tests[0]["dbt_utils.accepted_range"]["min_value"] == 0
        assert range_tests[0]["dbt_utils.accepted_range"]["max_value"] == 9999

    def test_relationships_skipped(self):
        schema = {
            "models": [
                {
                    "name": "test_model",
                    "columns": [
                        {
                            "name": "fk_id",
                            "tests": [
                                {"relationships": {"to": "ref('other')", "field": "id"}},
                            ],
                        },
                    ],
                },
            ],
        }
        result = import_dbt_schema(schema)
        contract_entry = result["contracts"][0]

        assert contract_entry["stats"]["skipped"] == 1
        assert len(contract_entry["skipped"]) == 1
        assert contract_entry["skipped"][0]["test"] == "relationships"
        assert contract_entry["skipped"][0]["reason"] == "not applicable to single-record validation"

    def test_empty_schema(self):
        schema = {"version": 2}
        result = import_dbt_schema(schema)
        assert result["contracts"] == []

    def test_dbt_schema_to_yaml(self):
        schema = _load_dbt_sample("dbt_schema_sample.yml")
        tuples = dbt_schema_to_yaml(schema)

        assert isinstance(tuples, list)
        assert len(tuples) > 0

        for name, yaml_str in tuples:
            assert isinstance(name, str)
            assert isinstance(yaml_str, str)

            # Must be valid YAML
            parsed = yaml.safe_load(yaml_str)
            assert "contract" in parsed
            assert parsed["contract"]["name"] == name


# ---------------------------------------------------------------------------
# Unit tests for the dbt exporter module
# ---------------------------------------------------------------------------


class TestDBTExporter:
    """Unit tests for export_dbt_schema / contract_to_dbt_yaml."""

    def test_export_not_null(self):
        rules = [{"field": "email", "type": "not_empty", "name": "email_not_empty"}]
        doc = export_dbt_schema("my_model", rules)
        col = doc["models"][0]["columns"][0]
        assert col["name"] == "email"
        assert "not_null" in col["tests"]

    def test_export_unique(self):
        rules = [{"field": "id", "type": "unique", "name": "id_unique"}]
        doc = export_dbt_schema("my_model", rules)
        col = doc["models"][0]["columns"][0]
        assert col["name"] == "id"
        assert "unique" in col["tests"]

    def test_export_regex(self):
        rules = [{"field": "code", "type": "regex", "name": "code_regex", "pattern": "^[A-Z]+$"}]
        doc = export_dbt_schema("my_model", rules)
        col = doc["models"][0]["columns"][0]
        assert col["name"] == "code"
        regex_tests = [
            t for t in col["tests"]
            if isinstance(t, dict) and "dbt_expectations.expect_column_values_to_match_regex" in t
        ]
        assert len(regex_tests) == 1
        assert regex_tests[0]["dbt_expectations.expect_column_values_to_match_regex"]["regex"] == "^[A-Z]+$"

    def test_export_range(self):
        rules = [{"field": "age", "type": "range", "name": "age_range", "min_value": 0, "max_value": 120}]
        doc = export_dbt_schema("my_model", rules)
        col = doc["models"][0]["columns"][0]
        assert col["name"] == "age"
        range_tests = [
            t for t in col["tests"]
            if isinstance(t, dict) and "dbt_utils.accepted_range" in t
        ]
        assert len(range_tests) == 1
        assert range_tests[0]["dbt_utils.accepted_range"]["min_value"] == 0
        assert range_tests[0]["dbt_utils.accepted_range"]["max_value"] == 120

    def test_export_unsupported_rule_skipped(self):
        rules = [
            {"field": "col", "type": "not_empty", "name": "ok_rule"},
            {"field": "col", "type": "custom_unsupported_xyz", "name": "bad_rule"},
        ]
        doc = export_dbt_schema("my_model", rules)
        skipped = doc["_skipped"]
        assert any(s["rule"] == "bad_rule" for s in skipped)
        col = doc["models"][0]["columns"][0]
        # Only the valid test appears in the column tests
        assert "not_null" in col["tests"]
        assert not any(
            isinstance(t, dict) and "custom_unsupported_xyz" in str(t)
            for t in col["tests"]
        )

    def test_contract_to_dbt_yaml_valid_yaml(self):
        rules = [
            {"field": "id", "type": "unique", "name": "id_unique"},
            {"field": "name", "type": "not_empty", "name": "name_not_empty"},
        ]
        yaml_str = contract_to_dbt_yaml("orders", rules, description="Order records")
        parsed = yaml.safe_load(yaml_str)
        assert parsed["version"] == 2
        assert len(parsed["models"]) == 1
        assert parsed["models"][0]["name"] == "orders"
        assert "_skipped" not in parsed
