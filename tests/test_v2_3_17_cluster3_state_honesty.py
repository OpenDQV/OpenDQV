"""
v2.3.17 Cluster 3 — state-claim-without-state family.

Sub-patches in this cluster:

- F-C: ``list_versions`` previously returned multiple ``status: active``
  rows for the same (contract_name, version) because the history table
  is append-only and prior ACTIVE rows were never demoted. Fix: at row
  insert time, when writing a new ACTIVE row, demote prior ACTIVE rows
  for the same (name, version) to ARCHIVED. History remains append-only
  for chain integrity; the *status field* on historical rows is a state
  attribute and is correctly updated when truth changes.

- F-G: ``approved_by`` / ``proposed_by`` look perpetually null on
  bundled exemplar contracts because exemplars never trigger the
  approve workflow. Sonnet's read of ``routes_contracts.py:604-614``
  confirmed the field IS populated on the live approve path. Q6's
  resolution: keep the fields, add a recurrence test asserting
  ``approved_by`` is non-null after ``POST /contracts/{name}/approve``.

- F-J: ``effective_rule_hash`` — the existing 3-hash triplet (entry,
  content, contract) is invariant to context, so two validate calls
  with different contexts produce the same triplet despite running
  different rule sets. Fix: add ``effective_rule_hash`` field on
  ``ValidateResponse`` and ``BatchValidateResponse`` AND on the MCP
  in-process validate_record/validate_batch dict outputs (dual-path
  discipline). Hashes the resolved Rule set after override
  application — see ``_compute_effective_rule_hash`` rationale.

- N-6: ``get_contract(context=X)`` regression guard — removed in 3.0.0
  together with ``contexts:`` (the ``?context=`` query parameter is gone).

3.0.0: ``contexts:`` is gone, so ``context`` on validate is a pure tag and
never changes which rules run. The F-J tests now pin that: the same record
validated with and without a context tag carries the SAME
``effective_rule_hash`` on both the REST and MCP paths.
"""

from opendqv.core.contracts import (
    ContractHistory, ContractRegistry, _compute_effective_rule_hash,
)


# ── F-C: history table — at-most-one-active invariant ─────────────────

class TestActiveVersionUniquenessInvariant:
    """``list_versions`` must return at most one ``status: active`` row per
    (contract_name, version_string) — even after multiple record_version
    calls that would otherwise have left several active rows in place."""

    def test_at_most_one_active_per_version_after_repeated_record(self, tmp_path):
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "tc.yaml").write_text("""
name: tc
status: active
version: "1.0"
rules:
  - name: x_required
    field: x
    type: not_empty
""", encoding="utf-8")

        # Use a dedicated history db for this test
        db_path = str(tmp_path / "h.db")
        reg = ContractRegistry(contracts_dir)
        # Replace the registry's history backend with one pointing at our temp db
        reg.history = ContractHistory(db_path)
        contract = reg.get("tc")

        # Force three back-to-back history writes for the same (name, version)
        # by mutating description and re-recording. Each call must demote the
        # previous active row before inserting the new one.
        for i in range(3):
            contract.description = f"version-iteration-{i}"
            reg.history.record_version(contract)

        # Query history directly
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT version, status FROM contract_history "
                "WHERE contract_name = ?", ("tc",),
            ).fetchall()
        finally:
            conn.close()

        active_rows = [r for r in rows if r[1] == "active" and r[0] == "1.0"]
        assert len(active_rows) <= 1, \
            f"contract_history must have ≤1 active row per (name, version), got {len(active_rows)}: {rows}"

        # The most recent row IS the active one; older ones are archived
        archived_rows = [r for r in rows if r[1] == "archived" and r[0] == "1.0"]
        assert len(archived_rows) >= 2, \
            f"prior active rows must be demoted to archived, got {len(archived_rows)} archived in {rows}"


# ── F-G: approved_by populates on live approve path ───────────────────

class TestApprovedBySchemaHonesty:
    """Q6 resolution: keep approved_by/proposed_by; add recurrence test
    asserting the field IS populated when the approve flow runs.

    Bundled exemplars look perpetually null on these fields because they
    never trigger the approve flow (loaded as ACTIVE from YAML on boot).
    A live create-draft → submit-for-review → approve sequence must
    populate approved_by — that's the schema's promise."""

    def test_approve_populates_approved_by(self, tmp_path):
        """Verifies approved_by IS populated on the live approve path —
        confirms Q6's resolution. Uses the registry directly (the HTTP layer
        is just a thin wrapper that calls registry.approve_contract)."""
        from opendqv.core.rule_parser import ContractStatus

        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        db_path = str(tmp_path / "h.db")

        reg = ContractRegistry(contracts_dir)
        reg.history = ContractHistory(db_path)

        # Create a draft via registry (mirrors what the HTTP create-draft route does)
        contract = reg.create_draft(
            name="MCP_approval_flow_test",
            description="v2.3.17 F-G test — approved_by population",
            owner="test-team",
            created_by="proposer-bob",
            rules_data=[{"name": "x_required", "field": "x", "type": "not_empty"}],
        )
        assert contract.status == ContractStatus.DRAFT

        # Submit for review
        contract = reg.submit_for_review(
            "MCP_approval_flow_test", contract.version, proposed_by="proposer-bob",
        )
        assert contract.status == ContractStatus.REVIEW

        # Approve
        contract = reg.approve_contract(
            "MCP_approval_flow_test", contract.version, approved_by="approver-alice",
        )
        assert contract.status == ContractStatus.ACTIVE
        # approved_by populated on the in-memory object
        assert contract.approved_by == "approver-alice"

        # And persisted to the history table
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT status, approved_by FROM contract_history "
                "WHERE contract_name = ? AND status = ?",
                ("MCP_approval_flow_test", "active"),
            ).fetchall()
        finally:
            conn.close()
        assert rows, "approved-active row should exist in history"
        assert rows[-1][1] == "approver-alice", \
            f"approved_by must be persisted to history, got: {rows}"


