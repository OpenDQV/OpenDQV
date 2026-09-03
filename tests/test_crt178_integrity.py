"""
CRT178 integrity release (v2.4.1) — recurrence tests.

One class per fix. Each test fails on v2.4.0 and passes after the fix:
  1. MCP create_draft name path-traversal (arbitrary file write)
  2. `format` (and conditional trigger fields) reaching DuckDB SQL unbound
  3. Cross-version approval forgery (path index keyed by name only)
  4. bump_contract_version aliasing the live ACTIVE object, persisting nothing
  5. REVIEW-state contracts mutable (maker-checker TOCTOU)
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from opendqv.core.contracts import CONTRACT_NAME_RE, ContractRegistry
from opendqv.core.rule_parser import ContractStatus, Rule

BUNDLED = Path(__file__).resolve().parent.parent / "opendqv" / "contracts"


@pytest.fixture
def reg(tmp_path):
    d = tmp_path / "contracts"
    d.mkdir()
    shutil.copy(BUNDLED / "customer.yaml", d / "customer.yaml")
    return ContractRegistry(d)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# ─────────────────────────────────────────────────────────────────────────────
# 1. MCP create_draft name traversal
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateDraftNameTraversal:
    BAD = ["MCP_../../escaped", "MCP_/etc/cron.d/x", "MCP_a b", "MCP_a.yaml", "MCP_" + "x" * 97, "../MCP_x"]

    @pytest.mark.parametrize("name", BAD)
    def test_core_rejects_and_writes_nothing(self, reg, tmp_path, name):
        before = _snapshot(tmp_path)
        with pytest.raises(ValueError, match="Invalid contract name|MCP_"):
            reg.create_draft(name=name, description="d", owner="o", created_by="agent",
                             rules_data=[{"name": "r", "type": "not_empty", "field": "f"}])
        assert _snapshot(tmp_path) == before
        assert not (tmp_path / "escaped.yaml").exists()

    def test_core_accepts_valid_name(self, reg):
        c = reg.create_draft(name="MCP_ok-name_1", description="d", owner="o", created_by="agent",
                             rules_data=[{"name": "r", "type": "not_empty", "field": "f"}])
        assert c.status == ContractStatus.DRAFT
        assert (reg.contracts_dir / "MCP_ok-name_1.yaml").exists()

    def test_deps_regex_is_the_core_regex(self):
        from opendqv.api import deps
        assert deps._CONTRACT_NAME_RE is CONTRACT_NAME_RE

    @pytest.mark.asyncio
    async def test_mcp_tool_returns_422_envelope_and_writes_nothing(self, tmp_path, monkeypatch):
        import opendqv.mcp_server as m
        d = tmp_path / "c"
        d.mkdir()
        monkeypatch.setattr(m, "_registry", ContractRegistry(d))
        monkeypatch.setenv("OPENDQV_MCP_AGENT_ID", "tester")
        before = _snapshot(tmp_path)
        out = await m._tool_create_contract_draft({
            "name": "MCP_../../pwned", "description": "d", "owner": "o", "created_by": "agent",
            "rules": [{"name": "r", "type": "not_empty", "field": "f"}],
        })
        body = json.loads(out[0].text)
        err = body.get("error", body)
        assert err.get("error_code") == "INVALID_CONTRACT_NAME"
        assert _snapshot(tmp_path) == before


# ─────────────────────────────────────────────────────────────────────────────
# 2. `format` (and every other field reference) reaching DuckDB SQL
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatSqlInjection:
    RECORDS = [{"d": "2026-01-02"}, {"d": "not a date"}, {"d": "2026-13-45"}]

    def _run(self, fmt: str):
        from opendqv.core.validator import validate_batch
        rule = Rule(name="fmt", type="date_format", field="d", format=fmt, error_message="bad date")
        return validate_batch(self.RECORDS, [rule], contract_name="t")

    @pytest.mark.parametrize("fmt", [
        "%Y-%m-%d') OR 1=1 --",
        "%Y'",
        "%Y-%m-%d\") OR (1=1",
        "%Y-%m-%d'; COPY data TO '/tmp/x.csv'; --",
    ])
    def test_hostile_format_is_a_bound_value_not_sql(self, fmt):
        """On v2.4.0 the first two raise a DuckDB parser error (500) and the injection
        variants change the WHERE clause. Now: no exception, and the format is simply a
        format that nothing matches — every record fails the rule, none false-pass."""
        out = self._run(fmt)
        assert out["summary"]["failed"] == 3
        assert all(not r["valid"] for r in out["results"])

    def test_legit_format_still_works(self):
        out = self._run("%Y-%m-%d")
        assert [r["valid"] for r in out["results"]] == [True, False, False]

    def test_query_text_binds_fmt(self):
        """Guard against someone re-inlining the format into the query string."""
        import inspect
        from opendqv.core import validator
        src = inspect.getsource(validator._batch_check_rule_inner)
        assert "$fmt" in src
        assert "'{strptime_fmt}'" not in src

    @pytest.mark.parametrize("kwargs", [
        {"type": "required_if", "required_if": {"field": 'x" OR 1=1--', "value": "y"}},
        {"type": "forbidden_if", "forbidden_if": {"field": 'x"; DROP TABLE data; --', "value": "y"}},
        {"type": "min", "min_value": 0, "condition": {"field": 'a"b', "value": "y"}},
        {"type": "compare", "compare_to": 'b"c', "compare_op": "gt"},
        {"type": "cross_field_range", "cross_min_field": 'lo"', "cross_max_field": "hi"},
        {"type": "date_diff", "date_diff_field": 'd";', "date_diff_unit": "days"},
    ])
    def test_sec004_covers_every_field_reference(self, kwargs):
        """Sonnet blocker: SEC-004 only inspected rule.field; trigger fields were quoted identifiers too."""
        with pytest.raises(ValueError, match="not permitted in a SQL identifier"):
            Rule(name="r", field="f", **kwargs)

    def test_sec004_still_allows_normal_references(self):
        Rule(name="r", field="f", type="required_if", required_if={"field": "other field.name-1", "value": "y"})


# ─────────────────────────────────────────────────────────────────────────────
# 5. REVIEW-state contracts must be frozen
# ─────────────────────────────────────────────────────────────────────────────

RULE = {"name": "extra", "type": "not_empty", "field": "g", "error_message": "g required"}


def _teardown(registry, name):
    """Remove a contract seeded into the LIVE registry so later tests never see it."""
    for p in list(registry.contracts_dir.glob(f"{name}*.yaml")):
        p.unlink()
    registry._contracts.pop(name, None)
    registry._contract_paths.pop(name, None)


def _seed(registry, name, status):
    """Create a DRAFT via the registry and move it to the requested state (no rate-limited routes)."""
    c = registry.create_draft(name=name, description="d", owner="o", created_by="alice",
                              rules_data=[{"name": "r", "type": "not_empty", "field": "f"}])
    if status in (ContractStatus.REVIEW, ContractStatus.ACTIVE):
        registry.submit_for_review(name, c.version, "alice")
    if status == ContractStatus.ACTIVE:
        registry.approve_contract(name, c.version, "bob")
    return registry.get(name)


class TestReviewStateFrozen:
    def test_registry_blocks_all_three_mutations_in_review(self, reg):
        from opendqv.core.contracts import ContractImmutableError
        c = _seed(reg, "MCP_rev1", ContractStatus.REVIEW)
        path = reg.contracts_dir / "MCP_rev1.yaml"
        before = path.read_bytes()
        for call in (lambda: reg.add_rule("MCP_rev1", RULE),
                     lambda: reg.update_rule("MCP_rev1", "r", {**RULE, "name": "r"}),
                     lambda: reg.delete_rule("MCP_rev1", "r")):
            with pytest.raises(ContractImmutableError, match="REVIEW"):
                call()
        assert path.read_bytes() == before
        assert [r.name for r in c.rules] == ["r"]

    def test_registry_blocks_active_too(self, reg):
        from opendqv.core.contracts import ContractImmutableError
        _seed(reg, "MCP_act1", ContractStatus.ACTIVE)
        with pytest.raises(ContractImmutableError, match="ACTIVE"):
            reg.add_rule("MCP_act1", RULE)

    def test_draft_still_mutable_and_reject_reopens_editing(self, reg):
        _seed(reg, "MCP_rev2", ContractStatus.REVIEW)
        reg.reject_contract("MCP_rev2", reg.get("MCP_rev2").version, "bob", "needs work")
        c = reg.add_rule("MCP_rev2", RULE)
        assert [r.name for r in c.rules] == ["r", "extra"]

    def test_rest_returns_409_for_review(self, client, editor_headers):
        from opendqv.api import deps as _d
        _seed(_d.registry, "MCP_rev3", ContractStatus.REVIEW)
        try:
            r = client.post("/api/v1/contracts/MCP_rev3/rules", json=RULE, headers=editor_headers)
            assert r.status_code == 409 and "REVIEW" in r.json()["detail"] and "reject" in r.json()["detail"]
            r = client.put("/api/v1/contracts/MCP_rev3/rules/r", json={**RULE, "name": "r"}, headers=editor_headers)
            assert r.status_code == 409
            r = client.delete("/api/v1/contracts/MCP_rev3/rules/r", headers=editor_headers)
            assert r.status_code == 409
            assert [x.name for x in _d.registry.get("MCP_rev3").rules] == ["r"]
        finally:
            _teardown(_d.registry, "MCP_rev3")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Cross-version approval forgery — path index keyed by (name, version)
# ─────────────────────────────────────────────────────────────────────────────

def _write_version_file(d: Path, name: str, version: str, status: str) -> Path:
    p = d / f"{name}_v{version}.yaml"
    p.write_text(yaml.safe_dump({"contract": {
        "name": name, "version": version, "status": status, "description": "d", "owner": "o",
        "rules": [{"name": f"r{version.replace('.', '_')}", "type": "not_empty", "field": "f", "error_message": "m"}],
    }}, sort_keys=False), encoding="utf-8")
    return p


class TestCrossVersionWriteBack:
    @pytest.fixture
    def two_files(self, tmp_path):
        d = tmp_path / "c"
        d.mkdir()
        v1 = _write_version_file(d, "t", "1.0", "active")
        v2 = _write_version_file(d, "t", "2.0", "draft")
        return ContractRegistry(d), v1, v2

    def test_index_is_two_level(self, two_files):
        reg, v1, v2 = two_files
        assert reg._contract_paths["t"] == {"1.0": v1, "2.0": v2}

    def test_approving_v2_never_touches_v1_file(self, two_files):
        reg, v1, v2 = two_files
        v1_before = v1.read_bytes()
        reg.submit_for_review("t", "2.0", "alice")
        reg.approve_contract("t", "2.0", "bob")
        assert v1.read_bytes() == v1_before                       # forgery vector closed
        on_disk = yaml.safe_load(v2.read_text(encoding="utf-8"))["contract"]
        assert on_disk["status"] == "active" and on_disk["approved_by"] == "bob"

    def test_demoting_v1_never_touches_v2_file(self, two_files):
        reg, v1, v2 = two_files
        v2_before = v2.read_bytes()
        reg.set_status("t", "1.0", ContractStatus.DRAFT)
        assert v2.read_bytes() == v2_before
        assert yaml.safe_load(v1.read_text(encoding="utf-8"))["contract"]["status"] == "draft"

    def test_rule_mutation_writes_only_the_matching_file(self, two_files):
        reg, v1, v2 = two_files
        v1_before = v1.read_bytes()
        reg.add_rule("t", RULE)                                  # latest = 2.0 (draft)
        assert v1.read_bytes() == v1_before
        names = [r["name"] for r in yaml.safe_load(v2.read_text(encoding="utf-8"))["contract"]["rules"]]
        assert names == ["r2_0", "extra"]

    def test_draft_patch_counter_rekeys_and_keeps_writing(self, two_files):
        reg, v1, v2 = two_files
        c = reg.add_rule("t", RULE)                              # 2.0 -> 2.0-draft.1
        assert c.version == "2.0-draft.1"
        assert reg._contract_paths["t"] == {"1.0": v1, "2.0-draft.1": v2}
        c = reg.add_rule("t", {**RULE, "name": "extra2"})        # -> 2.0-draft.2 via re-keyed index
        assert c.version == "2.0-draft.2"
        assert yaml.safe_load(v2.read_text(encoding="utf-8"))["contract"]["version"] == "2.0-draft.2"

    def test_ambiguous_target_refuses_and_writes_nothing(self, two_files, tmp_path):
        reg, v1, v2 = two_files
        # An in-memory version nobody indexed, on a name stored across two files.
        ghost = copy.deepcopy(reg.get("t", "1.0"))
        ghost.version = "9.9"
        reg._contracts["t"]["9.9"] = ghost
        before = _snapshot(tmp_path)
        with pytest.raises(RuntimeError, match="refusing to guess"):
            reg._write_contract_yaml("t", ghost)
        assert _snapshot(tmp_path) == before

    def test_single_file_contract_unversioned_key_still_resolves(self, reg):
        c = reg.get("customer")
        c2 = reg.add_rule("customer", RULE) if c.status == ContractStatus.DRAFT else None
        # customer is ACTIVE in the bundle; exercise the fallback via _path_for directly
        assert reg._path_for("customer", "not-indexed") == reg.contracts_dir / "customer.yaml"
        assert c2 is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. bump_contract_version must not alias or mutate the live object
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateVersion:
    def test_base_object_untouched_and_new_version_is_its_own_object(self, reg):
        base = reg.get("customer", "1.0")
        base_rules = [r.name for r in base.rules]
        new = reg.create_version("customer", "1.0", "2.0")
        assert new is not base and new.rules is not base.rules
        assert base.status == ContractStatus.ACTIVE and base.version == "1.0"
        assert reg.get("customer", "1.0") is base
        assert new.status == ContractStatus.DRAFT and new.version == "2.0"
        assert [r.name for r in new.rules] == base_rules

    def test_new_version_carries_no_approval_trail(self, reg):
        base = reg.get("customer", "1.0")
        base.approved_by, base.approved_at, base.proposed_by = "bob", "2026-01-01T00:00:00+00:00", "alice"
        new = reg.create_version("customer", "1.0", "2.0")
        assert new.approved_by is None and new.approved_at is None and new.proposed_by is None
        on_disk = yaml.safe_load((reg.contracts_dir / "customer_v2.0.yaml").read_text(encoding="utf-8"))["contract"]
        assert on_disk["status"] == "draft" and "approved_by" not in on_disk

    def test_persisted_and_survives_reload_with_both_versions(self, reg):
        reg.create_version("customer", "1.0", "2.0")
        reg.reload()
        assert reg.get("customer", "1.0").status == ContractStatus.ACTIVE
        assert reg.get("customer", "2.0").status == ContractStatus.DRAFT
        assert reg.get("customer").version == "2.0"
        assert reg._contract_paths["customer"]["2.0"].name == "customer_v2.0.yaml"

    def test_lossless_copy_of_base_file(self, reg):
        base_raw = yaml.safe_load((reg.contracts_dir / "customer.yaml").read_text(encoding="utf-8"))["contract"]
        reg.create_version("customer", "1.0", "2.0")
        new_raw = yaml.safe_load((reg.contracts_dir / "customer_v2.0.yaml").read_text(encoding="utf-8"))["contract"]
        assert new_raw["rules"] == base_raw["rules"]
        assert new_raw.get("contexts") == base_raw.get("contexts")

    @pytest.mark.parametrize("bad", ["1.0", "../x", "a/b", "", "x" * 51])
    def test_rejects_duplicate_and_unsafe_versions(self, reg, tmp_path, bad):
        before = _snapshot(tmp_path)
        with pytest.raises(ValueError):
            reg.create_version("customer", "1.0", bad)
        assert _snapshot(tmp_path) == before

    def test_route_keeps_response_shape_and_does_not_mutate_base(self, client, approver_headers):
        from opendqv.api import deps as _d
        _seed(_d.registry, "MCP_bump1", ContractStatus.ACTIVE)
        try:
            base = _d.registry.get("MCP_bump1", "1.0")
            r = client.post("/api/v1/contracts/MCP_bump1/version", params={"new_version": "2.0"}, headers=approver_headers)
            assert r.status_code == 200
            body = r.json()
            assert body["new_version"] == "2.0" and body["status"] == "draft" and "diff" in body
            assert base.status == ContractStatus.ACTIVE and base.version == "1.0"
            assert _d.registry.get("MCP_bump1", "2.0") is not base
            assert (_d.registry.contracts_dir / "MCP_bump1_v2.0.yaml").exists()
            r2 = client.post("/api/v1/contracts/MCP_bump1/version", params={"new_version": "2.0"}, headers=approver_headers)
            assert r2.status_code == 400
        finally:
            _teardown(_d.registry, "MCP_bump1")
