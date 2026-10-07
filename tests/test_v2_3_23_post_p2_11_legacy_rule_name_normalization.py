"""
Hydration of persisted rule names — 3.0.0.

v2.3.23 P2-11 added a `ctx_{context}_{rule}` → `{rule}` normalisation at
hydration, guarded on the contract declaring the context. 3.0.0 removes
`contexts:` entirely (and with it the `registry=` parameter of
`hydrate_stats_from_persistent_store` and the ctx_ heuristics), so
hydration now carries every persisted rule name through verbatim.
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


class TestHydrationKeepsRuleNamesVerbatim:
    def test_ctx_prefixed_name_is_not_rewritten(self, tmp_path):
        """A legacy `ctx_billing_revenue_ceiling` row hydrates under exactly
        that name — no context is declared anywhere to justify a rewrite."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )

        db = str(tmp_path / "h.db")
        _seed_legacy_row(db, "proof_of_play", "ctx_billing_revenue_ceiling", count=1)

        s = ValidationStats()
        result = hydrate_stats_from_persistent_store(s, db)
        assert result["rows_read"] == 1
        summary = s.get_summary()
        rule_names = {f["rule"] for f in summary["top_failing_fields"]}
        assert "ctx_billing_revenue_ceiling" in rule_names
        assert "revenue_ceiling" not in rule_names
