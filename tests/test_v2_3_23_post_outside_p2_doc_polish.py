"""
v2.3.23 outside-review doc polish (Sonnet ac114da9dc010d9a9).

Two minor doc gaps the reviewer flagged at P2 — both described
fields/values surface on the wire without inline explanation. The
schema descriptions exist; the MCP tool descriptions don't carry
them. Fix: add one-sentence pointers to each affected MCP tool
description.

Patch 1 — list_versions description must mention is_collision.
Patch 2 — get_quality_metrics / get_quality_trend / get_rule_velocity
descriptions must mention data_confidence band thresholds.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_proxy_module():
    os.environ.setdefault("OPENDQV_API_URL", "http://127.0.0.1:1")
    os.environ.setdefault("OPENDQV_API_TOKEN", "")
    proxy_path = Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"
    spec = importlib.util.spec_from_file_location("opendqv_mcp_proxy", proxy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["opendqv_mcp_proxy"] = mod
    spec.loader.exec_module(mod)
    return mod


def _in_process_descs():
    import asyncio
    from opendqv.mcp_server import server
    from mcp.types import ListToolsRequest
    handlers = server.request_handlers
    result = asyncio.run(
        handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    )
    return {t.name: (t.description or "") for t in result.root.tools}


@pytest.fixture
def proxy_mod():
    return _load_proxy_module()


class TestListVersionsMentionsIsCollision:
    def test_in_process(self):
        descs = _in_process_descs()
        desc = descs.get("list_versions", "")
        assert "is_collision" in desc, (
            f"v2.3.23 outside-review P2 doc polish: list_versions "
            f"description must mention is_collision so consumers see "
            f"the SemVer-collision flag's purpose without schema lookup. "
            f"Got: {desc!r}"
        )

    def test_proxy(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "list_versions")
        desc = tool.get("description", "")
        assert "is_collision" in desc, desc


class TestDataConfidenceBandsDocumented:
    """get_quality_metrics, get_quality_trend, get_rule_velocity all
    surface data_confidence on responses. The band thresholds (no_data:
    0, low: 1-9, medium: 10-99, high: >=100) belong in each tool's
    description so a consumer can interpret the value without sleuthing."""

    @pytest.mark.parametrize("tool_name", [
        "get_quality_metrics", "get_quality_trend", "get_rule_velocity",
    ])
    def test_in_process(self, tool_name):
        descs = _in_process_descs()
        desc = descs.get(tool_name, "").lower()
        # Must mention data_confidence + at least the threshold values.
        assert "data_confidence" in desc, (
            f"v2.3.23 outside-review P2 doc polish: {tool_name} must "
            f"mention data_confidence in its tool description (response "
            f"field surface). Got: {desc!r}"
        )
        # Band thresholds must be cited so consumers can interpret bands.
        assert "100" in desc, (
            f"{tool_name} description must cite the data_confidence "
            f"high-band threshold (>=100). Got: {desc!r}"
        )

    @pytest.mark.parametrize("tool_name", [
        "get_quality_metrics", "get_quality_trend", "get_rule_velocity",
    ])
    def test_proxy(self, tool_name, proxy_mod):
        tool = next((t for t in proxy_mod.TOOLS if t["name"] == tool_name), None)
        assert tool is not None, f"proxy missing tool {tool_name!r}"
        desc = tool.get("description", "").lower()
        assert "data_confidence" in desc, desc
        assert "100" in desc, desc
