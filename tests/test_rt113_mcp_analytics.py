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

from opendqv.monitoring import ValidationStats
from opendqv.core.quality_stats import QualityStats


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

    def test_latency_scoped_to_agent(self):
        """latency.sample_size must reflect only the filtered agent's events."""
        vs = ValidationStats()
        # 3 calls for 'fast', 5 calls for 'slow', different latencies
        for _ in range(3):
            vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="fast")
        for _ in range(5):
            vs.record("c1", "ctx", True, 0, 0, 100.0, agent_id="slow")
        fast = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="fast")
        slow = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="slow")
        assert fast["latency"]["sample_size"] == 3
        assert slow["latency"]["sample_size"] == 5
        assert fast["latency"]["avg_ms"] == 1.0
        assert slow["latency"]["avg_ms"] == 100.0

    def test_latency_empty_when_agent_absent(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="present")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="absent")
        assert result["latency"]["sample_size"] == 0
        assert result["latency"]["avg_ms"] is None

    def test_effective_window_present_in_unfiltered_windowed(self):
        """Unfiltered windowed summary must also carry effective_window_seconds."""
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        result = vs.get_windowed_summary(window_hours=24)
        assert "effective_window_seconds" in result
        assert "requested_window_hours" in result
        assert result["requested_window_hours"] == 24
        assert result["effective_window_seconds"] < 86400

    def test_effective_window_present_and_capped_by_uptime(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        result = vs.get_windowed_summary_for_agent(window_hours=24, agent_id="a")
        # Fresh stats object — uptime measured in seconds, so effective window
        # is FAR less than 24h (86400s), tells caller the request was not
        # fully coverable.
        assert "effective_window_seconds" in result
        assert result["effective_window_seconds"] < 86400
        assert result["requested_window_hours"] == 24

    def test_recent_history_scoped_to_agent(self):
        """recent_history must only contain events from the filtered agent."""
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="alpha")
        vs.record("c1", "ctx", False, 1, 0, 1.0,
                  errors=[{"field": "x", "rule": "rx", "severity": "error"}],
                  agent_id="beta")
        vs.record("c2", "ctx", True, 0, 0, 1.0, agent_id="alpha")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="alpha")
        agents_in_history = {h.get("agent_id") for h in result["recent_history"]}
        assert agents_in_history == {"alpha"}, \
            f"history leaked other agents: {agents_in_history}"
        assert len(result["recent_history"]) == 2

    def test_top_failing_fields_scoped_to_agent(self):
        """When filtered to one agent, top_failing_fields must only contain that agent's errors."""
        vs = ValidationStats()
        err_a = [{"field": "x", "rule": "rx", "severity": "error"}]
        err_b = [{"field": "y", "rule": "ry", "severity": "error"}]
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err_a, agent_id="agent-a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err_a, agent_id="agent-a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err_b, agent_id="agent-b")
        result = vs.get_windowed_summary_for_agent(window_hours=1, agent_id="agent-a")
        rules = [f["rule"] for f in result["top_failing_fields"]]
        assert "rx" in rules
        assert "ry" not in rules
        # top_failing_fields_by_agent is redundant when already filtered — must be removed
        assert "top_failing_fields_by_agent" not in result


# ── top_failing_fields_by_agent (per-agent failure breakdown) ──────────

class TestTopFailingFieldsByAgent:
    def test_empty_when_no_errors(self):
        vs = ValidationStats()
        vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="a")
        summary = vs.get_summary()
        assert summary["top_failing_fields_by_agent"] == {}

    def test_aggregates_by_agent_id(self):
        vs = ValidationStats()
        err = [{"field": "email", "rule": "email_format", "severity": "error"}]
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err, agent_id="agent-a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err, agent_id="agent-a")
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err, agent_id="agent-b")
        summary = vs.get_summary()
        by_agent = summary["top_failing_fields_by_agent"]
        assert "agent-a" in by_agent
        assert "agent-b" in by_agent
        assert by_agent["agent-a"][0]["count"] == 2
        assert by_agent["agent-a"][0]["rule"] == "email_format"
        assert by_agent["agent-b"][0]["count"] == 1

    def test_missing_agent_id_bucketed_as_unattributed(self):
        vs = ValidationStats()
        err = [{"field": "x", "rule": "rx", "severity": "error"}]
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err, agent_id="")
        summary = vs.get_summary()
        assert "unattributed" in summary["top_failing_fields_by_agent"]

    def test_ranks_per_agent_top_first(self):
        vs = ValidationStats()
        r1 = [{"field": "a", "rule": "r_a", "severity": "error"}]
        r2 = [{"field": "b", "rule": "r_b", "severity": "error"}]
        for _ in range(5):
            vs.record("c1", "ctx", False, 1, 0, 1.0, errors=r1, agent_id="src")
        for _ in range(2):
            vs.record("c1", "ctx", False, 1, 0, 1.0, errors=r2, agent_id="src")
        summary = vs.get_summary()
        src = summary["top_failing_fields_by_agent"]["src"]
        assert src[0]["rule"] == "r_a"
        assert src[0]["count"] == 5
        assert src[1]["rule"] == "r_b"
        assert src[1]["count"] == 2

    def test_windowed_summary_excludes_old_events(self):
        vs = ValidationStats()
        # Inject an old error event directly (> 1h ago)
        old_ts = time.time() - 7_300
        vs._error_events.append((old_ts, "c1", "old_field", "old_rule", "old-agent"))
        # Recent error
        err = [{"field": "new_field", "rule": "new_rule", "severity": "error"}]
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=err, agent_id="new-agent")

        summary = vs.get_windowed_summary(window_hours=1)
        by_agent = summary["top_failing_fields_by_agent"]
        assert "new-agent" in by_agent
        assert "old-agent" not in by_agent

    def test_cross_contract_under_same_agent(self):
        vs = ValidationStats()
        err_a = [{"field": "x", "rule": "rx", "severity": "error"}]
        err_b = [{"field": "y", "rule": "ry", "severity": "error"}]
        vs.record("contract_a", "ctx", False, 1, 0, 1.0, errors=err_a, agent_id="multi")
        vs.record("contract_b", "ctx", False, 1, 0, 1.0, errors=err_b, agent_id="multi")
        summary = vs.get_summary()
        rules = summary["top_failing_fields_by_agent"]["multi"]
        contracts = {r["contract"] for r in rules}
        assert contracts == {"contract_a", "contract_b"}


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
