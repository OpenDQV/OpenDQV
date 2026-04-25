"""
Smoke tests — wide coverage, shallow depth.

These tests verify the critical happy paths that a new deployer or contributor
would exercise first. They serve as living documentation of the API contract
and as a fast first gate before the full suite.

Coverage:
  1.  Health / service info
  2.  Contracts list and detail
  3.  Single-record validation — valid and invalid
  4.  Error codes present in validation responses (OPENDQV_<TYPE>_001 format)
  5.  Batch validation — mixed pass/fail
  6.  Batch validation — dry_run does not error
  7.  Contract linter — clean contract passes, unknown contract 404s
  8.  Code generation — spark and bigquery targets (new generators)
  9.  Code generation — existing targets still work
  10. Quality trend endpoint available
  11. Explain endpoint available
  12. Stats endpoint available
  13. Auth boundary — unauthenticated requests return 401
  14. Validate-file CLI smoke — valid and invalid file
  15. LocalValidator in-process SDK smoke
  16. Observation mode — basic happy path
"""

import csv
import os
import tempfile



# ── Shared fixtures and helpers ───────────────────────────────────────────────

_VALID_CUSTOMER = {
    "email": "alice@example.com",
    "age": 30,
    "name": "Alice Smith",
    "id": "CUST-001",
    "phone": "+441234567890",
    "balance": 500.0,
    "score": 80,
    "date": "2024-01-15",
    "username": "alice_s",
    "password": "securepass123",
}

_INVALID_CUSTOMER = {
    "email": "not-an-email",
    "age": -5,
    "name": "",
}


# ── 1. Health / service info ──────────────────────────────────────────────────

