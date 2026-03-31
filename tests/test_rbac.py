"""
Role-Based Access Control tests — full permission matrix.

Covers all 6 roles across all role-gated endpoints:
  - validator — validate records only; cannot touch contracts (source systems)
  - reader    — read contracts and validate; cannot mutate anything
  - auditor   — read + validate + access audit trail; cannot mutate contracts
  - editor    — validate + author DRAFT contracts + submit for review; cannot approve
  - approver  — validate + approve/reject (pure reviewer; cannot author contracts)
  - admin     — unrestricted

Maker-checker principle: editor and approver must be different people.
An editor CANNOT approve their own submission.
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RULE = {
    "name": "rbac_test_rule",
    "type": "not_empty",
    "field": "test_field",
    "severity": "error",
    "error_message": "test_field is required",
}

_VALID_RECORD = {"email": "alice@example.com", "age": 25, "name": "Alice"}


# ---------------------------------------------------------------------------
# Validation — all authenticated roles may validate
# ---------------------------------------------------------------------------

class TestValidationAllRoles:
    """All authenticated roles can call POST /validate."""

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",      # validator
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
        "admin_headers",
    ])
    def test_validate_allowed_for_all_roles(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/validate",
            json={"record": _VALID_RECORD, "contract": "customer"},
            headers=headers,
        )
        assert r.status_code == 200, f"{headers_fixture} should be able to validate"


# ---------------------------------------------------------------------------
# Rule mutations — editor, approver, admin only
# ---------------------------------------------------------------------------

class TestRuleMutationRoles:
    """Only editor, approver, admin may add/update/delete rules on DRAFT contracts."""

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",   # validator
        "reader_headers",
        "auditor_headers",
    ])
    def test_add_rule_forbidden_for_non_editors(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/contracts/customer/rules",
            json=_RULE,
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from adding rules"

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",   # validator
        "reader_headers",
        "auditor_headers",
    ])
    def test_update_rule_forbidden_for_non_editors(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.put(
            "/api/v1/contracts/customer/rules/valid_email",
            json=_RULE,
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from updating rules"

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",   # validator
        "reader_headers",
        "auditor_headers",
    ])
    def test_delete_rule_forbidden_for_non_editors(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.delete(
            "/api/v1/contracts/customer/rules/valid_email",
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from deleting rules"


# ---------------------------------------------------------------------------
# Submit for review — editor, approver, admin only
# ---------------------------------------------------------------------------

class TestSubmitReviewRoles:
    """Only editor or admin may submit a contract for review (approver is excluded — maker-checker)."""

    @pytest.mark.parametrize("headers_fixture,contract", [
        ("auth_headers",    "customer"),
        ("reader_headers",  "banking_transaction"),
        ("auditor_headers", "hr_employee"),
    ])
    def test_submit_review_forbidden(self, request, client, headers_fixture, contract):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            f"/api/v1/contracts/{contract}/1.0/submit-review",
            json={"proposed_by": "tester@example.com"},
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from submitting for review"


# ---------------------------------------------------------------------------
# Approve / Reject — approver and admin only
# ---------------------------------------------------------------------------

class TestApproveRejectRoles:
    """Only approver and admin may approve or reject contracts."""

    @pytest.mark.parametrize("headers_fixture,contract", [
        ("auth_headers",      "customer"),    # validator
        ("reader_headers",    "banking_transaction"),
        ("auditor_headers",   "hr_employee"),
        ("editor_headers",    "insurance_claim"),  # editor CANNOT approve
    ])
    def test_approve_forbidden(self, request, client, headers_fixture, contract):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            f"/api/v1/contracts/{contract}/1.0/approve",
            json={"approved_by": "tester@example.com"},
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from approving"

    @pytest.mark.parametrize("headers_fixture,contract", [
        ("auth_headers",      "customer"),
        ("reader_headers",    "banking_transaction"),
        ("auditor_headers",   "hr_employee"),
        ("editor_headers",    "insurance_claim"),
    ])
    def test_reject_forbidden(self, request, client, headers_fixture, contract):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            f"/api/v1/contracts/{contract}/1.0/reject",
            json={"rejected_by": "tester@example.com", "reason": "test"},
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from rejecting"


# ---------------------------------------------------------------------------
# Contract status change — activation requires approver/admin
# ---------------------------------------------------------------------------

class TestStatusChangeRoles:
    """Promoting to ACTIVE requires approver or admin."""

    @pytest.mark.parametrize("headers_fixture,contract", [
        ("auth_headers",      "customer"),          # validator
        ("reader_headers",    "banking_transaction"),
        ("auditor_headers",   "hr_employee"),
        ("editor_headers",    "insurance_claim"),
    ])
    def test_activate_forbidden(self, request, client, headers_fixture, contract):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            f"/api/v1/contracts/{contract}/status",
            params={"status": "active"},
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from activating"


# ---------------------------------------------------------------------------
# Token management — admin only for bulk revoke
# ---------------------------------------------------------------------------

class TestTokenManagementRoles:
    """Revoking all tokens for a system requires admin role."""

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",    # validator
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
    ])
    def test_bulk_revoke_forbidden_for_non_admin(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/tokens/revoke/some-system",
            headers=headers,
        )
        assert r.status_code == 403, f"{headers_fixture} should be forbidden from bulk revoking tokens"


# ---------------------------------------------------------------------------
# Read-only operations — all roles permitted
# ---------------------------------------------------------------------------

class TestReadOperationsAllRoles:
    """Contract reads are public (no auth required), but authenticated roles can also read."""

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
        "admin_headers",
    ])
    def test_list_contracts_allowed(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.get("/api/v1/contracts", headers=headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
        "admin_headers",
    ])
    def test_get_contract_allowed(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.get("/api/v1/contracts/customer", headers=headers)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Import endpoints — editor/admin only
# ---------------------------------------------------------------------------

class TestImportRoles:
    """POST /import/* requires editor or admin."""

    _GX_SUITE = {
        "expectation_suite_name": "rbac_test",
        "expectations": [],
        "data_asset_type": None,
    }

    @pytest.mark.parametrize("headers_fixture", ["editor_headers", "admin_headers"])
    def test_import_gx_allowed(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post("/api/v1/import/gx", json=self._GX_SUITE, headers=headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers", "reader_headers", "auditor_headers", "approver_headers",
    ])
    def test_import_gx_forbidden(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post("/api/v1/import/gx", json=self._GX_SUITE, headers=headers)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Webhook endpoints — editor/admin only for register/delete
# ---------------------------------------------------------------------------

class TestWebhookRoles:
    """POST/DELETE /webhooks require editor or admin. GET /webhooks is open."""

    @pytest.mark.parametrize("headers_fixture", ["editor_headers", "admin_headers"])
    def test_register_webhook_allowed(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/hook"},
            headers=headers,
        )
        assert r.status_code in (200, 400)  # 400 if SSRF blocked, 200 if registered

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers", "reader_headers", "auditor_headers", "approver_headers",
    ])
    def test_register_webhook_forbidden(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/hook"},
            headers=headers,
        )
        assert r.status_code == 403

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers", "reader_headers", "auditor_headers", "approver_headers",
    ])
    def test_delete_webhook_forbidden(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.request(
            "DELETE",
            "/api/v1/webhooks",
            json={"url": "https://example.com/hook"},
            headers=headers,
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Contract reload — admin only
# ---------------------------------------------------------------------------

class TestReloadRoles:
    """POST /contracts/reload requires admin."""

    def test_reload_admin_allowed(self, client, admin_headers):
        r = client.post("/api/v1/contracts/reload", headers=admin_headers)
        assert r.status_code == 200

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers", "reader_headers", "auditor_headers",
    ])
    def test_reload_forbidden(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post("/api/v1/contracts/reload", headers=headers)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Token generation — admin only (C1 fix, RT148)
# ---------------------------------------------------------------------------

class TestTokenGenerateRoles:
    """POST /tokens/generate requires admin role in AUTH_MODE=token."""

    def test_generate_token_admin_allowed(self, client, admin_headers):
        r = client.post(
            "/api/v1/tokens/generate",
            params={"username": "rbac-test-system", "role": "validator"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.json()["role"] == "validator"

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",      # validator
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
    ])
    def test_generate_token_forbidden_for_non_admin(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/tokens/generate",
            params={"username": "rbac-test-system", "role": "validator"},
            headers=headers,
        )
        assert r.status_code == 403, (
            f"{headers_fixture} should be forbidden from generating tokens"
        )


# ---------------------------------------------------------------------------
# Token revocation — admin only (L2 fix, RT148)
# ---------------------------------------------------------------------------

class TestTokenRevokeRoles:
    """POST /tokens/revoke requires admin role in AUTH_MODE=token."""

    def test_revoke_token_admin_allowed(self, client, admin_headers):
        pat = client.post(
            "/api/v1/tokens/generate",
            params={"username": "rbac-revoke-test", "role": "validator"},
            headers=admin_headers,
        ).json()["pat"]
        r = client.post(
            "/api/v1/tokens/revoke",
            content=pat,
            headers={"Content-Type": "text/plain", **admin_headers},
        )
        assert r.status_code == 200

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
    ])
    def test_revoke_token_forbidden_for_non_admin(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.post(
            "/api/v1/tokens/revoke",
            content="sometoken",
            headers={"Content-Type": "text/plain", **headers},
        )
        assert r.status_code == 403, (
            f"{headers_fixture} should be forbidden from revoking tokens"
        )


# ---------------------------------------------------------------------------
# Token listing — admin only (N3 fix, RT149)
# ---------------------------------------------------------------------------

class TestTokenListRoles:
    """GET /tokens requires admin role in AUTH_MODE=token."""

    def test_list_tokens_admin_allowed(self, client, admin_headers):
        r = client.get("/api/v1/tokens", headers=admin_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.parametrize("headers_fixture", [
        "auth_headers",      # validator
        "reader_headers",
        "auditor_headers",
        "editor_headers",
        "approver_headers",
    ])
    def test_list_tokens_forbidden_for_non_admin(self, request, client, headers_fixture):
        headers = request.getfixturevalue(headers_fixture)
        r = client.get("/api/v1/tokens", headers=headers)
        assert r.status_code == 403, (
            f"{headers_fixture} should be forbidden from listing tokens"
        )
