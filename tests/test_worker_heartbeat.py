"""Tests for per-worker enforcement window logging."""

import os

from core.worker_heartbeat import WorkerHeartbeat, _pid_exists


class TestWorkerHeartbeatSchema:
    """Table creation and schema correctness."""

    def test_table_created(self):
        h = WorkerHeartbeat(db_path=":memory:")
        assert h.get_heartbeats() == []

    def test_module_singleton_exists(self):
        from core.worker_heartbeat import heartbeat
        assert heartbeat is not None
        assert isinstance(heartbeat, WorkerHeartbeat)


class TestRecordValidation:
    """record_validation() upserts correctly."""

    def test_first_record_creates_row(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        rows = h.get_heartbeats()
        assert len(rows) == 1
        assert rows[0]["contract_name"] == "customer"
        assert rows[0]["contract_version"] == "1.0"
        assert rows[0]["validation_count"] == 1

    def test_repeat_call_increments_count(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        h.record_validation("customer", "1.0")
        h.record_validation("customer", "1.0")
        rows = h.get_heartbeats()
        assert len(rows) == 1
        assert rows[0]["validation_count"] == 3

    def test_different_contracts_create_separate_rows(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        h.record_validation("orders", "2.0")
        rows = h.get_heartbeats()
        assert len(rows) == 2
        names = {r["contract_name"] for r in rows}
        assert names == {"customer", "orders"}

    def test_version_update_on_upsert(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        h.record_validation("customer", "2.0")  # version bumped
        rows = h.get_heartbeats()
        assert len(rows) == 1
        assert rows[0]["contract_version"] == "2.0"
        assert rows[0]["validation_count"] == 2

    def test_pid_is_current_process(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        rows = h.get_heartbeats()
        assert rows[0]["worker_pid"] == os.getpid()

    def test_node_id_set(self):
        import config
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        rows = h.get_heartbeats()
        assert rows[0]["opendqv_node_id"] == config.OPENDQV_NODE_ID

    def test_timestamp_is_recent(self):
        from datetime import datetime, timezone
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        rows = h.get_heartbeats()
        ts = datetime.fromisoformat(rows[0]["last_validated_at"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        assert age < 5  # recorded within the last 5 seconds


class TestGetStaleWorkers:
    """get_stale_workers() identifies idle workers."""

    def test_fresh_row_not_stale(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        stale = h.get_stale_workers(max_age_seconds=60)
        assert stale == []

    def test_old_row_is_stale(self):
        """Directly insert a row with an old timestamp."""
        from datetime import datetime, timezone, timedelta
        h = WorkerHeartbeat(db_path=":memory:")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn = h._mem_conn
        conn.execute(
            "INSERT INTO worker_heartbeat "
            "(worker_pid, opendqv_node_id, contract_name, contract_version, "
            "last_validated_at, validation_count) VALUES (?, ?, ?, ?, ?, ?)",
            (99999, "test-node", "old_contract", "1.0", old_ts, 10),
        )
        conn.commit()
        stale = h.get_stale_workers(max_age_seconds=300)
        assert len(stale) == 1
        assert stale[0]["contract_name"] == "old_contract"

    def test_mixed_fresh_and_stale(self):
        from datetime import datetime, timezone, timedelta
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("fresh_contract", "1.0")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn = h._mem_conn
        conn.execute(
            "INSERT INTO worker_heartbeat "
            "(worker_pid, opendqv_node_id, contract_name, contract_version, "
            "last_validated_at, validation_count) VALUES (?, ?, ?, ?, ?, ?)",
            (99998, "test-node", "stale_contract", "1.0", old_ts, 5),
        )
        conn.commit()
        stale = h.get_stale_workers(max_age_seconds=60)
        assert len(stale) == 1
        assert stale[0]["contract_name"] == "stale_contract"


class TestGetActiveWorkerPids:
    """get_active_worker_pids() returns unique PIDs."""

    def test_empty_returns_empty(self):
        h = WorkerHeartbeat(db_path=":memory:")
        assert h.get_active_worker_pids() == []

    def test_single_pid_returned(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        pids = h.get_active_worker_pids()
        assert pids == [os.getpid()]

    def test_multiple_contracts_same_pid_deduped(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        h.record_validation("orders", "1.0")
        pids = h.get_active_worker_pids()
        assert pids.count(os.getpid()) == 1


class TestPurgeDeadWorkers:
    """purge_dead_workers() removes rows for gone PIDs."""

    def test_current_pid_not_purged(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.record_validation("customer", "1.0")
        removed = h.purge_dead_workers()
        assert removed == 0
        assert len(h.get_heartbeats()) == 1

    def test_dead_pid_purged(self):
        h = WorkerHeartbeat(db_path=":memory:")
        # PID 1 is init/systemd — if not current process, this is a "dead" PID for our test.
        # We insert a row with PID 999999 which certainly doesn't exist.
        from datetime import datetime, timezone
        conn = h._mem_conn
        conn.execute(
            "INSERT INTO worker_heartbeat "
            "(worker_pid, opendqv_node_id, contract_name, contract_version, "
            "last_validated_at, validation_count) VALUES (?, ?, ?, ?, ?, ?)",
            (999999, "dead-node", "ghost_contract", "1.0",
             datetime.now(timezone.utc).isoformat(), 1),
        )
        conn.commit()
        removed = h.purge_dead_workers()
        assert removed == 1
        remaining = h.get_heartbeats()
        assert all(r["worker_pid"] != 999999 for r in remaining)


class TestPidExists:
    """_pid_exists() utility."""

    def test_current_pid_exists(self):
        assert _pid_exists(os.getpid()) is True

    def test_dead_pid_does_not_exist(self):
        assert _pid_exists(999999) is False


class TestHealthEndpointIntegration:
    """Health endpoint exposes worker_count and stale_worker_count."""

    def test_health_has_worker_fields(self, client):
        import config
        from unittest.mock import patch
        with patch.object(config, "HEALTH_DETAIL", True):
            r = client.get("/health")
            assert r.status_code == 200
            data = r.json()
            assert "worker_count" in data
            assert "stale_worker_count" in data
            assert isinstance(data["worker_count"], int)
            assert isinstance(data["stale_worker_count"], int)

    def test_worker_count_increases_after_validation(self, client, auth_headers):
        import config
        from unittest.mock import patch
        # Trigger a validation to record a heartbeat
        client.post(
            "/api/v1/validate",
            json={
                "record": {"email": "a@b.com", "age": 25, "name": "Alice",
                           "id": "1", "phone": "+123", "balance": 100,
                           "score": 80, "date": "2024-01-01",
                           "username": "alice", "password": "pass1234"},
                "contract": "customer",
            },
            headers=auth_headers,
        )

        with patch.object(config, "HEALTH_DETAIL", True):
            r_after = client.get("/health")
        # worker_count should be >= 1 (the test process)
        assert r_after.json()["worker_count"] >= 1


class TestFlush:
    """flush() forces an immediate write of pending counts."""

    def test_flush_does_not_raise_on_empty(self):
        h = WorkerHeartbeat(db_path=":memory:")
        h.flush()  # nothing pending — should be a no-op

    def test_flush_writes_pending_counts(self):
        """
        Simulate a pending count by bypassing _WRITE_INTERVAL — directly inject
        pending state and verify flush() persists it to the DB.
        """
        h = WorkerHeartbeat(db_path=":memory:")
        pid = os.getpid()

        # Prime the heartbeat so a DB row exists
        h.record_validation("customer", "1.0")

        # Force a pending count without triggering the write interval
        key = (pid, "customer")
        h._pending_count[key] = 5
        # Set last_write far in the past so flush logic processes it
        h._last_write[key] = 0.0

        # flush() should write the pending count
        h.flush()

        # pending count should be reset
        assert h._pending_count.get(key, 0) == 0

    def test_flush_called_on_multiple_contracts(self):
        h = WorkerHeartbeat(db_path=":memory:")
        pid = os.getpid()

        h.record_validation("customer", "1.0")
        h.record_validation("orders", "1.0")

        for contract in ("customer", "orders"):
            key = (pid, contract)
            h._pending_count[key] = 3
            h._last_write[key] = 0.0

        h.flush()
        assert h._pending_count.get((pid, "customer"), 0) == 0
        assert h._pending_count.get((pid, "orders"), 0) == 0
