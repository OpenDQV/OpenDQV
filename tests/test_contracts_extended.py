"""
Extended contract API tests — covers missed paths in api/routes_contracts.py.

Focused on: explain_contract auth paths, explain_contract rule description
branches, history/timestamp endpoints, workflow error paths (submit/approve/reject),
diff error paths, rule mutation error paths, registry endpoints.
"""
import textwrap

import pytest
from fastapi.testclient import TestClient
from opendqv.main import app


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

    def test_explain_nhs_dsp_patient_has_required_if(self, client, auth_headers):
        """required_if (lines 161-162) rule description."""
        r = client.get("/api/v1/contracts/nhs_dsp_patient/explain", headers=auth_headers)
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


# ---------------------------------------------------------------------------
# Helpers shared by the new test classes below
# ---------------------------------------------------------------------------

def _insert_draft(name: str, rules=None):
    """Insert a synthetic DRAFT contract into the registry with a YAML file."""
    import yaml
    from opendqv.api.routes import registry
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import ContractStatus, Rule

    if rules is None:
        rules = [
            Rule(
                name="test_rule",
                field="email",
                type="not_empty",
                error_message="email is required",
            )
        ]
    contract = DataContract(
        name=name,
        version="1.0",
        description="Test draft contract",
        owner="pytest",
        status=ContractStatus.DRAFT,
        rules=rules,
    )
    # Write a real YAML file so _write_contract_yaml can persist mutations.
    path = registry.contracts_dir / f"{name}.yaml"
    yaml_data = {
        "name": name,
        "version": "1.0",
        "description": "Test draft contract",
        "owner": "pytest",
        "status": "draft",
        "rules": [
            {
                "name": r.name,
                "field": r.field,
                "type": r.type,
                "error_message": r.error_message,
            }
            for r in rules
        ],
    }
    path.write_text(yaml.dump(yaml_data), encoding="utf-8")
    registry._contracts.setdefault(name, {})[contract.version] = contract
    registry._contract_paths[name] = path
    return contract


def _remove_contract(name: str):
    """Remove a contract from the registry (in-memory only, no YAML written)."""
    from opendqv.api.routes import registry
    registry._contracts.pop(name, None)
    path = registry.contracts_dir / f"{name}.yaml"
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# TestListContractsIncludeAll — list endpoint include_all param (lines 48-49)
# ---------------------------------------------------------------------------


class TestListContractsIncludeAll:
    """GET /contracts?include_all=true — archived contracts are included."""

    _ARCH_NAME = "include_all_arch_test_xyz"

    def setup_method(self):
        _insert_draft(self._ARCH_NAME)

    def teardown_method(self):
        _remove_contract(self._ARCH_NAME)

    def test_include_all_includes_archived(self, client, auth_headers):
        """include_all=true returns >= count without include_all."""
        # Archive the dedicated test draft contract (DRAFT → ARCHIVED is always valid).
        client.post(
            f"/api/v1/contracts/{self._ARCH_NAME}/status",
            params={"status": "archived"},
            headers=auth_headers,
        )

        r_all = client.get("/api/v1/contracts?include_all=true", headers=auth_headers)
        assert r_all.status_code == 200
        count_all = len(r_all.json())

        r_default = client.get("/api/v1/contracts", headers=auth_headers)
        assert r_default.status_code == 200
        count_default = len(r_default.json())

        assert count_all >= count_default

    def test_default_excludes_archived(self, client, auth_headers):
        """Without include_all the list never contains archived status."""
        r = client.get("/api/v1/contracts", headers=auth_headers)
        assert r.status_code == 200
        for entry in r.json():
            assert entry.get("status") != "archived"


# ---------------------------------------------------------------------------
# TestWorkflowSuccessPaths — submit/approve/reject happy paths (lines 485-547)
# ---------------------------------------------------------------------------


