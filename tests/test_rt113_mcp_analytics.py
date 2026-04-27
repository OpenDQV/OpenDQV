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

    def test_effective_window_reflects_hydrated_events(self, tmp_path):
        """effective_window_seconds should grow when old events are hydrated into the deque,
        even if the API process itself just started."""
        from opendqv.monitoring import hydrate_stats_from_persistent_store
        from opendqv.core.quality_stats import QualityStats
        import sqlite3
        from datetime import datetime, timezone, timedelta

        db = str(tmp_path / "hydrate_test.db")
        QualityStats(db)  # initialize the table schema
        # Seed a row with a 3-day-old timestamp
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO quality_stats (contract_name, contract_version, context, "
            "recorded_at, total_records, passed, failed, pass_rate, "
            "rule_failure_counts, agent_id, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("c1", "v1", "default", three_days_ago, 10, 8, 2,
             0.8, '{"rule_a": 2}', "agent-x", "enforcement"),
        )
        conn.commit()
        conn.close()

        vs = ValidationStats()
        # Fresh stats — no events, so effective_window ~= uptime (tiny)
        assert vs._effective_window_seconds(requested_window_hours=168) < 10

        result = hydrate_stats_from_persistent_store(vs, db, window_hours=168)
        assert not result["skipped"]
        assert result["events"] == 10
        assert result["errors"] == 2

        # After hydration, effective_window should reflect the 3-day-old event
        eff = vs._effective_window_seconds(requested_window_hours=168)
        # 3 days = 259,200s; requested 168h = 604,800s; should clamp to ~3 days
        assert 250_000 < eff < 270_000, f"expected ~3 days of coverage, got {eff}s"

    def test_hydrate_skips_when_db_missing(self, tmp_path):
        from opendqv.monitoring import hydrate_stats_from_persistent_store
        vs = ValidationStats()
        result = hydrate_stats_from_persistent_store(vs, str(tmp_path / "no-such.db"))
        # DB doesn't exist as a valid table → should skip gracefully, not crash
        assert result["events"] == 0
        # Either skipped (OperationalError) or read zero rows from empty new db
        assert result["rows_read"] == 0

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


# ── Filter scoping fidelity (CRT167) ───────────────────────────────────

class TestFilterScopingFidelity:
    """
    Invariant-based scoping test. Catches the entire "inherit-then-override"
    leak family that produced five separate bugs in one day without relying
    on hardcoded expected values.

    Per CRT167 (2026-04-16, chaired by BT, with Grok + Sonnet input):
    - sum of per-agent scoped totals must equal unfiltered total (sum invariant)
    - max(scoped_vals) > min(scoped_vals) — catches uniform-average leaks where
      every scoped response returns the same wrong value
    - self-consistency within each scoped response
    - recent_history entries all belong to the filter target
    """

    def test_scoping_invariants_hold_across_multiple_agents(self):
        vs = ValidationStats()
        # Deliberately imbalanced per-agent volumes so max > min is meaningful.
        # agent_alpha: 10 validations, 2 failures
        for _ in range(8):
            vs.record("c1", "ctx", True, 0, 0, 1.0, agent_id="agent_alpha")
        for _ in range(2):
            vs.record("c1", "ctx", False, 1, 0, 1.0,
                      errors=[{"field": "a", "rule": "ra", "severity": "error"}],
                      agent_id="agent_alpha")
        # agent_beta: 5 validations, 1 failure
        for _ in range(4):
            vs.record("c2", "ctx", True, 0, 0, 2.0, agent_id="agent_beta")
        vs.record("c2", "ctx", False, 1, 0, 2.0,
                  errors=[{"field": "b", "rule": "rb", "severity": "error"}],
                  agent_id="agent_beta")
        # agent_gamma: 2 validations, 0 failures
        for _ in range(2):
            vs.record("c3", "ctx", True, 0, 0, 3.0, agent_id="agent_gamma")

        agents = ["agent_alpha", "agent_beta", "agent_gamma"]

        unfiltered = vs.get_windowed_summary(window_hours=1)

        scoped_totals = []
        scoped_pass = []
        scoped_fail = []
        for aid in agents:
            scoped = vs.get_windowed_summary_for_agent(window_hours=1, agent_id=aid)

            # Self-consistency: pass + fail == total for THIS agent
            assert scoped["total_pass"] + scoped["total_fail"] == scoped["total_validations"], \
                f"{aid}: pass+fail != total_validations"

            # recent_history must only contain this agent's events
            for h in scoped["recent_history"]:
                assert h.get("agent_id") == aid, \
                    f"{aid}: history leaked entry from {h.get('agent_id')}"

            # Leaked fields must be absent (CRT167 Option A)
            assert "total_errors" not in scoped, f"{aid}: total_errors leaked"
            assert "total_warnings" not in scoped, f"{aid}: total_warnings leaked"
            assert "dimensions" not in scoped, f"{aid}: dimensions leaked"

            scoped_totals.append(scoped["total_validations"])
            scoped_pass.append(scoped["total_pass"])
            scoped_fail.append(scoped["total_fail"])

        # Sum invariant: per-agent scoped totals must add up to unfiltered total
        assert sum(scoped_totals) == unfiltered["total_validations"], \
            "sum of per-agent scoped totals != unfiltered total"
        assert sum(scoped_pass) == unfiltered["total_pass"], \
            "sum of per-agent scoped pass != unfiltered pass"
        assert sum(scoped_fail) == unfiltered["total_fail"], \
            "sum of per-agent scoped fail != unfiltered fail"

        # Imbalance guard: protects against uniform-average leak where every
        # scoped response returns unfiltered/N (sum invariant trivially holds
        # but individual values are all wrong).
        assert max(scoped_totals) > min(scoped_totals), \
            "all agents returned identical total_validations — possible uniform leak"


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


