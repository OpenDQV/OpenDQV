"""
ODCS v3.1.0 importer / exporter tests (CRT179, v2.4.0).

Two oracles:
  * the official ODCS v3.1.0 JSON schema, vendored at tests/fixtures/ — every
    export must validate against it;
  * datacontract-cli (`datacontract lint`), when installed — same documents.

Plus: round-trip idempotence on every bundled contract, the spec's own
full example on import, and the import-path safety controls from the
Sonnet pre-implementation review (denied fields, fail-closed on bad rules,
duplicate-field reporting, 422 at the API).
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml

from opendqv.core.importers.odcs import (
    ODCS_API_VERSION,
    _from_jdk_format,
    _to_jdk_format,
    contract_to_odcs_yaml,
    export_odcs,
    import_odcs,
    odcs_to_yaml,
)
from opendqv.core.rule_parser import Rule

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "odcs-json-schema-v3.1.0.json").read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft201909Validator(SCHEMA)  # the schema declares draft 2019-09
FULL_EXAMPLE = yaml.safe_load((FIXTURES / "odcs-full-example-v3.1.0.yaml").read_text(encoding="utf-8"))
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "opendqv" / "contracts"


def _schema_errors(doc: dict) -> list[str]:
    return [f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in VALIDATOR.iter_errors(doc)]


def _rules(*dicts) -> list[Rule]:
    return [Rule(**d) for d in dicts]


def _bundled_contract_names() -> list[str]:
    return sorted(p.stem for p in BUNDLED_DIR.glob("*.yaml"))


def _load_bundled(name: str) -> dict:
    return yaml.safe_load((BUNDLED_DIR / f"{name}.yaml").read_text(encoding="utf-8"))["contract"]


def _export_bundled(name: str) -> dict:
    c = _load_bundled(name)
    return export_odcs(c["name"], [Rule(**r) for r in c.get("rules", [])],
                       version=str(c.get("version", "1.0")), status=c.get("status", "active"),
                       description=c.get("description", ""), owner=c.get("owner", ""),
                       owner_email=c.get("owner_email"))


# A minimal, schema-valid ODCS v3.1.0 document using only native constraints.
NATIVE_ODCS = {
    "apiVersion": "v3.1.0",
    "kind": "DataContract",
    "id": "customer-orders",
    "name": "Customer Orders",
    "version": "2.0",
    "status": "active",
    "description": {"purpose": "Orders placed by customers"},
    "team": {"name": "data-team", "members": [{"username": "owner@example.com", "role": "owner"}]},
    "schema": [
        {
            "name": "orders",
            "logicalType": "object",
            "properties": [
                {"name": "order_id", "logicalType": "string", "required": True, "unique": True,
                 "logicalTypeOptions": {"pattern": "^ORD-[0-9]{6}$", "minLength": 10, "maxLength": 10}},
                {"name": "amount", "logicalType": "number",
                 "logicalTypeOptions": {"minimum": 0, "maximum": 10000}},
                {"name": "qty", "logicalType": "integer", "logicalTypeOptions": {"exclusiveMinimum": 0}},
                {"name": "order_date", "logicalType": "date", "logicalTypeOptions": {"format": "yyyy-MM-dd"}},
                {"name": "country", "logicalType": "string", "quality": [
                    {"type": "library", "metric": "invalidValues", "arguments": {"validValues": ["GB", "IE"]},
                     "mustBe": 0, "severity": "error"},
                    {"type": "library", "metric": "nullValues", "mustBeLessThan": 5, "unit": "percent"},
                    {"type": "text", "description": "Country should follow ISO 3166-1 alpha-2"},
                ]},
                {"name": "email", "logicalType": "string", "quality": [
                    {"type": "library", "metric": "nullValues", "mustBe": 0, "severity": "warning"},
                    {"type": "library", "metric": "duplicateValues", "mustBe": 0},
                    {"type": "sql", "query": "SELECT COUNT(*) FROM orders WHERE email IS NULL", "mustBe": 0},
                ]},
            ],
        }
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Export: schema validity
# ─────────────────────────────────────────────────────────────────────────────

class TestExportIsValidODCS:
    def test_native_fixture_is_itself_valid(self):
        assert _schema_errors(NATIVE_ODCS) == []

    def test_required_top_level_fields_present(self):
        doc = export_odcs("t", _rules({"name": "r", "type": "not_empty", "field": "a"}))
        for key in ("apiVersion", "kind", "id", "version", "status"):
            assert key in doc
        assert doc["apiVersion"] == ODCS_API_VERSION == "v3.1.0"
        assert doc["kind"] == "DataContract"
        assert "info" not in doc

    def test_empty_contract_is_valid(self):
        doc = export_odcs("empty", [], status="draft")
        assert _schema_errors(doc) == []

    @pytest.mark.parametrize("name", _bundled_contract_names())
    def test_every_bundled_contract_exports_valid_odcs(self, name):
        assert _schema_errors(_export_bundled(name)) == []

    def test_status_mapping_to_odcs_vocabulary(self):
        for opendqv_status, odcs_status in (("draft", "draft"), ("review", "proposed"),
                                            ("active", "active"), ("archived", "deprecated")):
            doc = export_odcs("t", [], status=opendqv_status)
            assert doc["status"] == odcs_status
            assert {"property": "opendqv.status", "value": opendqv_status} in doc["customProperties"]

    def test_description_and_team_shape(self):
        doc = export_odcs("t", [], description="purpose text", owner="Team X", owner_email="x@example.com")
        assert doc["description"] == {"purpose": "purpose text"}
        assert doc["team"] == {"name": "Team X", "members": [{"username": "x@example.com", "role": "owner"}]}
        assert _schema_errors(doc) == []

    def test_no_description_or_team_when_empty(self):
        doc = export_odcs("t", [])
        assert "description" not in doc and "team" not in doc

    def test_yaml_helper_round_trips_to_same_dict(self):
        rules = _rules({"name": "r", "type": "regex", "field": "a", "pattern": "^x$"})
        assert yaml.safe_load(contract_to_odcs_yaml("t", rules)) == export_odcs("t", rules)


# ─────────────────────────────────────────────────────────────────────────────
# Export: native projection rules
# ─────────────────────────────────────────────────────────────────────────────

def _prop(doc: dict, field: str) -> dict:
    return next(p for p in doc["schema"][0]["properties"] if p["name"] == field)


class TestExportNativeProjection:
    def test_error_not_empty_and_unique_project_to_flags(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "not_empty", "field": "f"},
                                      {"name": "b", "type": "unique", "field": "f"}))
        p = _prop(doc, "f")
        assert p["required"] is True and p["unique"] is True

    def test_warning_rules_are_not_projected(self):
        doc = export_odcs("t", _rules(
            {"name": "a", "type": "not_empty", "field": "f", "severity": "warning"},
            {"name": "b", "type": "max", "field": "n", "max_value": 150, "severity": "warning"},
            {"name": "c", "type": "regex", "field": "s", "pattern": "^x$", "severity": "warning"}))
        assert "required" not in _prop(doc, "f")
        assert "logicalTypeOptions" not in _prop(doc, "n")
        assert "logicalTypeOptions" not in _prop(doc, "s")
        # …but every rule is still carried as a custom entry with its severity
        for field in ("f", "n", "s"):
            assert _prop(doc, field)["quality"][0]["severity"] == "warning"

    def test_string_options(self):
        doc = export_odcs("t", _rules(
            {"name": "a", "type": "regex", "field": "s", "pattern": "^x$"},
            {"name": "b", "type": "min_length", "field": "s", "min_length": 2},
            {"name": "c", "type": "max_length", "field": "s", "max_length": 5}))
        p = _prop(doc, "s")
        assert p["logicalType"] == "string"
        assert p["logicalTypeOptions"] == {"pattern": "^x$", "minLength": 2, "maxLength": 5}

    def test_number_options_from_min_max_range(self):
        doc = export_odcs("t", _rules(
            {"name": "a", "type": "range", "field": "n", "min": 1, "max": 9},
            {"name": "b", "type": "min", "field": "m", "min_value": 0},
            {"name": "c", "type": "max", "field": "k", "max_value": 100}))
        assert _prop(doc, "n")["logicalTypeOptions"] == {"minimum": 1.0, "maximum": 9.0}
        assert _prop(doc, "m")["logicalTypeOptions"] == {"minimum": 0.0}
        assert _prop(doc, "k")["logicalTypeOptions"] == {"maximum": 100.0}
        assert _prop(doc, "n")["logicalType"] == "number"

    def test_date_format_projects_jdk_pattern(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "date_format", "field": "d", "format": "%Y-%m-%d"}))
        p = _prop(doc, "d")
        assert p["logicalType"] == "date"
        assert p["logicalTypeOptions"] == {"format": "yyyy-MM-dd"}

    def test_unrepresentable_date_format_not_projected(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "date_format", "field": "d", "format": "%d %b %Y"}))
        assert "logicalTypeOptions" not in _prop(doc, "d")
        assert _schema_errors(doc) == []

    def test_conflicting_types_on_one_field_stay_schema_valid(self):
        """regex + min on the same field: number wins, pattern is custom-only (Sonnet blocker #2)."""
        doc = export_odcs("t", _rules(
            {"name": "a", "type": "regex", "field": "f", "pattern": "^[0-9]+$"},
            {"name": "b", "type": "min", "field": "f", "min_value": 0}))
        p = _prop(doc, "f")
        assert p["logicalType"] == "number"
        assert p["logicalTypeOptions"] == {"minimum": 0.0}
        assert _schema_errors(doc) == []
        assert {q["name"] for q in p["quality"]} == {"a", "b"}

    def test_allowed_values_emits_library_invalid_values_as_strings(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "allowed_values", "field": "c",
                                       "allowed_values": [True, 1.0, "pending"]}))
        lib = [q for q in _prop(doc, "c")["quality"] if q["type"] == "library"]
        assert lib == [{"type": "library", "metric": "invalidValues",
                        "arguments": {"validValues": ["True", "1.0", "pending"]},
                        "mustBe": 0, "severity": "error", "dimension": "conformity",
                        "description": "Validation failed"}]
        assert _schema_errors(doc) == []

    def test_non_portable_regex_is_not_projected(self):
        """Lookahead/lookbehind/backreferences crash RE2-based consumers — custom-only."""
        for pat in (r"^(?!BG|GB)[A-Z]{2}$", r"(?<=x)y", r"^(a)\1$", r"(?>ab)c"):
            doc = export_odcs("t", _rules({"name": "a", "type": "regex", "field": "s", "pattern": pat}))
            assert "logicalTypeOptions" not in _prop(doc, "s"), pat
            assert _prop(doc, "s")["quality"][0]["implementation"]["pattern"] == pat

    def test_builtin_pattern_alias_is_expanded_on_projection(self):
        from opendqv.core.rule_parser import _BUILTIN_PATTERNS
        alias, expanded = next(iter(_BUILTIN_PATTERNS.items()))
        doc = export_odcs("t", _rules({"name": "a", "type": "regex", "field": "s", "pattern": alias}))
        projected = _prop(doc, "s").get("logicalTypeOptions", {}).get("pattern")
        assert projected in (expanded, None)   # None only if the builtin itself is non-portable
        assert projected != alias

    def test_datetime_format_uses_timestamp_logical_type(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "date_format", "field": "ts", "format": "%Y-%m-%dT%H:%M:%S"}))
        p = _prop(doc, "ts")
        assert p["logicalType"] == "timestamp"
        assert p["logicalTypeOptions"] == {"format": "yyyy-MM-ddTHH:mm:ss"}
        assert _schema_errors(doc) == []

    def test_unique_with_group_by_becomes_object_level_duplicate_values(self):
        doc = export_odcs("t", _rules({"name": "a", "type": "unique", "field": "sku", "group_by": ["store"]},
                                      {"name": "b", "type": "unique", "field": "line", "group_by": ["order"]}))
        oq = doc["schema"][0]["quality"]
        assert len(oq) == 1   # at most one per object
        assert oq[0]["metric"] == "duplicateValues" and oq[0]["arguments"] == {"properties": ["sku", "store"]}
        assert _schema_errors(doc) == []

    def test_custom_entry_shape(self):
        doc = export_odcs("t", _rules({"name": "cmp", "type": "compare", "field": "end", "compare_to": "start",
                                       "compare_op": "gte", "error_message": "end before start"}))
        q = _prop(doc, "end")["quality"][0]
        assert q["type"] == "custom" and q["engine"] == "opendqv"
        assert q["name"] == "cmp" and q["severity"] == "error" and q["dimension"] == "consistency"
        assert q["description"] == "end before start"
        assert q["implementation"]["type"] == "compare"
        assert q["implementation"]["compare_to"] == "start"
        assert "min" not in q["implementation"] and "inherited" not in q["implementation"]

    def test_every_rule_becomes_exactly_one_custom_entry(self):
        for name in ("customer", "sox_control_test", "hipaa_disclosure_accounting"):
            c = _load_bundled(name)
            doc = _export_bundled(name)
            custom = [q for p in doc["schema"][0]["properties"] for q in p["quality"] if q["type"] == "custom"]
            assert len(custom) == len(c["rules"])
            assert [q["name"] for q in custom] == [r["name"] for r in c["rules"]]

    def test_export_is_deterministic(self):
        assert _export_bundled("customer") == _export_bundled("customer")


