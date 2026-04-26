"""
tests/test_crt173_v2310_quick_wins.py — CRT173 v2.3.10 quick wins.

Pins five small fixes from the Persona B punch list:
  1. /validate/batch with empty records returns 400 with the
     "records must not be empty" detail (matches Cloud).
  2. uptime_seconds is an integer (not a float with microseconds).
  3. compare rule's suggested_fix template branches on cross-time
     vs cross-field (compare_to in {"today", "now"} → temporal hint).
  4. validate_batch MCP tool description mentions the ~70ms fixed
     setup cost.
  5. validate_record MCP tool description clarifies the
     caller_principal vs agent_id trust distinction.
"""
import pytest
from fastapi.testclient import TestClient

from opendqv.core.explainer import quick_fix


# 1 ──────────────────────────────────────────────────────────────────
class TestEmptyBatchRejected:

    def test_post_empty_batch_returns_400(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/validate/batch",
            json={"contract": "customer", "records": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "records must not be empty" in resp.json()["detail"]


# 2 ──────────────────────────────────────────────────────────────────
class TestUptimeIsInteger:

    def test_uptime_seconds_returned_as_int(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/stats", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "uptime_seconds" in body
        assert isinstance(body["uptime_seconds"], int), (
            f"uptime_seconds must be int, got {type(body['uptime_seconds']).__name__}"
        )


# 3 ──────────────────────────────────────────────────────────────────
class TestCompareSuggestedFixBranches:

    def test_cross_time_today_uses_temporal_template(self):
        fix = quick_fix("compare", compare_to="today")
        assert "today" in fix
        assert "cross-field" not in fix.lower()

    def test_cross_time_now_uses_temporal_template(self):
        fix = quick_fix("compare", compare_to="now")
        assert "now" in fix
        assert "cross-field" not in fix.lower()

    def test_cross_field_template_names_other_field(self):
        fix = quick_fix("compare", compare_to="other_field")
        assert "other_field" in fix

    def test_compare_with_no_compare_to_falls_back_generic(self):
        fix = quick_fix("compare")
        assert "comparison" in fix.lower()
        # Must not mistakenly say cross-field when we don't know
        assert "cross-field" not in fix


# 4 ──────────────────────────────────────────────────────────────────
class TestMcpToolDescriptions:
    """Pin the MCP tool description hints the persona asked for."""

    @pytest.mark.asyncio
    async def test_validate_batch_description_mentions_setup_cost(self):
        from opendqv.mcp_server import list_tools
        tools = await list_tools()
        vb = next(t for t in tools if t.name == "validate_batch")
        # Setup cost wording — gives operators a concrete decision rule.
        assert "70ms" in vb.description or "setup cost" in vb.description.lower()

    @pytest.mark.asyncio
    async def test_validate_batch_description_mentions_empty_rejection(self):
        from opendqv.mcp_server import list_tools
        tools = await list_tools()
        vb = next(t for t in tools if t.name == "validate_batch")
        assert "empty" in vb.description.lower()

    @pytest.mark.asyncio
    async def test_validate_record_description_clarifies_caller_principal(self):
        from opendqv.mcp_server import list_tools
        tools = await list_tools()
        vr = next(t for t in tools if t.name == "validate_record")
        # The trust distinction is the load-bearing point.
        assert "caller_principal" in vr.description
        assert "agent_id" in vr.description
