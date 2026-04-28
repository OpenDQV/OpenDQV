"""
v2.3.23 P2-14 — get_quality_trend(days=N) returned N+1 buckets.

Persona B 2026-04-28: "Asking days=7 returned 8 daily buckets.
Off-by-one. Customer impact: minor UX confusion in windowed reporting."

Root cause: `since = datetime.now() - timedelta(days=days)` against
midnight-aligned recorded_at strings produced a partial 8th bucket
on the trailing day.

Sonnet's pre-impl review (a968e64aaabfd31bf):
  - Snap cutoff to start-of-(today - days + 1) UTC. For days=7
    today=04-28 → cutoff 04-22T00:00:00Z; buckets 22..28 = 7.
  - Convention: "last 7 days" includes today + 6 previous.
  - Three-assertion recurrence test: count ≤ days, last bucket is
    today, first bucket is (today - days + 1).
  - Scope tight to get_quality_trend; do not change get_windowed_totals
    (different semantics: hours, not calendar days).
"""

from datetime import datetime, timezone, timedelta

import pytest


class TestTrendDaysBoundary:
    """v2.3.23 P2-14: days=N must return at most N daily buckets,
    with today as the most recent and (today - days + 1) as the
    earliest."""

    def _seed_one_per_day(self, db_path: str, contract: str, span_days: int):
        """Seed one quality_stats row per day for `span_days` days
        ending today. Returns the list of seeded date strings."""
        import sqlite3
        import json
        from opendqv.core.quality_stats import QualityStats
        QualityStats(db_path)  # ensure schema
        conn = sqlite3.connect(db_path)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        seeded = []
        for offset in range(span_days):
            d = today - timedelta(days=offset)
            ts = (d + timedelta(hours=12)).isoformat()  # mid-day
            conn.execute(
                "INSERT INTO quality_stats (event_id, contract_name, contract_version, "
                "context, recorded_at, total_records, passed, failed, pass_rate_pct, "
                "rule_failure_counts, agent_id, mode, caller_principal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"evt-{offset}", contract, "1.0", "default", ts,
                 1, 1, 0, 100.0, json.dumps({}), "test", "enforcement", "alice"),
            )
            seeded.append(d.strftime("%Y-%m-%d"))
        conn.commit()
        conn.close()
        return seeded

    def test_days_7_returns_at_most_7_buckets(self, tmp_path):
        """Seed 14 days of data; ask days=7; assert count <= 7."""
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "trend.db")
        self._seed_one_per_day(db, "trend_test", span_days=14)
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=7)
        assert len(points) <= 7, (
            f"v2.3.23 P2-14: days=7 must return at most 7 buckets. "
            f"Got {len(points)}: {[p.get('date') for p in points]}"
        )

    def test_days_7_includes_today_as_most_recent(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "trend.db")
        self._seed_one_per_day(db, "trend_test", span_days=14)
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=7)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert points[-1]["date"] == today_str, (
            f"Most-recent bucket must be today ({today_str}). "
            f"Got: {points[-1].get('date')}"
        )

    def test_days_7_first_bucket_is_today_minus_six(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "trend.db")
        self._seed_one_per_day(db, "trend_test", span_days=14)
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=7)
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expected_first = (today - timedelta(days=6)).strftime("%Y-%m-%d")
        assert points[0]["date"] == expected_first, (
            f"v2.3.23 P2-14: earliest bucket must be (today - days + 1) = "
            f"{expected_first}. Got: {points[0].get('date')}"
        )

    @pytest.mark.parametrize("days", [1, 3, 7, 14, 30])
    def test_days_N_returns_at_most_N_buckets(self, tmp_path, days):
        """Sweep across realistic dashboard window sizes."""
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / f"trend_{days}.db")
        # Seed days+5 to ensure data outside the window.
        self._seed_one_per_day(db, "trend_test", span_days=days + 5)
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=days)
        assert len(points) <= days, (
            f"days={days} returned {len(points)} buckets: "
            f"{[p.get('date') for p in points]}"
        )
