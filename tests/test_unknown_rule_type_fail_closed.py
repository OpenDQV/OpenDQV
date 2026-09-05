"""2.8.0 — an unknown `type:` is refused wherever a rule enters the engine (fail closed).

Both engines adopt this the same day (Mac Claude / Pilot decision, 2026-09-03):
honour or refuse, never silently drop. Core's stored-vs-submitted answer: a
stored YAML with an unknown type FAILS TO LOAD at startup (logged, contract
absent → 404), exactly like any other invalid rule — lint before deploy.
"""
from __future__ import annotations

import logging

import pytest
import yaml

from opendqv.core.contracts import ContractRegistry
from opendqv.core.linter import _KNOWN_RULE_TYPES
from opendqv.core.rule_parser import RULE_TYPES, Rule
from opendqv.core.validator import _RULE_HANDLERS


def test_model_refuses_unknown_type_and_names_the_known_set():
    with pytest.raises(ValueError) as exc:
        Rule(name="r", type="min_lenght", field="f", min_length=3)
    msg = str(exc.value)
    assert "unknown rule type 'min_lenght'" in msg
    for t in ("min_length", "not_empty", "regex"):
        assert t in msg


@pytest.mark.parametrize("key", ["min_age", "max_age"])
def test_age_keys_are_not_types_and_the_message_says_so(key):
    with pytest.raises(ValueError, match="is a key on a `date_format` rule"):
        Rule(name="r", type=key, field="dob")
    # the real spelling still works
    Rule(name="r", type="date_format", field="dob", format="%Y-%m-%d", **{key: 18})


def test_every_known_type_constructs():
    for t in RULE_TYPES:
        Rule(name="r", type=t, field="f")


def test_linter_validator_and_model_agree_on_the_type_set():
    """Issue #163: one closed set, three consumers."""
    assert frozenset(_RULE_HANDLERS) == RULE_TYPES == _KNOWN_RULE_TYPES


