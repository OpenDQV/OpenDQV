"""
DuckDB-backed OLAP analytics over the SQLite quality_stats table.

OLTP/OLAP split:
  SQLite  = OLTP write path  (QualityStats.record_batch  in core/quality_stats.py)
  DuckDB  = OLAP read path   (QualityAnalytics here)

No data duplication — DuckDB attaches the SQLite file directly via its
built-in SQLite extension.  Both classes point at the same DB_PATH.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class QualityAnalytics:
    """DuckDB-backed OLAP queries over the SQLite quality_stats table."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _conn(self):
        import duckdb

        conn = duckdb.connect()
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        conn.execute(f"ATTACH '{self._db_path}' AS qs (TYPE SQLITE)")
        return conn

    def _since(self, days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # ── Public API ────────────────────────────────────────────────────

    def cross_contract_summary(self, days: int = 7) -> list[dict]:
        """
        Pass rate and failure count for every contract in the last N days.

        Returns a list of dicts sorted by pass_rate_pct ascending (worst first):
          {contract, total_records, passed, failed, pass_rate_pct}
        """
        since = self._since(days)
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    contract_name,
                    CAST(SUM(total_records) AS INTEGER) AS total_records,
                    CAST(SUM(passed)        AS INTEGER) AS passed,
                    CAST(SUM(failed)        AS INTEGER) AS failed
                FROM   qs.quality_stats
                WHERE  recorded_at >= ?
                GROUP  BY contract_name
                ORDER  BY (CAST(SUM(passed) AS REAL) / NULLIF(SUM(total_records), 0)) ASC NULLS LAST
                """,
                [since],
            ).fetchall()
        finally:
            conn.close()

        result = []
        for contract_name, total, passed, failed in rows:
            # v2.3.18 Q3: single canonical pass_rate_pct (percent 0–100, 1dp).
            # v2.3.22 Cluster F: empty contract → null (signal of no data,
            # not 0% / not 100%).
            pass_rate_pct = round(passed / total * 100, 1) if total else None
            result.append(
                {
                    "contract": contract_name,
                    "total_records": int(total),
                    "passed": int(passed),
                    "failed": int(failed),
                    "pass_rate_pct": pass_rate_pct,
                }
            )
        return result

    def rule_heatmap(self, days: int = 7) -> list[dict]:
        """
        Top failing rules across all contracts in the last N days, ranked by failure count.

        Returns a list of up to 50 dicts:
          {contract, rule, failure_count}
        """
        since = self._since(days)
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT contract_name, rule_failure_counts
                FROM   qs.quality_stats
                WHERE  recorded_at >= ?
                  AND  rule_failure_counts IS NOT NULL
                """,
                [since],
            ).fetchall()
        finally:
            conn.close()

        # Aggregate rule failures in Python — JSON column → per-(contract, rule) counts
        aggregated: dict[tuple, int] = {}
        for contract_name, rfc_json in rows:
            if not rfc_json or rfc_json in ("{}", "null"):
                continue
            try:
                rfc = json.loads(rfc_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for rule, count in rfc.items():
                key = (contract_name, rule)
                aggregated[key] = aggregated.get(key, 0) + int(count)

        result = sorted(
            [
                {"contract": k[0], "rule": k[1], "failure_count": v}
                for k, v in aggregated.items()
            ],
            key=lambda x: x["failure_count"],
            reverse=True,
        )
        return result[:50]

    def rule_failure_velocity(
        self,
        contract_name: str,
        window_hours: int = 24,
        bucket_minutes: int = 5,
    ) -> dict:
        """
        Time-series failure counts per rule for a single contract.

        Shows whether failures are accelerating or decelerating — the difference
        between a slow drip and a sudden spike. Returns the top 5 rules only
        (consistent with rule_heatmap cap).

        Returns:
          {
            "contract": str,
            "window_hours": int,
            "bucket_minutes": int,
            "series": {
              "<rule>": [{"bucket": "2026-03-26T10:00Z", "failures": 12}, ...]
            }
          }
        """
        since = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT recorded_at, rule_failure_counts
                FROM   qs.quality_stats
                WHERE  contract_name = ?
                  AND  recorded_at   >= ?
                  AND  rule_failure_counts IS NOT NULL
                ORDER  BY recorded_at ASC
                """,
                [contract_name, since],
            ).fetchall()
        finally:
            conn.close()

        # Identify top 5 rules by total failures across the window
        rule_totals: dict[str, int] = {}
        for _ts, rfc_json in rows:
            if not rfc_json or rfc_json in ("{}", "null"):
                continue
            try:
                rfc = json.loads(rfc_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for rule, count in rfc.items():
                rule_totals[rule] = rule_totals.get(rule, 0) + int(count)

        top_rules = sorted(rule_totals, key=lambda r: rule_totals[r], reverse=True)[:5]
        if not top_rules:
            return {
                "contract": contract_name,
                "window_hours": window_hours,
                "bucket_minutes": bucket_minutes,
                "series": {},
            }

        # Build per-rule time-series bucketed by bucket_minutes
        bucket_secs = bucket_minutes * 60
        series: dict[str, dict[str, int]] = {rule: {} for rule in top_rules}

        for recorded_at, rfc_json in rows:
            if not rfc_json or rfc_json in ("{}", "null"):
                continue
            try:
                rfc = json.loads(rfc_json)
            except (json.JSONDecodeError, TypeError):
                continue
            # Parse timestamp and snap to bucket boundary
            try:
                ts = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            epoch = ts.timestamp()
            bucket_epoch = int(epoch // bucket_secs) * bucket_secs
            bucket_label = (
                datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%MZ")
            )
            for rule in top_rules:
                if rule in rfc:
                    series[rule][bucket_label] = (
                        series[rule].get(bucket_label, 0) + int(rfc[rule])
                    )

        # Convert to sorted lists
        formatted_series = {}
        for rule in top_rules:
            formatted_series[rule] = [
                {"bucket": b, "failures": c}
                for b, c in sorted(series[rule].items())
            ]

        return {
            "contract": contract_name,
            "window_hours": window_hours,
            "bucket_minutes": bucket_minutes,
            "series": formatted_series,
        }

    # ── Observation-only analytics ─────────────────────────────────────

    def observation_summary(self, days: int = 7, contract: str | None = None) -> dict:
        """
        Cross-contract summary of observation-only runs.

        Returns: total_observation_records, would_have_failed_count,
                 would_have_passed_count, enforcement_readiness_pct,
                 by_contract list.
        """
        since = self._since(days)
        conn = self._conn()
        try:
            params: list = [since]
            contract_filter = ""
            if contract:
                contract_filter = " AND contract_name = ?"
                params.append(contract)

            # Per-contract breakdown
            rows = conn.execute(
                f"""
                SELECT
                    contract_name,
                    CAST(SUM(total_records) AS INTEGER) AS total_records,
                    CAST(SUM(passed)        AS INTEGER) AS passed,
                    CAST(SUM(failed)        AS INTEGER) AS failed
                FROM   qs.quality_stats
                WHERE  recorded_at >= ?
                  AND  mode = 'observation_only'
                  {contract_filter}
                GROUP  BY contract_name
                ORDER  BY contract_name
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

        by_contract = []
        total_obs = 0
        total_failed = 0
        total_passed = 0
        for contract_name, total, passed, failed in rows:
            total_obs += int(total)
            total_passed += int(passed)
            total_failed += int(failed)
            readiness = round(100 * passed / total, 1) if total else 0.0
            by_contract.append({
                "contract": contract_name,
                "total": int(total),
                "would_have_passed": int(passed),
                "would_have_failed": int(failed),
                "enforcement_readiness_pct": readiness,
            })

        overall_readiness = round(100 * total_passed / total_obs, 1) if total_obs else 0.0

        return {
            "days": days,
            "contract": contract,
            "total_observation_records": total_obs,
            "would_have_failed_count": total_failed,
            "would_have_passed_count": total_passed,
            "enforcement_readiness_pct": overall_readiness,
            "by_contract": by_contract,
        }

    def observation_trend(self, contract: str, days: int = 7) -> list[dict]:
        """
        Daily time-series for one contract in observation mode.

        Returns: list of {date, total, would_have_failed, would_have_passed}.
        """
        since = self._since(days)
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT
                    CAST(recorded_at AS DATE) AS day,
                    CAST(SUM(total_records) AS INTEGER) AS total,
                    CAST(SUM(passed)        AS INTEGER) AS passed,
                    CAST(SUM(failed)        AS INTEGER) AS failed
                FROM   qs.quality_stats
                WHERE  contract_name = ?
                  AND  recorded_at  >= ?
                  AND  mode = 'observation_only'
                GROUP  BY day
                ORDER  BY day
                """,
                [contract, since],
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "date": str(day),
                "total": int(total),
                "would_have_failed": int(failed),
                "would_have_passed": int(passed),
            }
            for day, total, passed, failed in rows
        ]

    def observation_fields(self, contract: str, days: int = 7) -> list[dict]:
        """
        Top failing rules/fields for a contract in observation mode.

        Returns: list of {rule, field, count} sorted by count desc.
        Uses the same Python-side JSON parsing pattern as rule_heatmap().
        """
        since = self._since(days)
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT rule_failure_counts
                FROM   qs.quality_stats
                WHERE  contract_name = ?
                  AND  recorded_at  >= ?
                  AND  mode = 'observation_only'
                  AND  rule_failure_counts IS NOT NULL
                """,
                [contract, since],
            ).fetchall()
        finally:
            conn.close()

        # Aggregate rule failures in Python — same pattern as rule_heatmap()
        aggregated: dict[str, int] = {}
        for (rfc_json,) in rows:
            if not rfc_json or rfc_json in ("{}", "null"):
                continue
            try:
                rfc = json.loads(rfc_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for rule, count in rfc.items():
                aggregated[rule] = aggregated.get(rule, 0) + int(count)

        result = sorted(
            [
                {"rule": rule, "field": rule, "count": v}
                for rule, v in aggregated.items()
            ],
            key=lambda x: x["count"],
            reverse=True,
        )
        return result[:50]
