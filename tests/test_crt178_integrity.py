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

import json
import shutil
from pathlib import Path

import pytest

from opendqv.core.contracts import CONTRACT_NAME_RE, ContractRegistry
from opendqv.core.rule_parser import ContractStatus

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
