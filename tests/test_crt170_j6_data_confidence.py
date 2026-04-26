"""
tests/test_crt170_j6_data_confidence.py — CRT170/J6 acceptance.

Pins data_confidence + confidence_note + total_validations symmetry across:
  - opendqv.core.quality_stats.quality_confidence (single source of truth)
  - opendqv.mcp_server._tool_get_quality_metrics (already covered by RT107)
  - opendqv.mcp_server._tool_get_quality_trend
  - opendqv.mcp_server._tool_get_rule_velocity
  - GET /api/v1/contracts/{name}/quality-trend
  - GET /api/v1/analytics/rule-velocity

Working principle (CRT170): a response surface that reports a metric must
also report enough context for the consumer to judge that metric's
reliability. Same scale across tools so clients interpret data sufficiency
consistently.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_quality_stats(monkeypatch):
    """Fresh in-memory QualityStats per test, swapped into deps singleton."""
    from opendqv.core.quality_stats import QualityStats
    import opendqv.api.deps as deps_module
    import opendqv.mcp_server as mcp_module

    fresh_qs = QualityStats(":memory:")
    monkeypatch.setattr(deps_module, "_quality_stats", fresh_qs)
    monkeypatch.setattr(mcp_module, "_quality_stats", fresh_qs)
    # Ensure MCP runs in local mode (not remote-client passthrough).
    monkeypatch.setattr(mcp_module, "_remote_client", None)
    yield


def _parse(result):
    assert isinstance(result, list) and len(result) == 1
    return json.loads(result[0].text)


# ── Single source of truth — quality_confidence(total) ─────────────────

class TestQualityConfidenceHelper:
    """The shared helper drives both MCP tools and both REST endpoints."""

    def test_no_data_band(self):
        from opendqv.core.quality_stats import quality_confidence
        band, note = quality_confidence(0)
        assert band == "no_data"
        assert note is not None and "No validation data" in note

    def test_low_band(self):
        from opendqv.core.quality_stats import quality_confidence
        band, note = quality_confidence(1)
        assert band == "low"
        assert "1 validation" in note and "validations" not in note  # singular

        band, note = quality_confidence(9)
        assert band == "low"
        assert "9 validations" in note  # plural

    def test_medium_band(self):
        # CRT173/23: medium/high bands return "" (empty string), not None.
        from opendqv.core.quality_stats import quality_confidence
        band, note = quality_confidence(10)
        assert band == "medium"
        assert note == ""

        band, note = quality_confidence(99)
        assert band == "medium"
        assert note == ""

    def test_high_band(self):
        # CRT173/23: medium/high bands return "" (empty string), not None.
        from opendqv.core.quality_stats import quality_confidence
        band, note = quality_confidence(100)
        assert band == "high"
        assert note == ""

        band, note = quality_confidence(10_000)
        assert band == "high"


# ── MCP — _tool_get_quality_trend ──────────────────────────────────────

@pytest.mark.asyncio
class TestMcpGetQualityTrendConfidence:

    async def test_no_data_returns_no_data_band(self):
        from opendqv.mcp_server import _tool_get_quality_trend
        data = _parse(await _tool_get_quality_trend({"contract": "customer", "days": 7}))
        assert data["data_confidence"] == "no_data"
        assert data["confidence_note"] is not None
        assert data["total_validations"] == 0

    async def test_low_band_after_few_records(self, monkeypatch):
        from opendqv.mcp_server import _tool_get_quality_trend
        import opendqv.mcp_server as mcp_module

        mcp_module._quality_stats.record_batch(
            "customer", "1.0", None, total=5, passed=4, failed=1,
            rule_failure_counts={"valid_email": 1},
        )
        data = _parse(await _tool_get_quality_trend({"contract": "customer", "days": 7}))
        assert data["data_confidence"] == "low"
        assert data["confidence_note"] is not None
        assert data["total_validations"] == 5

    async def test_high_band_after_many_records(self):
        from opendqv.mcp_server import _tool_get_quality_trend
        import opendqv.mcp_server as mcp_module

        mcp_module._quality_stats.record_batch(
            "customer", "1.0", None, total=500, passed=480, failed=20,
            rule_failure_counts={"valid_email": 20},
        )
        data = _parse(await _tool_get_quality_trend({"contract": "customer", "days": 7}))
        assert data["data_confidence"] == "high"
        # CRT173/23: confidence_note is "" (empty string) for high band, not None.
        assert data["confidence_note"] == ""
        assert data["total_validations"] == 500


# ── MCP — _tool_get_rule_velocity ──────────────────────────────────────

@pytest.mark.asyncio
class TestMcpGetRuleVelocityConfidence:

    async def test_no_data_returns_no_data_band(self):
        from opendqv.mcp_server import _tool_get_rule_velocity
        data = _parse(await _tool_get_rule_velocity({"contract": "customer", "window_hours": 24}))
        assert data["data_confidence"] == "no_data"
        assert data["confidence_note"] is not None
        assert data["total_validations"] == 0

    async def test_band_reflects_window_totals(self):
        from opendqv.mcp_server import _tool_get_rule_velocity
        import opendqv.mcp_server as mcp_module

        mcp_module._quality_stats.record_batch(
            "customer", "1.0", None, total=50, passed=45, failed=5,
            rule_failure_counts={"valid_email": 5},
        )
        data = _parse(await _tool_get_rule_velocity({"contract": "customer", "window_hours": 24}))
        assert data["data_confidence"] == "medium"
        assert data["total_validations"] == 50


# ── REST — GET /api/v1/contracts/{name}/quality-trend ──────────────────

class TestRestQualityTrendConfidence:

    def test_empty_returns_no_data_band(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_confidence"] == "no_data"
        assert body["confidence_note"] is not None
        assert body["total_validations"] == 0

    def test_band_after_batch_validation(self, client: TestClient, auth_headers):
        records = [
            {"name": "Alice", "email": "alice@example.com", "phone": "+447911123456",
             "age": 25, "score": 85, "date": "1999-06-15",
             "username": "alice_s", "password": "securepass123"},
            {"name": "", "email": "bad", "age": 25, "score": 85,
             "date": "1999-06-15", "username": "bob", "password": "securepass123"},
        ]
        resp = client.post(
            "/api/v1/validate/batch",
            json={"records": records, "contract": "customer"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        trend = client.get("/api/v1/contracts/customer/quality-trend?days=1").json()
        assert trend["total_validations"] == 2
        assert trend["data_confidence"] == "low"
        assert trend["confidence_note"] is not None


# ── REST — GET /api/v1/analytics/rule-velocity ─────────────────────────

class TestRestRuleVelocityConfidence:

    def test_empty_returns_no_data_band(self, client: TestClient, auth_headers):
        resp = client.get(
            "/api/v1/analytics/rule-velocity?contract=customer&window_hours=24",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_confidence"] == "no_data"
        assert body["confidence_note"] is not None
        assert body["total_validations"] == 0

    def test_band_reflects_window_totals(self, client: TestClient, auth_headers):
        from opendqv.api.deps import _quality_stats
        _quality_stats.record_batch(
            "customer", "1.0", None, total=15, passed=12, failed=3,
            rule_failure_counts={"valid_email": 3},
        )
        resp = client.get(
            "/api/v1/analytics/rule-velocity?contract=customer&window_hours=24",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_confidence"] == "medium"
        assert body["total_validations"] == 15


# ── Symmetry — every analytics surface returns the same field set ──────

class TestConfidenceFieldSymmetry:
    """The three confidence fields are present and named identically everywhere."""

    REQUIRED = {"data_confidence", "confidence_note", "total_validations"}

    def test_rest_quality_trend_has_all_three(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend").json()
        assert self.REQUIRED.issubset(resp.keys())

    def test_rest_rule_velocity_has_all_three(self, client: TestClient, auth_headers):
        resp = client.get(
            "/api/v1/analytics/rule-velocity?contract=customer&window_hours=24",
            headers=auth_headers,
        ).json()
        assert self.REQUIRED.issubset(resp.keys())

    @pytest.mark.asyncio
    async def test_mcp_quality_trend_has_all_three(self):
        from opendqv.mcp_server import _tool_get_quality_trend
        data = _parse(await _tool_get_quality_trend({"contract": "customer"}))
        assert self.REQUIRED.issubset(data.keys())

    @pytest.mark.asyncio
    async def test_mcp_rule_velocity_has_all_three(self):
        from opendqv.mcp_server import _tool_get_rule_velocity
        data = _parse(await _tool_get_rule_velocity({"contract": "customer"}))
        assert self.REQUIRED.issubset(data.keys())
