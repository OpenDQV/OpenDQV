"""
v2.3.22 proxy/in-process MCP shape-parity for get_quality_metrics.

Surfaced in the v2.3.22 inside-view persona probe (the eval done after
8 clusters shipped, before tag): the proxy returned the raw
/api/v1/stats summary while the in-process MCP returned a per-contract
entry. Same tool name, two paths, two shapes — CRT170-J family.

Pre-existing since v2.3.13. Pilot called the bluff on
"defer to v2.4" — a defect is a defect, not a debate. Fix shipped
alongside the cluster set.

The parity test pattern (which Sonnet flagged as missing in her
defer-recommendation) is the new structural guard. Cold-client smoke
gap closure for this specific tool.
"""

import asyncio
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_proxy_module():
    """Import the stdio proxy as a module (it lives at repo root, not
    under the package). The proxy hard-exits at import when
    OPENDQV_API_URL is unset, so set a stub URL — we don't actually
    make HTTP calls (the reshape helper is pure)."""
    import os
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


# ── Reshape unit tests ─────────────────────────────────────────────────

class TestReshapeShapeMatchesInProcessKeys:
    """The proxy's reshaped output must contain the same keys as the
    in-process MCP per-contract entry. Different value sources, same
    consumer-facing contract."""

    EXPECTED_KEYS = {
        "contract", "window_hours", "total_validations", "pass_rate_pct",
        "passed", "failed", "data_confidence", "confidence_note",
        "top_failing_rules", "latency", "catalog_hint", "governance_tip",
    }

    def test_contract_filter_returns_single_entry_with_all_keys(self, proxy_mod):
        summary = {
            "by_contract": {
                "customer:default": {"pass": 8, "fail": 2, "errors": 2, "warnings": 0},
            },
            "top_failing_fields": [
                {"contract": "customer", "field": "email", "rule": "valid_email", "count": 2},
            ],
            "latency": {"avg_ms": 1.2, "p95_ms": 3.5, "sample_size": 10},
        }
        entry = proxy_mod._reshape_quality_metrics(summary, "customer", 24)
        assert isinstance(entry, dict), entry
        missing = self.EXPECTED_KEYS - set(entry.keys())
        assert not missing, (
            f"v2.3.22 proxy/in-process shape parity broken — proxy entry "
            f"missing keys: {missing}. Expected: {self.EXPECTED_KEYS}. "
            f"Got: {set(entry.keys())}"
        )
        assert entry["contract"] == "customer"
        assert entry["passed"] == 8
        assert entry["failed"] == 2
        assert entry["total_validations"] == 10
        assert entry["pass_rate_pct"] == 80.0

    def test_no_contract_returns_list_of_entries(self, proxy_mod):
        summary = {
            "by_contract": {
                "customer:default": {"pass": 10, "fail": 0},
                "banking:default": {"pass": 5, "fail": 5},
            },
            "top_failing_fields": [],
            "latency": {},
        }
        out = proxy_mod._reshape_quality_metrics(summary, "", 24)
        assert isinstance(out, list)
        assert len(out) == 2
        for entry in out:
            assert set(entry.keys()).issuperset(self.EXPECTED_KEYS)
        contracts = {e["contract"] for e in out}
        assert contracts == {"customer", "banking"}

    def test_empty_state_with_contract_filter(self, proxy_mod):
        """Cluster F parity: empty-state per-contract entry returns
        pass_rate_pct=None, data_confidence='no_data', not 100.0."""
        summary = {"by_contract": {}, "top_failing_fields": [], "latency": {}}
        entry = proxy_mod._reshape_quality_metrics(summary, "ghost_contract", 24)
        assert isinstance(entry, dict)
        assert entry["total_validations"] == 0
        assert entry["pass_rate_pct"] is None, (
            f"v2.3.22 Cluster F parity: empty-state must return null, "
            f"not 100.0. Got: {entry['pass_rate_pct']!r}"
        )
        assert entry["data_confidence"] == "no_data"

    def test_top_failing_rules_shape(self, proxy_mod):
        """top_failing_rules is a list of {rule, field, failures, severity} —
        not the raw {contract, field, rule, count} from
        top_failing_fields. v2.3.23 round-3 review added `severity` so
        consumers can rank a warning vs error correctly without a
        registry round-trip."""
        summary = {
            "by_contract": {"customer:default": {"pass": 10, "fail": 5}},
            "top_failing_fields": [
                # API enriches each entry with severity in
                # routes_analytics._enrich_top_failing_fields_with_severity.
                # Pre-enriched here to mirror the live wire payload.
                {"contract": "customer", "field": "email", "rule": "valid_email", "count": 3, "severity": "error"},
                {"contract": "customer", "field": "name", "rule": "name_required", "count": 2, "severity": "warning"},
            ],
            "latency": {},
        }
        entry = proxy_mod._reshape_quality_metrics(summary, "customer", 24)
        rules = entry["top_failing_rules"]
        assert len(rules) == 2
        for r in rules:
            assert set(r.keys()) == {"rule", "field", "failures", "severity"}, r
        # Severity is preserved end-to-end.
        sev_by_rule = {r["rule"]: r["severity"] for r in rules}
        assert sev_by_rule == {"valid_email": "error", "name_required": "warning"}

    def test_by_agent_omitted_when_contract_filter_set(self, proxy_mod):
        """When a contract filter is set, the unscoped by_agent from
        the global summary must NOT leak into the per-contract entry —
        same Cluster A discipline. The engine doesn't expose
        contract-scoped by_agent on REST today; proxy omits rather
        than leaks."""
        summary = {
            "by_contract": {"customer:default": {"pass": 10, "fail": 0}},
            "top_failing_fields": [],
            "latency": {},
            "by_agent": {
                "broadsign-prod": {"pass": 100, "fail": 5, "total": 105, "pass_rate_pct": 95.2},
                "salesforce-prod": {"pass": 50, "fail": 0, "total": 50, "pass_rate_pct": 100.0},
            },
        }
        entry = proxy_mod._reshape_quality_metrics(summary, "customer", 24)
        assert "by_agent" not in entry, (
            f"v2.3.22 Cluster A discipline: scoped per-contract entry must "
            f"NOT carry the unscoped global by_agent. Got: {entry.get('by_agent')!r}"
        )

    def test_by_agent_omitted_when_no_contract_filter_too(self, proxy_mod):
        """v2.3.23 outside-review P0 (Sonnet a74a3758ab3476042):
        the v2.3.22 contract here was wrong. Inlining summary["by_agent"]
        into per-contract entries when no contract filter is set turned
        out to surface the SAME global rollup labelled as if it were
        each contract's per-agent breakdown — which is what the
        Persona B reviewer caught on 2026-04-28. The fix dropped the
        fallback in both the proxy reshape and the in-process MCP.
        Per-contract entries no longer carry by_agent at all; consumers
        wanting an agent rollup call list_agents (already global) or
        pass agent_id filter on get_quality_metrics."""
        summary = {
            "by_contract": {"customer:default": {"pass": 10, "fail": 0}},
            "top_failing_fields": [],
            "latency": {},
            "by_agent": {
                "broadsign-prod": {"pass": 100, "fail": 5, "total": 105, "pass_rate_pct": 95.2},
                "salesforce-prod": {"pass": 50, "fail": 0, "total": 50, "pass_rate_pct": 100.0},
            },
        }
        out = proxy_mod._reshape_quality_metrics(summary, "", 24)
        assert isinstance(out, list)
        assert len(out) == 1
        # Critical: the per-contract entry must NOT inline the global
        # by_agent rollup. That was the v2.3.23 outside-review P0.
        assert "by_agent" not in out[0]


