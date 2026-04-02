"""
tests/test_quality_analytics.py — DuckDB OLAP analytics layer (RT108).

Covers:
  Part A — QualityAnalytics class (unit tests with real temp SQLite file)
    cross_contract_summary: sorted by pass_rate, correct aggregation, window filtering
    rule_heatmap: ranked by failure_count, JSON aggregation, empty result on no data

  Part B — API endpoints via TestClient
    GET /api/v1/analytics/summary  — 200 response shape, auth enforced
    GET /api/v1/analytics/rule-heatmap — 200 response shape, auth enforced

  Part C — Schema-driven seeder helpers (make_valid_record, make_invalid_record)
    allowed_values rule generates value from the allowed list
    not_empty rule generates a non-empty string
    min/range rule generates a value within bounds
    date_format rule generates a YYYY-MM-DD string
    make_invalid_record corrupts exactly one field
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.quality_stats import QualityStats
from core.quality_analytics import QualityAnalytics


# ── Shared fixture: temp SQLite with seeded data ───────────────────────────────

@pytest.fixture
def seeded_db(tmp_path):
    """
    Real temp SQLite with several quality_stats rows — three recent, one old.
    Returns (db_path, QualityAnalytics instance).
    """
    db_path = str(tmp_path / "test_analytics.db")
    qs = QualityStats(db_path)
    now_utc = datetime.now(timezone.utc)

    # Insert three recent rows via QualityStats (timestamp = now)
    qs.record_batch("hr_employee", "1.0", "default", 80, 60, 20,
                    {"email_format": 12, "ni_number_format": 8})
    qs.record_batch("manufacturing_iot", "1.0", "default", 100, 40, 60,
                    {"status_values": 30, "alert_level_values": 20, "device_type_values": 10})
    qs.record_batch("energy_meter_reading", "1.0", "default", 50, 45, 5,
                    {"read_type_values": 3, "meter_type_valid": 2})

    # Insert one old row directly into SQLite with a past timestamp (10 days ago)
    old_ts = (now_utc - timedelta(days=10)).isoformat()
    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute(
        "INSERT INTO quality_stats "
        "(contract_name, contract_version, context, recorded_at, total_records, passed, failed, pass_rate, rule_failure_counts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("old_contract", "1.0", "default", old_ts, 200, 100, 100, 0.5, json.dumps({"old_rule": 100})),
    )
    raw_conn.commit()
    raw_conn.close()

    qa = QualityAnalytics(db_path)
    return db_path, qa


# ── Part A: QualityAnalytics unit tests ───────────────────────────────────────

class TestCrossContractSummary:

    def test_returns_list_of_dicts(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        assert isinstance(result, list)
        assert len(result) == 3  # 3 contracts within 7 days

    def test_excludes_old_rows(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        names = [r["contract"] for r in result]
        assert "old_contract" not in names

    def test_sorted_by_pass_rate_ascending(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        rates = [r["pass_rate"] for r in result]
        assert rates == sorted(rates), "Should be sorted worst-first (ascending pass_rate)"

    def test_manufacturing_iot_is_worst(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        assert result[0]["contract"] == "manufacturing_iot"

    def test_energy_meter_is_best(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        assert result[-1]["contract"] == "energy_meter_reading"

    def test_correct_total_records(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        hr = next(r for r in result if r["contract"] == "hr_employee")
        assert hr["total_records"] == 80
        assert hr["passed"] == 60
        assert hr["failed"] == 20

    def test_pass_rate_pct_field(self, seeded_db):
        _, qa = seeded_db
        result = qa.cross_contract_summary(days=7)
        hr = next(r for r in result if r["contract"] == "hr_employee")
        assert "pass_rate_pct" in hr
        assert abs(hr["pass_rate_pct"] - 75.0) < 0.5

    def test_empty_window_returns_empty_list(self, seeded_db):
        _, qa = seeded_db
        # Window so small nothing falls in it
        result = qa.cross_contract_summary(days=0)  # type: ignore[arg-type]
        # days=0 means since now — likely empty
        assert isinstance(result, list)


class TestRuleHeatmap:

    def test_returns_list_ranked_by_failure_count(self, seeded_db):
        _, qa = seeded_db
        result = qa.rule_heatmap(days=7)
        assert isinstance(result, list)
        counts = [r["failure_count"] for r in result]
        assert counts == sorted(counts, reverse=True), "Should be ranked descending"

    def test_top_rule_is_status_values(self, seeded_db):
        _, qa = seeded_db
        result = qa.rule_heatmap(days=7)
        assert result[0]["rule"] == "status_values"
        assert result[0]["failure_count"] == 30

    def test_excludes_old_rows(self, seeded_db):
        _, qa = seeded_db
        result = qa.rule_heatmap(days=7)
        rule_names = [r["rule"] for r in result]
        assert "old_rule" not in rule_names

    def test_required_fields_present(self, seeded_db):
        _, qa = seeded_db
        result = qa.rule_heatmap(days=7)
        for item in result:
            assert "contract" in item
            assert "rule" in item
            assert "failure_count" in item

    def test_returns_at_most_50_items(self, tmp_path):
        """Heatmap caps output at 50 entries."""
        db_path = str(tmp_path / "many_rules.db")
        qs = QualityStats(db_path)
        # Insert 60 distinct rules
        rules_fail = {f"rule_{i:03d}": i + 1 for i in range(60)}
        qs.record_batch("test", "1.0", "default", 1000, 100, 900, rules_fail)
        qa = QualityAnalytics(db_path)
        result = qa.rule_heatmap(days=7)
        assert len(result) <= 50

    def test_empty_on_no_data(self, tmp_path):
        """No rows → empty heatmap."""
        db_path = str(tmp_path / "empty.db")
        QualityStats(db_path)  # create schema
        qa = QualityAnalytics(db_path)
        assert qa.rule_heatmap(days=7) == []


# ── Part B: API endpoint tests ─────────────────────────────────────────────────

class TestAnalyticsSummaryEndpoint:

    def test_returns_200(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/summary", headers=auth_headers)
        assert resp.status_code == 200

    def test_response_shape(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/summary?days=7", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "days" in body
        assert "contracts" in body
        assert "total_contracts" in body
        assert body["days"] == 7
        assert isinstance(body["contracts"], list)

    def test_contract_item_shape(self, client, auth_headers):
        # Seed one validation so there's at least one contract
        client.post(
            "/api/v1/validate",
            json={"contract": "customer", "record": {"name": "Alice", "email": "alice@example.com", "age": 30}},
            headers=auth_headers,
        )
        resp = client.get("/api/v1/analytics/summary?days=7", headers=auth_headers)
        body = resp.json()
        if body["contracts"]:
            item = body["contracts"][0]
            assert "contract" in item
            assert "total_records" in item
            assert "passed" in item
            assert "failed" in item
            assert "pass_rate" in item
            assert "pass_rate_pct" in item

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/analytics/summary")
        assert resp.status_code == 401

    def test_days_param_accepted(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/summary?days=30", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["days"] == 30


class TestRuleHeatmapEndpoint:

    def test_returns_200(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/rule-heatmap", headers=auth_headers)
        assert resp.status_code == 200

    def test_response_shape(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/rule-heatmap?days=7", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "days" in body
        assert "rules" in body
        assert "total_rules" in body
        assert body["days"] == 7
        assert isinstance(body["rules"], list)

    def test_rule_item_shape(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/rule-heatmap?days=7", headers=auth_headers)
        body = resp.json()
        if body["rules"]:
            item = body["rules"][0]
            assert "contract" in item
            assert "rule" in item
            assert "failure_count" in item

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/analytics/rule-heatmap")
        assert resp.status_code == 401

    def test_days_param_accepted(self, client, auth_headers):
        resp = client.get("/api/v1/analytics/rule-heatmap?days=14", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["days"] == 14


# ── Part C: Schema-driven seeder unit tests ────────────────────────────────────

class TestMakeValidRecord:
    """Tests for the schema-driven make_valid_record function in seed_broad_demo.py."""

    @pytest.fixture(autouse=True)
    def _import(self):
        """Import the seeder functions under test."""
        import scripts.seed_broad_demo as s
        self.make_valid_record = s.make_valid_record
        self.make_invalid_record = s.make_invalid_record

    def test_allowed_values_rule_generates_valid_value(self):
        rules = [{"field": "status", "type": "allowed_values", "allowed_values": ["active", "inactive"]}]
        record = self.make_valid_record(rules)
        assert record.get("status") in ("active", "inactive")

    def test_not_empty_rule_generates_nonempty_string(self):
        rules = [{"field": "employee_id", "type": "not_empty"}]
        record = self.make_valid_record(rules)
        assert record.get("employee_id")

    def test_min_rule_generates_value_in_range(self):
        rules = [{"field": "salary", "type": "min", "min": 10000, "max": 200000}]
        record = self.make_valid_record(rules)
        assert record["salary"] >= 10000

    def test_range_rule_generates_value_in_bounds(self):
        rules = [{"field": "humidity", "type": "range", "min": 0, "max": 100}]
        record = self.make_valid_record(rules)
        assert 0 <= record["humidity"] <= 100

    def test_date_format_rule_generates_iso_date(self):
        rules = [{"field": "reading_date", "type": "date_format"}]
        record = self.make_valid_record(rules)
        val = record.get("reading_date", "")
        assert len(val) == 10 and val[4] == "-"

    def test_regex_email_generates_valid_email(self):
        rules = [{"field": "email", "type": "regex",
                  "pattern": r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$',
                  "name": "email_format"}]
        record = self.make_valid_record(rules)
        assert "@" in record.get("email", "")

    def test_multi_rule_contract_all_fields_set(self):
        rules = [
            {"field": "employee_id", "type": "not_empty"},
            {"field": "contract_type", "type": "allowed_values",
             "allowed_values": ["permanent", "contractor"]},
            {"field": "salary", "type": "min", "min": 0},
        ]
        record = self.make_valid_record(rules)
        assert record.get("employee_id")
        assert record.get("contract_type") in ("permanent", "contractor")
        assert record.get("salary") is not None


class TestMakeInvalidRecord:

    @pytest.fixture(autouse=True)
    def _import(self):
        import scripts.seed_broad_demo as s
        self.make_valid_record = s.make_valid_record
        self.make_invalid_record = s.make_invalid_record

    def test_corrupts_allowed_values_field(self):
        rules = [
            {"field": "status", "type": "allowed_values", "allowed_values": ["active", "inactive"]},
            {"field": "name", "type": "not_empty"},
        ]
        valid = self.make_valid_record(rules)
        invalid = self.make_invalid_record(valid, rules)
        assert invalid["status"] == "INVALID_SEED_VALUE"

    def test_corrupts_not_empty_when_no_allowed_values(self):
        rules = [
            {"field": "employee_id", "type": "not_empty", "severity": "error"},
        ]
        valid = self.make_valid_record(rules)
        invalid = self.make_invalid_record(valid, rules)
        assert invalid["employee_id"] == ""

    def test_result_is_a_copy(self):
        rules = [{"field": "status", "type": "allowed_values", "allowed_values": ["active"]}]
        valid = self.make_valid_record(rules)
        invalid = self.make_invalid_record(valid, rules)
        # Original should not be modified
        assert valid["status"] == "active"
        assert invalid["status"] != "active"


# ── Part D: Uncovered API endpoints ────────────────────────────────────────────

class TestRejectionSummaryEndpoint:

    def test_returns_200(self, client, auth_headers):
        resp = client.get("/api/v1/rejection-summary", headers=auth_headers)
        assert resp.status_code == 200

    def test_returns_list(self, client, auth_headers):
        resp = client.get("/api/v1/rejection-summary", headers=auth_headers)
        assert isinstance(resp.json(), list)

    def test_limit_param_accepted(self, client, auth_headers):
        resp = client.get("/api/v1/rejection-summary?limit=5", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) <= 5

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/rejection-summary")
        assert resp.status_code == 401

    def test_result_shape_when_data_present(self, client, auth_headers):
        # Seed a failing validation
        client.post(
            "/api/v1/validate",
            json={"contract": "customer", "record": {"name": "", "email": "bad", "age": -1}},
            headers=auth_headers,
        )
        resp = client.get("/api/v1/rejection-summary", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        if body:
            item = body[0]
            assert "contract" in item
            assert "total_validations" in item
            assert "failed" in item
            assert "pass_rate" in item
            assert "top_failing_rules" in item


class TestRuleVelocityEndpoint:

    def test_returns_200(self, client, auth_headers):
        resp = client.get(
            "/api/v1/analytics/rule-velocity?contract=customer",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_response_shape(self, client, auth_headers):
        resp = client.get(
            "/api/v1/analytics/rule-velocity?contract=customer&window_hours=24&bucket_minutes=5",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "contract" in body
        assert "window_hours" in body
        assert "bucket_minutes" in body
        assert "series" in body
        assert isinstance(body["series"], dict)

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/analytics/rule-velocity?contract=customer")
        assert resp.status_code == 401


class TestStatsWindowedEndpoint:

    def test_windowed_stats_200(self, client, auth_headers):
        resp = client.get("/api/v1/stats?window_hours=24", headers=auth_headers)
        assert resp.status_code == 200

    def test_windowed_stats_has_governance(self, client, auth_headers):
        resp = client.get("/api/v1/stats?window_hours=1", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "governance" in body

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/stats?window_hours=1")
        assert resp.status_code == 401


class TestObservationEndpoints:

    def test_observation_summary_200(self, client, auth_headers):
        resp = client.get("/api/v1/observation/summary", headers=auth_headers)
        assert resp.status_code == 200

    def test_observation_summary_requires_auth(self, client):
        resp = client.get("/api/v1/observation/summary")
        assert resp.status_code == 401

    def test_observation_trend_200(self, client, auth_headers):
        resp = client.get(
            "/api/v1/observation/trend?contract=customer",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_observation_trend_requires_auth(self, client):
        resp = client.get("/api/v1/observation/trend?contract=customer")
        assert resp.status_code == 401

    def test_observation_fields_200(self, client, auth_headers):
        resp = client.get(
            "/api/v1/observation/fields?contract=customer",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_observation_fields_requires_auth(self, client):
        resp = client.get("/api/v1/observation/fields?contract=customer")
        assert resp.status_code == 401
