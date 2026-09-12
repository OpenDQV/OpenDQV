"""ODCS v3.2.0 — the import door (2.10.0).

The managed engine reads and writes ODCS v3.2.0 (released 2026-09-08) and the
Data Contract CLI 1.2.0 stamps `apiVersion: v3.2.0` on everything it writes, so
a Core that stopped at 3.1.0 refused both. 3.2.0 is additive over 3.1.0 plus
one loosening (top-level `status` is no longer required).

Every document here is validated against the **official** vendored 3.2.0 JSON
schema before it is imported, so a passing test cannot be resting on an
invented shape (the CRT179 lesson: a format claim needs an oracle the engine
does not control).
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from opendqv.core.importers.odcs import (
    _SUPPORTED_API_VERSIONS,
    _enum_values,
    import_odcs,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_320 = json.loads((FIXTURES / "odcs-json-schema-v3.2.0.json").read_text(encoding="utf-8"))
VALIDATOR_320 = jsonschema.Draft201909Validator(SCHEMA_320)


def _doc(properties: list[dict], **overrides) -> dict:
    """A minimal 3.2.0 document. No `status`: 3.2.0 dropped it from the
    required set, and a document without one must import."""
    doc = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": "urn:opendqv:test:door",
        "version": "1.0.0",
        "schema": [{"name": "orders", "logicalType": "object", "properties": properties}],
    }
    doc.update(overrides)
    return doc


def _valid_320(doc: dict) -> dict:
    """Assert the document really is ODCS 3.2.0, then hand it back."""
    errors = sorted(VALIDATOR_320.iter_errors(doc), key=lambda e: e.path)
    assert not errors, "test document is not schema-valid 3.2.0: " + "; ".join(
        f"{list(e.path)}: {e.message}" for e in errors[:3])
    return doc


def _import(doc: dict) -> dict:
    return import_odcs(_valid_320(doc))


def _rules(out: dict, field: str | None = None) -> list[dict]:
    return [r for r in out["contract"]["rules"] if field is None or r["field"] == field]


class TestTheSchemaIsWhatItClaims:
    """Guards on the vendored oracle itself."""

    def test_schema_declares_v3_2_0_and_accepts_the_older_spellings(self):
        api = SCHEMA_320["properties"]["apiVersion"]
        assert api["default"] == "v3.2.0"
        assert {"v3.2.0", "v3.1.0", "v3.0.2", "v3.0.1", "v3.0.0"} <= set(api["enum"])

    def test_status_is_no_longer_required(self):
        assert "status" not in SCHEMA_320["required"]
        assert set(SCHEMA_320["required"]) == {"version", "apiVersion", "kind", "id"}

    def test_the_door_matches_the_standard_s_own_list(self):
        supported = {v for v in SCHEMA_320["properties"]["apiVersion"]["enum"] if v.startswith("v3.")}
        assert _SUPPORTED_API_VERSIONS == supported, (
            "Core's allow-list has drifted from the ODCS 3.x versions the vendored schema names")

    def test_property_level_enum_and_the_new_logical_types_exist(self):
        prop = SCHEMA_320["$defs"]["SchemaBaseProperty"]["properties"]
        assert prop["enum"]["items"]["$ref"].endswith("EnumValue")
        assert {"map", "vector"} <= set(prop["logicalType"]["enum"])
        assert prop["semanticType"]["enum"] == ["column", "measure", "dimension"]


class TestTheDoor:
    def test_a_v3_2_0_document_imports(self):
        out = _import(_doc([{"name": "id", "logicalType": "string", "required": True}]))
        assert [r["type"] for r in _rules(out, "id")] == ["not_empty"]

    def test_a_document_without_status_defaults_to_draft(self):
        out = _import(_doc([{"name": "id", "logicalType": "string"}]))
        assert out["contract"]["status"] == "draft"

    @pytest.mark.parametrize("version", sorted(_SUPPORTED_API_VERSIONS))
    def test_every_supported_version_still_opens(self, version):
        doc = _doc([{"name": "id", "logicalType": "string", "required": True}], apiVersion=version)
        doc["status"] = "active"          # required before 3.2.0; harmless after
        assert import_odcs(doc)["contract"]["rules"]

    def test_an_unknown_version_is_still_refused_by_name(self):
        with pytest.raises(ValueError, match="v4.0.0"):
            import_odcs(_doc([{"name": "id", "logicalType": "string"}], apiVersion="v4.0.0"))


class TestPropertyEnum:
    ENUM = [
        {"value": "ACTIVE", "label": "Active", "id": "01JBX0000000000000000ACTIV",
         "description": "Open for trading", "tags": ["public"]},
        {"value": "SUSPENDED", "label": "Suspended"},
        {"value": "CLOSED"},
    ]

    def test_enum_objects_become_allowed_values_from_their_value_alone(self):
        out = _import(_doc([{"name": "state", "logicalType": "string", "enum": self.ENUM}]))
        rules = _rules(out, "state")
        assert [r["type"] for r in rules] == ["allowed_values"]
        assert rules[0]["allowed_values"] == ["ACTIVE", "SUSPENDED", "CLOSED"]

    def test_the_labels_and_ids_are_governance_metadata_not_values(self):
        out = _import(_doc([{"name": "state", "logicalType": "string", "enum": self.ENUM}]))
        listed = _rules(out, "state")[0]["allowed_values"]
        assert not any(x in listed for x in ("Active", "Suspended", "Open for trading", "public"))

    def test_non_string_enum_values_are_rendered_as_text(self):
        doc = _doc([{"name": "tier", "logicalType": "integer",
                     "enum": [{"value": 1}, {"value": 2}, {"value": 3}]}])
        assert _rules(_import(doc), "tier")[0]["allowed_values"] == ["1", "2", "3"]

    def test_the_cli_shim_is_a_cli_extension_not_a_schema_valid_construct(self):
        """`logicalTypeOptions` is a closed object per logical type in 3.2.0, so
        the Data Contract CLI's `logicalTypeOptions.enum` is an extension the
        schema rejects. Core reads it anyway because the CLI writes it — but
        the distinction is pinned here so nobody mistakes it for the standard."""
        doc = _doc([{"name": "state", "logicalType": "string",
                     "logicalTypeOptions": {"enum": ["A", "B"]}}])
        assert list(VALIDATOR_320.iter_errors(doc)), "the shim is expected to be schema-invalid"

    def test_precedence_enum_over_the_cli_shim_over_the_library_twin(self):
        """`enum` → `logicalTypeOptions.enum` → `invalidValues.validValues`,
        the order the reference CLI reads them in. (Not schema-validated: the
        middle spelling is a CLI extension, see the test above.)"""
        both = _doc([{
            "name": "state", "logicalType": "string",
            "enum": [{"value": "FROM_ENUM"}],
            "logicalTypeOptions": {"enum": ["FROM_SHIM"]},
            "quality": [{"type": "library", "metric": "invalidValues", "mustBe": 0,
                         "arguments": {"validValues": ["FROM_TWIN"]}}],
        }])
        rules = _rules(import_odcs(both), "state")
        assert [r["type"] for r in rules] == ["allowed_values"], "one rule per property, never two"
        assert rules[0]["allowed_values"] == ["FROM_ENUM"]

    def test_the_cli_shim_alone_is_read_and_named_as_a_pre_3_2_0_spelling(self):
        doc = _doc([{"name": "state", "logicalType": "string",
                     "logicalTypeOptions": {"enum": ["A", "B"]}}])
        out = import_odcs(doc)
        assert _rules(out, "state")[0]["allowed_values"] == ["A", "B"]
        assert any("logicalTypeOptions.enum" in n for n in out["import_notes"])

    def test_the_shim_is_outranked_by_a_real_enum_and_outranks_the_twin(self):
        shim_and_twin = _doc([{
            "name": "state", "logicalType": "string",
            "logicalTypeOptions": {"enum": ["FROM_SHIM"]},
            "quality": [{"type": "library", "metric": "invalidValues", "mustBe": 0,
                         "arguments": {"validValues": ["FROM_TWIN"]}}],
        }])
        assert _rules(import_odcs(shim_and_twin), "state")[0]["allowed_values"] == ["FROM_SHIM"]

    def test_the_library_twin_alone_is_still_read(self):
        doc = _doc([{"name": "state", "logicalType": "string",
                     "quality": [{"type": "library", "metric": "invalidValues", "mustBe": 0,
                                  "arguments": {"validValues": ["A", "B"]}}]}])
        assert _rules(_import(doc), "state")[0]["allowed_values"] == ["A", "B"]

    def test_a_cloud_export_carrying_only_enum_is_counted_once(self):
        """The managed engine stopped emitting the twin beside an enum because
        the reference runner executed both and double-counted the same bad
        value. Core must not reintroduce the pair from one enum."""
        out = _import(_doc([{"name": "state", "logicalType": "string", "enum": self.ENUM}]))
        assert len([r for r in _rules(out, "state") if r["type"] == "allowed_values"]) == 1

    def test_an_entry_without_a_value_enumerates_nothing(self):
        assert _enum_values({"enum": [{"label": "only a label"}]}) == ([], None)

    def test_enum_sits_alongside_the_other_derived_rules(self):
        doc = _doc([{"name": "state", "logicalType": "string", "required": True,
                     "unique": True, "enum": [{"value": "A"}],
                     "logicalTypeOptions": {"maxLength": 8}}])
        assert {r["type"] for r in _rules(_import(doc), "state")} == {
            "not_empty", "unique", "allowed_values", "max_length"}


class TestUnsupportedLogicalTypes:
    MAP = {"name": "payload", "logicalType": "map", "required": True,
           "map": {"key": {"logicalType": "string"}, "value": {"logicalType": "string"}}}
    VECTOR = {"name": "payload", "logicalType": "vector", "required": True,
              "logicalTypeOptions": {"dimensions": 1536, "elementType": "float32"}}

    @pytest.mark.parametrize("ltype", ["map", "vector"])
    def test_the_property_is_named_not_dropped(self, ltype):
        prop = dict(self.MAP if ltype == "map" else self.VECTOR)
        doc = _doc([{"name": "id", "logicalType": "string", "required": True}, prop])
        out = _import(doc)
        assert _rules(out, "payload") == [], "no rule may be derived from a type we cannot read"
        assert any("payload" in s and ltype in s for s in out["skipped_checks"]), out["skipped_checks"]
        assert [r["field"] for r in out["contract"]["rules"]] == ["id"], "the rest of the object still imports"

    def test_nothing_on_the_property_is_read_including_a_custom_twin(self):
        prop = dict(self.MAP)
        prop["quality"] = [{"type": "custom", "engine": "opendqv",
                            "implementation": {"type": "not_empty", "field": "payload",
                                               "name": "payload_req", "error_message": "m"}}]
        out = _import(_doc([prop]))
        assert out["contract"]["rules"] == []
        assert len([s for s in out["skipped_checks"] if "payload" in s]) == 1, "named once, for the property"


class TestAcceptedWithoutRules:
    """Understood, nothing to enforce: no rule, no skip entry, no error."""

    PROP = {
        "name": "id", "logicalType": "string", "required": True,
        "semanticType": "dimension",
        "synonyms": [{"synonym": "customer_ref", "description": "legacy name"}],
        "deprecated": False,
        "examples": ["CUST-001"],
    }

    def test_property_level_metadata_is_silent(self):
        out = _import(_doc([dict(self.PROP)]))
        assert [r["type"] for r in _rules(out, "id")] == ["not_empty"]
        assert not [s for s in out["skipped_checks"] if "id" in s]

    def test_context_is_silent_where_the_schema_allows_it(self):
        """3.2.0 puts `context` (RFC-0038) on the document and on a schema
        object, not on a property — Core reads neither."""
        doc = _doc([{"name": "id", "logicalType": "string", "required": True}],
                   context={"instructions": "Orders placed through any channel."})
        doc["schema"][0]["context"] = "One row per order line."
        out = _import(doc)
        assert [r["type"] for r in _rules(out, "id")] == ["not_empty"]
        assert not out["skipped_checks"]

    def test_object_level_synonyms_and_deprecated_are_silent(self):
        doc = _doc([{"name": "id", "logicalType": "string", "required": True}])
        doc["schema"][0].update({"synonyms": [{"synonym": "purchases"}], "deprecated": True})
        out = _import(doc)
        assert [r["type"] for r in _rules(out, "id")] == ["not_empty"]
        assert not out["skipped_checks"]

    def test_a_deprecated_element_never_moves_the_contract_status(self):
        doc = _doc([{"name": "id", "logicalType": "string", "required": True, "deprecated": True}])
        doc["schema"][0]["deprecated"] = True
        assert _import(doc)["contract"]["status"] == "draft"

    @pytest.mark.parametrize("semantic", ["column", "measure", "dimension"])
    def test_every_semantic_type_in_the_closed_enum_is_accepted(self, semantic):
        doc = _doc([{"name": "amount", "logicalType": "number", "semanticType": semantic}])
        assert _import(doc)["contract"]["name"]


class TestExportStillReads:
    """Export stays 3.1.0-shaped; a 3.2.0 reader accepts that spelling, and
    Core's own door reads what Core writes."""

    def test_export_round_trips_through_the_3_2_0_door(self):
        from opendqv.core.importers.odcs import ODCS_API_VERSION, export_odcs
        from opendqv.core.rule_parser import Rule
        rules = [Rule(name="state_values", type="allowed_values", field="state",
                      allowed_values=["A", "B"], error_message="m")]
        doc = export_odcs("orders", rules, version="1.0.0", status="active")
        assert doc["apiVersion"] == ODCS_API_VERSION == "v3.1.0"
        assert not list(jsonschema.Draft201909Validator(SCHEMA_320).iter_errors(doc)), \
            "a 3.1.0-shaped export must still be a valid 3.2.0 document"
        back = import_odcs(doc)["contract"]["rules"]
        assert [(r["type"], r["allowed_values"]) for r in back if r["type"] == "allowed_values"] == \
               [("allowed_values", ["A", "B"])]


