"""Tests for the dataset profiler and rule suggester."""

import json
import os
import pytest

from core.profiler import profile_records

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_data", "profile_sample.json")


@pytest.fixture
def sample_records():
    with open(SAMPLE_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestProfiler:
    """Unit tests for profile_records()."""

    # 1
    def test_profile_sample_data(self, sample_records):
        result = profile_records(sample_records)
        contract = result["contract"]
        profile = result["profile"]

        # Contract has rules
        assert len(contract["rules"]) > 0

        # Profile covers all 8 fields
        assert len(profile["fields"]) == 8

        # email field
        email = profile["fields"]["email"]
        assert email["type"] == "string"
        assert email["null_count"] == 0

        # age field
        assert profile["fields"]["age"]["type"] == "numeric"

        # signup_date field
        assert profile["fields"]["signup_date"]["type"] == "date"

    # 2
    def test_not_empty_rules(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]
        rule_names = [r["name"] for r in rules]

        # email has 0 nulls -> should get not_empty
        assert "email_not_empty" in rule_names

        # name has nulls (record 6 has null name) -> should NOT get not_empty
        assert "name_not_empty" not in rule_names

        # phone has nulls -> should NOT get not_empty
        assert "phone_not_empty" not in rule_names

    # 3
    def test_unique_detection(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]
        rule_names = [r["name"] for r in rules]

        # id — all unique values
        assert "id_unique" in rule_names

        # status — repeated values (active, inactive, pending)
        assert "status_unique" not in rule_names

    # 4
    def test_email_detection(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]

        email_rules = [r for r in rules if r["name"] == "email_email_format"]
        assert len(email_rules) == 1
        assert email_rules[0]["type"] == "regex"
        assert "@" in email_rules[0]["pattern"]

    # 5
    def test_phone_detection(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]

        phone_rules = [r for r in rules if r["name"] == "phone_phone_format"]
        assert len(phone_rules) == 1
        assert phone_rules[0]["type"] == "regex"

    # 6
    def test_numeric_range(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]

        age_range = [r for r in rules if r["name"] == "age_range"]
        assert len(age_range) == 1
        rule = age_range[0]
        assert rule["type"] == "range"
        # Observed min=18, max=75; with 10% buffer of span 57 -> ~5.7
        # Buffered min should be < 18, buffered max should be > 75
        assert rule["min"] < 18
        assert rule["max"] > 75

    # 7
    def test_date_detection(self, sample_records):
        result = profile_records(sample_records)
        rules = result["contract"]["rules"]

        date_rules = [r for r in rules if r["name"] == "signup_date_date_format"]
        assert len(date_rules) == 1
        assert date_rules[0]["type"] == "date_format"

    # 8
    def test_empty_records(self):
        result = profile_records([])
        assert result["contract"]["rules"] == []
        assert result["profile"]["record_count"] == 0
        assert result["profile"]["fields"] == {}

    # 9
    def test_custom_contract_name(self, sample_records):
        result = profile_records(sample_records, contract_name="my_data")
        assert result["contract"]["name"] == "my_data"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestProfilerAPI:
    """Integration tests for the /profile endpoint."""

    # 10
    def test_profile_endpoint(self, client, auth_headers):
        records = [
            {"id": 1, "name": "Alice", "email": "alice@example.com"},
            {"id": 2, "name": "Bob", "email": "bob@example.com"},
        ]
        resp = client.post("/api/v1/profile", json=records, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "contract" in body
        assert "profile" in body
        assert body["profile"]["record_count"] == 2

    # 11
    def test_profile_endpoint_no_auth(self, client):
        records = [{"id": 1, "name": "Alice"}]
        resp = client.post("/api/v1/profile", json=records)
        assert resp.status_code == 401


_NUMERIC_RECORDS = [
    {"amount": i, "status": "active" if i % 3 != 0 else "closed"}
    for i in range(1, 21)
]  # 20 records: amount 1–20, status has 2 distinct values


class TestDuckDBNumericStats:
    # 12
    def test_numeric_mean(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["amount"]
        assert "mean" in field
        assert field["mean"] == round(sum(range(1, 21)) / 20, 4)

    # 13
    def test_numeric_stddev(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["amount"]
        assert "stddev" in field
        assert field["stddev"] > 0

    # 14
    def test_numeric_percentiles_present(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["amount"]
        for key in ("p25", "p50", "p75", "p95"):
            assert key in field, f"{key} missing from numeric field profile"

    # 15
    def test_numeric_percentile_ordering(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["amount"]
        assert field["p25"] <= field["p50"] <= field["p75"] <= field["p95"]

    # 16
    def test_numeric_min_max_still_present(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["amount"]
        assert field["min"] == 1
        assert field["max"] == 20


class TestDuckDBStringTopValues:
    # 17
    def test_low_cardinality_has_top_values(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["status"]
        assert "top_values" in field
        assert set(field["top_values"].keys()) == {"active", "closed"}

    # 18
    def test_top_values_counts_sum_to_total(self):
        result = profile_records(_NUMERIC_RECORDS)
        field = result["profile"]["fields"]["status"]
        assert sum(field["top_values"].values()) == 20

    # 19
    def test_high_cardinality_no_top_values(self):
        records = [{"email": f"user{i}@example.com"} for i in range(20)]
        result = profile_records(records)
        field = result["profile"]["fields"]["email"]
        assert "top_values" not in field
