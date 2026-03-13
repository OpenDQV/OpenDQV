"""
Quality statistics persistence for OpenDQV.

Records batch validation outcomes to enable quality trend queries.
One row per batch call — aggregated by the GET endpoint.
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS quality_stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_name    TEXT    NOT NULL,
    contract_version TEXT    NOT NULL,
    context          TEXT    NOT NULL DEFAULT 'default',
    recorded_at      TEXT    NOT NULL,
    total_records    INTEGER NOT NULL,
    passed           INTEGER NOT NULL,
    failed           INTEGER NOT NULL,
    pass_rate        REAL    NOT NULL,
    rule_failure_counts TEXT NOT NULL DEFAULT '{}'
)
"""

_INSERT = """
INSERT INTO quality_stats
    (contract_name, contract_version, context, recorded_at,
     total_records, passed, failed, pass_rate, rule_failure_counts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_SINCE = """
SELECT contract_name, contract_version, context, recorded_at,
       total_records, passed, failed, pass_rate, rule_failure_counts
FROM   quality_stats
WHERE  contract_name = ?
  AND  recorded_at   >= ?
  AND  (? IS NULL OR context = ?)
ORDER  BY recorded_at ASC
"""


class QualityStats:
    """SQLite-backed quality statistics store."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            return self._mem_conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        conn = self._connect()
        try:
            conn.execute(_CREATE_TABLE)
            conn.commit()
        finally:
            if self._db_path != ":memory:":
                conn.close()

    def record_batch(
        self,
        contract_name: str,
        contract_version: str,
        context: Optional[str],
        total: int,
        passed: int,
        failed: int,
        rule_failure_counts: dict,
    ) -> None:
        """Persist one batch validation result."""
        pass_rate = passed / total if total > 0 else 1.0
        now = datetime.now(timezone.utc).isoformat()
        ctx = context or "default"
        conn = self._connect()
        try:
            conn.execute(_INSERT, (
                contract_name, contract_version, ctx, now,
                total, passed, failed, pass_rate,
                json.dumps(rule_failure_counts),
            ))
            conn.commit()
        except Exception:
            logger.exception("Failed to record quality stats")
        finally:
            if self._db_path != ":memory:":
                conn.close()

    def get_trend(
        self,
        contract_name: str,
        days: int = 7,
        context: Optional[str] = None,
    ) -> list[dict]:
        """
        Return daily aggregated quality statistics for the last N days.

        Each dict has: date, total_records, passed, failed, pass_rate,
        top_failing_rules (merged rule_failure_counts for that day).
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(_SELECT_SINCE, (
                contract_name, since, context, context,
            )).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn.close()

        # Aggregate by calendar date (UTC)
        daily: dict[str, dict] = {}
        for row in rows:
            date = row["recorded_at"][:10]  # YYYY-MM-DD
            if date not in daily:
                daily[date] = {
                    "date": date,
                    "total_records": 0,
                    "passed": 0,
                    "failed": 0,
                    "rule_failure_counts": {},
                }
            d = daily[date]
            d["total_records"] += row["total_records"]
            d["passed"]        += row["passed"]
            d["failed"]        += row["failed"]
            # Merge rule_failure_counts
            for rule, count in json.loads(row["rule_failure_counts"]).items():
                d["rule_failure_counts"][rule] = d["rule_failure_counts"].get(rule, 0) + count

        # Compute pass_rate and sort top_failing_rules
        result = []
        for date in sorted(daily):
            d = daily[date]
            total = d["total_records"]
            d["pass_rate"] = round(d["passed"] / total, 4) if total > 0 else 1.0
            d["top_failing_rules"] = dict(
                sorted(d["rule_failure_counts"].items(), key=lambda x: x[1], reverse=True)[:10]
            )
            del d["rule_failure_counts"]
            result.append(d)

        return result
