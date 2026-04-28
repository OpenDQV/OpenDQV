"""
v2.3.23 round-3 #8 — persist batch-avg latency_ms so hydrated rows
emit a real per-event proxy instead of null.

Persona B 2026-04-28 outside review #3 (and prior rounds): hydrated
rows on /api/v1/stats recent_history surface `latency_ms: null`.
Reviewer flagged it across multiple rounds — the doc-only deferral
wasn't enough.

Sonnet pre-impl review (a2180d103efcbb82c) verdict: option A. Add
`latency_ms_avg REAL NOT NULL DEFAULT 0.0` column to quality_stats.
record_batch persists batch avg. Hydration uses it for synthesised
events. Null-when-zero policy: legacy rows with latency_ms_avg=0 emit
latency_ms=null on the wire (honest "not available", not misleading
0ms).

Tests cover:
  - Migration is idempotent (column exists after init).
  - record_batch persists latency_ms_avg.
  - record_batch default 0.0 when caller doesn't pass it (backward
    compat).
  - Hydration reads latency_ms_avg into synthesised history entries.
  - Hydration null-when-zero: legacy row → latency_ms is None on
    history; v2.3.23+ row with positive latency → real value emitted.
  - Single-record /validate path persists elapsed_ms.
  - Batch /validate path persists batch-avg.
"""

import sqlite3

import pytest


# ── Schema migration ──────────────────────────────────────────────────

class TestSchemaMigration:
    def test_latency_column_exists_after_init(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "lat.db")
        QualityStats(db)
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(quality_stats)").fetchall()}
        conn.close()
        assert "latency_ms_avg" in cols, (
            f"v2.3.23 round-3 #8: quality_stats must carry latency_ms_avg "
            f"column. Got: {cols}"
        )

    def test_migration_is_idempotent(self, tmp_path):
        """Re-instantiating QualityStats on an existing DB must not
        fail (column already exists)."""
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "idem.db")
        QualityStats(db)
        # Second instantiation — must not raise.
        QualityStats(db)


# ── record_batch persists latency_ms_avg ───────────────────────────────

class TestRecordBatchPersistsLatency:
    def test_explicit_latency_persisted(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "rec.db")
        qs = QualityStats(db)
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=10, passed=8, failed=2, rule_failure_counts={},
            event_id="evt-lat-001", latency_ms_avg=4.7,
        )
        ev = qs.get_event("evt-lat-001")
        assert ev is not None
        # get_event doesn't currently expose latency_ms_avg; check directly.
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT latency_ms_avg FROM quality_stats WHERE event_id=?",
            ("evt-lat-001",),
        ).fetchone()
        conn.close()
        assert row[0] == pytest.approx(4.7), (
            f"v2.3.23 round-3 #8: record_batch must persist latency_ms_avg. "
            f"Got: {row}"
        )

    def test_default_zero_when_omitted(self, tmp_path):
        """Backward compat: callers that don't pass latency_ms_avg get
        the 0.0 sentinel — read boundary translates to wire null."""
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "def.db")
        qs = QualityStats(db)
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=1, passed=1, failed=0, rule_failure_counts={},
            event_id="evt-default-001",
        )
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT latency_ms_avg FROM quality_stats WHERE event_id=?",
            ("evt-default-001",),
        ).fetchone()
        conn.close()
        assert row[0] == 0.0


# ── Hydration uses persisted latency for synthesised events ───────────

