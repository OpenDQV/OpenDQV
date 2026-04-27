"""
v2.3.17 Cluster 4 — proxy/in-process MCP parity test.

The single highest-leverage test in this release. OpenDQV ships TWO MCP
entry points — ``opendqv/mcp_server.py`` (FastMCP-based, in-process) and
``opendqv_mcp_proxy.py`` (stdio bridge that forwards to the REST API).
They drift, repeatedly. Every MCP-touching release in the v2.3.x sprint
introduced fresh drift — different schemas, missing tools, divergent
error shapes. The cause: no structural test enforces parity. Enforcement
has been manual inspection.

This test snapshots both servers' tool inventories (names, schemas,
required fields) and error shapes, and asserts equality. v2.4 single-
server-with-transport-adaptor unification (see CRT-N proxy unification
deferred decision) supersedes this test; until then it is the load-
bearing protection.

ONE acknowledged asymmetry: ``create_contract_draft`` is on the in-process
server only. The proxy cannot wrap it without a corresponding REST
endpoint (currently absent). Tracked as a v2.4 deliverable. Listed
explicitly in ``KNOWN_ASYMMETRIES`` so a NEW asymmetry introduced after
v2.3.17 fails this test loudly.
"""



# Tools that exist on in-process MCP only (with documented v2.4 reason).
# Adding a tool to either surface without adding it to the other MUST
# update this list with a v2.4 owner — otherwise the parity test fails.
KNOWN_ASYMMETRIES = {
    "create_contract_draft": (
        "in-process only — proxy cannot wrap without a corresponding REST "
        "endpoint (POST /api/v1/contracts/draft). v2.4 CRT-N proxy "
        "unification will resolve."
    ),
}


def _proxy_tool_names() -> set:
    """Read the proxy's TOOLS list without invoking its httpx client."""
    import importlib.util
    import sys
    import os

    # Stub OPENDQV_API_URL so importing the proxy doesn't sys.exit
    os.environ.setdefault("OPENDQV_API_URL", "http://localhost:0")
    spec = importlib.util.spec_from_file_location(
        "opendqv_mcp_proxy",
        "/home/sunny-sharma/OpenDQV/opendqv_mcp_proxy.py",
    )
    proxy = importlib.util.module_from_spec(spec)
    sys.modules["opendqv_mcp_proxy"] = proxy
    spec.loader.exec_module(proxy)
    return {t["name"] for t in proxy.TOOLS}, proxy


def _in_process_tool_names() -> set:
    """Inspect the in-process server's tools/list output."""
    import asyncio
    from opendqv.mcp_server import server

    # FastMCP exposes the registered tools via list_tools handlers.
    # Call the underlying handler directly to avoid running stdio.
    handlers = server.request_handlers
    # Find the list_tools handler
    from mcp.types import ListToolsRequest
    handler = handlers[ListToolsRequest]
    request = ListToolsRequest(method="tools/list")
    result = asyncio.run(handler(request))
    # result is ServerResult; access via .root
    tools = result.root.tools
    return {t.name for t in tools}, tools


