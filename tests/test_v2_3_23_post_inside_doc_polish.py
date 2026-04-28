"""
v2.3.23 inside-view doc polish — surfaced by the post-fix inside check
(agent ad8b0823f8656e8c6).

Two tiny doc-honesty patches:

1. `list_contracts` description should mention `include_all=true` so a
   consumer reading the MCP surface alone knows the default returns
   non-archived (active + draft + review) but archived can be included
   on request. Pairs with the P1-10 doc-fix on the REST endpoint.

2. `compare_contracts` description should make the workflow explicit:
   call list_versions first, then pass two hash values. The current
   parenthetical "(from list_versions)" is correct but easy to miss —
   the inside-view agent missed it and tried `name_a/name_b` instead
   of reading the schema. An explicit "Call list_versions first..."
   prefix removes the ambiguity.

Sonnet's pre-impl review (a28b3255e63e6ea8e): doc-only, ship bundled.
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


def _in_process_tool_descriptions():
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


class TestListContractsDocMentionsIncludeAll:
    """An MCP consumer reading list_contracts description alone must
    learn that `include_all=true` is the way to also surface archived
    contracts. Without this, they'd see the default-non-archived
    behaviour and not know how to broaden it."""

    def test_in_process_description_mentions_include_all(self):
        descs = _in_process_tool_descriptions()
        desc = descs.get("list_contracts", "")
        assert "include_all" in desc, (
            f"v2.3.23 inside-view: list_contracts description must "
            f"mention include_all=true so a consumer can discover the "
            f"archived-include path from MCP alone. Got: {desc!r}"
        )

    def test_proxy_description_mentions_include_all(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "list_contracts")
        desc = tool.get("description", "")
        assert "include_all" in desc, desc


class TestCompareContractsDocMakesWorkflowExplicit:
    """Inside-view agent ad8b0823f8656e8c6 missed the workflow hint
    in the current description and tried wrong arg shapes. Make the
    "call list_versions first" step explicit so an LLM consumer
    cannot misread."""

    def test_in_process_description_says_call_list_versions_first(self):
        descs = _in_process_tool_descriptions()
        desc = descs.get("compare_contracts", "").lower()
        # Either explicit "call list_versions first" or "list_versions
        # first" — both forms acceptable as long as the workflow order
        # is unambiguous.
        assert "list_versions first" in desc or "call list_versions" in desc, (
            f"v2.3.23 inside-view: compare_contracts must explicitly "
            f"name the list_versions-first workflow so an LLM consumer "
            f"can self-discover the call sequence. Got: {desc!r}"
        )

    def test_proxy_description_says_call_list_versions_first(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "compare_contracts")
        desc = tool.get("description", "").lower()
        assert "list_versions first" in desc or "call list_versions" in desc, desc
