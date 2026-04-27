"""
v2.3.22 Cluster C — persist effective_rule_hash + entry_hash + content_hash
on the audit log row.

Reviewer F-J framing (`v2_3_17_plan_draft_2026_04_27.md`):
> Context-override hashes do not change when the applied rule set
> changes (F-J), meaning the hash triplet cannot be used to identify
> what was validated against.

F-J shipped in v2.3.17 — the validate response now carries the
correct hashes. What's still missing: get_audit_event(event_id)
does NOT return them, because the persistence layer never received
them. A regulator looking up "which exact rule set was applied to
record X?" can read contract_name + contract_version + context but
must recompute the effective_rule_hash from the live contract head
— which may have changed since validation.

Sonnet's pre-impl review (a07f37d65358bb2fe) directed:
  - Persist all three hashes (effective_rule_hash, entry_hash,
    content_hash). Same blast radius as one column on the
    code-change side; v2.4 wouldn't need a follow-on migration.
  - Plumb hashes from the validate route through to record_batch.
    No recomputation in quality_stats.
  - No backfill of existing rows — recomputing from current head
    would assert false history. Empty-string sentinel for pre-Cluster-C
    rows. Document the migration boundary.
  - Optional[str] with default empty string on the Pydantic model
    (not None — downstream string operations must not crash).
  - Symmetric edit: validate AND validate/batch persist hashes.
  - Test idempotent migration explicitly (3b).

Single record_batch callsite is `_async_record_quality_stats` in
deps.py:102, called from BOTH validate routes
(routes_validation.py:154 single-record + 328 batch). Symmetric edit
plumbs hashes through this single chokepoint.
"""

import sqlite3
import uuid

import pytest


class TestAuditHashSchema:
    """SQLite migration adds three hash columns to quality_stats."""

    def test_new_db_has_hash_columns(self, tmp_path):
        """Fresh schema after this release has the columns from
        the start (no migration needed for new installs)."""
        from opendqv.core.quality_stats import QualityStats
        QualityStats(str(tmp_path / "new.db"))
        conn = sqlite3.connect(str(tmp_path / "new.db"))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(quality_stats)")}
        finally:
            conn.close()
        assert "effective_rule_hash" in cols, cols
        assert "entry_hash" in cols, cols
        assert "content_hash" in cols, cols

    def test_migration_adds_hash_columns_to_legacy_db(self, tmp_path):
        """A DB created against the v2.3.21 schema (no hash columns)
        gets the columns added without raising. Idempotent — running
        the migration again is a no-op."""
        db = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE quality_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL DEFAULT '',
                contract_name TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT 'default',
                recorded_at TEXT NOT NULL,
                total_records INTEGER NOT NULL,
                passed INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                pass_rate_pct REAL NOT NULL,
                rule_failure_counts TEXT NOT NULL DEFAULT '{}',
                agent_id TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'enforcement',
                caller_principal TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute(
            "INSERT INTO quality_stats (event_id, contract_name, contract_version, "
            "context, recorded_at, total_records, passed, failed, pass_rate_pct) "
            "VALUES ('legacy-evt', 'c', '1.0', 'default', '2026-01-01T00:00:00+00:00', "
            "1, 1, 0, 100.0)"
        )
        conn.commit()
        conn.close()

        # First QualityStats() — runs migration.
        from opendqv.core.quality_stats import QualityStats
        QualityStats(db)
        # Second QualityStats() — re-runs migration ALTERs (must no-op).
        QualityStats(db)

        conn = sqlite3.connect(db)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(quality_stats)")}
            row = conn.execute(
                "SELECT effective_rule_hash, entry_hash, content_hash "
                "FROM quality_stats WHERE event_id = 'legacy-evt'"
            ).fetchone()
        finally:
            conn.close()
        assert "effective_rule_hash" in cols
        assert "entry_hash" in cols
        assert "content_hash" in cols
        # Existing row has empty-string sentinel — no false history.
        assert row == ("", "", ""), row


class TestAuditHashRoundTrip:
    """validate → record_batch → get_event → response carries the
    same triplet that validate emitted."""

    def test_record_batch_persists_hashes(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        event_id = str(uuid.uuid4())
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=1, passed=1, failed=0,
            rule_failure_counts={}, agent_id="agent-x",
            mode="enforcement", event_id=event_id,
            caller_principal="alice@bank.example.com",
            effective_rule_hash="eff-deadbeef",
            entry_hash="entry-feedface",
            content_hash="content-cafebabe",
        )
        ev = qs.get_event(event_id)
        assert ev is not None
        assert ev["effective_rule_hash"] == "eff-deadbeef", ev
        assert ev["entry_hash"] == "entry-feedface", ev
        assert ev["content_hash"] == "content-cafebabe", ev

    def test_record_batch_no_hashes_stores_empty_string_sentinel(self, tmp_path):
        """Backwards-compat: callers that don't pass hashes (e.g. an
        old test fixture) get empty strings, not crashes."""
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        event_id = str(uuid.uuid4())
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=1, passed=1, failed=0, rule_failure_counts={},
            event_id=event_id,
        )
        ev = qs.get_event(event_id)
        assert ev["effective_rule_hash"] == "", ev
        assert ev["entry_hash"] == "", ev
        assert ev["content_hash"] == "", ev


