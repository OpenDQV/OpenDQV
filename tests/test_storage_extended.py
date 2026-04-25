"""
Extended storage tests — PostgreSQL backend via psycopg2 mocking.

core/storage.py lines 161–338 are the PostgreSQL implementation which requires
a live database. These tests use unittest.mock to exercise all code paths without
a real PostgreSQL instance.
"""
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pg_backend():
    """
    Construct a PostgresContractHistoryBackend with psycopg2 fully mocked.
    Returns (backend, mock_psycopg2_module).
    """
    mock_psycopg2 = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    # psycopg2.connect() returns a connection context manager
    mock_psycopg2.connect.return_value = mock_conn
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []

    with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
        from opendqv.core.storage import PostgresContractHistoryBackend
        backend = PostgresContractHistoryBackend("postgresql://fake/db")

    return backend, mock_psycopg2, mock_conn, mock_cursor


def _make_contract(name="test_contract", version="1.0", status="active"):
    """Build a minimal mock contract object."""
    from unittest.mock import MagicMock
    from enum import Enum

    class FakeStatus(str, Enum):
        active = "active"
        draft = "draft"

    contract = MagicMock()
    contract.name = name
    contract.version = version
    contract.status.value = status
    contract.description = "Test description"
    contract.owner = "test-owner"
    contract.owner_email = None
    contract.owner_team = None
    contract.asset_id = None
    contract.downstream_consumers = []
    contract.rules = []
    contract.contexts = {}
    return contract


# ---------------------------------------------------------------------------
# TestPostgresBackendInit
# ---------------------------------------------------------------------------

class TestPostgresBackendInit:

    def test_requires_db_url(self):
        mock_psycopg2 = MagicMock()
        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            from opendqv.core.storage import PostgresContractHistoryBackend
            with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
                PostgresContractHistoryBackend("")

    def test_missing_psycopg2_raises_import_error(self):
        with patch.dict("sys.modules", {"psycopg2": None}):
            # Force reimport
            import importlib
            import opendqv.core.storage
            importlib.reload(opendqv.core.storage)
            from opendqv.core.storage import PostgresContractHistoryBackend
            with pytest.raises(ImportError, match="psycopg2"):
                PostgresContractHistoryBackend("postgresql://fake/db")


# ---------------------------------------------------------------------------
# TestPostgresRecordVersion
# ---------------------------------------------------------------------------

class TestPostgresRecordVersion:
    """record_version() — covers lines 159–215."""

    def test_record_version_new_contract(self):
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        contract = _make_contract()
        mock_cursor.fetchone.return_value = None  # no previous snapshot

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            backend.record_version(contract)

        # INSERT should have been called
        assert mock_cursor.execute.called

    def test_record_version_skip_duplicate(self):
        """If last snapshot is identical, record_version skips the INSERT."""
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        contract = _make_contract()

        rules_json = json.dumps([], sort_keys=True)
        contexts_json = json.dumps({}, sort_keys=True)

        # Simulate previous row with identical content (v2.3.0 shape, 11 cols)
        mock_cursor.fetchone.return_value = (
            contract.version,           # last_version
            contract.status.value,      # last_status
            contract.description,       # last_desc
            contract.owner,             # last_owner
            contract.owner_email,       # last_owner_email
            contract.owner_team,        # last_owner_team
            contract.asset_id,          # last_asset_id
            "[]",                       # last_downstream
            rules_json,                 # last_rules
            contexts_json,              # last_contexts
            "abc" * 21 + "d",           # last_entry_hash (64 chars)
        )

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            backend.record_version(contract)

        # No INSERT should have been called (only the SELECT)
        insert_calls = [c for c in mock_cursor.execute.call_args_list
                        if "INSERT" in str(c)]
        assert len(insert_calls) == 0


# ---------------------------------------------------------------------------
# TestPostgresGetAsOf
# ---------------------------------------------------------------------------

class TestPostgresGetAsOf:
    """get_as_of() — covers lines 217–249."""

    def test_get_as_of_no_result(self):
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        mock_cursor.fetchone.return_value = None

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            result = backend.get_as_of("test_contract", "2026-01-01T00:00:00")

        assert result is None

    def test_get_as_of_with_result(self):
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        mock_cursor.fetchone.return_value = (
            "1.0", "active", "description", "owner",
            None, None, None, None,
            json.dumps([]), json.dumps({}),
            "node-1", "2026-01-01T12:00:00",
        )

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            result = backend.get_as_of("test_contract", "2026-01-01T23:59:59")

        assert result is not None
        assert result["version"] == "1.0"
        assert result["status"] == "active"
        assert result["rules"] == []


# ---------------------------------------------------------------------------
# TestPostgresGetHistory
# ---------------------------------------------------------------------------