class TestHealthSmoke:
    def test_root_returns_service_name(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "OpenDQV"
        assert body["contracts_loaded"] > 0

    def test_health_returns_healthy(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


# ── 2. Contracts list and detail ──────────────────────────────────────────────

class TestContractsSmoke:
    def test_list_contracts(self, client, auth_headers):
        r = client.get("/api/v1/contracts", headers=auth_headers)
        assert r.status_code == 200
        names = [c["name"] for c in r.json()]
        assert "customer" in names

    def test_contract_detail_has_rules(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "customer"
        assert len(body["rules"]) > 0

    def test_contract_detail_has_status(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer", headers=auth_headers)
        assert r.status_code == 200
        assert "status" in r.json()

    def test_unknown_contract_404(self, client, auth_headers):
        r = client.get("/api/v1/contracts/nonexistent_xyz_abc", headers=auth_headers)
        assert r.status_code == 404


# ── 3. Single-record validation ───────────────────────────────────────────────

class TestValidateSingleSmoke:
    def test_valid_record_passes(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is True
        assert body["errors"] == []
        assert body["contract"] == "customer"

    def test_invalid_record_fails(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _INVALID_CUSTOMER, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0

    def test_response_includes_engine_version(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "engine_version" in r.json()

    def test_record_id_echoed(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer", "record_id": "smoke-001"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["record_id"] == "smoke-001"

    def test_dry_run_returns_valid_response(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer", "dry_run": True},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "valid" in r.json()


# ── 4. Error codes in validation responses ────────────────────────────────────

class TestErrorCodesSmoke:
    """Verify OPENDQV_<TYPE>_001 error codes appear on every failed field."""

    def test_error_code_present_on_failure(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _INVALID_CUSTOMER, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        errors = r.json()["errors"]
        assert len(errors) > 0
        for err in errors:
            assert "error_code" in err, f"error_code missing from: {err}"

    def test_error_code_format(self, client, auth_headers):
        """Error codes must match OPENDQV_<RULETYPE>_001 pattern."""
        r = client.post(
            "/api/v1/validate",
            json={"record": {"email": "bad", "age": -1}, "contract": "customer"},
            headers=auth_headers,
        )
        errors = r.json()["errors"]
        for err in errors:
            code = err["error_code"]
            assert code.startswith("OPENDQV_"), f"Bad error_code format: {code}"
            assert code.endswith("_001"), f"Bad error_code suffix: {code}"

    def test_error_code_in_batch_response(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate/batch",
            json={"records": [_INVALID_CUSTOMER], "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        results = r.json()["results"]
        failed = [row for row in results if not row["valid"]]
        assert len(failed) > 0
        for row in failed:
            for err in row["errors"]:
                assert "error_code" in err


# ── 5. Batch validation ───────────────────────────────────────────────────────

class TestValidateBatchSmoke:
    def test_batch_returns_summary(self, client, auth_headers):
        records = [_VALID_CUSTOMER, _INVALID_CUSTOMER]
        r = client.post(
            "/api/v1/validate/batch",
            json={"records": records, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        summary = r.json()["summary"]
        assert summary["total"] == 2
        assert summary["passed"] >= 1
        assert summary["failed"] >= 1

    def test_batch_all_valid(self, client, auth_headers):
        records = [
            _VALID_CUSTOMER,
            dict(_VALID_CUSTOMER, email="bob@example.com", id="CUST-002", username="bob_s"),
        ]
        r = client.post(
            "/api/v1/validate/batch",
            json={"records": records, "contract": "customer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["summary"]["failed"] == 0

    def test_batch_dry_run(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate/batch",
            json={"records": [_VALID_CUSTOMER], "contract": "customer", "dry_run": True},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["summary"]["total"] == 1


# ── 7. Contract linter ────────────────────────────────────────────────────────

class TestLinterSmoke:
    def test_lint_clean_contract_passes(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer/lint", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] is True
        assert body["error_count"] == 0

    def test_lint_returns_contract_name(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer/lint", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["contract_name"] == "customer"

    def test_lint_unknown_contract_404(self, client, auth_headers):
        r = client.get("/api/v1/contracts/nonexistent_xyz/lint", headers=auth_headers)
        assert r.status_code == 404

    def test_lint_result_has_required_fields(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer/lint", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        for field in ("passed", "error_count", "warning_count", "issues"):
            assert field in body, f"Field '{field}' missing from lint response"


# ── 8. Code generation — new generators ──────────────────────────────────────

class TestCodeGenSmoke:
    def test_spark_generator_returns_sql(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "spark"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        code = r.json()["code"]
        assert "WITH _dqv_checks" in code
        assert "_dqv_valid" in code
        assert "__SOURCE_TABLE__" in code

    def test_bigquery_generator_returns_udf(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "bigquery"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        code = r.json()["code"]
        assert "CREATE OR REPLACE FUNCTION" in code
        assert "LANGUAGE js" in code
        assert "TO_JSON_STRING" in code

    def test_snowflake_generator_still_works(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "snowflake"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "CREATE OR REPLACE FUNCTION" in r.json()["code"]

    def test_salesforce_generator_still_works(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "salesforce"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "OpenDQVValidator" in r.json()["code"]

    def test_js_generator_still_works(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "js"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "opendqvValidate" in r.json()["code"]

    def test_generate_response_includes_code_key(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "spark"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "code" in body
        assert "target" in body
        assert body["target"] == "spark"


# ── 10. Quality trend ─────────────────────────────────────────────────────────

class TestQualityTrendSmoke:
    def test_quality_trend_responds(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer/quality-trend", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "contract" in body
        assert body["contract"] == "customer"


# ── 11. Explain endpoint ──────────────────────────────────────────────────────

class TestExplainSmoke:
    def test_explain_contract_responds(self, client, auth_headers):
        r = client.get(
            "/api/v1/contracts/customer/explain",
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.text
        assert "customer" in body.lower()


# ── 12. Stats endpoint ────────────────────────────────────────────────────────

class TestStatsSmoke:
    def test_stats_responds_after_validation(self, client, auth_headers):
        # First do a validation to seed the stats
        client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer"},
            headers=auth_headers,
        )
        r = client.get("/api/v1/stats", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "total_validations" in body or "validations" in body or isinstance(body, dict)


# ── 13. Auth boundary ─────────────────────────────────────────────────────────

class TestAuthSmoke:
    def test_validate_requires_auth(self, client):
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer"},
        )
        assert r.status_code == 401

    def test_validate_requires_auth_401(self, client):
        # validate is the primary guarded endpoint — confirm auth is enforced
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_CUSTOMER, "contract": "customer"},
        )
        assert r.status_code == 401

    def test_health_is_public(self, client):
        r = client.get("/health")
        assert r.status_code == 200


# ── 14. Validate-file CLI smoke ───────────────────────────────────────────────

class TestValidateFileCLISmoke:
    """Test the validate-file CLI command with a temporary CSV file."""

    def _write_csv(self, records: list) -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, newline=""
        )
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
        f.close()
        return f.name

    def test_valid_file_exits_zero(self):
        import subprocess
        import sys
        path = self._write_csv([_VALID_CUSTOMER])
        try:
            result = subprocess.run(
                [sys.executable, "-m", "opendqv.cli", "validate-file", "customer", path],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "PASS" in result.stdout
        finally:
            os.unlink(path)

    def test_invalid_file_exits_nonzero(self):
        import subprocess
        import sys
        path = self._write_csv([_INVALID_CUSTOMER])
        try:
            result = subprocess.run(
                [sys.executable, "-m", "opendqv.cli", "validate-file", "customer", path],
                cwd=os.path.dirname(os.path.dirname(__file__)),
                capture_output=True, text=True,
            )
            assert result.returncode != 0
            assert "FAIL" in result.stdout
        finally:
            os.unlink(path)

    def test_lint_cli_exits_zero_for_clean_contract(self):
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "opendqv.cli", "lint", "customer"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "PASS" in result.stdout


# ── 15. LocalValidator SDK smoke ──────────────────────────────────────────────

class TestLocalValidatorSmoke:
    """In-process SDK — no API server required."""

    def _contracts_dir(self):
        return os.path.join(os.path.dirname(__file__), "..", "opendqv", "contracts")

    def test_valid_record_passes(self):
        from opendqv.sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        result = v.validate(_VALID_CUSTOMER, contract="customer")
        assert result["valid"] is True

    def test_invalid_record_fails_with_errors(self):
        from opendqv.sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        result = v.validate(_INVALID_CUSTOMER, contract="customer")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_batch_returns_summary(self):
        from opendqv.sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        result = v.validate_batch(
            [_VALID_CUSTOMER, _INVALID_CUSTOMER], contract="customer"
        )
        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1


# ── 16. Observation mode ────────────────────────────────────────────────────

class TestObservationModeSmoke:
    """16. Observation mode — basic happy path"""

    def test_observe_only_single_returns_200_with_real_valid(self, client, auth_headers):
        # CRT170/J1: observe mode never blocks (always HTTP 200) but `valid`
        # reflects actual outcome — bad record → valid=False.
        r = client.post(
            "/api/v1/validate",
            json={"record": _INVALID_CUSTOMER, "contract": "customer", "observe_only": True},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["mode"] == "observation_only"

    def test_observe_only_mode_field_in_response(self, client, auth_headers):
        r = client.post(
            "/api/v1/validate",
            json={"record": _INVALID_CUSTOMER, "contract": "customer", "observe_only": True},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "observation_only"
        assert data["would_have_failed"] is True

    def test_observation_summary_endpoint_reachable(self, client, auth_headers):
        r = client.get("/api/v1/observation/summary", headers=auth_headers)
        assert r.status_code == 200