# ─────────────────────────────────────────────────────────────────────────────
# Round trip
# ─────────────────────────────────────────────────────────────────────────────

class TestRoundTrip:
    @pytest.mark.parametrize("name", _bundled_contract_names())
    def test_export_import_export_is_idempotent(self, name):
        c = _load_bundled(name)
        doc = _export_bundled(name)
        imported = import_odcs(yaml.safe_load(yaml.dump(doc, sort_keys=False, allow_unicode=True)))
        ic = imported["contract"]
        assert len(ic["rules"]) == len(c.get("rules", []))
        assert imported["skipped_checks"] == []
        doc2 = export_odcs(ic["name"], [Rule(**r) for r in ic["rules"]], ic["version"], ic["status"],
                           ic["description"], ic["owner"], ic.get("owner_email"))
        assert doc2 == doc

    def test_severity_and_message_survive(self):
        rules = _rules({"name": "a", "type": "max", "field": "age", "max_value": 150,
                        "severity": "warning", "error_message": "Age seems high"})
        back = import_odcs(export_odcs("t", rules))["contract"]["rules"]
        assert back == [{"name": "a", "type": "max", "field": "age", "severity": "warning",
                         "error_message": "Age seems high", "max_value": 150.0}]

    def test_status_round_trips_via_custom_property(self):
        for s in ("draft", "review", "active", "archived"):
            assert import_odcs(export_odcs("t", [], status=s))["contract"]["status"] == s