class TestValidateAuditChainCarriesHashes:
    """End-to-end via FastAPI client: validate response and
    get_audit_event(event_id) must agree on the triplet."""

    def test_single_validate_persists_response_hashes(self, client):
        from opendqv.security.auth import create_pat
        validator = create_pat("validator-c", role="validator")["token"]
        admin = create_pat("admin-c", role="admin")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post(
            "/api/v1/validate?allow_draft=true",
            json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        eff = resp["effective_rule_hash"]
        ent = resp["entry_hash"]
        cnt = resp["content_hash"]
        event_id = resp["event_id"]
        assert eff and ent and cnt, resp

        # Audit chain must now carry them too.
        r2 = client.get(
            f"/api/v1/audit/events/{event_id}",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r2.status_code == 200, r2.text
        ev = r2.json()
        assert ev["effective_rule_hash"] == eff, (
            f"v2.3.22 Cluster C regression: validate emitted "
            f"effective_rule_hash={eff!r} but audit row carries "
            f"effective_rule_hash={ev.get('effective_rule_hash')!r}. "
            f"The chain is broken — regulator cannot answer 'which "
            f"rule set was applied to event_id={event_id!r}'."
        )
        assert ev["entry_hash"] == ent, ev
        assert ev["content_hash"] == cnt, ev

    def test_batch_validate_persists_response_hashes(self, client):
        """Symmetric edit: validate/batch goes through the same
        _async_record_quality_stats wrapper. Same hashes must reach
        the audit row."""
        from opendqv.security.auth import create_pat
        validator = create_pat("validator-batch-c", role="validator")["token"]
        admin = create_pat("admin-batch-c", role="admin")["token"]

        body = {
            "contract": "customer",
            "records": [
                {"name": "Alice", "age": 30, "email": "a@b.co"},
                {"name": "Bob", "age": 25, "email": "b@b.co"},
            ],
        }
        r = client.post(
            "/api/v1/validate/batch?allow_draft=true",
            json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        eff = resp["effective_rule_hash"]
        ent = resp["entry_hash"]
        cnt = resp["content_hash"]
        event_id = resp["event_id"]
        assert eff and ent and cnt

        r2 = client.get(
            f"/api/v1/audit/events/{event_id}",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r2.status_code == 200, r2.text
        ev = r2.json()
        assert ev["effective_rule_hash"] == eff, ev
        assert ev["entry_hash"] == ent, ev
        assert ev["content_hash"] == cnt, ev

    def test_distinct_contracts_persist_distinct_effective_hashes(self, client):
        """Two different contracts → two different effective_rule_hashes
        on the persisted rows. Catches a regression where the same
        constant got passed everywhere."""
        from opendqv.security.auth import create_pat
        validator = create_pat("validator-distinct-c", role="validator")["token"]
        admin = create_pat("admin-distinct-c", role="admin")["token"]

        # customer contract — uses customer-specific rules.
        r1 = client.post(
            "/api/v1/validate?allow_draft=true",
            json={"contract": "customer",
                  "record": {"name": "Alice", "age": 30, "email": "a@b.co"}},
            headers={"Authorization": f"Bearer {validator}"},
        )
        # banking_transaction — different ruleset.
        r2 = client.post(
            "/api/v1/validate?allow_draft=true",
            json={"contract": "banking_transaction",
                  "record": {
                      "transaction_id": "TX-001",
                      "amount": 100.0, "currency": "USD",
                      "transaction_date": "2026-01-01",
                      "account_id": "ACC-001", "transaction_type": "DEBIT",
                  }},
            headers={"Authorization": f"Bearer {validator}"},
        )
        if r1.status_code != 200 or r2.status_code != 200:
            pytest.skip("contract fixtures not available in test env")

        ev1 = client.get(
            f"/api/v1/audit/events/{r1.json()['event_id']}",
            headers={"Authorization": f"Bearer {admin}"},
        ).json()
        ev2 = client.get(
            f"/api/v1/audit/events/{r2.json()['event_id']}",
            headers={"Authorization": f"Bearer {admin}"},
        ).json()
        assert ev1["effective_rule_hash"] != ev2["effective_rule_hash"], (
            f"Two different contracts persisted identical "
            f"effective_rule_hash={ev1['effective_rule_hash']!r}. "
            f"Suggests the value was hardcoded or shared in the fix."
        )
