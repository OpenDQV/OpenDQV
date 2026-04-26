"""Tests for observation-only mode.

Covers:
  - CLI --observe-only flag: exits 0 even with violations, labels output correctly
  - API observe_only=True: returns 200, response contains mode=observation_only
  - Trace log: entries written with correct mode value
  - Existing enforcement behaviour unchanged (regression)
  - DB persistence: mode column written correctly to quality_stats
  - Analytics endpoints: /observation/summary, /observation/trend, /observation/fields
"""

import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Path to the cli module
CLI = [sys.executable, str(Path(__file__).resolve().parent.parent / "opendqv" / "cli.py")]

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _run_cli(*args, expect_rc=None):
    """Run the CLI with given arguments and return CompletedProcess."""
    result = subprocess.run(
        CLI + list(args),
        capture_output=True,
        text=True,
    )
    if expect_rc is not None:
        assert result.returncode == expect_rc, (
            f"Expected rc={expect_rc}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


# ---------------------------------------------------------------------------
# CLI --observe-only tests
# ---------------------------------------------------------------------------

class TestCLIObserveOnly:
    """CLI validate-file --observe-only mode."""

    @pytest.fixture
    def bad_csv(self, tmp_path):
        """Create a CSV with records that fail the customer contract."""
        csv_path = tmp_path / "bad_customers.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["email", "age", "name"])
            writer.writeheader()
            writer.writerow({"email": "not-an-email", "age": "-5", "name": ""})
            writer.writerow({"email": "also-bad", "age": "-1", "name": ""})
        return str(csv_path)

    @pytest.fixture
    def good_csv(self, tmp_path):
        """Create a CSV with records that pass the customer contract."""
        csv_path = tmp_path / "good_customers.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["email", "age", "name"])
            writer.writeheader()
            writer.writerow({"email": "alice@example.com", "age": "25", "name": "Alice"})
        return str(csv_path)

    def test_observe_only_exits_zero_with_violations(self, bad_csv):
        """--observe-only must exit 0 even when records fail validation."""
        r = _run_cli("validate-file", "customer", bad_csv, "--observe-only", expect_rc=0)
        assert "OBSERVATION RUN" in r.stdout
        assert "Would have failed:" in r.stdout

    def test_observe_only_labels_output(self, bad_csv):
        """Output must say OBSERVATION RUN, not PASS or FAIL."""
        r = _run_cli("validate-file", "customer", bad_csv, "--observe-only")
        assert "OBSERVATION RUN" in r.stdout
        assert "PASS" not in r.stdout.split("Passed")[0]  # PASS should not appear in Result line
        assert "FAIL" not in r.stdout

    def test_observe_only_still_exports_failures(self, bad_csv, tmp_path):
        """--output-failures should still work in observe-only mode."""
        out_file = str(tmp_path / "failures.csv")
        _run_cli(
            "validate-file", "customer", bad_csv,
            "--observe-only", "--output-failures", out_file,
            expect_rc=0,
        )
        assert Path(out_file).exists()
        with open(out_file) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2  # both bad records

    def test_enforcement_mode_exits_nonzero_with_violations(self, bad_csv):
        """Without --observe-only, bad records should cause exit 1 (regression)."""
        r = _run_cli("validate-file", "customer", bad_csv)
        assert r.returncode == 1
        assert "FAIL" in r.stdout

    def test_enforcement_mode_exits_zero_with_good_data(self, good_csv):
        """Without --observe-only, good records should exit 0 (regression)."""
        r = _run_cli("validate-file", "customer", good_csv, expect_rc=0)
        assert "PASS" in r.stdout


# ---------------------------------------------------------------------------
# API observe_only tests
# ---------------------------------------------------------------------------

class TestAPIObserveOnlySingle:
    """POST /api/v1/validate with observe_only=True."""

    def test_observe_only_returns_200_with_violations(self, client, auth_headers):
        # CRT170/J1: in observe mode, HTTP is always 200 (never blocks),
        # but `valid` reflects the actual outcome — bad record → valid=False.
        body = {
            "record": {"email": "not-an-email", "age": -5, "name": ""},
            "contract": "customer",
            "observe_only": True,
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["mode"] == "observation_only"
        assert data["would_have_failed"] is True
        assert len(data["errors"]) > 0

    def test_observe_only_good_record(self, client, auth_headers):
        body = {
            "record": {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            "contract": "customer",
            "observe_only": True,
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["mode"] == "observation_only"
        assert data["would_have_failed"] is False

    def test_enforcement_mode_unchanged(self, client, auth_headers):
        """Without observe_only, bad records return valid=False (regression).

        CRT173/25: as of v2.3.14, mode and would_have_failed are always
        populated — was previously null in enforcement mode.
        """
        body = {
            "record": {"email": "not-an-email", "age": -5, "name": ""},
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["mode"] == "enforcement"
        assert data["would_have_failed"] is True


class TestJ1ValidCoherenceAcceptance:
    """CRT170/J1 acceptance — `valid` means what its name claims, in any mode.

    Working principle: observation mode is a *blocking* policy (never block,
    always return 200), not a truth policy. A response field's value must
    reflect its name: `valid` == "did this record pass validation". In v2.3.2
    and earlier, observe mode hardcoded `valid: true` even when the record
    failed every rule — making it indistinguishable from a passing record at
    the field level. Clients had to read `would_have_failed` to recover the
    real outcome. This was a name/value mismatch.
    """

    def test_observe_mode_valid_mirrors_real_outcome_failing(
        self, client, auth_headers,
    ):
        """Bad record in observe mode → HTTP 200 AND valid=False AND would_have_failed=True."""
        r = client.post(
            "/api/v1/validate",
            json={
                "record": {"email": "not-an-email", "age": -5, "name": ""},
                "contract": "customer",
                "observe_only": True,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["would_have_failed"] is True
        # Sanity: valid + would_have_failed are the negation of each other
        # in observe mode — same fact, two field names.
        assert data["valid"] != data["would_have_failed"]

    def test_observe_mode_valid_mirrors_real_outcome_passing(
        self, client, auth_headers,
    ):
        """Good record in observe mode → HTTP 200 AND valid=True AND would_have_failed=False."""
        r = client.post(
            "/api/v1/validate",
            json={
                "record": {
                    "email": "test@example.com", "age": 25, "name": "Alice",
                    "id": "12345", "phone": "+1234567890", "balance": 100,
                    "score": 85, "date": "2024-01-15",
                    "username": "alice_w", "password": "securepass123",
                },
                "contract": "customer",
                "observe_only": True,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["would_have_failed"] is False
        assert data["valid"] != data["would_have_failed"]

    def test_observe_mode_never_blocks_with_http(self, client, auth_headers):
        """Observe mode always returns 200 even for failing records — the
        blocking policy is independent of `valid`."""
        r = client.post(
            "/api/v1/validate",
            json={
                "record": {"email": "bad", "age": -5, "name": ""},
                "contract": "customer",
                "observe_only": True,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        # Enforcement mode also returns 200 currently — the contract is that
        # observe mode never raises 4xx-validation, not that enforcement does.
        # The semantic difference is downstream: clients reading `valid` may
        # choose to block in enforcement and only log in observation.


class TestAPIObserveOnlyBatch:
    """POST /api/v1/validate/batch with observe_only=True."""

    def test_observe_only_batch_returns_200(self, client, auth_headers):
        body = {
            "records": [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad-email", "age": -5, "name": ""},
            ],
            "contract": "customer",
            "observe_only": True,
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "observation_only"
        assert data["would_have_failed"] is True  # second record fails

    def test_enforcement_batch_unchanged(self, client, auth_headers):
        """Without observe_only, batch returns normal results (regression)."""
        body = {
            "records": [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad-email", "age": -5, "name": ""},
            ],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        # CRT173/25: as of v2.3.14, mode and would_have_failed are always populated
        assert data["mode"] == "enforcement"
        assert data["would_have_failed"] is True
        assert data["summary"]["failed"] > 0


# ---------------------------------------------------------------------------
# Trace log mode field tests
# ---------------------------------------------------------------------------

class TestTraceLogMode:
    """Verify trace_log entries include the mode field."""

    def test_trace_entry_default_mode(self):
        """write_trace_entry without mode kwarg defaults to enforcement."""
        from opendqv.core.trace_log import write_trace_entry
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            with patch.dict(os.environ, {
                "OPENDQV_TRACE_LOG": "true",
                "OPENDQV_TRACE_LOG_PATH": log_path,
            }):
                write_trace_entry(
                    contract_name="test_contract",
                    context=None,
                    record_index=0,
                    valid=True,
                    error_count=0,
                    warning_count=0,
                    fields_validated=["field_a"],
                    sensitive_fields=[],
                    failed_rules=[],
                )

            with open(log_path) as f:
                entry = json.loads(f.readline())
            assert entry["mode"] == "enforcement"
        finally:
            os.unlink(log_path)

    def test_trace_entry_observation_mode(self):
        """write_trace_entry with mode='observation_only' records that mode."""
        from opendqv.core.trace_log import write_trace_entry

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            with patch.dict(os.environ, {
                "OPENDQV_TRACE_LOG": "true",
                "OPENDQV_TRACE_LOG_PATH": log_path,
            }):
                write_trace_entry(
                    contract_name="test_contract",
                    context=None,
                    record_index=0,
                    valid=False,
                    error_count=2,
                    warning_count=0,
                    fields_validated=["field_a", "field_b"],
                    sensitive_fields=[],
                    failed_rules=["field_a"],
                    mode="observation_only",
                )

            with open(log_path) as f:
                entry = json.loads(f.readline())
            assert entry["mode"] == "observation_only"
        finally:
            os.unlink(log_path)

    def test_trace_hash_chain_valid_with_mode(self):
        """Hash chain should remain valid when mode field is included."""
        from opendqv.core.trace_log import write_trace_entry, verify_trace_log

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            with patch.dict(os.environ, {
                "OPENDQV_TRACE_LOG": "true",
                "OPENDQV_TRACE_LOG_PATH": log_path,
            }):
                write_trace_entry(
                    contract_name="test_contract",
                    context=None,
                    record_index=0,
                    valid=True,
                    error_count=0,
                    warning_count=0,
                    fields_validated=["email"],
                    sensitive_fields=[],
                    failed_rules=[],
                    mode="enforcement",
                )
                write_trace_entry(
                    contract_name="test_contract",
                    context=None,
                    record_index=1,
                    valid=False,
                    error_count=1,
                    warning_count=0,
                    fields_validated=["email"],
                    sensitive_fields=[],
                    failed_rules=["email"],
                    mode="observation_only",
                )

            result = verify_trace_log(log_path)
            assert result["valid"] is True
            assert result["entries"] == 2
        finally:
            os.unlink(log_path)


# ---------------------------------------------------------------------------
# DB persistence — mode column in quality_stats
# ---------------------------------------------------------------------------

class TestObserveOnlyPersistence:
    """Verify that the mode column is correctly persisted to quality_stats."""

    def _latest_mode(self):
        """Query the most recent quality_stats row and return its mode value."""
        import opendqv.config as config
        # Give the async fire-and-forget task time to flush to SQLite
        time.sleep(0.3)
        conn = sqlite3.connect(config.DB_PATH)
        try:
            row = conn.execute(
                "SELECT mode FROM quality_stats ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            assert row is not None, "No quality_stats rows found"
            return row[0]
        finally:
            conn.close()

    def test_observation_mode_persisted_to_db(self, client, auth_headers):
        """POST with observe_only=True persists mode='observation_only'."""
        body = {
            "record": {"email": "not-an-email", "age": -5, "name": ""},
            "contract": "customer",
            "observe_only": True,
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        assert self._latest_mode() == "observation_only"

    def test_enforcement_mode_persisted_to_db(self, client, auth_headers):
        """POST without observe_only persists mode='enforcement'."""
        body = {
            "record": {"email": "not-an-email", "age": -5, "name": ""},
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        assert self._latest_mode() == "enforcement"

    def test_batch_observation_mode_persisted(self, client, auth_headers):
        """POST batch with observe_only=True persists mode='observation_only'."""
        body = {
            "records": [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad-email", "age": -5, "name": ""},
            ],
            "contract": "customer",
            "observe_only": True,
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        assert self._latest_mode() == "observation_only"


# ---------------------------------------------------------------------------
# Observation analytics endpoints
# ---------------------------------------------------------------------------

class TestObservationAnalyticsEndpoints:
    """Tests for /observation/summary, /observation/trend, /observation/fields."""

    def test_observation_summary_returns_200(self, client, auth_headers):
        r = client.get("/api/v1/observation/summary", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        for key in (
            "total_observation_records",
            "would_have_failed_count",
            "would_have_passed_count",
            "enforcement_readiness_pct",
            "by_contract",
        ):
            assert key in data, f"Missing key '{key}' in observation summary"

    def test_observation_summary_with_contract_filter(self, client, auth_headers):
        r = client.get(
            "/api/v1/observation/summary",
            params={"contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "total_observation_records" in data
        assert "enforcement_readiness_pct" in data

    def test_observation_trend_requires_contract(self, client, auth_headers):
        r = client.get("/api/v1/observation/trend", headers=auth_headers)
        assert r.status_code == 422

    def test_observation_trend_returns_list(self, client, auth_headers):
        r = client.get(
            "/api/v1/observation/trend",
            params={"contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_observation_fields_returns_list(self, client, auth_headers):
        r = client.get(
            "/api/v1/observation/fields",
            params={"contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_observation_endpoints_require_auth(self, client):
        for path in (
            "/api/v1/observation/summary",
            "/api/v1/observation/trend?contract=customer",
            "/api/v1/observation/fields?contract=customer",
        ):
            r = client.get(path)
            assert r.status_code == 401, f"{path} returned {r.status_code}, expected 401"