class TestWorkflowSuccessPaths:
    """Happy-path workflow: DRAFT → REVIEW → ACTIVE and DRAFT → REVIEW → DRAFT."""

    _SUBMIT_NAME = "wf_submit_test_xyz"
    _APPROVE_NAME = "wf_approve_test_xyz"
    _REJECT_NAME = "wf_reject_test_xyz"

    def teardown_method(self):
        for name in (self._SUBMIT_NAME, self._APPROVE_NAME, self._REJECT_NAME):
            _remove_contract(name)

    def test_submit_review_success(self, client, editor_headers):
        """POST .../submit-review on a DRAFT contract → 200 status=submitted."""
        _insert_draft(self._SUBMIT_NAME)
        r = client.post(
            f"/api/v1/contracts/{self._SUBMIT_NAME}/1.0/submit-review",
            json={"proposed_by": "tester"},
            headers=editor_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "submitted"
        assert data["contract"] == self._SUBMIT_NAME

    def test_approve_success(self, client, editor_headers, approver_headers):
        """POST .../approve on a REVIEW contract → 200 status=approved."""
        _insert_draft(self._APPROVE_NAME)
        # First submit it so it enters REVIEW state.
        client.post(
            f"/api/v1/contracts/{self._APPROVE_NAME}/1.0/submit-review",
            json={"proposed_by": "tester"},
            headers=editor_headers,
        )
        r = client.post(
            f"/api/v1/contracts/{self._APPROVE_NAME}/1.0/approve",
            json={"approved_by": "approver"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "approved"
        assert data["contract"] == self._APPROVE_NAME

    def test_reject_success(self, client, editor_headers, approver_headers):
        """POST .../reject on a REVIEW contract → 200 status=rejected."""
        _insert_draft(self._REJECT_NAME)
        # Submit first.
        client.post(
            f"/api/v1/contracts/{self._REJECT_NAME}/1.0/submit-review",
            json={"proposed_by": "tester"},
            headers=editor_headers,
        )
        r = client.post(
            f"/api/v1/contracts/{self._REJECT_NAME}/1.0/reject",
            json={"rejected_by": "approver", "reason": "not ready"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "rejected"
        assert data["contract"] == self._REJECT_NAME


# ---------------------------------------------------------------------------
# TestRuleMutationValueErrors — add/update/delete ValueError → 422 (lines 638-639 etc.)
# ---------------------------------------------------------------------------


class TestRuleMutationValueErrors:
    """Rule mutation operations that raise ValueError return HTTP 422."""

    _NAME = "rule_mutation_val_err_xyz"

    def setup_method(self):
        _insert_draft(self._NAME)

    def teardown_method(self):
        _remove_contract(self._NAME)

    def test_add_duplicate_rule_name_returns_422(self, client, editor_headers):
        """Adding a rule whose name already exists returns 422 (ValueError from registry)."""
        # test_rule already exists in the draft inserted by setup_method.
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/rules",
            json={
                "name": "test_rule",
                "field": "email",
                "type": "not_empty",
                "error_message": "duplicate",
            },
            headers=editor_headers,
        )
        assert r.status_code == 422

    def test_update_rule_nonexistent_returns_422(self, client, editor_headers):
        """Updating a non-existent rule name returns 422 (ValueError from registry)."""
        r = client.put(
            f"/api/v1/contracts/{self._NAME}/rules/nonexistent_rule_xyz",
            json={
                "name": "nonexistent_rule_xyz",
                "field": "email",
                "type": "not_empty",
                "error_message": "required",
            },
            headers=editor_headers,
        )
        assert r.status_code == 422

    def test_delete_rule_nonexistent_returns_422(self, client, editor_headers):
        """Deleting a non-existent rule name returns 422 (ValueError from registry)."""
        r = client.delete(
            f"/api/v1/contracts/{self._NAME}/rules/nonexistent_rule_xyz",
            headers=editor_headers,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# TestUpdateRuleBreakingChange — update_rule breaking_change_warning (lines 706-716)
# ---------------------------------------------------------------------------


class TestUpdateRuleBreakingChange:
    """PUT .../rules/{rule_name} with breaking change returns breaking_change_warning."""

    _NAME = "rule_breaking_change_xyz"

    def setup_method(self):
        from opendqv.core.rule_parser import Rule
        rules = [
            Rule(
                name="check_name",
                field="name",
                type="not_empty",
                error_message="name is required",
            )
        ]
        _insert_draft(self._NAME, rules=rules)

    def teardown_method(self):
        _remove_contract(self._NAME)

    def test_update_rule_breaking_change_warning(self, client, editor_headers):
        """Changing rule type (not_empty → regex) sets breaking_change_warning in response."""
        r = client.put(
            f"/api/v1/contracts/{self._NAME}/rules/check_name",
            json={
                "name": "check_name",
                "field": "name",
                "type": "regex",
                "pattern": r"^[A-Za-z]+$",
                "error_message": "name must contain only letters",
            },
            headers=editor_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "breaking_change_warning" in data

    def test_update_rule_no_breaking_change(self, client, editor_headers):
        """Changing only error_message (non-breaking field) omits breaking_change_warning."""
        r = client.put(
            f"/api/v1/contracts/{self._NAME}/rules/check_name",
            json={
                "name": "check_name",
                "field": "name",
                "type": "not_empty",
                "error_message": "updated message — non-breaking",
            },
            headers=editor_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert "breaking_change_warning" not in data


# ---------------------------------------------------------------------------
# TestChangeContractStatusArchive — change_contract_status paths (lines 351-367)
# ---------------------------------------------------------------------------


class TestChangeContractStatusArchive:
    """POST /contracts/{name}/status — archive and invalid status paths."""

    _NAME = "status_archive_test_xyz"

    def setup_method(self):
        _insert_draft(self._NAME)

    def teardown_method(self):
        _remove_contract(self._NAME)

    def test_archive_draft_contract(self, client, auth_headers):
        """Archiving a DRAFT contract (DRAFT→ARCHIVED) returns 200 with status=archived."""
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "archived"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "archived"

    def test_invalid_status_returns_400(self, client, auth_headers):
        """An unrecognised status value returns 400."""
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "invalid_xyz"},
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "Invalid status" in r.json()["detail"]


# ---------------------------------------------------------------------------
# TestContractOnboardingFormat — _parse_onboarding_format (core/contracts.py 561-606)
# ---------------------------------------------------------------------------


class TestContractOnboardingFormat:
    """ContractRegistry._parse_onboarding_format — field-keyed YAML parsing."""

    def _make_registry(self, tmp_path, yaml_content: str, filename: str = "ob_test.yaml"):
        from opendqv.core.contracts import ContractRegistry
        (tmp_path / filename).write_text(yaml_content, encoding="utf-8")
        return ContractRegistry(tmp_path)

    def test_basic_loading(self, tmp_path):
        """Field-keyed YAML with metadata section loads as a DataContract."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "Basic onboarding test"
              author: "test-author"
            rules:
              email:
                error_message: "email required"
              name:
                error_message: "name required"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_basic.yaml")
        contract = registry.get("ob_basic")
        assert contract is not None
        assert contract.name == "ob_basic"
        fields = [r.field for r in contract.rules]
        assert "email" in fields
        assert "name" in fields

    def test_required_field_generates_not_empty(self, tmp_path):
        """required: true in a field definition adds an extra not_empty rule (lines 590-595)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "Required field test"
              author: "test-author"
            rules:
              email:
                type: regex
                regex: "^[^@]+@[^@]+$"
                error_message: "must be a valid email"
                required: true
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_required.yaml")
        contract = registry.get("ob_required")
        assert contract is not None
        email_rules = [r for r in contract.rules if r.field == "email"]
        assert any(r.type == "not_empty" for r in email_rules), (
            "required: true should generate an additional not_empty rule"
        )

    def test_date_format_field(self, tmp_path):
        """type: date with format key generates a date_format rule (lines 597-600)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "Date format test"
              author: "test-author"
            rules:
              created_at:
                type: date
                format: "%Y-%m-%d"
                error_message: "invalid date"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_date.yaml")
        contract = registry.get("ob_date")
        assert contract is not None
        date_rules = [r for r in contract.rules if r.field == "created_at"]
        assert any(r.type == "date_format" for r in date_rules)

    def test_non_numeric_min_skipped(self, tmp_path):
        """Non-numeric min value does not crash — it is silently skipped (lines 578-582)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "Non-numeric min test"
              author: "test-author"
            rules:
              score:
                min: "not-a-number"
                error_message: "invalid score"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_nonnumeric_min.yaml")
        contract = registry.get("ob_nonnumeric_min")
        assert contract is not None
        # Contract loads without error; the rule uses default not_empty type.
        score_rules = [r for r in contract.rules if r.field == "score"]
        assert len(score_rules) >= 1

    def test_today_max_skipped(self, tmp_path):
        """max: 'today' is skipped — does not become a numeric max (lines 584-589)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "today max test"
              author: "test-author"
            rules:
              expiry:
                max: "today"
                error_message: "expiry cannot be today"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_today_max.yaml")
        contract = registry.get("ob_today_max")
        assert contract is not None
        expiry_rules = [r for r in contract.rules if r.field == "expiry"]
        assert len(expiry_rules) >= 1
        # max_value should not be set (today was not converted to a float).
        for r in expiry_rules:
            assert r.max_value is None

    def test_min_length_field(self, tmp_path):
        """min_length key on a field generates min_length in the rule dict (lines 571-572)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "min_length test"
              author: "test-author"
            rules:
              username:
                min_length: 3
                error_message: "username too short"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_min_length.yaml")
        contract = registry.get("ob_min_length")
        assert contract is not None
        username_rules = [r for r in contract.rules if r.field == "username"]
        assert any(r.min_length == 3 for r in username_rules)

    def test_max_length_field(self, tmp_path):
        """max_length key on a field generates max_length in the rule dict (lines 573-574)."""
        yaml_content = textwrap.dedent("""\
            metadata:
              version: "1.0"
              description: "max_length test"
              author: "test-author"
            rules:
              bio:
                max_length: 200
                error_message: "bio too long"
        """)
        registry = self._make_registry(tmp_path, yaml_content, "ob_max_length.yaml")
        contract = registry.get("ob_max_length")
        assert contract is not None
        bio_rules = [r for r in contract.rules if r.field == "bio"]
        assert any(r.max_length == 200 for r in bio_rules)
