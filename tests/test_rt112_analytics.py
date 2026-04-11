"""
RT112 — Analytics Layer tests.

Covers:
- agent_id persistence in quality_stats SQLite
- Migration on existing DB (idempotent)
- get_windowed_totals fallback
- get_agent_breakdown
- ValidationStats event tuple with agent_id
- by_agent in get_windowed_summary
- rule_failure_velocity bucketing
"""

import json
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from opendqv.core.quality_stats import QualityStats
from opendqv.core.quality_analytics import QualityAnalytics
from opendqv.monitoring import ValidationStats


# ── QualityStats: agent_id persistence ────────────────────────────────

class TestAgentIdPersistence:
    def _qs(self):
        return QualityStats(":memory:")

    def test_record_batch_stores_agent_id(self):
        qs = self._qs()
        qs.record_batch("c1", "v1", "default", 10, 8, 2, {"rule_a": 2}, agent_id="source-x")
        rows = qs._connect().execute(
            "SELECT agent_id FROM quality_stats WHERE contract_name = 'c1'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "source-x"

    def test_record_batch_empty_agent_id_stored_as_blank(self):
        qs = self._qs()
        qs.record_batch("c2", "v1", "default", 5, 5, 0, {})
        rows = qs._connect().execute(
            "SELECT agent_id FROM quality_stats WHERE contract_name = 'c2'"
        ).fetchall()
        assert rows[0][0] == ""

    def test_migration_idempotent_on_existing_db(self, tmp_path):
        """Adding agent_id to a DB that already has it must not raise."""
        db = str(tmp_path / "test.db")
        qs1 = QualityStats(db)
        qs1.record_batch("m1", "v1", "default", 1, 1, 0, {})
        # Instantiate again — migration ALTER TABLE runs again; must be no-op
        qs2 = QualityStats(db)
        qs2.record_batch("m1", "v1", "default", 1, 1, 0, {}, agent_id="probe")
        rows = qs2._connect().execute(
            "SELECT agent_id FROM quality_stats WHERE agent_id != ''"
        ).fetchall()
        assert len(rows) == 1

    def test_migration_adds_column_to_existing_db_without_column(self, tmp_path):
        """DB created without agent_id column should be migrated transparently."""
        db = str(tmp_path / "legacy.db")
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
        conn.commit()
        conn.close()
        # QualityStats init should migrate without error
        qs = QualityStats(db)
        qs.record_batch("leg", "v1", "default", 3, 2, 1, {}, agent_id="migrated")
        rows = qs._connect().execute(
            "SELECT agent_id FROM quality_stats"
        ).fetchall()
        assert rows[0][0] == "migrated"


# ── QualityStats: get_windowed_totals ─────────────────────────────────

class TestGetWindowedTotals:
    def _qs(self):
        return QualityStats(":memory:")

    def test_returns_correct_totals(self):
        qs = self._qs()
        qs.record_batch("c1", "v1", "default", 10, 7, 3, {"r1": 3})
        qs.record_batch("c1", "v1", "default", 5, 5, 0, {})
        result = qs.get_windowed_totals("c1", window_hours=24)
        assert result["total"] == 15
        assert result["passed"] == 12
        assert result["failed"] == 3
        assert result["pass_rate"] == pytest.approx(12 / 15, abs=0.001)

    def test_returns_zeros_when_no_data(self):
        qs = self._qs()
        result = qs.get_windowed_totals("missing_contract", window_hours=24)
        assert result["total"] == 0
        assert result["pass_rate"] == 1.0

    def test_top_failing_rules_aggregated(self):
        qs = self._qs()
        qs.record_batch("c2", "v1", "default", 10, 8, 2, {"rule_x": 2})
        qs.record_batch("c2", "v1", "default", 10, 6, 4, {"rule_x": 3, "rule_y": 1})
        result = qs.get_windowed_totals("c2", window_hours=24)
        assert result["top_failing_rules"]["rule_x"] == 5
        assert result["top_failing_rules"]["rule_y"] == 1


# ── QualityStats: get_agent_breakdown ─────────────────────────────────

class TestGetAgentBreakdown:
    def _qs(self):
        return QualityStats(":memory:")

    def test_breakdown_by_agent(self):
        qs = self._qs()
        qs.record_batch("c1", "v1", "default", 10, 8, 2, {}, agent_id="feed-a")
        qs.record_batch("c1", "v1", "default", 5, 2, 3, {}, agent_id="feed-b")
        breakdown = qs.get_agent_breakdown("c1", window_hours=24)
        assert len(breakdown) == 2
        agents = {r["agent_id"]: r for r in breakdown}
        assert agents["feed-a"]["passed"] == 8
        assert agents["feed-b"]["failed"] == 3

    def test_excludes_blank_agent_id(self):
        qs = self._qs()
        qs.record_batch("c1", "v1", "default", 10, 10, 0, {})
        qs.record_batch("c1", "v1", "default", 5, 3, 2, {}, agent_id="named")
        breakdown = qs.get_agent_breakdown("c1", window_hours=24)
        assert len(breakdown) == 1
        assert breakdown[0]["agent_id"] == "named"

    def test_sorted_by_total_descending(self):
        qs = self._qs()
        qs.record_batch("c1", "v1", "default", 5, 5, 0, {}, agent_id="small")
        qs.record_batch("c1", "v1", "default", 100, 90, 10, {}, agent_id="big")
        breakdown = qs.get_agent_breakdown("c1", window_hours=24)
        assert breakdown[0]["agent_id"] == "big"


# ── ValidationStats: agent_id in events ───────────────────────────────

class TestValidationStatsAgentId:
    def test_event_tuple_includes_agent_id(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 10.0, agent_id="my-source")
        ts, contract, ctx, valid, latency_ms, agent_id = list(vs._events)[0]
        assert agent_id == "my-source"
        assert contract == "c1"

    def test_event_tuple_empty_agent_id(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 10.0)
        ts, contract, ctx, valid, latency_ms, agent_id = list(vs._events)[0]
        assert agent_id == ""

    def test_by_agent_appears_when_multiple_agents(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="agent-a")
        vs.record("c1", "ctx", False, 1, 0, 2.0, agent_id="agent-b")
        summary = vs.get_windowed_summary(window_hours=1)
        assert "by_agent" in summary
        assert "agent-a" in summary["by_agent"]
        assert "agent-b" in summary["by_agent"]

    def test_by_agent_absent_when_single_agent(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="only-one")
        summary = vs.get_windowed_summary(window_hours=1)
        assert "by_agent" not in summary

    def test_by_agent_pass_rate_correct(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="b")
        summary = vs.get_windowed_summary(window_hours=1)
        assert summary["by_agent"]["a"]["pass_rate"] == 1.0
        assert summary["by_agent"]["b"]["pass_rate"] == 0.0


# ── rule_failure_velocity ──────────────────────────────────────────────

class TestRuleFailureVelocity:
    def _qa_with_data(self, tmp_path):
        db = str(tmp_path / "test.db")
        qs = QualityStats(db)
        # Two batches with different rule failures
        qs.record_batch("pop", "v1", "default", 10, 7, 3, {"rule_a": 3})
        qs.record_batch("pop", "v1", "default", 5, 4, 1, {"rule_a": 1, "rule_b": 1})
        return QualityAnalytics(db)

    def test_returns_correct_structure(self, tmp_path):
        qa = self._qa_with_data(tmp_path)
        result = qa.rule_failure_velocity("pop", window_hours=24, bucket_minutes=60)
        assert result["contract"] == "pop"
        assert result["window_hours"] == 24
        assert result["bucket_minutes"] == 60
        assert "series" in result
        assert isinstance(result["series"], dict)

    def test_top_rules_by_total(self, tmp_path):
        qa = self._qa_with_data(tmp_path)
        result = qa.rule_failure_velocity("pop", window_hours=24, bucket_minutes=60)
        # rule_a has 4 total failures, rule_b has 1
        assert "rule_a" in result["series"]

    def test_empty_when_no_data(self, tmp_path):
        db = str(tmp_path / "empty.db")
        QualityStats(db)  # ensure table exists
        qa = QualityAnalytics(db)
        result = qa.rule_failure_velocity("nonexistent", window_hours=24)
        assert result["series"] == {}

    def test_bucket_structure(self, tmp_path):
        qa = self._qa_with_data(tmp_path)
        result = qa.rule_failure_velocity("pop", window_hours=24, bucket_minutes=60)
        for rule, buckets in result["series"].items():
            for b in buckets:
                assert "bucket" in b
                assert "failures" in b
                assert b["failures"] >= 0

    def test_limits_to_top_5_rules(self, tmp_path):
        db = str(tmp_path / "many.db")
        qs = QualityStats(db)
        counts = {f"rule_{i}": i for i in range(1, 9)}  # 8 rules
        qs.record_batch("big", "v1", "default", 100, 64, 36, counts)
        qa = QualityAnalytics(db)
        result = qa.rule_failure_velocity("big", window_hours=24)
        assert len(result["series"]) <= 5

    def test_outside_window_excluded(self, tmp_path):
        db = str(tmp_path / "old.db")
        qs = QualityStats(db)
        # Write a row with a timestamp far in the past
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        conn = qs._connect()
        conn.execute(
            "INSERT INTO quality_stats "
            "(contract_name, contract_version, context, recorded_at, total_records, "
            "passed, failed, pass_rate, rule_failure_counts, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("old_c", "v1", "default", old_ts, 10, 5, 5, 0.5, json.dumps({"old_rule": 5}), ""),
        )
        conn.commit()
        qa = QualityAnalytics(db)
        result = qa.rule_failure_velocity("old_c", window_hours=24)
        assert result["series"] == {}
