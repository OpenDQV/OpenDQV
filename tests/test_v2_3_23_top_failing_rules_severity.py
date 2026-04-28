"""
v2.3.23 round-3 review — top_failing_rules entries carry severity.

Persona B 2026-04-28 outside review #3 P1:
> "top_failing_rules mixes severities without flagging (warning vs error
>  counting). A warning failing 100x outranks an error failing 50x in
>  the dashboard, which is operationally backwards."

Sonnet pre-impl review (abab68d76a01115ec) verdict:
  - Read-side enrichment (Option A) — registry is authoritative; no
    schema migration; deferred severity for legacy data is honest as
    "unknown".
  - Single ranked list with `severity` field per entry — keep ranking
    policy on the consumer side, not baked into the API contract.
  - Explicit "unknown" when rule was deleted / cross-contract / legacy.

Surfaces tested:
  - opendqv/api/routes_analytics.py /api/v1/stats — REST + proxy source
  - opendqv/mcp_server.py _tool_get_quality_metrics — in-process MCP
  - opendqv/mcp_server.py _tool_get_quality_trend — in-process MCP trend
  - opendqv_mcp_proxy.py reshape — preserves severity from API
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest


# ── Proxy preserves severity from the API ───────────────────────────────

class TestProxyPreservesSeverity:
    """When the API enriches top_failing_fields with severity, the proxy's
    reshape must preserve it. Cold-client smoke covers this."""

    def _proxy(self):
        os.environ.setdefault("OPENDQV_API_URL", "http://127.0.0.1:1")
        os.environ.setdefault("OPENDQV_API_TOKEN", "")
        sys.modules.pop("opendqv_mcp_proxy", None)
        proxy_path = Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"
        spec = importlib.util.spec_from_file_location("opendqv_mcp_proxy", proxy_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["opendqv_mcp_proxy"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_severity_passes_through_when_api_provides_it(self):
        proxy = self._proxy()
        summary = {
            "by_contract": {"customer:default": {"pass": 10, "fail": 5}},
            "top_failing_fields": [
                {"contract": "customer", "field": "email", "rule": "valid_email",
                 "count": 3, "severity": "error"},
                {"contract": "customer", "field": "loyalty_tier", "rule": "tier_known",
                 "count": 100, "severity": "warning"},
            ],
            "latency": {},
        }
        entry = proxy._reshape_quality_metrics(summary, "customer", 24)
        rules = entry["top_failing_rules"]
        # Every entry has severity.
        for r in rules:
            assert "severity" in r, r
        # Sorting policy stays on the consumer — but the data is there.
        sev_by_rule = {r["rule"]: r["severity"] for r in rules}
        assert sev_by_rule == {
            "valid_email": "error",
            "tier_known": "warning",
        }

    def test_severity_unknown_when_api_omits_it(self):
        """Defensive: if a stale API (older than v2.3.23) returns
        top_failing_fields without severity, proxy emits "unknown" not
        a missing key."""
        proxy = self._proxy()
        summary = {
            "by_contract": {"customer:default": {"pass": 10, "fail": 5}},
            "top_failing_fields": [
                {"contract": "customer", "field": "email", "rule": "valid_email",
                 "count": 3},  # no severity
            ],
            "latency": {},
        }
        entry = proxy._reshape_quality_metrics(summary, "customer", 24)
        rules = entry["top_failing_rules"]
        assert rules[0]["severity"] == "unknown", rules[0]


# ── In-process MCP _severity_map helper ─────────────────────────────────

class TestSeverityMapHelper:
    """The helper reads from the live registry and degrades gracefully
    when the contract is missing or has no rules."""

    def test_returns_empty_dict_for_empty_contract_name(self):
        from opendqv.mcp_server import _severity_map
        assert _severity_map("") == {}

    def test_returns_empty_dict_for_unknown_contract(self):
        from opendqv.mcp_server import _severity_map
        assert _severity_map("__nonexistent_contract_xyz__") == {}

    def test_returns_severity_per_rule_for_real_contract(self):
        """Pick any bundled contract — the helper returns a non-empty
        map keyed by rule name."""
        from opendqv.mcp_server import _registry, _severity_map
        contracts = _registry.list_contracts()
        # Find a contract with at least one rule.
        for c_meta in contracts:
            cname = c_meta["name"]
            contract = _registry.get(cname)
            if contract and getattr(contract, "rules", None):
                sev_map = _severity_map(cname)
                assert sev_map, f"contract {cname!r} has rules but severity map is empty"
                # Every value is one of the known severity strings.
                for rule_name, sev in sev_map.items():
                    assert sev in ("error", "warning", "info"), (rule_name, sev)
                return
        pytest.skip("No bundled contract has rules — environment-specific.")


# ── routes_analytics enriches /api/v1/stats top_failing_fields ──────────

class TestApiStatsEnrichesTopFailingFields:
    """The API endpoint must tag every top_failing_fields entry with
    severity so consumers (proxy, REST, dashboard) get a single
    consistent shape."""

    def test_enrich_function_tags_severity_from_registry(self, monkeypatch):
        from opendqv.api.routes_analytics import _enrich_top_failing_fields_with_severity
        from opendqv.api import deps as _d

        class _FakeRule:
            def __init__(self, name, severity_value):
                self.name = name
                self.cached_severity_value = severity_value

        class _FakeContract:
            rules = [
                _FakeRule("valid_email", "error"),
                _FakeRule("tier_known", "warning"),
            ]
            contexts = {}  # required for normalizer (v2.3.23 ctx_* leak fix)

        class _FakeRegistry:
            def get(self, name, version="latest"):
                if name == "customer":
                    return _FakeContract()
                return None

        monkeypatch.setattr(_d, "registry", _FakeRegistry())

        summary = {
            "top_failing_fields": [
                {"contract": "customer", "field": "email", "rule": "valid_email", "count": 3},
                {"contract": "customer", "field": "tier", "rule": "tier_known", "count": 100},
                {"contract": "customer", "field": "?", "rule": "deleted_rule", "count": 1},
            ],
        }
        _enrich_top_failing_fields_with_severity(summary)
        sev_by_rule = {f["rule"]: f["severity"] for f in summary["top_failing_fields"]}
        assert sev_by_rule == {
            "valid_email": "error",
            "tier_known": "warning",
            "deleted_rule": "unknown",  # not in registry → explicit unknown
        }

    def test_enrich_handles_missing_registry(self, monkeypatch):
        """No registry shouldn't crash — every entry surfaces "unknown"."""
        from opendqv.api.routes_analytics import _enrich_top_failing_fields_with_severity
        from opendqv.api import deps as _d
        monkeypatch.setattr(_d, "registry", None)
        summary = {
            "top_failing_fields": [
                {"contract": "x", "field": "y", "rule": "z", "count": 1},
            ],
        }
        _enrich_top_failing_fields_with_severity(summary)
        assert summary["top_failing_fields"][0]["severity"] == "unknown"

    def test_enrich_no_op_on_empty_list(self):
        from opendqv.api.routes_analytics import _enrich_top_failing_fields_with_severity
        s = {"top_failing_fields": []}
        _enrich_top_failing_fields_with_severity(s)
        assert s["top_failing_fields"] == []

    def test_enrich_preserves_existing_severity(self, monkeypatch):
        """If a future ingest path already tagged severity at write-time,
        don't clobber it."""
        from opendqv.api.routes_analytics import _enrich_top_failing_fields_with_severity
        from opendqv.api import deps as _d

        class _FakeContract:
            rules = []
            contexts = {}

        class _FakeRegistry:
            def get(self, name, version="latest"):
                return _FakeContract()

        monkeypatch.setattr(_d, "registry", _FakeRegistry())
        summary = {
            "top_failing_fields": [
                {"contract": "x", "field": "y", "rule": "z", "count": 1, "severity": "info"},
            ],
        }
        _enrich_top_failing_fields_with_severity(summary)
        assert summary["top_failing_fields"][0]["severity"] == "info"


# ── REST integration: /api/v1/stats end-to-end ──────────────────────────

class TestApiStatsEndToEnd:
    """Hit the actual route and assert top_failing_fields entries carry
    severity. Uses the standard test fixtures."""

    def test_stats_endpoint_enriches_top_failing_fields(self, client, auth_headers):
        from opendqv.monitoring import stats
        # Seed one validation failure so top_failing_fields is non-empty.
        stats.record(
            contract="customer", context="default", valid=False,
            error_count=1, warning_count=0, latency_ms=1.0,
            errors=[{
                "field": "email", "rule": "valid_email",
                "message": "bad", "severity": "error",
                "error_code": "OPENDQV_FORMAT",
            }],
            agent_id="severity-test",
        )
        resp = client.get("/api/v1/stats", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        fields = body.get("top_failing_fields", [])
        # All recorded failures must carry severity now.
        for f in fields:
            assert "severity" in f, f
            assert f["severity"] in ("error", "warning", "info", "unknown"), f
