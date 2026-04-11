"""
Node health state machine.

Tracks the connectivity state of this node relative to its upstream (if federated).
In standalone mode the node starts and stays `online` — the state machine is a no-op
that adds zero overhead to validation.

States
------
  online    — node is healthy, upstream reachable (or standalone)
  degraded  — upstream reachable but this node is behind (replication lag detected)
  isolated  — upstream unreachable; node is operating on locally cached contracts

Transitions
-----------
  online    → degraded, isolated
  degraded  → online,   isolated
  isolated  → online,   degraded

Self-transitions (same → same) are silently ignored — callers may call
`transition()` repeatedly without concern for duplicates.

Commercial hook
---------------
`time_in_current_state()` returns the seconds since the last transition.
The enterprise tier gates validation when this exceeds MAX_ISOLATION_HOURS
and the current state is `isolated`. The OSS layer provides the measurement;
the policy is enterprise-only.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import opendqv.config as config

logger = logging.getLogger(__name__)


class NodeState(str, Enum):
    ONLINE   = "online"
    DEGRADED = "degraded"
    ISOLATED = "isolated"


# Valid transitions: from_state → set of allowed to_states
_VALID_TRANSITIONS: dict[NodeState, frozenset[NodeState]] = {
    NodeState.ONLINE:   frozenset({NodeState.DEGRADED, NodeState.ISOLATED}),
    NodeState.DEGRADED: frozenset({NodeState.ONLINE,   NodeState.ISOLATED}),
    NodeState.ISOLATED: frozenset({NodeState.ONLINE,   NodeState.DEGRADED}),
}


class NodeHealthStateMachine:
    """
    Persistent node health state machine backed by SQLite.

    One row per transition. The current state is always the most recent row.
    The genesis row is written at construction time — every deployment has
    a complete health history from first boot.
    """

    def __init__(self, db_path: str = None, initial_state: NodeState = NodeState.ONLINE):
        if db_path is None:
            db_path = config.DB_PATH
        self.db_path = db_path
        self._mem_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        # observers: list of callables(old_state, new_state, reason) — called after each
        # recorded transition. Used by IsolationLog and future commercial hooks.
        # Errors in observers are swallowed so they never block a state change.
        self._observers: list = []
        self._obs_lock = threading.Lock()
        self._init_db(initial_state)

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self, initial_state: NodeState):
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS node_health_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "opendqv_node_id TEXT NOT NULL, "
            "state TEXT NOT NULL, "
            "reason TEXT, "
            "transitioned_at TEXT NOT NULL)"
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()

        # Write genesis row only if the table is empty
        row = conn.execute("SELECT COUNT(*) FROM node_health_log").fetchone()
        if row[0] == 0:
            from opendqv.core.clock_sync import check_ntp_skew
            clock = check_ntp_skew()
            startup_reason = (
                f"node startup"
                f" | clock_status={clock['status']}"
                f" | skew_ms={clock['skew_ms']}"
                f" | ntp_source={clock['ntp_source']}"
            )
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO node_health_log (opendqv_node_id, state, reason, transitioned_at) "
                "VALUES (?, ?, ?, ?)",
                (config.OPENDQV_NODE_ID, initial_state.value, startup_reason, now),
            )
            conn.commit()

        if self._mem_conn is None:
            conn.close()

    def current_state(self) -> NodeState:
        """Return the current node state (most recent log entry)."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT state FROM node_health_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            if not is_shared:
                conn.close()
        return NodeState(row[0]) if row else NodeState.ONLINE

    def transition(self, new_state: NodeState, reason: str = "") -> bool:
        """
        Attempt a state transition.

        Returns True if the transition was recorded, False if it was a no-op
        (self-transition to the same state).

        Raises ValueError for invalid transitions (e.g. attempting to skip
        states or supply an unknown state value).
        """
        if not isinstance(new_state, NodeState):
            raise ValueError(
                f"new_state must be a NodeState, got {type(new_state).__name__}. "
                f"Valid values: {[s.value for s in NodeState]}"
            )

        current = self.current_state()
        if current == new_state:
            return False  # idempotent no-op

        allowed = _VALID_TRANSITIONS[current]
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {current.value} → {new_state.value}. "
                f"Allowed from {current.value}: {[s.value for s in allowed]}"
            )

        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            conn.execute(
                "INSERT INTO node_health_log (opendqv_node_id, state, reason, transitioned_at) "
                "VALUES (?, ?, ?, ?)",
                (config.OPENDQV_NODE_ID, new_state.value, reason, now),
            )
            conn.commit()
        finally:
            if not is_shared:
                conn.close()

        logger.info(
            "node_health: %s → %s (%s)", current.value, new_state.value, reason or "no reason given"
        )
        with self._obs_lock:
            observers = list(self._observers)
        for obs in observers:
            try:
                obs(current, new_state, reason)
            except Exception as exc:
                logger.debug("node_health observer error (non-fatal): %s", exc)
        return True

    def add_observer(self, fn) -> None:
        """
        Register a state-change observer.

        fn(old_state: NodeState, new_state: NodeState, reason: str) → None
        Called after every recorded transition. Errors are swallowed.
        """
        with self._obs_lock:
            self._observers.append(fn)

    def time_in_current_state(self) -> float:
        """
        Return the number of seconds since the last state transition.

        Commercial hook: enterprise tier uses this to enforce MAX_ISOLATION_HOURS.
        Returns 0.0 if no log entries exist (should never happen post-init).
        """
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT transitioned_at FROM node_health_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            if not is_shared:
                conn.close()

        if not row:
            return 0.0

        last_ts = datetime.fromisoformat(row[0])
        return (datetime.now(timezone.utc) - last_ts).total_seconds()

    def get_log(self, limit: int = 100) -> list[dict]:
        """
        Return the most recent state transition log entries, newest first.

        Args:
            limit: Maximum number of entries to return (default 100).
        """
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT id, opendqv_node_id, state, reason, transitioned_at "
                "FROM node_health_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        return [
            {
                "id": row[0],
                "opendqv_node_id": row[1],
                "state": row[2],
                "reason": row[3],
                "transitioned_at": row[4],
            }
            for row in rows
        ]

    def isolated_since(self) -> Optional[str]:
        """
        Return the ISO 8601 timestamp when the node entered isolation, or None.

        Used by the health endpoint and the commercial enforcement layer.
        """
        if self.current_state() != NodeState.ISOLATED:
            return None

        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT transitioned_at FROM node_health_log "
                "WHERE state = ? ORDER BY id DESC LIMIT 1",
                (NodeState.ISOLATED.value,),
            ).fetchone()
        finally:
            if not is_shared:
                conn.close()

        return row[0] if row else None


# Module-level singleton — shared across the process (one per Gunicorn worker)
node_health = NodeHealthStateMachine()
