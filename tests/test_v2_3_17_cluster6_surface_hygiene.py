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


# ── Q12: engine_version on validate response is opt-in ────────────────

class TestEngineVersionOptIn:
    """v2.3.17 Q12: validate response strips engine_version by default;
    include_metadata=true opts in. Audit-event payload preserves the
    durable per-call version regardless."""

    def test_default_validate_omits_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        # engine_version is either absent or empty string — Pydantic default ""
        assert not r.json().get("engine_version"), \
            f"engine_version should be empty by default, got: {r.json().get('engine_version')!r}"

    def test_include_metadata_opt_in_returns_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "include_metadata": True,
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        ev = r.json().get("engine_version")
        assert ev, "include_metadata=true must return engine_version"
        assert ev == _pyproject_version(), \
            f"engine_version on opt-in response must match pyproject.toml, got {ev}"

    def test_batch_default_omits_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "records": [{"name": "Alice", "age": 30, "email": "a@b.co"}],
        }
        r = client.post("/api/v1/validate/batch?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        assert not r.json().get("engine_version"), \
            f"batch engine_version should be empty by default, got: {r.json().get('engine_version')!r}"

    def test_batch_include_metadata_returns_engine_version(self, client, auth_headers):
        body = {
            "contract": "customer",
            "records": [{"name": "Alice", "age": 30, "email": "a@b.co"}],
            "include_metadata": True,
        }
        r = client.post("/api/v1/validate/batch?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        ev = r.json().get("engine_version")
        assert ev == _pyproject_version()


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
            "/home/sunny-sharma/OpenDQV/opendqv_mcp_proxy.py",
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
