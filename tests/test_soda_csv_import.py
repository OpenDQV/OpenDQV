"""Tests for the Soda Core and CSV rule importers."""

import os
import textwrap

import yaml

from core.importers.soda import import_soda_checks, soda_checks_to_yaml
from core.importers.csv_rules import import_csv_rules, csv_rules_to_yaml

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")


def _load_soda_sample(filename: str) -> dict:
    with open(os.path.join(SAMPLE_DIR, filename)) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Unit tests — Soda importer
# ---------------------------------------------------------------------------


class TestSodaImporter:
    """Unit tests that call import_soda_checks / soda_checks_to_yaml directly."""

    def test_import_soda_sample(self):
        """Loading soda_checks_sample.yaml should produce exactly 2 contracts."""
        checks = _load_soda_sample("soda_checks_sample.yaml")
        result = import_soda_checks(checks)

        contracts = result["contracts"]
        assert len(contracts) == 2

        names = [c["contract"]["name"] for c in contracts]
        assert "customers" in names
        assert "orders" in names

    def test_soda_missing_count_maps_to_not_empty(self):
        """missing_count = 0 on a field must produce a not_empty rule."""
        checks = {
            "checks for users": [
                "missing_count(username) = 0",
            ]
        }
        result = import_soda_checks(checks)

        rules = result["contracts"][0]["contract"]["rules"]
        assert len(rules) == 1
        rule = rules[0]
        assert rule["type"] == "not_empty"
        assert rule["field"] == "username"
        assert rule["severity"] == "error"

    def test_soda_duplicate_count_maps_to_unique(self):
        """duplicate_count = 0 on a field must produce a unique rule."""
        checks = {
            "checks for products": [
                "duplicate_count(sku) = 0",
            ]
        }
        result = import_soda_checks(checks)

        rules = result["contracts"][0]["contract"]["rules"]
        assert len(rules) == 1
        rule = rules[0]
        assert rule["type"] == "unique"
        assert rule["field"] == "sku"
        assert rule["severity"] == "error"

    def test_soda_min_max_rules(self):
        """min / max check expressions should produce min / max rules."""
        checks = {
            "checks for readings": [
                "min(temperature) >= 0",
                "max(temperature) <= 100",
            ]
        }
        result = import_soda_checks(checks)

        rules = result["contracts"][0]["contract"]["rules"]
        rule_types = {r["type"] for r in rules}
        assert "min" in rule_types
        assert "max" in rule_types

        min_rule = next(r for r in rules if r["type"] == "min")
        assert min_rule["field"] == "temperature"
        assert min_rule["min_value"] == 0

        max_rule = next(r for r in rules if r["type"] == "max")
        assert max_rule["field"] == "temperature"
        assert max_rule["max_value"] == 100

    def test_soda_unsupported_skipped(self):
        """freshness and other non-metric checks must be skipped with a reason."""
        checks = {
            "checks for events": [
                "missing_count(event_id) = 0",
                "freshness(created_at) < 24h",
            ]
        }
        result = import_soda_checks(checks)

        entry = result["contracts"][0]
        assert entry["stats"]["imported"] == 1
        assert entry["stats"]["skipped"] == 1

        skipped = entry["skipped"]
        assert len(skipped) == 1
        # The freshness entry should appear and have a reason
        assert skipped[0]["reason"] != ""

    def test_soda_to_yaml(self):
        """soda_checks_to_yaml() must return a list of (name, yaml_string) tuples."""
        checks = _load_soda_sample("soda_checks_sample.yaml")
        pairs = soda_checks_to_yaml(checks)

        assert isinstance(pairs, list)
        assert len(pairs) > 0

        for name, yaml_str in pairs:
            assert isinstance(name, str)
            assert isinstance(yaml_str, str)

            parsed = yaml.safe_load(yaml_str)
            assert "contract" in parsed
            assert parsed["contract"]["name"] == name


# ---------------------------------------------------------------------------
# Unit tests — CSV importer
# ---------------------------------------------------------------------------


