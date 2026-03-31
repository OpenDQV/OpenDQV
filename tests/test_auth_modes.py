"""
Auth mode test matrix — ACT-AUTH series.

Verifies that every security-sensitive endpoint behaves correctly in
both AUTH_MODE=open and AUTH_MODE=token. A gap was found where we tested
auth-works and auth-blocks, but almost never tested the bypass — that in
open mode, endpoints work without a token.

The conftest hardcodes AUTH_MODE=token for all tests. This file patches
config.AUTH_MODE per-test using unittest.mock.patch, which overrides
the value at call time (both security/auth.py and api/routes.py read
config.AUTH_MODE on each request, not at import time).
"""
import pytest
from unittest.mock import patch
import config


# ── Helpers ───────────────────────────────────────────────────────────────────

def _no_auth(client, method, url, **kwargs):
    """Make a request with no Authorization header."""
    return client.request(method, url, **kwargs)


# ── ACT-AUTH-001: /explain respects AUTH_MODE=open ───────────────────────────

class TestExplainAuthMode:
    """/explain returned 401 in AUTH_MODE=open because the authorization check
    fired before the auth-mode check. These tests lock in the correct behaviour
    so it can never regress."""

    def test_explain_accessible_in_open_mode_without_token(self, client):
        """/explain must return 200 in AUTH_MODE=open without any token."""
        with patch.object(config, 'AUTH_MODE', 'open'):
            resp = _no_auth(client, "GET", "/api/v1/contracts/customer/explain")
        assert resp.status_code == 200, (
            f"/explain returned {resp.status_code} in AUTH_MODE=open without token. "
            f"Expected 200. Response: {resp.text[:200]}"
        )

    def test_explain_blocked_in_token_mode_without_token(self, client):
        """/explain must return 401 in AUTH_MODE=token without a token."""
        # conftest already sets AUTH_MODE=token, but we be explicit
        with patch.object(config, 'AUTH_MODE', 'token'):
            resp = _no_auth(client, "GET", "/api/v1/contracts/customer/explain")
        assert resp.status_code == 401

    def test_explain_accessible_in_token_mode_with_explain_public(self, client, auth_headers):
        """/explain must be accessible without a token when EXPLAIN_PUBLIC=true,
        regardless of AUTH_MODE."""
        with patch.object(config, 'AUTH_MODE', 'token'):
            with patch("api.deps.EXPLAIN_PUBLIC", True):
                resp = _no_auth(client, "GET", "/api/v1/contracts/customer/explain")
        assert resp.status_code == 200, (
            f"EXPLAIN_PUBLIC=true should allow unauthenticated access but got {resp.status_code}"
        )

    def test_explain_accessible_with_valid_token_in_token_mode(self, client, auth_headers):
        """/explain works normally with a valid token in token mode."""
        with patch.object(config, 'AUTH_MODE', 'token'):
            resp = client.get("/api/v1/contracts/customer/explain", headers=auth_headers)
        assert resp.status_code == 200


# ── ACT-AUTH-002: standard endpoints work without token in open mode ──────────

class TestOpenModeBypass:
    """In AUTH_MODE=open, every endpoint that uses get_current_user must
    work without an Authorization header. This is the documented behaviour
    for local development and self-hosted deployments with upstream auth."""

    @pytest.mark.parametrize("method,url,body", [
        ("GET",  "/api/v1/contracts",  None),
        ("GET",  "/api/v1/contracts/customer",  None),
        ("POST", "/api/v1/validate",
         {"contract": "customer", "record": {"name": "Alice", "email": "alice@example.com"}}),
        ("POST", "/api/v1/validate/batch",
         {"contract": "customer", "records": [{"name": "Alice", "email": "alice@example.com"}]}),
        ("GET",  "/health",  None),
    ], ids=["list_contracts", "get_contract", "validate", "validate_batch", "health"])
    def test_endpoint_works_without_token_in_open_mode(self, client, method, url, body):
        """Key endpoints must not return 401 in AUTH_MODE=open without a token."""
        with patch.object(config, 'AUTH_MODE', 'open'):
            if body:
                resp = client.request(method, url, json=body)
            else:
                resp = client.request(method, url)
        assert resp.status_code != 401, (
            f"{method} {url} returned 401 in AUTH_MODE=open without a token. "
            f"Open mode must not require auth. Response: {resp.text[:200]}"
        )
        assert resp.status_code < 500, (
            f"{method} {url} returned server error {resp.status_code}: {resp.text[:200]}"
        )

    def test_open_mode_grants_admin_role(self, client):
        """In AUTH_MODE=open, get_current_role returns 'admin' — development convenience."""
        with patch.object(config, 'AUTH_MODE', 'open'):
            # An admin-only endpoint should be accessible
            resp = client.post("/api/v1/contracts/reload")
        assert resp.status_code != 403, (
            f"In AUTH_MODE=open, admin-only endpoints should be accessible "
            f"(get_current_role returns 'admin'). Got {resp.status_code}"
        )

    def test_token_mode_requires_auth_on_protected_endpoint(self, client):
        """Sanity check: the same endpoint must require auth in token mode."""
        with patch.object(config, 'AUTH_MODE', 'token'):
            resp = _no_auth(client, "POST", "/api/v1/contracts/reload")
        assert resp.status_code == 401


# ── ACT-AUTH-003: /explain/{field}/{rule_name} is always public ───────────────

class TestExplainFieldEndpoint:
    """The per-field explain endpoint has no auth dependency at all —
    it is always public. This must be true in both modes."""

    def test_explain_field_accessible_in_token_mode_without_token(self, client):
        """/explain/{field}/{rule_name} must be accessible without a token
        even in AUTH_MODE=token."""
        with patch.object(config, 'AUTH_MODE', 'token'):
            resp = _no_auth(
                client, "GET",
                "/api/v1/contracts/customer/explain/email/valid_email",
            )
        # 200 if rule exists, 404 if not — either is acceptable, 401 is not
        assert resp.status_code != 401, (
            f"/explain/{{field}}/{{rule_name}} should always be public "
            f"but returned {resp.status_code} in token mode without a token"
        )

    def test_explain_field_accessible_in_open_mode_without_token(self, client):
        """/explain/{field}/{rule_name} must also work in AUTH_MODE=open."""
        with patch.object(config, 'AUTH_MODE', 'open'):
            resp = _no_auth(
                client, "GET",
                "/api/v1/contracts/customer/explain/email/valid_email",
            )
        assert resp.status_code != 401


# ── ACT-AUTH-004: token mode strictly enforces auth on all paths ──────────────

class TestTokenModeEnforcement:
    """Belt-and-braces: verify that switching to token mode re-enables
    auth on endpoints that bypass it in open mode."""

    @pytest.mark.parametrize("method,url,body", [
        # /api/v1/contracts is intentionally public (no auth dep) — not included here
        ("POST", "/api/v1/validate",
         {"contract": "customer", "record": {"name": "Alice"}}),
        ("POST", "/api/v1/validate/batch",
         {"contract": "customer", "records": [{"name": "Alice"}]}),
        ("GET",  "/api/v1/contracts/customer/explain", None),
    ], ids=["validate", "validate_batch", "explain"])
    def test_token_mode_blocks_unauthenticated(self, client, method, url, body):
        """Endpoints that require auth must return 401 in token mode without a token."""
        with patch.object(config, 'AUTH_MODE', 'token'):
            kwargs = {"json": body} if body else {}
            resp = _no_auth(client, method, url, **kwargs)
        assert resp.status_code == 401, (
            f"{method} {url} should return 401 in token mode without a token, "
            f"got {resp.status_code}"
        )
