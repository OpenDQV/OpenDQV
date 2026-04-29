"""
v2.3.24 hotfix — explain_error curated_message reaches the wire.

v2.3.23 round-4 P2-A added `curated_message` to the `explain_rule()`
helper output. Three wrappers (REST route, MCP in-process tool, MCP
remote-client tool) build their response from explicit pick-lists and
silently dropped the field. The helper-level test in round-4 passed;
the wire-level test didn't exist; the persona reviewer didn't flag
the gap because absent-but-useful-field doesn't break composition.

Found during the post-tag MCP smoke (BT-7274 inside-check 2026-04-29).
Hotfix: add `curated_message` to all three response shapes.

Tests pin the wire shape on every surface:
  - REST `/api/v1/contracts/{name}/explain/{field}/{rule_name}`
  - MCP in-process `_tool_explain_error`
  - ExplainErrorResponse Pydantic model carries the field
"""

import asyncio
import json



VALID_LEI = "529900T8BM49AURSDO55"


class TestExplainErrorResponseModelCarriesField:
    def test_model_has_curated_message_field(self):
        from opendqv.api.models import ExplainErrorResponse
        assert "curated_message" in ExplainErrorResponse.model_fields, (
            f"v2.3.24: ExplainErrorResponse must declare curated_message. "
            f"Got: {sorted(ExplainErrorResponse.model_fields)}"
        )


class TestRestExplainCarriesCuratedMessage:
    """The REST route at /contracts/{name}/explain/{field}/{rule_name}
    must surface curated_message verbatim."""

    def test_lei_rule_rest_response_has_curated_message(
        self, client, auth_headers,
    ):
        resp = client.get(
            "/api/v1/contracts/mifid_transaction_report/explain/"
            "reporting_firm_lei/reporting_firm_lei_valid",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "curated_message" in body, (
            f"v2.3.24 hotfix: REST explain endpoint must propagate "
            f"curated_message from the helper. Got keys: {sorted(body)}"
        )
        assert body["curated_message"], (
            f"curated_message must be non-empty for a rule with an "
            f"authored error_message. Got: {body['curated_message']!r}"
        )
        assert "ISO 17442" in body["curated_message"]
        # Real LEI must also be present (round-4 P2-A regression guard).
        assert VALID_LEI in body.get("valid_examples", [])


class TestMcpExplainCarriesCuratedMessage:
    """The in-process MCP _tool_explain_error must propagate
    curated_message from explain_rule()."""

    def test_mcp_in_process_emits_curated_message(self):
        from opendqv import mcp_server

        async def call():
            out = await mcp_server._tool_explain_error({
                "contract": "mifid_transaction_report",
                "field": "reporting_firm_lei",
                "rule": "reporting_firm_lei_valid",
            })
            return json.loads(out[0].text)

        body = asyncio.run(call())
        assert "curated_message" in body, (
            f"v2.3.24 hotfix: in-process MCP _tool_explain_error must "
            f"propagate curated_message. Got keys: {sorted(body)}"
        )
        assert body["curated_message"], (
            f"curated_message must be non-empty. Got: "
            f"{body['curated_message']!r}"
        )
        assert "ISO 17442" in body["curated_message"]


class TestEmptyErrorMessageGracefulFallback:
    """Rules with no authored error_message should not crash the
    wrapper — curated_message can be absent or empty."""

    def test_rule_without_error_message_does_not_crash(self):
        from opendqv import mcp_server

        async def call():
            # Pick a contract+rule combo where error_message may be sparse.
            out = await mcp_server._tool_explain_error({
                "contract": "customer",
                "field": "email",
                "rule": "valid_email",
            })
            return json.loads(out[0].text)

        body = asyncio.run(call())
        # The response must be well-formed regardless of whether
        # curated_message is present.
        assert "explanation" in body
        # If present, must be a string (or empty string).
        if "curated_message" in body:
            assert isinstance(body["curated_message"], str)
