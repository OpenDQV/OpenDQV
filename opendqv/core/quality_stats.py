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

from opendqv.monitoring import _is_system_agent

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS quality_stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT    NOT NULL DEFAULT '',
    contract_name    TEXT    NOT NULL,
    contract_version TEXT    NOT NULL,
    context          TEXT    NOT NULL DEFAULT 'default',
    recorded_at      TEXT    NOT NULL,
    total_records    INTEGER NOT NULL,
    passed           INTEGER NOT NULL,
    failed           INTEGER NOT NULL,
    pass_rate_pct    REAL    NOT NULL,
    rule_failure_counts TEXT NOT NULL DEFAULT '{}',
    agent_id         TEXT    NOT NULL DEFAULT '',
    mode             TEXT    NOT NULL DEFAULT 'enforcement',
    caller_principal TEXT    NOT NULL DEFAULT '',
    effective_rule_hash TEXT NOT NULL DEFAULT '',
    entry_hash       TEXT    NOT NULL DEFAULT '',
    content_hash     TEXT    NOT NULL DEFAULT ''
)
"""

_MIGRATE_AGENT_ID = "ALTER TABLE quality_stats ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''"
_MIGRATE_MODE = "ALTER TABLE quality_stats ADD COLUMN mode TEXT NOT NULL DEFAULT 'enforcement'"
_MIGRATE_EVENT_ID = "ALTER TABLE quality_stats ADD COLUMN event_id TEXT NOT NULL DEFAULT ''"
_MIGRATE_CALLER_PRINCIPAL = "ALTER TABLE quality_stats ADD COLUMN caller_principal TEXT NOT NULL DEFAULT ''"
# v2.3.22 Cluster C: F-J persistence — hash triplet on the audit row.
# Existing rows get empty-string sentinel; consumers must treat empty as
# "pre-Cluster-C, not available" (no false history via head-recompute).
_MIGRATE_EFFECTIVE_RULE_HASH = "ALTER TABLE quality_stats ADD COLUMN effective_rule_hash TEXT NOT NULL DEFAULT ''"
_MIGRATE_ENTRY_HASH = "ALTER TABLE quality_stats ADD COLUMN entry_hash TEXT NOT NULL DEFAULT ''"
_MIGRATE_CONTENT_HASH = "ALTER TABLE quality_stats ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
# v2.3.23 round-3 #8 (Sonnet a2180d103efcbb82c): persist batch-average
# latency so hydration emits a real per-event latency proxy instead of
# null on hydrated rows. Default 0.0 = "not recorded" sentinel —
# read boundary translates 0.0 to wire null (honest "we don't know"
# rather than misleading "really fast").
_MIGRATE_LATENCY_MS_AVG = "ALTER TABLE quality_stats ADD COLUMN latency_ms_avg REAL NOT NULL DEFAULT 0.0"
# v2.3.18 Q3 Phase 1 (storage): rename pass_rate (ratio 0–1) to
# pass_rate_pct (percent 0–100). One-time migration on existing
# installs: rename column AND multiply existing values × 100. SQLite
# RENAME COLUMN is supported since 3.25 (2018). The dual UPDATE-
# multiplication step is done before any read path uses the new column
# name, so the value range is consistent across stored and freshly
# inserted rows. No bare `pass_rate` column remains anywhere in the
# stored schema after this migration.
_MIGRATE_PASS_RATE_PCT_RENAME = "ALTER TABLE quality_stats RENAME COLUMN pass_rate TO pass_rate_pct"
_MIGRATE_PASS_RATE_PCT_VALUE = "UPDATE quality_stats SET pass_rate_pct = pass_rate_pct * 100 WHERE pass_rate_pct <= 1.0"
_CREATE_EVENT_ID_INDEX = "CREATE INDEX IF NOT EXISTS idx_quality_stats_event_id ON quality_stats(event_id)"

_INSERT = """
INSERT INTO quality_stats
    (event_id, contract_name, contract_version, context, recorded_at,
     total_records, passed, failed, pass_rate_pct, rule_failure_counts, agent_id, mode,
     caller_principal, effective_rule_hash, entry_hash, content_hash, latency_ms_avg)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_DELETE_BY_CONTEXT = "DELETE FROM quality_stats WHERE context = ?"

_SELECT_SINCE = """
SELECT contract_name, contract_version, context, recorded_at,
       total_records, passed, failed, pass_rate_pct, rule_failure_counts,
       agent_id
FROM   quality_stats
WHERE  contract_name = ?
  AND  recorded_at   >= ?
  AND  (? IS NULL OR context = ?)
ORDER  BY recorded_at ASC
"""

