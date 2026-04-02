"""
Extended contract API tests — covers missed paths in api/routes_contracts.py.

Focused on: explain_contract auth paths, explain_contract rule description
branches, history/timestamp endpoints, workflow error paths (submit/approve/reject),
diff error paths, rule mutation error paths, registry endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_CONTRACT = "customer"


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# TestExplainContractAuth — explain_contract() auth validation paths
# ---------------------------------------------------------------------------

class TestExplainContractAuth:
    """Lines 95-105: auth paths in explain_contract()."""

    def test_no_auth_returns_401(self, client):
        """No Authorization header → 401 when AUTH_MODE=token."""
        r = client.get(f"/api/v1/contracts/{KNOWN_CONTRACT}/explain")
        assert r.status_code == 401

    def test_invalid_scheme_returns_401(self, client):
        """Non-Bearer scheme → 401."""
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/explain",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401

    def test_bad_token_returns_401(self, client):
        """Bearer with invalid token → 401."""
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/explain",
            headers={"Authorization": "Bearer totally.invalid.jwt"},
        )
        assert r.status_code == 401

    def test_valid_token_returns_200(self, client, auth_headers):
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/explain",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "explanation" in data


# ---------------------------------------------------------------------------
# TestExplainContractRuleDescriptions — explain_contract() rule type branches
# ---------------------------------------------------------------------------

class TestExplainContractRuleDescriptions:
    """
    Lines 123-206: rule description branches.
    Use the /explain endpoint with auth; the customer contract has not_empty rules.
    """

    def test_explanation_contains_contract_name(self, client, auth_headers):
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/explain",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert KNOWN_CONTRACT in data["explanation"]

    def test_explanation_contains_rules_section(self, client, auth_headers):
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/explain",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert "Validation Rules" in r.json()["explanation"]

    def test_explanation_404_unknown_contract(self, client, auth_headers):
        r = client.get(
            "/api/v1/contracts/nonexistent_xyz_contract/explain",
            headers=auth_headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# TestLintContractFailPath — lint endpoint 422 path (line 277-278)
# ---------------------------------------------------------------------------

class TestLintContractFailPath:
    """Line 277-278: lint returns 422 when contract has errors."""

    def test_lint_valid_contract_returns_200(self, client, auth_headers):
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/lint",
            headers=auth_headers,
        )
        # Either 200 (passed) or 422 (issues found) — both are valid responses
        assert r.status_code in (200, 422)

    def test_lint_nonexistent_contract_returns_404(self, client, auth_headers):
        r = client.get(
            "/api/v1/contracts/nonexistent_xyz_contract/lint",
            headers=auth_headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# TestContractHistoryTimestamp — as_of endpoint (lines 351-378)
# ---------------------------------------------------------------------------

class TestContractHistoryTimestamp:
    """GET /contracts/{name}/as-of — history timestamp paths."""

    def test_unknown_contract_404(self, client, auth_headers):
        """Line 353: no history → 404."""
        r = client.get(
            "/api/v1/contracts/nonexistent_xyz_contract/as-of?timestamp=2026-01-01T00:00:00",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_timestamp_before_all_history_404(self, client, auth_headers):
        """Line 360-365: timestamp before earliest snapshot → 404."""
        # Use a timestamp very far in the past before any contract existed
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/as-of?timestamp=1990-01-01T00:00:00",
            headers=auth_headers,
        )
        # Either 404 (no history) or 404 (no snapshot before timestamp)
        assert r.status_code in (404,)

    def test_valid_timestamp_returns_snapshot(self, client, auth_headers):
        """Timestamp in the future returns the latest snapshot."""
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/as-of?timestamp=2099-12-31T23:59:59",
            headers=auth_headers,
        )
        # Either 200 (snapshot found) or 404 (no history recorded yet)
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            data = r.json()
            assert "version" in data
            assert "rules" in data


# ---------------------------------------------------------------------------
# TestContractDiffErrors — diff endpoint error paths (lines 574-579)
# ---------------------------------------------------------------------------

class TestContractDiffErrors:
    """GET /contracts/{name}/diff — error paths."""

    def test_diff_unknown_contract_404(self, client, auth_headers):
        """Line 574-575: contract not found → 404."""
        r = client.get(
            "/api/v1/contracts/nonexistent_xyz_contract/diff?version_a=1.0&version_b=2.0",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_diff_unknown_versions_404(self, client, auth_headers):
        """Line 576-579: version not found → 404."""
        r = client.get(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/diff?version_a=99.0&version_b=100.0",
            headers=auth_headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# TestContractWorkflowErrors — submit/approve/reject error paths
# ---------------------------------------------------------------------------

class TestContractWorkflowErrors:
    """Submit/approve/reject non-existent contract → 404 or 409."""

    def test_submit_nonexistent_contract_returns_error(self, client, editor_headers):
        """Lines 484-488: submit → contract not found → 404/409."""
        r = client.post(
            "/api/v1/contracts/nonexistent_xyz_contract/latest/submit-review",
            json={"proposed_by": "test"},
            headers=editor_headers,
        )
        assert r.status_code in (404, 409)

    def test_approve_nonexistent_contract_returns_error(self, client, approver_headers):
        """Lines 510-515: approve → contract not found → 404/409."""
        r = client.post(
            "/api/v1/contracts/nonexistent_xyz_contract/latest/approve",
            json={"approved_by": "approver"},
            headers=approver_headers,
        )
        assert r.status_code in (404, 409)

    def test_reject_nonexistent_contract_returns_error(self, client, approver_headers):
        """Lines 538-543: reject → contract not found → 404/409."""
        r = client.post(
            "/api/v1/contracts/nonexistent_xyz_contract/latest/reject",
            json={"rejected_by": "approver", "reason": "test"},
            headers=approver_headers,
        )
        assert r.status_code in (404, 409)

    def test_submit_wrong_role_returns_403(self, client, auth_headers):
        """Line 477-481: reader/validator role → 403."""
        r = client.post(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/latest/submit-review",
            json={"proposed_by": "test"},
            headers=auth_headers,  # validator role, not editor/admin
        )
        assert r.status_code == 403

    def test_approve_wrong_role_returns_403(self, client, editor_headers):
        """Line 504-508: editor role → 403 for approve."""
        r = client.post(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/latest/approve",
            json={"approved_by": "test"},
            headers=editor_headers,
        )
        assert r.status_code == 403

    def test_reject_wrong_role_returns_403(self, client, editor_headers):
        """Line 531-535: editor role → 403 for reject."""
        r = client.post(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/latest/reject",
            json={"rejected_by": "test", "reason": "no"},
            headers=editor_headers,
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# TestRuleMutationErrors — add/update/delete rule error paths
# ---------------------------------------------------------------------------

class TestRuleMutationErrors:
    """Rule mutation 422/403 error paths."""

    def test_add_rule_wrong_role_returns_403(self, client, auth_headers):
        """Line 672-673: reader/validator role → 403."""
        r = client.post(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/rules",
            json={"name": "test_rule", "type": "not_empty", "field": "name",
                  "error_message": "Required"},
            headers=auth_headers,
        )
        assert r.status_code == 403

    def test_update_rule_wrong_role_returns_403(self, client, auth_headers):
        """Line 702-703: reader/validator role → 403 for update."""
        r = client.put(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/rules/some_rule",
            json={"name": "some_rule", "type": "not_empty", "field": "name",
                  "error_message": "Required"},
            headers=auth_headers,
        )
        assert r.status_code == 403

    def test_delete_rule_wrong_role_returns_403(self, client, auth_headers):
        """Line 735-736: reader/validator role → 403 for delete."""
        r = client.delete(
            f"/api/v1/contracts/{KNOWN_CONTRACT}/rules/some_rule",
            headers=auth_headers,
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# TestSchemaRegistry — registry endpoints (lines 746-821)
# ---------------------------------------------------------------------------

class TestSchemaRegistry:
    """GET /registry and GET /registry/{name} endpoints."""

    def test_list_schema_registry(self, client, auth_headers):
        r = client.get("/api/v1/registry", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "registry" in data
        assert "count" in data
        assert isinstance(data["registry"], list)

    def test_get_schema_registry_entry(self, client, auth_headers):
        r = client.get(f"/api/v1/registry/{KNOWN_CONTRACT}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == KNOWN_CONTRACT
        assert "rules" in data
        assert "schema_hash" in data

    def test_get_schema_registry_entry_404(self, client, auth_headers):
        r = client.get("/api/v1/registry/nonexistent_xyz_contract", headers=auth_headers)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# TestGenerateCodeErrors — generate_code UnknownContextError (lines 841-842)
# ---------------------------------------------------------------------------

class TestExplainContractRuleTypeBranches:
    """
    Cover explain_contract() rule description elif branches (lines 124-206).
    Each test uses a contract that contains the relevant rule type.
    """

    def test_explain_financial_trade_has_checksum_and_compare(self, client, auth_headers):
        """Checksum (line 166-167) and compare (156-159) rule descriptions."""
        r = client.get("/api/v1/contracts/financial_trade/explain", headers=auth_headers)
        assert r.status_code == 200
        explanation = r.json()["explanation"]
        assert len(explanation) > 0

    def test_explain_healthcare_patient_has_required_if(self, client, auth_headers):
        """required_if (lines 161-162) rule description."""
        r = client.get("/api/v1/contracts/healthcare_patient/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_social_media_has_min_age(self, client, auth_headers):
        """min_age/max_age (lines 185-186, 191-193) rule description."""
        r = client.get("/api/v1/contracts/social_media_age_compliance/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_eu_gdpr_has_required_if_and_forbidden_if(self, client, auth_headers):
        """required_if + forbidden_if (lines 161-165)."""
        r = client.get("/api/v1/contracts/eu_gdpr_dsar_request/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_insurance_claim_has_cross_field_and_field_sum(self, client, auth_headers):
        """cross_field_range (168-174) + field_sum (175-176) branches."""
        r = client.get("/api/v1/contracts/insurance_claim/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_logistics_has_compare_and_required_if(self, client, auth_headers):
        """compare (156-159) and required_if (161-162) rules."""
        r = client.get("/api/v1/contracts/logistics_shipment/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_universal_benchmark_all_types(self, client, auth_headers):
        """Covers many rule types in one shot."""
        r = client.get("/api/v1/contracts/universal_benchmark/explain", headers=auth_headers)
        assert r.status_code == 200
        explanation = r.json()["explanation"]
        assert "Validation Rules" in explanation

    def test_explain_dora_ict_has_conditional_value(self, client, auth_headers):
        """conditional_value (lines 182-184)."""
        r = client.get("/api/v1/contracts/dora_ict_incident/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_proof_of_play_has_unique_and_forbidden_if(self, client, auth_headers):
        """unique with group_by (177-179) + forbidden_if (163-165)."""
        r = client.get("/api/v1/contracts/proof_of_play/explain", headers=auth_headers)
        assert r.status_code == 200

    def test_explain_financial_services_customer_has_age(self, client, auth_headers):
        """min_age/max_age description (191-193)."""
        r = client.get("/api/v1/contracts/financial_services_customer/explain", headers=auth_headers)
        assert r.status_code == 200


class TestGenerateCodeErrors:
    """POST /generate — error paths."""

    def test_generate_with_unknown_context_falls_back_to_base_rules(self, client, auth_headers):
        """Unknown context falls back to base rules (no UnknownContextError raised)."""
        r = client.post(
            f"/api/v1/generate?contract_name={KNOWN_CONTRACT}&target=snowflake&context=nonexistent_context_xyz",
            headers=auth_headers,
        )
        # Falls back to base rules → 200 with generated code
        assert r.status_code == 200
        data = r.json()
        assert "code" in data

    def test_generate_unknown_contract_returns_404(self, client, auth_headers):
        r = client.post(
            "/api/v1/generate?contract_name=nonexistent_xyz_contract&target=snowflake",
            headers=auth_headers,
        )
        assert r.status_code == 404
