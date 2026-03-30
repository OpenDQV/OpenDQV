"""Tests for contract lifecycle (DRAFT -> ACTIVE -> ARCHIVED)."""

import pytest
from api.routes import registry
from core.rule_parser import ContractStatus
from core.contracts import DataContract


class TestContractStatus:
    """Test that contract status is exposed in API responses."""

    def test_list_contracts_has_status(self, client):
        r = client.get("/api/v1/contracts")
        assert r.status_code == 200
        for c in r.json():
            assert "status" in c

    def test_contract_detail_has_status(self, client):
        r = client.get("/api/v1/contracts/customer")
        assert r.status_code == 200
        assert r.json()["status"] == "active"


class TestStatusChange:
    """Test changing contract lifecycle status."""

    def test_change_to_draft(self, client, auth_headers, approver_headers):
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "draft"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "draft"

        # Verify it shows as draft in detail
        r = client.get("/api/v1/contracts/customer")
        assert r.json()["status"] == "draft"

        # Reset — activating requires approver role
        client.post("/api/v1/contracts/customer/status", params={"status": "active"}, headers=approver_headers)

    def test_invalid_status(self, client, auth_headers):
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "invalid_status"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_status_change_requires_auth(self, client):
        r = client.post("/api/v1/contracts/customer/status", params={"status": "draft"})
        assert r.status_code == 401

    def test_status_change_not_found(self, client, approver_headers):
        r = client.post("/api/v1/contracts/nonexistent/status", params={"status": "active"}, headers=approver_headers)
        assert r.status_code == 404

    def test_validator_cannot_activate(self, client, auth_headers):
        """Maker-checker: activating a contract requires approver or admin role."""
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "active"},
            headers=auth_headers,
        )
        assert r.status_code == 403