# ─────────────────────────────────────────────────────────────────────────────
# Import: real ODCS documents
# ─────────────────────────────────────────────────────────────────────────────

class TestImportNative:
    def test_full_example_from_spec(self):
        r = import_odcs(FULL_EXAMPLE)
        c = r["contract"]
        assert c["version"] == "1.1.0"
        assert c["status"] == "active"
        assert c["owner"] == "my-team"
        types = {(x["type"], x["field"]) for x in c["rules"]}
        assert ("not_empty", "id") in types
        assert ("unique", "id") in types
        assert ("not_empty", "rcvr_cntry_code") in types
        assert any("rowCount" in s for s in r["skipped_checks"])

    def test_top_level_metadata(self):
        c = import_odcs(NATIVE_ODCS)["contract"]
        assert c["name"] == "customer_orders"
        assert c["version"] == "2.0"
        assert c["status"] == "active"
        assert c["description"] == "Orders placed by customers"
        assert c["owner"] == "data-team"
        assert c["owner_email"] == "owner@example.com"

    def test_name_falls_back_to_id_then_default(self):
        base = {"apiVersion": "v3.1.0", "kind": "DataContract", "version": "1", "status": "draft", "schema": []}
        assert import_odcs({**base, "id": "My-ID 7"})["contract"]["name"] == "my_id_7"
        assert import_odcs(base)["contract"]["name"] == "imported_contract"

    def test_status_vocabulary_mapping(self):
        base = {"apiVersion": "v3.1.0", "kind": "DataContract", "id": "x", "version": "1", "schema": []}
        for odcs, ours in (("proposed", "review"), ("deprecated", "archived"), ("retired", "archived"),
                           ("draft", "draft"), ("active", "active"), ("weird", "draft")):
            assert import_odcs({**base, "status": odcs})["contract"]["status"] == ours

    def _by(self, result, rtype, field):
        return next(r for r in result["contract"]["rules"] if r["type"] == rtype and r["field"] == field)

    def test_flags_and_string_options(self):
        r = import_odcs(NATIVE_ODCS)
        assert self._by(r, "not_empty", "order_id")["severity"] == "error"
        assert self._by(r, "unique", "order_id")
        assert self._by(r, "regex", "order_id")["pattern"] == "^ORD-[0-9]{6}$"
        assert self._by(r, "min_length", "order_id")["min_length"] == 10
        assert self._by(r, "max_length", "order_id")["max_length"] == 10

    def test_number_options(self):
        r = import_odcs(NATIVE_ODCS)
        rng = self._by(r, "range", "amount")
        assert rng["min_value"] == 0.0 and rng["max_value"] == 10000.0
        # Exclusive bounds are skipped, never loosened to inclusive (that would pass a value the document rejects).
        assert not any(x["field"] == "qty" for x in r["contract"]["rules"])
        assert any("qty.exclusiveMinimum" in s and "not loosened" in s for s in r["skipped_checks"])

    def test_date_format_jdk_to_strftime(self):
        r = import_odcs(NATIVE_ODCS)
        assert self._by(r, "date_format", "order_date")["format"] == "%Y-%m-%d"

    def test_library_metrics(self):
        r = import_odcs(NATIVE_ODCS)
        assert self._by(r, "allowed_values", "country")["allowed_values"] == ["GB", "IE"]
        assert self._by(r, "not_empty", "email")["severity"] == "warning"
        assert self._by(r, "unique", "email")["severity"] == "error"

    def test_dataset_level_and_text_sql_are_skipped_with_reason(self):
        r = import_odcs(NATIVE_ODCS)
        joined = " | ".join(r["skipped_checks"])
        assert "country.nullValues" in joined      # percent threshold → dataset-level
        assert "country.text" in joined
        assert "email.sql" in joined

    def test_required_note_is_reported(self):
        r = import_odcs(NATIVE_ODCS)
        assert any(n.startswith("order_id.required") and "stricter" in n for n in r["import_notes"])

    def test_missing_values_null_and_empty_maps_to_not_empty(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [
            {"name": "f", "quality": [{"type": "library", "metric": "missingValues",
                                       "arguments": {"missingValues": [None, ""]}, "mustBe": 0}]},
            {"name": "g", "quality": [{"type": "library", "metric": "missingValues",
                                       "arguments": {"missingValues": [None, "", "N/A"]}, "mustBe": 0}]},
        ]
        r = import_odcs(doc)
        assert self._by(r, "not_empty", "f")
        assert not any(x["field"] == "g" for x in r["contract"]["rules"])
        assert any("g.missingValues" in s and "N/A" not in s for s in r["skipped_checks"])

    def test_invalid_values_pattern_argument_maps_to_regex(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "quality": [
            {"type": "library", "metric": "invalidValues", "arguments": {"pattern": "^[A-Z]+$"}, "mustBe": 0}]}]
        assert self._by(import_odcs(doc), "regex", "f")["pattern"] == "^[A-Z]+$"

    def test_text_entry_with_metric_is_treated_as_library(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "quality": [
            {"type": "text", "metric": "nullValues", "mustBe": 0, "description": "no nulls"}]}]
        assert self._by(import_odcs(doc), "not_empty", "f")["error_message"] == "no nulls"

    def test_default_quality_type_is_library(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "quality": [{"metric": "duplicateValues", "mustBe": 0}]}]
        assert self._by(import_odcs(doc), "unique", "f")

    def test_native_pattern_that_does_not_compile_fails_closed(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "logicalType": "string",
                                           "logicalTypeOptions": {"pattern": "(unclosed"}}]
        with pytest.raises(ValueError, match="f: invalid rule"):
            import_odcs(doc)

    def test_deprecated_rule_key_accepted(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "quality": [{"type": "library", "rule": "nullValues", "mustBe": 0}]}]
        assert self._by(import_odcs(doc), "not_empty", "f")

    def test_v3_0_accepted(self):
        doc = {**copy.deepcopy(NATIVE_ODCS), "apiVersion": "v3.0.0"}
        assert import_odcs(doc)["rule_count"] > 0

    def test_v3_0_team_list_form(self):
        doc = {**copy.deepcopy(NATIVE_ODCS), "apiVersion": "v3.0.0",
               "team": [{"username": "a@example.com", "role": "owner"}]}
        c = import_odcs(doc)["contract"]
        assert c["owner"] == "a@example.com" and c["owner_email"] == "a@example.com"

    def test_rule_names_unique_across_objects_and_flatten_is_reported(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"].append({"name": "second", "properties": [
            {"name": "order_id", "required": True},        # duplicate of orders.order_id
            {"name": "note", "required": True},
        ]})
        r = import_odcs(doc)
        names = [x["name"] for x in r["contract"]["rules"]]
        assert len(names) == len(set(names))
        assert any("second.order_id (duplicate of orders.order_id" in s for s in r["skipped_checks"])
        assert self._by(r, "not_empty", "note")

    def test_native_flag_and_library_entry_not_double_counted(self):
        doc = copy.deepcopy(NATIVE_ODCS)
        doc["schema"][0]["properties"] = [{"name": "f", "required": True,
                                           "quality": [{"type": "library", "metric": "nullValues", "mustBe": 0}]}]
        r = import_odcs(doc)
        assert [x["type"] for x in r["contract"]["rules"]] == ["not_empty"]

    def test_passthrough_metadata(self):
        r = import_odcs(FULL_EXAMPLE)
        assert "servers" in r["_odcs_metadata"]
        assert "schema" not in r["_odcs_metadata"]

    def test_odcs_to_yaml_override_name(self):
        name, text = odcs_to_yaml(NATIVE_ODCS, "override")
        assert name == "override"
        assert yaml.safe_load(text)["contract"]["name"] == "override"


