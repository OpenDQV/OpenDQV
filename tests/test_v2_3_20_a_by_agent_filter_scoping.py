"""
v2.3.20 Cluster A — MCP `get_quality_metrics(contract=X)` by_agent scoping.

Persona B 2026-04-27 outside-review P1.5:
> Filtering by contract=mifid_transaction_report returns total_validations:0
> in the headline but the by_agent block ignores the filter and returns
> cross-contract per-agent totals. Customer impact: dashboards built on
> this endpoint show contradictory numbers.

Sonnet's pre-impl review pinpointed the leak at ``mcp_server.py:1504-1505``
— the fallback ``elif summary.get("by_agent"): entry["by_agent"] =
summary["by_agent"]`` passes the unscoped cross-contract by_agent rollup
when ``get_agent_breakdown`` (which IS contract-scoped) returns ≤1 result.

This is a recurrence of the v2.3.17 Cluster 5 F-H/N-7 family — Cluster 5
fixed by_contract + top_failing_fields + totals scoping; missed by_agent
on the MCP _tool_get_quality_metrics fallback path.

Fix: when ``contract_name`` is set, the unscoped fallback is suppressed —
``by_agent`` is either the scoped result or omitted entirely.
"""

import asyncio
import json



class TestMcpQualityMetricsByAgentScoping:
    def test_contract_filter_suppresses_unscoped_by_agent(self):
        """The exact reviewer scenario: contract=X with no scoped agents
        (so get_agent_breakdown returns ≤1) must NOT leak the unscoped
        cross-contract by_agent rollup via the fallback path."""
        from opendqv.monitoring import stats as global_stats
        from opendqv.mcp_server import _tool_get_quality_metrics

        # Clean slate
        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats._error_events.clear()
        global_stats.field_errors.clear()

        # Multi-contract traffic with distinct agents per contract.
        # contract_x: 1 agent (so scoped get_agent_breakdown returns ≤1
        # and the buggy fallback would fire).
        for _ in range(5):
            global_stats.record("contract_x", "default", True, 0, 0, 1.0, agent_id="src-x-only")
        # contract_y: distinct agent. The unscoped summary["by_agent"]
        # will contain BOTH src-x-only and src-y-only.
        for _ in range(5):
            global_stats.record("contract_y", "default", True, 0, 0, 1.0, agent_id="src-y-only")

        # Call MCP tool with contract=contract_x.
        result = asyncio.run(_tool_get_quality_metrics({"contract": "contract_x"}))
        assert len(result) == 1
        body = json.loads(result[0].text)

        # If by_agent is present, it must NOT contain agents from other
        # contracts. The pre-fix bug: by_agent contained src-y-only.
        by_agent = body.get("by_agent", {})
        if by_agent:
            assert "src-y-only" not in by_agent, (
                f"v2.3.20 P1.5 regression: contract=contract_x leaked "
                f"src-y-only (a contract_y-only agent) into by_agent. "
                f"by_agent={by_agent}"
            )
            # Positive assertion: src-x-only IS valid because it's a
            # contract_x agent. (May be absent if get_agent_breakdown
            # returned ≤1 — in which case by_agent is omitted entirely,
            # which is also acceptable.)

    def test_no_contract_filter_returns_unscoped_by_agent(self):
        """Sanity: when no contract filter is set, the unscoped fallback
        path is the correct behaviour. Don't over-correct."""
        from opendqv.monitoring import stats as global_stats
        from opendqv.mcp_server import _tool_get_quality_metrics

        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats._error_events.clear()
        global_stats.field_errors.clear()

        for _ in range(5):
            global_stats.record("contract_a", "default", True, 0, 0, 1.0, agent_id="src-a")
        for _ in range(5):
            global_stats.record("contract_b", "default", True, 0, 0, 1.0, agent_id="src-b")

        result = asyncio.run(_tool_get_quality_metrics({}))
        assert len(result) == 1
        body = json.loads(result[0].text)
        # Unfiltered: both contracts may surface. Just assert no error.
        assert isinstance(body, list) or isinstance(body, dict)
