"""
v2.3.23 post-release fix C1 — hydration completeness.

Persona B inside-view 2026-04-28 surfaced four symptoms with one root
cause:

  * total_error_violations: 0 despite 409 failed records (P1)
  * latency block all null with sample_size: 0 despite 5043 validations (P1)
  * recent_history: [] despite traffic (P1)
  * top_failing_fields: [] globally but populated per-agent (P2)

Trace: `hydrate_stats_from_persistent_store` at monitoring.py:574-659
populates `_events` and `_error_events` deques only. It does NOT
populate:

  * `self.totals[key]["errors"]` / `["warnings"]` — counter feed for
    total_error_violations / total_warning_violations.
  * `self.field_errors` — global rule rollup for top_failing_fields.
  * `self.severity_counts` — dimensions.by_severity.
  * `self.history` — recent_history ring buffer.
  * `self._latencies` — latency stats.

The post-restart engine therefore reports zero/empty for all five
even though the underlying SQLite quality_stats has the data.

What we CAN hydrate from quality_stats:
  - totals[key]["errors"]: sum of rule_failure_counts.values() per row
  - field_errors[(contract, "?", rule)]: from rule_failure_counts
  - history: synthesise one ring-buffer entry per row

What we CANNOT hydrate (data not in quality_stats):
  - severity_counts: only error counts are stored, not warning counts
    (per-rule severity is not persisted alongside the count). v2.4
    schema work could split this, but partial-hydration today is
    correct: we hydrate errors, leave warnings at zero, and document
    the limit.
  - _latencies: no per-record latency persisted. Document the gap;
    next-restart latency populates from live traffic only.

Customer impact (regulator-facing): post-restart dashboards show
flat-zero violation counters and empty trend rings. Reviewer's exact
framing: "After every restart, customers will see misleading
'improvement'."
"""

import sqlite3



def _seed_quality_stats(db_path: str):
    """Write a representative row to quality_stats so we can prove
    hydration populates the in-memory aggregates."""
    conn = sqlite3.connect(db_path)
    # Use the same schema migration the engine runs. Simplest path:
    # invoke QualityStats once which creates and migrates the table.
    from opendqv.core.quality_stats import QualityStats
    qs = QualityStats(db_path)
    qs.record_batch(
        contract_name="banking_transaction", contract_version="1.0",
        context="default",
        total=10, passed=7, failed=3,
        rule_failure_counts={"valid_amount": 2, "currency_lookup": 1},
        agent_id="core-banking-feed", mode="enforcement",
        event_id="evt-1",
        caller_principal="alice@bank.example.com",
    )
    conn.close()


class TestHydrationPopulatesAggregates:
    """C1: hydration must populate every aggregate that feeds /stats
    output, not just the deques."""

    def test_hydrate_populates_totals_errors_counter(self, tmp_path):
        """total_error_violations on get_summary must reflect persisted
        rule_failure_counts after restart."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)

        s = ValidationStats()
        result = hydrate_stats_from_persistent_store(s, db)
        assert result["rows_read"] == 1, result

        summary = s.get_summary()
        # 2 + 1 = 3 rule violations recorded in rule_failure_counts.
        assert summary["total_error_violations"] == 3, (
            f"v2.3.23 C1: hydration must populate totals.errors. "
            f"Persisted rule_failure_counts sum to 3 but get_summary "
            f"returned total_error_violations={summary['total_error_violations']}. "
            f"Reviewer's exact symptom: post-restart dashboards show "
            f"flat zero despite real failures."
        )

    def test_hydrate_populates_totals_pass_fail(self, tmp_path):
        """Sanity baseline — pass/fail were already populated via the
        events-deque path. Guard against regression."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        summary = s.get_windowed_summary(window_hours=24)
        assert summary["total_pass"] == 7
        assert summary["total_fail"] == 3
        assert summary["total_validations"] == 10

    def test_hydrate_populates_field_errors_for_top_failing_fields(self, tmp_path):
        """top_failing_fields global rollup must reflect hydrated
        rule_failure_counts. Real field names are not in quality_stats
        (only rule names) so the field is the F-K sentinel '?' with
        provenance unavailable — but the rule + count must be present."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        summary = s.get_summary()
        rules = {f["rule"]: f["count"] for f in summary["top_failing_fields"]}
        assert "valid_amount" in rules, summary["top_failing_fields"]
        assert "currency_lookup" in rules
        assert rules["valid_amount"] == 2
        assert rules["currency_lookup"] == 1

    def test_hydrate_populates_recent_history(self, tmp_path):
        """recent_history ring must surface hydrated rows so
        post-restart trend dashboards aren't empty."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        summary = s.get_summary()
        assert len(summary["recent_history"]) > 0, (
            f"v2.3.23 C1: hydration must populate recent_history so "
            f"post-restart dashboards reflect real traffic. Got: "
            f"{summary['recent_history']!r}"
        )
        # Each hydrated history entry should at least carry contract
        # name and a hydration marker.
        first = summary["recent_history"][0]
        assert first.get("contract") == "banking_transaction"

    def test_hydrate_idempotent_no_double_count(self, tmp_path):
        """Sonnet's pre-impl review: the weak `<= 2x + 1` bound was
        meaningless. The strict assertion is `second == first` —
        hydration must be exactly idempotent via the _hydrated guard."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        result1 = hydrate_stats_from_persistent_store(s, db)
        first_summary = s.get_summary()
        assert result1["rows_read"] == 1
        assert s._hydrated is True

        # Second hydration: must early-return.
        result2 = hydrate_stats_from_persistent_store(s, db)
        assert result2.get("already_hydrated") is True, result2
        assert result2["rows_read"] == 0

        second_summary = s.get_summary()
        # Strict equality — no double-counting permitted.
        assert second_summary["total_error_violations"] == first_summary["total_error_violations"], (
            f"Hydration is NOT idempotent. First: "
            f"{first_summary['total_error_violations']}, "
            f"Second: {second_summary['total_error_violations']}. "
            f"_hydrated guard not effective."
        )
        assert second_summary["total_validations"] == first_summary["total_validations"]
        # field_errors and severity_counts also strict-equal.
        assert (
            second_summary["top_failing_fields"]
            == first_summary["top_failing_fields"]
        )

    def test_hydrate_populates_severity_counts(self, tmp_path):
        """Sonnet's pre-impl review item D: severity_counts must
        attribute hydrated violations to the 'error' severity bucket
        so dimensions.by_severity is internally consistent with
        total_error_violations. quality_stats does not store warning
        severity, so warnings stay at zero — by design."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        summary = s.get_summary()
        by_severity = summary["dimensions"]["by_severity"]
        assert by_severity["error"] == 3, (
            f"Sonnet C1 item D: severity_counts must hydrate the "
            f"error bucket from rule_failure_counts sum (=3). "
            f"Got: {by_severity}"
        )
        # Internal consistency: by_severity.error == total_error_violations
        assert by_severity["error"] == summary["total_error_violations"]


