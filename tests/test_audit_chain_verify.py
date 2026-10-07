"""`opendqv audit-verify` must PASS on every chain the engine itself writes.

Two holes (3.0.1):
1. CRT180 (2.5.0) put strict_schema + allowed_fields into the hash domain but
   the verifier never passed them, so every strict contract read as a hash
   MISMATCH — a fresh database of the bundled library failed (6 contracts).
2. The v2.3.17 F-C invariant demoted a superseded ACTIVE row IN PLACE
   (status active -> archived); status is hashed, so the chain broke after any
   same-version re-record. History is now append-only and F-C is applied on
   read; rows an older engine already demoted are verified as recorded
   'active' and reported, while any other edit still fails.
"""
import argparse
import shutil
import sqlite3
from pathlib import Path

import pytest

import opendqv.cli as cli
from opendqv.core.contracts import ContractHistory, ContractRegistry, _supersede_statuses

ROOT = Path(__file__).resolve().parent.parent

DOC = """\
name: widget
version: "1.0"
status: active
owner_email: team@example.com
description: {desc}
strict_schema: true
allowed_fields: [note]
rules:
  - name: sku_required
    type: not_empty
    field: sku
    error_message: sku is required
"""


def _verify(db: Path, capsys) -> tuple[int, str]:
    try:
        cli.cmd_audit_verify(argparse.Namespace(db=str(db)))
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, capsys.readouterr().out


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "h.db"
    import opendqv.config as config
    monkeypatch.setattr(config, "DB_PATH", str(path))
    return path


def test_fresh_bundled_library_chain_passes(tmp_path, db, capsys):
    shutil.copytree(ROOT / "opendqv" / "contracts", tmp_path / "c")
    ContractRegistry(tmp_path / "c")
    code, out = _verify(db, capsys)
    assert "MISMATCH" not in out and "Chain integrity: PASS" in out and code == 0


def _two_actives(tmp_path) -> None:
    c = tmp_path / "c"
    c.mkdir()
    (c / "widget.yaml").write_text(DOC.format(desc="one"), encoding="utf-8")
    ContractRegistry(c)
    (c / "widget.yaml").write_text(DOC.format(desc="two"), encoding="utf-8")
    ContractRegistry(c)


def test_same_version_rerecord_keeps_rows_immutable_and_chain_valid(tmp_path, db, capsys):
    _two_actives(tmp_path)
    stored = [r[0] for r in sqlite3.connect(str(db)).execute("SELECT status FROM contract_history ORDER BY id")]
    assert stored == ["active", "active"]            # never rewritten
    code, out = _verify(db, capsys)
    assert "MISMATCH" not in out and "demoted in place" not in out and code == 0


def test_f_c_invariant_applied_on_read(tmp_path, db):
    _two_actives(tmp_path)
    hist = ContractHistory(str(db)).get_history("widget")
    assert [(h["status"], h["recorded_status"]) for h in hist] == [("archived", "active"), ("active", "active")]


def test_list_versions_shows_one_active(tmp_path, db, client, auth_headers, monkeypatch):
    import opendqv.api.deps as d
    _two_actives(tmp_path)
    monkeypatch.setattr(d.registry, "history", ContractHistory(str(db)))
    versions = client.get("/api/v1/contracts/widget/versions", headers=auth_headers).json()["versions"]
    assert [v["status"] for v in versions] == ["archived", "active"]
    hist = client.get("/api/v1/contracts/widget/history", headers=auth_headers).json()["history"]
    assert [h["recorded_status"] for h in hist] == ["active", "active"]


def test_legacy_in_place_demotion_is_reported_not_failed(tmp_path, db, capsys):
    _two_actives(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE contract_history SET status = 'archived' WHERE id = (SELECT MIN(id) FROM contract_history)")
    conn.commit()
    code, out = _verify(db, capsys)
    assert code == 0 and "Chain integrity: PASS" in out
    assert "demoted in place by an engine before 3.0.1" in out and "1 entry carried" in out


@pytest.mark.parametrize("sql", [
    "UPDATE contract_history SET description = 'tampered' WHERE id = (SELECT MIN(id) FROM contract_history)",
    "UPDATE contract_history SET status = 'draft' WHERE id = (SELECT MIN(id) FROM contract_history)",
    "UPDATE contract_history SET strict_schema = 0 WHERE id = (SELECT MIN(id) FROM contract_history)",
])
def test_any_other_edit_still_fails(tmp_path, db, capsys, sql):
    _two_actives(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(sql)
    conn.commit()
    code, out = _verify(db, capsys)
    assert code == 1 and "MISMATCH" in out and "Chain integrity: FAIL" in out


def test_supersede_only_counts_later_active_rows():
    rows = [{"version": "1.0", "status": s} for s in ("active", "draft", "active", "review")]
    out = _supersede_statuses(rows)
    assert [r["status"] for r in out] == ["archived", "draft", "active", "review"]
    assert [r["recorded_status"] for r in out] == ["active", "draft", "active", "review"]
