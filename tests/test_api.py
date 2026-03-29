"""Integration tests for the REST API."""



class TestHealth:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["service"] == "OpenDQV"
        assert data["contracts_loaded"] > 0

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"


class TestTokens:
    def test_generate_token(self, client, admin_headers):
        r = client.post("/api/v1/tokens/generate", params={"username": "testuser"}, headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert "pat" in data
        assert "username" in data
        assert "expires_at" in data
        assert "expiry_days" in data

    def test_generate_token_custom_expiry(self, client, admin_headers):
        r = client.post("/api/v1/tokens/generate", params={"username": "short-lived", "expiry_days": 7}, headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["expiry_days"] == 7

    def test_revoke_token(self, client, admin_headers):
        r = client.post("/api/v1/tokens/generate", params={"username": "revokeuser"}, headers=admin_headers)
        token = r.json()["pat"]
        r = client.post("/api/v1/tokens/revoke", content=token, headers={"Content-Type": "text/plain", **admin_headers})
        assert r.status_code == 200
        assert r.json()["status"] == "revoked"


class TestAuthBoundaries:
    """Verify endpoints that require authentication return 401 without a token."""

    def test_stats_requires_auth(self, client):
        r = client.get("/api/v1/stats")
        assert r.status_code == 401

    def test_tokens_revoke_requires_auth(self, client):
        r = client.post("/api/v1/tokens/revoke", content="fake_token",
                        headers={"Content-Type": "text/plain"})
        assert r.status_code == 401

    def test_stats_succeeds_with_auth(self, client, auth_headers):
        r = client.get("/api/v1/stats", headers=auth_headers)
        assert r.status_code == 200


class TestContracts:
    def test_list_contracts(self, client):
        r = client.get("/api/v1/contracts")
        assert r.status_code == 200
        contracts = r.json()
        assert len(contracts) > 0
        names = [c["name"] for c in contracts]
        assert "customer" in names

    def test_get_contract_detail(self, client):
        r = client.get("/api/v1/contracts/customer")
        assert r.status_code == 200
        detail = r.json()
        assert detail["name"] == "customer"
        assert len(detail["rules"]) > 0

    def test_get_contract_not_found(self, client):
        r = client.get("/api/v1/contracts/nonexistent")
        assert r.status_code == 404


class TestValidateSingle:
    def test_valid_record(self, client, auth_headers):
        body = {
            "record": {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["contract"] == "customer"

    def test_invalid_record(self, client, auth_headers):
        body = {
            "record": {"email": "not-an-email", "age": -5, "name": ""},
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0
        # Check error structure
        err = data["errors"][0]
        assert "field" in err
        assert "rule" in err
        assert "message" in err
        assert "severity" in err

    def test_record_id_echoed(self, client, auth_headers):
        body = {
            "record": {"email": "a@b.com"},
            "contract": "customer",
            "record_id": "sf-lead-12345",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.json()["record_id"] == "sf-lead-12345"

    def test_contract_not_found(self, client, auth_headers):
        body = {"record": {}, "contract": "nonexistent"}
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 404

    def test_no_auth(self, client):
        body = {"record": {"email": "a@b.com"}, "contract": "customer"}
        r = client.post("/api/v1/validate", json=body)
        assert r.status_code == 401

    def test_context_override(self, client, auth_headers):
        # kids_app context: age must be 5-17
        body = {
            "record": {"email": "kid@example.com", "age": 25, "name": "Kiddo"},
            "contract": "customer",
            "context": "kids_app",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        data = r.json()
        # Age 25 should fail in kids_app context (5-17)
        age_errors = [e for e in data["errors"] if e["field"] == "age"]
        assert len(age_errors) > 0


class TestValidateBatch:
    def test_batch_success(self, client, auth_headers):
        body = {
            "records": [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "c@d.com", "age": 30, "name": "Bob"},
            ],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["total"] == 2
        assert len(data["results"]) == 2

    def test_batch_mixed(self, client, auth_headers):
        body = {
            "records": [
                {"email": "good@test.com", "age": 25, "name": "Alice"},
                {"email": "bad", "age": -5, "name": ""},
            ],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        data = r.json()
        assert data["summary"]["failed"] > 0
        assert data["results"][0]["valid"] is True
        assert data["results"][1]["valid"] is False

    def test_batch_no_auth(self, client):
        body = {"records": [{"email": "a@b.com"}], "contract": "customer"}
        r = client.post("/api/v1/validate/batch", json=body)
        assert r.status_code == 401


class TestCodeGeneration:
    def test_generate_snowflake(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "snowflake"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "CREATE OR REPLACE FUNCTION" in r.json()["code"]

    def test_generate_js(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "js"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "opendqvValidate" in r.json()["code"]

    def test_generate_snowflake_header_content(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "snowflake"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        code = r.json()["code"]
        assert "-- Generated by OpenDQV" in code
        assert "customer" in code
        assert "opendqv generate customer snowflake" in code

    def test_generate_salesforce_header_content(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "salesforce"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        code = r.json()["code"]
        assert "// Generated by OpenDQV" in code
        assert "customer" in code
        assert "opendqv generate customer salesforce" in code

    def test_generate_js_header_content(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate",
            params={"contract_name": "customer", "target": "js"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        code = r.json()["code"]
        assert "// Generated by OpenDQV" in code
        assert "customer" in code
        assert "opendqv generate customer js" in code


class TestTokenRoles:
    """Token role field — default and custom. Requires admin role (C1 fix, RT148)."""

    def test_generate_token_has_role_field(self, client, admin_headers):
        r = client.post("/api/v1/tokens/generate", params={"username": "roletest"}, headers=admin_headers)
        assert r.status_code == 200
        data = r.json()
        assert "role" in data
        assert data["role"] == "validator"

    def test_list_tokens_after_generate(self, client, admin_headers):
        client.post("/api/v1/tokens/generate", params={"username": "listrole"}, headers=admin_headers)
        r = client.get("/api/v1/tokens", headers=admin_headers)
        assert r.status_code == 200
        tokens = r.json()
        assert any(t["username"] == "listrole" for t in tokens)

    def test_generate_token_with_custom_role(self, client, admin_headers):
        r = client.post(
            "/api/v1/tokens/generate",
            params={"username": "editor-role-test", "role": "editor"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["role"] == "editor"

    def test_token_role_appears_in_list(self, client, admin_headers):
        client.post(
            "/api/v1/tokens/generate",
            params={"username": "auditor-role-test", "role": "auditor"},
            headers=admin_headers,
        )
        tokens = client.get("/api/v1/tokens", headers=admin_headers).json()
        match = next((t for t in tokens if t["username"] == "auditor-role-test"), None)
        assert match is not None
        assert match["role"] == "auditor"

    def test_generate_token_invalid_role_rejected(self, client, admin_headers):
        r = client.post(
            "/api/v1/tokens/generate",
            params={"username": "test", "role": "superadmin"},
            headers=admin_headers,
        )
        assert r.status_code == 422


class TestProfileFile:
    def test_profile_file_csv(self, client, auth_headers):
        import io
        csv_content = b"amount,status\n10,active\n20,active\n30,closed\n"
        r = client.post(
            "/api/v1/profile/file",
            headers=auth_headers,
            files={"file": ("data.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert r.status_code == 200
        body = r.json()
        assert "profile" in body
        assert "contract" in body
        assert body["rows"] == 3
        assert "amount" in body["profile"]["fields"]
        assert body["profile"]["fields"]["amount"]["type"] == "numeric"

    def test_profile_file_has_duckdb_stats(self, client, auth_headers):
        import io
        csv_content = b"score\n10\n20\n30\n40\n50\n"
        r = client.post(
            "/api/v1/profile/file",
            headers=auth_headers,
            files={"file": ("scores.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert r.status_code == 200
        field = r.json()["profile"]["fields"]["score"]
        assert "mean" in field
        assert "p50" in field

    def test_profile_file_parquet(self, client, auth_headers):
        import io
        import pandas as pd
        df = pd.DataFrame({"x": [1, 2, 3], "label": ["a", "b", "a"]})
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        r = client.post(
            "/api/v1/profile/file",
            headers=auth_headers,
            files={"file": ("data.parquet", buf, "application/octet-stream")},
        )
        assert r.status_code == 200
        assert r.json()["rows"] == 3

    def test_profile_file_too_large(self, client, auth_headers, monkeypatch):
        import io
        import api.routes as routes_module
        monkeypatch.setattr(routes_module, "MAX_UPLOAD_MB", 0)
        csv_content = b"a,b\n1,2\n"
        r = client.post(
            "/api/v1/profile/file",
            headers=auth_headers,
            files={"file": ("data.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert r.status_code == 413

    def test_profile_file_no_auth(self, client):
        import io
        csv_content = b"a\n1\n"
        r = client.post(
            "/api/v1/profile/file",
            files={"file": ("data.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert r.status_code == 401


class TestDeleteQualityStatsByContext:
    """ACT-RT110 — DELETE /api/v1/quality/stats?context= endpoint."""

    def _seed_stats(self, client, auth_headers, context: str, n: int = 2):
        """Seed quality_stats rows by validating records with a given context tag."""
        for i in range(n):
            client.post(
                "/api/v1/validate",
                json={"contract": "customer", "context": context,
                      "record": {"name": f"User{i}", "age": 25, "email": f"u{i}@x.com"}},
                headers=auth_headers,
            )

    def test_delete_requires_auth(self, client):
        r = client.delete("/api/v1/quality/stats", params={"context": "demo"})
        assert r.status_code == 401

    def test_delete_requires_admin_role(self, client, auth_headers):
        r = client.delete("/api/v1/quality/stats", params={"context": "demo"},
                          headers=auth_headers)
        assert r.status_code == 403

    def test_delete_returns_count_and_context(self, client, admin_headers, auth_headers):
        tag = "rt110_test_ctx"
        self._seed_stats(client, auth_headers, tag, n=3)
        r = client.delete("/api/v1/quality/stats", params={"context": tag},
                          headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["context"] == tag
        assert body["deleted"] >= 3

    def test_delete_nonexistent_context_returns_zero(self, client, admin_headers):
        r = client.delete("/api/v1/quality/stats",
                          params={"context": "ctx_that_never_existed_xyz"},
                          headers=admin_headers)
        assert r.status_code == 200
        assert r.json() == {"deleted": 0, "context": "ctx_that_never_existed_xyz"}

    def test_delete_is_idempotent(self, client, admin_headers, auth_headers):
        tag = "rt110_idempotent_ctx"
        self._seed_stats(client, auth_headers, tag, n=2)
        r1 = client.delete("/api/v1/quality/stats", params={"context": tag},
                           headers=admin_headers)
        assert r1.json()["deleted"] >= 2
        r2 = client.delete("/api/v1/quality/stats", params={"context": tag},
                           headers=admin_headers)
        assert r2.status_code == 200
        assert r2.json()["deleted"] == 0
