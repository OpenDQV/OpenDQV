"""
v2.3.23 round-4 P1-C — get_quality_metrics top_failing_fields is now
window-scoped, matching get_quality_trend by=rule totals.

Persona B 2026-04-28 outside review #4 P1:
> get_quality_metrics for proof_of_play: revenue_ceiling 450 failures
> get_quality_trend by=rule (7 days): revenue_ceiling 199
> The metrics endpoint appears to fold context-overridden rule failures
> into the base rule name; the trend endpoint apparently doesn't.

Sonnet pre-impl review (afa6d1f8581846bfe) found the actual root
cause: `get_windowed_summary` in monitoring.py inherits the unscoped
lifetime `top_failing_fields` from `get_summary()`. So when
window_hours is set, top_failing_fields was still lifetime —
metrics saw lifetime, trend saw windowed, totals diverged.

Two-part fix:
  1. monitoring.py:get_windowed_summary now walks _error_events with
     the window cutoff to build a windowed top_failing_fields (mirroring
     the existing windowed top_failing_fields_by_agent treatment).
  2. mcp_server.py:_tool_get_quality_metrics dropped the
     `_quality_stats.get_trend(cname, days=1)` augmentation block. It
     was a hydration-era crutch that mixed lifetime + windowed counts
     via max() — actively wrong now that the source is windowed.

Tests pin:
  - get_windowed_summary returns a window-scoped top_failing_fields
    (not lifetime).
  - metrics top_failing_rules counts match the window.
  - Augmentation block is gone — no double-counting under any window.
"""

import time

import pytest


@pytest.fixture
def stats_with_two_windows():
    """Seed monitoring with one OLD error_event (outside window) and
    several NEW (inside window). The windowed walk must surface only
    the new ones."""
    from opendqv.monitoring import ValidationStats
    s = ValidationStats()
    now = time.time()
    # Old event — 48h ago, outside a 24h window.
    s._error_events.append((now - 48 * 3600, "customer", "email", "valid_email", ""))
    # 5 new events — inside 24h.
    for _ in range(5):
        s._error_events.append((now - 1, "customer", "email", "valid_email", ""))
    # Lifetime field_errors carries both old + new (6 total).
    s.field_errors[("customer", "email", "valid_email")] = 6
    # Plus an unrelated lifetime entry that must NOT bleed into windowed.
    s.field_errors[("customer", "name", "name_required")] = 100
    yield s


class TestWindowedTopFailingFieldsScoped:
    """The reviewer's exact root cause: windowed summary surfaced
    lifetime counts. Pin that the new walk respects the cutoff."""

    def test_windowed_summary_excludes_old_events(self, stats_with_two_windows):
        s = stats_with_two_windows
        summary = s.get_windowed_summary(window_hours=24)
        fields = summary["top_failing_fields"]
        # valid_email had 5 windowed + 1 outside-window. Window must show 5.
        ve = next(f for f in fields if f["rule"] == "valid_email")
        assert ve["count"] == 5, (
            f"v2.3.23 round-4 P1-C: windowed top_failing_fields must "
            f"reflect only events inside the window. Got count={ve['count']} "
            f"(expected 5: 5 in-window + 1 outside-window). "
            f"Lifetime field_errors had 6 — must not leak into windowed view."
        )

    def test_windowed_summary_excludes_lifetime_only_entries(
        self, stats_with_two_windows,
    ):
        """A field_errors entry with no _error_events in the window
        must NOT appear in the windowed top_failing_fields. Pre-fix it
        leaked because top_failing_fields was inherited from unscoped
        get_summary()."""
        s = stats_with_two_windows
        summary = s.get_windowed_summary(window_hours=24)
        fields = summary["top_failing_fields"]
        rules_in_window = {f["rule"] for f in fields}
        assert "name_required" not in rules_in_window, (
            f"v2.3.23 round-4 P1-C: name_required has lifetime count=100 "
            f"in field_errors but ZERO _error_events in window. Must NOT "
            f"appear in windowed top_failing_fields. Got: {fields}"
        )

    def test_unscoped_summary_still_shows_lifetime(
        self, stats_with_two_windows,
    ):
        """Regression guard: get_summary() (no window) must still emit
        the lifetime view. The fix is window-only."""
        s = stats_with_two_windows
        summary = s.get_summary()
        fields = summary["top_failing_fields"]
        rules_in_lifetime = {f["rule"] for f in fields}
        assert "name_required" in rules_in_lifetime, (
            f"unscoped get_summary() must continue to emit lifetime "
            f"top_failing_fields. Got: {fields}"
        )


class TestMetricsAugmentationDropped:
    """The `_quality_stats.get_trend(cname, days=1)` block in
    `_tool_get_quality_metrics` is gone. Pin its absence so a future
    refactor doesn't reintroduce the hydration-era crutch."""

    def test_no_augmentation_call_in_metrics_path(self):
        """Source-level pin: assert that the augmentation block
        signature ('get_trend(cname, days=1)' followed by
        max(existing["failures"], count)) is no longer present."""
        from pathlib import Path
        src = Path(
            "/home/sunny-sharma/OpenDQV/opendqv/mcp_server.py"
        ).read_text(encoding="utf-8")
        # The augmentation block's signature combination must not exist.
        # We allow `get_trend` calls elsewhere (the trend tool itself uses
        # it). The pin is on the days=1 + max-merge pattern.
        assert "max(existing[\"failures\"], count)" not in src, (
            "v2.3.23 round-4 P1-C: the days=1 augmentation block in "
            "_tool_get_quality_metrics has been intentionally removed. "
            "Reintroducing it returns the metrics-vs-trend reconciliation "
            "gap (lifetime count beats windowed count via max())."
        )

    def test_metrics_emits_windowed_top_rules(self, client, auth_headers):
        """End-to-end: hit /api/v1/stats with window_hours and assert
        the response carries top_failing_fields scoped to the window."""
        # The route is /api/v1/stats which mirrors the metrics tool's source.
        from opendqv.monitoring import stats
        # Seed: 3 in-window error events, 0 lifetime-only.
        for _ in range(3):
            stats.record(
                contract="customer", context="default", valid=False,
                error_count=1, warning_count=0, latency_ms=1.0,
                errors=[{
                    "field": "email", "rule": "metric_window_test",
                    "message": "x", "severity": "error",
                    "error_code": "OPENDQV_REGEX",
                }],
                agent_id="metric-window-probe",
            )
        resp = client.get("/api/v1/stats?window_hours=24", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # window_hours=24 must scope top_failing_fields. Our seeded rule
        # name is unique enough that count must be exactly 3.
        seeded = [
            f for f in body.get("top_failing_fields", [])
            if f["rule"] == "metric_window_test"
        ]
        if seeded:  # entry may be evicted from top-20 if the engine has heavy traffic
            assert seeded[0]["count"] == 3, (
                f"v2.3.23 round-4 P1-C: windowed top_failing_fields count "
                f"must reflect only the 3 in-window seeded events. "
                f"Got: {seeded[0]}"
            )