# ── Confidence band parity ────────────────────────────────────────────

class TestConfidenceBandParity:
    """The proxy's _confidence_band must mirror the engine's
    quality_confidence so data_confidence is identical across paths."""

    def test_no_data(self, proxy_mod):
        from opendqv.core.quality_stats import quality_confidence
        for total in (0, -1):
            engine = quality_confidence(total)
            proxy = proxy_mod._confidence_band(total)
            assert engine == proxy, (engine, proxy, total)

    def test_low_band(self, proxy_mod):
        from opendqv.core.quality_stats import quality_confidence
        for total in (1, 5, 9):
            engine = quality_confidence(total)
            proxy = proxy_mod._confidence_band(total)
            assert engine[0] == proxy[0], (engine, proxy, total)

    def test_medium_band(self, proxy_mod):
        from opendqv.core.quality_stats import quality_confidence
        for total in (10, 25, 49):
            engine = quality_confidence(total)
            proxy = proxy_mod._confidence_band(total)
            assert engine[0] == proxy[0]

    def test_high_band(self, proxy_mod):
        from opendqv.core.quality_stats import quality_confidence
        for total in (50, 100, 1000):
            engine = quality_confidence(total)
            proxy = proxy_mod._confidence_band(total)
            assert engine[0] == proxy[0]


