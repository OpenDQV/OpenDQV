"""Tests for the Great Expectations importer (unit + integration)."""

import json
import os

import yaml

from core.importers.great_expectations import import_gx_suite, gx_suite_to_yaml

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")


def _load_sample(filename: str) -> dict:
    with open(os.path.join(SAMPLE_DIR, filename)) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Unit tests for the importer module
# ---------------------------------------------------------------------------


class TestGXImporter:
    """Unit tests that call import_gx_suite / gx_suite_to_yaml directly."""

    def test_import_v0_suite(self):
        suite = _load_sample("gx_suite_sample.json")
        result = import_gx_suite(suite)

        contract = result["contract"]
        stats = result["stats"]

        assert contract["name"] == "customer_contacts"
        assert stats["total_expectations"] == 12
        assert stats["skipped"] == 0
        # 12 expectations, but 2 length_between each produce 2 rules -> 14
        assert stats["imported"] >= 13

        rule_names = [r["name"] for r in contract["rules"]]
        assert "email_not_empty" in rule_names
        assert "email_regex" in rule_names
        assert "age_range" in rule_names
        assert "customer_id_unique" in rule_names

        # Severity: last_name has mostly=0.95 -> warning; email has no mostly -> error
        rules_by_name = {r["name"]: r for r in contract["rules"]}
        assert rules_by_name["last_name_not_empty"]["severity"] == "warning"
        assert rules_by_name["email_not_empty"]["severity"] == "error"

        # Description from meta.notes
        assert "Revenue" in rules_by_name["annual_revenue_range"]["description"]

    def test_import_v1_suite(self):
        suite = _load_sample("gx_suite_v1_sample.json")
        result = import_gx_suite(suite)

        contract = result["contract"]
        stats = result["stats"]

        assert contract["name"] == "customer_contacts"
        assert stats["total_expectations"] == 12
        assert stats["skipped"] == 0
        assert stats["imported"] >= 13

        rule_names = [r["name"] for r in contract["rules"]]
        assert "email_not_empty" in rule_names
        assert "customer_id_unique" in rule_names

    def test_import_unsupported(self):
        suite = _load_sample("gx_suite_unsupported.json")
        result = import_gx_suite(suite)

        stats = result["stats"]
        assert stats["imported"] == 2
        assert stats["skipped"] == 2

        skipped_types = [s["expectation_type"] for s in result["skipped"]]
        assert "expect_column_pair_values_to_be_equal" in skipped_types
        assert "expect_table_row_count_to_be_between" in skipped_types

        for entry in result["skipped"]:
            assert entry["reason"] == "unsupported expectation type"

    def test_import_empty_suite(self):
        suite = {"expectation_suite_name": "empty", "expectations": []}
        result = import_gx_suite(suite)

        assert result["stats"]["imported"] == 0
        assert result["stats"]["skipped"] == 0
        assert result["contract"]["rules"] == []

    def test_gx_suite_to_yaml(self):
        suite = _load_sample("gx_suite_sample.json")
        yaml_str = gx_suite_to_yaml(suite)

        assert isinstance(yaml_str, str)
        # Must be valid YAML
        parsed = yaml.safe_load(yaml_str)
        assert "contract" in parsed

        assert "contract:" in yaml_str
        assert "name: customer_contacts" in yaml_str
        assert "rules:" in yaml_str

    def test_mostly_severity_mapping(self):
        suite = {
            "expectation_suite_name": "severity_test",
            "expectations": [
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "col_no_mostly"},
                    "meta": {},
                },
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "col_mostly_1", "mostly": 1.0},
                    "meta": {},
                },
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "col_mostly_099", "mostly": 0.99},
                    "meta": {},
                },
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "col_mostly_050", "mostly": 0.5},
                    "meta": {},
                },
            ],
        }
        result = import_gx_suite(suite)
        rules_by_name = {r["name"]: r for r in result["contract"]["rules"]}

        assert rules_by_name["col_no_mostly_not_empty"]["severity"] == "error"
        assert rules_by_name["col_mostly_1_not_empty"]["severity"] == "error"
        assert rules_by_name["col_mostly_099_not_empty"]["severity"] == "warning"
        assert rules_by_name["col_mostly_050_not_empty"]["severity"] == "warning"

    def test_meta_notes_to_description(self):
        suite = {
            "expectation_suite_name": "notes_test",
            "expectations": [
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "important_field"},
                    "meta": {"notes": "Important field"},
                },
            ],
        }
        result = import_gx_suite(suite)
        rule = result["contract"]["rules"][0]
        assert rule["description"] == "Important field"

    def test_duplicate_rule_names(self):
        suite = {
            "expectation_suite_name": "dupes_test",
            "expectations": [
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "email"},
                    "meta": {},
                },
                {
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "kwargs": {"column": "email"},
                    "meta": {},
                },
            ],
        }
        result = import_gx_suite(suite)
        names = [r["name"] for r in result["contract"]["rules"]]
        assert len(names) == 2
        assert "email_not_empty_1" in names
        assert "email_not_empty_2" in names


