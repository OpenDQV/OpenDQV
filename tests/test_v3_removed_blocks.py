"""3.0.0: the ``contract:`` wrapper and ``contexts:`` are refused on every way in.

One shared parse-point check (``check_removed_blocks`` → ``check_contract_keys``)
runs before the unknown-key check, with the managed engine's wording, on file
load (into ``load_failures``), ``/import/*`` (422), ``opendqv fork`` and the
linter. Every writer emits the flat document and every write reloads. Stored
history rows that carry a contexts block still verify and still load (the
block is dropped with a warning).
"""
import argparse
import json
import logging
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from opendqv.core.contracts import (
    CONTEXTS_UNSUPPORTED,
    CONTRACT_KEYS,
    ENVELOPE_UNSUPPORTED,
    ContractHistory,
    ContractRegistry,
    DataContract,
    _compute_entry_hash,
    _contract_from_snapshot,
    check_contract_keys,
)
from opendqv.core.linter import lint_contract_yaml

ROOT = Path(__file__).resolve().parent.parent

FLAT = """\
name: widget
version: "1.0"
description: a widget
owner: team
owner_email: team@example.com
status: draft
rules:
  - name: sku_required
    type: not_empty
    field: sku
    severity: error
    error_message: sku is required
"""


def _wrapped(text: str) -> str:
    return "contract:\n" + "".join(f"  {line}\n" for line in text.splitlines())


def _registry(tmp_path: Path, files: dict[str, str]) -> ContractRegistry:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return ContractRegistry(tmp_path)


def _failure(reg: ContractRegistry, filename: str) -> str:
    return next(f["error"] for f in reg.load_failures if f["file"] == filename)


# ── The exact strings (Cloud parity) ─────────────────────────────────────────

def test_refusal_messages_are_exact():
    assert CONTEXTS_UNSUPPORTED == (
        "contract_contexts_unsupported: This contract declares a 'contexts' block, which OpenDQV Core "
        "does not support. Remove the 'contexts' block, or publish one contract per context "
        "(e.g. 'salesforce_lead_web_form') so each has its own version history and audit lineage."
    )
    assert ENVELOPE_UNSUPPORTED == (
        "contract_envelope_unsupported: contract YAML uses the legacy top-level 'contract:' wrapper "
        "— remove the wrapper so name/version/rules are top-level fields."
    )
    assert "contexts" not in CONTRACT_KEYS


# ── File load ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("block", ["contexts: {}", "contexts: []", "contexts:\n  web_form:\n    sku: {severity: warning}"])
def test_load_refuses_any_contexts_value(tmp_path, block):
    reg = _registry(tmp_path, {"widget.yaml": FLAT + block + "\n"})
    assert reg.get("widget") is None
    assert _failure(reg, "widget.yaml") == CONTEXTS_UNSUPPORTED


