"""
tests/test_crt173_v2313_observability.py — CRT173 v2.3.13.

Pins the observability surface added in v2.3.13:

  Item 14: get_summary() emits total_error_violations / total_warning_violations
           alongside the legacy total_errors / total_warnings keys. The new keys
           name what the math is — *rule-violation sums*, not record counts.

  Item 15: warning-severity aggregation actually counts warnings. A contract
           rule with severity=warning, when failed, must increment
           total_warning_violations and dimensions.by_severity.warning, and
           must NOT increment total_error_violations.

  Item 18: latency stats expose p99_9_ms and max_ms (in addition to p50/p95/p99).

  Item 16: GET /api/v1/agents lists known agents in the window so MCP clients
           can discover values to feed back into agent_id filters.

  Item 17: GET /contracts/{name}/quality-trend?by=date|agent|context|rule —
           the same underlying data regrouped along whichever dimension the
           caller wants to diagnose.

  Item 19: top_failing_rules_ranked is an array of {rule, count} sorted desc,
           emitted alongside the legacy dict form. JSON dicts have no inherent
           ordering and key collisions silently overwrite — the ranked array
           is the canonical surface; the dict is deprecated for v2.4 removal.
"""

from opendqv.core.quality_stats import QualityStats
from opendqv.monitoring import ValidationStats


def _stats_with_one_warning():
    """Record one validation that triggered a single warning-severity rule."""
    s = ValidationStats()
    s.record(
        contract="demo",
        context="none",
        valid=True,  # warnings don't invalidate
        error_count=0,
        warning_count=1,
        latency_ms=1.2,
        errors=[{
            "field": "f", "rule": "r", "message": "warn",
            "severity": "warning", "error_code": "OPENDQV_WARN_001",
        }],
    )
    return s


def _stats_with_one_error():
    s = ValidationStats()
    s.record(
        contract="demo",
        context="none",
        valid=False,
        error_count=1,
        warning_count=0,
        latency_ms=1.2,
        errors=[{
            "field": "f", "rule": "r", "message": "err",
            "severity": "error", "error_code": "OPENDQV_ERR_001",
        }],
    )
    return s


# Item 14 ───────────────────────────────────────────────────────────
class TestViolationsRename:

    def test_summary_emits_violations_keys_alongside_legacy(self):
        summary = _stats_with_one_warning().get_summary()
        assert "total_error_violations" in summary
        assert "total_warning_violations" in summary
        assert summary["total_warning_violations"] == 1
        assert summary["total_error_violations"] == 0

    def test_legacy_aliases_match_violations_values(self):
        summary = _stats_with_one_warning().get_summary()
        assert summary["total_errors"] == summary["total_error_violations"]
        assert summary["total_warnings"] == summary["total_warning_violations"]

    def test_total_fail_is_record_count_not_violation_count(self):
        s = ValidationStats()
        s.record(
            contract="demo", context="none", valid=False,
            error_count=3, warning_count=0, latency_ms=1.0,
            errors=[
                {"field": "a", "rule": "r1", "message": "x", "severity": "error", "error_code": "E1"},
                {"field": "b", "rule": "r2", "message": "x", "severity": "error", "error_code": "E2"},
                {"field": "c", "rule": "r3", "message": "x", "severity": "error", "error_code": "E3"},
            ],
        )
        summary = s.get_summary()
        assert summary["total_fail"] == 1
        assert summary["total_error_violations"] == 3


# Item 15 ───────────────────────────────────────────────────────────
class TestWarningAggregation:

    def test_warning_increments_warning_violations_only(self):
        summary = _stats_with_one_warning().get_summary()
        assert summary["total_warning_violations"] == 1
        assert summary["total_error_violations"] == 0

    def test_warning_appears_in_by_severity(self):
        summary = _stats_with_one_warning().get_summary()
        assert summary["dimensions"]["by_severity"]["warning"] == 1
        assert summary["dimensions"]["by_severity"]["error"] == 0

    def test_error_does_not_leak_into_warning_bucket(self):
        summary = _stats_with_one_error().get_summary()
        assert summary["total_error_violations"] == 1
        assert summary["total_warning_violations"] == 0
        assert summary["dimensions"]["by_severity"]["error"] == 1
        assert summary["dimensions"]["by_severity"]["warning"] == 0


