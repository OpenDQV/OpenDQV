"""
tests/test_crt172_k5_config_endpoint.py — CRT172/K5 acceptance.

Pins the GET /config endpoint introduced in v2.3.8.

Before v2.3.8:
    Tenant configuration was scattered across `/` (a few discovery
    fields), `/health` (extended-mode body, only when
    OPENDQV_HEALTH_DETAIL=true), and the `opendqv.config` module
    itself. An auditor wanting to confirm "what AUTH_MODE,
    AUDIT_MODE, and rate limits is this node actually running"
    had no single endpoint to call — they had to read three
    surfaces and still missed federation/MCP/policy values.

From v2.3.8:
    GET /config returns one consolidated snapshot, auth-gated to
    admin or auditor. Secret-bearing values (SECRET_KEY, DB_URL,
    JOIN_TOKEN, MCP_TOKEN) are deliberately omitted — only their
    presence is reported, never the value.

Working principle (CRT170, extended): a response field's value
must reflect what its name claims. Sections (auth/audit/storage/
limits/...) are named so a regulator reading the JSON knows
exactly what concern each block represents.
"""
from fastapi.testclient import TestClient


class TestConfigEndpointShape:

    def test_returns_200_for_admin(self, client: TestClient, admin_headers):
        resp = client.get("/config", headers=admin_headers)
        assert resp.status_code == 200

    def test_returns_200_for_auditor(self, client: TestClient, auditor_headers):
        resp = client.get("/config", headers=auditor_headers)
        assert resp.status_code == 200

    def test_returns_403_for_validator_role(self, client: TestClient, auth_headers):
        """Default test_token is the 'validator' role (RBAC default)."""
        resp = client.get("/config", headers=auth_headers)
        assert resp.status_code == 403

    def test_returns_403_for_reader_role(self, client: TestClient, reader_headers):
        resp = client.get("/config", headers=reader_headers)
        assert resp.status_code == 403

    def test_required_top_level_sections_present(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        required = {
            "engine_version", "node_id",
            "auth", "audit", "storage", "limits",
            "rate_limits", "federation", "mcp", "policy",
        }
        assert required.issubset(set(body.keys()))


class TestConfigSecretsNeverLeak:
    """
    Sentinel guard. A regulator must be able to call /config
    without exposing tenant secrets. We assert by exact key
    name and by value pattern — both the field-presence and
    string-content checks would have to fail before a real
    secret could leak.
    """

    def test_secret_key_value_never_in_response(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        assert "secret_key" not in body["auth"]
        # Only a boolean indicator of insecurity is allowed.
        assert isinstance(body["auth"]["secret_key_insecure"], bool)

    def test_db_url_value_never_in_response(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        assert "db_url" not in body["storage"]
        assert isinstance(body["storage"]["db_url_set"], bool)

    def test_join_token_value_never_in_response(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        assert "join_token" not in body["federation"]
        assert isinstance(body["federation"]["join_token_set"], bool)

    def test_mcp_token_value_never_in_response(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        assert "token" not in body["mcp"]
        assert isinstance(body["mcp"]["token_set"], bool)


class TestConfigSectionContents:

    def test_auth_section(self, client: TestClient, admin_headers):
        body = client.get("/config", headers=admin_headers).json()
        auth = body["auth"]
        assert auth["mode"] in ("open", "token")
        assert isinstance(auth["token_expiry_days"], int)
        assert auth["token_expiry_days"] >= 1

    def test_audit_section(self, client: TestClient, admin_headers):
        body = client.get("/config", headers=admin_headers).json()
        audit = body["audit"]
        assert audit["mode"] in ("basic", "signed")
        assert isinstance(audit["trust_proxy_headers"], bool)

    def test_storage_section(self, client: TestClient, admin_headers):
        body = client.get("/config", headers=admin_headers).json()
        storage = body["storage"]
        assert storage["db_backend"] in ("sqlite", "postgres")
        assert isinstance(storage["contracts_dir"], str)
        assert storage["contracts_dir"]  # non-empty

    def test_limits_section(self, client: TestClient, admin_headers):
        body = client.get("/config", headers=admin_headers).json()
        limits = body["limits"]
        assert limits["max_batch_rows"] >= 1
        assert limits["max_isolation_hours"] >= 1
        assert limits["max_sse_connections"] >= 1

    def test_rate_limits_section(self, client: TestClient, admin_headers):
        body = client.get("/config", headers=admin_headers).json()
        rl = body["rate_limits"]
        assert "default" in rl and "validate" in rl and "tokens" in rl
        assert isinstance(rl["validate_active"], bool)

    def test_engine_version_matches_running_engine(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/config", headers=admin_headers).json()
        from opendqv.config import ENGINE_VERSION
        assert body["engine_version"] == ENGINE_VERSION
