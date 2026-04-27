"""Tests for the quality trend endpoint and QualityStats store."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_quality_stats(monkeypatch):
    """
    Replace the module-level _quality_stats singleton in api.routes with a
    fresh in-memory instance for every test in this module.

    Root cause of the previous file-based DELETE approach: the shared SQLite
    file accumulates rows written by other test modules (test_api, test_core_hardening,
    etc.) during the same pytest session.  A DELETE in a separate connection worked
    in isolation but was fragile in the full suite because SQLite WAL mode can
    expose committed writes only after the reader's snapshot is refreshed.

    Monkeypatching the singleton is the correct solution: each test starts with a
    guaranteed-empty in-memory store, and the app routes read/write through that
    same object, so there is no cross-connection visibility gap.
    """
    from opendqv.core.quality_stats import QualityStats
    import opendqv.api.deps as routes_module

    fresh_qs = QualityStats(":memory:")
    monkeypatch.setattr(routes_module, "_quality_stats", fresh_qs)
    yield


class TestQualityStats:
    """Unit tests for the QualityStats store."""

    def test_record_and_retrieve(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("customer", "1.0", None, total=100, passed=90, failed=10,
                        rule_failure_counts={"valid_email": 7, "name_required": 3})
        trend = qs.get_trend("customer", days=1)
        assert len(trend) == 1
        assert trend[0]["total_records"] == 100
        assert trend[0]["passed"] == 90
        assert trend[0]["failed"] == 10
        # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp). 90/100 → 90.0.
        assert trend[0]["pass_rate_pct"] == 90.0
        assert trend[0]["top_failing_rules"]["valid_email"] == 7

    def test_empty_trend(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        trend = qs.get_trend("nonexistent", days=7)
        assert trend == []

    def test_multiple_batches_same_day_aggregated(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("customer", "1.0", None, 50, 45, 5, {"valid_email": 5})
        qs.record_batch("customer", "1.0", None, 50, 40, 10, {"valid_email": 8, "name_required": 2})
        trend = qs.get_trend("customer", days=1)
        assert len(trend) == 1
        assert trend[0]["total_records"] == 100
        assert trend[0]["failed"] == 15
        assert trend[0]["top_failing_rules"]["valid_email"] == 13
        assert trend[0]["top_failing_rules"]["name_required"] == 2

    def test_context_filter(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("customer", "1.0", "billing", 100, 80, 20, {})
        qs.record_batch("customer", "1.0", "operations", 100, 95, 5, {})
        billing = qs.get_trend("customer", days=1, context="billing")
        assert billing[0]["total_records"] == 100
        assert billing[0]["failed"] == 20
        ops = qs.get_trend("customer", days=1, context="operations")
        assert ops[0]["failed"] == 5

    def test_zero_total_records_pass_rate(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("customer", "1.0", None, 0, 0, 0, {})
        trend = qs.get_trend("customer", days=1)
        # v2.3.18 Q3: empty-batch case returns 100.0 (vacuously perfect).
        assert trend[0]["pass_rate_pct"] == 100.0

    def test_top_failing_rules_sorted(self):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(":memory:")
        qs.record_batch("customer", "1.0", None, 100, 70, 30,
                        {"rule_a": 5, "rule_b": 20, "rule_c": 5})
        trend = qs.get_trend("customer", days=1)
        rules = list(trend[0]["top_failing_rules"].keys())
        assert rules[0] == "rule_b"  # highest count first


class TestQualityTrendAPI:
    """Integration tests for GET /api/v1/contracts/{name}/quality-trend."""

    def test_empty_trend_returns_200(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["contract"] == "customer"
        assert data["days"] == 7
        assert data["points"] == []

    def test_trend_after_batch_validation(self, client: TestClient, auth_headers):
        # Run a batch to generate stats
        records = [
            {"name": "Alice", "email": "alice@example.com", "phone": "+447911123456",
             "age": 25, "score": 85, "date": "1999-06-15",
             "username": "alice_s", "password": "securepass123"},
            {"name": "", "email": "bad", "age": 25, "score": 85,
             "date": "1999-06-15", "username": "bob", "password": "securepass123"},
        ]
        batch_resp = client.post("/api/v1/validate/batch",
                                 json={"records": records, "contract": "customer"},
                                 headers=auth_headers)
        assert batch_resp.status_code == 200

        trend_resp = client.get("/api/v1/contracts/customer/quality-trend?days=1")
        assert trend_resp.status_code == 200
        data = trend_resp.json()
        assert len(data["points"]) == 1
        assert data["points"][0]["total_records"] == 2

    def test_days_parameter(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend?days=30")
        assert resp.status_code == 200
        assert resp.json()["days"] == 30

    def test_days_max_90(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend?days=91")
        assert resp.status_code == 422

    def test_unknown_contract_returns_404(self, client: TestClient):
        resp = client.get("/api/v1/contracts/nonexistent/quality-trend")
        assert resp.status_code == 404

    def test_context_filter_in_api(self, client: TestClient):
        resp = client.get("/api/v1/contracts/customer/quality-trend?context=financial")
        assert resp.status_code == 200
        assert resp.json()["context"] == "financial"
