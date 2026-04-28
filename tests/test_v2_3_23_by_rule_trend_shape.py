"""
v2.3.23 round-3 review — by:rule trend shape parity.

Persona B 2026-04-28 outside review #3 P2:
> "get_quality_trend by:rule emits date:null + empty top_failing_rules
>  — inconsistent vs by:contract / by:agent shapes."

Root cause: REST route at routes_contracts.py used QualityTrendResponse
which constructs QualityTrendPoint(**p) for each raw point. Pydantic
fills unspecified fields with defaults (date=None, top_failing_rules={},
top_failing_rules_ranked=[]) — so a by=rule point that should be just
{key, violation_count} surfaces as a bloated 9-key object on the wire.

Sonnet pre-impl review (afac7ed4604cc1e07) verdict:
  - response_model_exclude_unset=True on the route — drops fields that
    weren't explicitly set in the constructor kwargs. Pydantic V2's
    model_fields_set is populated only from kwargs.
  - severity must be passed in the constructor (not mutated post-hoc),
    otherwise it gets dropped by exclude_unset too.
  - Don't split into per-by sub-models — discriminated unions break
    SDK consumers.

Surfaces tested:
  - REST `/api/v1/contracts/{name}/quality-trend` for by ∈ {date, agent,
    context, rule}
  - severity tagging on by=rule rows + on top_failing_rules_ranked entries
"""

import json

import pytest


@pytest.fixture
def seeded_trend(monkeypatch):
    """Replace shared `_quality_stats` with an in-memory instance with a
    deterministic by=rule trail so the parametrized shape assertions are
    against known data."""
    from opendqv.core.quality_stats import QualityStats
    import opendqv.api.deps as deps_module
    import opendqv.mcp_server as mcp_module

    fresh = QualityStats(":memory:")
    fresh.record_batch(
        contract_name="customer",
        contract_version="1.0",
        context=None,
        total=100,
        passed=90,
        failed=10,
        rule_failure_counts={"valid_email": 5, "name_required": 3},
    )
    monkeypatch.setattr(deps_module, "_quality_stats", fresh)
    monkeypatch.setattr(mcp_module, "_quality_stats", fresh)
    yield fresh


# ── by=rule emits only {key, violation_count, severity} ─────────────────

