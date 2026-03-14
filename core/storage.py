"""
Storage backend abstraction for OpenDQV persistence.

Defines abstract interfaces for contract history and federation log storage,
with SQLite as the default implementation and a PostgreSQL stub that marks
the interface boundary for the enterprise tier.

Backend selection:
    OPENDQV_DB_BACKEND=sqlite  (default) — local SQLite, zero dependencies
    OPENDQV_DB_BACKEND=postgres          — PostgreSQL, requires psycopg2-binary

Connection config:
    SQLite:   OPENDQV_DB_PATH=/path/to/opendqv.db  (or :memory: for tests)
    Postgres: OPENDQV_DB_URL=postgresql://user:pass@host:5432/opendqv

Why this abstraction exists:
    SQLite is perfect for single-node deployments (zero ops, embedded, fast).
    PostgreSQL is required for multi-node federation with the 99.9% SLA tier —
    shared state across Gunicorn workers and across nodes in the same datacenter.
    The ABCs here make the interface contract explicit so the enterprise
    PostgreSQL implementation can drop in without touching any caller code.
"""

import abc
from typing import Optional


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------

class ContractHistoryBackend(abc.ABC):
    """Abstract interface for contract history persistence."""

    @abc.abstractmethod
    def record_version(self, contract) -> None:
        """Snapshot the current state of a contract."""

    @abc.abstractmethod
    def get_history(self, contract_name: str) -> list[dict]:
        """Return all snapshots for a contract, ordered by id ascending."""

    @abc.abstractmethod
    def diff(self, contract_name: str, version_a: str, version_b: str) -> dict:
        """Compare two named versions. Raises ValueError if either is not found."""


class FederationLogBackend(abc.ABC):
    """Abstract interface for federation event log persistence."""

    @abc.abstractmethod
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
        """Insert a federation event. Returns the assigned lsn."""

    @abc.abstractmethod
    def update_status(self, lsn: int, status: str) -> bool:
        """Transition the status of an existing event. Returns True if updated."""

    @abc.abstractmethod
    def get_since(self, lsn: int, contract_name: Optional[str] = None) -> list[dict]:
        """Return all events with lsn > given value, ordered by lsn ascending."""

    @abc.abstractmethod
    def get_pending(self, contract_name: Optional[str] = None) -> list[dict]:
        """Return all events with status='pending'."""


# ---------------------------------------------------------------------------
# PostgreSQL stubs — implement the interface, raise NotImplementedError.
# These exist to make the interface boundary explicit and to fail fast with a
# clear message if someone sets OPENDQV_DB_BACKEND=postgres before the
# enterprise implementation is installed.
# ---------------------------------------------------------------------------

class PostgresContractHistoryBackend(ContractHistoryBackend):
    """
    PostgreSQL implementation stub for ContractHistoryBackend.

    Interface is wired; implementation ships in the enterprise tier.
    Raises NotImplementedError on any call so misconfigured deployments
    fail loudly rather than silently falling back to SQLite.
    """

    def __init__(self, db_url: str):
        if not db_url:
            raise ValueError(
                "PostgreSQL backend requires OPENDQV_DB_URL to be set. "
                "Example: postgresql://opendqv:password@localhost:5432/opendqv"
            )
        self.db_url = db_url
        # Verify driver availability at construction time
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL backend requires 'psycopg2-binary'. "
                "Install it with: pip install 'opendqv[postgres]' "
                "or: pip install psycopg2-binary"
            ) from exc

    def record_version(self, contract) -> None:
        raise NotImplementedError(
            "PostgreSQL ContractHistoryBackend is part of the enterprise tier. "
            "See https://opendqv.io/enterprise for access."
        )

    def get_history(self, contract_name: str) -> list[dict]:
        raise NotImplementedError(
            "PostgreSQL ContractHistoryBackend is part of the enterprise tier."
        )

    def diff(self, contract_name: str, version_a: str, version_b: str) -> dict:
        raise NotImplementedError(
            "PostgreSQL ContractHistoryBackend is part of the enterprise tier."
        )


class PostgresFederationLogBackend(FederationLogBackend):
    """
    PostgreSQL implementation stub for FederationLogBackend.

    Interface is wired; implementation ships in the enterprise tier.
    """

    def __init__(self, db_url: str):
        if not db_url:
            raise ValueError(
                "PostgreSQL backend requires OPENDQV_DB_URL to be set."
            )
        self.db_url = db_url
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL backend requires 'psycopg2-binary'. "
                "Install it with: pip install psycopg2-binary"
            ) from exc

    def record_event(self, event_type, contract_name, contract_version,
                     source_node, target_node=None, payload=None, status="pending") -> int:
        raise NotImplementedError(
            "PostgreSQL FederationLogBackend is part of the enterprise tier."
        )

    def update_status(self, lsn: int, status: str) -> bool:
        raise NotImplementedError(
            "PostgreSQL FederationLogBackend is part of the enterprise tier."
        )

    def get_since(self, lsn: int, contract_name=None) -> list[dict]:
        raise NotImplementedError(
            "PostgreSQL FederationLogBackend is part of the enterprise tier."
        )

    def get_pending(self, contract_name=None) -> list[dict]:
        raise NotImplementedError(
            "PostgreSQL FederationLogBackend is part of the enterprise tier."
        )


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def get_contract_history_backend(db_path_or_url: Optional[str] = None) -> ContractHistoryBackend:
    """
    Return the configured ContractHistoryBackend.

    Args:
        db_path_or_url: Override the path/URL. Defaults to OPENDQV_DB_PATH
                        (SQLite) or OPENDQV_DB_URL (Postgres).
    """
    import config
    backend_name = getattr(config, "DB_BACKEND", "sqlite")

    if backend_name == "postgres":
        url = db_path_or_url or getattr(config, "DB_URL", "")
        return PostgresContractHistoryBackend(url)

    # SQLite (default)
    from core.contracts import ContractHistory
    path = db_path_or_url or config.DB_PATH
    return ContractHistory(path)


def get_federation_log_backend(db_path_or_url: Optional[str] = None) -> FederationLogBackend:
    """
    Return the configured FederationLogBackend.

    Args:
        db_path_or_url: Override the path/URL.
    """
    import config
    backend_name = getattr(config, "DB_BACKEND", "sqlite")

    if backend_name == "postgres":
        url = db_path_or_url or getattr(config, "DB_URL", "")
        return PostgresFederationLogBackend(url)

    from core.federation import FederationLog
    path = db_path_or_url or config.DB_PATH
    return FederationLog(path)