def test_load_tolerates_bare_contexts(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": FLAT + "contexts:\n"})
    assert reg.load_failures == []
    assert reg.get("widget") is not None
    assert not hasattr(reg.get("widget"), "contexts")


def test_load_refuses_wrapper(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": _wrapped(FLAT)})
    assert reg.get("widget") is None
    assert _failure(reg, "widget.yaml") == ENVELOPE_UNSUPPORTED


def test_wrapper_is_checked_before_contexts(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": _wrapped(FLAT + "contexts: {a: {}}")})
    assert _failure(reg, "widget.yaml") == ENVELOPE_UNSUPPORTED


def test_contexts_is_checked_before_unknown_keys(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": FLAT + "contexts: {}\nbogus_key: 1\n"})
    assert _failure(reg, "widget.yaml") == CONTEXTS_UNSUPPORTED


def test_onboarding_format_refuses_contexts_too(tmp_path):
    doc = "metadata:\n  version: '1.0'\nrules:\n  sku:\n    type: string\n    required: true\ncontexts:\n  a: {}\n"
    reg = _registry(tmp_path, {"widget.yaml": doc})
    assert _failure(reg, "widget.yaml") == CONTEXTS_UNSUPPORTED


def test_one_bad_file_does_not_stop_the_others(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": FLAT, "old.yaml": _wrapped(FLAT.replace("widget", "old"))})
    assert reg.get("widget") is not None
    assert [f["file"] for f in reg.load_failures] == ["old.yaml"]


def test_flat_document_reads_every_contract_field(tmp_path):
    doc = FLAT + (
        "strict_schema: true\nallowed_fields: [note]\nasset_id: urn:x:widget\n"
        "owner_team: t\nsensitive_fields: [sku]\napproved_by: someone\n"
    )
    c = _registry(tmp_path, {"anything.yaml": doc}).get("widget")
    assert (c.name, c.version, c.status.value, c.owner_email) == ("widget", "1.0", "draft", "team@example.com")
    assert c.strict_schema is True and c.allowed_fields == ["note"]
    assert (c.asset_id, c.owner_team, c.sensitive_fields, c.approved_by) == ("urn:x:widget", "t", ["sku"], "someone")


def test_bundled_library_is_flat_and_loads():
    for path in sorted((ROOT / "opendqv" / "contracts").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "contract" not in doc and "contexts" not in doc, path.name
        check_contract_keys(doc)


# ── Writers: every one emits the flat document and reloads ──────────────────

def _assert_flat_and_reloads(tmp_path: Path, filename: str, name: str):
    doc = yaml.safe_load((tmp_path / filename).read_text(encoding="utf-8"))
    assert "contract" not in doc and "contexts" not in doc
    check_contract_keys(doc)
    reg = ContractRegistry(tmp_path)
    assert reg.load_failures == []
    return reg.get(name)


def test_create_draft_writes_flat(tmp_path):
    reg = ContractRegistry(tmp_path)
    reg.create_draft("MCP_widget", "d", "o", "agent", [
        {"name": "sku_required", "type": "not_empty", "field": "sku", "error_message": "x"},
    ])
    assert _assert_flat_and_reloads(tmp_path, "MCP_widget.yaml", "MCP_widget") is not None


def test_rule_mutation_and_lifecycle_write_flat(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": FLAT})
    reg.add_rule("widget", {"name": "qty_min", "type": "min", "field": "qty", "min": 0, "error_message": "x"})
    reg.submit_for_review("widget", reg.get("widget").version, "maker")
    c = _assert_flat_and_reloads(tmp_path, "widget.yaml", "widget")
    assert {r.name for r in c.rules} == {"sku_required", "qty_min"}
    assert c.status.value == "review" and c.proposed_by == "maker"


def test_create_version_writes_flat(tmp_path):
    reg = _registry(tmp_path, {"widget.yaml": FLAT})
    reg.create_version("widget", "1.0", "2.0")
    doc = yaml.safe_load((tmp_path / "widget_v2.0.yaml").read_text(encoding="utf-8"))
    assert "contract" not in doc and doc["version"] == "2.0" and doc["status"] == "draft"
    assert ContractRegistry(tmp_path).get("widget", "2.0") is not None


def test_importer_and_generator_yaml_is_flat():
    from opendqv.core.importers.csv_rules import csv_rules_to_yaml
    from opendqv.core.importers.csvw import csvw_to_yaml
    from opendqv.core.importers.ndc import ndc_to_yaml
    from opendqv.core.onboarding import generate_contract_yaml
    docs = [
        yaml.safe_load(csv_rules_to_yaml("field,rule_type,severity,error_message\nsku,not_empty,error,x\n", "w")),
        yaml.safe_load(csvw_to_yaml({"tableSchema": {"columns": [{"name": "sku", "required": True}]}}, "w")),
        yaml.safe_load(ndc_to_yaml(None, "w")),
        yaml.safe_load(generate_contract_yaml("w", ["email", "order_amount"])),
    ]
    for doc in docs:
        assert "contract" not in doc and doc["name"] == "w"
        check_contract_keys(doc)


# ── /import/* : the shared check is a 422 before anything is written ────────

def test_import_meta_refuses_contexts_and_wrapper():
    from opendqv.api.routes_imports import _apply_import_meta
    for doc, msg in (
        ({"name": "w", "rules": [], "contexts": {}}, CONTEXTS_UNSUPPORTED),
        ({"contract": {"name": "w", "rules": []}}, ENVELOPE_UNSUPPORTED),
    ):
        with pytest.raises(HTTPException) as exc:
            _apply_import_meta(doc, "me")
        assert exc.value.status_code == 422 and exc.value.detail == msg


def test_import_meta_stamps_flat_document():
    from opendqv.api.routes_imports import _apply_import_meta
    doc = {"name": "w", "status": "active", "rules": []}
    _apply_import_meta(doc, "me")
    assert (doc["status"], doc["source"], doc["proposed_by"]) == ("draft", "import", "me")


def test_import_save_lands_flat_and_loads(client, editor_headers):
    r = client.post(
        "/api/v1/import/csv?save=true&contract_name=v3_flat_import",
        content="field,rule_type,severity,error_message\nsku,not_empty,error,sku is required\n",
        headers={**editor_headers, "Content-Type": "text/plain"},
    )
    assert r.status_code == 200, r.text
    path = Path(r.json()["saved_to"])
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "contract" not in doc and doc["name"] == "v3_flat_import" and doc["status"] == "draft"
        assert client.get("/api/v1/contracts/v3_flat_import?version=latest", headers=editor_headers).status_code == 200
    finally:
        path.unlink(missing_ok=True)


# ── opendqv fork ─────────────────────────────────────────────────────────────

def _fork(tmp_path, monkeypatch, src, dst):
    import opendqv.cli as cli
    monkeypatch.setattr(cli, "CONTRACTS_DIR", tmp_path)
    cli.cmd_fork(argparse.Namespace(src=src, dst=dst, force=False))


@pytest.mark.parametrize("text,msg", [
    (FLAT + "contexts: {a: {}}\n", CONTEXTS_UNSUPPORTED),
    (_wrapped(FLAT), ENVELOPE_UNSUPPORTED),
])
def test_fork_refuses_removed_blocks(tmp_path, monkeypatch, capsys, text, msg):
    (tmp_path / "widget.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit):
        _fork(tmp_path, monkeypatch, "widget", "widget2")
    assert msg in capsys.readouterr().err
    assert not (tmp_path / "widget2.yaml").exists()


def test_fork_rewrites_top_level_keys_only(tmp_path, monkeypatch):
    src = FLAT.replace("status: draft\n", "status: active\n") + "  - name: status_rule\n    type: not_empty\n    field: status\n    error_message: x\n"
    (tmp_path / "widget.yaml").write_text(src, encoding="utf-8")
    _fork(tmp_path, monkeypatch, "widget", "gadget")
    doc = yaml.safe_load((tmp_path / "gadget.yaml").read_text(encoding="utf-8"))
    assert (doc["name"], doc["version"], doc["status"], doc["asset_id"]) == ("gadget", "1.0", "draft", "urn:opendqv:gadget")
    assert [r["name"] for r in doc["rules"]] == ["sku_required", "status_rule"]
    assert ContractRegistry(tmp_path).get("gadget").status.value == "draft"


# ── Linter speaks the engine's refusal ───────────────────────────────────────

@pytest.mark.parametrize("text,code,msg", [
    (_wrapped(FLAT), "CONTRACT_ENVELOPE_UNSUPPORTED", ENVELOPE_UNSUPPORTED),
    (FLAT + "contexts: {}\n", "CONTRACT_CONTEXTS_UNSUPPORTED", CONTEXTS_UNSUPPORTED),
])
def test_linter_reports_engine_refusal(text, code, msg):
    issues = lint_contract_yaml(text, "widget").issues
    assert [(i.severity, i.code, i.message) for i in issues] == [("error", code, msg)]


def test_linter_clean_on_flat_and_bare_contexts():
    for text in (FLAT, FLAT + "contexts:\n"):
        assert not [i for i in lint_contract_yaml(text, "widget").issues if i.severity == "error"]


# ── History: stored rows keep verifying and still load ──────────────────────

def test_record_version_writes_empty_contexts_column(tmp_path):
    hist = ContractHistory(str(tmp_path / "h.db"))
    hist.record_version(DataContract(name="w", rules=[]))
    row = sqlite3.connect(str(tmp_path / "h.db")).execute("SELECT contexts FROM contract_history").fetchone()
    assert row[0] == "{}"


def _legacy_row(db: Path) -> None:
    """Insert a 2.x history row whose contexts column carries a block, with an
    entry_hash computed over that stored column (as 2.x did)."""
    ContractHistory(str(db))  # schema
    block = {"web_form": {"sku_required": {"severity": "warning"}}}
    rules = [{"name": "sku_required", "type": "not_empty", "field": "sku", "severity": "error", "error_message": "x"}]
    ts, node = "2026-01-01T00:00:00+00:00", "n1"
    eh = _compute_entry_hash("0" * 64, "w", "1.0", "active", "", None, None, None, "", [], rules, block, node, ts)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO contract_history (contract_name, version, status, description, owner, downstream_consumers, "
        "rules, contexts, opendqv_node_id, updated_at, prev_hash, entry_hash, content_hash, domain_version) "
        "VALUES ('w','1.0','active','','','[]',?,?,?,?,?,?,'',2)",
        (json.dumps(rules), json.dumps(block), node, ts, "0" * 64, eh),
    )
    conn.commit()
    conn.close()


def test_stored_contexts_row_still_verifies(tmp_path, capsys):
    import opendqv.cli as cli
    db = tmp_path / "h.db"
    _legacy_row(db)
    cli.cmd_audit_verify(argparse.Namespace(db=str(db)))   # exits non-zero only on FAIL
    assert "Chain integrity: PASS" in capsys.readouterr().out


def test_stored_contexts_row_loads_with_block_dropped(tmp_path, caplog):
    db = tmp_path / "h.db"
    _legacy_row(db)
    snap = ContractHistory(str(db)).get_as_of("w", "2027-01-01T00:00:00+00:00")
    assert snap["contexts"]      # the stored column is untouched
    with caplog.at_level(logging.WARNING, logger="opendqv.core.contracts"):
        c = _contract_from_snapshot("w", snap)
    assert [r.name for r in c.rules] == ["sku_required"] and not hasattr(c, "contexts")
    assert any("contexts block" in m for m in caplog.messages)


# ── REST / MCP surface ───────────────────────────────────────────────────────

def test_context_is_a_tag_only(client, auth_headers):
    body = {"contract": "customer", "record": {"email": "a@b.co", "name": "A", "age": 30}, "dry_run": True}
    plain = client.post("/api/v1/validate", json=body, headers=auth_headers).json()
    tagged = client.post("/api/v1/validate", json={**body, "context": "kids_app"}, headers=auth_headers).json()
    assert "context_warning" not in tagged
    assert tagged["effective_rule_hash"] == plain["effective_rule_hash"]
    assert tagged["valid"] == plain["valid"]


def test_contract_detail_has_no_contexts_field(client, auth_headers):
    d = client.get("/api/v1/contracts/customer", headers=auth_headers).json()
    assert "contexts" not in d


def test_mcp_both_entry_points_drop_override_context():
    src_inproc = (ROOT / "opendqv" / "mcp_server.py").read_text(encoding="utf-8")
    src_proxy = (ROOT / "opendqv_mcp_proxy.py").read_text(encoding="utf-8")
    for src in (src_inproc, src_proxy):
        assert "contexts block" not in src
        assert "context_warning" not in src
        assert "get_rules_with_context" not in src


# ── Red-team follow-ups ──────────────────────────────────────────────────────

def test_duplicate_name_version_is_refused_not_shadowed(tmp_path):
    reg = _registry(tmp_path, {"a_first.yaml": FLAT, "b_second.yaml": FLAT.replace("sku_required", "other_rule")})
    assert [r.name for r in reg.get("widget").rules] == ["sku_required"]
    err = _failure(reg, "b_second.yaml")
    assert "already loaded from a_first.yaml" in err


def test_fork_accepts_onboarding_format_source(tmp_path, monkeypatch):
    src = "metadata:\n  version: '2'\nrules:\n  email:\n    type: string\n    required: true\n"
    (tmp_path / "onb.yaml").write_text(src, encoding="utf-8")
    _fork(tmp_path, monkeypatch, "onb", "onb2")
    doc = yaml.safe_load((tmp_path / "onb2.yaml").read_text(encoding="utf-8"))
    assert doc["name"] == "onb2" and doc["status"] == "draft"


def test_fork_refuses_when_textual_rewrite_misses(tmp_path, monkeypatch, capsys):
    src = 'description: "first\nname: inside the scalar"\n' + FLAT
    (tmp_path / "widget.yaml").write_text(src, encoding="utf-8")
    with pytest.raises(SystemExit):
        _fork(tmp_path, monkeypatch, "widget", "gadget")
    assert "did not produce a valid DRAFT" in capsys.readouterr().err
    assert not (tmp_path / "gadget.yaml").exists()