# ── F-J: effective_rule_hash on every validate response ────────────────

class TestEffectiveRuleHashOnRest:
    """REST validate response carries effective_rule_hash. 3.0.0: a context
    tag never changes the rule set, so the hash is the same with or without
    one."""

    def test_context_tag_does_not_change_effective_rule_hash(self, client, auth_headers):
        record = {
            "play_id": "p1",
            "advertiser_id": "a1",
            "venue_id": "v1",
            "media_content_id": "m1",
            "transaction_type": "CHARGE",
            "revenue_gbp": 100.0,
            "dwell_seconds": 30,
            "impression_start": "2026-04-27T10:00:00Z",
            "impression_end": "2026-04-27T10:00:30Z",
            "currency": "GBP",
        }
        body_default = {"contract": "proof_of_play", "record": record}
        body_billing = {"contract": "proof_of_play", "record": record, "context": "billing"}

        r_d = client.post("/api/v1/validate?allow_draft=true", json=body_default, headers=auth_headers)
        r_b = client.post("/api/v1/validate?allow_draft=true", json=body_billing, headers=auth_headers)

        assert r_d.status_code == 200, r_d.text
        assert r_b.status_code == 200, r_b.text

        h_d = r_d.json()["effective_rule_hash"]
        h_b = r_b.json()["effective_rule_hash"]

        assert h_d and h_b, "effective_rule_hash must populate on every validate response"
        assert h_d == h_b, \
            f"3.0.0: context is a pure tag — it must not change the rule set; got h_d={h_d}, h_b={h_b}"
        assert r_d.json()["entry_hash"] == r_b.json()["entry_hash"]


class TestEffectiveRuleHashOnMcp:
    """MCP in-process validate_record dict output must carry the same
    effective_rule_hash field as the REST surface — dual-path discipline."""

    def test_mcp_in_process_returns_effective_rule_hash(self):
        import asyncio
        import json
        from opendqv.mcp_server import _tool_validate_record

        record = {
            "play_id": "p1",
            "advertiser_id": "a1",
            "venue_id": "v1",
            "media_content_id": "m1",
            "transaction_type": "CHARGE",
            "revenue_gbp": 100.0,
            "dwell_seconds": 30,
            "impression_start": "2026-04-27T10:00:00Z",
            "impression_end": "2026-04-27T10:00:30Z",
            "currency": "GBP",
        }
        result_default = asyncio.run(_tool_validate_record({
            "contract": "proof_of_play", "record": record,
        }))
        result_billing = asyncio.run(_tool_validate_record({
            "contract": "proof_of_play", "record": record, "context": "billing",
        }))

        d = json.loads(result_default[0].text)
        b = json.loads(result_billing[0].text)

        assert d.get("effective_rule_hash"), \
            "MCP in-process validate_record dict must carry effective_rule_hash"
        assert b.get("effective_rule_hash"), \
            "MCP in-process validate_record dict (with context) must carry effective_rule_hash"
        assert d["effective_rule_hash"] == b["effective_rule_hash"], \
            "3.0.0: context is a pure tag on the MCP path too — same rule set, same hash"


# ── compute_effective_rule_hash unit tests ─────────────────────────────

class TestComputeEffectiveRuleHashUnit:
    def test_empty_rules_produces_stable_hash(self):
        h1 = _compute_effective_rule_hash([])
        h2 = _compute_effective_rule_hash([])
        assert h1 == h2 and len(h1) == 64

    def test_same_rules_produce_same_hash(self, tmp_path):
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "tc.yaml").write_text("""
name: tc
status: active
version: "1.0"
rules:
  - name: x_required
    field: x
    type: not_empty
""", encoding="utf-8")
        reg = ContractRegistry(contracts_dir)
        rules = reg.get("tc").rules
        assert _compute_effective_rule_hash(rules) == _compute_effective_rule_hash(rules)

    def test_different_severity_produces_different_hash(self):
        """A rule set that differs only in severity must change the hash —
        rule-names-only would miss this, which is exactly the bug F-J fixes."""
        from opendqv.core.rule_parser import Rule
        warn = [Rule(name="x_required", field="x", type="not_empty", severity="warning")]
        err = [Rule(name="x_required", field="x", type="not_empty", severity="error")]
        assert _compute_effective_rule_hash(warn) != _compute_effective_rule_hash(err)
