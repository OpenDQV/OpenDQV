"""
v2.3.23 round-3 review — ctx_<context>_ prefix leaks in LIVE trend
output (P2-11 only normalized hydration, not live emit).

Persona B 2026-04-28 outside review #3 P2:
> "ctx_billing_* prefix leaks in trend output (P2-11 only normalized
>  hydration, not live trend output)"

Sonnet pre-impl review (a154314ae2e179025) verdict: read-side
normalization at every emit boundary. Storage stays canonical to
execution (the override IS the rule that fired); presentation collapses
to the base rule name. Coalesce on collision so a window spanning a
mid-window context-declaration change doesn't silently drop counts.

Conservative guard: only strip `ctx_<context>_` when the context is
declared on the contract AND the suffix matches a base rule name.
Genuinely synthetic branch-3 rules (no base equivalent) stay as-is.

Surfaces tested:
  - GET /api/v1/contracts/{name}/quality-trend (REST, by ∈ all)
  - GET /api/v1/stats top_failing_fields (REST + proxy source)
  - opendqv/mcp_server.py:_tool_get_quality_metrics top_failing_rules
  - opendqv/mcp_server.py:_tool_get_quality_trend points

Single high-leverage assertion per Sonnet's recommendation: emit a
validation against an override rule, then assert no `ctx_` prefix
appears anywhere in the trend output.
"""

import asyncio
import json

import pytest


@pytest.fixture
def seeded_ctx_trend(monkeypatch):
    """Replace the shared `_quality_stats` singleton with an in-memory
    instance carrying both legacy-synth and canonical rule names. Seed
    is deliberate: we want to assert the COALESCE pattern works, not
    just the strip pattern."""
    from opendqv.core.quality_stats import QualityStats
    import opendqv.api.deps as deps_module
    import opendqv.mcp_server as mcp_module

    fresh = QualityStats(":memory:")
    # Two batches: one against the override (synthesised name), one
    # against the base rule (canonical name). After normalization both
    # collapse to `age_minimum` and counts must sum. customer.yaml has
    # `age_minimum` as a rule and `financial` as a declared context, so
    # the conservative guard (rule-exists + context-declared) strips.
    fresh.record_batch(
        contract_name="customer",
        contract_version="1.0",
        context="financial",
        total=10,
        passed=7,
        failed=3,
        rule_failure_counts={"ctx_financial_age_minimum": 3},
    )
    fresh.record_batch(
        contract_name="customer",
        contract_version="1.0",
        context=None,
        total=20,
        passed=18,
        failed=2,
        rule_failure_counts={"age_minimum": 2},
    )
    monkeypatch.setattr(deps_module, "_quality_stats", fresh)
    monkeypatch.setattr(mcp_module, "_quality_stats", fresh)
    yield fresh


# ── REST trend: no ctx_ leak across all `by` modes ─────────────────────

class TestRestTrendStripsCtxPrefix:
    @pytest.mark.parametrize("by", ["date", "agent", "context", "rule"])
    def test_no_ctx_prefix_in_response(self, client, seeded_ctx_trend, by):
        r = client.get(f"/api/v1/contracts/customer/quality-trend?by={by}&days=1")
        assert r.status_code == 200, r.text
        body = r.json()
        full_response_text = json.dumps(body)
        # Single high-leverage assertion: scan the entire response for
        # any `ctx_<declared_context>_` prefix. customer.yaml declares
        # contexts {kids_app, financial}; either prefix in the wire
        # output is the leak.
        assert "ctx_kids_app_" not in full_response_text, (
            f"v2.3.23 round-3: ctx_kids_app_ prefix leaked in by={by} "
            f"trend response. Got: {full_response_text}"
        )
        assert "ctx_financial_" not in full_response_text, (
            f"v2.3.23 round-3: ctx_financial_ prefix leaked in by={by} "
            f"trend response. Got: {full_response_text}"
        )

    def test_by_rule_coalesces_legacy_and_canonical(
        self, client, seeded_ctx_trend
    ):
        """Both `ctx_financial_age_minimum` (3 violations) and
        `age_minimum` (2 violations) seed rows must collapse to a
        single `age_minimum` entry with violation_count=5. Overwrite
        would silently lose either count."""
        r = client.get("/api/v1/contracts/customer/quality-trend?by=rule&days=1")
        body = r.json()
        rule_keys = {p["key"]: p for p in body["points"]}
        assert "age_minimum" in rule_keys, rule_keys
        assert "ctx_financial_age_minimum" not in rule_keys, rule_keys
        assert rule_keys["age_minimum"]["violation_count"] == 5, (
            f"Coalesce failed: ctx_financial_age_minimum=3 + "
            f"age_minimum=2 must sum to 5 under canonical key. "
            f"Got: {rule_keys['age_minimum']}"
        )


# ── /api/v1/stats top_failing_fields: no ctx_ leak ─────────────────────

