"""Tests for CSVW, OTel, and NDC importers."""
from core.importers.csvw import import_csvw, csvw_to_yaml
from core.importers.otel import import_otel, otel_to_yaml
from core.importers.ndc import import_ndc, generate_ndc_rules


class TestCSVWImporter:
    SAMPLE = {
        "@context": "http://www.w3.org/ns/csvw",
        "url": "patients.csv",
        "tableSchema": {
            "columns": [
                {"name": "patient_id", "required": True, "datatype": "string",
                 "constraints": {"pattern": "^[A-Z]{2}[0-9]{6}$"}},
                {"name": "age", "datatype": "integer",
                 "constraints": {"minimum": 0, "maximum": 150}},
                {"name": "status", "datatype": "string",
                 "constraints": {"enum": ["ACTIVE", "INACTIVE"]}},
                {"name": "dob", "datatype": "date"},
            ]
        }
    }

    def test_required_generates_not_empty(self):
        result = import_csvw(self.SAMPLE)
        names = [r["name"] for r in result["rules"]]
        assert "patient_id_required" in names

    def test_pattern_generates_regex(self):
        result = import_csvw(self.SAMPLE)
        regex_rules = [r for r in result["rules"] if r["type"] == "regex"]
        assert any(r["field"] == "patient_id" for r in regex_rules)

    def test_numeric_bounds_generate_range(self):
        result = import_csvw(self.SAMPLE)
        range_rules = [r for r in result["rules"] if r["type"] == "range"]
        assert any(r["field"] == "age" for r in range_rules)

    def test_enum_generates_regex(self):
        # BUG-CSVW-2: lookup_values not supported by validator; enum converted to regex
        result = import_csvw(self.SAMPLE)
        regex_rules = [r for r in result["rules"] if r["type"] == "regex"]
        status_rules = [r for r in regex_rules if r["field"] == "status"]
        assert len(status_rules) == 1
        assert "ACTIVE" in status_rules[0]["pattern"]
        assert "INACTIVE" in status_rules[0]["pattern"]

    def test_date_generates_date_format(self):
        result = import_csvw(self.SAMPLE)
        date_rules = [r for r in result["rules"] if r["type"] == "date_format"]
        assert any(r["field"] == "dob" for r in date_rules)

    def test_to_yaml_produces_valid_yaml(self):
        import yaml
        result = csvw_to_yaml(self.SAMPLE, "test_contract")
        parsed = yaml.safe_load(result)
        assert parsed["contract"]["name"] == "test_contract"
        assert len(parsed["contract"]["rules"]) > 0

    def test_json_string_input(self):
        import json
        result = import_csvw(json.dumps(self.SAMPLE))
        assert len(result["rules"]) > 0

    def test_metadata_source(self):
        result = import_csvw(self.SAMPLE)
        assert result["metadata"]["source"] == "csvw"
        assert result["metadata"]["url"] == "patients.csv"

    def test_min_inclusive_treated_as_minimum(self):
        data = {
            "url": "test.csv",
            "tableSchema": {
                "columns": [
                    {"name": "score", "datatype": "integer",
                     "constraints": {"minInclusive": 1, "maxInclusive": 100}},
                ]
            }
        }
        result = import_csvw(data)
        range_rules = [r for r in result["rules"] if r["type"] == "range"]
        assert len(range_rules) == 1
        assert range_rules[0]["min_value"] == 1.0
        assert range_rules[0]["max_value"] == 100.0

    def test_string_length_constraints(self):
        data = {
            "url": "test.csv",
            "tableSchema": {
                "columns": [
                    {"name": "code", "datatype": "string",
                     "constraints": {"minLength": 3, "maxLength": 10}},
                ]
            }
        }
        result = import_csvw(data)
        types = [r["type"] for r in result["rules"]]
        assert "min_length" in types
        assert "max_length" in types