class TestCSVImporter:
    """Unit tests that call import_csv_rules / csv_rules_to_yaml directly."""

    def test_import_csv_sample(self):
        """Loading csv_rules_sample.csv should import all 8 rows without skips."""
        with open(os.path.join(SAMPLE_DIR, "csv_rules_sample.csv")) as f:
            csv_content = f.read()

        result = import_csv_rules(csv_content, "sample_contract")

        assert result["stats"]["total_rules"] == 8
        assert result["stats"]["skipped"] == 0
        assert result["stats"]["imported"] == 8

        rule_types = {r["type"] for r in result["contract"]["rules"]}
        assert "not_empty" in rule_types
        assert "regex" in rule_types
        assert "min" in rule_types
        assert "max" in rule_types
        assert "range" in rule_types
        assert "min_length" in rule_types
        assert "max_length" in rule_types
        assert "unique" in rule_types

    def test_csv_not_empty_rule(self):
        """A row with rule_type=not_empty should generate a not_empty rule."""
        csv_content = textwrap.dedent("""\
            field,rule_type,value,severity,error_message
            username,not_empty,,error,Username is required
        """)
        result = import_csv_rules(csv_content)

        rules = result["contract"]["rules"]
        assert len(rules) == 1
        rule = rules[0]
        assert rule["type"] == "not_empty"
        assert rule["field"] == "username"
        assert rule["error_message"] == "Username is required"

    def test_csv_regex_rule(self):
        """A row with rule_type=regex should set the pattern field on the rule."""
        pattern = r"^\d{5}(-\d{4})?$"
        csv_content = textwrap.dedent(f"""\
            field,rule_type,value,severity,error_message
            zip_code,regex,{pattern},error,Invalid ZIP code
        """)
        result = import_csv_rules(csv_content)

        rules = result["contract"]["rules"]
        assert len(rules) == 1
        rule = rules[0]
        assert rule["type"] == "regex"
        assert rule["pattern"] == pattern

    def test_csv_severity_mapping(self):
        """A row with severity=warning should produce a rule with severity='warning'."""
        csv_content = textwrap.dedent("""\
            field,rule_type,value,severity,error_message
            first_name,min_length,2,warning,Name is short but OK
        """)
        result = import_csv_rules(csv_content)

        rules = result["contract"]["rules"]
        assert len(rules) == 1
        assert rules[0]["severity"] == "warning"

    def test_csv_empty_input(self):
        """An empty CSV (header only) should produce 0 rules and 0 skips."""
        csv_content = "field,rule_type,value,severity,error_message\n"
        result = import_csv_rules(csv_content)

        assert result["stats"]["total_rules"] == 0
        assert result["stats"]["imported"] == 0
        assert result["stats"]["skipped"] == 0
        assert result["contract"]["rules"] == []

    def test_csv_to_yaml(self):
        """csv_rules_to_yaml() must return valid YAML with a 'contract' key."""
        csv_content = textwrap.dedent("""\
            field,rule_type,value,severity,error_message
            email,not_empty,,error,Email required
            email,regex,^.+@.+$,error,Bad email
        """)
        yaml_str = csv_rules_to_yaml(csv_content, "test_contract")

        assert isinstance(yaml_str, str)
        parsed = yaml.safe_load(yaml_str)
        assert "contract" in parsed
        assert parsed["contract"]["name"] == "test_contract"
        assert len(parsed["contract"]["rules"]) == 2


# ---------------------------------------------------------------------------
# Integration tests — Import API endpoints
# ---------------------------------------------------------------------------


class TestImportAPI:
    """Integration tests that exercise /api/v1/import/soda and /api/v1/import/csv."""

    def test_soda_import_endpoint(self, client, auth_headers):
        """POST a valid Soda dict to /api/v1/import/soda; expect 200 and contracts list."""
        payload = {
            "checks for customers": [
                "missing_count(email) = 0",
                "duplicate_count(customer_id) = 0",
                "min(age) >= 0",
                "max(age) <= 120",
            ]
        }
        response = client.post(
            "/api/v1/import/soda",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "contracts" in data
        assert len(data["contracts"]) == 1
        contract = data["contracts"][0]["contract"]
        assert contract["name"] == "customers"
        rule_types = {r["type"] for r in contract["rules"]}
        assert "not_empty" in rule_types
        assert "unique" in rule_types
        # Import must produce draft status with source tag
        assert contract.get("status") == "draft"
        assert contract.get("source") == "import"

    def test_csv_import_endpoint(self, client, auth_headers):
        """POST valid CSV text to /api/v1/import/csv; expect 200 and a contract."""
        csv_body = (
            "field,rule_type,value,severity,error_message\n"
            "email,not_empty,,error,Email required\n"
            "email,regex,^.+@.+$,error,Bad email\n"
        )
        response = client.post(
            "/api/v1/import/csv",
            content=csv_body,
            headers={**auth_headers, "Content-Type": "text/plain"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "contract" in data
        assert data["stats"]["imported"] == 2
        assert data["contract"]["name"] == "csv_import"

    def test_import_endpoints_require_auth(self, client):
        """Both import endpoints must return 401 when no auth token is provided."""
        soda_response = client.post(
            "/api/v1/import/soda",
            json={"checks for test": ["missing_count(id) = 0"]},
        )
        assert soda_response.status_code == 401

        csv_response = client.post(
            "/api/v1/import/csv",
            content="field,rule_type,value,severity,error_message\nemail,not_empty,,error,\n",
            headers={"Content-Type": "text/plain"},
        )
        assert csv_response.status_code == 401
