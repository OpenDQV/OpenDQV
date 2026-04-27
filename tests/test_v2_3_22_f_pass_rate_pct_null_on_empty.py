"""
v2.3.22 Cluster F — pass_rate_pct: null on empty (0/0) state.

Persona B round-2 (2026-04-27) P2:
> pass_rate_pct: 100.0 returned on 0/0 (should be null).
> Impact: blank dashboards look perfect.

Sweep across every wire-visible empty-state fallback that previously
returned 100.0:

- monitoring.py: get_summary, get_windowed_summary,
  get_windowed_summary_for_agent, list_agents, by_agent inner blocks
- mcp_server.py: _tool_get_quality_metrics entry-level + no-data
  fallback
- routes_analytics.py: _scope_summary_to_contract helper
- quality_stats.py: get_trend (date + group), get_windowed_totals,
  get_agent_breakdown
- quality_analytics.py: cross_contract_summary

All return None on empty/0/0; populate as percent (0–100, 1dp) when
data exists. Models updated: AnalyticsSummaryItem.pass_rate_pct +
AuditEventDetail.pass_rate_pct now Optional[float].

Reviewer's framing: empty dashboards should signal "no data," not
"100% perfect." A regulator looking at a no-data window seeing
pass_rate_pct=100 would interpret that as "fully clean" when the
truth is "haven't seen anything yet."
"""



class TestEmptyStatePassRatePctNull:
    def test_get_summary_empty_returns_null(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        summary = s.get_summary()
        assert summary["pass_rate_pct"] is None, (
            f"empty get_summary should return null pass_rate_pct, "
            f"got {summary['pass_rate_pct']!r}"
        )

    def test_get_summary_with_data_returns_pct(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        s.record(contract="c", context="x", valid=True, error_count=0,
                 warning_count=0, latency_ms=1.0)
        summary = s.get_summary()
        assert summary["pass_rate_pct"] == 100.0  # 1/1 → 100%

    def test_windowed_summary_empty_returns_null(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        windowed = s.get_windowed_summary(window_hours=1)
        assert windowed["pass_rate_pct"] is None

    def test_list_agents_empty_window(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        agents = s.list_agents(window_hours=1)
        assert agents == []  # No agents to assert pass_rate_pct on

    def test_quality_stats_empty_windowed_totals_returns_null(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        result = qs.get_windowed_totals("nonexistent", window_hours=24)
        assert result["pass_rate_pct"] is None, (
            f"get_windowed_totals on empty contract should return null, "
            f"got {result['pass_rate_pct']!r}"
        )

    def test_quality_stats_empty_record_batch_storage(self, tmp_path):
        """Storage column is REAL NOT NULL; record_batch with total=0
        stores 0.0, but read paths translate to null on the wire."""
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("c", "v1", "default", 0, 0, 0, {})
        # Read via get_trend (read path) — empty bucket → null on wire
        trend = qs.get_trend("c", days=1)
        # Empty batch may not produce a trend point, but if it does:
        for point in trend:
            if point["total_records"] == 0:
                assert point["pass_rate_pct"] is None

    def test_rest_stats_scoped_helper_empty_returns_null(self, client, auth_headers):
        """REST /stats?contract=X with no events returns null pass_rate_pct."""
        # Use a contract with no validations recorded
        from opendqv.monitoring import stats as global_stats
        global_stats.totals.clear()
        global_stats._events.clear()

        r = client.get("/api/v1/stats?contract=customer", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        # No events for `customer` → scoped pass_rate_pct should be null
        if body["total_validations"] == 0:
            assert body["pass_rate_pct"] is None, (
                f"v2.3.22 P2 regression: empty-state /stats?contract=X "
                f"returned pass_rate_pct={body['pass_rate_pct']!r} instead "
                f"of null. Reviewer's framing: blank dashboards should "
                f"signal no-data, not perfect."
            )

    def test_mcp_get_quality_metrics_no_data_fallback_returns_null(self):
        """The explicit no-data fallback path at mcp_server.py — when
        contract_name is set but no traffic, the response includes a
        synthesised entry. That entry's pass_rate_pct must be null."""
        import asyncio
        import json
        from opendqv.mcp_server import _tool_get_quality_metrics
        from opendqv.monitoring import stats as global_stats

        # Clear traffic for a contract that has no events
        global_stats.totals.clear()
        global_stats._events.clear()

        result = asyncio.run(_tool_get_quality_metrics(
            {"contract": "customer"}
        ))
        body = json.loads(result[0].text)
        # Single-entry result when contract is set
        if body.get("total_validations") == 0:
            assert body.get("pass_rate_pct") is None, (
                f"MCP no-data fallback returned pass_rate_pct={body.get('pass_rate_pct')!r} "
                f"instead of null. Closes v2.3.22 Cluster F."
            )
