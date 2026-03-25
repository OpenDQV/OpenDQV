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

        Returns a list of dicts sorted by pass_rate ascending (worst first):
          {contract, total_records, passed, failed, pass_rate, pass_rate_pct}
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
            pass_rate = round(passed / total, 4) if total else 0.0
            result.append(
                {
                    "contract": contract_name,
                    "total_records": int(total),
                    "passed": int(passed),
                    "failed": int(failed),
                    "pass_rate": pass_rate,
                    "pass_rate_pct": round(pass_rate * 100, 1),
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
