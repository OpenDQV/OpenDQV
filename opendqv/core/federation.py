"""
Federation log — append-only record of every sync event between nodes.

This table is the OSS foundation for the commercial two-phase commit machinery.
The schema is intentionally stable: commercial code will write new event_types
and read this log without schema changes.

Event lifecycle (two-phase propagation):
  authority node: INSERT event_type='push',  status='pending'
  child node ack: UPDATE status='ack'
  authority:      INSERT event_type='commit', status='committed'   (all acked)
  or on timeout:  INSERT event_type='reject', status='rejected'    (governance alert)

Isolation events:
  INSERT event_type='isolation_start' when a node loses upstream contact
  INSERT event_type='isolation_end'   when contact is restored
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import opendqv.config as config
from opendqv.core.storage import FederationLogBackend

logger = logging.getLogger(__name__)

# Valid event types — OSS subset; commercial tier adds workflow events
FEDERATION_EVENT_TYPES = frozenset({
    "push",           # authority pushes contract change to child
    "pull",           # child pulls from upstream
    "ack",            # child acknowledges a pending push
    "commit",         # authority commits after all acks received
    "reject",         # push rejected (constraint violation or timeout)
    "isolation_start",  # node lost contact with upstream
    "isolation_end",    # node regained contact
})

FEDERATION_STATUSES = frozenset({"pending", "ack", "committed", "rejected"})


class FederationLog(FederationLogBackend):
    """
    Append-only federation event log, persisted in SQLite.

    Each row captures one sync event between two nodes. The `lsn` (log sequence
    number) is the monotonically increasing primary key — downstream nodes use it
    as a replication cursor: "give me all events since lsn=N".
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

    @contextmanager
    def _get_conn(self):
        """Context manager for per-operation connections.

        Yields the shared in-memory connection unchanged (no close).
        For file-backed databases, opens a new connection and guarantees
        it is closed after the operation — even on exception.
        """
        if self._mem_conn is not None:
            yield self._mem_conn
        else:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                yield conn
            finally:
                conn.close()

    def _init_db(self):
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS federation_log ("
            "lsn INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_type TEXT NOT NULL, "
            "contract_name TEXT NOT NULL, "
            "contract_version TEXT NOT NULL, "
            "source_node TEXT NOT NULL, "
            "target_node TEXT, "          # NULL = broadcast
            "payload TEXT NOT NULL DEFAULT '{}', "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "created_at TEXT NOT NULL)"
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_federation_log_contract "
            "ON federation_log(contract_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_federation_log_lsn_status "
            "ON federation_log(lsn, status)"
        )
        conn.commit()
        if self._mem_conn is None:
            conn.close()

    def record_event(
        self,
        event_type: str,
        contract_name: str,
        contract_version: str,
        source_node: str,
        target_node: Optional[str] = None,
        payload: Optional[dict] = None,
        status: str = "pending",
    ) -> int:
        """
        Insert a federation event. Returns the assigned lsn.

        Raises ValueError for unknown event_type or status values so callers
        fail fast rather than silently writing garbage into the audit trail.
        """
        if event_type not in FEDERATION_EVENT_TYPES:
            raise ValueError(f"Unknown federation event_type: {event_type!r}. "
                             f"Valid types: {sorted(FEDERATION_EVENT_TYPES)}")
        if status not in FEDERATION_STATUSES:
            raise ValueError(f"Unknown federation status: {status!r}. "
                             f"Valid statuses: {sorted(FEDERATION_STATUSES)}")

        payload_json = json.dumps(payload or {}, sort_keys=True)
        created_at = datetime.now(timezone.utc).isoformat()

        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO federation_log "
                "(event_type, contract_name, contract_version, source_node, "
                "target_node, payload, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_type, contract_name, contract_version, source_node,
                 target_node, payload_json, status, created_at),
            )
            conn.commit()
            return cur.lastrowid

    def update_status(self, lsn: int, status: str) -> bool:
        """
        Update the status of an existing event. Returns True if a row was updated.

        Used by the 2PC machinery to transition: pending → ack → committed/rejected.
        """
        if status not in FEDERATION_STATUSES:
            raise ValueError(f"Unknown federation status: {status!r}")

        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE federation_log SET status = ? WHERE lsn = ?",
                (status, lsn),
            )
            conn.commit()
            return cur.rowcount == 1

    def get_since(self, lsn: int, contract_name: Optional[str] = None) -> list[dict]:
        """
        Return all events with lsn > given value (replication cursor).

        Optionally filter by contract_name. Results are ordered by lsn ascending
        so callers can process them in order and advance their cursor.
        """
        with self._get_conn() as conn:
            if contract_name:
                rows = conn.execute(
                    "SELECT lsn, event_type, contract_name, contract_version, "
                    "source_node, target_node, payload, status, created_at "
                    "FROM federation_log "
                    "WHERE lsn > ? AND contract_name = ? ORDER BY lsn",
                    (lsn, contract_name),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT lsn, event_type, contract_name, contract_version, "
                    "source_node, target_node, payload, status, created_at "
                    "FROM federation_log WHERE lsn > ? ORDER BY lsn",
                    (lsn,),
                ).fetchall()

        return [
            {
                "lsn": row[0],
                "event_type": row[1],
                "contract_name": row[2],
                "contract_version": row[3],
                "source_node": row[4],
                "target_node": row[5],
                "payload": json.loads(row[6]),
                "status": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    def get_pending(self, contract_name: Optional[str] = None) -> list[dict]:
        """Return all events with status='pending', optionally filtered by contract."""
        with self._get_conn() as conn:
            if contract_name:
                rows = conn.execute(
                    "SELECT lsn, event_type, contract_name, contract_version, "
                    "source_node, target_node, payload, status, created_at "
                    "FROM federation_log "
                    "WHERE status = 'pending' AND contract_name = ? ORDER BY lsn",
                    (contract_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT lsn, event_type, contract_name, contract_version, "
                    "source_node, target_node, payload, status, created_at "
                    "FROM federation_log WHERE status = 'pending' ORDER BY lsn",
                ).fetchall()

        return [
            {
                "lsn": row[0],
                "event_type": row[1],
                "contract_name": row[2],
                "contract_version": row[3],
                "source_node": row[4],
                "target_node": row[5],
                "payload": json.loads(row[6]),
                "status": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]
