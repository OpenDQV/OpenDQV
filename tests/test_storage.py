"""Tests for the storage backend abstraction layer."""

import pytest

from core.storage import (
    ContractHistoryBackend,
    FederationLogBackend,
    PostgresContractHistoryBackend,
    PostgresFederationLogBackend,
    get_contract_history_backend,
    get_federation_log_backend,
)
from core.contracts import ContractHistory, DataContract
from core.federation import FederationLog


class TestABCConformance:
    """Verify that concrete implementations satisfy the ABCs."""

    def test_contract_history_is_backend(self):
        h = ContractHistory(db_path=":memory:")
        assert isinstance(h, ContractHistoryBackend)

    def test_federation_log_is_backend(self):
        log = FederationLog(db_path=":memory:")
        assert isinstance(log, FederationLogBackend)

    def test_postgres_contract_history_is_backend(self):
        # The class satisfies the ABC even though methods raise NotImplementedError
        assert issubclass(PostgresContractHistoryBackend, ContractHistoryBackend)

    def test_postgres_federation_log_is_backend(self):
        assert issubclass(PostgresFederationLogBackend, FederationLogBackend)


class TestPostgresStubs:
    """Postgres stubs raise clear errors — no silent fallback to SQLite."""

    def _make_pg_contract_history(self):
        # psycopg2 is not installed in the test environment — ImportError is expected.
        # We test the ValueError (missing URL) which fires before the import check.
        with pytest.raises((ValueError, ImportError)):
            PostgresContractHistoryBackend("")

    def _make_pg_federation_log(self):
        with pytest.raises((ValueError, ImportError)):
            PostgresFederationLogBackend("")

    def test_postgres_contract_history_empty_url_raises_value_error(self):
        with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
            PostgresContractHistoryBackend("")

    def test_postgres_federation_log_empty_url_raises_value_error(self):
        with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
            PostgresFederationLogBackend("")

    def test_postgres_contract_history_no_psycopg2_raises_import_error(self):
        """When psycopg2 is absent and URL is provided, ImportError is raised."""
        import unittest.mock as mock
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "psycopg2":
                raise ImportError("No module named 'psycopg2'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="psycopg2"):
                PostgresContractHistoryBackend("postgresql://localhost/opendqv")

    def test_postgres_federation_log_no_psycopg2_raises_import_error(self):
        import unittest.mock as mock
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "psycopg2":
                raise ImportError("No module named 'psycopg2'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="psycopg2"):
                PostgresFederationLogBackend("postgresql://localhost/opendqv")


class TestFactoryFunctions:
    """get_contract_history_backend() and get_federation_log_backend() factories."""

    def test_sqlite_backend_by_default(self):
        backend = get_contract_history_backend(":memory:")
        assert isinstance(backend, ContractHistory)

    def test_sqlite_federation_backend_by_default(self):
        backend = get_federation_log_backend(":memory:")
        assert isinstance(backend, FederationLog)

    def test_factory_returns_working_sqlite_history(self):
        backend = get_contract_history_backend(":memory:")
        contract = DataContract(name="test", version="1.0")
        backend.record_version(contract)
        history = backend.get_history("test")
        assert len(history) == 1
        assert history[0]["version"] == "1.0"

    def test_factory_returns_working_sqlite_federation_log(self):
        backend = get_federation_log_backend(":memory:")
        lsn = backend.record_event("push", "customer", "1.0", "node-a")
        assert lsn >= 1
        events = backend.get_since(0)
        assert len(events) == 1

    def test_postgres_factory_raises_value_error_without_url(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "DB_BACKEND", "postgres", raising=False)
        monkeypatch.setattr(config, "DB_URL", "", raising=False)

        with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
            get_contract_history_backend()

    def test_postgres_federation_factory_raises_without_url(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "DB_BACKEND", "postgres", raising=False)
        monkeypatch.setattr(config, "DB_URL", "", raising=False)

        with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
            get_federation_log_backend()