class TestProxyInprocessParity:

    def test_tool_inventories_match_modulo_known_asymmetries(self):
        proxy_names, _ = _proxy_tool_names()
        inproc_names, _ = _in_process_tool_names()

        only_in_proxy = proxy_names - inproc_names
        only_in_inproc = inproc_names - proxy_names

        # Tools on proxy but not in-process — should never happen.
        # The proxy is a thin shim; everything it exposes must exist
        # in-process too.
        assert not only_in_proxy, \
            f"Tools on proxy but not in-process: {only_in_proxy}. " \
            f"Add them to mcp_server.py or remove from proxy."

        # Tools on in-process but not on proxy — only if in KNOWN_ASYMMETRIES.
        unexpected_only_inproc = only_in_inproc - set(KNOWN_ASYMMETRIES)
        assert not unexpected_only_inproc, (
            f"NEW tools on in-process MCP that are missing from the proxy: "
            f"{unexpected_only_inproc}. Add them to opendqv_mcp_proxy.py "
            f"OR add to KNOWN_ASYMMETRIES with a v2.4 owner reason."
        )

    def test_error_envelope_shape_matches(self):
        """Both surfaces must return errors with the same shape:
        ``{"error": {"error_code", "kind", "status", "detail", "remediation"}}``"""
        from opendqv.mcp_server import _error_envelope as inproc_env

        proxy_names, proxy = _proxy_tool_names()
        proxy_env = proxy._error_envelope

        inproc_str = inproc_env(
            error_code="TEST_CODE", kind="bad_request",
            detail="test detail", status=400, remediation="test fix",
        )
        proxy_str = proxy_env(
            error_code="TEST_CODE", kind="bad_request",
            detail="test detail", status=400, remediation="test fix",
        )

        # Both must produce the same JSON byte-for-byte (modulo dict order
        # which json.dumps stabilises for the same insertion order).
        import json
        inproc_obj = json.loads(inproc_str)
        proxy_obj = json.loads(proxy_str)
        assert inproc_obj == proxy_obj, \
            f"Error envelope shapes diverge:\n  in-process: {inproc_obj}\n  proxy: {proxy_obj}"

        # Required keys
        for key in ("error_code", "kind", "status", "detail", "remediation"):
            assert key in inproc_obj["error"], f"in-process missing {key}"
            assert key in proxy_obj["error"], f"proxy missing {key}"

    def test_required_fields_match_for_shared_tools(self):
        """For each tool present on BOTH surfaces, the `required` fields
        in inputSchema must agree (caller code that works against proxy
        must also work against in-process)."""
        proxy_names_set, proxy = _proxy_tool_names()
        inproc_names_set, inproc_tools = _in_process_tool_names()

        proxy_by_name = {t["name"]: t for t in proxy.TOOLS}
        inproc_by_name = {t.name: t for t in inproc_tools}

        shared = proxy_names_set & inproc_names_set
        diffs = []
        for name in shared:
            proxy_required = set(proxy_by_name[name]["inputSchema"].get("required", []))
            inproc_required = set(inproc_by_name[name].inputSchema.get("required", []))
            if proxy_required != inproc_required:
                diffs.append((name, proxy_required, inproc_required))
        assert not diffs, \
            f"Required-field drift between proxy and in-process for tools: {diffs}"

    def test_initialize_serverinfo_version_does_not_drift(self):
        """v2.3.18+ Sonnet belt-and-suspenders: the existing parity test
        snapshots tool inventories, NOT initialize.serverInfo. The proxy
        version-hardcode bug that slipped through v2.3.17 + v2.3.18 lived
        in that gap. This test asserts that the proxy's version-resolution
        function exists and refuses to return a hardcoded SemVer when the
        engine is unreachable. Cheaper than the cluster-6 subprocess test
        — runs in unit-test time.

        The cluster-6 ``test_proxy_initialize_reports_real_engine_version_
        when_connected`` is the full positive-path test against a live
        engine. This here is the unit-level guard that the proxy's
        resolution code path is wired correctly (not bypassed by a
        future "easy fix" that re-introduces a hardcode).
        """
        import importlib.util
        import sys
        import os

        os.environ.setdefault("OPENDQV_API_URL", "http://localhost:0")
        os.environ.setdefault("OPENDQV_API_TOKEN", "")
        spec = importlib.util.spec_from_file_location(
            "opendqv_mcp_proxy",
            "/home/sunny-sharma/OpenDQV/opendqv_mcp_proxy.py",
        )
        proxy = importlib.util.module_from_spec(spec)
        sys.modules["opendqv_mcp_proxy"] = proxy
        spec.loader.exec_module(proxy)

        # The resolved engine version constant must exist and must NOT
        # be a SemVer-shaped string when the engine is unreachable. A
        # SemVer-shaped value here means a future regression hardcoded
        # the version back. The acceptable values are "unknown" or the
        # version of an actually-running engine on http://localhost:0
        # (which can't be running because port 0 is the OS sentinel for
        # "unreachable").
        assert hasattr(proxy, "_ENGINE_VERSION"), \
            "proxy must expose _ENGINE_VERSION resolved at module-import time"
        # When _client points at localhost:0, resolution must fail and
        # return 'unknown'. A v.x.y string here means the proxy has a
        # hardcoded fallback (the bug class this test guards against).
        import re
        assert proxy._ENGINE_VERSION == "unknown" or not re.match(
            r"^\d+\.\d+\.\d+", proxy._ENGINE_VERSION
        ), (
            f"proxy._ENGINE_VERSION resolved to {proxy._ENGINE_VERSION!r} when engine "
            f"unreachable — looks like a SemVer hardcode regression. The 'unknown' "
            f"sentinel is the only acceptable value when /openapi.json cannot be reached."
        )