# Item 16 ───────────────────────────────────────────────────────────
class TestListAgentsEndpoint:

    def test_list_agents_method_returns_unique_agents_with_volume(self):
        s = ValidationStats()
        for _ in range(3):
            s.record(contract="c", context="none", valid=True,
                     error_count=0, warning_count=0, latency_ms=1.0,
                     agent_id="alpha")
        for _ in range(2):
            s.record(contract="c", context="none", valid=False,
                     error_count=1, warning_count=0, latency_ms=1.0,
                     errors=[{"field": "f", "rule": "r", "message": "x",
                              "severity": "error", "error_code": "E"}],
                     agent_id="beta")
        agents = s.list_agents(window_hours=24)
        assert len(agents) == 2
        assert agents[0]["agent_id"] == "alpha"
        assert agents[0]["total_validations"] == 3
        assert agents[0]["total_pass"] == 3
        assert agents[1]["agent_id"] == "beta"
        assert agents[1]["total_validations"] == 2
        assert agents[1]["total_fail"] == 2
        for a in agents:
            assert "last_seen" in a
            assert "pass_rate_pct" in a

    def test_list_agents_excludes_empty_agent_id(self):
        s = ValidationStats()
        s.record(contract="c", context="none", valid=True,
                 error_count=0, warning_count=0, latency_ms=1.0,
                 agent_id="")
        agents = s.list_agents(window_hours=24)
        assert agents == []

    def test_agents_endpoint_returns_window_and_list(self, client, auth_headers):
        resp = client.get("/api/v1/agents?window_hours=24", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["window_hours"] == 24
        assert isinstance(body["agents"], list)


# Item 17 ───────────────────────────────────────────────────────────
class TestMultiDimensionalTrend:

    def _seed(self, tmp_path):
        db = tmp_path / "qs.db"
        qs = QualityStats(db_path=str(db))
        qs.record_batch(
            contract_name="t", contract_version="1", context="prod",
            total=10, passed=8, failed=2,
            rule_failure_counts={"r_a": 2}, agent_id="alpha",
        )
        qs.record_batch(
            contract_name="t", contract_version="1", context="staging",
            total=20, passed=15, failed=5,
            rule_failure_counts={"r_a": 3, "r_b": 2}, agent_id="beta",
        )
        return qs

    def test_by_agent_groups_by_agent_id(self, tmp_path):
        qs = self._seed(tmp_path)
        result = qs.get_trend("t", days=1, by="agent")
        keys = {p["key"] for p in result}
        assert keys == {"alpha", "beta"}
        beta = next(p for p in result if p["key"] == "beta")
        assert beta["total_records"] == 20
        # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp). 15/20 → 75.0
        assert beta["pass_rate_pct"] == 75.0

    def test_by_context_groups_by_context(self, tmp_path):
        qs = self._seed(tmp_path)
        result = qs.get_trend("t", days=1, by="context")
        keys = {p["key"] for p in result}
        assert keys == {"prod", "staging"}

    def test_by_rule_groups_and_ranks(self, tmp_path):
        qs = self._seed(tmp_path)
        result = qs.get_trend("t", days=1, by="rule")
        assert result[0]["key"] == "r_a"
        assert result[0]["violation_count"] == 5
        assert result[1]["key"] == "r_b"
        assert result[1]["violation_count"] == 2

    def test_by_date_preserves_legacy_shape(self, tmp_path):
        qs = self._seed(tmp_path)
        result = qs.get_trend("t", days=1, by="date")
        assert result, "expected at least one daily bucket"
        assert "date" in result[0]
        assert "key" not in result[0]

    def test_invalid_by_rejected(self, tmp_path):
        qs = self._seed(tmp_path)
        try:
            qs.get_trend("t", days=1, by="bogus")
        except ValueError:
            return
        assert False, "expected ValueError for unknown by dimension"


# Item 18 ───────────────────────────────────────────────────────────
class TestLatencyTailPercentiles:

    def test_summary_latency_has_p99_9_and_max(self):
        s = ValidationStats()
        for i in range(200):
            s.record(
                contract="demo", context="none", valid=True,
                error_count=0, warning_count=0, latency_ms=float(i),
            )
        latency = s.get_summary()["latency"]
        assert "p99_9_ms" in latency
        assert "max_ms" in latency
        assert latency["max_ms"] >= latency["p99_ms"]
        assert latency["p99_9_ms"] >= latency["p99_ms"]


# Item 19 ───────────────────────────────────────────────────────────
class TestTopFailingRulesArrayShape:

    def _seed_quality_stats(self, tmp_path):
        db = tmp_path / "qs.db"
        qs = QualityStats(db_path=str(db))
        qs.record_batch(
            contract_name="t", contract_version="1", context=None,
            total=10, passed=7, failed=3,
            rule_failure_counts={"rule_a": 5, "rule_b": 2, "rule_c": 1},
        )
        return qs

    def test_quality_trend_emits_ranked_array(self, tmp_path):
        qs = self._seed_quality_stats(tmp_path)
        trend = qs.get_trend("t", days=1)
        assert trend, "fixture must produce one trend point"
        point = trend[0]
        assert "top_failing_rules_ranked" in point
        ranked = point["top_failing_rules_ranked"]
        assert isinstance(ranked, list)
        assert ranked[0] == {"rule": "rule_a", "count": 5}
        assert ranked[1]["count"] >= ranked[2]["count"]

    def test_legacy_dict_still_present_for_wire_compat(self, tmp_path):
        qs = self._seed_quality_stats(tmp_path)
        trend = qs.get_trend("t", days=1)
        assert "top_failing_rules" in trend[0]
        assert isinstance(trend[0]["top_failing_rules"], dict)

    def test_windowed_totals_emits_ranked_array(self, tmp_path):
        qs = self._seed_quality_stats(tmp_path)
        totals = qs.get_windowed_totals("t", window_hours=24)
        assert "top_failing_rules_ranked" in totals
        assert isinstance(totals["top_failing_rules_ranked"], list)
        assert isinstance(totals["top_failing_rules"], dict)