class TestOdcsPassthroughBlock:
    """The managed engine's native YAML may carry a top-level `odcs:` block —
    opaque maps of the ODCS sections it does not enforce. Core's choice:
    carry it verbatim rather than refuse the file. Refusing would mean Core
    could not load a contract written by the other engine at all, which is the
    opposite of the parity the library is mirrored for; and unlike a misspelt
    key (2.9.0) this is a recognised block, not a typo that silently does
    nothing. It is preserved on write, reported by lint, and enforces nothing.
    """

    BLOCK = {
        "document": {"tags": ["pii"], "slaProperties": [{"property": "latency", "value": 4}]},
        "object": {"dataGranularityDescription": "one row per order line"},
        "properties": [{"name": "state", "body": {"physicalType": "varchar(16)",
                                                  "context": "the account state"}}],
    }

    def _write(self, tmp_path, **extra):
        import yaml
        tmp_path.mkdir(parents=True, exist_ok=True)
        block = {"name": "passthru", "version": "1.0",
                 "rules": [{"name": "id_req", "type": "not_empty", "field": "id", "error_message": "id required"}]}
        block.update(extra)
        (tmp_path / "passthru.yaml").write_text(
            yaml.safe_dump({"contract": block}, sort_keys=False), encoding="utf-8")
        return tmp_path

    def test_a_contract_carrying_the_block_loads(self, tmp_path):
        from opendqv.core.contracts import ContractRegistry
        reg = ContractRegistry(self._write(tmp_path, odcs=self.BLOCK))
        c = reg.get("passthru")
        assert c is not None, "refusing it would lock Core out of the other engine's contracts"
        assert c.odcs == self.BLOCK

    def test_it_survives_a_write_verbatim(self, tmp_path):
        import yaml
        from opendqv.core.contracts import ContractRegistry
        reg = ContractRegistry(self._write(tmp_path, odcs=self.BLOCK))
        rewritten = yaml.safe_load(reg._contract_to_yaml(reg.get("passthru")))["contract"]
        assert rewritten["odcs"] == self.BLOCK

    def test_it_contributes_no_rules(self, tmp_path):
        from opendqv.core.contracts import ContractRegistry
        c = ContractRegistry(self._write(tmp_path, odcs=self.BLOCK)).get("passthru")
        assert [r.name for r in c.rules] == ["id_req"]

    def test_load_says_it_is_carried_not_enforced(self, tmp_path, caplog):
        import logging

        from opendqv.core.contracts import ContractRegistry
        with caplog.at_level(logging.INFO, logger="opendqv.core.contracts"):
            ContractRegistry(self._write(tmp_path, odcs=self.BLOCK))
        said = " ".join(r.getMessage() for r in caplog.records)
        assert "odcs" in said and "enforces nothing" in said

    def test_lint_reports_it_as_information_not_an_error(self, tmp_path):
        import yaml

        from opendqv.core.linter import lint_contract_yaml
        path = self._write(tmp_path, odcs=self.BLOCK) / "passthru.yaml"
        res = lint_contract_yaml(path.read_text(encoding="utf-8"), "passthru")
        hits = [i for i in res.issues if i.code == "ODCS_BLOCK_NOT_ENFORCED"]
        assert [i.severity for i in hits] == ["info"]
        assert res.passed, "an unenforced block is not a lint failure"
        assert not yaml.safe_load(path.read_text(encoding="utf-8"))["contract"].get("rules_from_odcs")

    def test_a_contract_without_the_block_says_nothing(self, tmp_path):
        from opendqv.core.contracts import ContractRegistry
        from opendqv.core.linter import lint_contract_yaml
        path = self._write(tmp_path) / "passthru.yaml"
        assert ContractRegistry(tmp_path).get("passthru").odcs == {}
        res = lint_contract_yaml(path.read_text(encoding="utf-8"), "passthru")
        assert not [i for i in res.issues if i.code == "ODCS_BLOCK_NOT_ENFORCED"]

    def test_it_is_outside_the_hash_domain(self, tmp_path):
        """Opaque metadata must not change a contract's identity: two contracts
        differing only by the block hash the same."""
        from opendqv.core.contracts import ContractRegistry, _compute_content_hash

        def _hash_of(c):
            return _compute_content_hash(
                c.name, c.version, c.status.value, c.owner, c.owner_email, c.owner_team,
                c.asset_id, c.description, c.downstream_consumers,
                [r.model_dump(by_alias=True, mode="json") for r in c.rules], c.contexts,
                c.strict_schema, c.allowed_fields)

        plain = ContractRegistry(self._write(tmp_path / "a", odcs={})).get("passthru")
        carried = ContractRegistry(self._write(tmp_path / "b", odcs=self.BLOCK)).get("passthru")
        assert _hash_of(plain) == _hash_of(carried)
