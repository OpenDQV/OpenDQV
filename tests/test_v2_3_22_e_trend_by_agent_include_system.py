"""
v2.3.22 Cluster E — include_system on get_quality_trend(by=agent) +
get_agent_breakdown.

Persona B round-1 inside-view (2026-04-26) finding 2.2 verbatim:

> Reserved-prefix suppression has a hole — `/quality-trend?by=agent` leaks
> GET /api/v1/contracts/customer/quality-trend?by=agent&days=7
> → points include key="OpenDQV_SA_smoke_cursor_walk"
> The suppression contract claims "system traffic does not appear in
> customer-visible read surfaces unless include_system=true."
> Trend-by-agent breaks that contract.

The round-2 outside-only reviewer didn't probe `?by=agent` (their
MCP-only view doesn't show it easily), but the round-1 inside-view
called it as load-bearing. Sonnet's pre-impl review (agentId
a6ef2d4485bde2ad9) confirmed scope:

  - Default `include_system=False`. The contract was always
    "customer-visible surfaces don't show system agents." Trend was a
    defect from birth. No back-compat exemption — match list_agents
    and get_quality_metrics.
  - Filter in Python via `_is_system_agent` (single source of truth in
    monitoring.py). Not SQL. Avoids predicate drift.
  - get_agent_breakdown is the sibling leak. Same fix shape, same
    cluster — closes both surfaces in one pass.

Test seeds two agents on a known contract:
  - regular agent: "broadsign-prod" (visible)
  - system agent: "OpenDQV_SA_smoke_cursor_walk" (visible only with
    include_system=true)

Pre-fix: REST + MCP in-process leak the SA agent on by=agent (red).
       get_agent_breakdown leaks too (red).
Post-fix: SA agent suppressed by default; opt-in via include_system=true
         restores it on every surface.
"""

import asyncio
import json

import pytest

REGULAR_AGENT = "broadsign-prod"
SA_AGENT = "OpenDQV_SA_smoke_cursor_walk"


@pytest.fixture
def seeded_two_agents(monkeypatch):
    """In-memory QualityStats with one regular agent and one SA agent
    on the same contract. Same monkeypatch idiom as Cluster F + N-2."""
    from opendqv.core.quality_stats import QualityStats
    import opendqv.api.deps as deps_module
    import opendqv.mcp_server as mcp_module

    fresh = QualityStats(":memory:")
    fresh.record_batch(
        contract_name="customer", contract_version="1.0", context=None,
        total=100, passed=90, failed=10,
        rule_failure_counts={"valid_email": 5, "name_required": 3},
        agent_id=REGULAR_AGENT,
    )
    fresh.record_batch(
        contract_name="customer", contract_version="1.0", context=None,
        total=20, passed=20, failed=0,
        rule_failure_counts={},
        agent_id=SA_AGENT,
    )
    monkeypatch.setattr(deps_module, "_quality_stats", fresh)
    monkeypatch.setattr(mcp_module, "_quality_stats", fresh)
    yield fresh


