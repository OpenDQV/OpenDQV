"""
Isolation event log — lifecycle record for each period a node operated without
upstream contact.

Each row in `isolation_events` represents one isolation episode:
  opened when the node transitions to NodeState.ISOLATED
  closed when the node transitions back to NodeState.ONLINE or DEGRADED

The `exceeded_threshold` flag is the OSS hook for the commercial compliance
report: when True, the governance dashboard surfaces the event as a policy
violation requiring review.

Wired into the node health state machine via the observer pattern:
  from core.node_health import node_health
  from core.isolation_log import isolation_log
  node_health.add_observer(isolation_log.observe_state_change)

This keeps both modules decoupled — neither imports the other.
"""

import logging
import sqlite3
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


class IsolationLog:
    """
    Append-only isolation event log, persisted in SQLite.

    Each row covers one complete isolation episode. Open events have
    ended_at=NULL. Close events fill in ended_at, duration_seconds,
    resolution, and exceeded_threshold.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = config.DB_PATH
        self.db_path = db_path
        self._mem_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS isolation_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "opendqv_node_id TEXT NOT NULL, "
            "started_at TEXT NOT NULL, "
            "ended_at TEXT, "                   # NULL while ongoing
            "trigger TEXT NOT NULL, "           # what caused isolation
            "resolution TEXT, "                 # how it ended (NULL while ongoing)
            "duration_seconds REAL, "           # NULL while ongoing
            "max_allowed_seconds REAL NOT NULL, "  # snapshot of policy at open time
            "exceeded_threshold INTEGER NOT NULL DEFAULT 0)"  # 0/1 bool
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_isolation_node "
            "ON isolation_events(opendqv_node_id, started_at)"
        )
        conn.commit()
        if self._mem_conn is None:
            conn.close()

    def open_event(self, trigger: str) -> int:
        """
        Record the start of an isolation episode.

        Args:
            trigger: Human-readable cause, e.g. 'upstream_unreachable',
                     'timeout', 'manual_override'.

        Returns the row id (isolation event id).
        """
        max_allowed = config.MAX_ISOLATION_HOURS * 3600
        started_at = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            cur = conn.execute(
                "INSERT INTO isolation_events "
                "(opendqv_node_id, started_at, trigger, max_allowed_seconds) "
                "VALUES (?, ?, ?, ?)",
                (config.OPENDQV_NODE_ID, started_at, trigger, max_allowed),
            )
            conn.commit()
            event_id = cur.lastrowid
        finally:
            if not is_shared:
                conn.close()

        logger.warning("isolation_log: isolation OPENED id=%d trigger=%s", event_id, trigger)
        return event_id

    def close_event(self, event_id: int, resolution: str) -> bool:
        """
        Record the end of an isolation episode.

        Computes duration_seconds and sets exceeded_threshold if the isolation
        lasted longer than the policy window recorded at open time.

        Args:
            event_id:   The id returned by open_event().
            resolution: How isolation ended, e.g. 'upstream_restored',
                        'force_closed', 'manual_override'.

        Returns True if the event was found and closed, False if not found or
        already closed.
        """
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT started_at, max_allowed_seconds FROM isolation_events "
                "WHERE id = ? AND ended_at IS NULL",
                (event_id,),
            ).fetchone()

            if not row:
                return False

            started_at_str, max_allowed = row
            started_at = datetime.fromisoformat(started_at_str)
            ended_at = datetime.now(timezone.utc)
            duration = (ended_at - started_at).total_seconds()
            exceeded = 1 if duration > max_allowed else 0

            conn.execute(
                "UPDATE isolation_events SET "
                "ended_at = ?, resolution = ?, duration_seconds = ?, exceeded_threshold = ? "
                "WHERE id = ?",
                (ended_at.isoformat(), resolution, duration, exceeded, event_id),
            )
            conn.commit()
        finally:
            if not is_shared:
                conn.close()

        logger.info(
            "isolation_log: isolation CLOSED id=%d resolution=%s duration=%.1fs exceeded=%s",
            event_id, resolution, duration, bool(exceeded),
        )
        return True

    def close_open_events(self, resolution: str) -> int:
        """
        Close all open isolation events for this node (best-effort recovery path).

        Used when coming back online and the event_id was lost (e.g. after process restart).
        Returns the number of events closed.
        """
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT id, started_at, max_allowed_seconds FROM isolation_events "
                "WHERE opendqv_node_id = ? AND ended_at IS NULL",
                (config.OPENDQV_NODE_ID,),
            ).fetchall()

            count = 0
            now = datetime.now(timezone.utc)
            for event_id, started_at_str, max_allowed in rows:
                started_at = datetime.fromisoformat(started_at_str)
                duration = (now - started_at).total_seconds()
                exceeded = 1 if duration > max_allowed else 0
                conn.execute(
                    "UPDATE isolation_events SET "
                    "ended_at = ?, resolution = ?, duration_seconds = ?, exceeded_threshold = ? "
                    "WHERE id = ?",
                    (now.isoformat(), resolution, duration, exceeded, event_id),
                )
                count += 1
            conn.commit()
            return count
        finally:
            if not is_shared:
                conn.close()

    def get_open_events(self) -> list[dict]:
        """Return all currently open isolation events (ended_at IS NULL)."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT id, opendqv_node_id, started_at, trigger, max_allowed_seconds "
                "FROM isolation_events WHERE ended_at IS NULL ORDER BY id"
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        return [
            {
                "id": r[0],
                "opendqv_node_id": r[1],
                "started_at": r[2],
                "ended_at": None,
                "trigger": r[3],
                "resolution": None,
                "duration_seconds": None,
                "max_allowed_seconds": r[4],
                "exceeded_threshold": False,
            }
            for r in rows
        ]

    def get_events(self, limit: int = 50) -> list[dict]:
        """Return isolation events, newest first."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT id, opendqv_node_id, started_at, ended_at, trigger, resolution, "
                "duration_seconds, max_allowed_seconds, exceeded_threshold "
                "FROM isolation_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        return [
            {
                "id": r[0],
                "opendqv_node_id": r[1],
                "started_at": r[2],
                "ended_at": r[3],
                "trigger": r[4],
                "resolution": r[5],
                "duration_seconds": r[6],
                "max_allowed_seconds": r[7],
                "exceeded_threshold": bool(r[8]),
            }
            for r in rows
        ]

    def observe_state_change(self, old_state, new_state, reason: str) -> None:
        """
        Observer for NodeHealthStateMachine — wires isolation open/close automatically.

        Register via: node_health.add_observer(isolation_log.observe_state_change)

        Importing NodeState here avoids a circular import (neither module imports
        the other at module level).
        """
        from core.node_health import NodeState

        if new_state == NodeState.ISOLATED:
            self.open_event(trigger=reason or "upstream_unreachable")

        elif old_state == NodeState.ISOLATED and new_state != NodeState.ISOLATED:
            # Coming back online or degraded — close any open events
            self.close_open_events(resolution=reason or "upstream_restored")


# Module-level singleton
isolation_log = IsolationLog()
