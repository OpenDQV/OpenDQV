"""
v2.3.17 Cluster 6 — surface hygiene, version consistency, opt-in metadata.

Resolved open questions integrated here:
- Q11: ``engine_version`` is dropped from ``GET /config`` (Sonnet's option
  iv). The OpenAPI 3.x spec REQUIRES ``info.version`` on
  ``/openapi.json`` so that surface keeps it. ``/config`` was redundant.
- Q12: ``engine_version`` on validate response is now opt-in via
  ``include_metadata: bool = False``. Default-off matches MCP
  reference-server minimalism; the durable per-call version remains in
  the audit-event payload retrievable via ``get_audit_event``.
- F-S: version-source consistency invariant — every surface that
  exposes engine version MUST agree with ``importlib.metadata`` and
  ``pyproject.toml``. Three-way (was four-way before Q11).
- F-Q: ``record_id`` is now declared in the MCP ``validate_record``
  inputSchema on both surfaces (proxy and in-process).
"""

import importlib.metadata
from pathlib import Path

import pytest


try:
    import tomllib as _tomllib  # py311+
except ImportError:  # pragma: no cover
    import tomli as _tomllib


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    return _tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]


# ── F-S: version-source consistency ────────────────────────────────────

class TestVersionSourceConsistency:
    """Every version surface must agree. Drift between any two is a
    CRT170-J violation — a field's name claims a guarantee its
    implementation does not honour."""

    def test_pyproject_matches_importlib_metadata(self):
        pp = _pyproject_version()
        meta = importlib.metadata.version("opendqv")
        assert pp == meta, (
            f"pyproject.toml ({pp}) and importlib.metadata.version('opendqv') "
            f"({meta}) disagree. After bumping the version, run "
            f"`pip install -e .` to refresh the installed metadata."
        )

    def test_openapi_info_version_matches_pyproject(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        info_version = r.json()["info"]["version"]
        assert info_version == _pyproject_version(), (
            f"/openapi.json info.version ({info_version}) disagrees with "
            f"pyproject.toml ({_pyproject_version()})."
        )

    def test_proxy_initialize_reports_unknown_when_engine_unreachable(self):
        """v2.3.18+ negative-path recurrence test: when the engine is
        unreachable, the proxy must NOT report a stale hardcoded version
        — it reports the "unknown" sentinel instead. Guards against
        regression to the v2.3.16-era hardcode pattern."""
        import os
        import subprocess
        import json

        env = {
            **os.environ,
            "OPENDQV_API_URL": "http://localhost:0",  # Unreachable
            "OPENDQV_API_TOKEN": "",
        }
        init_frame = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
            '{"protocolVersion":"2024-11-05","capabilities":{},'
            '"clientInfo":{"name":"v","version":"v"}}}\n'
        )
        result = subprocess.run(
            ["python3", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py")],
            input=init_frame, capture_output=True, text=True, timeout=15, env=env,
        )
        version_seen = None
        for line in result.stdout.splitlines():
            try:
                m = json.loads(line)
                if m.get("id") == 1:
                    version_seen = m.get("result", {}).get("serverInfo", {}).get("version")
                    break
            except Exception:
                pass
        assert version_seen == "unknown", (
            f"proxy must report 'unknown' when engine is unreachable; got {version_seen!r}. "
            "If you see a hardcoded version here, the proxy has regressed to the v2.3.16-style "
            "static hardcode that drifted every release."
        )

    def test_proxy_initialize_reports_real_engine_version_when_connected(self):
        """v2.3.18+ positive-path recurrence test (Sonnet's catch): when
        the engine IS reachable, the proxy must report the SAME version
        as importlib.metadata. Closes the F-S invariant ring for the
        proxy surface — the unreachable-engine test alone only guards
        against regression to a hardcode; this guards against regression
        to a wrong-but-plausible source (e.g. the proxy reading a
        different version field from the wrong endpoint).

        Spawns the proxy as a subprocess pointing at a TestClient-style
        in-process app. Because TestClient does not bind a real port,
        we boot a real uvicorn on a random free port for this test only.
        """
        import importlib.metadata
        import json
        import os
        import socket
        import subprocess
        import time

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        # Boot a real uvicorn for this test
        engine_proc = subprocess.Popen(
            ["python", "-m", "uvicorn", "opendqv.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            # Wait for the engine to bind the port
            import urllib.request
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/openapi.json", timeout=1
                    ):
                        break
                except Exception:
                    time.sleep(0.2)
            else:
                pytest.skip("test engine did not start in time")

            # Now run the proxy against the live engine
            env = {
                **os.environ,
                "OPENDQV_API_URL": f"http://127.0.0.1:{port}",
                "OPENDQV_API_TOKEN": "",
            }
            init_frame = (
                '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
                '{"protocolVersion":"2024-11-05","capabilities":{},'
                '"clientInfo":{"name":"v","version":"v"}}}\n'
            )
            result = subprocess.run(
                ["python3", str(__import__("pathlib").Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py")],
                input=init_frame, capture_output=True, text=True, timeout=15, env=env,
            )
            version_seen = None
            for line in result.stdout.splitlines():
                try:
                    m = json.loads(line)
                    if m.get("id") == 1:
                        version_seen = m.get("result", {}).get("serverInfo", {}).get("version")
                        break
                except Exception:
                    pass

            expected = importlib.metadata.version("opendqv")
            assert version_seen == expected, (
                f"proxy must report the running engine version; got {version_seen!r}, "
                f"expected {expected!r}. F-S invariant violation."
            )
        finally:
            engine_proc.terminate()
            engine_proc.wait(timeout=5)

    def test_config_does_not_expose_engine_version(self, client, admin_token):
        """Q11 (Sonnet option iv): engine_version is dropped from /config —
        it was redundant with /openapi.json info.version (which is REQUIRED
        by the OpenAPI 3.x spec). /config remains the operator-diagnostic
        surface for non-version operator fields."""
        r = client.get(
            "/config",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "engine_version" not in body, (
            f"engine_version should be dropped from /config in v2.3.17 — "
            f"de-duplication with /openapi.json info.version. Got: {body}"
        )
        # /config is still a useful operator surface — confirm at least
        # one non-version operator field is present so the endpoint isn't
        # accidentally gutted.
        assert "node_id" in body or "auth" in body, \
            f"/config should retain operator-diagnostic fields, got: {body}"


# ── v2.3.20: engine_version always populated (Q12 reversal) ────────────

class TestEngineVersionAlwaysPopulated:
    """v2.3.20 reverses v2.3.17 Q12 (Sonnet's option iv). The opt-in
    default-off backfired in regulated-FS context — Persona B's outside
    review flagged ``engine_version: ""`` as a P2 observability gap.
    Their SoX/DORA/MiFIR audit-trail framing outranks the MCP reference-
    server minimalism that drove the original choice. The
    ``include_metadata`` flag is removed entirely; engine_version is
    always emitted on validate responses."""

    def test_validate_always_returns_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        ev = r.json().get("engine_version")
        assert ev, "engine_version must always be populated post-v2.3.20"
        assert ev == _pyproject_version(), \
            f"engine_version must match pyproject.toml, got {ev}"

    def test_batch_validate_always_returns_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "records": [{"name": "Alice", "age": 30, "email": "a@b.co"}],
        }
        r = client.post("/api/v1/validate/batch?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        ev = r.json().get("engine_version")
        assert ev == _pyproject_version()

    def test_include_metadata_field_removed_from_request_models(self):
        """v2.3.20: the include_metadata field is removed from
        ValidateRequest and BatchValidateRequest. Sending it in a request
        body is now ignored (Pydantic default behaviour for unknown
        fields) — confirm the field is no longer declared."""
        from opendqv.api.models import ValidateRequest, BatchValidateRequest
        assert "include_metadata" not in ValidateRequest.model_fields, \
            "v2.3.20: include_metadata must be removed from ValidateRequest"
        assert "include_metadata" not in BatchValidateRequest.model_fields, \
            "v2.3.20: include_metadata must be removed from BatchValidateRequest"


# ── F-Q: record_id on MCP validate_record schema ──────────────────────

class TestMcpValidateRecordHasRecordId:
    """Both MCP surfaces (proxy and in-process) must declare record_id
    in the validate_record inputSchema. Caller code that uses record_id
    against REST should also work via MCP."""

    def test_proxy_validate_record_schema_includes_record_id(self):
        import importlib.util
        import os
        import sys

        os.environ.setdefault("OPENDQV_API_URL", "http://localhost:0")
        spec = importlib.util.spec_from_file_location(
            "opendqv_mcp_proxy",
            str(__import__("pathlib").Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"),
        )
        proxy = importlib.util.module_from_spec(spec)
        sys.modules["opendqv_mcp_proxy"] = proxy
        spec.loader.exec_module(proxy)
        validate_record = next(t for t in proxy.TOOLS if t["name"] == "validate_record")
        assert "record_id" in validate_record["inputSchema"]["properties"], \
            f"proxy validate_record must declare record_id, got: {list(validate_record['inputSchema']['properties'])}"

    def test_in_process_validate_record_schema_includes_record_id(self):
        import asyncio
        from opendqv.mcp_server import server
        from mcp.types import ListToolsRequest

        handlers = server.request_handlers
        handler = handlers[ListToolsRequest]
        result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
        tools = {t.name: t for t in result.root.tools}
        validate_record = tools["validate_record"]
        assert "record_id" in validate_record.inputSchema["properties"], \
            f"in-process validate_record must declare record_id, got: {list(validate_record.inputSchema['properties'])}"
