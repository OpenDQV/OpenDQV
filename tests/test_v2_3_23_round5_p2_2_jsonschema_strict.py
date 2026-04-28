"""
v2.3.23 round-5 P2-2 — JSON Schema export additionalProperties is
flippable via OPENDQV_JSON_SCHEMA_STRICT env var or per-call kwarg.

Persona B 2026-04-28 outside review #5 P2:
> additionalProperties: true is hardcoded in the JSON Schema export.
> For strict producer/consumer contracts you'd want this flippable.

Default preserved (false → permissive) so existing callers see no
change. Opt-in to strict mode either via env var (engine-wide) or
per-call (`strict=true`).
"""

from types import SimpleNamespace



def _build_test_contract():
    """Build a minimal contract object with one not_empty rule."""
    rule = SimpleNamespace(
        name="email_required",
        field="email",
        type="not_empty",
        condition=None,
        # Following are not used for not_empty but the unmapper checks them.
        pattern=None, min_value=None, max_value=None,
        min_length=None, max_length=None, allowed_values=None,
        format=None, lookup_file=None,
    )
    return SimpleNamespace(
        name="t", description="", rules=[rule],
    )


# ── Default preserves additionalProperties: true ───────────────────────

class TestDefaultPermissive:
    def test_default_emits_additional_properties_true(self, monkeypatch):
        monkeypatch.delenv("OPENDQV_JSON_SCHEMA_STRICT", raising=False)
        # Reload config so the default is read fresh.
        import importlib
        import opendqv.config
        importlib.reload(opendqv.config)
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(_build_test_contract())
        assert schema["additionalProperties"] is True, (
            f"v2.3.23 round-5 P2-2: default must preserve "
            f"additionalProperties:true. Got: {schema['additionalProperties']}"
        )


# ── Per-call strict=True flips to additionalProperties: false ──────────

class TestPerCallStrict:
    def test_strict_true_emits_additional_properties_false(self):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(_build_test_contract(), strict=True)
        assert schema["additionalProperties"] is False, (
            f"v2.3.23 round-5 P2-2: strict=True must emit "
            f"additionalProperties:false for FS producer/consumer "
            f"strictness. Got: {schema['additionalProperties']}"
        )

    def test_strict_false_emits_additional_properties_true(self):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(_build_test_contract(), strict=False)
        assert schema["additionalProperties"] is True


# ── OPENDQV_JSON_SCHEMA_STRICT env var ─────────────────────────────────

class TestEnvVarOverride:
    def test_env_var_true_flips_default(self, monkeypatch):
        monkeypatch.setenv("OPENDQV_JSON_SCHEMA_STRICT", "true")
        import importlib
        import opendqv.config
        importlib.reload(opendqv.config)
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(_build_test_contract())
        assert schema["additionalProperties"] is False, (
            f"v2.3.23 round-5 P2-2: OPENDQV_JSON_SCHEMA_STRICT=true must "
            f"flip the default to additionalProperties:false. "
            f"Got: {schema['additionalProperties']}"
        )
        # Cleanup: reload default config so subsequent tests see permissive.
        monkeypatch.delenv("OPENDQV_JSON_SCHEMA_STRICT", raising=False)
        importlib.reload(opendqv.config)

    def test_per_call_overrides_env_var(self, monkeypatch):
        """Per-call strict kwarg always wins over the env var default."""
        monkeypatch.setenv("OPENDQV_JSON_SCHEMA_STRICT", "true")
        import importlib
        import opendqv.config
        importlib.reload(opendqv.config)
        from opendqv.core.jsonschema import contract_to_jsonschema
        # Env says strict; call says non-strict — call wins.
        schema = contract_to_jsonschema(_build_test_contract(), strict=False)
        assert schema["additionalProperties"] is True
        monkeypatch.delenv("OPENDQV_JSON_SCHEMA_STRICT", raising=False)
        importlib.reload(opendqv.config)


# ── REST endpoint honours the strict query param ──────────────────────

class TestRestEndpointStrictParam:
    def test_rest_strict_param_emits_additional_properties_false(
        self, client, auth_headers,
    ):
        # customer is a bundled contract — its JSON Schema export is
        # the canonical end-to-end check.
        resp = client.get(
            "/api/v1/contracts/customer/jsonschema?strict=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["additionalProperties"] is False, (
            f"v2.3.23 round-5 P2-2: ?strict=true must emit "
            f"additionalProperties:false. Got: {body.get('additionalProperties')}"
        )

    def test_rest_no_strict_param_uses_default_permissive(
        self, client, auth_headers, monkeypatch,
    ):
        monkeypatch.delenv("OPENDQV_JSON_SCHEMA_STRICT", raising=False)
        resp = client.get(
            "/api/v1/contracts/customer/jsonschema",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["additionalProperties"] is True


# ── In-process MCP get_contract_jsonschema honours strict arg ─────────

class TestMcpToolStrictArg:
    def test_mcp_strict_true_emits_additional_properties_false(self):
        import asyncio
        import json
        from opendqv import mcp_server

        async def _call():
            out = await mcp_server._tool_get_contract_jsonschema({
                "name": "customer",
                "strict": True,
            })
            return json.loads(out[0].text)

        body = asyncio.run(_call())
        assert body["additionalProperties"] is False, (
            f"v2.3.23 round-5 P2-2: MCP get_contract_jsonschema strict=True "
            f"must emit additionalProperties:false. Got: "
            f"{body.get('additionalProperties')}"
        )
