"""Tests for the webhook notification system (unit + integration)."""

import os
import tempfile
import pytest

from core.webhooks import WebhookManager


# ---------------------------------------------------------------------------
# Unit tests for WebhookManager
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_db():
    """Provide a fresh temporary DB path for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


class TestWebhookManager:
    """Unit tests that exercise WebhookManager directly."""

    def test_register_webhook(self, webhook_db):
        mgr = WebhookManager(webhook_db)
        hook = mgr.register("https://example.com/hook")

        assert hook["url"] == "https://example.com/hook"
        hooks = mgr.list_hooks()
        assert len(hooks) == 1
        assert hooks[0]["url"] == "https://example.com/hook"

    def test_register_with_filters(self, webhook_db):
        mgr = WebhookManager(webhook_db)
        hook = mgr.register(
            "https://example.com/hook",
            events=["opendqv.validation.failed"],
            contracts=["customer"],
        )

        assert hook["events"] == ["opendqv.validation.failed"]
        assert hook["contracts"] == ["customer"]

        stored = mgr.list_hooks()[0]
        assert stored["events"] == ["opendqv.validation.failed"]
        assert stored["contracts"] == ["customer"]

    def test_unregister_webhook(self, webhook_db):
        mgr = WebhookManager(webhook_db)
        mgr.register("https://example.com/hook")
        assert len(mgr.list_hooks()) == 1

        removed = mgr.unregister("https://example.com/hook")
        assert removed is True
        assert len(mgr.list_hooks()) == 0

    def test_unregister_nonexistent(self, webhook_db):
        mgr = WebhookManager(webhook_db)
        removed = mgr.unregister("https://example.com/does-not-exist")
        assert removed is False

    def test_list_hooks_empty(self, webhook_db):
        mgr = WebhookManager(webhook_db)
        assert mgr.list_hooks() == []


# ---------------------------------------------------------------------------
# Integration tests via the FastAPI TestClient
# ---------------------------------------------------------------------------


class TestWebhookAPI:
    """Integration tests that hit the REST webhook endpoints."""

    def test_register_webhook_endpoint(self, client, editor_headers):
        r = client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/wh1"},
            headers=editor_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "registered"
        assert data["webhook"]["url"] == "https://example.com/wh1"

    def test_list_webhooks_endpoint(self, client, editor_headers):
        # Register one first so the list is non-empty
        client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/wh-list"},
            headers=editor_headers,
        )
        r = client.get("/api/v1/webhooks", headers=editor_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        urls = [h["url"] for h in data]
        assert "https://example.com/wh-list" in urls

    def test_unregister_webhook_endpoint(self, client, editor_headers):
        # Register then remove
        client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/wh-del"},
            headers=editor_headers,
        )
        r = client.request(
            "DELETE",
            "/api/v1/webhooks",
            json={"url": "https://example.com/wh-del"},
            headers=editor_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "unregistered"

    def test_webhook_endpoints_require_auth(self, client):
        # POST without auth
        r = client.post("/api/v1/webhooks", json={"url": "https://example.com/no-auth"})
        assert r.status_code == 401

        # GET without auth
        r = client.get("/api/v1/webhooks")
        assert r.status_code == 401

        # DELETE without auth
        r = client.request("DELETE", "/api/v1/webhooks", json={"url": "https://example.com/no-auth"})
        assert r.status_code == 401

    def test_register_and_verify_state(self, client, editor_headers):
        """Register a webhook via the API and verify it shows up in the list."""
        url = "https://example.com/wh-state-check"
        client.post(
            "/api/v1/webhooks",
            json={
                "url": url,
                "events": ["opendqv.validation.failed"],
                "contracts": ["customer"],
            },
            headers=editor_headers,
        )

        r = client.get("/api/v1/webhooks", headers=editor_headers)
        assert r.status_code == 200
        hooks = r.json()
        matched = [h for h in hooks if h["url"] == url]
        assert len(matched) == 1
        assert matched[0]["events"] == ["opendqv.validation.failed"]
        assert matched[0]["contracts"] == ["customer"]


class TestWebhookSchemas:
    """Schema constants are present and structurally sound."""

    def test_validation_schema_has_required_fields(self):
        from core.webhooks import VALIDATION_EVENT_SCHEMA
        required_fields = {"event", "timestamp", "contract", "contract_version",
                           "opendqv_node_id", "valid", "error_count", "warning_count", "violations"}
        assert required_fields.issubset(VALIDATION_EVENT_SCHEMA.keys())

    def test_batch_schema_has_required_fields(self):
        from core.webhooks import BATCH_EVENT_SCHEMA
        required_fields = {"event", "timestamp", "contract", "contract_version",
                           "opendqv_node_id", "total", "passed", "failed"}
        assert required_fields.issubset(BATCH_EVENT_SCHEMA.keys())

    def test_validation_schema_violations_is_list(self):
        from core.webhooks import VALIDATION_EVENT_SCHEMA
        assert VALIDATION_EVENT_SCHEMA["violations"]["type"] == "list"
        assert "field" in VALIDATION_EVENT_SCHEMA["violations"]["items"]

    def test_valid_events_set(self):
        """VALID_EVENTS contains all expected event types including lifecycle events."""
        from core.webhooks import VALID_EVENTS
        assert VALID_EVENTS == {
            "opendqv.validation.failed",
            "opendqv.validation.warning",
            "opendqv.batch.failed",
            "opendqv.contract.submitted",
            "opendqv.contract.approved",
            "opendqv.contract.rejected",
        }