# ---------------------------------------------------------------------------
# Integration tests via the API
# ---------------------------------------------------------------------------


class TestGXImportAPI:
    """Integration tests that hit the REST endpoints."""

    def test_import_endpoint(self, client, auth_headers):
        suite = _load_sample("gx_suite_sample.json")
        r = client.post("/api/v1/import/gx", json=suite, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "contract" in data
        assert "stats" in data
        assert "skipped" in data

    def test_import_sets_source_and_status(self, client, auth_headers):
        """ACT-046-04: Import endpoint must tag contract with source=import and status=draft."""
        suite = _load_sample("gx_suite_sample.json")
        r = client.post("/api/v1/import/gx", json=suite, headers=auth_headers)
        assert r.status_code == 200
        contract = r.json()["contract"]
        assert contract.get("source") == "import", f"Expected source=import, got {contract.get('source')}"
        assert contract.get("status") == "draft", f"Expected status=draft, got {contract.get('status')}"

    def test_import_and_save(self, client, auth_headers):
        suite = _load_sample("gx_suite_sample.json")
        r = client.post(
            "/api/v1/import/gx",
            json=suite,
            params={"save": "true"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "saved_to" in data
        assert "message" in data

        saved_path = data["saved_to"]

        try:
            # The saved contract should now be accessible via the contracts API
            r2 = client.get("/api/v1/contracts/customer_contacts")
            assert r2.status_code == 200
            assert r2.json()["name"] == "customer_contacts"
        finally:
            # Clean up saved file
            if os.path.exists(saved_path):
                os.remove(saved_path)

    def test_import_endpoint_no_auth(self, client):
        suite = _load_sample("gx_suite_sample.json")
        r = client.post("/api/v1/import/gx", json=suite)
        assert r.status_code == 401

    def test_export_endpoint(self, client, auth_headers):
        r = client.get("/api/v1/export/gx/customer", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # BUG-GX-1: exporter now emits GX 1.x format ("name", not "expectation_suite_name")
        assert "name" in data
        assert "expectations" in data
        assert isinstance(data["expectations"], list)

    def test_export_not_found(self, client, auth_headers):
        r = client.get("/api/v1/export/gx/nonexistent", headers=auth_headers)
        assert r.status_code == 404

    def test_roundtrip(self, client, auth_headers):
        # Import a GX suite
        suite = _load_sample("gx_suite_sample.json")
        r1 = client.post(
            "/api/v1/import/gx",
            json=suite,
            params={"save": "true"},
            headers=auth_headers,
        )
        assert r1.status_code == 200
        saved_path = r1.json()["saved_to"]

        try:
            # Export it back as a GX suite
            r2 = client.get(
                "/api/v1/export/gx/customer_contacts", headers=auth_headers
            )
            assert r2.status_code == 200
            exported = r2.json()

            # BUG-GX-1: GX 1.x uses "name" key
            assert exported["name"] == "customer_contacts"
            assert len(exported["expectations"]) > 0

            # Verify the exported expectations cover the same columns
            original_columns = {
                exp.get("kwargs", {}).get("column")
                for exp in suite["expectations"]
                if exp.get("kwargs", {}).get("column")
            }
            exported_columns = {
                exp.get("kwargs", {}).get("column")
                for exp in exported["expectations"]
                if exp.get("kwargs", {}).get("column")
            }
            # All original columns should be present in the export
            assert original_columns.issubset(exported_columns)
        finally:
            if os.path.exists(saved_path):
                os.remove(saved_path)