class TestDraftBlocking:
    """Test that DRAFT contracts block production validation."""

    def test_draft_blocks_validate(self, client, auth_headers, approver_headers):
        # Set to draft
        client.post("/api/v1/contracts/customer/status", params={"status": "draft"}, headers=auth_headers)

        body = {"record": {"email": "test@example.com"}, "contract": "customer"}
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 422
        assert "draft" in r.json()["detail"].lower()

        # Reset — activating requires approver role
        client.post("/api/v1/contracts/customer/status", params={"status": "active"}, headers=approver_headers)

    def test_draft_allowed_with_flag(self, client, auth_headers, approver_headers):
        client.post("/api/v1/contracts/customer/status", params={"status": "draft"}, headers=auth_headers)

        body = {
            "record": {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers, params={"allow_draft": "true"})
        assert r.status_code == 200
        assert r.json()["valid"] is True

        # Reset — activating requires approver role
        client.post("/api/v1/contracts/customer/status", params={"status": "active"}, headers=approver_headers)


class TestArchivedFilter:
    """Test that archived contracts are filtered from default listing."""

    def test_archived_hidden_in_list(self):
        """Test via registry directly to avoid rate limit issues."""
        from api.routes import registry

        # Save original, set to archived
        contract = registry.get("customer")
        original_status = contract.status
        registry.set_status("customer", "latest", ContractStatus.ARCHIVED)

        # Default list hides archived
        contracts = registry.list_contracts(include_all=False)
        names = [c["name"] for c in contracts]
        assert "customer" not in names

        # include_all shows them
        contracts = registry.list_contracts(include_all=True)
        names = [c["name"] for c in contracts]
        assert "customer" in names

        # Reset: ARCHIVED → DRAFT → original_status (direct ARCHIVED → ACTIVE is blocked by
        # transition map; archived contracts must re-enter the lifecycle via draft).
        registry.set_status("customer", "latest", ContractStatus.DRAFT)
        if original_status != ContractStatus.DRAFT:
            registry.set_status("customer", "latest", original_status)


class TestActiveContractImmutability:
    """ACT-047-01: ACTIVE contracts are immutable — rule mutations return 409 for all callers."""

    _RULE = {
        "name": "act047_immutability_test_rule",
        "field": "act047_field",
        "type": "not_empty",
        "severity": "warning",
        "error_message": "act047 immutability test",
    }

    def _get_active_contract(self, client, headers):
        r = client.get("/api/v1/contracts", headers=headers)
        active = [c for c in r.json() if c["status"] == "active"]
        return active[0]["name"] if active else None

    def test_add_rule_blocked_on_active_validator(self, client, auth_headers):
        """Validator cannot add a rule to any contract — role check returns 403."""
        name = self._get_active_contract(client, auth_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.post(f"/api/v1/contracts/{name}/rules", json=self._RULE, headers=auth_headers)
        assert r.status_code == 403

    def test_add_rule_blocked_on_active_editor(self, client, editor_headers):
        """Editor is blocked from adding a rule to an ACTIVE contract — 409."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.post(f"/api/v1/contracts/{name}/rules", json=self._RULE, headers=editor_headers)
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_update_rule_blocked_on_active(self, client, editor_headers):
        """update_rule returns 409 on ACTIVE contracts for editors."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.get(f"/api/v1/contracts/{name}", headers=editor_headers)
        rules = r.json().get("rules", [])
        if not rules:
            pytest.skip("Contract has no rules to update")
        rule = rules[0]
        payload = {
            "name": rule["name"],
            "field": rule["field"],
            "type": rule["type"],
            "severity": rule["severity"],
            "error_message": rule.get("error_message", ""),
        }
        r = client.put(f"/api/v1/contracts/{name}/rules/{rule['name']}", json=payload, headers=editor_headers)
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_delete_rule_blocked_on_active(self, client, editor_headers):
        """delete_rule returns 409 on ACTIVE contracts for editors."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.get(f"/api/v1/contracts/{name}", headers=editor_headers)
        rules = r.json().get("rules", [])
        if not rules:
            pytest.skip("Contract has no rules to delete")
        rule_name = rules[0]["name"]
        r = client.delete(f"/api/v1/contracts/{name}/rules/{rule_name}", headers=editor_headers)
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_409_detail_includes_remediation_path(self, client, editor_headers):
        """The 409 response body must name the bump_contract_version endpoint as the fix."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.post(f"/api/v1/contracts/{name}/rules", json=self._RULE, headers=editor_headers)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert f"/contracts/{name}/version" in detail


class TestRuleMutationOnDraft:
    """ACT-047-01/02: Rule mutations on DRAFT contracts succeed and auto-increment the patch counter."""

    _RULE = {
        "name": "act047_draft_test_rule",
        "field": "act047_draft_field",
        "type": "not_empty",
        "severity": "warning",
        "error_message": "act047 draft test",
    }

    def test_editor_can_add_rule_to_draft_contract(self, client, auth_headers, editor_headers, approver_headers):
        """An editor may add rules to a DRAFT contract."""
        r = client.get("/api/v1/contracts", headers=auth_headers)
        active = [c for c in r.json() if c["status"] == "active"]
        if not active:
            pytest.skip("No active contracts available")
        contract_name = active[0]["name"]

        client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "draft"}, headers=auth_headers)
        try:
            r = client.post(f"/api/v1/contracts/{contract_name}/rules", json=self._RULE, headers=editor_headers)
            assert r.status_code == 200
            assert r.json()["status"] == "added"
            # Draft patch counter must appear in the version
            assert "-draft." in r.json()["version"]
            # Clean up rule
            client.delete(f"/api/v1/contracts/{contract_name}/rules/{self._RULE['name']}", headers=editor_headers)
        finally:
            client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "active"}, headers=approver_headers)

    def test_draft_patch_counter_increments_on_successive_mutations(self, client, auth_headers, editor_headers, approver_headers):
        """Each rule mutation on a DRAFT contract increments the patch counter."""
        r = client.get("/api/v1/contracts", headers=auth_headers)
        active = [c for c in r.json() if c["status"] == "active"]
        if not active:
            pytest.skip("No active contracts available")
        contract_name = active[0]["name"]

        client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "draft"}, headers=auth_headers)
        try:
            rule_a = dict(self._RULE, name="act047_counter_rule_a")
            rule_b = dict(self._RULE, name="act047_counter_rule_b")

            r1 = client.post(f"/api/v1/contracts/{contract_name}/rules", json=rule_a, headers=editor_headers)
            assert r1.status_code == 200
            version_after_first = r1.json()["version"]
            assert "-draft." in version_after_first

            r2 = client.post(f"/api/v1/contracts/{contract_name}/rules", json=rule_b, headers=editor_headers)
            assert r2.status_code == 200
            version_after_second = r2.json()["version"]

            # Second counter must be strictly greater
            n1 = int(version_after_first.split("-draft.")[1])
            n2 = int(version_after_second.split("-draft.")[1])
            assert n2 == n1 + 1, f"Counter did not increment: {version_after_first} → {version_after_second}"

            # Clean up
            client.delete(f"/api/v1/contracts/{contract_name}/rules/act047_counter_rule_a", headers=editor_headers)
            client.delete(f"/api/v1/contracts/{contract_name}/rules/act047_counter_rule_b", headers=editor_headers)
        finally:
            client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "active"}, headers=approver_headers)

    def test_delete_rule_on_draft_increments_counter(self, client, auth_headers, editor_headers, approver_headers):
        """delete_rule on a DRAFT contract also increments the patch counter."""
        r = client.get("/api/v1/contracts", headers=auth_headers)
        active = [c for c in r.json() if c["status"] == "active"]
        if not active:
            pytest.skip("No active contracts available")
        contract_name = active[0]["name"]

        client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "draft"}, headers=auth_headers)
        try:
            # Add a rule first so we have something to delete
            r = client.post(f"/api/v1/contracts/{contract_name}/rules", json=self._RULE, headers=editor_headers)
            assert r.status_code == 200
            version_after_add = r.json()["version"]

            r = client.delete(f"/api/v1/contracts/{contract_name}/rules/{self._RULE['name']}", headers=editor_headers)
            assert r.status_code == 200
            version_after_delete = r.json()["version"]

            n_add = int(version_after_add.split("-draft.")[1])
            n_del = int(version_after_delete.split("-draft.")[1])
            assert n_del == n_add + 1
        finally:
            client.post(f"/api/v1/contracts/{contract_name}/status", params={"status": "active"}, headers=approver_headers)


