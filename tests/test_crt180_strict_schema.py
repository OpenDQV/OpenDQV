"""CRT180 — `strict_schema: true` + `fields:` allow-list.

Reject records carrying fields the contract does not declare, on both
validation paths, with hash-chain persistence and every export knowing it.
"""
import yaml

from opendqv.core.contracts import (
    ContractHistory,
    DataContract,
    _compute_content_hash,
    _contract_from_snapshot,
)
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import (
    declared_field_set,
    strict_schema_kwargs,
    validate_batch,
    validate_record,
)


def _rules():
    return [
        Rule(name="amount_required", type="not_empty", field="amount", error_message="amount required"),
        Rule(name="settled_after_booked", type="compare", field="settled_at",
             compare_to="booked_at", compare_op="gte", error_message="settle after book"),
        Rule(name="net_required_if_card", type="required_if", field="net",
             required_if={"field": "channel", "value": "card"}, error_message="net required"),
        Rule(name="unique_ref", type="unique", field="ref", group_by=["warehouse"]),
        Rule(name="totals", type="field_sum", field="gross", sum_fields=["net", "vat"],
             sum_equals=0, error_message="sum"),
    ]


class TestDeclaredFieldSet:
    def test_union_of_every_reference(self):
        declared = declared_field_set(_rules(), extra_fields=["trace_id"])
        assert declared == {
            "amount", "settled_at", "booked_at", "net", "channel", "ref", "warehouse",
            "gross", "vat", "trace_id",
        }

    def test_sentinels_are_not_fields(self):
        r = Rule(name="x", type="compare", field="d", compare_to="today", compare_op="lte")
        assert declared_field_set([r]) == {"d"}


class TestSingleRecord:
    def test_default_is_permissive(self):
        res = validate_record({"amount": 1, "surprise": "x"}, [_rules()[0]])
        assert res["valid"] is True

    def test_strict_rejects_undeclared_and_names_them(self):
        kw = {"strict_schema": True, "declared_fields": declared_field_set([_rules()[0]])}
        res = validate_record({"amount": 1, "card_pan": "4111", "notes": "n"}, [_rules()[0]], **kw)
        assert res["valid"] is False
        err = res["errors"][0]
        assert err["error_code"] == "OPENDQV_ADDITIONAL_PROPERTIES"
        assert err["rule"] == "additional_properties"
        assert err["field"] == ""
        assert '"card_pan", "notes"' in err["message"]
        assert "2 unknown field(s)" in err["message"]

    def test_cross_field_references_and_allow_list_count_as_declared(self):
        dc = DataContract(name="c", rules=_rules(), strict_schema=True, allowed_fields=["trace_id"])
        kw = strict_schema_kwargs(dc, dc.rules)
        rec = {"amount": 1, "settled_at": "2026-01-02", "booked_at": "2026-01-01", "channel": "cash",
               "ref": "r1", "warehouse": "LON", "gross": 0, "net": 0, "vat": 0, "trace_id": "t"}
        assert validate_record(rec, dc.rules, **kw)["valid"] is True

    def test_kwargs_helper_is_empty_for_non_strict(self):
        assert strict_schema_kwargs(DataContract(name="c", rules=_rules()), _rules()) == {}


class TestBatchParity:
    def test_batch_matches_single(self):
        rules = [_rules()[0]]
        kw = {"strict_schema": True, "declared_fields": declared_field_set(rules)}
        records = [{"amount": 1}, {"amount": 1, "extra": 1}, {"amount": ""}]
        single = [validate_record(r, rules, **kw) for r in records]
        batch = validate_batch(records, rules, **kw)
        for s, b in zip(single, batch["results"]):
            assert s["valid"] == b["valid"]
            assert sorted(e["error_code"] for e in s["errors"]) == sorted(e["error_code"] for e in b["errors"])
        assert batch["summary"]["failed"] == 2


