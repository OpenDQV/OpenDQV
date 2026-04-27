"""
v2.3.22 N-2 (P0) — get_quality_trend(by=rule) returns no_data while
data exists.

Reviewer Persona B round-2 (2026-04-27):

> Same window + contract: by=date returns 3,695 validations with rich
> daily breakdowns. by=rule returns total_validations: 0,
> data_confidence: "no_data", all pass_rate: 1.0 even where
> violation_count: 255.
> Customer impact: any dashboard pivoting trend by rule (the natural
> way to spot a worsening rule) reads healthy when it is not. SRE
> blind spot.

Root cause: by=rule points carry `violation_count` not `total_records`
(a rule has violations, not records — see quality_stats.py:300-323).
Aggregator sums `total_records` from those points → 0 →
`_quality_confidence(0)` → `data_confidence: "no_data"`.

Two paths produce this trend response:

  1. REST: routes_contracts.py:395-397 — ALREADY fixed in v2.3.17
     (re-queries by=date for total_validations when by=rule).
  2. MCP in-process: mcp_server.py:1590-1591 — UNFIXED. Sums
     total_records straight from by=rule points → 0.

Sonnet's pre-impl review (agentId a0103a3fef8e7e83f):
  - The REST fix is the spec; mirror it into MCP in-process.
  - "all pass_rate: 1.0" in reviewer's output is stale-client
    pollution — by=rule points have no pass_rate field at all in
    engine code (Pydantic null default serializes as null, not 1.0).
    Don't chase a phantom; confirm via this regression test.
  - Test parametrizes over (rest, mcp_inprocess). Proxy path calls
    REST so it inherits the fix; explicit proxy assertion is
    belt-and-suspenders only.

Pre-fix expected: REST green (already patched), MCP in-process red.
Post-fix expected: both green.
"""

import asyncio
import json

import pytest


@pytest.fixture
def seeded_quality_stats(monkeypatch):
    """Replace the shared `_quality_stats` singleton with an in-memory
    instance carrying a known by=rule trail. Same monkeypatch pattern
    as `tests/test_quality_trend.py` — guarantees no cross-test
    pollution from the SQLite tempfile.

    Seed: contract='customer', one batch with violations on
    `valid_email` (5) + `name_required` (3). Total 100 records, 90
    pass / 10 fail.
    """
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


class TestN2ByRuleTotalValidations:
    """N-2 P0: total_validations + data_confidence on by=rule must
    reflect the underlying record volume from by=date — not the
    sum-of-zeros that comes from points carrying violation_count
    instead of total_records."""

    def test_rest_path_by_rule_reports_real_total_validations(
        self, client, seeded_quality_stats
    ):
        """REST endpoint already patched in v2.3.17. Regression
        guard so the fix isn't accidentally reverted."""
        r = client.get(
            "/api/v1/contracts/customer/quality-trend?by=rule&days=1"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["by"] == "rule"
        assert body["total_validations"] == 100, (
            f"REST by=rule must derive total_validations from by=date "
            f"(100), not from sum-of-zeros on rule points. "
            f"Got: {body['total_validations']}"
        )
        assert body["data_confidence"] != "no_data", (
            f"REST by=rule data_confidence must reflect the real "
            f"underlying record volume, not 'no_data'. "
            f"Got: {body['data_confidence']!r}"
        )
        # Each rule entry must carry violation_count (not pass_rate).
        rule_keys = {p["key"]: p for p in body["points"]}
        assert "valid_email" in rule_keys
        assert rule_keys["valid_email"]["violation_count"] == 5
        # Reviewer report mentioned "all pass_rate: 1.0" — Pydantic
        # null default serializes as null. Assert that's still the
        # case (no phantom 1.0).
        assert rule_keys["valid_email"].get("pass_rate_pct") is None

    def test_mcp_in_process_by_rule_reports_real_total_validations(
        self, seeded_quality_stats, monkeypatch
    ):
        """The bug. mcp_server.py:1590-1591 sums total_records from
        by=rule points (which don't have it). Must mirror the REST
        fix at routes_contracts.py:395-397."""
        from opendqv import mcp_server

        # Force in-process branch (no proxy).
        monkeypatch.setattr(mcp_server, "_remote_client", None)

        result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer",
            "by": "rule",
            "days": 1,
        }))
        body = json.loads(result[0].text)

        assert body["by"] == "rule", body
        assert body["total_validations"] == 100, (
            f"MCP in-process by=rule must derive total_validations "
            f"from by=date (100), not sum-of-zeros on rule points. "
            f"Got: {body['total_validations']}. This is N-2 P0 — the "
            f"reviewer's exact failure: 'by=rule returns "
            f"total_validations: 0' while real data exists."
        )
        assert body["data_confidence"] != "no_data", (
            f"MCP in-process by=rule data_confidence must reflect "
            f"the real record volume. Got: {body['data_confidence']!r}. "
            f"This is the SRE blind spot — rule-pivot dashboards "
            f"reading healthy when they're not."
        )
        rule_keys = {p["key"]: p for p in body["points"]}
        assert "valid_email" in rule_keys
        assert rule_keys["valid_email"]["violation_count"] == 5

    def test_rest_and_mcp_in_process_agree_on_total_validations(
        self, client, seeded_quality_stats, monkeypatch
    ):
        """Cross-surface invariant: REST and MCP in-process must
        return the same total_validations for the same by=rule
        query against the same data. Catches drift between the two
        fix sites in any future refactor."""
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)

        rest_resp = client.get(
            "/api/v1/contracts/customer/quality-trend?by=rule&days=1"
        )
        rest_body = rest_resp.json()

        mcp_result = asyncio.run(mcp_server._tool_get_quality_trend({
            "contract": "customer",
            "by": "rule",
            "days": 1,
        }))
        mcp_body = json.loads(mcp_result[0].text)

        assert rest_body["total_validations"] == mcp_body["total_validations"], (
            f"REST and MCP in-process disagree on by=rule total_validations: "
            f"REST={rest_body['total_validations']}, "
            f"MCP={mcp_body['total_validations']}. They derive from the "
            f"same engine data and must agree."
        )
        assert rest_body["data_confidence"] == mcp_body["data_confidence"], (
            f"REST and MCP in-process disagree on by=rule data_confidence: "
            f"REST={rest_body['data_confidence']!r}, "
            f"MCP={mcp_body['data_confidence']!r}."
        )
