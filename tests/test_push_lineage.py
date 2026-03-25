"""
tests/test_push_lineage.py — Unit tests for push_quality_lineage.py and marmot_proxy.py
new logic (RT106 Phase 1 + 2A + 2B).
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


# ---------------------------------------------------------------------------
# Helpers to import modules under test with path manipulation
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def import_push_lineage():
    """Import push_quality_lineage as a module (without executing main)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "push_quality_lineage", SCRIPTS_DIR / "push_quality_lineage.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Provide a dummy MARMOT_TOKEN so the module-level guard doesn't trigger
    with patch.dict("os.environ", {"MARMOT_TOKEN": "test-token"}):
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# DataContract round-trip tests
# ---------------------------------------------------------------------------

class TestDataContractNewFields:
    def test_downstream_consumers_defaults_empty(self):
        from core.contracts import DataContract
        c = DataContract(name="x", rules=[])
        assert c.downstream_consumers == []

    def test_downstream_consumers_set(self):
        from core.contracts import DataContract
        mrns = ["mrn://dataset/tableau/sales", "mrn://dataset/dbt/mart"]
        c = DataContract(name="x", rules=[], downstream_consumers=mrns)
        assert c.downstream_consumers == mrns

    def test_catalog_visible_defaults_true(self):
        from core.contracts import DataContract
        c = DataContract(name="x", rules=[])
        assert c.catalog_visible is True

    def test_catalog_visible_false(self):
        from core.contracts import DataContract
        c = DataContract(name="x", rules=[], catalog_visible=False)
        assert c.catalog_visible is False


class TestContractParsing:
    """Ensure new fields survive YAML → DataContract round-trip."""

    def test_downstream_consumers_parsed(self, tmp_path):
        from core.contracts import ContractRegistry
        yaml_content = """
contract:
  name: test_dc
  version: "1.0"
  status: active
  asset_id: "mrn://dataset/opendqv/test_dc"
  downstream_consumers:
    - "mrn://dataset/tableau/sales"
    - "mrn://dataset/dbt/mart"
  rules: []
"""
        (tmp_path / "test_dc.yaml").write_text(yaml_content, encoding="utf-8")
        reg = ContractRegistry(tmp_path)
        contract = reg.get("test_dc")
        assert contract is not None
        assert contract.downstream_consumers == [
            "mrn://dataset/tableau/sales",
            "mrn://dataset/dbt/mart",
        ]

    def test_catalog_visible_false_parsed(self, tmp_path):
        from core.contracts import ContractRegistry
        yaml_content = """
contract:
  name: test_hidden
  version: "1.0"
  status: active
  asset_id: "mrn://dataset/opendqv/test_hidden"
  catalog_visible: false
  rules: []
"""
        (tmp_path / "test_hidden.yaml").write_text(yaml_content, encoding="utf-8")
        reg = ContractRegistry(tmp_path)
        contract = reg.get("test_hidden")
        assert contract is not None
        assert contract.catalog_visible is False

    def test_catalog_visible_defaults_true_when_absent(self, tmp_path):
        from core.contracts import ContractRegistry
        yaml_content = """
contract:
  name: test_visible
  version: "1.0"
  status: active
  asset_id: "mrn://dataset/opendqv/test_visible"
  rules: []
"""
        (tmp_path / "test_visible.yaml").write_text(yaml_content, encoding="utf-8")
        reg = ContractRegistry(tmp_path)
        contract = reg.get("test_visible")
        assert contract is not None
        assert contract.catalog_visible is True

    def test_old_contract_without_new_fields_loads(self, tmp_path):
        """Backward compatibility — existing contracts without new fields load cleanly."""
        from core.contracts import ContractRegistry
        yaml_content = """
contract:
  name: legacy
  version: "2.1"
  status: active
  rules:
    - name: chk_id
      type: not_empty
      field: id
      severity: error
"""
        (tmp_path / "legacy.yaml").write_text(yaml_content, encoding="utf-8")
        reg = ContractRegistry(tmp_path)
        contract = reg.get("legacy")
        assert contract is not None
        assert contract.downstream_consumers == []
        assert contract.catalog_visible is True


# ---------------------------------------------------------------------------
# push_quality_lineage.py tests
# ---------------------------------------------------------------------------

class TestLoadContracts:
    def test_hidden_contract_excluded(self, tmp_path):
        (tmp_path / "visible.yaml").write_text(yaml.dump({
            "contract": {
                "name": "visible", "status": "active",
                "asset_id": "mrn://dataset/opendqv/visible",
                "catalog_visible": True, "rules": [],
            }
        }), encoding="utf-8")
        (tmp_path / "hidden.yaml").write_text(yaml.dump({
            "contract": {
                "name": "hidden", "status": "active",
                "asset_id": "mrn://dataset/opendqv/hidden",
                "catalog_visible": False, "rules": [],
            }
        }), encoding="utf-8")

        pql = import_push_lineage()
        with patch.object(pql, "CONTRACTS_DIR", tmp_path):
            contracts = pql.load_contracts()

        names = [c["name"] for c in contracts]
        assert "visible" in names
        assert "hidden" not in names

    def test_contract_without_catalog_visible_included(self, tmp_path):
        (tmp_path / "no_flag.yaml").write_text(yaml.dump({
            "contract": {
                "name": "no_flag", "status": "active",
                "asset_id": "mrn://dataset/opendqv/no_flag",
                "rules": [],
            }
        }), encoding="utf-8")

        pql = import_push_lineage()
        with patch.object(pql, "CONTRACTS_DIR", tmp_path):
            contracts = pql.load_contracts()

        assert any(c["name"] == "no_flag" for c in contracts)


