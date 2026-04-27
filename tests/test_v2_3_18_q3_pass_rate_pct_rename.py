"""
v2.3.18 Q3 — pass_rate_pct rename: cross-surface contract test.

The single high-leverage recurrence test for the Q3 rename. Asserts:

1. ``pass_rate_pct`` is the canonical field name on every wire surface
   that exposes a pass-rate value. Single name, single scale (percent
   0–100, 1dp).
2. The bare ``pass_rate`` and the redundant ``pass_rate_ratio`` fields
   are absent from every wire response — no surface re-introduces them.
3. Every emitted ``pass_rate_pct`` value falls in [0.0, 100.0] (or is
   None for the by=rule case where pass-rate is not meaningful).
4. The DuckDB storage column is renamed to ``pass_rate_pct`` and stored
   values are in [0, 100] (not [0, 1]) — proves the migration ran.

Pilot decision 2026-04-27: "we need to use pass_rate_pct and drop
pass_rate_ratio". Queen's Standard answer: rename storage column too,
convert audit emit, add this contract test.
"""

import sqlite3

import pytest


# ── Storage column ────────────────────────────────────────────────────

class TestStorageColumnRenamed:
    def test_quality_stats_table_has_pass_rate_pct_column(self, tmp_path):
        """Fresh DB: the column is created as pass_rate_pct."""
        from opendqv.core.quality_stats import QualityStats

        db = str(tmp_path / "fresh.db")
        QualityStats(db)
        conn = sqlite3.connect(db)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(quality_stats)").fetchall()]
        finally:
            conn.close()
        assert "pass_rate_pct" in cols, f"quality_stats column should be pass_rate_pct, got: {cols}"
        assert "pass_rate" not in cols, \
            f"quality_stats must NOT have a bare pass_rate column after migration; got: {cols}"

    def test_legacy_db_with_pass_rate_column_migrates(self, tmp_path):
        """Existing DB with pass_rate (ratio) column: migration renames
        the column AND multiplies values × 100 so storage range is
        [0, 100] consistent with the new wire format."""
        from opendqv.core.quality_stats import QualityStats

        db = str(tmp_path / "legacy.db")
        # Create the legacy schema (no caller_principal, no event_id —
        # whole point is that ALTER TABLE migrations all run on init).
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE quality_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_name TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT 'default',
                recorded_at TEXT NOT NULL,
                total_records INTEGER NOT NULL,
                passed INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                pass_rate REAL NOT NULL,
                rule_failure_counts TEXT NOT NULL DEFAULT '{}'
            )
        """)
        # Pre-existing row with ratio value (0.85 = 85%)
        conn.execute(
            "INSERT INTO quality_stats "
            "(contract_name, contract_version, context, recorded_at, "
            " total_records, passed, failed, pass_rate, rule_failure_counts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy_c", "1.0", "default", "2026-01-01T00:00:00+00:00",
             20, 17, 3, 0.85, "{}"),
        )
        conn.commit()
        conn.close()

        # QualityStats init must rename the column AND multiply values × 100.
        QualityStats(db)

        conn = sqlite3.connect(db)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(quality_stats)").fetchall()]
            row = conn.execute(
                "SELECT pass_rate_pct FROM quality_stats WHERE contract_name = ?",
                ("legacy_c",),
            ).fetchone()
        finally:
            conn.close()

        assert "pass_rate_pct" in cols and "pass_rate" not in cols, \
            f"migration should rename column; got cols: {cols}"
        assert row is not None
        assert row[0] == pytest.approx(85.0, abs=0.01), \
            f"migration should multiply existing ratio (0.85) by 100; got {row[0]}"


# ── Cross-surface absence of bare pass_rate / pass_rate_ratio ─────────

class TestNoLegacyFieldNamesOnWire:
    """No surface should re-introduce the legacy field names. Single
    canonical pass_rate_pct everywhere or nothing."""

    def test_get_summary_has_pass_rate_pct_only(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        s.record(contract="c", context="x", valid=True, error_count=0,
                 warning_count=0, latency_ms=1.0)
        summary = s.get_summary()
        assert "pass_rate_pct" in summary
        assert "pass_rate" not in summary
        assert "pass_rate_ratio" not in summary
        assert 0.0 <= summary["pass_rate_pct"] <= 100.0

    def test_windowed_summary_has_pass_rate_pct_only(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        s.record(contract="c", context="x", valid=True, error_count=0,
                 warning_count=0, latency_ms=1.0)
        windowed = s.get_windowed_summary(window_hours=1)
        assert "pass_rate_pct" in windowed
        assert "pass_rate" not in windowed
        assert "pass_rate_ratio" not in windowed
        assert 0.0 <= windowed["pass_rate_pct"] <= 100.0

    def test_list_agents_has_pass_rate_pct_only(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        s.record(contract="c", context="x", valid=True, error_count=0,
                 warning_count=0, latency_ms=1.0, agent_id="a")
        agents = s.list_agents(window_hours=1)
        assert agents
        for a in agents:
            assert "pass_rate_pct" in a
            assert "pass_rate" not in a, \
                f"list_agents row leaked bare pass_rate: {a}"
            assert "pass_rate_ratio" not in a
            assert 0.0 <= a["pass_rate_pct"] <= 100.0

    def test_rest_stats_response_has_pass_rate_pct_only(self, client, auth_headers):
        # Inject some traffic
        from opendqv.monitoring import stats
        stats.record("rs_c1", "default", True, 0, 0, 1.0, agent_id="rs-a")
        stats.record("rs_c1", "default", False, 1, 0, 1.0, agent_id="rs-a")
        r = client.get("/api/v1/stats", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "pass_rate_pct" in body
        assert "pass_rate" not in body, "REST /stats top-level must use pass_rate_pct only"
        assert "pass_rate_ratio" not in body
        assert 0.0 <= body["pass_rate_pct"] <= 100.0

    def test_rest_agents_response_has_pass_rate_pct_only(self, client, auth_headers):
        from opendqv.monitoring import stats
        stats.record("ra_c1", "default", True, 0, 0, 1.0, agent_id="ra-a")
        r = client.get("/api/v1/agents", headers=auth_headers)
        assert r.status_code == 200
        agents = r.json().get("agents", [])
        for a in agents:
            assert "pass_rate_pct" in a
            assert "pass_rate" not in a
            assert "pass_rate_ratio" not in a


# ── Audit-event payload uses pass_rate_pct ────────────────────────────

class TestAuditEventPassRatePct:
    def test_get_event_returns_pass_rate_pct(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats

        db = str(tmp_path / "audit.db")
        qs = QualityStats(db)
        qs.record_batch(
            "audit_c", "1.0", "default", 100, 80, 20, {"r1": 20},
            event_id="ev-test-1",
        )
        row = qs.get_event("ev-test-1")
        assert row is not None
        assert "pass_rate_pct" in row
        assert "pass_rate" not in row
        assert row["pass_rate_pct"] == pytest.approx(80.0, abs=0.1)