# ── Cross-path shape comparison (in-process MCP vs proxy reshape) ─────

class TestInProcessVsProxyShape:
    """Run the same input through the in-process MCP tool dispatcher
    and the proxy reshape; assert keys match. This is the structural
    guard Sonnet flagged as missing — the cold-client smoke gap closure
    for get_quality_metrics specifically."""

    def test_same_keys_when_contract_filter_set(self, proxy_mod, monkeypatch):
        from opendqv import mcp_server
        from opendqv.core.quality_stats import QualityStats
        import opendqv.api.deps as deps_module

        monkeypatch.setattr(mcp_server, "_remote_client", None)

        # Seed shared state
        fresh_qs = QualityStats(":memory:")
        fresh_qs.record_batch(
            "customer", "1.0", "default",
            total=10, passed=8, failed=2,
            rule_failure_counts={"valid_email": 2},
            agent_id="broadsign-prod",
        )
        monkeypatch.setattr(deps_module, "_quality_stats", fresh_qs)
        monkeypatch.setattr(mcp_server, "_quality_stats", fresh_qs)

        # In-memory ValidationStats: feed it the same batch shape
        from opendqv.monitoring import ValidationStats
        fresh_stats = ValidationStats()
        fresh_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="broadsign-prod",
        )
        monkeypatch.setattr(mcp_server, "_stats", fresh_stats)

        in_process_result = asyncio.run(
            mcp_server._tool_get_quality_metrics({"contract": "customer", "window_hours": 24})
        )
        in_process_body = json.loads(in_process_result[0].text)

        # Proxy reshape — feed the same kind of summary the REST endpoint
        # would emit for /api/v1/stats?contract=customer.
        rest_summary = {
            "by_contract": {"customer:default": {"pass": 1, "fail": 0, "errors": 0, "warnings": 0}},
            "top_failing_fields": [],
            "latency": {"avg_ms": 1.0, "sample_size": 1},
            "total_validations": 1,
            "total_pass": 1,
            "total_fail": 0,
            "pass_rate_pct": 100.0,
            "contract_filter": "customer",
        }
        proxy_body = proxy_mod._reshape_quality_metrics(rest_summary, "customer", 24)

        # Both must be dicts (contract filter set)
        assert isinstance(in_process_body, dict)
        assert isinstance(proxy_body, dict)

        # Key parity — the v2.3.22 fix.
        in_process_keys = set(in_process_body.keys())
        proxy_keys = set(proxy_body.keys())
        # Allow proxy to be a subset (omit by_agent, omit any in-process-only
        # augmentation) — but must contain the core consumer-facing keys.
        core = {
            "contract", "window_hours", "total_validations", "pass_rate_pct",
            "passed", "failed", "data_confidence", "top_failing_rules",
            "latency", "catalog_hint", "governance_tip",
        }
        assert core.issubset(in_process_keys), in_process_keys
        assert core.issubset(proxy_keys), proxy_keys

        # Values that should match exactly
        assert in_process_body["contract"] == proxy_body["contract"]
        assert in_process_body["total_validations"] == proxy_body["total_validations"]
        assert in_process_body["pass_rate_pct"] == proxy_body["pass_rate_pct"]
        assert in_process_body["passed"] == proxy_body["passed"]
        assert in_process_body["failed"] == proxy_body["failed"]
        assert in_process_body["data_confidence"] == proxy_body["data_confidence"]
        assert in_process_body["catalog_hint"] == proxy_body["catalog_hint"]
