"""Tests for ODCS 3.1 importer and exporter.

Covers:
  - import_odcs: field shortcuts, quality checks, dedup, skipped types
  - odcs_to_yaml: returns (name, yaml_string) tuple
  - export_odcs: produces valid ODCS 3.1 dict
  - contract_to_odcs_yaml: produces valid YAML string
  - POST /import/odcs API endpoint
  - GET /export/odcs/{name} API endpoint
"""

import yaml

from opendqv.core.importers.odcs import (
    import_odcs,
    odcs_to_yaml,
    export_odcs,
    contract_to_odcs_yaml,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CONTRACT = {
    "apiVersion": "v3.1.0",
    "kind": "DataContract",
    "info": {
        "title": "Customer Contract",
        "version": "2.0",
        "status": "active",
        "description": "Test contract",
        "owner": "data-team",
    },
    "schema": [
        {
            "name": "customers",
            "properties": [
                {"name": "email", "required": True, "unique": True},
                {"name": "name", "required": True},
            ],
        }
    ],
}

FULL_QUALITY_CONTRACT = {
    "apiVersion": "v3.1.0",
    "kind": "DataContract",
    "info": {"title": "full_quality", "version": "1.0"},
    "schema": [
        {
            "name": "orders",
            "properties": [
                {
                    "name": "email",
                    "quality": [
                        {"type": "not_null", "mustBeSatisfied": True},
                        {"type": "regex", "pattern": r"^[\w.]+@[\w.]+$", "mustBeSatisfied": True},
                    ],
                },
                {
                    "name": "age",
                    "quality": [
                        {"type": "range", "min": 0, "max": 120, "mustBeSatisfied": False},
                    ],
                },
                {
                    "name": "score",
                    "quality": [
                        {"type": "min", "min": 0, "mustBeSatisfied": True},
                        {"type": "max", "max": 100, "mustBeSatisfied": True},
                    ],
                },
                {
                    "name": "code",
                    "minLength": 3,
                    "maxLength": 10,
                    "quality": [
                        {"type": "min_length", "minLength": 3, "mustBeSatisfied": False},
                        {"type": "date_format", "format": "%Y-%m-%d", "mustBeSatisfied": False},
                    ],
                },
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# TestImportODCSBasic
# ---------------------------------------------------------------------------

class TestImportODCSBasic:
    """Basic import_odcs functionality."""

    def test_returns_contract_key(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert "contract" in result

    def test_contract_name_from_title(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["contract"]["name"] == "customer_contract"

    def test_name_sanitised_lowercase(self):
        c = {"info": {"title": "My Contract-2"}, "schema": []}
        result = import_odcs(c)
        assert result["contract"]["name"] == "my_contract_2"

    def test_version_extracted(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["contract"]["version"] == "2.0"

    def test_status_extracted(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["contract"]["status"] == "active"

    def test_description_extracted(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["contract"]["description"] == "Test contract"

    def test_owner_extracted(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["contract"]["owner"] == "data-team"

    def test_rule_count_in_result(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert result["rule_count"] == len(result["contract"]["rules"])

    def test_skipped_checks_list(self):
        result = import_odcs(MINIMAL_CONTRACT)
        assert "skipped_checks" in result
        assert isinstance(result["skipped_checks"], list)

    def test_empty_schema_produces_no_rules(self):
        c = {"info": {"title": "empty"}, "schema": []}
        result = import_odcs(c)
        assert result["contract"]["rules"] == []

    def test_missing_info_uses_defaults(self):
        c = {"schema": []}
        result = import_odcs(c)
        assert result["contract"]["name"] == "imported_contract"
        assert result["contract"]["version"] == "1.0"
        assert result["contract"]["status"] == "active"


# ---------------------------------------------------------------------------
# TestFieldShortcuts
# ---------------------------------------------------------------------------

class TestFieldShortcuts:
    """Field-level shortcut attributes → rules."""

    def _rules(self, contract):
        return import_odcs(contract)["contract"]["rules"]

    def test_required_produces_not_empty_rule(self):
        rules = self._rules(MINIMAL_CONTRACT)
        types = [r["type"] for r in rules if r["field"] == "email"]
        assert "not_empty" in types

    def test_required_rule_is_error_severity(self):
        rules = self._rules(MINIMAL_CONTRACT)
        r = next(r for r in rules if r["field"] == "email" and r["type"] == "not_empty")
        assert r["severity"] == "error"

    def test_unique_produces_unique_rule(self):
        rules = self._rules(MINIMAL_CONTRACT)
        types = [r["type"] for r in rules if r["field"] == "email"]
        assert "unique" in types

    def test_min_length_shortcut(self):
        c = {
            "info": {"title": "t"},
            "schema": [{"name": "t", "properties": [{"name": "code", "minLength": 5}]}],
        }
        rules = self._rules(c)
        r = next(r for r in rules if r["type"] == "min_length")
        assert r["min_length"] == 5

    def test_max_length_shortcut(self):
        c = {
            "info": {"title": "t"},
            "schema": [{"name": "t", "properties": [{"name": "code", "maxLength": 20}]}],
        }
        rules = self._rules(c)
        r = next(r for r in rules if r["type"] == "max_length")
        assert r["max_length"] == 20


# ---------------------------------------------------------------------------
# TestQualityChecks
# ---------------------------------------------------------------------------

class TestQualityChecks:
    """Inline quality[] checks → rules."""

    def _rules(self):
        return import_odcs(FULL_QUALITY_CONTRACT)["contract"]["rules"]

    def test_not_null_maps_to_not_empty(self):
        rules = self._rules()
        types = [r["type"] for r in rules if r["field"] == "email"]
        assert "not_empty" in types

    def test_must_be_satisfied_true_is_error(self):
        rules = self._rules()
        r = next(r for r in rules if r["field"] == "email" and r["type"] == "not_empty")
        assert r["severity"] == "error"

    def test_must_be_satisfied_false_is_warning(self):
        rules = self._rules()
        r = next(r for r in rules if r["field"] == "age" and r["type"] == "range")
        assert r["severity"] == "warning"

    def test_regex_pattern_captured(self):
        rules = self._rules()
        r = next(r for r in rules if r["type"] == "regex")
        assert r["pattern"] == r"^[\w.]+@[\w.]+$"

    def test_range_min_max(self):
        rules = self._rules()
        r = next(r for r in rules if r["field"] == "age" and r["type"] == "range")
        assert r["min_value"] == 0.0
        assert r["max_value"] == 120.0

    def test_min_rule(self):
        rules = self._rules()
        r = next(r for r in rules if r["field"] == "score" and r["type"] == "min")
        assert r["min_value"] == 0.0

    def test_max_rule(self):
        rules = self._rules()
        r = next(r for r in rules if r["field"] == "score" and r["type"] == "max")
        assert r["max_value"] == 100.0

    def test_min_length_quality(self):
        rules = self._rules()
        # Dedup by (type, field): shortcut "code_min_length" wins over quality "code_min_length_0"
        r = next(r for r in rules if r["field"] == "code" and r["type"] == "min_length")
        assert r["min_length"] == 3

    def test_date_format_quality(self):
        rules = self._rules()
        r = next(r for r in rules if r["type"] == "date_format")
        assert r["format"] == "%Y-%m-%d"

    def test_unsupported_type_skipped(self):
        c = {
            "info": {"title": "t"},
            "schema": [{
                "name": "t",
                "properties": [{"name": "f", "quality": [{"type": "custom_check"}]}],
            }],
        }
        result = import_odcs(c)
        assert "f.custom_check" in result["skipped_checks"]

    def test_regex_without_pattern_skipped(self):
        c = {
            "info": {"title": "t"},
            "schema": [{
                "name": "t",
                "properties": [{"name": "f", "quality": [{"type": "regex"}]}],
            }],
        }
        result = import_odcs(c)
        assert result["contract"]["rules"] == []


# ---------------------------------------------------------------------------
# TestDeduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Shortcut + quality duplicates are deduplicated."""

    def test_required_plus_not_null_quality_deduped(self):
        c = {
            "info": {"title": "t"},
            "schema": [{
                "name": "t",
                "properties": [{
                    "name": "email",
                    "required": True,
                    "quality": [{"type": "not_null", "mustBeSatisfied": True}],
                }],
            }],
        }
        result = import_odcs(c)
        not_empty_rules = [r for r in result["contract"]["rules"] if r["type"] == "not_empty"]
        # Dedup by (type, field): shortcut "email_not_empty" added first, quality check dropped.
        assert len(not_empty_rules) == 1


# ---------------------------------------------------------------------------
# TestOdcsToYaml
# ---------------------------------------------------------------------------

class TestOdcsToYaml:
    """odcs_to_yaml() returns (name, yaml_string)."""

    def test_returns_tuple(self):
        result = odcs_to_yaml(MINIMAL_CONTRACT)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_name_matches_title(self):
        name, _ = odcs_to_yaml(MINIMAL_CONTRACT)
        assert name == "customer_contract"

    def test_name_override(self):
        name, _ = odcs_to_yaml(MINIMAL_CONTRACT, contract_name="override_name")
        assert name == "override_name"

    def test_yaml_is_valid(self):
        _, yaml_str = odcs_to_yaml(MINIMAL_CONTRACT)
        parsed = yaml.safe_load(yaml_str)
        assert "contract" in parsed

    def test_yaml_has_rules(self):
        _, yaml_str = odcs_to_yaml(MINIMAL_CONTRACT)
        parsed = yaml.safe_load(yaml_str)
        assert isinstance(parsed["contract"]["rules"], list)


# ---------------------------------------------------------------------------
# TestExportODCS
# ---------------------------------------------------------------------------

class TestExportODCS:
    """export_odcs() produces valid ODCS 3.1 dict."""

    def _make_rules(self):
        return [
            {"field": "email", "type": "not_empty", "severity": "error", "error_message": "required"},
            {"field": "email", "type": "regex", "severity": "error", "pattern": r"^.+@.+$",
             "error_message": "bad email"},
            {"field": "age", "type": "range", "severity": "warning", "min_value": 0, "max_value": 120,
             "error_message": "out of range"},
            {"field": "name", "type": "min_length", "severity": "error", "min_length": 2,
             "error_message": "too short"},
            {"field": "code", "type": "max_length", "severity": "error", "max_length": 10,
             "error_message": "too long"},
        ]

    def test_api_version(self):
        doc = export_odcs("test", self._make_rules())
        assert doc["apiVersion"] == "v3.1.0"

    def test_kind_is_data_contract(self):
        doc = export_odcs("test", self._make_rules())
        assert doc["kind"] == "DataContract"

    def test_info_title(self):
        doc = export_odcs("mycontract", self._make_rules())
        assert doc["info"]["title"] == "mycontract"

    def test_info_version(self):
        doc = export_odcs("test", self._make_rules(), version="3.0")
        assert doc["info"]["version"] == "3.0"

    def test_info_status(self):
        doc = export_odcs("test", self._make_rules(), status="draft")
        assert doc["info"]["status"] == "draft"

    def test_schema_has_properties(self):
        doc = export_odcs("test", self._make_rules())
        assert len(doc["schema"]) == 1
        props = doc["schema"][0]["properties"]
        assert len(props) > 0

    def test_not_empty_maps_to_not_null(self):
        doc = export_odcs("test", self._make_rules())
        email_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "email")
        types = [q["type"] for q in email_prop["quality"]]
        assert "not_null" in types

    def test_must_be_satisfied_for_error(self):
        doc = export_odcs("test", self._make_rules())
        email_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "email")
        nn = next(q for q in email_prop["quality"] if q["type"] == "not_null")
        assert nn["mustBeSatisfied"] is True

    def test_must_be_satisfied_false_for_warning(self):
        doc = export_odcs("test", self._make_rules())
        age_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "age")
        r = next(q for q in age_prop["quality"] if q["type"] == "range")
        assert r["mustBeSatisfied"] is False

    def test_regex_pattern_exported(self):
        doc = export_odcs("test", self._make_rules())
        email_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "email")
        rx = next(q for q in email_prop["quality"] if q["type"] == "regex")
        assert "pattern" in rx

    def test_min_length_exported(self):
        doc = export_odcs("test", self._make_rules())
        name_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "name")
        ml = next(q for q in name_prop["quality"] if q["type"] == "min_length")
        assert ml["minLength"] == 2

    def test_max_length_exported(self):
        doc = export_odcs("test", self._make_rules())
        code_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "code")
        ml = next(q for q in code_prop["quality"] if q["type"] == "max_length")
        assert ml["maxLength"] == 10

    def test_description_in_quality(self):
        doc = export_odcs("test", self._make_rules())
        email_prop = next(p for p in doc["schema"][0]["properties"] if p["name"] == "email")
        nn = next(q for q in email_prop["quality"] if q["type"] == "not_null")
        assert nn["description"] == "required"


# ---------------------------------------------------------------------------
# TestContractToOdcsYaml
# ---------------------------------------------------------------------------

class TestContractToOdcsYaml:
    """contract_to_odcs_yaml() returns a valid YAML string."""

    def test_returns_string(self):
        result = contract_to_odcs_yaml("test", [])
        assert isinstance(result, str)

    def test_valid_yaml(self):
        rules = [{"field": "x", "type": "not_empty", "severity": "error", "error_message": "e"}]
        yaml_str = contract_to_odcs_yaml("test", rules)
        parsed = yaml.safe_load(yaml_str)
        assert parsed["apiVersion"] == "v3.1.0"

    def test_empty_rules_produces_empty_properties(self):
        yaml_str = contract_to_odcs_yaml("test", [])
        parsed = yaml.safe_load(yaml_str)
        assert parsed["schema"][0]["properties"] == []


# ---------------------------------------------------------------------------
# TestODCSAPIEndpoints
# ---------------------------------------------------------------------------

class TestImportODCSEndpoint:
    """POST /api/v1/import/odcs"""

    def test_requires_auth(self, client):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT)
        assert r.status_code == 401

    def test_returns_200_with_auth(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT, headers=editor_headers)
        assert r.status_code == 200

    def test_response_has_contract(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT, headers=editor_headers)
        data = r.json()
        assert "contract" in data

    def test_response_has_rule_count(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT, headers=editor_headers)
        data = r.json()
        assert "rule_count" in data
        assert data["rule_count"] >= 2  # email: not_empty + unique; name: not_empty

    def test_response_has_skipped_checks(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT, headers=editor_headers)
        assert "skipped_checks" in r.json()

    def test_rules_have_correct_fields(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=MINIMAL_CONTRACT, headers=editor_headers)
        rules = r.json()["contract"]["rules"]
        for rule in rules:
            assert "name" in rule
            assert "type" in rule
            assert "field" in rule
            assert "severity" in rule


class TestExportODCSEndpoint:
    """GET /api/v1/export/odcs/{contract_name}"""

    def test_requires_auth(self, client):
        r = client.get("/api/v1/export/odcs/customer")
        assert r.status_code == 401

    def test_returns_200_for_known_contract(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        assert r.status_code == 200

    def test_returns_404_for_unknown_contract(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/nonexistent_zzz", headers=auth_headers)
        assert r.status_code == 404

    def test_content_type_is_yaml(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        assert "yaml" in r.headers.get("content-type", "")

    def test_response_is_valid_yaml(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        parsed = yaml.safe_load(r.text)
        assert parsed is not None

    def test_api_version_in_response(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        parsed = yaml.safe_load(r.text)
        assert parsed["apiVersion"] == "v3.1.0"

    def test_kind_is_data_contract(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        parsed = yaml.safe_load(r.text)
        assert parsed["kind"] == "DataContract"

    def test_schema_has_properties(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        parsed = yaml.safe_load(r.text)
        assert len(parsed["schema"][0]["properties"]) > 0


# ---------------------------------------------------------------------------
# C1/C2 — _odcs_metadata passthrough tests
# ---------------------------------------------------------------------------

class TestOdcsMetadataPassthrough:
    """Tests for preserving unknown top-level ODCS sections (C1/C2)."""

    _CONTRACT_WITH_EXTRA = {
        "apiVersion": "v3.1.0",
        "kind": "DataContract",
        "info": {
            "title": "Order Contract",
            "version": "1.0",
            "status": "active",
        },
        "schema": [
            {
                "name": "orders",
                "properties": [
                    {
                        "name": "order_id",
                        "required": True,
                    }
                ],
            }
        ],
        "sla": {
            "responseTime": "100ms",
            "availability": "99.9%",
        },
        "semantics": {
            "description": "Order domain ontology",
        },
    }

    def test_passthrough_import_preserves_sla(self):
        result = import_odcs(self._CONTRACT_WITH_EXTRA)
        assert "_odcs_metadata" in result
        assert "sla" in result["_odcs_metadata"]
        assert result["_odcs_metadata"]["sla"]["availability"] == "99.9%"

    def test_passthrough_import_rules_unaffected(self):
        result = import_odcs(self._CONTRACT_WITH_EXTRA)
        assert result["rule_count"] == 1
        assert result["contract"]["rules"][0]["type"] == "not_empty"

    def test_clean_import_has_empty_odcs_metadata(self):
        clean = {
            "apiVersion": "v3.1.0",
            "kind": "DataContract",
            "info": {"title": "Clean", "version": "1.0"},
            "schema": [],
        }
        result = import_odcs(clean)
        assert "_odcs_metadata" in result
        assert result["_odcs_metadata"] == {}

    def test_round_trip_preserves_sla_and_semantics(self):
        imported = import_odcs(self._CONTRACT_WITH_EXTRA)
        exported = export_odcs(
            contract_name=imported["contract"]["name"],
            rules=imported["contract"]["rules"],
            odcs_metadata=imported["_odcs_metadata"],
        )
        assert "sla" in exported
        assert exported["sla"]["responseTime"] == "100ms"
        assert "semantics" in exported
        assert exported["semantics"]["description"] == "Order domain ontology"
