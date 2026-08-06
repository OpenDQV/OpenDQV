"""
CRT177 Tier 2 — governance / RBAC recurrence tests (Protocol 32).

Three write paths bypassed the contract-governance invariants:

  * **Profiler save.** `POST /profile?save=true` and `/profile/file?save=true`
    had NO role guard (every `/import/*` sibling requires editor/admin per
    SEC-010) and did a bare `open(f"{contract_name}.yaml", "w")` with no
    existence check — so ANY authenticated principal could REPLACE a live
    ACTIVE contract with profiler-generated rules and reload it, defeating
    ACTIVE immutability and the approval workflow in one request.

  * **Status changes.** `POST /contracts/{name}/status` checked the caller's
    role ONLY when the target status was ACTIVE, so any principal —
    `reader`/`auditor`/`validator` — could archive or demote a production
    contract. Demote is not a lesser act than promote: DRAFT unlocks rule
    mutation, so demote-then-edit defeats maker-checker.

  * **Import status.** Covered in tests/test_crt177_imports.py.
"""
import pytest


ALL_ROLE_FIXTURES = ["reader_headers", "auditor_headers", "auth_headers"]  # auth_headers == validator


class TestProfilerSaveRequiresRole:
    """Profiler contract-write path must match /import/* (editor/admin)."""

    @pytest.mark.parametrize("hdr_name", ALL_ROLE_FIXTURES)
    def test_low_privilege_roles_cannot_save(self, client, request, hdr_name):
        headers = request.getfixturevalue(hdr_name)
        r = client.post(
            "/api/v1/profile",
            json=[{"a": 1}, {"a": 2}],
            params={"contract_name": "crt177_profile_rbac", "save": "true"},
            headers=headers,
        )
        assert r.status_code == 403, f"{hdr_name} must not be able to save a contract"

    def test_profiling_without_save_is_open_to_any_authenticated_role(self, client, auth_headers):
        """The guard must gate the WRITE, not profiling itself."""
        r = client.post(
            "/api/v1/profile",
            json=[{"a": 1}, {"a": 2}],
            params={"contract_name": "crt177_profile_readonly"},
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestProfilerCannotOverwriteGovernedContract:
    """The destructive-overwrite hole: profiling onto an existing ACTIVE name."""

    def test_editor_cannot_clobber_active_contract(self, client, editor_headers):
        # `customer` is a bundled ACTIVE contract.
        r = client.post(
            "/api/v1/profile",
            json=[{"totally": "unrelated"}, {"totally": "shape"}],
            params={"contract_name": "customer", "save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 409, "profiler must refuse to overwrite a non-DRAFT contract"

    def test_active_contract_survives_the_attempt(self, client, editor_headers):
        before = client.get("/api/v1/contracts/customer").json()
        client.post(
            "/api/v1/profile",
            json=[{"totally": "unrelated"}],
            params={"contract_name": "customer", "save": "true"},
            headers=editor_headers,
        )
        after = client.get("/api/v1/contracts/customer").json()
        assert after["status"] == before["status"] == "active"
        assert len(after.get("rules", [])) == len(before.get("rules", [])), \
            "ACTIVE contract rules must be untouched by a refused profiler save"


class TestImportCannotOverwriteGovernedContract:
    """The import routes are the profiler hole's twin: caller-supplied contract
    name, bare `open(..., "w")`, no existence check. An editor could point an
    import at a live ACTIVE contract and replace its rules wholesale — and
    because imports now correctly land as DRAFT, doing so also *demoted* it,
    which `POST /contracts/{name}/status` restricts to approver/admin."""

    _ACTIVE = "customer"

    def _gx_suite(self, name):
        return {
            "expectation_suite_name": name,
            "expectations": [{
                "expectation_type": "expect_column_values_to_not_be_null",
                "kwargs": {"column": "crt177_probe"},
            }],
        }

    def test_import_save_onto_active_name_is_refused(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/gx", params={"save": "true"},
            json=self._gx_suite(self._ACTIVE), headers=editor_headers,
        )
        assert r.status_code == 409

    def test_active_contract_survives_import_attempt(self, client, editor_headers):
        before = client.get(f"/api/v1/contracts/{self._ACTIVE}").json()
        client.post(
            "/api/v1/import/gx", params={"save": "true"},
            json=self._gx_suite(self._ACTIVE), headers=editor_headers,
        )
        after = client.get(f"/api/v1/contracts/{self._ACTIVE}").json()
        assert after["status"] == before["status"] == "active"
        assert len(after.get("rules", [])) == len(before.get("rules", [])), \
            "an import must not replace the rules of an ACTIVE contract"

    def test_preview_without_save_is_still_allowed(self, client, editor_headers):
        """The guard gates the WRITE — a read-only preview must not 409."""
        r = client.post(
            "/api/v1/import/gx",
            json=self._gx_suite(self._ACTIVE), headers=editor_headers,
        )
        assert r.status_code == 200

    def test_import_to_a_new_name_still_saves(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/gx", params={"save": "true"},
            json=self._gx_suite("crt177_import_fresh"), headers=editor_headers,
        )
        assert r.status_code == 200


class TestStatusChangeAuthorization:
    """Every transition is a governed write — not just promotion to ACTIVE."""

    @pytest.mark.parametrize("hdr_name", ALL_ROLE_FIXTURES)
    @pytest.mark.parametrize("target", ["draft", "archived"])
    def test_low_privilege_cannot_demote_or_archive_active(self, client, request, hdr_name, target):
        headers = request.getfixturevalue(hdr_name)
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": target},
            headers=headers,
        )
        assert r.status_code == 403, (
            f"{hdr_name} must not be able to set an ACTIVE contract to {target!r} — "
            f"archive is a validation outage and demote unlocks rule mutation"
        )

    def test_editor_cannot_demote_active(self, client, editor_headers):
        """Demote carries promotion's weight: editor-then-edit would defeat maker-checker."""
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "draft"},
            headers=editor_headers,
        )
        assert r.status_code == 403

    def test_approver_can_demote_active(self, client, approver_headers):
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "draft"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        # restore
        client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "active"},
            headers=approver_headers,
        )

    def test_validator_still_cannot_activate(self, client, auth_headers):
        """Pre-existing SEC guard must remain intact."""
        r = client.post(
            "/api/v1/contracts/customer/status",
            params={"status": "active"},
            headers=auth_headers,
        )
        assert r.status_code == 403
