"""Tests for the federation API skeleton endpoints."""



class TestFederationStatus:
    """GET /api/v1/federation/status"""

    def test_requires_auth(self, client):
        r = client.get("/api/v1/federation/status")
        assert r.status_code == 401

    def test_returns_200_with_auth(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        assert r.status_code == 200

    def test_response_shape(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        data = r.json()
        assert "opendqv_node_id" in data
        assert "is_federated" in data
        assert "opendqv_node_state" in data
        assert "audit_mode" in data
        assert "contracts_loaded" in data
        assert "time_in_state_seconds" in data
        assert "isolated_since" in data

    def test_standalone_not_federated(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        data = r.json()
        # Test environment has no OPENDQV_UPSTREAM set
        assert data["is_federated"] is False
        assert data["upstream_url"] is None

    def test_node_state_valid_value(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        data = r.json()
        assert data["opendqv_node_state"] in ("online", "degraded", "isolated")

    def test_contracts_loaded_positive(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        data = r.json()
        assert data["contracts_loaded"] > 0

    def test_time_in_state_is_non_negative(self, client, auth_headers):
        r = client.get("/api/v1/federation/status", headers=auth_headers)
        data = r.json()
        assert data["time_in_state_seconds"] >= 0.0


class TestFederationLog:
    """GET /api/v1/federation/log"""

    def test_requires_auth(self, client):
        r = client.get("/api/v1/federation/log")
        assert r.status_code == 401

    def test_returns_200_with_auth(self, client, auth_headers):
        r = client.get("/api/v1/federation/log", headers=auth_headers)
        assert r.status_code == 200

    def test_response_shape(self, client, auth_headers):
        r = client.get("/api/v1/federation/log", headers=auth_headers)
        data = r.json()
        assert "opendqv_node_id" in data
        assert "since" in data
        assert "count" in data
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_default_since_zero(self, client, auth_headers):
        r = client.get("/api/v1/federation/log", headers=auth_headers)
        assert r.json()["since"] == 0

    def test_since_param_echoed(self, client, auth_headers):
        r = client.get("/api/v1/federation/log?since=99", headers=auth_headers)
        assert r.json()["since"] == 99

    def test_empty_log_in_standalone(self, client, auth_headers):
        # Standalone mode with no federation events — log should be empty
        r = client.get("/api/v1/federation/log", headers=auth_headers)
        data = r.json()
        assert data["count"] == len(data["events"])

    def test_contract_filter_param_accepted(self, client, auth_headers):
        r = client.get(
            "/api/v1/federation/log?contract=customer",
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestFederationHealth:
    """GET /api/v1/federation/health"""

    def test_requires_auth(self, client):
        r = client.get("/api/v1/federation/health")
        assert r.status_code == 401

    def test_returns_200_with_auth(self, client, auth_headers):
        r = client.get("/api/v1/federation/health", headers=auth_headers)
        assert r.status_code == 200

    def test_response_shape(self, client, auth_headers):
        r = client.get("/api/v1/federation/health", headers=auth_headers)
        data = r.json()
        assert "opendqv_node_id" in data
        assert "opendqv_node_state" in data
        assert "time_in_state_seconds" in data
        assert "isolated_since" in data
        assert "health_log" in data
        assert "open_isolation_events" in data
        assert "recent_isolation_events" in data

    def test_health_log_is_list(self, client, auth_headers):
        r = client.get("/api/v1/federation/health", headers=auth_headers)
        data = r.json()
        assert isinstance(data["health_log"], list)

    def test_health_log_has_genesis_entry(self, client, auth_headers):
        r = client.get("/api/v1/federation/health", headers=auth_headers)
        data = r.json()
        # At minimum there's a genesis entry from NodeHealthStateMachine init
        assert len(data["health_log"]) >= 1

    def test_no_open_isolation_in_normal_operation(self, client, auth_headers):
        r = client.get("/api/v1/federation/health", headers=auth_headers)
        data = r.json()
        assert data["open_isolation_events"] == []

    def test_log_limit_param_accepted(self, client, auth_headers):
        r = client.get(
            "/api/v1/federation/health?log_limit=5",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["health_log"]) <= 5


class TestFederationRegister:
    """POST /api/v1/federation/register — stub returns 501."""

    def test_returns_501(self, client):
        r = client.post("/api/v1/federation/register", json={})
        assert r.status_code == 501

    def test_error_detail_shape(self, client):
        r = client.post("/api/v1/federation/register", json={})
        detail = r.json()["detail"]
        assert detail["error"] == "federation_not_enabled"
        assert "message" in detail
        assert "docs" in detail

    def test_no_auth_required_for_register_stub(self, client):
        # register is intentionally unauthenticated — it's a 501 stub that
        # should tell even unauthenticated callers to configure OPENDQV_UPSTREAM
        r = client.post("/api/v1/federation/register", json={})
        assert r.status_code == 501

    def test_message_mentions_upstream_env_var(self, client):
        r = client.post("/api/v1/federation/register", json={})
        detail = r.json()["detail"]
        assert "OPENDQV_UPSTREAM" in detail["message"]