class TestOTelImporter:
    SAMPLE = {
        "groups": [{
            "id": "trace.http",
            "attributes": [
                {"id": "http.method", "type": "string", "requirement_level": "required",
                 "brief": "HTTP method"},
                {"id": "http.status_code", "type": "int", "requirement_level": "required",
                 "brief": "HTTP status code"},
                {"id": "http.url", "type": "string", "requirement_level": "recommended",
                 "brief": "Full HTTP URL"},
            ]
        }]
    }

    def test_required_generates_not_empty(self):
        result = import_otel(self.SAMPLE)
        names = [r["name"] for r in result["rules"]]
        assert "http_method_required" in names

    def test_known_enum_generates_regex(self):
        # lookup_values not supported by validator; enum converted to regex pattern
        result = import_otel(self.SAMPLE)
        regex_rules = [r for r in result["rules"] if r["type"] == "regex"]
        method_rules = [r for r in regex_rules if r["field"] == "http_method"]
        assert len(method_rules) == 1
        assert "GET" in method_rules[0]["pattern"]

    def test_known_range_generates_range(self):
        result = import_otel(self.SAMPLE)
        range_rules = [r for r in result["rules"] if r["type"] == "range"]
        assert any(r["field"] == "http_status_code" for r in range_rules)

    def test_yaml_output(self):
        import yaml
        result = otel_to_yaml(self.SAMPLE)
        parsed = yaml.safe_load(result)
        assert "contract" in parsed

    def test_metadata_source(self):
        result = import_otel(self.SAMPLE)
        assert result["metadata"]["source"] == "otel"
        assert result["metadata"]["group_count"] == 1

    def test_recommended_severity_is_warning(self):
        result = import_otel(self.SAMPLE)
        # http.url is recommended, not required — any rules for it should be warning
        url_rules = [r for r in result["rules"] if r["field"] == "http_url"]
        for r in url_rules:
            assert r["severity"] == "warning"

    def test_yaml_string_input(self):
        yaml_str = """
groups:
  - id: trace.db
    attributes:
      - id: db.system
        type: string
        requirement_level: required
        brief: DB system
"""
        result = import_otel(yaml_str)
        assert len(result["rules"]) > 0

    def test_member_enum_generates_regex(self):
        # lookup_values not supported by validator; member enum converted to regex
        data = {
            "groups": [{
                "id": "test.group",
                "attributes": [{
                    "id": "custom.attr",
                    "type": {"members": [
                        {"id": "val_a", "value": "a"},
                        {"id": "val_b", "value": "b"},
                    ]},
                    "requirement_level": "recommended",
                    "brief": "Custom attribute",
                }]
            }]
        }
        result = import_otel(data)
        regex_rules = [r for r in result["rules"] if r["type"] == "regex"]
        assert len(regex_rules) == 1
        assert "val_a" in regex_rules[0]["pattern"]


class TestNDCImporter:
    def test_generates_not_empty_and_regex(self):
        result = import_ndc({"fields": ["ndc_code"]})
        types = [r["type"] for r in result["rules"]]
        assert "not_empty" in types
        assert "regex" in types

    def test_multiple_fields(self):
        result = import_ndc({"fields": ["ndc_1", "ndc_2"]})
        fields = [r["field"] for r in result["rules"]]
        assert "ndc_1" in fields
        assert "ndc_2" in fields

    def test_ndc_pattern_valid_formats(self):
        import re
        rules = generate_ndc_rules()
        pattern_rule = next(r for r in rules if r["type"] == "regex")
        p = re.compile(pattern_rule["pattern"])
        # 4-4-2 format (4 digits, hyphen, 4 digits, hyphen, 2 digits)
        assert p.match("0002-3227-01")
        # 5-3-2 format
        assert p.match("12345-123-45")
        # 5-4-1 format
        assert p.match("12345-1234-5")
        # 11-digit no-hyphen format
        assert p.match("12345678901")

    def test_ndc_pattern_rejects_invalid(self):
        import re
        rules = generate_ndc_rules()
        pattern_rule = next(r for r in rules if r["type"] == "regex")
        p = re.compile(pattern_rule["pattern"])
        # Too short
        assert not p.match("1234-567-89")
        # Letters
        assert not p.match("ABCDE-1234-5")
        # Wrong separator
        assert not p.match("12345/1234/5")

    def test_default_field_name(self):
        result = import_ndc()
        assert all(r["field"] == "ndc_code" for r in result["rules"])

    def test_metadata_source(self):
        result = import_ndc()
        assert result["metadata"]["source"] == "ndc"

    def test_custom_severity(self):
        result = import_ndc({"fields": ["ndc_code"], "severity": "warning"})
        assert all(r["severity"] == "warning" for r in result["rules"])

    def test_yaml_output(self):
        import yaml
        from core.importers.ndc import ndc_to_yaml
        result = ndc_to_yaml({"fields": ["ndc_code"]}, "pharma_test")
        parsed = yaml.safe_load(result)
        assert parsed["contract"]["name"] == "pharma_test"
        assert len(parsed["contract"]["rules"]) > 0