class TestImportCustomOpenDQV:
    def _doc(self, quality, **prop):
        return {"apiVersion": "v3.1.0", "kind": "DataContract", "id": "x", "version": "1", "status": "draft",
                "schema": [{"name": "o", "properties": [{"name": "f", **prop, "quality": quality}]}]}

    def test_custom_overrides_native_projection(self):
        doc = self._doc([{"type": "custom", "engine": "opendqv",
                          "implementation": {"name": "only", "type": "min", "field": "f", "min": 3,
                                             "severity": "warning", "error_message": "m"}}],
                        required=True, logicalTypeOptions={"pattern": "^x$"})
        rules = import_odcs(doc)["contract"]["rules"]
        assert rules == [{"name": "only", "type": "min", "field": "f", "severity": "warning",
                          "error_message": "m", "min_value": 3.0}]

    def test_field_defaults_to_property_name(self):
        doc = self._doc([{"type": "custom", "engine": "opendqv", "implementation": {"type": "not_empty"}}])
        r = import_odcs(doc)["contract"]["rules"][0]
        assert r["field"] == "f" and r["name"] == "f_not_empty"

    def test_other_engine_is_skipped(self):
        doc = self._doc([{"type": "custom", "engine": "soda", "implementation": "checks: []"}])
        r = import_odcs(doc)
        assert r["contract"]["rules"] == [] and "f.custom (engine 'soda')" in r["skipped_checks"]

    @pytest.mark.parametrize("field,value", [
        ("inherited", True),
        ("federation_tier", "REGULATORY"),
        ("provenance", {"authority_node": "evil", "lsn": 1}),
        ("severity_floor", "error"),
        ("lookup_auth_header", "Bearer ${OPENDQV_LOOKUP_TOKEN}"),
    ])
    def test_denied_fields_fail_closed(self, field, value):
        """Sonnet blocker #1: an import must not mint authority/provenance/credential fields."""
        doc = self._doc([{"type": "custom", "engine": "opendqv",
                          "implementation": {"type": "not_empty", field: value}}])
        with pytest.raises(ValueError, match="may not be set by an ODCS import"):
            import_odcs(doc)

    def test_unknown_field_fails_closed(self):
        doc = self._doc([{"type": "custom", "engine": "opendqv",
                          "implementation": {"type": "not_empty", "bogus": 1}}])
        with pytest.raises(ValueError, match="unknown rule field"):
            import_odcs(doc)

    def test_invalid_rule_fails_closed_without_stack_detail(self):
        doc = self._doc([{"type": "custom", "engine": "opendqv",
                          "implementation": {"type": "regex", "pattern": 123}}])
        with pytest.raises(ValueError) as exc:
            import_odcs(doc)
        assert "invalid rule" in str(exc.value) and "Traceback" not in str(exc.value)

    def test_non_dict_implementation_rejected(self):
        doc = self._doc([{"type": "custom", "engine": "opendqv", "implementation": "SELECT 1"}])
        with pytest.raises(ValueError, match="must be an object"):
            import_odcs(doc)


