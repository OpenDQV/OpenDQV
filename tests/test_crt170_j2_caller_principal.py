"""
tests/test_crt170_j2_caller_principal.py — CRT170/J2 acceptance.

Pins the caller_principal semantic introduced in v2.3.7.

Before v2.3.7:
    `agent_id` was caller-asserted only — anyone could send
    {"agent_id": "anyone"} and have it stored on the audit row.
    No server-derived identity was carried alongside it, so a
    response could not be used to attribute a validation back to
    an authenticated caller.

From v2.3.7:
    `caller_principal` is a new response field derived server-side
    from the authenticated token's JWT sub claim (or "anonymous"
    in AUTH_MODE=open). It cannot be spoofed. The pre-existing
    `agent_id` field is preserved with its original
    self-labelling semantics — clients keep their session
    correlation ID, and now also get a trustable attribution key.

Working principle (CRT170, extends J1, J3, J4, J6): a response
field's value must reflect what its name claims. agent_id claims
to be a caller-asserted label and continues to do so;
caller_principal claims to be the authenticated identity and
provably is.
"""
from fastapi.testclient import TestClient


# ── Single-record path ─────────────────────────────────────────────────


class TestSingleRecordCallerPrincipal:

    def test_caller_principal_present_and_derived_from_token(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/validate",
            json={
                "record": {"name": "Alice", "email": "alice@example.com", "age": 25},
                "contract": "customer",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "caller_principal" in body
        # `auth_headers` fixture uses the testuser PAT — sub should be "testuser"
        assert body["caller_principal"] == "testuser"

    def test_spoofed_agent_id_does_not_become_caller_principal(
        self, client: TestClient, auth_headers
    ):
        """A caller can label themselves anything via agent_id — caller_principal
        must remain the authenticated subject."""
        resp = client.post(
            "/api/v1/validate",
            json={
                "record": {"name": "Bob", "email": "bob@example.com", "age": 30},
                "contract": "customer",
                "agent_id": "totally-not-the-real-caller",
            },
            headers=auth_headers,
        )
        body = resp.json()
        assert body["agent_id"] == "totally-not-the-real-caller"
        assert body["caller_principal"] == "testuser"
        assert body["agent_id"] != body["caller_principal"]

    def test_two_distinct_tokens_produce_two_distinct_principals(
        self, client: TestClient, auth_headers, editor_headers
    ):
        record = {"name": "C", "email": "c@example.com", "age": 40}
        r1 = client.post(
            "/api/v1/validate",
            json={"record": record, "contract": "customer"},
            headers=auth_headers,
        )
        r2 = client.post(
            "/api/v1/validate",
            json={"record": record, "contract": "customer"},
            headers=editor_headers,
        )
        assert r1.json()["caller_principal"] != r2.json()["caller_principal"]


# ── Batch path ─────────────────────────────────────────────────────────


class TestBatchCallerPrincipal:

    def test_batch_response_carries_caller_principal(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/validate/batch",
            json={
                "records": [
                    {"name": "A", "email": "a@example.com", "age": 20},
                    {"name": "B", "email": "b@example.com", "age": 30},
                ],
                "contract": "customer",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["caller_principal"] == "testuser"

    def test_batch_caller_principal_independent_of_agent_id(
        self, client: TestClient, auth_headers
    ):
        resp = client.post(
            "/api/v1/validate/batch",
            json={
                "records": [{"name": "A", "email": "a@example.com", "age": 20}],
                "contract": "customer",
                "agent_id": "claimed-as-someone-else",
            },
            headers=auth_headers,
        )
        body = resp.json()
        assert body["agent_id"] == "claimed-as-someone-else"
        assert body["caller_principal"] == "testuser"


# ── Persistence: caller_principal lands on the SQLite row ──────────────


class TestQualityStatsPersistence:

    def test_caller_principal_is_persisted_on_audit_row(self, tmp_path):
        """quality_stats.record_batch must accept and persist caller_principal."""
        from opendqv.core.quality_stats import QualityStats

        db = tmp_path / "qs.db"
        qs = QualityStats(str(db))
        qs.record_batch(
            contract_name="customer",
            contract_version="1.0",
            context=None,
            total=1,
            passed=1,
            failed=0,
            rule_failure_counts={},
            agent_id="self-labelled",
            caller_principal="alice@example.com",
            event_id="evt-1",
        )

        import sqlite3
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT agent_id, caller_principal FROM quality_stats"
        ).fetchall()
        conn.close()
        assert rows == [("self-labelled", "alice@example.com")]

    def test_caller_principal_default_blank_when_omitted(self, tmp_path):
        """Old call sites that don't pass caller_principal must still work."""
        from opendqv.core.quality_stats import QualityStats

        db = tmp_path / "qs.db"
        qs = QualityStats(str(db))
        qs.record_batch(
            contract_name="customer",
            contract_version="1.0",
            context=None,
            total=1,
            passed=1,
            failed=0,
            rule_failure_counts={},
        )
        import sqlite3
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT caller_principal FROM quality_stats"
        ).fetchall()
        conn.close()
        assert rows == [("",)]


# ── Migration safety: existing DBs without the column don't break ──────


class TestMigrationIdempotent:

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        """Simulate a v2.3.6-shaped DB and confirm v2.3.7 init adds the column."""
        import sqlite3
        from opendqv.core.quality_stats import QualityStats

        db_path = tmp_path / "legacy.db"
        legacy_create = """
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
            pass_rate REAL NOT NULL,
            rule_failure_counts TEXT NOT NULL DEFAULT '{}',
            agent_id TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'enforcement'
        )
        """
        conn = sqlite3.connect(str(db_path))
        conn.execute(legacy_create)
        conn.commit()
        conn.close()

        QualityStats(str(db_path))  # __init__ runs migrations

        conn = sqlite3.connect(str(db_path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(quality_stats)").fetchall()}
        conn.close()
        assert "caller_principal" in cols
