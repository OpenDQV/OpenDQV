"""
v2.3.23 P0-1 + P0-2 — auth/trust model doc honesty.

Persona B inside-view 2026-04-28:
  P0-1: list_audit_events description claims auth-gated to admin/auditor;
    in AUTH_MODE=open, every caller is admin (boot warning issued, but
    not visible in tool description).
  P0-2: get_quality_metrics + list_agents have no auth gate at all;
    document the single-tenant trust boundary explicitly per Persona B's
    multi-tenant concern.

Sonnet's pre-impl review (a348734a7798db94b) directed:
  - Doc-only fix in v2.3.23. Adding role gates to metrics/list_agents
    would break existing token-mode integrations using validator/reader
    tokens — that's a v2.4 architectural change alongside per-contract
    scoping.
  - Required wording standard: state what code does in EACH mode, not
    what it intends. Forbidden: "auth-gated" as a bare claim.
  - Update both mcp_server.py AND opendqv_mcp_proxy.py in lockstep
    (dual-path discipline).
  - Add auth_mode field to list_audit_events REST response (additive,
    non-breaking) for machine-readable trust-model evidence.
  - Recurrence test asserts the mode-conditional language appears in
    the description — same release, not deferred.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_proxy_module():
    """Same loader pattern as
    tests/test_v2_3_22_proxy_metrics_shape_parity.py."""
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


def _in_process_tools():
    """Return {tool_name: tool_dict} from in-process server.list_tools()."""
    import asyncio
    from opendqv.mcp_server import server
    from mcp.types import ListToolsRequest

    handlers = server.request_handlers
    result = asyncio.run(
        handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    )
    return {t.name: t for t in result.root.tools}


# ── P0-1: audit tool descriptions name AUTH_MODE behaviour explicitly ─

class TestAuditToolDescriptionsNameAuthModes:
    """The description string for list_audit_events and get_audit_event
    must enumerate behaviour in BOTH AUTH_MODE=token and AUTH_MODE=open
    so a reader of the MCP surface alone (no source access) can't be
    misled into thinking auth-gating is unconditional."""

    def test_list_audit_events_in_process_describes_both_modes(self):
        tools = _in_process_tools()
        desc = tools["list_audit_events"].description or ""
        assert "AUTH_MODE=token" in desc, (
            f"v2.3.23 P0-1: list_audit_events description must "
            f"explicitly state AUTH_MODE=token requires admin/auditor. "
            f"Reviewer's regulator-side reader can otherwise mis-state "
            f"capabilities. Got: {desc!r}"
        )
        assert "AUTH_MODE=open" in desc, (
            f"v2.3.23 P0-1: list_audit_events description must "
            f"explicitly call out AUTH_MODE=open behaviour (every caller "
            f"granted admin). Got: {desc!r}"
        )

    def test_get_audit_event_in_process_describes_both_modes(self):
        tools = _in_process_tools()
        desc = tools["get_audit_event"].description or ""
        assert "AUTH_MODE=token" in desc, desc
        assert "AUTH_MODE=open" in desc, desc

    def test_list_audit_events_proxy_describes_both_modes(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "list_audit_events")
        desc = tool.get("description", "")
        assert "AUTH_MODE=token" in desc, desc
        assert "AUTH_MODE=open" in desc, desc

    def test_get_audit_event_proxy_describes_both_modes(self, proxy_mod):
        tool = next((t for t in proxy_mod.TOOLS if t["name"] == "get_audit_event"), None)
        # Sonnet flagged P2-16: reviewer reported get_audit_event missing
        # from proxy. Verify it's present.
        assert tool is not None, (
            "v2.3.23 P2-16 (reviewer report): get_audit_event missing "
            "from proxy TOOLS list. Must be exposed on both MCP surfaces."
        )
        desc = tool.get("description", "")
        assert "AUTH_MODE=token" in desc, desc
        assert "AUTH_MODE=open" in desc, desc


# ── P0-2: metrics + list_agents trust-model statement ─────────────────

class TestMetricsListAgentsTrustModelStatement:
    """get_quality_metrics + list_agents have no role gate today.
    The descriptions must state the single-tenant trust boundary
    explicitly so a multi-tenant deployer doesn't assume isolation."""

    def test_metrics_in_process_states_single_tenant_assumption(self):
        tools = _in_process_tools()
        desc = tools["get_quality_metrics"].description or ""
        # Required: explicit "single-tenant" trust statement OR
        # equivalent (per-tenant scoping deferred to v2.4).
        assert "single-tenant" in desc.lower() or "tenant" in desc.lower(), (
            f"v2.3.23 P0-2: get_quality_metrics description must state "
            f"the single-tenant trust boundary. Multi-tenant deployers "
            f"otherwise assume isolation. Got: {desc!r}"
        )
        # Required: AUTH_MODE caveat parity with audit tools.
        assert "AUTH_MODE" in desc, (
            f"v2.3.23 P0-2: get_quality_metrics must state AUTH_MODE "
            f"behaviour. Reviewer in open mode read all data anonymously. "
            f"Got: {desc!r}"
        )

    def test_list_agents_in_process_states_single_tenant_assumption(self):
        tools = _in_process_tools()
        desc = tools["list_agents"].description or ""
        assert "single-tenant" in desc.lower() or "tenant" in desc.lower(), desc
        assert "AUTH_MODE" in desc, desc

    def test_metrics_proxy_states_single_tenant_assumption(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "get_quality_metrics")
        desc = tool.get("description", "")
        assert "single-tenant" in desc.lower() or "tenant" in desc.lower(), desc
        assert "AUTH_MODE" in desc, desc

    def test_list_agents_proxy_states_single_tenant_assumption(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "list_agents")
        desc = tool.get("description", "")
        assert "single-tenant" in desc.lower() or "tenant" in desc.lower(), desc
        assert "AUTH_MODE" in desc, desc


# ── auth_mode on list_audit_events response ───────────────────────────

class TestAuditEventResponseCarriesAuthMode:
    """Sonnet's directive: additive auth_mode field on list_audit_events
    response so a consuming system has machine-readable evidence of
    the trust model in effect at retrieval time."""

    def test_list_audit_events_response_carries_auth_mode(self, client):
        from opendqv.security.auth import create_pat
        admin_token = create_pat("audit-trust-test", role="admin")["token"]
        r = client.get(
            "/api/v1/audit/events?limit=1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "auth_mode" in body, (
            f"v2.3.23 P0-1: list_audit_events REST response must carry "
            f"auth_mode field for machine-readable trust evidence. "
            f"Sonnet's pre-impl directive (a348734a7798db94b). "
            f"Response keys: {list(body.keys())}"
        )
        assert body["auth_mode"] in ("token", "open"), body["auth_mode"]