class TestStitchConsumerLineage:
    def test_correct_payload(self):
        pql = import_push_lineage()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = "created"
        mock_client.post.return_value = mock_response

        with patch.object(pql, "MARMOT_URL", "http://marmot:8080"), \
             patch.object(pql, "MARMOT_TOKEN", "tok"):
            status, body = pql.stitch_consumer_lineage(
                mock_client, "sales_orders", "mrn://dataset/tableau/sales_dashboard"
            )

        assert status == 201
        call_kwargs = mock_client.post.call_args
        payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
        assert payload["source"] == "mrn://dataset/opendqv/sales_orders"
        assert payload["target"] == "mrn://dataset/tableau/sales_dashboard"
        assert payload["type"] == "downstream"


class TestBuildRunEventOwnerTeam:
    def _make_stats(self, contract_name: str) -> dict:
        return {
            "total": 100,
            "passed": 90,
            "failed": 10,
            "top_failing_rules": {},
        }

    def test_owner_team_included_in_facet(self):
        pql = import_push_lineage()
        contract = {
            "name": "sales",
            "asset_id": "mrn://dataset/opendqv/sales",
            "owner_team": "data-platform",
        }
        event = pql.build_run_event(contract, self._make_stats("sales"))
        facet = event["run"]["facets"]["opendqvQuality"]
        assert facet["contractOwnerTeam"] == "data-platform"

    def test_owner_team_none_when_absent(self):
        pql = import_push_lineage()
        contract = {
            "name": "sales",
            "asset_id": "mrn://dataset/opendqv/sales",
        }
        event = pql.build_run_event(contract, self._make_stats("sales"))
        facet = event["run"]["facets"]["opendqvQuality"]
        assert facet["contractOwnerTeam"] is None


# ---------------------------------------------------------------------------
# marmot_proxy.py tests
# ---------------------------------------------------------------------------

class TestLoadHiddenNames:
    def test_returns_hidden_names(self, tmp_path):
        (tmp_path / "a.yaml").write_text(yaml.dump({
            "contract": {"name": "alpha", "catalog_visible": False, "rules": []}
        }), encoding="utf-8")
        (tmp_path / "b.yaml").write_text(yaml.dump({
            "contract": {"name": "beta", "rules": []}
        }), encoding="utf-8")

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "marmot_proxy_test", REPO_ROOT / "marmot_proxy.py"
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"OPENDQV_CONTRACTS_DIR": str(tmp_path)}):
            spec.loader.exec_module(mod)
            hidden = mod._load_hidden_names()

        assert "alpha" in hidden
        assert "beta" not in hidden

    def test_empty_when_dir_missing(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "marmot_proxy_test2", REPO_ROOT / "marmot_proxy.py"
        )
        mod = importlib.util.module_from_spec(spec)
        missing_dir = str(tmp_path / "nonexistent")
        with patch.dict("os.environ", {"OPENDQV_CONTRACTS_DIR": missing_dir}):
            spec.loader.exec_module(mod)
            hidden = mod._load_hidden_names()

        assert hidden == set()


class TestFilterDiscoverResponse:
    def _make_body(self, asset_names: list[str]) -> str:
        assets = [{"name": n, "mrn": f"mrn://dataset/opendqv/{n}"} for n in asset_names]
        inner = json.dumps({"assets": assets, "total": len(assets)})
        outer = {"result": {"content": [{"type": "text", "text": inner}]}}
        return json.dumps(outer)

    def test_hidden_asset_removed(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "marmot_proxy_filter", REPO_ROOT / "marmot_proxy.py"
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"OPENDQV_CONTRACTS_DIR": "/nonexistent_xyz"}):
            spec.loader.exec_module(mod)

        # Manually inject hidden names to avoid filesystem dependency
        mod._HIDDEN_NAMES = {"secret_contract"}
        body = self._make_body(["public_contract", "secret_contract"])
        result = mod._filter_discover_response(body)
        parsed = json.loads(result)
        inner = json.loads(parsed["result"]["content"][0]["text"])
        names = [a["name"] for a in inner["assets"]]
        assert "public_contract" in names
        assert "secret_contract" not in names

    def test_visible_assets_pass_through(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "marmot_proxy_filter2", REPO_ROOT / "marmot_proxy.py"
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"OPENDQV_CONTRACTS_DIR": "/nonexistent_xyz"}):
            spec.loader.exec_module(mod)

        mod._HIDDEN_NAMES = set()
        body = self._make_body(["a", "b", "c"])
        result = mod._filter_discover_response(body)
        # No hidden names → body returned as-is (possibly reparsed but equivalent)
        assert result == body

    def test_malformed_json_returned_unchanged(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "marmot_proxy_filter3", REPO_ROOT / "marmot_proxy.py"
        )
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"OPENDQV_CONTRACTS_DIR": "/nonexistent_xyz"}):
            spec.loader.exec_module(mod)

        mod._HIDDEN_NAMES = {"x"}
        bad = "not json at all"
        assert mod._filter_discover_response(bad) == bad