class TestImportRejectsNonODCS3:
    @pytest.mark.parametrize("doc,msg", [
        ({"kind": "DataContract"}, "Unsupported apiVersion"),
        ({"apiVersion": "v2.2.2", "kind": "DataContract"}, "Unsupported apiVersion"),
        ({"apiVersion": "v3.1.0", "kind": "DataProduct"}, "kind must be"),
        ({"apiVersion": "v3.1.0", "kind": "DataContract", "schema": {"not": "a list"}}, "schema must be a list"),
        ("not a mapping", "must be a mapping"),
    ])
    def test_rejected(self, doc, msg):
        with pytest.raises(ValueError, match=msg):
            import_odcs(doc)

    def test_legacy_invented_shape_is_rejected_not_silently_empty(self):
        """The pre-v2.4.0 `info:` + `mustBeSatisfied` shape must not import as a valid-looking empty contract."""
        legacy = {"apiVersion": "v3.1.0", "kind": "DataContract",
                  "info": {"title": "x", "version": "1.0"},
                  "schema": [{"name": "t", "properties": [{"name": "f", "quality": [
                      {"type": "not_null", "mustBeSatisfied": True}]}]}]}
        r = import_odcs(legacy)
        assert r["contract"]["rules"] == []
        assert any("unknown quality type" in s for s in r["skipped_checks"])