class TestStatsEndpointStripsCtxPrefix:
    def test_top_failing_fields_collapse_ctx_to_base_rule(
        self, client, auth_headers
    ):
        """Live monitoring records the synthesised name; the API
        boundary normalizes before emit so the proxy and dashboards
        see the base rule name."""
        from opendqv.monitoring import stats
        # Seed via the live monitoring path so we exercise the API
        # enrichment, not the SQLite trend path.
        stats.record(
            contract="customer", context="financial", valid=False,
            error_count=1, warning_count=0, latency_ms=1.0,
            errors=[{
                "field": "age", "rule": "ctx_financial_age_minimum",
                "message": "Must be 18+", "severity": "error",
                "error_code": "OPENDQV_RULE_AGE_MINIMUM",
            }],
            agent_id="ctx-prefix-test",
        )
        resp = client.get("/api/v1/stats", headers=auth_headers)
        body = resp.json()
        fields = body.get("top_failing_fields", [])
        for f in fields:
            assert not f.get("rule", "").startswith("ctx_kids_app_"), f
            assert not f.get("rule", "").startswith("ctx_financial_"), (
                f"v2.3.23 round-3: live monitoring rule "
                f"`ctx_financial_age_minimum` must collapse to "
                f"`age_minimum` at the API emit boundary. Got: {f}"
            )


# ── In-process MCP _tool_get_quality_trend: no ctx_ leak ──────────────

class TestInProcessMcpTrendStripsCtxPrefix:
    def test_in_process_by_rule_collapses_ctx_to_base(
        self, seeded_ctx_trend, monkeypatch
    ):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer",
            "by": "rule",
            "days": 1,
        }))
        body = json.loads(result[0].text)
        for p in body["points"]:
            assert not p["key"].startswith("ctx_"), (
                f"v2.3.23 round-3: in-process MCP by=rule must strip "
                f"the ctx_<context>_ prefix at the emit boundary. "
                f"Got: {p}"
            )

    def test_in_process_by_date_no_ctx_in_top_failing_rules(
        self, seeded_ctx_trend, monkeypatch
    ):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer",
            "by": "date",
            "days": 1,
        }))
        body = json.loads(result[0].text)
        for p in body["points"]:
            for rule_name in p.get("top_failing_rules", {}).keys():
                assert not rule_name.startswith("ctx_"), (
                    f"top_failing_rules dict carries ctx_-prefixed rule "
                    f"name. Got: {rule_name}"
                )
            for entry in p.get("top_failing_rules_ranked", []):
                assert not entry["rule"].startswith("ctx_"), (
                    f"top_failing_rules_ranked carries ctx_-prefixed "
                    f"rule name. Got: {entry}"
                )


# ── Helper unit tests ───────────────────────────────────────────────────

class TestNormalizerHelper:
    def test_strips_when_context_and_base_rule_exist(self):
        from opendqv.monitoring import _build_rule_normalizer

        class _R:
            def __init__(self, n):
                self.name = n

        class _C:
            rules = [_R("age_min")]
            contexts = {"financial": {}}

        n = _build_rule_normalizer(_C())
        assert n("ctx_financial_age_min") == "age_min"

    def test_keeps_unknown_context(self):
        from opendqv.monitoring import _build_rule_normalizer

        class _R:
            def __init__(self, n):
                self.name = n

        class _C:
            rules = [_R("age_min")]
            contexts = {"financial": {}}

        n = _build_rule_normalizer(_C())
        # 'undeclared' isn't in contexts → stay as-is (could be a
        # genuinely synthetic rule).
        assert n("ctx_undeclared_age_min") == "ctx_undeclared_age_min"

    def test_keeps_unknown_base_rule(self):
        from opendqv.monitoring import _build_rule_normalizer

        class _R:
            def __init__(self, n):
                self.name = n

        class _C:
            rules = [_R("age_min")]
            contexts = {"financial": {}}

        n = _build_rule_normalizer(_C())
        # 'unknown_rule' isn't a base rule → stay as-is (synthetic
        # branch-3 rule that has no canonical counterpart).
        assert n("ctx_financial_unknown_rule") == "ctx_financial_unknown_rule"

    def test_passes_through_non_ctx_names(self):
        from opendqv.monitoring import _build_rule_normalizer

        class _C:
            rules = []
            contexts = {}

        n = _build_rule_normalizer(_C())
        assert n("plain_rule") == "plain_rule"
        assert n("") == ""

    def test_normalize_trend_rule_names_coalesces_by_rule(self):
        from opendqv.monitoring import normalize_trend_rule_names

        class _R:
            def __init__(self, n):
                self.name = n

        class _C:
            rules = [_R("amount_min")]
            contexts = {"billing": {}}

        points = [
            {"key": "ctx_billing_amount_min", "violation_count": 3, "severity": "error"},
            {"key": "amount_min", "violation_count": 2, "severity": "error"},
            {"key": "ctx_other_amount_min", "violation_count": 1, "severity": "unknown"},
        ]
        out = normalize_trend_rule_names(points, _C(), "rule")
        keys = {p["key"]: p for p in out}
        assert "amount_min" in keys
        assert keys["amount_min"]["violation_count"] == 5, keys
        # ctx_other_amount_min stays (unknown context).
        assert "ctx_other_amount_min" in keys, keys
