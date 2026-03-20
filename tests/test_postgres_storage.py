"""
PostgreSQL ContractHistoryBackend integration tests.

Requires a running Postgres instance:
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d

Connection: OPENDQV_TEST_POSTGRES_URL (default: postgresql://opendqv:opendqv@localhost:5432/opendqv)

Tests skip automatically when Postgres is unavailable — main CI is never broken.

Run:
    pytest tests/test_postgres_storage.py -v
"""

import os
import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")

POSTGRES_URL = os.environ.get(
    "OPENDQV_TEST_POSTGRES_URL",
    "postgresql://opendqv:opendqv@localhost:5432/opendqv",
)


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="Postgres unavailable. Start with: docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d",
)


@pytest.fixture
def pg_backend():
    """Fresh PostgresContractHistoryBackend with a clean table for each test."""
    from core.storage import PostgresContractHistoryBackend
    backend = PostgresContractHistoryBackend(POSTGRES_URL)

    # Clean the table before each test
    import psycopg2
    conn = psycopg2.connect(POSTGRES_URL)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE contract_history RESTART IDENTITY")
    finally:
        conn.close()

    yield backend


@pytest.fixture
def sample_contract():
    from core.contracts import DataContract
    return DataContract(name="test_customer", version="1.0", description="Test contract",
                        owner="Test Team")


@pytest.fixture
def sample_contract_v2():
    from core.contracts import DataContract, ContractStatus
    from core.rule_parser import Rule
    c = DataContract(name="test_customer", version="2.0", description="Updated",
                     owner="Test Team", status=ContractStatus.ACTIVE)
    c.rules = [Rule(name="email_valid", field="email", type="regex",
                    pattern="^[^@]+@[^@]+\\.[^@]+$")]
    return c


class TestPostgresContractHistory:
    """PostgresContractHistoryBackend mirrors SQLite ContractHistory behaviour."""

    def test_record_and_retrieve(self, pg_backend, sample_contract):
        pg_backend.record_version(sample_contract)
        history = pg_backend.get_history("test_customer")

        assert len(history) == 1
        assert history[0]["version"] == "1.0"
        assert history[0]["contract_name"] if "contract_name" in history[0] else True
        assert history[0]["description"] == "Test contract"
        assert history[0]["owner"] == "Test Team"

    def test_no_duplicate_consecutive_snapshots(self, pg_backend, sample_contract):
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract)  # identical — should not insert
        history = pg_backend.get_history("test_customer")
        assert len(history) == 1

    def test_new_version_creates_new_snapshot(self, pg_backend, sample_contract, sample_contract_v2):
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract_v2)
        history = pg_backend.get_history("test_customer")
        assert len(history) == 2
        assert history[0]["version"] == "1.0"
        assert history[1]["version"] == "2.0"

    def test_hash_chain_integrity(self, pg_backend, sample_contract, sample_contract_v2):
        """Each entry's prev_hash matches the previous entry's entry_hash."""
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract_v2)
        history = pg_backend.get_history("test_customer")

        assert len(history) == 2
        # Second entry's prev_hash must equal first entry's entry_hash
        assert history[1]["prev_hash"] == history[0]["entry_hash"]
        # Both hashes must be 64-char hex strings
        for snap in history:
            assert len(snap["entry_hash"]) == 64
            assert all(c in "0123456789abcdef" for c in snap["entry_hash"])

    def test_approved_by_stored(self, pg_backend, sample_contract):
        pg_backend.record_version(sample_contract, approved_by="alice@example.com")
        history = pg_backend.get_history("test_customer")
        assert history[0]["approved_by"] == "alice@example.com"

    def test_empty_history(self, pg_backend):
        history = pg_backend.get_history("nonexistent")
        assert history == []

    def test_multiple_contracts_isolated(self, pg_backend):
        from core.contracts import DataContract
        c1 = DataContract(name="contract_a", version="1.0")
        c2 = DataContract(name="contract_b", version="1.0")
        pg_backend.record_version(c1)
        pg_backend.record_version(c2)

        assert len(pg_backend.get_history("contract_a")) == 1
        assert len(pg_backend.get_history("contract_b")) == 1
        assert len(pg_backend.get_history("contract_c")) == 0


class TestPostgresGetAsOf:
    """Point-in-time retrieval — required for EMA/MiFIR regulatory audit."""

    def test_get_as_of_returns_correct_snapshot(self, pg_backend, sample_contract, sample_contract_v2):
        pg_backend.record_version(sample_contract)
        # Capture the timestamp between the two writes
        import time
        time.sleep(0.01)
        from datetime import datetime, timezone
        midpoint = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        pg_backend.record_version(sample_contract_v2)

        snap = pg_backend.get_as_of("test_customer", midpoint)
        assert snap is not None
        assert snap["version"] == "1.0"

    def test_get_as_of_returns_latest_before_timestamp(self, pg_backend, sample_contract, sample_contract_v2):
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract_v2)

        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        snap = pg_backend.get_as_of("test_customer", future)
        assert snap is not None
        assert snap["version"] == "2.0"

    def test_get_as_of_returns_none_before_any_history(self, pg_backend):
        snap = pg_backend.get_as_of("test_customer", "2000-01-01T00:00:00+00:00")
        assert snap is None

    def test_get_as_of_nonexistent_contract(self, pg_backend):
        from datetime import datetime, timezone
        snap = pg_backend.get_as_of("ghost", datetime.now(timezone.utc).isoformat())
        assert snap is None


class TestPostgresDiff:
    """diff() compares two versions — pure Python, same as SQLite."""

    def test_diff_added_rules(self, pg_backend, sample_contract, sample_contract_v2):
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract_v2)

        result = pg_backend.diff("test_customer", "1.0", "2.0")
        assert result["from_version"] == "1.0"
        assert result["to_version"] == "2.0"
        assert any(r["name"] == "email_valid" for r in result["rules_added"])
        assert result["rules_removed"] == []

    def test_diff_unknown_version_raises(self, pg_backend, sample_contract):
        pg_backend.record_version(sample_contract)
        with pytest.raises(ValueError, match="not found"):
            pg_backend.diff("test_customer", "1.0", "99.0")

    def test_diff_metadata_change(self, pg_backend, sample_contract, sample_contract_v2):
        pg_backend.record_version(sample_contract)
        pg_backend.record_version(sample_contract_v2)

        result = pg_backend.diff("test_customer", "1.0", "2.0")
        assert "description" in result["metadata_changed"]


class TestPostgresIsBackend:
    """PostgresContractHistoryBackend satisfies the ContractHistoryBackend ABC."""

    def test_isinstance_check(self, pg_backend):
        from core.storage import ContractHistoryBackend
        assert isinstance(pg_backend, ContractHistoryBackend)

    def test_has_all_abstract_methods(self, pg_backend):
        assert callable(pg_backend.record_version)
        assert callable(pg_backend.get_history)
        assert callable(pg_backend.diff)
        assert callable(pg_backend.get_as_of)
