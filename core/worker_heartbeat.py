"""
Per-worker enforcement window logging.

In Gunicorn multi-worker mode each OS process independently validates records.
This module tracks — per worker PID — which contracts each worker is enforcing
and when it last ran a validation. The data feeds:

  - /health endpoint: visible proof that all workers are active
  - Prometheus: opendqv_worker_stale_count gauge (see monitoring.py)
  - Commercial tier: isolation enforcement — a worker that hasn't resynced
    within MAX_ISOLATION_HOURS is considered stale and can be blocked from
    accepting new validation requests

Design notes:
  - Writes are best-effort (try/except in record_validation) — a heartbeat
    failure MUST never block or slow a validation call
  - SQLite UPSERT on (worker_pid, contract_name) keeps the table compact
  - The module exposes a process-level singleton `heartbeat` that routes.py
    imports once per worker process
"""

import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

import config

# Minimum seconds between SQLite writes per (pid, contract_name).
# The stale threshold is 300s, so 10s precision is more than adequate.
_WRITE_INTERVAL = 10.0

logger = logging.getLogger(__name__)


class WorkerHeartbeat:
    """
    Records per-worker, per-contract enforcement activity in SQLite.

    One row per (worker_pid, contract_name) pair. Updated on every validation
    call so the timestamp always reflects the last time that worker ran a
    validation for that contract.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = config.DB_PATH
        self.db_path = db_path
        self._mem_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        # _last_write[(pid, contract_name)] = monotonic timestamp of last SQLite flush
        self._last_write: dict = {}
        # _pending_count[(pid, contract_name)] = validations since last flush
        self._pending_count: dict = {}
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS worker_heartbeat ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "worker_pid INTEGER NOT NULL, "
            "opendqv_node_id TEXT NOT NULL, "
            "contract_name TEXT NOT NULL, "
            "contract_version TEXT NOT NULL, "
            "last_validated_at TEXT NOT NULL, "
            "validation_count INTEGER NOT NULL DEFAULT 1, "
            "UNIQUE(worker_pid, contract_name))"
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_worker_heartbeat_pid "
            "ON worker_heartbeat(worker_pid)"
        )
        conn.commit()
        if self._mem_conn is None:
            conn.close()

    def record_validation(self, contract_name: str, contract_version: str) -> None:
        """
        Upsert a heartbeat row for the current process + contract.

        Called from the validation endpoints after every successful validation.
        Best-effort — exceptions are logged and swallowed so they never block
        a validation response.

        SQLite writes are throttled to at most once per _WRITE_INTERVAL seconds
        per (pid, contract_name) to avoid write-lock contention across Gunicorn
        workers on the hot validation path.
        """
        pid = os.getpid()
        key = (pid, contract_name)
        now_mono = time.monotonic()

        with self._lock:
            # Always increment in-memory pending counter
            self._pending_count[key] = self._pending_count.get(key, 0) + 1

            # Only flush to SQLite if enough time has elapsed.
            # In-memory DBs (used in tests) always flush immediately.
            last = self._last_write.get(key, 0.0)
            if self._mem_conn is None and now_mono - last < _WRITE_INTERVAL:
                return

            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                pending = self._pending_count.get(key, 1)
                conn = self._connect()
                is_shared = self._mem_conn is not None
                try:
                    conn.execute(
                        "INSERT INTO worker_heartbeat "
                        "(worker_pid, opendqv_node_id, contract_name, contract_version, "
                        "last_validated_at, validation_count) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(worker_pid, contract_name) DO UPDATE SET "
                        "contract_version = excluded.contract_version, "
                        "last_validated_at = excluded.last_validated_at, "
                        "validation_count = validation_count + excluded.validation_count",
                        (pid, config.OPENDQV_NODE_ID, contract_name, contract_version, now_iso, pending),
                    )
                    conn.commit()
                finally:
                    if not is_shared:
                        conn.close()
                self._last_write[key] = now_mono
                self._pending_count[key] = 0
            except Exception as exc:
                # Heartbeat failures must never surface to callers
                logger.debug("worker_heartbeat.record_validation failed (non-fatal): %s", exc)

    def flush(self) -> None:
        """
        Force an immediate SQLite write for all pending (pid, contract) pairs.

        Useful in tests and on graceful shutdown to ensure the last batch of
        validations is persisted even if _WRITE_INTERVAL has not elapsed.
        """
        for (pid, contract_name), pending in list(self._pending_count.items()):
            if pending == 0:
                continue
            try:
                # Find the last-known version from the last_write context
                # We don't store it separately, so read from DB first
                now_iso = datetime.now(timezone.utc).isoformat()
                conn = self._connect()
                is_shared = self._mem_conn is not None
                try:
                    conn.execute(
                        "UPDATE worker_heartbeat SET "
                        "last_validated_at = ?, "
                        "validation_count = validation_count + ? "
                        "WHERE worker_pid = ? AND contract_name = ?",
                        (now_iso, pending, pid, contract_name),
                    )
                    conn.commit()
                finally:
                    if not is_shared:
                        conn.close()
                self._last_write[(pid, contract_name)] = time.monotonic()
                self._pending_count[(pid, contract_name)] = 0
            except Exception as exc:
                logger.debug("worker_heartbeat.flush failed (non-fatal): %s", exc)

    def get_heartbeats(self) -> list[dict]:
        """Return all heartbeat rows, ordered by last_validated_at descending."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT worker_pid, opendqv_node_id, contract_name, contract_version, "
                "last_validated_at, validation_count "
                "FROM worker_heartbeat ORDER BY last_validated_at DESC"
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        return [
            {
                "worker_pid": row[0],
                "opendqv_node_id": row[1],
                "contract_name": row[2],
                "contract_version": row[3],
                "last_validated_at": row[4],
                "validation_count": row[5],
            }
            for row in rows
        ]

    def get_active_worker_pids(self) -> list[int]:
        """Return the distinct PIDs that have recorded at least one heartbeat."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT DISTINCT worker_pid FROM worker_heartbeat"
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()
        return [r[0] for r in rows]

    def get_stale_workers(self, max_age_seconds: int = 300) -> list[dict]:
        """
        Return heartbeat rows where last_validated_at is older than max_age_seconds.

        OSS hook for the commercial isolation enforcement:
        - Standalone use: visibility into idle workers
        - Enterprise: gate validation if any worker exceeds MAX_ISOLATION_HOURS

        A worker PID appears stale once per (pid, contract) pair — if it stops
        validating a contract, that row ages out and becomes stale.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        ).isoformat()

        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT worker_pid, opendqv_node_id, contract_name, contract_version, "
                "last_validated_at, validation_count "
                "FROM worker_heartbeat WHERE last_validated_at < ? "
                "ORDER BY last_validated_at ASC",
                (cutoff,),
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        return [
            {
                "worker_pid": row[0],
                "opendqv_node_id": row[1],
                "contract_name": row[2],
                "contract_version": row[3],
                "last_validated_at": row[4],
                "validation_count": row[5],
            }
            for row in rows
        ]

    def purge_dead_workers(self) -> int:
        """
        Remove heartbeat rows for PIDs that no longer exist on this machine.

        Safe to call periodically (e.g. on /contracts/reload) to keep the table
        from growing unboundedly across restarts.

        Returns the number of rows deleted.
        """
        all_pids = self.get_active_worker_pids()
        dead_pids = [pid for pid in all_pids if not _pid_exists(pid)]
        if not dead_pids:
            return 0

        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            placeholders = ",".join("?" * len(dead_pids))
            cur = conn.execute(
                f"DELETE FROM worker_heartbeat WHERE worker_pid IN ({placeholders})",
                dead_pids,
            )
            conn.commit()
            return cur.rowcount
        finally:
            if not is_shared:
                conn.close()


def _pid_exists(pid: int) -> bool:
    """Check if a process with the given PID is currently running.

    On Windows, os.kill(pid, 0) sends CTRL_C_EVENT (signal 0) which raises
    KeyboardInterrupt. Use OpenProcess instead.
    """
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# Module-level singleton — one per worker process (different PIDs in Gunicorn)
heartbeat = WorkerHeartbeat()
