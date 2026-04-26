"""
tests/test_crt173_v2312_response_shape.py — CRT173 v2.3.12.

Pins the response-shape contract intelligence surface added in v2.3.12:

  1. validate_record / validate_batch accept `hash` to pin validation to a
     specific historical contract version (a 404 is returned for unknown
     hashes — silent fallback to latest is a regulatory hazard).

  2. compare_contracts / GET /contracts/{name}/diff accept hash_a + hash_b
     and return rules_added/removed/changed plus from_hash/to_hash so the
     payload self-documents which snapshots were compared.

  3. GET /contracts/{name}/jsonschema emits a JSON Schema draft 2020-12
     document with rule-derived constraints and surfaces unmapped (cross-
     field) rules under x-opendqv-unmapped — never silently drops them.

  4. GET /contracts/{name}?context=X returns the EFFECTIVE rule set with
     overrides resolved (not just the base rules + a side-channel).
"""

import yaml

from opendqv.core.contracts import ContractRegistry
from opendqv.core.jsonschema import contract_to_jsonschema


def _seed_contract_with_history(tmp_path, monkeypatch):
    """Create a tiny contract on disk + history so hash-based tests have something to point at."""
    cdir = tmp_path / "contracts"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "demo_v12.yaml").write_text(
        yaml.safe_dump({
            "name": "demo_v12",
            "version": "1.0",
            "description": "v2.3.12 fixture contract",
            "owner": "test",
            "status": "active",
            "rules": [
                {"name": "name_required", "type": "not_empty", "field": "name",
                 "error_message": "name required"},
                {"name": "age_range", "type": "range", "field": "age",
                 "min_value": 0, "max_value": 150,
                 "error_message": "age out of range"},
                {"name": "email_pattern", "type": "regex", "field": "email",
                 "pattern": r"^[^@]+@[^@]+$",
                 "error_message": "invalid email"},
            ],
            "contexts": {
                "kids_app": {
                    "age_range": {
                        "type": "range",
                        "min": 4,
                        "max": 13,
                        "error_message": "kids_app: age must be 4-13",
                    }
                }
            },
        }),
        encoding="utf-8",
    )
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("OPENDQV_CONTRACTS_DIR", str(cdir))
    monkeypatch.setenv("DB_PATH", str(db_path))
    registry = ContractRegistry(contracts_dir=cdir)
    return registry


# 1 ──────────────────────────────────────────────────────────────────
class TestHashPinnedValidation:

    def test_validate_with_unknown_hash_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/validate",
            headers=auth_headers,
            json={
                "contract": "customer",
                "record": {"name": "Alice"},
                "hash": "0" * 64,
                "dry_run": True,
            },
        )
        assert resp.status_code == 404
        assert "history entry matching hash" in resp.json()["detail"]

    def test_batch_with_unknown_hash_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/validate/batch",
            headers=auth_headers,
            json={
                "contract": "customer",
                "records": [{"name": "Alice"}],
                "hash": "f" * 64,
                "dry_run": True,
            },
        )
        assert resp.status_code == 404


# 2 ──────────────────────────────────────────────────────────────────
class TestCompareContractsByHash:

    def test_diff_endpoint_requires_pair(self, client, auth_headers):
        resp = client.get(
            "/api/v1/contracts/customer/diff",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_diff_endpoint_unknown_hash_404(self, client, auth_headers):
        resp = client.get(
            "/api/v1/contracts/customer/diff",
            headers=auth_headers,
            params={"hash_a": "0" * 64, "hash_b": "f" * 64},
        )
        assert resp.status_code == 404

    def test_registry_diff_by_hash_shape(self, tmp_path, monkeypatch):
        registry = _seed_contract_with_history(tmp_path, monkeypatch)
        history = registry.get_history("demo_v12")
        assert history, "fixture must produce a history entry"
        h = history[0].get("entry_hash") or history[0].get("content_hash")
        diff = registry.diff_by_hash("demo_v12", h, h)
        assert diff["from_hash"] == h
        assert diff["to_hash"] == h
        assert diff["changes"]["rules_added"] == []
        assert diff["changes"]["rules_removed"] == []
        assert diff["changes"]["rules_changed"] == []


# 3 ──────────────────────────────────────────────────────────────────
class TestJSONSchemaEmitter:

    def test_emits_draft_2020_12(self, tmp_path, monkeypatch):
        registry = _seed_contract_with_history(tmp_path, monkeypatch)
        contract = registry.get("demo_v12")
        schema = contract_to_jsonschema(contract)

        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["title"] == "demo_v12"
        assert "name" in schema["required"]

        props = schema["properties"]
        assert props["age"]["minimum"] == 0
        assert props["age"]["maximum"] == 150
        assert props["age"]["type"] in ("number", "integer")
        assert props["email"]["pattern"]
        assert props["email"]["type"] == "string"

    def test_unmapped_rules_surfaced(self, tmp_path, monkeypatch):
        cdir = tmp_path / "contracts"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "with_unique.yaml").write_text(
            yaml.safe_dump({
                "name": "with_unique",
                "version": "1.0",
                "description": "exercises unmapped path",
                "owner": "test",
                "status": "active",
                "rules": [
                    {"name": "id_unique", "type": "unique", "field": "id",
                     "error_message": "must be unique"},
                ],
            }),
            encoding="utf-8",
        )
        registry = ContractRegistry(contracts_dir=cdir)
        schema = contract_to_jsonschema(registry.get("with_unique"))
        unmapped = schema.get("x-opendqv-unmapped", [])
        assert any(item["type"] == "unique" for item in unmapped), (
            "unique rules can't be expressed in JSON Schema and must be flagged"
        )

    def test_jsonschema_endpoint_404(self, client, auth_headers):
        resp = client.get(
            "/api/v1/contracts/does_not_exist_v12/jsonschema",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# 4 ──────────────────────────────────────────────────────────────────
class TestGetContractContextResolution:

    def test_context_overrides_applied_to_rules_field(self, tmp_path, monkeypatch):
        registry = _seed_contract_with_history(tmp_path, monkeypatch)
        scoped = registry.get_rules_with_context(registry.get("demo_v12"), "kids_app")
        age = next(r for r in scoped if r.name == "age_range")
        assert age.min_value == 4
        assert age.max_value == 13

    def test_context_endpoint_404_for_unknown_context(self, client, auth_headers):
        resp = client.get(
            "/api/v1/contracts/customer",
            headers=auth_headers,
            params={"context": "this_context_does_not_exist"},
        )
        assert resp.status_code == 404
