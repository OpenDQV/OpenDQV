"""
v2.3.23 P2-11 — hydration normalizes legacy `ctx_{context}_{rule}`
rule names to the base rule name when a matching rule exists.

Persona B 2026-04-28: "A rule that fires under the billing context
is reported as `revenue_ceiling` in the validate response and as
`ctx_billing_revenue_ceiling` in the metrics response. Customer
impact: an engineer searching the contract for a rule name they
saw in a dashboard finds nothing."

Root cause: legacy persisted rule_failure_counts (written by
pre-v2.3.x engine versions where the override-matching logic was
buggy) carry the synthesised `ctx_{context}_{rule}` name. The
current engine emits the correct base rule name. C1 hydration
ingests the legacy rule names verbatim, surfacing them in metrics
indefinitely.

Sonnet's pre-impl review (a3b8052e9904f4ab4): Option A — normalize
at hydration with two-condition guard. Strip `ctx_{context}_`
prefix when (a) the prefix-stripped name matches a rule on the
current contract AND (b) the named context is declared on the
contract. Otherwise leave the name unchanged (preserves genuinely
synthetic branch-3 rules).
"""

import json



def _seed_legacy_row(db_path: str, contract: str, rule_name: str, count: int = 1):
    """Seed a quality_stats row with a specific rule name in
    rule_failure_counts. Simulates legacy data from earlier engine
    versions."""
    import sqlite3
    from opendqv.core.quality_stats import QualityStats
    QualityStats(db_path)  # ensure schema
    conn = sqlite3.connect(db_path)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO quality_stats (event_id, contract_name, contract_version, "
        "context, recorded_at, total_records, passed, failed, pass_rate_pct, "
        "rule_failure_counts, agent_id, mode, caller_principal) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("evt-legacy", contract, "1.0", "default", ts,
         1, 0, 1, 0.0, json.dumps({rule_name: count}),
         "test", "enforcement", "alice"),
    )
    conn.commit()
    conn.close()


class TestHydrationNormalizesLegacyRuleNames:
    def test_ctx_prefix_stripped_when_matching_base_rule(self, tmp_path):
        """Legacy 'ctx_billing_revenue_ceiling' for proof_of_play
        must be normalized to 'revenue_ceiling' on hydration —
        proof_of_play has a base rule named revenue_ceiling AND
        declares context billing."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        import opendqv.api.deps as _d

        db = str(tmp_path / "h.db")
        _seed_legacy_row(db, "proof_of_play", "ctx_billing_revenue_ceiling", count=2)

        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db, registry=_d.registry)

        summary = s.get_summary()
        rule_names = {f["rule"] for f in summary["top_failing_fields"]}

        assert "revenue_ceiling" in rule_names, (
            f"v2.3.23 P2-11: legacy 'ctx_billing_revenue_ceiling' must "
            f"normalize to 'revenue_ceiling' on hydration when the "
            f"contract has a base rule by that name. Got: {rule_names}"
        )
        assert "ctx_billing_revenue_ceiling" not in rule_names, (
            f"v2.3.23 P2-11: legacy prefix must be stripped on hydration. "
            f"Got: {rule_names}"
        )

    def test_genuine_ctx_synthetic_rule_preserved(self, tmp_path):
        """When the prefix-stripped name does NOT match a base rule,
        the rule name is preserved (it's a genuine branch-3 synthetic
        rule). Negative case: customer contract has no rule named
        'unknown_rule', no context 'otherctx' declared."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        import opendqv.api.deps as _d

        db = str(tmp_path / "h.db")
        _seed_legacy_row(db, "customer", "ctx_otherctx_unknown_rule", count=1)

        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db, registry=_d.registry)

        summary = s.get_summary()
        rule_names = {f["rule"] for f in summary["top_failing_fields"]}

        assert "ctx_otherctx_unknown_rule" in rule_names, (
            f"v2.3.23 P2-11: rule name with no matching base rule on "
            f"the contract must be preserved (genuinely synthetic). "
            f"Got: {rule_names}"
        )

    def test_normalization_skipped_when_registry_not_provided(self, tmp_path):
        """Backwards-compat: hydration without a registry argument
        (e.g. test fixtures, programmatic use) must not crash. Names
        stay unchanged when the registry is None."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )

        db = str(tmp_path / "h.db")
        _seed_legacy_row(db, "proof_of_play", "ctx_billing_revenue_ceiling", count=1)

        s = ValidationStats()
        # Call without registry — should still work, just no normalization.
        result = hydrate_stats_from_persistent_store(s, db)
        assert result["rows_read"] == 1
        summary = s.get_summary()
        rule_names = {f["rule"] for f in summary["top_failing_fields"]}
        # No normalization without registry — legacy name preserved.
        assert "ctx_billing_revenue_ceiling" in rule_names

    def test_normalization_only_when_context_declared(self, tmp_path):
        """Two-condition guard: normalize only when (a) prefix-stripped
        name matches a base rule AND (b) the named context is declared.
        Negative case: customer has rule 'name_required' but no context
        'spam' declared. 'ctx_spam_name_required' should NOT normalize."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        import opendqv.api.deps as _d

        db = str(tmp_path / "h.db")
        _seed_legacy_row(db, "customer", "ctx_spam_name_required", count=1)

        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db, registry=_d.registry)

        summary = s.get_summary()
        rule_names = {f["rule"] for f in summary["top_failing_fields"]}
        # 'spam' is not a declared context on customer — preserve as-is.
        assert "ctx_spam_name_required" in rule_names, (
            f"Two-condition guard: must NOT normalize when context "
            f"'spam' is not declared on contract 'customer'. "
            f"Got: {rule_names}"
        )
