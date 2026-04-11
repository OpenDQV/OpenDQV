"""
DuckDB / Snowflake local simulation integration tests.

DuckDB is already a core dependency, so these tests run with zero extra deps.
They validate the local validation harness against an in-memory DuckDB table,
simulating the Snowflake pattern locally.

See docs/snowflake_integration.md — "Local simulation with DuckDB" section.
"""

import duckdb
import pytest

from opendqv.sdk.local import LocalValidator

# Complete customer record — satisfies all rules in contracts/customer.yaml
_VALID_CUSTOMER = {
    "name": "Alice",
    "email": "alice@example.com",
    "age": 30,
    "phone": "+447911123456",
    "score": 85,
    "date": "2024-01-15",
    "username": "alice123",
    "password": "securepass",
    "balance": 100.0,
    "id": "cust-001",
}

_VALID_CUSTOMER_2 = {
    "name": "Bob",
    "email": "bob@example.com",
    "age": 25,
    "phone": "+14155552671",
    "score": 72,
    "date": "2024-03-01",
    "username": "bob_data",
    "password": "p@ssword99",
    "balance": 500.0,
    "id": "cust-002",
}

# Invalid record — bad email, missing name, out-of-range score
_INVALID_CUSTOMER = {
    "name": "",
    "email": "not-an-email",
    "age": -5,
    "phone": "+447911999888",
    "score": 200,
    "date": "2024-01-15",
    "username": "u",
    "password": "short",
    "balance": -10.0,
    "id": "cust-bad",
}


@pytest.fixture
def validator():
    """LocalValidator using OPENDQV_CONTRACTS_DIR set by conftest."""
    return LocalValidator()


class TestDuckDBBatchValidation:
    """Validate records extracted from a DuckDB in-memory table via LocalValidator."""

    def test_validate_clean_records_from_duckdb(self, validator):
        """Two clean records from a DuckDB table should both pass."""
        conn = duckdb.connect(":memory:")
        conn.execute("""
            CREATE TABLE customers AS SELECT * FROM (VALUES
                ('Alice', 'alice@example.com', 30, '+447911123456', 85,
                 '2024-01-15', 'alice123', 'securepass', 100.0, 'cust-001'),
                ('Bob',   'bob@example.com',   25, '+14155552671',  72,
                 '2024-03-01', 'bob_data', 'p@ssword99', 500.0, 'cust-002')
            ) t(name, email, age, phone, score, date, username, password, balance, id)
        """)

        records = conn.execute("SELECT * FROM customers").fetchdf().to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 0
        conn.close()

    def test_validate_mixed_records_from_duckdb(self, validator):
        """One clean record and one invalid record should split correctly."""
        records = [_VALID_CUSTOMER, _INVALID_CUSTOMER]
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1

    def test_quarantine_pattern(self, validator):
        """Simulate the Snowflake quarantine pattern: separate clean vs rejected rows."""
        records = [_VALID_CUSTOMER, _INVALID_CUSTOMER, _VALID_CUSTOMER_2]
        result = validator.validate_batch(records, contract="customer")

        clean_indices = {r["index"] for r in result["results"] if r["valid"]}
        rejected_indices = {r["index"] for r in result["results"] if not r["valid"]}

        clean_records = [records[i] for i in sorted(clean_indices)]
        rejected_records = [records[i] for i in sorted(rejected_indices)]

        assert len(clean_records) + len(rejected_records) == 3
        assert all(r["email"] != "not-an-email" for r in clean_records)
        assert any(r["email"] == "not-an-email" for r in rejected_records)

    def test_duckdb_dataframe_roundtrip(self, validator):
        """Validate a DuckDB DataFrame via df.to_dict('records') pattern."""
        conn = duckdb.connect(":memory:")
        df = conn.execute(
            "SELECT ? AS name, ? AS email, ? AS age, ? AS phone, "
            "? AS score, ? AS date, ? AS username, ? AS password, ? AS balance, ? AS id",
            ["David", "david@example.com", 42, "+447700900001",
             90, "2024-06-01", "david42", "strongpass", 250.0, "cust-003"]
        ).fetchdf()

        records = df.to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 1
        assert result["summary"]["passed"] == 1
        conn.close()


class TestDuckDBSingleValidation:
    """Single-record validation against a DuckDB-sourced record."""

    def test_single_record_valid(self, validator):
        """A complete valid customer record passes."""
        result = validator.validate(_VALID_CUSTOMER, contract="customer")
        assert result["valid"] is True
        assert result["errors"] == []

    def test_single_record_invalid(self, validator):
        """An invalid record fails with errors on the bad fields."""
        result = validator.validate(_INVALID_CUSTOMER, contract="customer")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_duckdb_fetchone_to_record(self, validator):
        """Validate a record built from DuckDB fetchone() result."""
        conn = duckdb.connect(":memory:")
        row = conn.execute(
            "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?",
            ["Eve", "eve@example.com", 28, "+447911555666",
             60, "2023-12-01", "eve_dq", "evepassword", 300.0, "cust-eve"]
        ).fetchone()
        cols = ["name", "email", "age", "phone", "score", "date",
                "username", "password", "balance", "id"]
        record = dict(zip(cols, row))

        result = validator.validate(record, contract="customer")
        assert result["valid"] is True
        conn.close()