class TestDraftFallback:
    """ACT-037-08: Draft-fallback path with STRICT_DRAFT_VALIDATION=True."""

    def test_draft_fallback_with_snapshot(self, client, auth_headers, monkeypatch):
        """
        Set STRICT_DRAFT_VALIDATION=True. Set a contract to draft (which captures
        last_active_snapshot). Validate — must return 200 with X-Contract-Status: draft-fallback.

        Uses registry directly for status changes to avoid the 10/minute rate-limit on
        the status HTTP endpoint (which can be exhausted by prior lifecycle tests).
        """
        import config
        from api.routes import registry
        from core.rule_parser import ContractStatus

        monkeypatch.setattr(config, "STRICT_DRAFT_VALIDATION", True)

        # Find a contract that is available in the registry
        available = [c for c in registry.list_contracts(include_all=True) if c["status"] == "active"]
        if not available:
            pytest.skip("No active contracts available for test")
        contract_name = available[0]["name"]
        contract = registry.get(contract_name)
        original_status = contract.status

        # Set to draft directly — this should capture a snapshot
        registry.set_status(contract_name, "latest", ContractStatus.DRAFT)

        try:
            # Validate without allow_draft — should get 200 with draft-fallback header
            r = client.post(
                "/api/v1/validate",
                json={"record": {}, "contract": contract_name},
                headers=auth_headers,
            )
            assert r.status_code == 200, f"Expected 200 in draft-fallback mode, got {r.status_code}"
            assert r.headers.get("x-contract-status") == "draft-fallback", (
                f"Expected X-Contract-Status: draft-fallback, got {r.headers.get('x-contract-status')}"
            )
        finally:
            # Restore original status
            registry.set_status(contract_name, "latest", original_status)

    def test_draft_serves_normally_without_strict(self, client, auth_headers, monkeypatch):
        """
        With STRICT_DRAFT_VALIDATION=False (default), DRAFT contracts serve validation
        with allow_draft=True and must NOT return the draft-fallback header.

        Note: contracts may have validate_in_states restrictions that block DRAFT
        validation without allow_draft. This test uses allow_draft=True to focus on
        the absence of draft-fallback header (the key invariant for non-strict mode).

        Uses registry directly for status changes to avoid the 10/minute rate-limit.
        """
        import config
        from api.routes import registry
        from core.rule_parser import ContractStatus

        monkeypatch.setattr(config, "STRICT_DRAFT_VALIDATION", False)

        # Find a contract that is available in the registry
        available = [c for c in registry.list_contracts(include_all=True) if c["status"] == "active"]
        if not available:
            pytest.skip("No active contracts available for test")
        contract_name = available[0]["name"]
        contract = registry.get(contract_name)
        original_status = contract.status

        # Set to draft directly
        registry.set_status(contract_name, "latest", ContractStatus.DRAFT)

        try:
            # Validate with allow_draft=True — should get 200, no draft-fallback header
            # (draft-fallback only appears when STRICT_DRAFT_VALIDATION=True)
            r = client.post(
                "/api/v1/validate",
                json={"record": {}, "contract": contract_name},
                params={"allow_draft": "true"},
                headers=auth_headers,
            )
            assert r.status_code == 200, f"Expected 200 for draft in non-strict mode, got {r.status_code}"
            assert r.headers.get("x-contract-status") != "draft-fallback", (
                "Expected no draft-fallback header in non-strict mode"
            )
        finally:
            # Restore original status
            registry.set_status(contract_name, "latest", original_status)