def test_stored_yaml_with_unknown_type_fails_to_load(tmp_path, caplog):
    """Core's stored-content answer: refuse at load (same as any other invalid rule)."""
    d = tmp_path / "c"
    d.mkdir()
    (d / "good.yaml").write_text(yaml.safe_dump({"contract": {"name": "good", "version": "1.0", "rules": [
        {"name": "r", "type": "not_empty", "field": "f"}]}}), encoding="utf-8")
    (d / "typo.yaml").write_text(yaml.safe_dump({"contract": {"name": "typo", "version": "1.0", "rules": [
        {"name": "r", "type": "not_emtpy", "field": "f"}]}}), encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        reg = ContractRegistry(d)
    assert reg.get("good") is not None
    assert reg.get("typo") is None
    assert any("typo" in rec.getMessage() and "not_emtpy" in rec.getMessage() for rec in caplog.records), \
        "the refusal must be logged with the file and the offending type"


def test_rest_add_rule_returns_422(client, editor_headers):
    from opendqv.api import deps as _d
    _d.registry.create_draft(name="MCP_unk_rest", description="d", owner="o", created_by="a",
                                 rules_data=[{"name": "r", "type": "not_empty", "field": "f"}])
    try:
        r = client.post("/api/v1/contracts/MCP_unk_rest/rules",
                        json={"name": "x", "type": "not_emtpy", "field": "g"}, headers=editor_headers)
        assert r.status_code == 422
        assert "unknown rule type" in r.json()["detail"]
        assert [x.name for x in _d.registry.get("MCP_unk_rest").rules] == ["r"]
    finally:
        for p in _d.registry.contracts_dir.glob("MCP_unk_rest*.yaml"):
            p.unlink()
        _d.registry._contracts.pop("MCP_unk_rest", None)
        _d.registry._contract_paths.pop("MCP_unk_rest", None)


@pytest.mark.asyncio
async def test_mcp_create_draft_returns_error_envelope(tmp_path, monkeypatch):
    import json

    import opendqv.mcp_server as m
    d = tmp_path / "c"
    d.mkdir()
    monkeypatch.setattr(m, "_registry", ContractRegistry(d))
    out = await m._tool_create_contract_draft({
        "name": "MCP_unk", "description": "d", "owner": "o", "created_by": "agent",
        "rules": [{"name": "r", "type": "min_lenght", "field": "f", "min_length": 2}],
    })
    body = json.loads(out[0].text)
    err = body.get("error", body)
    assert err.get("status") in (400, 422)
    assert "unknown rule type" in json.dumps(body)
    assert not list(d.glob("*.yaml"))


def test_odcs_custom_implementation_with_unknown_type_is_refused():
    from opendqv.core.importers.odcs import import_odcs
    doc = {"apiVersion": "v3.1.0", "kind": "DataContract", "id": "x", "version": "1", "status": "draft",
           "schema": [{"name": "o", "properties": [{"name": "f", "quality": [
               {"type": "custom", "engine": "opendqv", "implementation": {"type": "not_emtpy"}}]}]}]}
    with pytest.raises(ValueError, match="unknown rule type"):
        import_odcs(doc)


def test_validate_record_never_sees_an_unknown_type():
    """The engine's fallback is now a hard error, not a silent pass, for any bypass."""
    from opendqv.core.validator import validate_record
    rule = Rule.model_construct(**{**Rule(name="r", type="regex", field="f", pattern="^a$").model_dump(),
                                   "type": "future_type"})
    out = validate_record({"f": "a"}, [rule], "t")
    # a rule the engine cannot evaluate fails the record (OPENDQV_RULE_ERROR) — never passes it
    assert not out["valid"]
    assert out["errors"][0]["error_code"] == "OPENDQV_RULE_ERROR"


# ── Blind review of PR #165 (2026-09-05): two Rule() construction paths were
# still outside the closed set and turned a 2.7.0 silent pass into a 500. ──

def _contract_yaml(name, rules, contexts=None):
    c = {"name": name, "version": "1.0", "rules": rules}
    if contexts:
        c["contexts"] = contexts
    return yaml.safe_dump({"contract": c})


def test_context_override_with_unknown_type_refuses_the_file_at_load(tmp_path, caplog):
    d = tmp_path / "c"
    d.mkdir()
    (d / "ok.yaml").write_text(_contract_yaml("ok", [{"name": "age_min", "type": "min", "field": "age", "min": 18}],
                                              {"kids": {"age_min": {"min": 13}}}), encoding="utf-8")
    (d / "bad.yaml").write_text(_contract_yaml("bad", [{"name": "age_min", "type": "min", "field": "age", "min": 18}],
                                               {"kids": {"age_min": {"type": "min_lenght"}}}), encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        reg = ContractRegistry(d)
    assert reg.get("ok") is not None
    assert reg.get("bad") is None, "an override the model refuses must refuse the file, not every request"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "bad.yaml" in msg and "context 'kids'" in msg and "min_lenght" in msg
    assert reg.load_failures and reg.load_failures[0]["file"] == "bad.yaml"


def test_linter_reports_unknown_type_in_a_context_override():
    from opendqv.core.linter import lint_contract_yaml
    res = lint_contract_yaml(_contract_yaml("t", [{"name": "age_min", "type": "min", "field": "age", "min": 18}],
                                            {"kids": {"age_min": {"type": "min_lenght"}}}), "t")
    hits = [i for i in res.issues if i.code == "UNKNOWN_RULE_TYPE"]
    assert hits and "kids" in hits[0].message and "min_lenght" in hits[0].message


def test_reload_response_names_the_file_that_did_not_load(client, admin_headers):
    import opendqv.api.deps as _d
    bad = _d.registry.contracts_dir / "zz_typo_reload.yaml"
    bad.write_text(_contract_yaml("zz_typo_reload", [{"name": "r", "type": "not_emtpy", "field": "f"}]), encoding="utf-8")
    try:
        r = client.post("/api/v1/contracts/reload", headers=admin_headers)
        assert r.status_code == 200, r.text
        mine = [f for f in r.json()["failed"] if f["file"] == "zz_typo_reload.yaml"]
        assert mine and "not_emtpy" in mine[0]["error"], r.json()["failed"]
    finally:
        bad.unlink()
        _d.registry.reload()
    assert not [f for f in _d.registry.load_failures if f["file"] == "zz_typo_reload.yaml"]


def test_historical_snapshot_with_a_refused_rule_is_409_not_500(client, admin_headers):
    import asyncio
    import json as _json
    import opendqv.api.deps as _d
    import opendqv.mcp_server as mcp
    NAME = "MCP_zz_snapshot_probe"   # dedicated contract: never touch the shared `customer` history
    ct = _d.registry.get(NAME) or _d.registry.create_draft(
        name=NAME, description="d", owner="o", created_by="t",
        rules_data=[{"name": "r", "type": "not_empty", "field": "f", "error_message": "m"}])
    bad = Rule.model_construct(**{**ct.rules[0].model_dump(), "type": "not_emtpy", "name": "legacy_typo"})
    ct2 = ct.model_copy(update={"rules": list(ct.rules) + [bad], "version": "9.9"})
    _d.registry.history.record_version(ct2)   # a 2.7.0-era snapshot that loaded then
    snap = [s for s in _d.registry.get_history(NAME) if s["version"] == "9.9"][0]
    r = client.post("/api/v1/validate", headers=admin_headers,
                    json={"contract": NAME, "record": {"f": "x"}, "hash": snap["entry_hash"]})
    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "SNAPSHOT_RULE_REFUSED" and "legacy_typo" in r.json()["detail"]
    r = client.get(f"/api/v1/contracts/{NAME}?hash={snap['entry_hash']}", headers=admin_headers)
    assert r.status_code == 409, r.text
    # MCP: the same snapshot through the tool surface is an envelope, not INTERNAL_ERROR
    if mcp._registry is not _d.registry:
        mcp._registry.history.record_version(ct2)
    out = asyncio.run(mcp.call_tool("validate_record", {"contract": NAME, "hash": snap["entry_hash"],
                                                         "record": {"f": "x"}}))
    env = _json.loads(out[0].text)
    assert env["error"]["error_code"] == "SNAPSHOT_RULE_REFUSED", env


def test_field_keyed_onboarding_format_maps_bare_data_types_instead_of_refusing_the_file(tmp_path, caplog):
    """/code-review: in the field-keyed format `type:` is a DATA type; the closed
    set must not turn a formerly silent no-op into whole-contract loss."""
    d = tmp_path / "c"
    d.mkdir()
    (d / "signup.yaml").write_text(yaml.safe_dump({"rules": {
        "created_at": {"type": "date", "required": True},
        "email": {"type": "email"},
        "name": {"type": "string", "min_length": 2},
        "age": {"type": "number", "min": 0, "max": 130},
    }}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        reg = ContractRegistry(d)
    c = reg.get("signup")
    assert c is not None, "the file must load"
    kinds = sorted((r.field, r.type) for r in c.rules)
    assert kinds == [("age", "max"), ("age", "min"), ("created_at", "not_empty"), ("name", "min_length")]
    assert any("email" in r.getMessage() and "no rule emitted" in r.getMessage() for r in caplog.records)
    from opendqv.core.validator import validate_record
    assert validate_record({"created_at": "2026-01-01", "name": "A", "age": 200}, c.rules)["valid"] is False
    assert validate_record({"created_at": "2026-01-01", "name": "Al", "age": 20}, c.rules)["valid"] is True


def test_handler_table_drift_is_a_runtime_error_not_an_assert():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "opendqv" / "core" / "validator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    asserts = [n for n in tree.body if isinstance(n, ast.Assert)]
    assert not asserts, "module-level assert is stripped under python -O; use an explicit raise"
    assert "if frozenset(_RULE_HANDLERS) != RULE_TYPES" in src


def test_malformed_context_block_is_refused_at_load_with_the_context_named(tmp_path, caplog):
    d = tmp_path / "c"
    d.mkdir()
    (d / "bad.yaml").write_text(yaml.safe_dump({"contract": {"name": "bad", "version": "1.0",
        "rules": [{"name": "r1", "field": "status", "type": "not_empty"}],
        "contexts": {"eu": [{"name": "r2", "field": "status", "type": "not_empty"}]}}}), encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        reg = ContractRegistry(d)
    assert reg.get("bad") is None
    assert "context 'eu'" in " ".join(r.getMessage() for r in caplog.records) and "mapping" in " ".join(r.getMessage() for r in caplog.records)
