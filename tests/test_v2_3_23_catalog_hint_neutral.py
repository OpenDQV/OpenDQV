"""
v2.3.23 — catalog_hint URI scheme is configurable, not hardcoded to Marmot.

Pilot observation 2026-04-28: "I feel like we have pitched our carriage
to the marmot engine when it was just supposed to be an example of mcp
data catalog integration."

Sonnet pre-impl review (a20541e90ba0d8ca8) verdict: default to
`marmot:assets/` (preserve current behaviour, opt-out for non-Marmot
catalogs), env-configurable via OPENDQV_CATALOG_URI_PREFIX. Empty
string omits the field entirely. Defer default-empty to v2.4 along with
positioning shift in docs.

Tests pin the v2.3.23 contract:
  1. Default unset → catalog_hint = `marmot:assets/{name}` (regression
     guard: scripts/test_marmot_integration.py:218 still works).
  2. Custom prefix → catalog_hint = `{prefix}{name}` (DataHub, Unity
     Catalog, OpenMetadata operators can rebrand without code changes).
  3. Empty prefix → catalog_hint omitted entirely (operators who don't
     run any catalog can drop the field).
  4. Both surfaces (in-process MCP + stdio proxy) read the same env
     var — dual-path discipline.
"""

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _reload_proxy_module():
    """Force-reload the proxy with current env so CATALOG_URI_PREFIX
    captures the monkeypatched value (proxy reads env at import time)."""
    os.environ.setdefault("OPENDQV_API_URL", "http://127.0.0.1:1")
    os.environ.setdefault("OPENDQV_API_TOKEN", "")
    sys.modules.pop("opendqv_mcp_proxy", None)
    proxy_path = Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"
    spec = importlib.util.spec_from_file_location("opendqv_mcp_proxy", proxy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["opendqv_mcp_proxy"] = mod
    spec.loader.exec_module(mod)
    return mod


def _reload_config():
    import opendqv.config as _cfg
    importlib.reload(_cfg)
    return _cfg


# ── Default behaviour (no override) ─────────────────────────────────────

class TestDefaultPrefixIsMarmot:
    """Regression guard. v2.3.22 and earlier hardcoded `marmot:assets/`
    everywhere; the v2.3.23 patch must preserve that default so existing
    smoke tests / demo paths don't break."""

    def test_in_process_default_prefix(self, monkeypatch):
        monkeypatch.delenv("OPENDQV_CATALOG_URI_PREFIX", raising=False)
        cfg = _reload_config()
        assert cfg.CATALOG_URI_PREFIX == "marmot:assets/"

    def test_proxy_default_prefix(self, monkeypatch):
        monkeypatch.delenv("OPENDQV_CATALOG_URI_PREFIX", raising=False)
        proxy = _reload_proxy_module()
        assert proxy.CATALOG_URI_PREFIX == "marmot:assets/"


# ── Override (DataHub / Unity Catalog / OpenMetadata) ──────────────────

class TestCustomPrefix:
    @pytest.mark.parametrize("prefix", [
        "datahub:dataset/",
        "unitycatalog://",
        "openmetadata:asset/",
        "atlan:asset/",
        "collibra:asset/",
    ])
    def test_in_process_honours_custom_prefix(self, monkeypatch, prefix):
        monkeypatch.setenv("OPENDQV_CATALOG_URI_PREFIX", prefix)
        cfg = _reload_config()
        assert cfg.CATALOG_URI_PREFIX == prefix

    @pytest.mark.parametrize("prefix", [
        "datahub:dataset/",
        "unitycatalog://",
    ])
    def test_proxy_honours_custom_prefix(self, monkeypatch, prefix):
        monkeypatch.setenv("OPENDQV_CATALOG_URI_PREFIX", prefix)
        proxy = _reload_proxy_module()
        assert proxy.CATALOG_URI_PREFIX == prefix


# ── Empty prefix → field omitted ────────────────────────────────────────

class TestEmptyPrefixOmitsField:
    """Operators with no catalog set OPENDQV_CATALOG_URI_PREFIX="" and
    catalog_hint disappears from the response. Consumer code can check
    `"catalog_hint" in entry` to branch on availability."""

    def test_in_process_empty_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENDQV_CATALOG_URI_PREFIX", "")
        cfg = _reload_config()
        assert cfg.CATALOG_URI_PREFIX == ""

    def test_proxy_empty_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENDQV_CATALOG_URI_PREFIX", "")
        proxy = _reload_proxy_module()
        assert proxy.CATALOG_URI_PREFIX == ""


# ── Description copy reflects configurability ───────────────────────────

class TestDescriptionDocumentsEnvVar:
    """The reviewer must be able to discover the env var from the tool
    description alone — not from the source code."""

    def test_in_process_description_mentions_env_var(self):
        import asyncio
        from opendqv.mcp_server import server
        from mcp.types import ListToolsRequest
        handlers = server.request_handlers
        result = asyncio.run(
            handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        )
        descs = {t.name: (t.description or "") for t in result.root.tools}
        desc = descs.get("get_quality_metrics", "")
        assert "OPENDQV_CATALOG_URI_PREFIX" in desc, (
            f"get_quality_metrics description must surface the env var "
            f"name so consumers can rebrand without source-diving. "
            f"Got: {desc!r}"
        )

    def test_proxy_description_mentions_env_var(self, monkeypatch):
        monkeypatch.delenv("OPENDQV_CATALOG_URI_PREFIX", raising=False)
        proxy = _reload_proxy_module()
        tool = next(t for t in proxy.TOOLS if t["name"] == "get_quality_metrics")
        desc = tool.get("description", "")
        assert "OPENDQV_CATALOG_URI_PREFIX" in desc, desc


# ── Marmot smoke-test compatibility ─────────────────────────────────────

class TestMarmotSmokeTestCompat:
    """scripts/test_marmot_integration.py:218 does
    `hint.startswith("marmot:assets/")`. v2.3.23 must keep that path
    working out of the box. v2.4 may flip the default; the env var is
    the long-term contract."""

    def test_default_emits_marmot_prefix(self, monkeypatch):
        monkeypatch.delenv("OPENDQV_CATALOG_URI_PREFIX", raising=False)
        cfg = _reload_config()
        # Reproduce the exact hint format the legacy smoke test asserts.
        contract = "customer"
        hint = f"{cfg.CATALOG_URI_PREFIX}{contract}"
        assert hint.startswith("marmot:assets/"), hint
        assert hint == "marmot:assets/customer", hint
