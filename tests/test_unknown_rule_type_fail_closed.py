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