# ─────────────────────────────────────────────────────────────────────────────
# Date-format helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestDateFormatConversion:
    @pytest.mark.parametrize("ours,jdk", [
        ("%Y-%m-%d", "yyyy-MM-dd"),
        ("%Y-%m-%dT%H:%M:%S", "yyyy-MM-dd'T'HH:mm:ss".replace("'T'", "T")),
        ("YYYY-MM-DD", "yyyy-MM-dd"),
        ("YYYY-MM-DD HH:MM:SS", "yyyy-MM-dd HH:mm:ss"),
        ("DD/MM/YYYY", "dd/MM/yyyy"),
    ])
    def test_to_jdk(self, ours, jdk):
        assert _to_jdk_format(ours) == jdk

    def test_to_jdk_unsupported_returns_none(self):
        assert _to_jdk_format("%d %b %Y") is None
        assert _to_jdk_format("") is None

    @pytest.mark.parametrize("jdk,strf", [
        ("yyyy-MM-dd", "%Y-%m-%d"),
        ("yyyy-MM-dd HH:mm:ss", "%Y-%m-%d %H:%M:%S"),
        ("dd/MM/yyyy", "%d/%m/%Y"),
    ])
    def test_from_jdk(self, jdk, strf):
        assert _from_jdk_format(jdk) == strf

    def test_from_jdk_unsupported_returns_none(self):
        assert _from_jdk_format("EEE, dd MMM yyyy") is None


