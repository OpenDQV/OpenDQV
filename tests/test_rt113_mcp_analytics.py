"""
RT113 — MCP analytics completion tests.

Covers:
- get_contract_latency() per-contract isolation
- get_windowed_summary_for_agent() agent filter
- get_quality_trend MCP tool (local mode)
- agent_id filter on get_quality_metrics MCP tool
"""

import time

import pytest

from monitoring import ValidationStats
from core.quality_stats import QualityStats


# ── Per-contract latency ───────────────────────────────────────────────

class TestGetContractLatency:
    def test_isolates_by_contract(self):
        vs = ValidationStats()
        vs.record("fast_c", "ctx", True, 0, 0, 1.0)
        vs.record("fast_c", "ctx", True, 0, 0, 2.0)
        vs.record("slow_c", "ctx", True, 0, 0, 100.0)
        vs.record("slow_c", "ctx", True, 0, 0, 200.0)

        fast = vs.get_contract_latency("fast_c", window_hours=1)
        slow = vs.get_contract_latency("slow_c", window_hours=1)

        assert fast["avg_ms"] == pytest.approx(1.5, abs=0.1)
        assert slow["avg_ms"] == pytest.approx(150.0, abs=0.1)
        assert fast["sample_size"] == 2
        assert slow["sample_size"] == 2

    def test_returns_no_data_when_contract_absent(self):
        vs = ValidationStats()
        vs.record("other_c", "ctx", True, 0, 0, 5.0)
        result = vs.get_contract_latency("missing_c", window_hours=1)
        assert result["avg_ms"] is None
        assert result["sample_size"] == 0

    def test_respects_window(self):
        vs = ValidationStats()
        # Inject an old event directly (> 1h ago)
        old_ts = time.time() - 7_300
        vs._events.append((old_ts, "c1", "ctx", True, 999.0, ""))
        # Recent event
        vs.record("c1", "ctx", True, 0, 0, 5.0)

        result = vs.get_contract_latency("c1", window_hours=1)
        # Only the recent event should be included
        assert result["sample_size"] == 1
        assert result["avg_ms"] == pytest.approx(5.0, abs=0.1)

    def test_percentiles_present(self):
        vs = ValidationStats()
        for ms in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            vs.record("c1", "ctx", True, 0, 0, ms)
        result = vs.get_contract_latency("c1", window_hours=1)
        assert result["p50_ms"] is not None
        assert result["p95_ms"] is not None
        assert result["p99_ms"] is not None
        assert result["p95_ms"] >= result["p50_ms"]


# ── Agent-filtered summary ─────────────────────────────────────────────

class TestGetWindowedSummaryForAgent:
    def test_scopes_to_agent(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="good-source")
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="good-source")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="bad-source")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="bad-source")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="bad-source")

        good = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="good-source")
        bad = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="bad-source")

        assert good["total_validations"] == 2
        assert good["total_fail"] == 0
        assert bad["total_validations"] == 3
        assert bad["total_pass"] == 0

    def test_unknown_agent_returns_zeros(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="known")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="unknown")
        assert result["total_validations"] == 0
        assert result["by_contract"] == {}

    def test_agent_id_filter_in_result(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="probe")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="probe")
        assert result["agent_id_filter"] == "probe"

    def test_excludes_other_agent_events(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="b")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="a")
        assert result["total_fail"] == 0


# ── get_quality_trend MCP tool ─────────────────────────────────────────

class TestGetQualityTrendTool:
    """Test the _tool_get_quality_trend function in isolation via QualityStats."""

    def _qs_with_data(self, tmp_path):
        db = str(tmp_path / "test.db")
        qs = QualityStats(db)
        qs.record_batch("c1", "v1", "default", 10, 8, 2, {"rule_a": 2})
        qs.record_batch("c1", "v1", "default", 10, 9, 1, {"rule_a": 1})
        return qs, db

    def test_get_trend_returns_points(self, tmp_path):
        qs, _ = self._qs_with_data(tmp_path)
        points = qs.get_trend("c1", days=7)
        assert len(points) >= 1
        assert "pass_rate" in points[0]
        assert "total_records" in points[0]
        assert "top_failing_rules" in points[0]

    def test_trend_direction_improving(self, tmp_path):
        from datetime import datetime, timezone, timedelta
        db = str(tmp_path / "trend.db")
        qs = QualityStats(db)
        # Insert old bad data and recent good data manually
        conn = qs._connect()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        today = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO quality_stats (contract_name, contract_version, context, recorded_at, "
            "total_records, passed, failed, pass_rate, rule_failure_counts, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("improving_c", "v1", "default", yesterday, 10, 3, 7, 0.3, "{}", ""),
        )
        conn.execute(
            "INSERT INTO quality_stats (contract_name, contract_version, context, recorded_at, "
            "total_records, passed, failed, pass_rate, rule_failure_counts, agent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("improving_c", "v1", "default", today, 10, 9, 1, 0.9, "{}", ""),
        )
        conn.commit()
        points = qs.get_trend("improving_c", days=7)
        assert len(points) == 2
        # Latest pass_rate > earliest pass_rate
        assert points[-1]["pass_rate"] > points[0]["pass_rate"]

    def test_trend_empty_for_unknown_contract(self, tmp_path):
        qs = QualityStats(str(tmp_path / "empty.db"))
        points = qs.get_trend("nonexistent", days=7)
        assert points == []


# ── Integration: agent_id filter changes metrics response ──────────────

class TestAgentIdFilterIntegration:
    def test_filter_isolates_source(self):
        vs = ValidationStats()
        # Clean source: 10/10 pass
        for _ in range(10):
            vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="clean-src")
        # Dirty source: 1/10 pass
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="dirty-src")
        for _ in range(9):
            vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="dirty-src")

        clean = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="clean-src")
        dirty = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="dirty-src")

        assert clean["total_pass"] == 10
        assert clean["total_fail"] == 0
        assert dirty["total_pass"] == 1
        assert dirty["total_fail"] == 9

    def test_unfiltered_returns_combined(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="src-a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, agent_id="src-b")

        combined = vs.get_windowed_summary(window_hours=1)
        assert combined["total_validations"] == 2
