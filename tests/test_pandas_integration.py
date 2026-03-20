"""
Pandas DataFrame integration tests.

LocalValidator.validate_batch() takes a list of dicts — which is exactly
what df.to_dict('records') produces. Zero extra dependencies beyond pandas
(already a core dep via DuckDB's fetchdf support).

See docs/pandas_integration.md for the documented pattern.
"""

import pandas as pd
import pytest

from sdk.local import LocalValidator


@pytest.fixture
def validator():
    """LocalValidator using OPENDQV_CONTRACTS_DIR set by conftest."""
    return LocalValidator()


_CLEAN_CUSTOMERS = pd.DataFrame([
    {
        "name": "Alice", "email": "alice@example.com", "age": 30,
        "phone": "+447911123456", "score": 85, "date": "2024-01-15",
        "username": "alice123", "password": "securepass", "balance": 100.0, "id": "c1",
    },
    {
        "name": "Bob", "email": "bob@example.com", "age": 25,
        "phone": "+14155552671", "score": 72, "date": "2024-03-01",
        "username": "bob_data", "password": "p@ssword99", "balance": 500.0, "id": "c2",
    },
])

_MIXED_CUSTOMERS = pd.DataFrame([
    {
        "name": "Alice", "email": "alice@example.com", "age": 30,
        "phone": "+447911123456", "score": 85, "date": "2024-01-15",
        "username": "alice123", "password": "securepass", "balance": 100.0, "id": "c1",
    },
    {
        "name": "", "email": "not-an-email", "age": -1,
        "phone": "+447900000001", "score": 200, "date": "2024-01-15",
        "username": "u", "password": "short", "balance": -50.0, "id": "c-bad",
    },
])


class TestPandasBatchValidation:
    """Validate DataFrames converted via df.to_dict('records')."""

    def test_clean_dataframe_passes(self, validator):
        records = _CLEAN_CUSTOMERS.to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 2
        assert result["summary"]["failed"] == 0

    def test_mixed_dataframe_splits_correctly(self, validator):
        records = _MIXED_CUSTOMERS.to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1

    def test_annotate_dataframe_with_validity_column(self, validator):
        """Standard pattern: annotate df with _opendqv_valid column."""
        records = _MIXED_CUSTOMERS.to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        validity = {r["index"]: r["valid"] for r in result["results"]}
        df = _MIXED_CUSTOMERS.copy()
        df["_opendqv_valid"] = df.index.map(validity)

        clean_df = df[df["_opendqv_valid"]]
        rejected_df = df[~df["_opendqv_valid"]]

        assert len(clean_df) == 1
        assert len(rejected_df) == 1
        assert clean_df.iloc[0]["email"] == "alice@example.com"
        assert rejected_df.iloc[0]["email"] == "not-an-email"

    def test_empty_dataframe(self, validator):
        """Empty DataFrame produces zero results without error."""
        records = pd.DataFrame(columns=["name", "email", "age"]).to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 0
        assert result["summary"]["passed"] == 0
        assert result["summary"]["failed"] == 0

    def test_single_row_dataframe(self, validator):
        """Single-row DataFrame — one record validated."""
        df = _CLEAN_CUSTOMERS.iloc[:1]
        records = df.to_dict("records")
        result = validator.validate_batch(records, contract="customer")

        assert result["summary"]["total"] == 1
        assert result["summary"]["passed"] == 1


class TestPandasSingleRecord:
    """Validate a single row extracted from a DataFrame."""

    def test_validate_row_as_dict(self, validator):
        row = _CLEAN_CUSTOMERS.iloc[0].to_dict()
        result = validator.validate(row, contract="customer")
        assert result["valid"] is True

    def test_validate_invalid_row(self, validator):
        row = _MIXED_CUSTOMERS.iloc[1].to_dict()
        result = validator.validate(row, contract="customer")
        assert result["valid"] is False
        assert len(result["errors"]) > 0