class TestWindowedSummaryScopesViolationCounters:
    """Sonnet's pre-impl review item E (BLOCKING): get_windowed_summary
    inherited total_error_violations from get_summary, leaking the
    unscoped self.totals.errors counter into a window-scoped response.
    With hydration completeness, totals.errors now contains ALL hydrated
    + live data — so the unscoped value is much larger than the windowed
    record counts. Dual-source inconsistency. Override from windowed
    _error_events deque, same source as top_failing_fields_by_agent."""

    def test_windowed_summary_violation_counter_scoped_to_window(self, tmp_path):
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        from datetime import datetime, timezone, timedelta
        # Seed a row with timestamp WAY OUTSIDE the 1h window we'll query.
        # Hydrated rows that fall outside the cutoff must NOT contribute
        # to total_error_violations on a windowed query.
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "w.db")
        qs = QualityStats(db)
        # Manually insert a row 25h old so a 1h-window query excludes it.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        conn = qs._connect()
        import json as _json
        conn.execute(
            "INSERT INTO quality_stats (event_id, contract_name, contract_version, "
            "context, recorded_at, total_records, passed, failed, pass_rate_pct, "
            "rule_failure_counts, agent_id, mode, caller_principal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("evt-old", "banking_transaction", "1.0", "default", old_ts,
             10, 5, 5, 50.0, _json.dumps({"valid_amount": 5}), "old-feed",
             "enforcement", "alice"),
        )
        conn.commit()

        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        # Unscoped get_summary surfaces 5 hydrated violations.
        unscoped = s.get_summary()
        assert unscoped["total_error_violations"] == 5

        # 1h-window query must surface ZERO violations (old row outside).
        windowed = s.get_windowed_summary(window_hours=1)
        assert windowed["total_error_violations"] == 0, (
            f"v2.3.23 C1 BLOCKING (Sonnet item E): get_windowed_summary "
            f"leaked unscoped total_error_violations into a window-scoped "
            f"response. Hydrated row was 25h old; 1h window must exclude "
            f"its violations. Got: {windowed['total_error_violations']}. "
            f"Dual-source inconsistency — same CRT170-J family the team "
            f"has caught repeatedly."
        )
        assert windowed["total_validations"] == 0
        # dimensions.by_severity.error must agree with total_error_violations
        assert windowed["dimensions"]["by_severity"]["error"] == 0

    def test_windowed_summary_includes_in_window_violations(self, tmp_path):
        """Symmetric guard: rows INSIDE the window must be counted."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "w.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        # 24h window — seeded row is current, so inside.
        windowed = s.get_windowed_summary(window_hours=24)
        assert windowed["total_error_violations"] == 3, windowed
        assert windowed["total_validations"] == 10
        assert windowed["dimensions"]["by_severity"]["error"] == 3


class TestHydrationDocumentsLatencyGap:
    """latency.sample_size remaining 0 after hydration is by design —
    quality_stats schema has no latency column. Test asserts the
    documented behaviour: the latency block is shaped correctly with
    None values rather than missing or crashing."""

    def test_latency_block_shape_after_hydration(self, tmp_path):
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        summary = s.get_summary()
        latency = summary["latency"]
        # All keys present, all values None or 0 — never absent.
        assert "avg_ms" in latency
        assert "sample_size" in latency
        # Documented gap: latency does not hydrate from quality_stats.
        # That's a v2.4 schema item.

    def test_latency_block_populated_after_live_record(self, tmp_path):
        """After hydration, a live record() call MUST populate
        latency stats — proving that the hydration limitation doesn't
        prevent forward-going population."""
        from opendqv.monitoring import (
            ValidationStats, hydrate_stats_from_persistent_store,
        )
        db = str(tmp_path / "h.db")
        _seed_quality_stats(db)
        s = ValidationStats()
        hydrate_stats_from_persistent_store(s, db)
        s.record(
            contract="banking_transaction", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=2.5,
            agent_id="live-agent",
        )
        summary = s.get_summary()
        assert summary["latency"]["sample_size"] >= 1