class TestPostgresGetHistory:
    """get_history() — covers lines 251–291."""

    def test_get_history_empty(self):
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        mock_cursor.fetchall.return_value = []

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            result = backend.get_history("test_contract")

        assert result == []

    def test_get_history_with_rows(self):
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        mock_cursor.fetchall.return_value = [
            (
                "1.0", "active", "desc", "owner",
                None, None, None, None,
                json.dumps([{"name": "r1", "type": "not_empty", "field": "email"}]),
                json.dumps({}),
                "node-1", "2026-01-01T12:00:00",
                "0" * 64, "abc" * 21 + "d", "0" * 64, 2,
                "approver", None, None, None, None, None,
            )
        ]

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            result = backend.get_history("test_contract")

        assert len(result) == 1
        assert result[0]["version"] == "1.0"
        assert result[0]["rules"][0]["name"] == "r1"


# ---------------------------------------------------------------------------
# TestPostgresDiff
# ---------------------------------------------------------------------------

class TestPostgresDiff:
    """diff() — covers lines 293–337. Pure Python over get_history()."""

    def _backend_with_history(self, history_rows):
        """Return a backend whose get_history() returns the given rows."""
        backend, mock_psycopg2, mock_conn, mock_cursor = _make_pg_backend()
        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            backend._connect = MagicMock(return_value=mock_conn)
            backend.get_history = MagicMock(return_value=history_rows)
        return backend

    def test_diff_no_changes(self):
        rules = [{"name": "r1", "type": "not_empty", "field": "email"}]
        history = [
            {"version": "1.0", "status": "active", "description": "d", "owner": "o", "rules": rules, "contexts": {}},
            {"version": "1.1", "status": "active", "description": "d", "owner": "o", "rules": rules, "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        result = backend.diff("test", "1.0", "1.1")
        assert result["rules_added"] == []
        assert result["rules_removed"] == []
        assert result["rules_changed"] == []

    def test_diff_rule_added(self):
        rules_a = [{"name": "r1", "type": "not_empty", "field": "email"}]
        rules_b = [
            {"name": "r1", "type": "not_empty", "field": "email"},
            {"name": "r2", "type": "min", "field": "age"},
        ]
        history = [
            {"version": "1.0", "status": "active", "description": "d", "owner": "o", "rules": rules_a, "contexts": {}},
            {"version": "1.1", "status": "active", "description": "d", "owner": "o", "rules": rules_b, "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        result = backend.diff("test", "1.0", "1.1")
        assert len(result["rules_added"]) == 1
        assert result["rules_added"][0]["name"] == "r2"

    def test_diff_rule_removed(self):
        rules_a = [
            {"name": "r1", "type": "not_empty", "field": "email"},
            {"name": "r2", "type": "min", "field": "age"},
        ]
        rules_b = [{"name": "r1", "type": "not_empty", "field": "email"}]
        history = [
            {"version": "1.0", "status": "active", "description": "d", "owner": "o", "rules": rules_a, "contexts": {}},
            {"version": "1.1", "status": "active", "description": "d", "owner": "o", "rules": rules_b, "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        result = backend.diff("test", "1.0", "1.1")
        assert len(result["rules_removed"]) == 1
        assert result["rules_removed"][0]["name"] == "r2"

    def test_diff_rule_changed(self):
        rules_a = [{"name": "r1", "type": "min", "field": "age", "min_value": 18}]
        rules_b = [{"name": "r1", "type": "min", "field": "age", "min_value": 21}]
        history = [
            {"version": "1.0", "status": "active", "description": "d", "owner": "o", "rules": rules_a, "contexts": {}},
            {"version": "1.1", "status": "active", "description": "d", "owner": "o", "rules": rules_b, "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        result = backend.diff("test", "1.0", "1.1")
        assert len(result["rules_changed"]) == 1
        assert result["rules_changed"][0]["name"] == "r1"
        assert "min_value" in result["rules_changed"][0]["changes"]

    def test_diff_metadata_changed(self):
        rules = [{"name": "r1", "type": "not_empty", "field": "email"}]
        history = [
            {"version": "1.0", "status": "draft", "description": "old desc", "owner": "o", "rules": rules, "contexts": {}},
            {"version": "1.1", "status": "active", "description": "new desc", "owner": "o", "rules": rules, "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        result = backend.diff("test", "1.0", "1.1")
        assert "status" in result["metadata_changed"]
        assert "description" in result["metadata_changed"]

    def test_diff_version_a_not_found(self):
        history = [
            {"version": "1.1", "status": "active", "description": "d", "owner": "o", "rules": [], "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        with pytest.raises(ValueError, match="Version '1.0' not found"):
            backend.diff("test", "1.0", "1.1")

    def test_diff_version_b_not_found(self):
        history = [
            {"version": "1.0", "status": "active", "description": "d", "owner": "o", "rules": [], "contexts": {}},
        ]
        backend = self._backend_with_history(history)
        with pytest.raises(ValueError, match="Version '2.0' not found"):
            backend.diff("test", "1.0", "2.0")