class TestHydrationUsesPersistedLatency:
    def test_hydrated_history_carries_real_latency_when_persisted(
        self, tmp_path,
    ):
        from opendqv.core.quality_stats import QualityStats
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "hyd.db")
        qs = QualityStats(db)
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=5, passed=5, failed=0, rule_failure_counts={},
            event_id="evt-hyd-001", latency_ms_avg=2.3,
        )

        fresh_stats = ValidationStats()
        hydrate_stats_from_persistent_store(fresh_stats, db)
        # The hydrated history entry must carry latency_ms = persisted avg.
        hydrated = [h for h in fresh_stats.history if h.get("hydrated")]
        assert hydrated, "no hydrated entries — hydration didn't run"
        for h in hydrated:
            if h["contract"] == "customer":
                assert h["latency_ms"] == pytest.approx(2.3), (
                    f"v2.3.23 round-3 #8: hydrated history entry must carry "
                    f"the persisted batch-average latency. Got: {h}"
                )

    def test_hydrated_history_emits_null_for_legacy_zero(self, tmp_path):
        """Legacy rows with latency_ms_avg=0 — honest "not available"
        signal as null, not misleading 0.0 'really fast'."""
        from opendqv.core.quality_stats import QualityStats
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "leg.db")
        qs = QualityStats(db)
        # No latency_ms_avg passed → defaults to 0.0 in storage.
        qs.record_batch(
            contract_name="customer", contract_version="1.0", context=None,
            total=3, passed=3, failed=0, rule_failure_counts={},
            event_id="evt-legacy-001",
        )
        fresh = ValidationStats()
        hydrate_stats_from_persistent_store(fresh, db)
        legacy_entries = [
            h for h in fresh.history if h.get("hydrated") and h["contract"] == "customer"
        ]
        assert legacy_entries
        for h in legacy_entries:
            assert h["latency_ms"] is None, (
                f"v2.3.23 round-3 #8: legacy row (latency_ms_avg=0) must "
                f"emit latency_ms=null on hydrated history (honest 'not "
                f"available'), never 0.0 (misleading 'really fast'). "
                f"Got: {h}"
            )


# ── Live validate paths persist latency_ms_avg ─────────────────────────

class TestValidateRoutesPersistLatency:
    def test_single_validate_persists_elapsed_ms(self, client, auth_headers):
        """Single-record /validate path must pass elapsed_ms as
        latency_ms_avg to record_batch."""
        # Submit a valid record.
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "customer", "record": {
                "name": "lat-test", "email": "lat@test.com", "age": 30,
                "balance": 100.0, "id": "lat-test-001",
            }},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        # Pull the audit row and assert latency_ms_avg > 0.
        body = resp.json()
        event_id = body.get("event_id")
        from opendqv.api import deps as _d
        conn = sqlite3.connect(_d._quality_stats._db_path)
        row = conn.execute(
            "SELECT latency_ms_avg FROM quality_stats WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] > 0.0, (
            f"v2.3.23 round-3 #8: single /validate must persist a "
            f"non-zero latency_ms_avg (the elapsed_ms of the call). "
            f"Got: {row}"
        )

    def test_batch_validate_persists_batch_avg(self, client, auth_headers):
        """Batch /validate path must pass elapsed_ms / batch_size as
        latency_ms_avg."""
        resp = client.post(
            "/api/v1/validate/batch",
            json={"contract": "customer", "records": [
                {"name": "u1", "email": "u1@x.com", "age": 25, "balance": 0, "id": "b1"},
                {"name": "u2", "email": "u2@x.com", "age": 30, "balance": 0, "id": "b2"},
                {"name": "u3", "email": "u3@x.com", "age": 35, "balance": 0, "id": "b3"},
            ]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        event_id = body.get("event_id")
        from opendqv.api import deps as _d
        conn = sqlite3.connect(_d._quality_stats._db_path)
        row = conn.execute(
            "SELECT latency_ms_avg, total_records FROM quality_stats WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        latency_avg, total = row
        assert latency_avg > 0.0, (
            f"v2.3.23 round-3 #8: batch /validate must persist a "
            f"non-zero batch-avg latency_ms_avg. Got: {row}"
        )
        # Sanity: batch avg should be < single-record latency for a 3-record batch.
        # Just assert it's a plausible value (< 1000ms for a tiny batch).
        assert latency_avg < 1000, (
            f"batch latency_ms_avg looks wrong (>1s): {latency_avg}"
        )