# ─────────────────────────────────────────────────────────────────────────────
# API surface
# ─────────────────────────────────────────────────────────────────────────────

class TestODCSAPI:
    def test_export_endpoint_is_schema_valid(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        assert r.status_code == 200
        assert _schema_errors(yaml.safe_load(r.text)) == []

    def test_import_native_document(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json=NATIVE_ODCS, headers=editor_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["rule_count"] > 0
        assert body["contract"]["status"] == "draft"

    def test_import_non_odcs_returns_422(self, client, editor_headers):
        r = client.post("/api/v1/import/odcs", json={"info": {"title": "x"}}, headers=editor_headers)
        assert r.status_code == 422
        assert "Unsupported apiVersion" in r.json()["detail"]

    def test_import_denied_field_returns_422(self, client, editor_headers):
        doc = {"apiVersion": "v3.1.0", "kind": "DataContract", "id": "x", "version": "1", "status": "draft",
               "schema": [{"name": "o", "properties": [{"name": "f", "quality": [
                   {"type": "custom", "engine": "opendqv",
                    "implementation": {"type": "not_empty", "inherited": True}}]}]}]}
        r = client.post("/api/v1/import/odcs", json=doc, headers=editor_headers)
        assert r.status_code == 422
        assert "may not be set" in r.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# Optional external oracle: datacontract-cli
# ─────────────────────────────────────────────────────────────────────────────

_DATACONTRACT = shutil.which("datacontract") or os.environ.get("OPENDQV_DATACONTRACT_BIN")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.mark.skipif(not _DATACONTRACT, reason="datacontract-cli not installed")
class TestDatacontractCliLint:
    @pytest.mark.parametrize("name", ["customer", "sox_control_test", "hipaa_disclosure_accounting"])
    def test_export_passes_datacontract_lint(self, name, tmp_path):
        out = tmp_path / f"{name}.odcs.yaml"
        out.write_text(yaml.dump(_export_bundled(name), sort_keys=False, allow_unicode=True), encoding="utf-8")
        proc = subprocess.run([_DATACONTRACT, "lint", str(out)], capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "data contract is valid" in proc.stdout

    @pytest.mark.parametrize("csv_name,expect_ok,expected_failing_fields", [
        ("odcs_customer_clean.csv", True, set()),
        ("odcs_customer_bad.csv", False, {"age", "email", "id", "name", "password", "phone", "score", "username"}),
    ])
    def test_reference_implementation_executes_projections(self, tmp_path, csv_name, expect_ok, expected_failing_fields):
        """`datacontract test` runs the projected required/unique/logicalTypeOptions checks against a CSV.

        Requires the [duckdb] extra; skipped when the local server type is unavailable.
        """
        doc = _export_bundled("customer")
        csv_path = tmp_path / "rows.csv"
        shutil.copy(FIXTURES / csv_name, csv_path)
        doc["servers"] = [{"server": "local", "type": "local", "path": str(csv_path), "format": "csv"}]
        out = tmp_path / "customer.odcs.yaml"
        out.write_text(yaml.dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        proc = subprocess.run([_DATACONTRACT, "test", str(out)], capture_output=True, text=True, timeout=300)
        text = _strip_ansi(proc.stdout + proc.stderr)
        if "duckdb" in text.lower() and "No module" in text:
            pytest.skip("datacontract-cli installed without the duckdb extra")
        assert ("data contract is valid" in text) is expect_ok, text
        failing = {m.group(1) for m in re.finditer(r"│ failed │[^│]*│ (\w+)\s*│", text)}
        assert failing == expected_failing_fields, text
