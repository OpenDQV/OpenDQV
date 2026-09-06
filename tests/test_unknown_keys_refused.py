"""2.9.0 — unknown rule keys and unknown contract keys are refused at load.

Requested by the managed-engine maintainer (2026-09-06): the same class as the
unknown-type silent pass 2.8.0 closed (#165), one level down. pydantic's
default ``extra="ignore"`` let ``date_diff_feild: start`` load as a rule with
no counterpart that never fired.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from opendqv.core.contracts import CONTRACT_KEYS, ContractRegistry, DataContract
from opendqv.core.linter import lint_contract_yaml
from opendqv.core.rule_parser import RULE_KEYS, Rule, nearest_key, parse_rules

ROOT = Path(__file__).resolve().parents[1]


class TestRuleKeys:
    def test_misspelt_key_is_refused_with_the_nearest_key(self):
        with pytest.raises(ValueError) as exc:
            Rule(name="span", type="date_diff", field="end", date_diff_feild="start",
                 date_diff_unit="days", min_value=0, error_message="m")
        msg = str(exc.value)
        assert 'rule "span"' in msg and '"date_diff_feild"' in msg and 'did you mean "date_diff_field"' in msg
        assert "Known keys:" in msg

    def test_every_unknown_key_is_named(self):
        with pytest.raises(ValueError) as exc:
            Rule(name="r", type="not_empty", field="f", banana=7, Min_Value=3, error_message="m")
        msg = str(exc.value)
        assert '"banana"' in msg and '"Min_Value" (did you mean "min_value"?)' in msg

    def test_managed_engine_s_own_slip_unit_instead_of_date_diff_unit(self):
        with pytest.raises(ValueError, match='"unit"'):
            parse_rules("rules:\n  - {name: d, type: date_diff, field: end, date_diff_field: start, unit: days, min_value: 0, error_message: m}\n")

    def test_aliases_and_every_known_key_are_accepted(self):
        Rule(name="r", type="range", field="f", min=1, max=2, error_message="m")
        Rule(name="r", type="range", field="f", min_value=1, max_value=2, error_message="m")
        for k in RULE_KEYS:
            assert not k.startswith("cached_") and k != "compiled_pattern"

    def test_nested_maps_are_the_author_s_and_never_walked(self):
        # provenance / required_if / forbidden_if values are maps whose keys are
        # not this rule's keys. (condition has its own closed vocabulary since 2.6.0.)
        Rule(name="r", type="not_empty", field="f", error_message="m",
             provenance={"authority_node": "hq", "lsn": 3, "anything_else": True})
        Rule(name="r", type="required_if", field="f", error_message="m",
             required_if={"field": "g", "value": "x", "note": "author's own annotation"})
        Rule(name="r", type="forbidden_if", field="f", error_message="m",
             forbidden_if={"field": "g", "value": "x", "reason": "author's own annotation"})

    def test_yaml_merge_key_and_alias_keep_working_and_an_aliased_rule_is_still_inspected(self):
        rules = parse_rules(
            "rules:\n"
            "  - &base {name: a, type: not_empty, field: f, error_message: m, severity: warning}\n"
            "  - {<<: *base, name: b, field: g}\n"
        )
        assert [(r.name, r.field, r.severity.value) for r in rules] == [("a", "f", "warning"), ("b", "g", "warning")]
        with pytest.raises(ValueError, match='rule "b".*"bannana"'):
            parse_rules(
                "rules:\n"
                "  - &base {name: a, type: not_empty, field: f, error_message: m}\n"
                "  - {<<: *base, name: b, bannana: 1}\n"
            )

    def test_model_dump_round_trip_carries_no_unknown_key(self):
        r = Rule(name="r", type="regex", field="f", pattern="^a$", error_message="m", min_age=3)
        Rule(**r.model_dump(by_alias=True))
        Rule(**r.model_dump(by_alias=True, exclude_none=True))

    def test_nearest_key_heuristics(self):
        assert nearest_key("DateDiffField", RULE_KEYS) == "date_diff_field"
        assert nearest_key("errormessage", RULE_KEYS) == "error_message"
        assert nearest_key("comparto", RULE_KEYS) == "compare_to"
        assert nearest_key("zzzz", RULE_KEYS) is None


def _write(d: Path, name: str, doc: dict) -> Path:
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return p


class TestContractKeys:
    def test_unknown_block_key_refuses_the_file_at_load_with_the_hint(self, tmp_path, caplog):
        _write(tmp_path, "ok", {"contract": {"name": "ok", "version": "1.0", "rules": []}})
        _write(tmp_path, "bad", {"contract": {"name": "bad", "version": "1.0", "onwer": "x", "rules": []}})
        with caplog.at_level(logging.ERROR):
            reg = ContractRegistry(tmp_path)
        assert reg.get("ok") is not None and reg.get("bad") is None
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "bad.yaml" in msg and '"onwer" (did you mean "owner"?)' in msg

    def test_top_level_holder_key_for_anchors_is_unknown(self, tmp_path, caplog):
        (tmp_path / "anch.yaml").write_text(
            "_defaults: &d {severity: warning}\n"
            "contract:\n  name: anch\n  version: '1.0'\n  rules:\n    - {<<: *d, name: a, type: not_empty, field: f, error_message: m}\n",
            encoding="utf-8")
        with caplog.at_level(logging.ERROR):
            reg = ContractRegistry(tmp_path)
        assert reg.get("anch") is None
        assert '"_defaults"' in " ".join(r.getMessage() for r in caplog.records)

    def test_misspelt_rule_key_in_a_stored_file_names_file_rule_and_key(self, tmp_path, caplog):
        _write(tmp_path, "typo", {"contract": {"name": "typo", "version": "1.0", "rules": [
            {"name": "span", "type": "date_diff", "field": "end", "date_diff_feild": "start", "date_diff_unit": "days", "min_value": 0, "error_message": "m"}]}})
        with caplog.at_level(logging.ERROR):
            reg = ContractRegistry(tmp_path)
        assert reg.get("typo") is None
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "typo.yaml" in msg and 'rule "span"' in msg and '"date_diff_feild"' in msg

    def test_legacy_flat_document_is_checked_too(self, tmp_path, caplog):
        _write(tmp_path, "flat", {"name": "flat", "version": "1.0", "descripton": "x",
                                  "rules": [{"name": "a", "type": "not_empty", "field": "f", "error_message": "m"}]})
        with caplog.at_level(logging.ERROR):
            reg = ContractRegistry(tmp_path)
        assert reg.get("flat") is None
        assert '"descripton" (did you mean "description"?)' in " ".join(r.getMessage() for r in caplog.records)

    def test_context_override_with_unknown_key_is_refused_at_load(self, tmp_path, caplog):
        _write(tmp_path, "ctx", {"contract": {"name": "ctx", "version": "1.0",
                                              "rules": [{"name": "a", "type": "min", "field": "age", "min": 18, "error_message": "m"}],
                                              "contexts": {"kids": {"a": {"mn": 13}}}}})
        with caplog.at_level(logging.ERROR):
            reg = ContractRegistry(tmp_path)
        assert reg.get("ctx") is None
        assert "context 'kids'" in " ".join(r.getMessage() for r in caplog.records)

    def test_every_key_the_loader_reads_is_known(self):
        src = (ROOT / "opendqv" / "core" / "contracts.py").read_text(encoding="utf-8")
        body = src.split("def _parse_contract_format")[1].split("def _parse_legacy_format")[0]
        import re
        read = set(re.findall(r'c\.get\("([a-z_]+)"', body)) | set(re.findall(r'c\["([a-z_]+)"\]', body))
        assert read <= CONTRACT_KEYS, read - CONTRACT_KEYS


class TestLinter:
    def test_codes_and_hints(self):
        res = lint_contract_yaml(
            "_anchors: {x: 1}\ncontract:\n  name: t\n  onwer: x\n  rules:\n    - {name: a, type: not_empty, field: f, banana: 7, error_message: m}\n", "t")
        by_code = {}
        for i in res.issues:
            by_code.setdefault(i.code, []).append(i)
        assert len(by_code["UNKNOWN_CONTRACT_KEY"]) == 2
        assert any('"_anchors"' in i.message for i in by_code["UNKNOWN_CONTRACT_KEY"])
        assert any('"onwer" (did you mean "owner"?)' in i.message for i in by_code["UNKNOWN_CONTRACT_KEY"])
        assert [i.rule_name for i in by_code["UNKNOWN_RULE_KEY"]] == ["a"] and all(i.severity == "error" for i in by_code["UNKNOWN_RULE_KEY"])

    def test_bundled_library_and_examples_carry_no_unknown_key(self):
        paths = sorted((ROOT / "opendqv" / "contracts").glob("*.yaml")) + sorted((ROOT / "examples").rglob("*.yaml"))
        assert len(paths) > 50
        offenders = []
        for p in paths:
            res = lint_contract_yaml(p.read_text(encoding="utf-8"), p.stem)
            hits = [i for i in res.issues if i.code in ("UNKNOWN_RULE_KEY", "UNKNOWN_CONTRACT_KEY")]
            if hits:
                offenders.append((str(p.relative_to(ROOT)), [i.message[:80] for i in hits]))
        assert offenders == [], offenders


class TestAttestationRoundTrip:
    FOUR = {"proposed_by": "alice", "proposed_at": "2026-09-01T10:00:00Z",
            "approved_by": "bob", "approved_at": "2026-09-02T10:00:00Z"}

    def test_fresh_file_serialiser_writes_all_four_and_the_other_read_attributes(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        c = DataContract(name="att", version="1.0", status="active", owner="o", owner_email="o@x.org",
                         asset_id="urn:x", sensitive_fields=["ssn"], contexts={"eu": {"a": {"severity": "warning"}}},
                         rules=[Rule(name="a", type="not_empty", field="f", error_message="m")], **self.FOUR)
        raw = yaml.safe_load(reg._contract_to_yaml(c))["contract"]
        for k, v in self.FOUR.items():
            assert raw[k] == v, k
        assert raw["owner_email"] == "o@x.org" and raw["asset_id"] == "urn:x"
        assert raw["sensitive_fields"] == ["ssn"] and raw["contexts"] == {"eu": {"a": {"severity": "warning"}}}
        assert set(raw) <= CONTRACT_KEYS   # and nothing the loader would refuse

    def test_bundled_contract_survives_load_serialise_load(self, tmp_path):
        src = ROOT / "opendqv" / "contracts" / "customer.yaml"
        first = ContractRegistry(_copy(src, tmp_path / "a"))
        c = first.get("customer")
        assert c.proposed_by and c.approved_by and c.approved_at and c.proposed_at, "the bundled library carries all four"
        out = tmp_path / "b"
        out.mkdir()
        (out / "customer.yaml").write_text(first._contract_to_yaml(c), encoding="utf-8")
        again = ContractRegistry(out).get("customer")
        for k in self.FOUR:
            assert getattr(again, k) == getattr(c, k), k

    def test_odcs_export_import_round_trip_keeps_the_trail(self):
        from opendqv.core.importers.odcs import export_odcs, import_odcs
        rules = [Rule(name="a", type="not_empty", field="f", error_message="m")]
        doc = export_odcs("rt", rules, version="1.0", status="active", attestation=self.FOUR)
        props = {p["property"]: p["value"] for p in doc["customProperties"]}
        for k, v in self.FOUR.items():
            assert props[f"opendqv.{k}"] == v
        back = import_odcs(doc)["contract"]
        for k, v in self.FOUR.items():
            assert back[k] == v

    def test_rest_odcs_export_carries_the_trail(self, client, auth_headers):
        r = client.get("/api/v1/export/odcs/customer", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert "opendqv.approved_by" in r.text and "opendqv.proposed_at" in r.text


def _copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    ref = src.parent / "ref"
    if ref.exists():
        import shutil
        shutil.copytree(ref, dest_dir / "ref", dirs_exist_ok=True)
    return dest_dir
