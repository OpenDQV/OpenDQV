"""
v2.3.23 round-4 P1-D — _severity_map returns worst-case severity
across base + all context overrides.

Persona B 2026-04-28 outside review #4 P1:
> revenue_ceiling is severity: warning in the default context but
> severity: error in the billing context (with a different error
> message). top_failing_rules reports it as warning regardless of
> which context the validation actually ran under.

Pre-fix `_severity_map` walked only `contract.rules` (base severity),
ignoring `contract.contexts[*][rule].severity`. A CHARGE record
validated in billing context that hit the ceiling fired as a hard
error but showed up as warning on the dashboard — exactly backwards
for ops escalation.

Sonnet pre-impl review (a410fe4a545b865bc) verdict: option A —
read-side worst-case lookup across all contexts. Worst-case is the
right ops default (over-classify > under-classify). Defer per-context
emission (option C) to v2.4.

Test parametrises all three cases:
  1. Base warning + billing override error → worst-case error (promotion)
  2. Base error + billing override warning → worst-case error (demotion suppressed)
  3. No context override → base severity unchanged
"""



def _build_contract(base_severity: str, context_severity=None,
                    override_field=False):
    """Hand-roll a Contract-like object for the helper to walk."""
    from types import SimpleNamespace
    rule = SimpleNamespace(
        name="revenue_ceiling",
        field="revenue_gbp",
        cached_severity_value=base_severity,
    )
    contract = SimpleNamespace(rules=[rule], contexts={})
    if context_severity is not None:
        # By default, override is keyed by rule name (revenue_ceiling).
        # Set override_field=True to test the field-name override branch.
        key = "revenue_gbp" if override_field else "revenue_ceiling"
        contract.contexts = {
            "billing": {key: {"severity": context_severity}},
        }
    return contract


# ── _build_worst_case_severity_map (REST path) ─────────────────────────

class TestRestSeverityMapWorstCase:

    def test_promotion_warning_to_error(self):
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        contract = _build_contract("warning", "error")
        sev_map = _build_worst_case_severity_map(contract)
        assert sev_map["revenue_ceiling"] == "error", (
            f"v2.3.23 round-4 P1-D: rule with base warning + context error "
            f"override must surface as error (worst-case). Pre-fix the "
            f"dashboard showed warning, masking live errors. Got: {sev_map}"
        )

    def test_demotion_error_to_warning_keeps_error(self):
        """If a context demotes (default error → context warning), the
        worst-case lookup keeps error. Sonnet's directive: over-classify
        is acceptable for an ops dashboard; the alternative is masking
        a real error elsewhere."""
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        contract = _build_contract("error", "warning")
        sev_map = _build_worst_case_severity_map(contract)
        assert sev_map["revenue_ceiling"] == "error", (
            f"worst-case must keep error when default is error and a "
            f"context demotes to warning. Got: {sev_map}"
        )

    def test_no_override_keeps_base(self):
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        contract = _build_contract("warning")
        sev_map = _build_worst_case_severity_map(contract)
        assert sev_map["revenue_ceiling"] == "warning"

    def test_field_name_override_promotes(self):
        """Branch-2 of override resolution: override keyed by FIELD name
        applies to every rule on that field. Worst-case lookup must walk
        this branch too."""
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        contract = _build_contract("warning", "error", override_field=True)
        sev_map = _build_worst_case_severity_map(contract)
        assert sev_map["revenue_ceiling"] == "error", (
            f"field-name override (branch-2) must escalate severity on "
            f"every rule on that field. Got: {sev_map}"
        )

    def test_empty_contract_returns_empty_map(self):
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        from types import SimpleNamespace
        empty = SimpleNamespace(rules=[], contexts={})
        assert _build_worst_case_severity_map(empty) == {}

    def test_none_contract_returns_empty_map(self):
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        assert _build_worst_case_severity_map(None) == {}


# ── _severity_map (in-process MCP path) ────────────────────────────────

class TestMcpSeverityMapWorstCase:
    """Mirror set against the in-process MCP path. Both surfaces must
    emit the same worst-case severity per the dual-path discipline."""

    def test_real_contract_revenue_ceiling_promotes_to_error(self):
        """End-to-end check against the bundled proof_of_play contract.
        revenue_ceiling has base severity warning + billing context
        override severity=error. Worst-case must surface error."""
        from opendqv.mcp_server import _severity_map
        sev_map = _severity_map("proof_of_play")
        assert sev_map.get("revenue_ceiling") == "error", (
            f"v2.3.23 round-4 P1-D: bundled proof_of_play contract has "
            f"revenue_ceiling base=warning, billing-context override=error. "
            f"_severity_map must report worst-case (error). Got: "
            f"{sev_map.get('revenue_ceiling')!r}"
        )
        assert sev_map.get("dwell_seconds_max") == "error", (
            f"dwell_seconds_max same pattern: warning in default, error "
            f"in billing. Worst-case must be error. Got: "
            f"{sev_map.get('dwell_seconds_max')!r}"
        )

    def test_unknown_contract_returns_empty_map(self):
        from opendqv.mcp_server import _severity_map
        assert _severity_map("__nonexistent_contract__") == {}

    def test_rest_and_mcp_paths_agree(self):
        """Dual-path discipline: REST and in-process MCP must produce
        identical severity maps for the same contract."""
        from opendqv.api.routes_analytics import _build_worst_case_severity_map
        from opendqv.api import deps as _d
        from opendqv.mcp_server import _severity_map as mcp_sev_map

        contract = _d.registry.get("proof_of_play")
        rest_map = _build_worst_case_severity_map(contract)
        mcp_map = mcp_sev_map("proof_of_play")
        assert rest_map == mcp_map, (
            f"v2.3.23 round-4 P1-D: REST and in-process MCP paths must "
            f"emit identical severity maps. REST: {rest_map}; "
            f"MCP: {mcp_map}"
        )
