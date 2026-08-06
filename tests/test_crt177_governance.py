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
import yaml

import pytest


ALL_ROLE_FIXTURES = ["reader_headers", "auditor_headers", "auth_headers"]  # auth_headers == validator


def _make_active(name: str):
    """Create a dedicated ACTIVE contract for a test.

    These tests must NOT drive a shared bundled contract (e.g. `customer`)
    through the API: `POST /contracts/{name}/status` is rate-limited to
    10/minute, so a restore call can be refused with 429 and leave the shared
    contract demoted — silently breaking every downstream test that assumes it
    is ACTIVE. Own the fixture, and set up / tear down through the registry,
    which is not rate-limited.
    """
    from opendqv.api.routes import registry

    path = registry.contracts_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"contract": {
        "name": name, "version": "1.0", "status": "active",
        "description": "CRT177 governance fixture", "owner": "pytest",
        "rules": [{"name": "r1", "field": "email", "type": "not_empty",
                   "error_message": "email is required"}],
    }}), encoding="utf-8")
    registry.reload()
    return name


def _destroy(name: str):
    from opendqv.api.routes import registry

    registry._contracts.pop(name, None)
    path = registry.contracts_dir / f"{name}.yaml"
    if path.exists():
        path.unlink()


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

    _NAME = "crt177_gov_profiler"

    def setup_method(self):
        _make_active(self._NAME)

    def teardown_method(self):
        _destroy(self._NAME)

    def test_editor_cannot_clobber_active_contract(self, client, editor_headers):
        r = client.post(
            "/api/v1/profile",
            json=[{"totally": "unrelated"}, {"totally": "shape"}],
            params={"contract_name": self._NAME, "save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 409, "profiler must refuse to overwrite a non-DRAFT contract"

    def test_active_contract_survives_the_attempt(self, client, editor_headers):
        before = client.get(f"/api/v1/contracts/{self._NAME}").json()
        client.post(
            "/api/v1/profile",
            json=[{"totally": "unrelated"}],
            params={"contract_name": self._NAME, "save": "true"},
            headers=editor_headers,
        )
        after = client.get(f"/api/v1/contracts/{self._NAME}").json()
        assert after["status"] == before["status"] == "active"
        assert len(after.get("rules", [])) == len(before.get("rules", [])), \
            "ACTIVE contract rules must be untouched by a refused profiler save"


class TestImportCannotOverwriteGovernedContract:
    """The import routes are the profiler hole's twin: caller-supplied contract
    name, bare `open(..., "w")`, no existence check. An editor could point an
    import at a live ACTIVE contract and replace its rules wholesale — and
    because imports now correctly land as DRAFT, doing so also *demoted* it,
    which `POST /contracts/{name}/status` restricts to approver/admin."""

    _ACTIVE = "crt177_gov_import"

    def setup_method(self):
        _make_active(self._ACTIVE)

    def teardown_method(self):
        _destroy(self._ACTIVE)
        _destroy("crt177_import_fresh")

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


class TestOverwriteGuardIsCaseInsensitive:
    """The guard looks up a case-SENSITIVE dict but writes to a filesystem that
    may be case-INSENSITIVE (Windows/macOS defaults — both supported targets),
    where `Customer.yaml` and `customer.yaml` are the same inode. A
    case-sensitive "doesn't exist" verdict would let the write silently replace
    a live ACTIVE contract. Found by adversarial review of the first cut."""

    _NAME = "crt177_case_target"

    def setup_method(self):
        _make_active(self._NAME)

    def teardown_method(self):
        _destroy(self._NAME)
        _destroy("crt177_case_fresh")

    @pytest.mark.parametrize("variant", [
        "crt177_case_target",      # exact
        "CRT177_CASE_TARGET",      # upper
        "Crt177_Case_Target",      # mixed
    ])
    def test_profiler_refuses_every_casing(self, client, editor_headers, variant):
        r = client.post(
            "/api/v1/profile", json=[{"a": 1}],
            params={"contract_name": variant, "save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 409, f"casing {variant!r} must not bypass the guard"

    def test_import_refuses_case_variant(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/gx", params={"save": "true"},
            json={"expectation_suite_name": "CRT177_Case_Target",
                  "expectations": [{"expectation_type": "expect_column_values_to_not_be_null",
                                    "kwargs": {"column": "x"}}]},
            headers=editor_headers,
        )
        assert r.status_code == 409

    def test_distinct_name_still_allowed(self, client, editor_headers):
        r = client.post(
            "/api/v1/profile", json=[{"a": 1}],
            params={"contract_name": "crt177_case_fresh", "save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 200


class TestStatusChangeAuthorization:
    """Every transition is a governed write — not just promotion to ACTIVE."""

    _NAME = "crt177_gov_status"

    def setup_method(self):
        _make_active(self._NAME)

    def teardown_method(self):
        _destroy(self._NAME)

    @pytest.mark.parametrize("hdr_name", ALL_ROLE_FIXTURES)
    @pytest.mark.parametrize("target", ["draft", "archived"])
    def test_low_privilege_cannot_demote_or_archive_active(self, client, request, hdr_name, target):
        headers = request.getfixturevalue(hdr_name)
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
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
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "draft"},
            headers=editor_headers,
        )
        assert r.status_code == 403

    def test_approver_can_demote_active(self, client, approver_headers):
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "draft"},
            headers=approver_headers,
        )
        assert r.status_code == 200
        # restore
        client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=approver_headers,
        )

    def test_validator_still_cannot_activate(self, client, auth_headers):
        """Pre-existing SEC guard must remain intact."""
        r = client.post(
            f"/api/v1/contracts/{self._NAME}/status",
            params={"status": "active"},
            headers=auth_headers,
        )
        assert r.status_code == 403