class TestPersistence:
    def test_hash_unchanged_when_unset_and_changes_when_set(self):
        args = ("c", "1.0", "active", "o", None, None, None, "", [], [], {})
        base = _compute_content_hash(*args)
        assert _compute_content_hash(*args, strict_schema=False, allowed_fields=[]) == base
        assert _compute_content_hash(*args, strict_schema=True) != base
        assert _compute_content_hash(*args, allowed_fields=["x"]) != base

    def test_history_round_trip(self):
        history = ContractHistory(db_path=":memory:")
        dc = DataContract(name="strict_probe", version="1.0", rules=_rules(),
                          strict_schema=True, allowed_fields=["trace_id"])
        history.record_version(dc)
        entry = history.get_history("strict_probe")[-1]
        assert entry["strict_schema"] is True
        assert entry["allowed_fields"] == ["trace_id"]
        rebuilt = _contract_from_snapshot("strict_probe", entry)
        assert rebuilt.strict_schema is True and rebuilt.allowed_fields == ["trace_id"]
        # a second identical record is deduped; flipping strict off is a new entry
        history.record_version(dc)
        assert len(history.get_history("strict_probe")) == 1
        dc2 = dc.model_copy(update={"strict_schema": False, "allowed_fields": []})
        history.record_version(dc2)
        assert len(history.get_history("strict_probe")) == 2
        assert history.get_history("strict_probe")[-1]["strict_schema"] is False

    def test_yaml_round_trip_through_registry(self, tmp_path):
        from opendqv.core.contracts import ContractRegistry
        (tmp_path / "strict_probe.yaml").write_text(yaml.safe_dump({"contract": {
            "name": "strict_probe", "version": "1.0", "strict_schema": True, "allowed_fields": ["trace_id"],
            "rules": [{"name": "a", "type": "not_empty", "field": "amount"}],
        }}), encoding="utf-8")
        reg = ContractRegistry(tmp_path)
        dc = reg.get("strict_probe")
        assert dc.strict_schema is True and dc.allowed_fields == ["trace_id"]
        out = yaml.safe_load(reg._contract_to_yaml(dc))["contract"]
        assert out["strict_schema"] is True and out["allowed_fields"] == ["trace_id"]
        permissive = reg._contract_to_yaml(dc.model_copy(update={"strict_schema": False, "allowed_fields": []}))
        assert "strict_schema" not in permissive


class TestExports:
    def test_jsonschema_additional_properties_false_and_fields_declared(self):
        from opendqv.core.jsonschema import contract_to_jsonschema
        dc = DataContract(name="c", rules=[_rules()[0]], strict_schema=True, allowed_fields=["trace_id"])
        schema = contract_to_jsonschema(dc)
        assert schema["additionalProperties"] is False
        assert "trace_id" in schema["properties"]
        assert contract_to_jsonschema(DataContract(name="c", rules=[_rules()[0]]), strict=None)["additionalProperties"] is True

    def test_odcs_round_trip(self):
        from opendqv.core.importers import odcs
        doc = odcs.export_odcs("c", [_rules()[0]], strict_schema=True, allowed_fields=["trace_id"])
        props = {cp["property"]: cp["value"] for cp in doc["customProperties"]}
        assert props["opendqv.strict_schema"] is True and props["opendqv.allowed_fields"] == ["trace_id"]
        back = odcs.import_odcs(doc)["contract"]
        assert back["strict_schema"] is True and back["allowed_fields"] == ["trace_id"]

    def test_linter_shapes(self):
        from opendqv.core.linter import lint_contract_yaml
        bad = "contract:\n  name: c\n  version: '1.0'\n  strict_schema: yes_please\n  allowed_fields: [1]\n  rules:\n    - {name: a, type: not_empty, field: amount}\n"
        codes = {i.code for i in lint_contract_yaml(bad, "c").issues}
        assert {"STRICT_SCHEMA_NOT_BOOL", "ALLOWED_FIELDS_NOT_STRING_LIST"} <= codes
        warn = "contract:\n  name: c\n  version: '1.0'\n  allowed_fields: [trace_id]\n  rules:\n    - {name: a, type: not_empty, field: amount}\n"
        assert "ALLOWED_FIELDS_WITHOUT_STRICT_SCHEMA" in {i.code for i in lint_contract_yaml(warn, "c").issues}