class TestTrendByAgentSuppression:
    """REST + MCP in-process: by=agent must suppress OpenDQV_SA_*
    agents by default; include_system=true restores them."""

    def test_baseline_db_carries_both_agents(self, seeded_two_agents):
        """Sanity: persistence layer recorded both. Suppression must
        live above the SQL row, not at write time."""
        breakdown = seeded_two_agents.get_agent_breakdown(
            "customer", window_hours=24, include_system=True,
        )
        agents = {r["agent_id"] for r in breakdown}
        assert REGULAR_AGENT in agents
        assert SA_AGENT in agents

    def test_rest_by_agent_default_suppresses_sa(self, client, seeded_two_agents):
        r = client.get("/api/v1/contracts/customer/quality-trend?by=agent&days=1")
        assert r.status_code == 200, r.text
        keys = {p["key"] for p in r.json()["points"]}
        assert REGULAR_AGENT in keys
        assert SA_AGENT not in keys, (
            f"v2.3.22 Cluster E regression: REST /quality-trend?by=agent "
            f"leaked system agent {SA_AGENT!r}. The suppression contract "
            f"requires system traffic absent from customer-visible read "
            f"surfaces unless include_system=true. Got keys: {keys}"
        )

    def test_rest_by_agent_include_system_restores_sa(self, client, seeded_two_agents):
        r = client.get(
            "/api/v1/contracts/customer/quality-trend?by=agent&days=1&include_system=true"
        )
        assert r.status_code == 200, r.text
        keys = {p["key"] for p in r.json()["points"]}
        assert REGULAR_AGENT in keys
        assert SA_AGENT in keys, (
            f"include_system=true must restore system agents. Got keys: {keys}"
        )

    def test_mcp_in_process_by_agent_default_suppresses_sa(
        self, seeded_two_agents, monkeypatch
    ):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer", "by": "agent", "days": 1,
        }))
        body = json.loads(result[0].text)
        keys = {p["key"] for p in body["points"]}
        assert REGULAR_AGENT in keys
        assert SA_AGENT not in keys, (
            f"MCP in-process by=agent leaked {SA_AGENT!r}. Same suppression "
            f"contract as REST. Got: {keys}"
        )

    def test_mcp_in_process_by_agent_include_system_restores_sa(
        self, seeded_two_agents, monkeypatch
    ):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer", "by": "agent", "days": 1,
            "include_system": True,
        }))
        body = json.loads(result[0].text)
        keys = {p["key"] for p in body["points"]}
        assert SA_AGENT in keys, f"include_system=true must restore. Got: {keys}"

    def test_rest_and_mcp_agree_on_default_suppression(
        self, client, seeded_two_agents, monkeypatch
    ):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        rest = client.get("/api/v1/contracts/customer/quality-trend?by=agent&days=1").json()
        mcp_result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer", "by": "agent", "days": 1,
        }))
        mcp_body = json.loads(mcp_result[0].text)
        assert {p["key"] for p in rest["points"]} == {p["key"] for p in mcp_body["points"]}, (
            "REST and MCP in-process must produce the same agent set under "
            "the same default-suppression contract."
        )

    def test_other_by_dimensions_unaffected(self, client, seeded_two_agents):
        """by=date / by=context / by=rule do not surface agent_id; the
        SA agent's records must still contribute to those rollups so we
        don't silently drop validation history."""
        r_date = client.get("/api/v1/contracts/customer/quality-trend?by=date&days=1")
        assert r_date.status_code == 200
        # Both batches collapse into one daily bucket — SA records still count
        total = sum(p["total_records"] for p in r_date.json()["points"])
        assert total == 120, (
            f"by=date must include SA-agent records (120 = 100 regular + "
            f"20 SA). Got {total}. Suppression is on the agent surface, "
            f"not at the write boundary."
        )


class TestAgentBreakdownSuppression:
    """get_agent_breakdown is the sibling leak. Used by
    `_tool_get_quality_metrics` MCP path at mcp_server.py:1511 to
    populate `by_agent`. Same fix shape — same cluster."""

    def test_get_agent_breakdown_default_suppresses_sa(self, seeded_two_agents):
        breakdown = seeded_two_agents.get_agent_breakdown("customer", window_hours=24)
        agents = {r["agent_id"] for r in breakdown}
        assert REGULAR_AGENT in agents
        assert SA_AGENT not in agents, (
            f"get_agent_breakdown leaked {SA_AGENT!r}. This feeds "
            f"_tool_get_quality_metrics by_agent — leak surfaces on "
            f"every metrics call. Got: {agents}"
        )

    def test_get_agent_breakdown_include_system_true_restores_sa(
        self, seeded_two_agents
    ):
        breakdown = seeded_two_agents.get_agent_breakdown(
            "customer", window_hours=24, include_system=True,
        )
        agents = {r["agent_id"] for r in breakdown}
        assert SA_AGENT in agents

    def test_mcp_get_quality_metrics_by_agent_default_suppresses_sa(
        self, client, seeded_two_agents, monkeypatch
    ):
        """End-to-end: MCP get_quality_metrics' by_agent rollup
        must not surface system agents by default."""
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_metrics({
            "contract": "customer", "window_hours": 24,
        }))
        body = json.loads(result[0].text)
        by_agent = body.get("by_agent", {})
        if by_agent:
            assert SA_AGENT not in by_agent, (
                f"get_quality_metrics by_agent leaked {SA_AGENT!r}. "
                f"Got: {set(by_agent.keys())}"
            )
            assert REGULAR_AGENT in by_agent
