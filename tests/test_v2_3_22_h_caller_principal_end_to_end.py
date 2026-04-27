"""
v2.3.22 Cluster H — caller_principal end-to-end token-mode smoke.

The Pilot's specific concern after v2.3.20: "are we sure [token mode]
works?" v2.3.20 Cluster G's smoke proved the validate route captures
JWT `sub` into `caller_principal` ON THE RESPONSE. That left a gap:
does the captured value persist to the audit store, AND does
`get_audit_event(event_id)` return it?

This test closes the gap end-to-end:
  validate (with Bearer)
    → response carries caller_principal
    → quality_stats row carries caller_principal
    → get_audit_event returns caller_principal

Plus the /health endpoint test (auth_mode field is already exposed,
verified here as a regression guard).

Plus a confirmation that the MCP docstring already qualifies the
"cannot be spoofed" claim (Sonnet's pre-impl review found this is
already accurate; assertion guards against regression).

Sonnet's pre-impl review confirmed no fixture isolation gap. TestClient
uses the same SQLite tempdir conftest sets via OPENDQV_DB_PATH, and
deps.py:25 constructs _quality_stats from config.DB_PATH at import-time
after conftest has set the env var.
"""



class TestCallerPrincipalEndToEnd:
    def test_jwt_sub_persists_through_validate_audit_chain(self, client):
        """The reviewer's P0 #1: prove caller_principal flows from JWT
        sub through to get_audit_event. Single test, full chain."""
        from opendqv.security.auth import create_pat

        principal = "alice@bank.example.com"
        validator_token = create_pat(principal, role="validator")["token"]
        admin_token = create_pat("auditor-test", role="admin")["token"]

        # Step 1: validate with Bearer — caller_principal lands on response.
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post(
            "/api/v1/validate?allow_draft=true",
            json=body,
            headers={"Authorization": f"Bearer {validator_token}"},
        )
        assert r.status_code == 200, r.text
        validate_resp = r.json()
        assert validate_resp["caller_principal"] == principal
        event_id = validate_resp["event_id"]
        assert event_id, "validate must return event_id for audit replay"

        # Step 2: read back via get_audit_event — caller_principal must
        # match. This is the chain the v2.3.20 Cluster G smoke missed.
        r2 = client.get(
            f"/api/v1/audit/events/{event_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r2.status_code == 200, r2.text
        event = r2.json()
        assert event["caller_principal"] == principal, (
            f"v2.3.22 P0 #1 regression: validate captured "
            f"caller_principal={principal!r} on response, but the persisted "
            f"audit row has caller_principal={event.get('caller_principal')!r}. "
            f"The chain is broken between validate response and audit log "
            f"persistence — fails the regulator's 'who wrote which record' "
            f"audit requirement."
        )
        # Belt and suspenders — the value is the JWT sub, never anonymous.
        assert event["caller_principal"] != "anonymous"

    def test_no_bearer_token_returns_401_in_token_mode(self, client):
        """Sanity: in AUTH_MODE=token (test default per conftest line 22),
        validate without a Bearer returns 401 — confirming the auth
        gate actually fires. If this test passes (i.e. gets 200), the
        engine is silently auth-bypassed and the principal capture is
        decorative."""
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body)
        assert r.status_code == 401, (
            f"AUTH_MODE=token must reject Bearer-less requests with 401; "
            f"got {r.status_code}. If this test fails, the engine is "
            f"silently in open mode and caller_principal is a sham."
        )

    def test_distinct_jwt_subs_persist_distinct_caller_principals(self, client):
        """Two different JWTs → two different audit events with
        distinct caller_principal values. Catches a regression where a
        future refactor accidentally hardcoded or shared the value."""
        from opendqv.security.auth import create_pat

        token_a = create_pat("alice@bank.example.com", role="validator")["token"]
        token_b = create_pat("bob@bank.example.com", role="validator")["token"]
        admin = create_pat("admin-test-distinct", role="admin")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "X", "age": 30, "email": "x@x.co"},
        }
        ra = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        rb = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert ra.status_code == 200 and rb.status_code == 200

        ea = client.get(
            f"/api/v1/audit/events/{ra.json()['event_id']}",
            headers={"Authorization": f"Bearer {admin}"},
        )
        eb = client.get(
            f"/api/v1/audit/events/{rb.json()['event_id']}",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert ea.json()["caller_principal"] == "alice@bank.example.com"
        assert eb.json()["caller_principal"] == "bob@bank.example.com"
        assert ea.json()["caller_principal"] != eb.json()["caller_principal"]


class TestHealthEndpointAuthMode:
    """v2.3.22 Cluster H: `/health` exposes `auth_mode` so operators
    of a deployed engine can see whether it's in open or token mode
    without grep-ing process env. Sonnet confirmed this already exists
    (main.py); test guards against regression."""

    def test_health_includes_auth_mode_field(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert "auth_mode" in body, (
            "v2.3.22 Cluster H: /health must expose auth_mode for "
            "operator visibility into AUTH_MODE config. Reviewer's "
            "P0 #1 framing depends on operators being able to see "
            "what mode is active."
        )
        assert body["auth_mode"] in ("token", "open"), (
            f"auth_mode must be 'token' or 'open'; got {body['auth_mode']!r}"
        )

    def test_health_includes_secret_key_insecure_flag(self, client):
        """Companion field — operators of a regulated deployment need
        to see if SECRET_KEY is the default (insecure) or replaced."""
        r = client.get("/health")
        body = r.json()
        assert "secret_key_insecure" in body
        assert isinstance(body["secret_key_insecure"], bool)


class TestMcpDocstringAlreadyQualifiesAuthClaim:
    """v2.3.22 Cluster H: Sonnet's pre-impl review found that the MCP
    docstring at `mcp_server.py` ALREADY qualifies "cannot be spoofed"
    with "or 'anonymous' when AUTH_MODE=open." This test guards against
    a future refactor accidentally dropping the qualifier — which would
    re-introduce the doc/reality drift the reviewer flagged."""

    def test_validate_record_tool_description_qualifies_auth_mode(self):
        """The validate_record tool's description must mention either
        'AUTH_MODE' or 'anonymous' — i.e. the qualifier that closes
        the reviewer's 'cannot be spoofed' over-claim concern."""
        import asyncio
        from opendqv.mcp_server import server
        from mcp.types import ListToolsRequest

        handlers = server.request_handlers
        result = asyncio.run(
            handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
        )
        tools = {t.name: t for t in result.root.tools}
        validate_record = tools["validate_record"]
        desc = validate_record.description or ""
        assert "AUTH_MODE" in desc or "anonymous" in desc.lower(), (
            f"v2.3.22 Cluster H regression: validate_record description "
            f"no longer qualifies the caller_principal trust claim with "
            f"AUTH_MODE / anonymous fallback. This is the doc/reality "
            f"drift the round-2 reviewer flagged. Description: {desc!r}"
        )
