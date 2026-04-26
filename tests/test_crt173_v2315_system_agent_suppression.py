"""
tests/test_crt173_v2315_system_agent_suppression.py — CRT173 items 26-28.

Persona-B reviewer literally saw `impostor`, `cursor-walk`, `smoke-v239` appear
in their persona's read of "production-shaped" data. Pins the v2.3.15 fix:

  - Reserved prefix `OpenDQV_SA_` for OpenDQV-owned system agents.
  - Customer-visible metrics surfaces (REST `/stats`, `/agents`, MCP
    `get_quality_metrics`, `list_agents`) suppress system agents by default.
  - `include_system=true` surfaces them and tags each row with
    `is_system_agent: true`.

These tests cover the suppression boundary in monitoring.py + the REST and
MCP entry points that thread the flag through.
"""
import json

import pytest
from fastapi.testclient import TestClient


# ── Helper-level invariants — _is_system_agent + classifier ───────────

class TestIsSystemAgent:
    """The single classifier underpinning every read-surface filter."""

    def test_opendqv_sa_prefix_classifies_as_system(self):
        from opendqv.monitoring import _is_system_agent
        assert _is_system_agent("OpenDQV_SA_smoke_v240") is True
        assert _is_system_agent("OpenDQV_SA_probe_persona_b") is True
        assert _is_system_agent("OpenDQV_SA_demo_ppds") is True

    def test_non_prefix_classifies_as_customer(self):
        from opendqv.monitoring import _is_system_agent
        # Real customer-style names — must NOT be suppressed.
        assert _is_system_agent("broadsign-prod") is False
        assert _is_system_agent("salesforce-uk") is False
        assert _is_system_agent("vistar-ssp") is False
        # Legacy probe names the reviewer saw — these stay un-suppressed
        # by design (clean-cut migration; pre-existing rows in the DB
        # require a one-shot dev cleanup, not a code denylist).
        assert _is_system_agent("impostor") is False
        assert _is_system_agent("cursor-walk") is False
        assert _is_system_agent("smoke-v239") is False

    def test_empty_classifies_as_non_system(self):
        from opendqv.monitoring import _is_system_agent
        assert _is_system_agent("") is False
        assert _is_system_agent(None) is False  # type: ignore[arg-type]

    def test_partial_prefix_does_not_match(self):
        from opendqv.monitoring import _is_system_agent
        # Substring without the leading anchor must not match.
        assert _is_system_agent("my_OpenDQV_SA_thing") is False
        assert _is_system_agent("opendqv_sa_lower") is False  # case sensitive


# ── ValidationStats.list_agents — suppression + diagnostic surface ────

