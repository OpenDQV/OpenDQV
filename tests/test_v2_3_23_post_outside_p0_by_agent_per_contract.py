"""
v2.3.23 outside-review fix #1 — per-contract get_quality_metrics
entries must NOT inline the unscoped global by_agent blob.

Persona B 2026-04-28 outside review P0:
> "get_quality_metrics (all-contracts) by_agent block is wrong. The
>  exact same per-agent totals (broadsign-bauer-uk: 68/3/71, etc.)
>  are inlined into the response for banking_transaction, customer,
>  media_content, AND proof_of_play. The breakdown is not actually
>  scoped to the contract."

Regression I introduced in c4a0dbf (proxy reshape) AND mirrored in
the in-process MCP. Both paths fall back to inlining
summary["by_agent"] into every per-contract entry when no contract
filter is set. Result: each entry advertises the same global agent
rollup labelled as if it were that contract's breakdown.

Sonnet's pre-impl review (a74a3758ab3476042): drop the elif/if entirely
in both paths. The scoped get_agent_breakdown path (in-process only,
when ≤1 agent fall through) is correct; the leaking fallback is the
bug. Wrapping in {contracts:[], by_agent:{}} is a wire-shape break
not justified for a P0 hotfix; per-contract rollup in the proxy
needs DB access proxy doesn't have. Drop and defer.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_proxy_module():
    os.environ.setdefault("OPENDQV_API_URL", "http://127.0.0.1:1")
    os.environ.setdefault("OPENDQV_API_TOKEN", "")
    proxy_path = Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"
    spec = importlib.util.spec_from_file_location("opendqv_mcp_proxy", proxy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["opendqv_mcp_proxy"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def proxy_mod():
    return _load_proxy_module()


class TestProxyAllContractsBypAgentNotLeaked:
    def test_proxy_no_contract_filter_per_contract_entries_have_no_by_agent(self, proxy_mod):
        """The reviewer's exact reproduction: multi-contract by_contract,
        no contract filter, summary has by_agent. Each per-contract entry
        in the response must NOT carry the global by_agent blob."""
        summary = {
            "by_contract": {
                "customer:default": {"pass": 8, "fail": 2},
                "banking_transaction:default": {"pass": 5, "fail": 5},
                "proof_of_play:default": {"pass": 100, "fail": 1},
            },
            "top_failing_fields": [],
            "latency": {},
            "by_agent": {
                "broadsign-bauer-uk": {"pass": 68, "fail": 3, "total": 71, "pass_rate_pct": 95.8},
                "salesforce-prod": {"pass": 50, "fail": 0, "total": 50, "pass_rate_pct": 100.0},
            },
        }
        out = proxy_mod._reshape_quality_metrics(summary, "", 24)
        assert isinstance(out, list)
        assert len(out) == 3
        for entry in out:
            assert "by_agent" not in entry, (
                f"v2.3.23 outside-review P0: per-contract entry "
                f"{entry['contract']!r} must NOT inline the unscoped "
                f"global by_agent rollup. Reviewer caught the same "
                f"per-agent totals labelled as breakdown for every "
                f"contract. Got entry: {entry}"
            )

    def test_proxy_contract_filter_still_omits_by_agent(self, proxy_mod):
        """Already covered by Cluster A discipline. Regression guard."""
        summary = {
            "by_contract": {"customer:default": {"pass": 8, "fail": 2}},
            "top_failing_fields": [],
            "latency": {},
            "by_agent": {
                "broadsign-bauer-uk": {"pass": 68, "fail": 3, "total": 71, "pass_rate_pct": 95.8},
                "salesforce-prod": {"pass": 50, "fail": 0, "total": 50, "pass_rate_pct": 100.0},
            },
        }
        entry = proxy_mod._reshape_quality_metrics(summary, "customer", 24)
        assert isinstance(entry, dict)
        assert "by_agent" not in entry


class TestInProcessAllContractsByAgentNotLeaked:
    """Same defect in-process — `_tool_get_quality_metrics` had the
    same fallback elif. Reviewer hit it via proxy, but the in-process
    path falls into it too whenever get_agent_breakdown returns ≤1
    agent."""

    def test_in_process_no_contract_filter_omits_by_agent(self, monkeypatch):
        import asyncio
        import json
        from opendqv import mcp_server
        from opendqv.core.quality_stats import QualityStats
        from opendqv.monitoring import ValidationStats
        import opendqv.api.deps as deps_module

        # Force in-process branch (no remote client).
        monkeypatch.setattr(mcp_server, "_remote_client", None)

        # Seed multi-contract data with multiple agents.
        fresh_qs = QualityStats(":memory:")
        fresh_qs.record_batch(
            "customer", "1.0", "default",
            total=10, passed=9, failed=1,
            rule_failure_counts={"valid_email": 1},
            agent_id="customer-feed",
        )
        fresh_qs.record_batch(
            "banking_transaction", "1.0", "default",
            total=20, passed=18, failed=2,
            rule_failure_counts={"amount_min": 2},
            agent_id="banking-feed",
        )
        monkeypatch.setattr(deps_module, "_quality_stats", fresh_qs)
        monkeypatch.setattr(mcp_server, "_quality_stats", fresh_qs)

        fresh_stats = ValidationStats()
        fresh_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="customer-feed",
        )
        fresh_stats.record(
            contract="banking_transaction", context="default", valid=False,
            error_count=1, warning_count=0, latency_ms=2.0,
            agent_id="banking-feed",
        )
        monkeypatch.setattr(mcp_server, "_stats", fresh_stats)

        # Call with no contract filter — should fan out per-contract entries.
        result = asyncio.run(mcp_server._tool_get_quality_metrics({"window_hours": 24}))
        body = json.loads(result[0].text)
        assert isinstance(body, list)
        for entry in body:
            assert "by_agent" not in entry, (
                f"v2.3.23 outside-review P0: in-process MCP per-contract "
                f"entry must NOT inline the unscoped global by_agent. "
                f"Got entry: {entry}"
            )
