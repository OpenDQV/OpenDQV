"""
Postgres integration tests — application-layer validate-before-INSERT pattern.

These tests use LocalValidator (no API server) to validate records before
writing to Postgres. They require a running Postgres instance:

    docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d

Skip gracefully if psycopg2 or Postgres is unavailable so the main CI suite
(SQLite-only) is never broken by a missing database.

Run just these tests:
    pytest tests/test_postgres_integration.py -v

See docs/postgres_integration.md for the full integration guide.
"""

import os
import pytest

from sdk.local import LocalValidator

# ── Fixtures ──────────────────────────────────────────────────────────────────

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")

POSTGRES_URL = os.environ.get(
    "OPENDQV_TEST_POSTGRES_URL",
    "postgresql://opendqv:opendqv@localhost:5432/opendqv",
)


def _pg_conn():
    """Return a psycopg2 connection, or raise OperationalError if unavailable."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    return psycopg2.connect(POSTGRES_URL, cursor_factory=RealDictCursor)


@pytest.fixture(scope="module")
def pg_conn():
    """Module-scoped Postgres connection. Skips entire module if unavailable."""
    try:
        conn = _pg_conn()
    except Exception as exc:
        pytest.skip(f"Postgres unavailable ({exc}). Start with: docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_table(pg_conn):
    """Drop and recreate test table before each test."""
    cur = pg_conn.cursor()
    cur.execute("DROP TABLE IF EXISTS test_customers")
    cur.execute("DROP TABLE IF EXISTS test_customers_quarantine")
    cur.execute("""
        CREATE TABLE test_customers (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            email VARCHAR NOT NULL,
            age INTEGER,
            phone VARCHAR,
            score NUMERIC,
            date VARCHAR,
            username VARCHAR,
            password VARCHAR,
            balance NUMERIC
        )
    """)
    cur.execute("""
        CREATE TABLE test_customers_quarantine (
            id SERIAL PRIMARY KEY,
            source_id VARCHAR,
            record_json JSONB,
            errors_json JSONB,
            rejected_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    pg_conn.commit()
    yield
    cur.execute("DROP TABLE IF EXISTS test_customers")
    cur.execute("DROP TABLE IF EXISTS test_customers_quarantine")
    pg_conn.commit()


@pytest.fixture
def validator():
    return LocalValidator()


# ── Test data ─────────────────────────────────────────────────────────────────

_VALID_RECORDS = [
    {
        "id": "c1", "name": "Alice", "email": "alice@example.com", "age": 30,
        "phone": "+447911123456", "score": 85, "date": "2024-01-15",
        "username": "alice123", "password": "securepass", "balance": 100.0,
    },
    {
        "id": "c2", "name": "Bob", "email": "bob@example.com", "age": 25,
        "phone": "+14155552671", "score": 72, "date": "2024-03-01",
        "username": "bob_data", "password": "p@ssword99", "balance": 500.0,
    },
]

_INVALID_RECORD = {
    "id": "bad", "name": "", "email": "not-an-email", "age": -1,
    "phone": "+447900000001", "score": 200, "date": "2024-01-15",
    "username": "u", "password": "short", "balance": -50.0,
}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestValidateBeforeInsert:
    """Application-layer validate-before-INSERT pattern."""

    def test_clean_records_written_to_postgres(self, validator, pg_conn):
        """Valid records pass validation and are inserted into Postgres."""
        result = validator.validate_batch(_VALID_RECORDS, contract="customer")
        assert result["summary"]["failed"] == 0

        cur = pg_conn.cursor()
        for record in _VALID_RECORDS:
            cur.execute(
                "INSERT INTO test_customers (id, name, email, age, phone, score, "
                "date, username, password, balance) VALUES "
                "(%(id)s, %(name)s, %(email)s, %(age)s, %(phone)s, %(score)s, "
                "%(date)s, %(username)s, %(password)s, %(balance)s)",
                record,
            )
        pg_conn.commit()

        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers")
        row = cur.fetchone()
        assert row["cnt"] == 2

    def test_invalid_record_blocked_from_postgres(self, validator, pg_conn):
        """Invalid record fails validation and is NOT inserted; goes to quarantine."""
        import json
        result = validator.validate(_INVALID_RECORD, contract="customer")
        assert result["valid"] is False

        cur = pg_conn.cursor()
        # Quarantine instead of inserting
        cur.execute(
            "INSERT INTO test_customers_quarantine (source_id, record_json, errors_json) "
            "VALUES (%s, %s::jsonb, %s::jsonb)",
            [
                _INVALID_RECORD["id"],
                json.dumps(_INVALID_RECORD),
                json.dumps(result["errors"]),
            ],
        )
        pg_conn.commit()

        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers")
        assert cur.fetchone()["cnt"] == 0

        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers_quarantine")
        assert cur.fetchone()["cnt"] == 1

    def test_mixed_batch_quarantine_pattern(self, validator, pg_conn):
        """Mixed batch: clean records inserted, invalid quarantined."""
        import json
        mixed = _VALID_RECORDS + [_INVALID_RECORD]
        result = validator.validate_batch(mixed, contract="customer")

        clean = [mixed[r["index"]] for r in result["results"] if r["valid"]]
        rejected = [
            {"record": mixed[r["index"]], "errors": r["errors"]}
            for r in result["results"] if not r["valid"]
        ]

        cur = pg_conn.cursor()
        for record in clean:
            cur.execute(
                "INSERT INTO test_customers (id, name, email, age, phone, score, "
                "date, username, password, balance) VALUES "
                "(%(id)s, %(name)s, %(email)s, %(age)s, %(phone)s, %(score)s, "
                "%(date)s, %(username)s, %(password)s, %(balance)s)",
                record,
            )
        for item in rejected:
            cur.execute(
                "INSERT INTO test_customers_quarantine (source_id, record_json, errors_json) "
                "VALUES (%s, %s::jsonb, %s::jsonb)",
                [
                    item["record"]["id"],
                    json.dumps(item["record"]),
                    json.dumps(item["errors"]),
                ],
            )
        pg_conn.commit()

        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers")
        assert cur.fetchone()["cnt"] == 2

        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers_quarantine")
        assert cur.fetchone()["cnt"] == 1

    def test_zero_records_batch(self, validator, pg_conn):
        """Empty batch — no writes, no errors."""
        result = validator.validate_batch([], contract="customer")
        assert result["summary"]["total"] == 0

        cur = pg_conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM test_customers")
        assert cur.fetchone()["cnt"] == 0