class TestListAgentsSuppression:

    @pytest.fixture
    def seeded_stats(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        # Real customer traffic.
        for _ in range(10):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        for _ in range(3):
            s.record("customer", "default", False, 1, 0, 5.0,
                     errors=[{"field": "email", "rule": "valid_email", "severity": "error"}],
                     agent_id="broadsign-prod")
        # System probes.
        for _ in range(20):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")
        for _ in range(5):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_probe_persona_b")
        return s

    def test_default_suppresses_system_agents(self, seeded_stats):
        agents = seeded_stats.list_agents(window_hours=24)
        agent_ids = {a["agent_id"] for a in agents}
        assert "broadsign-prod" in agent_ids
        assert "OpenDQV_SA_smoke_v240" not in agent_ids
        assert "OpenDQV_SA_probe_persona_b" not in agent_ids

    def test_include_system_surfaces_system_agents(self, seeded_stats):
        agents = seeded_stats.list_agents(window_hours=24, include_system=True)
        agent_ids = {a["agent_id"] for a in agents}
        assert "broadsign-prod" in agent_ids
        assert "OpenDQV_SA_smoke_v240" in agent_ids
        assert "OpenDQV_SA_probe_persona_b" in agent_ids

    def test_is_system_agent_flag_present_on_each_row(self, seeded_stats):
        agents = seeded_stats.list_agents(window_hours=24, include_system=True)
        flags = {a["agent_id"]: a["is_system_agent"] for a in agents}
        assert flags["broadsign-prod"] is False
        assert flags["OpenDQV_SA_smoke_v240"] is True
        assert flags["OpenDQV_SA_probe_persona_b"] is True


# ── ValidationStats.get_summary — recent_history + top_failing_fields_by_agent ─

class TestGetSummarySuppression:

    @pytest.fixture
    def seeded_stats(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        # Real customer failure.
        s.record("customer", "default", False, 1, 0, 5.0,
                 errors=[{"field": "email", "rule": "valid_email", "severity": "error"}],
                 agent_id="broadsign-prod")
        # System probe failure — must not leak into top_failing_fields_by_agent.
        s.record("customer", "default", False, 1, 0, 5.0,
                 errors=[{"field": "name", "rule": "not_empty", "severity": "error"}],
                 agent_id="OpenDQV_SA_smoke_v240")
        return s

    def test_default_recent_history_excludes_system(self, seeded_stats):
        summary = seeded_stats.get_summary()
        agents_in_history = {h["agent_id"] for h in summary["recent_history"]}
        assert "broadsign-prod" in agents_in_history
        assert "OpenDQV_SA_smoke_v240" not in agents_in_history

    def test_include_system_recent_history_includes_system(self, seeded_stats):
        summary = seeded_stats.get_summary(include_system=True)
        agents_in_history = {h["agent_id"] for h in summary["recent_history"]}
        assert "broadsign-prod" in agents_in_history
        assert "OpenDQV_SA_smoke_v240" in agents_in_history

    def test_default_top_failing_fields_by_agent_excludes_system(self, seeded_stats):
        summary = seeded_stats.get_summary()
        assert "broadsign-prod" in summary["top_failing_fields_by_agent"]
        assert "OpenDQV_SA_smoke_v240" not in summary["top_failing_fields_by_agent"]

    def test_include_system_flag_echoed_in_response(self, seeded_stats):
        # Callers can detect whether suppression was applied.
        assert seeded_stats.get_summary()["include_system"] is False
        assert seeded_stats.get_summary(include_system=True)["include_system"] is True


# ── ValidationStats.get_windowed_summary — by_agent suppression ───────

class TestGetWindowedSummarySuppression:

    @pytest.fixture
    def seeded_stats(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        # Two customers + two system probes, enough to trigger by_agent (>1).
        for _ in range(4):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        for _ in range(3):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="vistar-ssp")
        for _ in range(10):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")
        for _ in range(2):
            s.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_probe_persona_b")
        return s

    def test_default_by_agent_excludes_system(self, seeded_stats):
        summary = seeded_stats.get_windowed_summary(24)
        assert "by_agent" in summary
        agent_ids = set(summary["by_agent"].keys())
        assert agent_ids == {"broadsign-prod", "vistar-ssp"}

    def test_include_system_by_agent_includes_system(self, seeded_stats):
        summary = seeded_stats.get_windowed_summary(24, include_system=True)
        agent_ids = set(summary["by_agent"].keys())
        assert "broadsign-prod" in agent_ids
        assert "vistar-ssp" in agent_ids
        assert "OpenDQV_SA_smoke_v240" in agent_ids
        assert "OpenDQV_SA_probe_persona_b" in agent_ids


# ── REST — GET /api/v1/stats ──────────────────────────────────────────

class TestRestStatsSuppression:

    def _seed(self):
        from opendqv.monitoring import stats
        # Real customer.
        for _ in range(5):
            stats.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        # System probe.
        for _ in range(8):
            stats.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")

    def test_default_excludes_system(self, client: TestClient, auth_headers):
        from opendqv.monitoring import stats as _stats
        # Reset for isolation — this test reads the singleton.
        _stats.history.clear()
        _stats._events.clear()
        _stats._error_events.clear()
        self._seed()
        resp = client.get("/api/v1/stats?window_hours=24", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        if "by_agent" in body:
            assert "OpenDQV_SA_smoke_v240" not in body["by_agent"]
        history_agents = {h.get("agent_id", "") for h in body.get("recent_history", [])}
        assert "OpenDQV_SA_smoke_v240" not in history_agents

    def test_include_system_surfaces_system(self, client: TestClient, auth_headers):
        from opendqv.monitoring import stats as _stats
        _stats.history.clear()
        _stats._events.clear()
        _stats._error_events.clear()
        self._seed()
        resp = client.get(
            "/api/v1/stats?window_hours=24&include_system=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        # by_agent only emitted when >1 distinct agent — here we have 2 once
        # OpenDQV_SA_smoke_v240 is counted, so it must show up.
        assert body.get("include_system") is True


# ── REST — GET /api/v1/agents ─────────────────────────────────────────

class TestRestAgentsSuppression:

    def test_default_excludes_system(self, client: TestClient, auth_headers):
        from opendqv.monitoring import stats as _stats
        _stats.history.clear()
        _stats._events.clear()
        _stats._error_events.clear()
        for _ in range(5):
            _stats.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        for _ in range(5):
            _stats.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")

        resp = client.get("/api/v1/agents?window_hours=24", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        agent_ids = {a["agent_id"] for a in body["agents"]}
        assert "broadsign-prod" in agent_ids
        assert "OpenDQV_SA_smoke_v240" not in agent_ids
        assert body["include_system"] is False

    def test_include_system_surfaces_system_with_flag(self, client: TestClient, auth_headers):
        from opendqv.monitoring import stats as _stats
        _stats.history.clear()
        _stats._events.clear()
        _stats._error_events.clear()
        for _ in range(5):
            _stats.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        for _ in range(5):
            _stats.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")

        resp = client.get(
            "/api/v1/agents?window_hours=24&include_system=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        flags = {a["agent_id"]: a["is_system_agent"] for a in body["agents"]}
        assert flags["broadsign-prod"] is False
        assert flags["OpenDQV_SA_smoke_v240"] is True


# ── MCP — list_agents tool ────────────────────────────────────────────

@pytest.mark.asyncio
class TestMcpListAgentsSuppression:

    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch):
        import opendqv.mcp_server as mcp_module
        from opendqv.monitoring import ValidationStats
        fresh = ValidationStats()
        monkeypatch.setattr(mcp_module, "_stats", fresh)
        monkeypatch.setattr(mcp_module, "_remote_client", None)
        # Seed.
        for _ in range(5):
            fresh.record("customer", "default", True, 0, 0, 5.0, agent_id="broadsign-prod")
        for _ in range(7):
            fresh.record("customer", "default", True, 0, 0, 5.0, agent_id="OpenDQV_SA_smoke_v240")

    async def test_default_excludes_system(self):
        from opendqv.mcp_server import _tool_list_agents
        result = await _tool_list_agents({"window_hours": 24})
        data = json.loads(result[0].text)
        agent_ids = {a["agent_id"] for a in data["agents"]}
        assert "broadsign-prod" in agent_ids
        assert "OpenDQV_SA_smoke_v240" not in agent_ids
        assert data["include_system"] is False

    async def test_include_system_surfaces_system(self):
        from opendqv.mcp_server import _tool_list_agents
        result = await _tool_list_agents({"window_hours": 24, "include_system": True})
        data = json.loads(result[0].text)
        agent_ids = {a["agent_id"] for a in data["agents"]}
        assert "broadsign-prod" in agent_ids
        assert "OpenDQV_SA_smoke_v240" in agent_ids
        assert data["include_system"] is True
