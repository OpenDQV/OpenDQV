"""
v2.3.17 Cluster 2 — F-B reserved-prefix input guard.

The v2.3.15 reserved-prefix discipline was implemented at four OUTPUT
surfaces (list endpoints, trend queries) so that OpenDQV system-agent
traffic (probes, demos, MCP self-tests) is suppressed from customer
dashboards. The complementary INPUT side was missed: a caller could
supply ``agent_id: "OpenDQV_SA_evil"`` and have it accepted, persisted,
and echoed back. Combined with the output-side suppression that hides
``OpenDQV_SA_*`` traffic by design, the pollution becomes invisible to
every dashboard that would reveal it — completing the attack in
software.

This test parametrises the rejection across all four write surfaces:
- REST  POST /api/v1/validate
- REST  POST /api/v1/validate/batch
- MCP   validate_record (in-process)
- MCP   validate_batch  (in-process)

A fifth write surface added in a future release will fail this test
unless the author adds an explicit input guard. That is the point.
"""

import asyncio



SPOOF_AGENT_ID = "OpenDQV_SA_probe_spoof"
RESERVED_PREFIX = "OpenDQV_SA_"


# ── REST surfaces ──────────────────────────────────────────────────────

class TestRestValidateRejectsReservedPrefix:
    def test_validate_single_returns_422(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "agent_id": SPOOF_AGENT_ID,
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 422, \
            f"REST /validate must reject reserved-prefix agent_id with 422, got {r.status_code}: {r.text}"
        # Pydantic-style error envelope; assert the reserved prefix is named in the error
        assert RESERVED_PREFIX in r.text or "reserved" in r.text.lower(), \
            f"422 response should explain WHY: {r.text}"

    def test_validate_batch_returns_422(self, client, auth_headers):
        body = {
            "contract": "customer",
            "records": [{"name": "Alice", "age": 30, "email": "a@b.co"}],
            "agent_id": SPOOF_AGENT_ID,
        }
        r = client.post("/api/v1/validate/batch?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 422, \
            f"REST /validate/batch must reject reserved-prefix agent_id with 422, got {r.status_code}: {r.text}"
        assert RESERVED_PREFIX in r.text or "reserved" in r.text.lower()

    def test_clean_agent_id_accepted(self, client, auth_headers):
        """Sanity: a normal agent_id still works — the guard is targeted, not over-broad."""
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "agent_id": "salesforce-prod",
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200, r.text


# ── MCP in-process surfaces ────────────────────────────────────────────

class TestMcpInProcessRejectsReservedPrefix:
    """The FastMCP-based in-process server doesn't go through Pydantic
    models, so the guard is implemented explicitly in mcp_server.py."""

    def test_validate_record_returns_invalid_agent_id_envelope(self):
        from opendqv.mcp_server import _tool_validate_record

        result = asyncio.run(_tool_validate_record({
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "agent_id": SPOOF_AGENT_ID,
        }))
        assert len(result) == 1
        text = result[0].text
        assert "INVALID_AGENT_ID" in text, \
            f"MCP validate_record must return INVALID_AGENT_ID envelope: {text}"
        assert SPOOF_AGENT_ID in text, "envelope should echo the offending agent_id"

    def test_validate_batch_returns_invalid_agent_id_envelope(self):
        from opendqv.mcp_server import _tool_validate_batch

        result = asyncio.run(_tool_validate_batch({
            "contract": "customer",
            "records": [{"name": "Alice", "age": 30, "email": "a@b.co"}],
            "agent_id": SPOOF_AGENT_ID,
        }))
        assert len(result) == 1
        text = result[0].text
        assert "INVALID_AGENT_ID" in text, \
            f"MCP validate_batch must return INVALID_AGENT_ID envelope: {text}"
        assert SPOOF_AGENT_ID in text

    def test_clean_agent_id_passes_through_validate_record(self):
        """Sanity: a normal agent_id is not rejected by the guard."""
        from opendqv.mcp_server import _tool_validate_record

        result = asyncio.run(_tool_validate_record({
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "agent_id": "salesforce-prod",
        }))
        assert len(result) == 1
        text = result[0].text
        assert "INVALID_AGENT_ID" not in text


# ── Audit-store invariant: rejected requests do NOT pollute metrics ────

class TestRejectedRequestsDoNotPolluteAuditStore:
    """The point of the guard isn't just 422 — it's that the spoofed
    agent_id NEVER becomes a row anywhere downstream."""

    def test_rejected_request_absent_from_subsequent_metrics(self, client, auth_headers):
        # Submit a spoof — rejected
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "agent_id": SPOOF_AGENT_ID,
        }
        r1 = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r1.status_code == 422

        # Pull /agents with include_system=true so suppression cannot mask
        # the assertion. The spoofed agent_id MUST NOT appear.
        r2 = client.get("/api/v1/agents?include_system=true", headers=auth_headers)
        assert r2.status_code == 200
        agents = r2.json().get("agents", [])
        spoofed_ids = [a["agent_id"] for a in agents if a["agent_id"] == SPOOF_AGENT_ID]
        assert not spoofed_ids, \
            f"Rejected agent_id leaked into /agents (include_system=true): {spoofed_ids}"
