"""
v2.3.22 Cluster A — proxy-path parameter forwarding integration test.

Persona B round-2 (2026-04-27) hit the same P1.5 finding the round-1
report named: ``get_quality_metrics(contract=X)`` returns
mathematically-impossible by_agent counts (broadsign-bauer-uk: 402
under a contract whose total is 78). The reviewer was on the proxy
path; v2.3.17 Cluster 5 + v2.3.20 Cluster A both shipped fixes that
only protected the in-process branch.

Sonnet's pre-impl review for v2.3.22 Cluster A confirmed the actual
bug: ``_tool_get_quality_metrics`` proxy path at
``mcp_server.py:1399`` constructed ``params`` with only
``window_hours`` and ``include_system``. ``contract_name``,
``agent_id_filter``, AND ``window_hours=0`` were silently dropped.
So when the proxy fetched ``/api/v1/stats`` for a contract-filtered
request, the URL had no ``?contract=X``, the server-side
``_scope_summary_to_contract`` helper never ran, and the response
carried unscoped cross-contract ``by_agent``.

This file closes the structural test-suite blind spot: the existing
Cluster 5 / Cluster A invariants exercise the in-process path only.
The proxy path (``_remote_client`` truthy) is a separate code branch
with separate failure modes — it now has its own test surface.

Test boundary (Sonnet's recommendation): mock ``_remote_client``
directly. The bug is "wrong params on the outbound HTTP call"; a
captured-params assertion is the precise oracle. FastAPI TestClient
adds nothing here — that would test the server-side scoping helper,
which is a separate (already-covered) concern.
"""

import asyncio
from unittest.mock import MagicMock

import pytest


def _stub_summary():
    """A REST `/stats` response shape sufficient for `_tool_get_quality_metrics`
    to walk through without raising. Empty content; we're testing the
    outbound call, not the response handling."""
    return {
        "by_contract": {},
        "total_validations": 0,
        "total_pass": 0,
        "total_fail": 0,
        "pass_rate_pct": 100.0,
        "total_error_violations": 0,
        "total_warning_violations": 0,
        "total_errors": 0,
        "total_warnings": 0,
        "uptime_seconds": 1,
        "top_failing_fields": [],
        "top_failing_fields_by_agent": {},
        "recent_history": [],
        "latency": {"avg_ms": None, "sample_size": 0},
        "dimensions": {"by_severity": {"error": 0, "warning": 0}},
        "governance": {"draft_count": 0, "active_count": 41, "review_count": 0},
        "include_system": False,
    }


class _CapturingClient:
    """Minimal mock that captures the most recent .get() call's params
    and returns the stub summary."""
    def __init__(self):
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None):
        self.last_url = url
        self.last_params = dict(params or {})
        resp = MagicMock()
        resp.json.return_value = _stub_summary()
        resp.raise_for_status.return_value = None
        return resp


@pytest.fixture
def proxy_client(monkeypatch):
    """Install a capturing _remote_client for the duration of the test."""
    from opendqv import mcp_server
    client = _CapturingClient()
    monkeypatch.setattr(mcp_server, "_remote_client", client)
    yield client


class TestProxyPathParamForwarding:
    """v2.3.22 Cluster A: the proxy path's outbound call to /api/v1/stats
    must forward contract, agent_id, window_hours (including 0), and
    include_system. Closes the structural test-suite blind spot that
    let the same regression slip through in v2.3.17 + v2.3.20."""

    def test_contract_param_forwarded(self, proxy_client):
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({
            "contract": "mifid_transaction_report",
            "window_hours": 24,
        }))
        assert proxy_client.last_url == "/api/v1/stats"
        assert proxy_client.last_params.get("contract") == "mifid_transaction_report", (
            f"v2.3.22 P1.5 regression: contract NOT forwarded to /api/v1/stats. "
            f"params={proxy_client.last_params}. Server-side _scope_summary_to_contract "
            f"can't run without ?contract= in the URL."
        )
        assert proxy_client.last_params.get("window_hours") == 24

    def test_agent_id_param_forwarded(self, proxy_client):
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({
            "contract": "customer",
            "agent_id": "salesforce-prod-eu",
            "window_hours": 24,
        }))
        assert proxy_client.last_params.get("agent_id") == "salesforce-prod-eu", (
            f"v2.3.22 P1.5 regression: agent_id NOT forwarded to /api/v1/stats "
            f"on the proxy path. params={proxy_client.last_params}. The in-process "
            f"path uses agent_id_filter to call get_windowed_summary_for_agent; "
            f"the proxy must send it on the URL so the server applies the same scope."
        )

    def test_window_hours_zero_forwarded_not_dropped(self, proxy_client):
        """The pre-fix code used `if window_hours else {}` — a truthiness
        check that drops window_hours=0. Now uses `is not None`."""
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({
            "contract": "customer",
            "window_hours": 0,
        }))
        assert "window_hours" in proxy_client.last_params, (
            "window_hours=0 (falsy but valid) was silently dropped on the "
            "proxy path. Pre-fix code used `if window_hours else {}` which "
            "treats 0 as missing. Sonnet flagged this edge case; the fix "
            "uses `is not None`."
        )
        assert proxy_client.last_params["window_hours"] == 0

    def test_include_system_forwarded(self, proxy_client):
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({
            "contract": "customer",
            "include_system": True,
        }))
        assert proxy_client.last_params.get("include_system") == "true"

    def test_no_extraneous_params_when_minimal_call(self, proxy_client):
        """Sanity: with no contract / no agent / no include_system, the
        only param forwarded is window_hours. Don't leak invented keys."""
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({}))
        # Default window_hours=24 from args.get() default
        assert set(proxy_client.last_params.keys()) <= {"window_hours"}, (
            f"unexpected params on minimal call: {proxy_client.last_params}"
        )

    def test_all_four_params_forwarded_together(self, proxy_client):
        """End-to-end happy-path: all four supported params present and
        forwarded correctly in the same outbound call."""
        from opendqv.mcp_server import _tool_get_quality_metrics
        asyncio.run(_tool_get_quality_metrics({
            "contract": "mifid_transaction_report",
            "agent_id": "broadsign-bauer-uk",
            "window_hours": 168,
            "include_system": True,
        }))
        params = proxy_client.last_params
        assert params.get("contract") == "mifid_transaction_report"
        assert params.get("agent_id") == "broadsign-bauer-uk"
        assert params.get("window_hours") == 168
        assert params.get("include_system") == "true"