# ── v2.3.17 Cluster 5 — aggregator arithmetic invariants ───────────────

class TestCluster5AggregatorInvariants:
    """v2.3.17 Cluster 5 recurrence test (Queen's Standard pairing).

    A single high-leverage test class covering the family of bugs found by
    Persona B in the v2.3.16 outside review:

    - F-H / N-7: ``get_quality_metrics(contract=X)`` filter ignored on the
      proxy/REST path because ``/api/v1/stats`` did not accept a ``contract``
      query parameter and FastAPI silently dropped it.
    - F-K / N-8: ``top_failing_fields_by_agent`` collapsed field names to
      the literal string "?" — actually the in-memory hydration sentinel
      from the SQLite quality_stats aggregate (which does not store field
      names alongside rule names).
    - N-2: ``get_quality_trend(by=rule)`` returned ``data_confidence:
      "no_data"`` and ``pass_rate: 1.0`` per rule even when violations were
      present — caused by the per-rule rows not carrying ``total_records``
      so the route's ``sum(total_records)`` was 0, plus the
      ``QualityTrendPoint.pass_rate`` model defaulting to 1.0.

    Composes with the Grok imbalance-guard insight from CRT167: the seed
    fixture must intentionally imbalance per-contract / per-agent volumes
    so ``max(scoped_vals) > min(scoped_vals)`` is meaningful and uniform-
    average leaks cannot trivially satisfy the sum invariant.
    """

    def test_field_provenance_unavailable_for_synthesised_question_marks(self):
        """F-K / N-8: hydrated ``field="?"`` events surface as null + provenance flag.

        Synthesised error events from SQLite hydration carry "?" as the
        field name because the aggregate does not persist field names.
        The aggregator output must not echo "?" as if it were a real field
        name; it must emit ``field=null, field_provenance="unavailable"``.
        """
        vs = ValidationStats()
        # Simulate hydrated error events (field name lost in persistence)
        ts = time.time()
        vs._error_events.append((ts, "c1", "?", "rule_a", "agent-a"))
        vs._error_events.append((ts, "c1", "?", "rule_a", "agent-a"))
        vs._error_events.append((ts, "c1", "?", "rule_b", "agent-a"))
        # Plus a live error event with a real field name
        live_err = [{"field": "email", "rule": "email_format", "severity": "error"}]
        vs.record("c1", "ctx", False, 1, 0, 1.0, errors=live_err, agent_id="agent-a")

        summary = vs.get_summary()
        agent_a_entries = summary["top_failing_fields_by_agent"]["agent-a"]

        # No entry should have the literal "?" as field
        assert all(e["field"] != "?" for e in agent_a_entries), \
            f"top_failing_fields_by_agent leaked '?' as a field name: {agent_a_entries}"

        # Synthesised entries (field was "?") must surface as None + provenance
        unavailable_entries = [e for e in agent_a_entries if e.get("field_provenance") == "unavailable"]
        assert len(unavailable_entries) >= 1, \
            "synthesised ?-field events must emit field_provenance=unavailable"
        for e in unavailable_entries:
            assert e["field"] is None, \
                f"field_provenance=unavailable entry must have field=null, got: {e}"

        # Live entries (real field name) must NOT carry field_provenance
        live_entries = [e for e in agent_a_entries if e["field"] == "email"]
        assert len(live_entries) == 1
        assert "field_provenance" not in live_entries[0], \
            "live entries must not carry field_provenance"

    def test_rest_stats_contract_filter_scopes_response(self, client, auth_headers):
        """F-H / N-7: ``GET /api/v1/stats?contract=X`` scopes by_contract,
        top_failing_fields, dimensions.by_severity, totals to that contract only.

        Closes the proxy/REST drift where the proxy passed ``contract`` but
        the endpoint silently dropped it and returned the unfiltered summary.
        """
        from opendqv.monitoring import stats as global_stats

        # Inject deliberately imbalanced multi-contract traffic
        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats._error_events.clear()
        global_stats.field_errors.clear()

        err = [{"field": "amount", "rule": "amount_positive", "severity": "error"}]
        # contract X: 10 events, 7 pass / 3 fail
        for _ in range(7):
            global_stats.record("contract_x", "default", True, 0, 0, 1.0, agent_id="src-a")
        for _ in range(3):
            global_stats.record("contract_x", "default", False, 1, 0, 1.0, errors=err, agent_id="src-a")
        # contract Y: 5 events, 5 pass / 0 fail (different volume → max>min)
        for _ in range(5):
            global_stats.record("contract_y", "default", True, 0, 0, 1.0, agent_id="src-b")

        # Unfiltered: both contracts visible
        r_all = client.get("/api/v1/stats", headers=auth_headers)
        assert r_all.status_code == 200
        unfiltered = r_all.json()
        assert any(k.startswith("contract_x:") for k in unfiltered.get("by_contract", {}))
        assert any(k.startswith("contract_y:") for k in unfiltered.get("by_contract", {}))

        # Scoped to contract_x
        r_x = client.get("/api/v1/stats?contract=contract_x", headers=auth_headers)
        assert r_x.status_code == 200
        scoped = r_x.json()

        # by_contract scoped
        assert all(k.startswith("contract_x:") for k in scoped["by_contract"]), \
            f"by_contract leaked non-X keys: {list(scoped['by_contract'].keys())}"

        # top_failing_fields scoped
        assert all(f["contract"] == "contract_x" for f in scoped.get("top_failing_fields", []))

        # totals scoped to contract_x's slice (7 pass + 3 fail = 10)
        assert scoped["total_validations"] == 10
        assert scoped["total_pass"] == 7
        assert scoped["total_fail"] == 3

        # contract_filter echo for trace
        assert scoped.get("contract_filter") == "contract_x"

        # Imbalance guard: unfiltered total > scoped total
        # (proves we're filtering; uniform leak would have scoped == unfiltered)
        assert unfiltered["total_validations"] > scoped["total_validations"], \
            "scoped total equals unfiltered total — possible filter ignored"

    def test_quality_trend_by_rule_data_confidence_honest(self, client, auth_headers):
        """N-2: by=rule must report sane data_confidence when violations exist.

        Previously: ``data_confidence: "no_data"`` despite ``violation_count: 255``
        because per-rule rows did not carry ``total_records`` and the route
        summed zero. Fix: route fetches ``by=date`` to compute total_validations
        when the user asked for by=rule.
        """
        import opendqv.api.deps as _d

        # Seed quality_stats with a contract that has rule violations
        qs = _d._quality_stats
        # Ensure clean slate for this contract
        try:
            qs.delete_by_context("test_n2")
        except Exception:
            pass
        # Use a real bundled contract name so /quality-trend route resolves
        contract = "customer"
        qs.record_batch(contract, "v1", "test_n2", 100, 80, 20, {"email_format": 15, "age_range": 5})
        qs.record_batch(contract, "v1", "test_n2", 50, 45, 5, {"email_format": 5})

        try:
            r = client.get(
                f"/api/v1/contracts/{contract}/quality-trend?days=7&by=rule&context=test_n2",
                headers=auth_headers,
            )
            assert r.status_code == 200, r.text
            body = r.json()

            # Honest signals: violations exist, total_validations reflects underlying records
            assert body["total_validations"] >= 100, \
                f"by=rule total_validations should be aggregated from by=date, got {body['total_validations']}"
            assert body["data_confidence"] != "no_data", \
                f"by=rule data_confidence should not be 'no_data' when violations exist; got {body['data_confidence']}"

            # Per-rule points: pass_rate is None (not 1.0) because pass-rate is not
            # meaningful per rule — a rule has violations, not passes.
            for p in body["points"]:
                assert p.get("pass_rate") is None, \
                    f"by=rule point.pass_rate should be null, got {p.get('pass_rate')} on {p}"
                assert p.get("violation_count", 0) > 0, \
                    f"by=rule point should carry violation_count, got {p}"

            # Imbalance guard: rule violations are not uniform
            counts = [p["violation_count"] for p in body["points"]]
            if len(counts) > 1:
                assert max(counts) > min(counts), \
                    "all rule violation_counts identical — possible uniform-average leak"
        finally:
            qs.delete_by_context("test_n2")