class TestSchemaRegistry:
    """ACT-038-02: Schema registry endpoints."""

    def test_registry_list(self, client, auth_headers):
        """GET /api/v1/registry returns registry catalog."""
        r = client.get("/api/v1/registry", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "registry" in data
        assert "count" in data
        assert "opendqv_node_id" in data
        assert data["count"] == len(data["registry"])
        # Each entry must have required fields
        for entry in data["registry"]:
            assert "name" in entry
            assert "version" in entry
            assert "status" in entry
            assert "schema_hash" in entry

    def test_registry_single_entry(self, client, auth_headers):
        """GET /api/v1/registry/{name} returns single contract in registry format."""
        # Use the first available contract from the registry list to avoid hardcoded names
        r = client.get("/api/v1/registry", headers=auth_headers)
        assert r.status_code == 200
        entries = r.json().get("registry", [])
        if not entries:
            pytest.skip("No contracts in registry")
        contract_name = entries[0]["name"]

        r = client.get(f"/api/v1/registry/{contract_name}", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == contract_name
        assert "schema_hash" in data
        assert "rules" in data
        assert isinstance(data["rules"], list)
        assert "opendqv_node_id" in data

    def test_registry_not_found(self, client, auth_headers):
        """GET /api/v1/registry/{name} returns 404 for unknown contract."""
        r = client.get("/api/v1/registry/nonexistent_contract_xyz", headers=auth_headers)
        assert r.status_code == 404


# ── TestMCPReviewPrerequisite ─────────────────────────────────────────────────

_MCP_TEST_RULE = {
    "name": "lifecycle_test_rule",
    "field": "test_field",
    "type": "not_empty",
    "severity": "error",
    "error_message": "test_field is required",
}


def _insert_mcp_draft(name: str, proposed_at=None, description="Test contract", owner="pytest"):
    """Insert a synthetic MCP-sourced draft contract directly into the registry."""
    from core.contracts import Rule
    contract = DataContract(
        name=name,
        version="1.0",
        description=description,
        owner=owner,
        status=ContractStatus.DRAFT,
        rules=[Rule(**_MCP_TEST_RULE)],
        source="mcp",
        proposed_by="agent@example.com",
        proposed_at=proposed_at,
        validate_in_states=["draft", "active"],
    )
    registry._contracts.setdefault(name, {})[contract.version] = contract
    return contract


def _remove_test_contract(name: str):
    registry._contracts.pop(name, None)
    path = registry.contracts_dir / f"{name}.yaml"
    if path.exists():
        path.unlink()


class TestMCPReviewPrerequisite:
    """ACT-046-07: MCP-sourced contracts must complete review workflow before activation."""

    _NAME = "MCP_lifecycle_review_prereq_test"

    def teardown_method(self):
        _remove_test_contract(self._NAME)

    def test_mcp_contract_blocked_without_review(self, client, approver_headers):
        """Activating an MCP draft that has not been submitted for review returns 403."""
        _insert_mcp_draft(self._NAME, proposed_at=None)
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 403
        assert "submit-review" in r.json()["detail"]

    def test_mcp_contract_allowed_after_review_submitted(self, client, approver_headers):
        """Activating an MCP draft that has proposed_at set (review submitted) returns 200."""
        from datetime import datetime, timezone
        _insert_mcp_draft(self._NAME, proposed_at=datetime.now(timezone.utc).isoformat())
        # Also field-completeness is satisfied (description, owner, rules all set)
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "active"

    def test_non_mcp_contract_not_affected(self, client, approver_headers):
        """Standard (non-MCP) contracts can be activated without the review workflow."""
        # Find an existing draft contract with description/owner/rules set
        r = client.get("/api/v1/contracts", headers=approver_headers)
        active = [c for c in r.json() if c["status"] == "active"]
        if not active:
            pytest.skip("No active contracts available to test non-MCP path")
        name = active[0]["name"]
        # Set it to draft first, then re-activate — non-MCP path should not be blocked
        client.post(f"/api/v1/contracts/{name}/status", params={"status": "draft"}, headers=approver_headers)
        r = client.post(f"/api/v1/contracts/{name}/status", params={"status": "active"}, headers=approver_headers)
        assert r.status_code == 200


# ── TestPromotionReadiness ────────────────────────────────────────────────────

class TestPromotionReadiness:
    """ACT-046-06: Field-completeness gate blocks promotion if description/owner/rules missing."""

    _NAME = "MCP_lifecycle_promo_ready_test"

    def teardown_method(self):
        _remove_test_contract(self._NAME)

    def _insert_draft(self, description="Test", owner="pytest", rules=True):
        from datetime import datetime, timezone
        from core.contracts import Rule
        contract = DataContract(
            name=self._NAME,
            version="1.0",
            description=description,
            owner=owner,
            status=ContractStatus.DRAFT,
            rules=[Rule(**_MCP_TEST_RULE)] if rules else [],
            proposed_at=datetime.now(timezone.utc).isoformat(),
        )
        registry._contracts.setdefault(self._NAME, {})[contract.version] = contract
        return contract

    def test_missing_description_blocks_activation(self, client, approver_headers):
        """Contract with empty description cannot be activated — 422."""
        self._insert_draft(description="")
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 422
        assert "description" in r.json()["detail"]

    def test_missing_owner_blocks_activation(self, client, approver_headers):
        """Contract with empty owner cannot be activated — 422."""
        self._insert_draft(owner="")
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 422
        assert "owner" in r.json()["detail"]

    def test_no_rules_blocks_activation(self, client, approver_headers):
        """Contract with no rules cannot be activated — 422."""
        self._insert_draft(rules=False)
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 422
        assert "rule" in r.json()["detail"]

    def test_all_fields_present_allows_activation(self, client, approver_headers):
        """Contract with description, owner, and rules can be activated — 200."""
        self._insert_draft()
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )
        assert r.status_code == 200


# ── TestXAuthModeHeader ───────────────────────────────────────────────────────

class TestXAuthModeHeader:
    """ACT-046-08: Write endpoints must return X-Auth-Mode response header."""

    _NAME = "MCP_xauth_header_test"

    def teardown_method(self):
        _remove_test_contract(self._NAME)

    def _get_draft_contract_name(self, client, headers):
        r = client.get("/api/v1/contracts", headers=headers)
        drafts = [c for c in r.json() if c["status"] == "draft"]
        return drafts[0]["name"] if drafts else None

    def test_change_status_has_x_auth_mode_header(self, client, approver_headers):
        """POST /contracts/{name}/status returns X-Auth-Mode header."""
        r = client.get("/api/v1/contracts", headers=approver_headers)
        active = [c for c in r.json() if c["status"] == "active"]
        if not active:
            pytest.skip("No active contracts available")
        name = active[0]["name"]
        # Toggle to draft and back — both status-change responses should have the header
        r = client.post(
            f"/api/v1/contracts/{name}/status",
            params={"status": "draft"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        assert "x-auth-mode" in r.headers
        # Restore
        client.post(f"/api/v1/contracts/{name}/status", params={"status": "active"}, headers=approver_headers)

    def test_add_rule_has_x_auth_mode_header(self, client, editor_headers, approver_headers):
        """POST /contracts/{name}/rules returns X-Auth-Mode header."""
        from core.contracts import Rule
        contract = DataContract(
            name=self._NAME,
            version="1.0",
            description="X-Auth-Mode header test",
            owner="pytest",
            status=ContractStatus.DRAFT,
            rules=[Rule(**_MCP_TEST_RULE)],
        )
        # Write to disk so the registry can save rule mutations atomically
        path = registry.contracts_dir / f"{self._NAME}.yaml"
        path.write_text(registry._contract_to_yaml(contract))
        registry._contracts.setdefault(self._NAME, {})[contract.version] = contract
        registry._contract_paths[self._NAME] = path
        new_rule = {
            "name": "xauth_extra_rule",
            "field": "extra_field",
            "type": "not_empty",
            "severity": "warning",
            "error_message": "extra_field required",
        }
        r = client.post(f"/api/v1/contracts/{self._NAME}/rules", json=new_rule, headers=editor_headers)
        assert r.status_code == 200
        assert "x-auth-mode" in r.headers

# ---------------------------------------------------------------------------
# ACT-049-07: Write guardrail bypass — extra query parameters must not
# weaken the ACTIVE contract immutability check.
# ---------------------------------------------------------------------------

class TestWriteGuardrailBypass:
    """Confirm that unknown/extra query parameters cannot bypass write guardrails."""

    _RULE = {
        "name": "guardrail_bypass_probe_rule",
        "field": "probe_field",
        "type": "not_empty",
        "severity": "warning",
        "error_message": "guardrail bypass probe",
    }

    def _get_active_contract(self, client, headers):
        r = client.get("/api/v1/contracts", headers=headers)
        active = [c for c in r.json() if c["status"] == "active"]
        return active[0]["name"] if active else None

    def test_add_rule_with_dry_run_false_still_blocked(self, client, editor_headers):
        """Passing dry_run=false as a query param must not bypass the ACTIVE guardrail."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.post(
            f"/api/v1/contracts/{name}/rules?dry_run=false",
            json=self._RULE,
            headers=editor_headers,
        )
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_add_rule_with_dry_run_true_still_blocked(self, client, editor_headers):
        """Passing dry_run=true as a query param must not bypass the ACTIVE guardrail."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.post(
            f"/api/v1/contracts/{name}/rules?dry_run=true",
            json=self._RULE,
            headers=editor_headers,
        )
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_update_rule_with_extra_params_still_blocked(self, client, editor_headers):
        """Extra query parameters on update_rule must not bypass the ACTIVE guardrail."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.get(f"/api/v1/contracts/{name}", headers=editor_headers)
        rules = r.json().get("rules", [])
        if not rules:
            pytest.skip("Contract has no rules")
        rule = rules[0]
        payload = {
            "name": rule["name"],
            "field": rule["field"],
            "type": rule["type"],
            "severity": rule["severity"],
            "error_message": rule.get("error_message", ""),
        }
        r = client.put(
            f"/api/v1/contracts/{name}/rules/{rule['name']}?force=true&bypass=1",
            json=payload,
            headers=editor_headers,
        )
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_delete_rule_with_extra_params_still_blocked(self, client, editor_headers):
        """Extra query parameters on delete_rule must not bypass the ACTIVE guardrail."""
        name = self._get_active_contract(client, editor_headers)
        if not name:
            pytest.skip("No active contracts available")
        r = client.get(f"/api/v1/contracts/{name}", headers=editor_headers)
        rules = r.json().get("rules", [])
        if not rules:
            pytest.skip("Contract has no rules")
        rule_name = rules[0]["name"]
        r = client.delete(
            f"/api/v1/contracts/{name}/rules/{rule_name}?force=true",
            headers=editor_headers,
        )
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]


# ---------------------------------------------------------------------------
# State machine transition validation (C2 fix, RT148)
# ---------------------------------------------------------------------------

class TestStatusTransitionValidation:
    """Contract state machine enforces valid transitions — invalid ones return 409.

    Setup/cleanup uses registry directly to avoid rate-limit exhaustion on the
    status endpoint (10 req/min). API is used only for the assertion under test.
    """

    def test_archived_to_active_blocked_via_api(self, client, approver_headers):
        """ARCHIVED → ACTIVE must be blocked — would bypass review workflow."""
        registry.set_status("customer", "latest", ContractStatus.ARCHIVED)
        try:
            r = client.post(
                "/api/v1/contracts/customer/status",
                params={"status": "active"},
                headers=approver_headers,
            )
            assert r.status_code == 409
            assert "archived" in r.json()["detail"].lower()
        finally:
            registry.set_status("customer", "latest", ContractStatus.DRAFT)
            registry.set_status("customer", "latest", ContractStatus.ACTIVE)

    def test_archived_to_active_blocked_via_registry(self):
        """Registry-level guard: set_status raises ValueError on archived → active."""
        registry.set_status("customer", "latest", ContractStatus.ARCHIVED)
        try:
            with pytest.raises(ValueError, match="archived"):
                registry.set_status("customer", "latest", ContractStatus.ACTIVE)
        finally:
            registry.set_status("customer", "latest", ContractStatus.DRAFT)
            registry.set_status("customer", "latest", ContractStatus.ACTIVE)

    def test_active_to_archived_allowed(self):
        """ACTIVE → ARCHIVED is a valid transition (retiring a contract)."""
        registry.set_status("customer", "latest", ContractStatus.ARCHIVED)
        assert registry.get("customer").status == ContractStatus.ARCHIVED
        # Cleanup
        registry.set_status("customer", "latest", ContractStatus.DRAFT)
        registry.set_status("customer", "latest", ContractStatus.ACTIVE)

    def test_active_to_draft_allowed(self):
        """ACTIVE → DRAFT is valid (rollback for revision)."""
        registry.set_status("customer", "latest", ContractStatus.DRAFT)
        assert registry.get("customer").status == ContractStatus.DRAFT
        # Cleanup
        registry.set_status("customer", "latest", ContractStatus.ACTIVE)

    def test_archived_to_draft_allowed(self):
        """ARCHIVED → DRAFT is valid (restore for revision)."""
        registry.set_status("customer", "latest", ContractStatus.ARCHIVED)
        registry.set_status("customer", "latest", ContractStatus.DRAFT)
        assert registry.get("customer").status == ContractStatus.DRAFT
        # Cleanup
        registry.set_status("customer", "latest", ContractStatus.ACTIVE)
