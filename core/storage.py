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

    @abc.abstractmethod
    def get_as_of(self, contract_name: str, timestamp: str) -> Optional[dict]:
        """Return the most recent snapshot with updated_at <= timestamp, or None."""


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
    PostgreSQL implementation of ContractHistoryBackend.

    Requires psycopg2-binary: pip install 'opendqv[postgres]'
    Connection string via OPENDQV_DB_URL:
        postgresql://opendqv:password@localhost:5432/opendqv

    Tables are created automatically on first use (CREATE TABLE IF NOT EXISTS).
    Migration from SQLite to Postgres is not supported in v1.0.0 — start a
    fresh Postgres instance. The SQLite database remains usable in parallel.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS contract_history (
            id              BIGSERIAL PRIMARY KEY,
            contract_name   TEXT        NOT NULL,
            version         TEXT        NOT NULL,
            status          TEXT        NOT NULL,
            description     TEXT,
            owner           TEXT,
            rules           TEXT        NOT NULL,
            contexts        TEXT        NOT NULL,
            opendqv_node_id TEXT        NOT NULL,
            updated_at      TEXT        NOT NULL,
            prev_hash       TEXT        NOT NULL DEFAULT '',
            entry_hash      TEXT        NOT NULL DEFAULT '',
            approved_by     TEXT,
            proposed_by     TEXT,
            proposed_at     TEXT,
            reviewed_by     TEXT,
            reviewed_at     TEXT,
            rejected_by     TEXT,
            rejected_at     TEXT,
            rejection_reason TEXT,
            sensitive_fields TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_contract_history_name
            ON contract_history(contract_name);
    """

    def __init__(self, db_url: str):
        if not db_url:
            raise ValueError(
                "PostgreSQL backend requires OPENDQV_DB_URL to be set. "
                "Example: postgresql://opendqv:password@localhost:5432/opendqv"
            )
        self.db_url = db_url
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "PostgreSQL backend requires 'psycopg2-binary'. "
                "Install it with: pip install 'opendqv[postgres]' "
                "or: pip install psycopg2-binary"
            ) from exc
        self._init_db()

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self.db_url)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._DDL)
        finally:
            conn.close()

    def record_version(self, contract, approved_by: Optional[str] = None) -> None:
        """Snapshot the current contract state with hash-chain integrity."""
        import copy
        import json
        from datetime import datetime, timezone

        rules = [r.model_dump(by_alias=True, exclude_none=True) for r in contract.rules]
        contexts = copy.deepcopy(contract.contexts)
        updated_at = datetime.now(timezone.utc).isoformat()
        rules_json = json.dumps(rules, sort_keys=True)
        contexts_json = json.dumps(contexts, sort_keys=True)

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    # Fetch most recent snapshot to detect duplicates and build hash chain
                    cur.execute(
                        "SELECT version, status, description, owner, rules, contexts, entry_hash "
                        "FROM contract_history WHERE contract_name = %s "
                        "ORDER BY id DESC LIMIT 1",
                        (contract.name,),
                    )
                    row = cur.fetchone()

                    from core.contracts import _GENESIS_HASH, _compute_entry_hash
                    import config

                    prev_hash = _GENESIS_HASH
                    if row:
                        last_version, last_status, last_desc, last_owner, \
                            last_rules, last_contexts, last_entry_hash = row
                        if (last_version == contract.version
                                and last_status == contract.status.value
                                and last_rules == rules_json
                                and last_contexts == contexts_json
                                and last_desc == contract.description
                                and last_owner == contract.owner):
                            return  # no change — skip duplicate snapshot
                        prev_hash = last_entry_hash or _GENESIS_HASH

                    entry_hash = _compute_entry_hash(
                        prev_hash, contract.name, contract.version, contract.status.value,
                        rules_json, contexts_json, config.OPENDQV_NODE_ID, updated_at,
                    )

                    cur.execute(
                        "INSERT INTO contract_history "
                        "(contract_name, version, status, description, owner, rules, contexts, "
                        " opendqv_node_id, updated_at, prev_hash, entry_hash, approved_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (contract.name, contract.version, contract.status.value,
                         contract.description, contract.owner, rules_json, contexts_json,
                         config.OPENDQV_NODE_ID, updated_at, prev_hash, entry_hash, approved_by),
                    )
        finally:
            conn.close()

    def get_as_of(self, contract_name: str, timestamp: str) -> Optional[dict]:
        """Return the most recent snapshot with updated_at <= timestamp, or None."""
        import json

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version, status, description, owner, rules, contexts, "
                    "       opendqv_node_id, updated_at "
                    "FROM contract_history "
                    "WHERE contract_name = %s AND updated_at <= %s "
                    "ORDER BY id DESC LIMIT 1",
                    (contract_name, timestamp),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return None
        version, status, description, owner, rules_json, contexts_json, \
            opendqv_node_id, updated_at = row
        return {
            "version": version,
            "status": status,
            "description": description or "",
            "owner": owner or "",
            "rules": json.loads(rules_json),
            "contexts": json.loads(contexts_json),
            "opendqv_node_id": opendqv_node_id,
            "updated_at": updated_at,
        }

    def get_history(self, contract_name: str) -> list[dict]:
        """Return all snapshots for a contract, ordered by id ascending."""
        import json

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version, status, description, owner, rules, contexts, "
                    "       opendqv_node_id, updated_at, prev_hash, entry_hash, approved_by, "
                    "       proposed_by, proposed_at, rejected_by, rejected_at, rejection_reason "
                    "FROM contract_history WHERE contract_name = %s ORDER BY id",
                    (contract_name,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        history = []
        for (version, status, description, owner, rules_json, contexts_json,
             opendqv_node_id, updated_at, prev_hash, entry_hash, approved_by,
             proposed_by, proposed_at, rejected_by, rejected_at, rejection_reason) in rows:
            history.append({
                "version": version,
                "status": status,
                "description": description,
                "owner": owner,
                "rules": json.loads(rules_json),
                "contexts": json.loads(contexts_json),
                "opendqv_node_id": opendqv_node_id,
                "updated_at": updated_at,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "approved_by": approved_by,
                "proposed_by": proposed_by,
                "proposed_at": proposed_at,
                "rejected_by": rejected_by,
                "rejected_at": rejected_at,
                "rejection_reason": rejection_reason,
            })
        return history

    def diff(self, contract_name: str, version_a: str, version_b: str) -> dict:
        """Compare two named versions. Pure Python over get_history() results."""
        history = self.get_history(contract_name)

        snap_a = next((s for s in history if s["version"] == version_a), None)
        snap_b = None
        for s in history:
            if s["version"] == version_b:
                snap_b = s  # take latest snapshot for this version

        if not snap_a:
            raise ValueError(f"Version '{version_a}' not found in history for '{contract_name}'")
        if not snap_b:
            raise ValueError(f"Version '{version_b}' not found in history for '{contract_name}'")

        rules_a = {r["name"]: r for r in snap_a["rules"]}
        rules_b = {r["name"]: r for r in snap_b["rules"]}
        names_a, names_b = set(rules_a), set(rules_b)

        rules_added   = [{"name": n, "type": rules_b[n].get("type",""), "field": rules_b[n].get("field","")}
                         for n in sorted(names_b - names_a)]
        rules_removed = [{"name": n, "type": rules_a[n].get("type",""), "field": rules_a[n].get("field","")}
                         for n in sorted(names_a - names_b)]
        rules_changed = []
        for name in sorted(names_a & names_b):
            ra, rb = rules_a[name], rules_b[name]
            if ra != rb:
                all_keys = set(ra) | set(rb)
                changes = {k: {"old": ra.get(k), "new": rb.get(k)}
                           for k in sorted(all_keys) if ra.get(k) != rb.get(k)}
                rules_changed.append({"name": name, "field": rb.get("field", ra.get("field","")), "changes": changes})

        metadata_changed = {k: {"old": snap_a.get(k), "new": snap_b.get(k)}
                            for k in ("status", "description", "owner")
                            if snap_a.get(k) != snap_b.get(k)}

        return {
            "contract": contract_name,
            "from_version": version_a,
            "to_version": version_b,
            "rules_added": rules_added,
            "rules_removed": rules_removed,
            "rules_changed": rules_changed,
            "metadata_changed": metadata_changed,
        }


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
            "FederationLog PostgreSQL backend is not yet available. "
            "Set OPENDQV_DB_BACKEND=sqlite to use federation features."
        )

    def update_status(self, lsn: int, status: str) -> bool:
        raise NotImplementedError(
            "FederationLog PostgreSQL backend is not yet available. "
            "Set OPENDQV_DB_BACKEND=sqlite to use federation features."
        )

    def get_since(self, lsn: int, contract_name=None) -> list[dict]:
        raise NotImplementedError(
            "FederationLog PostgreSQL backend is not yet available. "
            "Set OPENDQV_DB_BACKEND=sqlite to use federation features."
        )

    def get_pending(self, contract_name=None) -> list[dict]:
        raise NotImplementedError(
            "FederationLog PostgreSQL backend is not yet available. "
            "Set OPENDQV_DB_BACKEND=sqlite to use federation features."
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
