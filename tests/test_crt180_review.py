"""Regression tests from the maintainer review of the CRT180 conformance PR.

Each test names the review item it pins (B = blocker, S = should).
"""
from __future__ import annotations

import ast
from pathlib import Path

from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record

ROOT = Path(__file__).resolve().parents[1]


# ── B1: negated pattern must be reachable under start-anchored matching ──────

def test_b1_salesforce_personal_email_rule_fires_under_anchored_match():
    reg = ContractRegistry(ROOT / "opendqv" / "contracts")
    contract = reg.get("salesforce_lead")
    rule = next(r for r in contract.rules if r.name == "email_not_personal")
    # D7 (search semantics) made the `.*` prefix redundant; the golden library
    # (2.7.0) dropped it. The property that matters: negated, $-anchored.
    assert rule.negate is True and rule.pattern.endswith(r"\.com$")

    def hits(email: str) -> list[dict]:
        res = validate_record({rule.field: email}, contract.rules)
        return [x for x in res["errors"] + res["warnings"] if x["rule"] == "email_not_personal"]

    gmail = hits("someone@gmail.com")
    assert gmail and gmail[0]["severity"] == "warning"
    assert hits("someone@corp.example") == []
    assert hits("someone@gmail.com.evil") == []  # $-anchored: not a personal domain


# ── B2: no validate call site may bypass strict_schema ───────────────────────

_CORE_FUNCS = {"validate_record", "validate_batch"}


def test_b2_every_engine_call_site_passes_strict_kwargs():
    """Every call to the engine's validate_record/validate_batch outside the
    validator itself must pass strict_schema explicitly or splat
    strict_schema_kwargs(...). A surface that forgets is a strict-mode bypass."""
    offenders = []
    for path in sorted((ROOT / "opendqv").rglob("*.py")):
        if path.name == "validator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name not in _CORE_FUNCS:
                continue
            # Attribute calls on an object (self.validate_record(...), client.validate_batch(...))
            # are wrappers, not the engine; the engine is imported by bare name.
            if isinstance(fn, ast.Attribute) and not (
                isinstance(fn.value, ast.Name) and fn.value.id in {"validator", "core_validator"}
            ):
                continue
            if any(k.arg == "strict_schema" or k.arg is None for k in node.keywords):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], f"validate call sites without strict_schema: {offenders}"


# ── B5: single and batch paths must emit the same not_empty_string message ───

def test_b5_not_empty_string_message_identical_on_both_paths():
    rules = [Rule(name="acct_str", type="not_empty_string", field="account_number",
                  error_message="account_number must be a non-empty string")]
    single = validate_record({"account_number": 0}, rules)["errors"][0]
    batch = validate_batch([{"account_number": 0}], rules)["results"][0]["errors"][0]
    assert single["error_code"] == batch["error_code"]
    assert single["message"] == batch["message"]
    assert "JSON string" in single["message"] and "number" in single["message"]


# ── S2: the additional-properties message is capped; the list is not ─────────

def test_s2_additional_properties_message_capped_at_ten_names():
    rules = [Rule(name="a_req", type="not_empty", field="a", error_message="a")]
    record = {"a": "x"} | {f"extra_{i:02d}": i for i in range(15)}
    res = validate_record(record, rules, strict_schema=True, declared_fields={"a"})
    entry = next(e for e in res["errors"] if e["error_code"] == "OPENDQV_ADDITIONAL_PROPERTIES")
    assert "and 5 more" in entry["message"]
    assert entry["message"].count("extra_") == 10
    assert sorted(entry["unknown_fields"]) == sorted(record)[1:]  # all 15, in the structured field


# ── B3: the draft fallback reads the snapshot taken at ACTIVE→DRAFT, not live flags ──

def test_b3_active_to_draft_snapshots_strict_flags(tmp_path):
    from opendqv.core.contracts import ContractStatus

    (tmp_path / "c9.yaml").write_text(
        "contract:\n  name: c9\n  version: '1.0'\n  status: active\n  strict_schema: true\n"
        "  allowed_fields: [trace_id]\n  rules:\n"
        "    - name: a_req\n      type: not_empty\n      field: a\n      error_message: a required\n",
        encoding="utf-8",
    )
    reg = ContractRegistry(tmp_path)
    reg.set_status("c9", "1.0", ContractStatus.DRAFT)
    c = reg.get("c9")
    assert c.status == ContractStatus.DRAFT
    assert c.last_active_strict_schema is True
    assert c.last_active_fields == ["trace_id"]
    # Editing the draft must not rewrite what the last ACTIVE version enforced.
    c.strict_schema = False
    c.allowed_fields = []
    assert c.last_active_strict_schema is True and c.last_active_fields == ["trace_id"]
    routes = (ROOT / "opendqv" / "api" / "routes_validation.py").read_text(encoding="utf-8")
    assert "last_active_strict_schema" in routes and "last_active_fields" in routes


# ── B4: the Postgres history backend persists the flags and hashes like SQLite ──

def test_b4_postgres_backend_persists_strict_flags_with_sqlite_identical_hash():
    import inspect
    import json
    from unittest.mock import MagicMock, patch

    from opendqv.core import storage
    from opendqv.core.contracts import ContractHistory, DataContract
    from tests.test_storage_extended import _make_pg_backend

    src = inspect.getsource(storage)
    assert "ADD COLUMN IF NOT EXISTS strict_schema INTEGER NOT NULL DEFAULT 0" in src
    assert "ADD COLUMN IF NOT EXISTS allowed_fields TEXT" in src

    rules = [Rule(name="a_req", type="not_empty", field="a", error_message="a required")]
    contract = DataContract(name="c", version="1.0", rules=rules, strict_schema=True, allowed_fields=["b", "a"])

    sqlite = ContractHistory(db_path=":memory:")
    sqlite.record_version(contract)
    sq = sqlite.get_history("c")[-1]
    assert sq["strict_schema"] is True and sorted(sq["allowed_fields"]) == ["a", "b"]

    backend, mock_pg, mock_conn, mock_cursor = _make_pg_backend()
    with patch.dict("sys.modules", {"psycopg2": mock_pg}):
        backend._connect = MagicMock(return_value=mock_conn)
        backend.record_version(contract)
    inserts = [c for c in mock_cursor.execute.call_args_list if "INSERT INTO contract_history" in str(c.args[0])]
    assert len(inserts) == 1
    sql, params = inserts[0].args
    assert "strict_schema, allowed_fields" in sql
    assert 1 in params and json.dumps(["a", "b"]) in params
    assert sq["content_hash"] in params, "content hash must not depend on the history backend"