_SELECT_WINDOWED_TOTALS = """
SELECT SUM(total_records) AS total, SUM(passed) AS passed, SUM(failed) AS failed,
       rule_failure_counts
FROM   quality_stats
WHERE  contract_name = ?
  AND  recorded_at   >= ?
"""

_SELECT_AGENT_BREAKDOWN = """
SELECT agent_id,
       SUM(total_records) AS total,
       SUM(passed)        AS passed,
       SUM(failed)        AS failed
FROM   quality_stats
WHERE  contract_name = ?
  AND  recorded_at   >= ?
  AND  agent_id      != ''
GROUP  BY agent_id
ORDER  BY SUM(total_records) DESC
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
            # Migration: add agent_id to existing DBs (idempotent — raises OperationalError if already present)
            try:
                conn.execute(_MIGRATE_AGENT_ID)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add mode to existing DBs (idempotent)
            try:
                conn.execute(_MIGRATE_MODE)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add event_id to existing DBs (idempotent)
            try:
                conn.execute(_MIGRATE_EVENT_ID)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            # CRT170/J2: server-derived caller principal (cannot be spoofed)
            try:
                conn.execute(_MIGRATE_CALLER_PRINCIPAL)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            # v2.3.22 Cluster C: F-J persistence — hash triplet on the audit
            # row. Each migration is independently idempotent.
            for stmt in (_MIGRATE_EFFECTIVE_RULE_HASH, _MIGRATE_ENTRY_HASH, _MIGRATE_CONTENT_HASH):
                try:
                    conn.execute(stmt)
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # column already exists
            # v2.3.23 round-3 #8: persist batch-average latency_ms.
            try:
                conn.execute(_MIGRATE_LATENCY_MS_AVG)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
            # v2.3.18 Q3 Phase 1: rename pass_rate (ratio) → pass_rate_pct
            # (percent), and multiply existing ratio values × 100. Idempotent —
            # OperationalError on the rename means the migration already ran
            # (column already named pass_rate_pct), so the value-conversion is
            # also skipped to avoid double-multiplying.
            try:
                conn.execute(_MIGRATE_PASS_RATE_PCT_RENAME)
                conn.execute(_MIGRATE_PASS_RATE_PCT_VALUE)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # already renamed
            try:
                conn.execute(_CREATE_EVENT_ID_INDEX)
                conn.commit()
            except sqlite3.OperationalError:
                pass
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
        agent_id: str = "",
        mode: str = "enforcement",
        event_id: str = "",
        caller_principal: str = "",
        effective_rule_hash: str = "",
        entry_hash: str = "",
        content_hash: str = "",
        latency_ms_avg: float = 0.0,
    ) -> None:
        """Persist one batch validation result.

        v2.3.22 Cluster C: hash triplet (effective_rule_hash, entry_hash,
        content_hash) is now persisted alongside contract_name/version/
        context, so get_audit_event(event_id) can answer the regulator's
        "which exact rule set was applied" question. Empty string is the
        sentinel for "caller did not supply" — never recompute from
        current head (false history).
        """
        # v2.3.18 Q3: storage column is pass_rate_pct (percent 0–100).
        # v2.3.22 Cluster F: empty-batch case stores 0.0 in storage (no
        # NULL since the column is REAL NOT NULL); read paths translate
        # storage 0.0 with total_records=0 into wire null at the response
        # boundary. The "vacuously perfect" framing was wrong — empty
        # batches are signal-of-no-data, not signal-of-perfection.
        pass_rate_pct = round(passed / total * 100, 1) if total > 0 else 0.0
        now = datetime.now(timezone.utc).isoformat()
        ctx = context or "default"
        conn = self._connect()
        try:
            conn.execute(_INSERT, (
                event_id or "",
                contract_name, contract_version, ctx, now,
                total, passed, failed, pass_rate_pct,
                json.dumps(rule_failure_counts),
                agent_id or "",
                mode or "enforcement",
                caller_principal or "",
                effective_rule_hash or "",
                entry_hash or "",
                content_hash or "",
                float(latency_ms_avg or 0.0),
            ))
            conn.commit()
        except (sqlite3.Error, OSError, ValueError) as exc:
            logger.exception("Failed to record quality stats: %s", exc)
        finally:
            if self._db_path != ":memory:":
                conn.close()

    def delete_by_context(self, context: str) -> int:
        """Delete all rows with the given context. Returns number of rows deleted."""
        conn = self._connect()
        try:
            cur = conn.execute(_DELETE_BY_CONTEXT, (context,))
            conn.commit()
            return cur.rowcount
        except (sqlite3.Error, OSError) as exc:
            logger.exception("Failed to delete quality stats by context: %s", exc)
            return 0
        finally:
            if self._db_path != ":memory:":
                conn.close()

    def get_trend(
        self,
        contract_name: str,
        days: int = 7,
        context: Optional[str] = None,
        by: str = "date",
        include_system: bool = False,
    ) -> list[dict]:
        """
        Return aggregated quality statistics for the last N days.

        `by` selects the grouping dimension:
          - "date" (default): daily buckets keyed by `date` (legacy shape)
          - "agent":   per agent_id buckets, keyed by `key`
          - "context": per context buckets, keyed by `key`
          - "rule":    per rule_failure_counts entry, keyed by `key`

        When by != "date", entries omit `date` and use `key` instead. The
        legacy date-shape response is preserved exactly when by="date" so
        existing wire consumers are unaffected.
        """
        if by not in ("date", "agent", "context", "rule"):
            raise ValueError(f"unknown trend dimension: {by}")

        # v2.3.23 P2-14 (Sonnet a968e64aaabfd31bf): snap cutoff to start
        # of (today - days + 1) UTC. The previous "now - timedelta(days=N)"
        # cutoff produced N+1 calendar buckets when the trailing day was
        # partial — Persona B reported "days=7 returned 8 buckets."
        # Convention: "last 7 days" = today + 6 previous = 7 calendar days.
        _today_utc = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        since = (_today_utc - timedelta(days=days - 1)).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(_SELECT_SINCE, (
                contract_name, since, context, context,
            )).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn.close()

        if by != "date":
            return self._group_trend(rows, by, include_system=include_system)

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

        # Compute pass_rate_pct (v2.3.18 Q3) and sort top_failing_rules
        result = []
        for date in sorted(daily):
            d = daily[date]
            total = d["total_records"]
            # v2.3.22 Cluster F: empty bucket → null on wire, not 100.0.
            d["pass_rate_pct"] = round(d["passed"] / total * 100, 1) if total > 0 else None
            _ranked = sorted(d["rule_failure_counts"].items(), key=lambda x: x[1], reverse=True)[:10]
            # Legacy dict form (deprecated v2.3.13, removed v2.4) — JSON dicts have
            # no guaranteed ordering, so consumers cannot infer the failure ranking.
            d["top_failing_rules"] = dict(_ranked)
            # Canonical array form: ordered, no key collisions across contracts.
            d["top_failing_rules_ranked"] = [{"rule": r, "count": c} for r, c in _ranked]
            del d["rule_failure_counts"]
            result.append(d)

        return result

    def _group_trend(self, rows: list, by: str, include_system: bool = False) -> list[dict]:
        """Group raw quality_stats rows by a non-date dimension."""
        # by ∈ {"agent", "context", "rule"} — caller validated.
        grouped: dict[str, dict] = {}
        for row in rows:
            if by == "agent":
                # v2.3.23 outside-review #2 (Sonnet a15e627a5e6a24fa3):
                # empty agent_id surfaces as "unattributed" bucket
                # rather than an empty-string key. Mirrors the in-memory
                # _aggregate_by_agent helper (monitoring.py). Reviewer
                # P0 #3: response with key="" looks like the grouping
                # dimension is broken; "unattributed" is the honest
                # label for legacy / pre-agent_id data.
                aid = row["agent_id"] or "unattributed"
                # v2.3.22 Cluster E: suppression contract ("system traffic
                # does not appear in customer-visible read surfaces unless
                # include_system=true") extends to the trend by=agent
                # surface. Round-1 inside-view 2.2 caught the leak.
                if not include_system and _is_system_agent(aid):
                    continue
                keys: list[tuple[str, int]] = [(aid, row["passed"] + row["failed"])]
            elif by == "context":
                keys = [(row["context"] or "default", row["passed"] + row["failed"])]
            else:  # by == "rule"
                rfc = json.loads(row["rule_failure_counts"]) if row["rule_failure_counts"] else {}
                keys = [(rule, count) for rule, count in rfc.items()]

            for key, _hint in keys:
                bucket = grouped.setdefault(key, {
                    "key": key,
                    "total_records": 0,
                    "passed": 0,
                    "failed": 0,
                })
                if by == "rule":
                    # For by=rule, we sum rule-violation counts. passed/failed
                    # are not meaningful per rule, so we surface only the count.
                    bucket["failed"] += _hint
                else:
                    bucket["total_records"] += row["total_records"]
                    bucket["passed"] += row["passed"]
                    bucket["failed"] += row["failed"]

        out = []
        for k, b in grouped.items():
            if by == "rule":
                out.append({"key": k, "violation_count": b["failed"]})
            else:
                t = b["total_records"]
                # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp).
                # v2.3.22 Cluster F: empty bucket → null.
                b["pass_rate_pct"] = round(b["passed"] / t * 100, 1) if t > 0 else None
                out.append(b)
        # Sort: by=rule by violation_count desc, others by total_records desc
        if by == "rule":
            out.sort(key=lambda x: x["violation_count"], reverse=True)
        else:
            out.sort(key=lambda x: x["total_records"], reverse=True)
        return out

    def get_windowed_totals(self, contract_name: str, window_hours: int) -> dict:
        """
        Return aggregated totals for a contract within the last window_hours.

        Used as a SQLite fallback when in-memory stats are empty (e.g. after restart).
        Returns dict with: total, passed, failed, pass_rate_pct, top_failing_rules.
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT SUM(total_records), SUM(passed), SUM(failed), rule_failure_counts "
                "FROM quality_stats WHERE contract_name = ? AND recorded_at >= ?",
                (contract_name, since),
            ).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn.close()

        total = int(rows[0][0] or 0)
        passed = int(rows[0][1] or 0)
        failed = int(rows[0][2] or 0)

        # Aggregate rule failure counts from all matching rows
        conn2 = self._connect()
        try:
            detail_rows = conn2.execute(
                "SELECT rule_failure_counts FROM quality_stats "
                "WHERE contract_name = ? AND recorded_at >= ?",
                (contract_name, since),
            ).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn2.close()

        rule_counts: dict = {}
        for (rfc_json,) in detail_rows:
            if not rfc_json or rfc_json in ("{}", "null"):
                continue
            try:
                for rule, count in json.loads(rfc_json).items():
                    rule_counts[rule] = rule_counts.get(rule, 0) + int(count)
            except (json.JSONDecodeError, TypeError):
                pass

        _ranked = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_rules = dict(_ranked)
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            # v2.3.18 Q3: pass_rate_pct (percent 0–100).
            # v2.3.22 Cluster F: empty window → null (no-data signal).
            "pass_rate_pct": round(passed / total * 100, 1) if total > 0 else None,
            # Legacy dict form — see get_trend() comment.
            "top_failing_rules": top_rules,
            "top_failing_rules_ranked": [{"rule": r, "count": c} for r, c in _ranked],
        }

    def get_event(self, event_id: str) -> Optional[dict]:
        """
        CRT172 / K1. Return the audit row for a single event_id, or None.

        Returns the full row including JSON-decoded `rule_failure_counts`.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, event_id, contract_name, contract_version, context, "
                "       recorded_at, total_records, passed, failed, pass_rate_pct, "
                "       rule_failure_counts, agent_id, mode, caller_principal, "
                "       effective_rule_hash, entry_hash, content_hash "
                "FROM   quality_stats WHERE event_id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
        finally:
            if self._db_path != ":memory:":
                conn.close()
        if row is None:
            return None
        try:
            rfc = json.loads(row["rule_failure_counts"]) if row["rule_failure_counts"] else {}
        except (json.JSONDecodeError, TypeError):
            rfc = {}
        return {
            "id": int(row["id"]),
            "event_id": row["event_id"],
            "contract": row["contract_name"],
            "contract_version": row["contract_version"],
            "context": row["context"],
            "recorded_at": row["recorded_at"],
            "total_records": int(row["total_records"]),
            "passed": int(row["passed"]),
            "failed": int(row["failed"]),
            "pass_rate_pct": float(row["pass_rate_pct"]),
            "rule_failure_counts": rfc,
            # v2.3.23 round-3 review: emit null for unattributed events
            # (was: ""). Wire shape consistency across all surfaces.
            "agent_id": row["agent_id"] or None,
            "mode": row["mode"] or "enforcement",
            "caller_principal": row["caller_principal"] or "",
            "effective_rule_hash": row["effective_rule_hash"] or "",
            "entry_hash": row["entry_hash"] or "",
            "content_hash": row["content_hash"] or "",
        }

    def list_events(
        self,
        *,
        contract: Optional[str] = None,
        contract_version: Optional[str] = None,
        context: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        agent_id: Optional[str] = None,
        caller_principal: Optional[str] = None,
        valid: Optional[bool] = None,
        mode: Optional[str] = None,
        cursor_recorded_at: Optional[str] = None,
        cursor_id: Optional[int] = None,
        limit: int = 100,
    ) -> tuple[list[dict], bool]:
        """
        CRT172 / K2. Cursor-paginated row-level audit listing over quality_stats.

        Cursor pair is (recorded_at, id) where id is the integer auto-increment
        primary key — strict tiebreaker for events landing in the same instant.

        `valid=True` requires `failed = 0 AND total_records > 0` so vacuous
        zero-record rows do not silently match the filter (CRT170 working
        principle: a field's value must mean what its name claims).

        Returns (events, has_more). has_more is computed via limit+1 lookahead
        so callers can detect truncation.
        """
        clauses: list[str] = []
        params: list = []
        if contract is not None:
            clauses.append("contract_name = ?")
            params.append(contract)
        if contract_version is not None:
            clauses.append("contract_version = ?")
            params.append(contract_version)
        if context is not None:
            clauses.append("context = ?")
            params.append(context)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("recorded_at < ?")
            params.append(until)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if caller_principal is not None:
            clauses.append("caller_principal = ?")
            params.append(caller_principal)
        if valid is True:
            clauses.append("failed = 0 AND total_records > 0")
        elif valid is False:
            clauses.append("failed > 0")
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        # Cursor: descending order by (recorded_at, id) — caller passes the last
        # (recorded_at, id) seen and we return strictly older rows.
        if cursor_recorded_at is not None and cursor_id is not None:
            clauses.append("(recorded_at < ? OR (recorded_at = ? AND id < ?))")
            params.extend([cursor_recorded_at, cursor_recorded_at, cursor_id])

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT id, event_id, contract_name, contract_version, recorded_at, "
            "       total_records, passed, failed, agent_id, caller_principal, mode "
            "FROM   quality_stats" + where +
            " ORDER BY recorded_at DESC, id DESC LIMIT ?"
        )
        params.append(limit + 1)  # +1 lookahead for has_more

        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn.close()

        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [
            {
                "id": int(r["id"]),
                "event_id": r["event_id"],
                "contract": r["contract_name"],
                "contract_version": r["contract_version"],
                "recorded_at": r["recorded_at"],
                "total_records": int(r["total_records"]),
                "passed": int(r["passed"]),
                "failed": int(r["failed"]),
                # v2.3.23 round-3 review: emit null for unattributed
                # events (was: ""). Wire shape consistency.
                "agent_id": r["agent_id"] or None,
                "caller_principal": r["caller_principal"] or "",
                "mode": r["mode"] or "enforcement",
            }
            for r in rows
        ]
        return events, has_more

    def get_agent_breakdown(
        self,
        contract_name: str,
        window_hours: int = 24,
        include_system: bool = False,
    ) -> list[dict]:
        """
        Return per-agent_id totals for a contract within the last window_hours.

        Only includes rows where agent_id is non-empty. Returns list of dicts:
          {agent_id, total, passed, failed, pass_rate_pct}
        sorted by total descending.

        v2.3.22 Cluster E: OpenDQV_SA_* system agents are suppressed by
        default — the same suppression contract that governs list_agents
        and get_quality_metrics. This feeds the by_agent rollup on
        get_quality_metrics, so leaving it unfiltered surfaces system
        traffic on every metrics call.
        """
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(_SELECT_AGENT_BREAKDOWN, (contract_name, since)).fetchall()
        finally:
            if self._db_path != ":memory:":
                conn.close()

        result = []
        for row in rows:
            agent_id = row[0]
            if not include_system and _is_system_agent(agent_id):
                continue
            total = int(row[1] or 0)
            passed = int(row[2] or 0)
            failed = int(row[3] or 0)
            result.append({
                "agent_id": agent_id,
                "total": total,
                "passed": passed,
                "failed": failed,
                # v2.3.18 Q3: pass_rate_pct (percent 0–100).
                # v2.3.22 Cluster F: empty agent → null.
                "pass_rate_pct": round(passed / total * 100, 1) if total > 0 else None,
            })
        return result


# ── Confidence band helper (CRT170/J6) ──────────────────────────────────
# Single source of truth for the data_confidence + confidence_note pair
# attached to MCP and REST analytics responses. Same scale across
# get_quality_metrics, get_quality_trend, and get_rule_velocity so clients
# interpret data sufficiency consistently.

def quality_confidence(total: int) -> tuple[str, str]:
    """Return (data_confidence, confidence_note) for `total` underlying validations.

    confidence_note is ALWAYS a string. Returns "" when no caveat is needed
    (medium/high bands). v2.3.14 / CRT173 finding 23: prior shape varied
    between null, absent, and string — three states is two too many.
    """
    if total <= 0:
        return "no_data", "No validation data recorded yet for this contract."
    if total < 10:
        s = "s" if total != 1 else ""
        return "low", f"Based on {total} validation{s} — treat with caution"
    if total < 100:
        return "medium", ""
    return "high", ""