class TestByRuleShapeIsLean:
    """The reviewer's exact complaint: by=rule must not surface
    date:null, top_failing_rules:{}, top_failing_rules_ranked:[],
    passed:0, failed:0, total_records:0 — those are date-mode fields
    that don't apply to a rule entry."""

    def test_by_rule_no_date_no_rule_dicts(self, client, seeded_trend):
        r = client.get("/api/v1/contracts/customer/quality-trend?by=rule&days=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == "rule"
        # Each by=rule point must be lean.
        for p in body["points"]:
            assert "date" not in p, f"by=rule must not surface date:null. Got: {p}"
            assert "top_failing_rules" not in p, (
                f"by=rule must not surface top_failing_rules:{{}}. Got: {p}"
            )
            assert "top_failing_rules_ranked" not in p, (
                f"by=rule must not surface top_failing_rules_ranked:[]. Got: {p}"
            )
            # passed/failed/total_records/pass_rate_pct don't apply per-rule.
            assert "passed" not in p, p
            assert "failed" not in p, p
            assert "total_records" not in p, p
            assert "pass_rate_pct" not in p, p

    def test_by_rule_carries_required_fields(self, client, seeded_trend):
        r = client.get("/api/v1/contracts/customer/quality-trend?by=rule&days=1")
        body = r.json()
        for p in body["points"]:
            assert "key" in p, p
            assert "violation_count" in p, p
            # severity comes from the live registry — bundled `customer`
            # contract has severities on its rules.
            assert "severity" in p, (
                f"v2.3.23 round-3: each by=rule entry must carry severity. "
                f"Got: {p}"
            )
            assert p["severity"] in ("error", "warning", "info", "unknown"), p


# ── by=date keeps the full daily shape ─────────────────────────────────

class TestByDateShapeIsFull:
    """Regression guard: the by=date shape must NOT lose its date,
    daily counters, or top_failing_rules_ranked. exclude_unset must drop
    only fields that weren't explicitly set."""

    def test_by_date_carries_date_and_daily_fields(self, client, seeded_trend):
        r = client.get("/api/v1/contracts/customer/quality-trend?by=date&days=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == "date"
        assert body["points"], "by=date must return at least one point with seeded data"
        for p in body["points"]:
            assert "date" in p, p
            assert "total_records" in p, p
            assert "passed" in p, p
            assert "failed" in p, p
            assert "pass_rate_pct" in p, p
            assert "top_failing_rules_ranked" in p, p
            # by=date must NOT carry by=rule-only fields.
            assert "key" not in p, p
            assert "violation_count" not in p, p

    def test_by_date_top_failing_rules_ranked_carries_severity(
        self, client, seeded_trend
    ):
        """The severity tag also lands on per-day ranked entries so a
        consumer can read 'on 2026-04-28, error rule X failed 50x' vs
        'warning rule Y failed 100x' from one response."""
        r = client.get("/api/v1/contracts/customer/quality-trend?by=date&days=1")
        body = r.json()
        for p in body["points"]:
            for entry in p.get("top_failing_rules_ranked", []):
                assert "severity" in entry, (
                    f"v2.3.23 round-3: top_failing_rules_ranked entries "
                    f"must carry severity for consistent ranking. Got: {entry}"
                )


# ── by=agent / by=context drop date:null but keep their own bucket fields ─

class TestByAgentContextShapeIsLean:
    """by=agent and by=context already worked correctly per the round-3
    reviewer — but exclude_unset would change them too. Pin the new
    shape: no spurious date:null, but bucket counters preserved."""

    @pytest.mark.parametrize("by", ["agent", "context"])
    def test_no_spurious_date_field(self, client, seeded_trend, by):
        r = client.get(f"/api/v1/contracts/customer/quality-trend?by={by}&days=1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == by
        for p in body["points"]:
            assert "date" not in p, (
                f"by={by} must not surface date:null. Got: {p}"
            )

    @pytest.mark.parametrize("by", ["agent", "context"])
    def test_bucket_fields_preserved(self, client, seeded_trend, by):
        """by=agent / by=context entries explicitly set total_records,
        passed, failed, pass_rate_pct in _group_trend — those must
        survive exclude_unset."""
        r = client.get(f"/api/v1/contracts/customer/quality-trend?by={by}&days=1")
        body = r.json()
        for p in body["points"]:
            assert "key" in p, p
            assert "total_records" in p, p
            assert "passed" in p, p
            assert "failed" in p, p
            # pass_rate_pct can be null when bucket is empty (Cluster F),
            # but the field is explicitly set in _group_trend so it stays.
            assert "pass_rate_pct" in p, p
            # by=rule-only fields stay absent.
            assert "violation_count" not in p, p


# ── In-process MCP path (non-Pydantic) parity check ─────────────────────

class TestInProcessMcpByRuleShape:
    """The in-process MCP path returns raw dicts (no Pydantic) — already
    clean by construction. Pin parity so a future refactor that routes
    in-process through Pydantic doesn't re-introduce the leak."""

    def test_in_process_by_rule_dict_is_lean(self, seeded_trend, monkeypatch):
        import asyncio
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer",
            "by": "rule",
            "days": 1,
        }))
        body = json.loads(result[0].text)
        for p in body["points"]:
            assert "date" not in p, p
            assert "top_failing_rules" not in p, p
            assert "top_failing_rules_ranked" not in p, p
            assert "key" in p, p
            assert "violation_count" in p, p
            assert "severity" in p, (
                f"v2.3.23 round-3: in-process MCP by=rule entry must "
                f"carry severity (matches REST). Got: {p}"
            )
