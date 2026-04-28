"""
v2.3.22 post-release inside-view finding B2 — REST `/api/v1/stats?contract=X`
leaks the unscoped global `by_agent` rollup.

Persona B (Data Platform Engineer) outside review on 2026-04-28 caught
the inconsistency:

    "I asked for metrics filtered to mifid_transaction_report, but the
     response shows zero records for the contract while the by_agent
     block lists OOH advertising agents (broadsign, jcdecaux, vistar)
     that have no connection to MiFID. The contract_filter is being
     acknowledged but the per-agent breakdown ignores it."

Tracing the helper at `opendqv/api/routes_analytics.py:67-133`
(`_scope_summary_to_contract`): scopes by_contract,
top_failing_fields, top_failing_fields_by_agent, recent_history,
dimensions.by_severity, and recomputes totals — but does NOT scope
`by_agent`. The unscoped global rollup leaks through the contract
filter.

Customer impact (regulator-facing): a dashboard scoped to one
contract shows agents that never touched it. Confidence-breaking
inconsistency. Same family as the v2.3.20 Cluster A by_agent leak
(which fixed the proxy-side parameter drop, not the server-side
scoping).

Fix shape: drop `by_agent` from the scoped response. Same discipline
as Cluster A on the proxy (omit rather than leak), and matches the
in-process MCP behaviour which already omits unscoped by_agent when
contract_name is set. v2.4 may re-introduce a contract-scoped
by_agent backed by per-contract event re-aggregation.
"""



class TestStatsContractScopesByAgent:
    """REST `/api/v1/stats?contract=X` must NOT carry the unscoped
    global by_agent rollup."""

    def test_contract_filter_omits_unscoped_by_agent(self, client, auth_headers):
        """Seed multi-contract multi-agent traffic. Filter to one
        contract. by_agent must be absent or empty — not leaking
        unrelated agents."""
        from opendqv.monitoring import stats as global_stats
        # Reset in-memory state so this test is isolated.
        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats.field_errors.clear()
        global_stats.severity_counts.clear()

        # Seed two contracts with distinct agents.
        global_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="hubspot-prod",
        )
        global_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="salesforce-prod",
        )
        global_stats.record(
            contract="banking_transaction", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="core-banking-feed",
        )

        # Filter to mifid_transaction_report — no traffic for it.
        r = client.get(
            "/api/v1/stats?contract=mifid_transaction_report&window_hours=24",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_validations"] == 0
        assert body["contract_filter"] == "mifid_transaction_report"
        # by_agent must not surface unrelated agents.
        by_agent = body.get("by_agent") or {}
        assert by_agent == {} or by_agent is None, (
            f"v2.3.22 post-release B2: /api/v1/stats?contract=X must "
            f"NOT leak the unscoped global by_agent. Got agents: "
            f"{list(by_agent.keys())} for contract with 0 traffic."
        )

    def test_contract_filter_with_traffic_omits_other_contract_agents(
        self, client, auth_headers
    ):
        """Filter to a contract that HAS traffic — by_agent must
        either carry only agents that touched THAT contract, OR be
        absent. It must NOT carry agents from other contracts."""
        from opendqv.monitoring import stats as global_stats
        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats.field_errors.clear()
        global_stats.severity_counts.clear()

        global_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="hubspot-prod",
        )
        global_stats.record(
            contract="banking_transaction", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="core-banking-feed",
        )

        r = client.get(
            "/api/v1/stats?contract=customer&window_hours=24",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Either by_agent is absent (cleanest) or contains only agents
        # known to have touched the customer contract.
        by_agent = body.get("by_agent") or {}
        if by_agent:
            forbidden = {"core-banking-feed"}
            leaked = forbidden & set(by_agent.keys())
            assert not leaked, (
                f"v2.3.22 post-release B2: /api/v1/stats?contract=customer "
                f"leaked agents from banking_transaction: {leaked}"
            )

    def test_unfiltered_stats_still_carries_by_agent(self, client, auth_headers):
        """Regression guard: when no contract filter is set, by_agent
        SHOULD surface the global rollup."""
        from opendqv.monitoring import stats as global_stats
        global_stats.totals.clear()
        global_stats._events.clear()
        global_stats.field_errors.clear()
        global_stats.severity_counts.clear()

        global_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="hubspot-prod",
        )
        global_stats.record(
            contract="banking_transaction", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="core-banking-feed",
        )

        r = client.get(
            "/api/v1/stats?window_hours=24",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # No contract filter → global by_agent rollup is correct shape.
        by_agent = body.get("by_agent") or {}
        assert "hubspot-prod" in by_agent
        assert "core-banking-feed" in by_agent
