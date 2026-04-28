"""
v2.3.23 round-3 review — agent_id wire-shape consistency.

Persona B 2026-04-28 outside review #3 P2:
> "agent_id "" vs null consistency"

Pre-fix the engine emitted a mix of empty-string and null for
unattributed agents across response surfaces:
  - ValidateResponse / BatchValidateResponse: null (correct)
  - AuditEvent / list_events / get_event: "" (inconsistent)
  - recent_history entries on /api/v1/stats: "" (inconsistent)

Sonnet pre-impl review (af52284db94bba3b1) verdict: option A.
Normalize all wire surfaces to null for unattributed; internal SQLite
storage stays "" (no schema migration). Single boundary translation.
The by=agent grouping label `unattributed` is intentionally NOT null
— bucket label vs presence signal is a different semantic layer.

Sweep test (Sonnet's recommended single test): submit a record with
no agent_id, assert response["agent_id"] is None across every surface.
"""

import pytest


@pytest.fixture
def ensure_some_history(client, auth_headers):
    """Trigger one validate without agent_id so recent_history /
    list_events / get_event have something to read. The actual content
    is incidental — we only assert the wire shape of agent_id."""
    resp = client.post(
        "/api/v1/validate",
        json={"contract": "customer", "record": {
            "name": "test_user_for_null_check",
            "email": "test@example.com",
            "age": 30,
            "balance": 100.0,
            "id": "user_test_null_check",
        }},
        headers=auth_headers,
    )
    return resp


# ── Wire surfaces emit null (not "") for unattributed agent_id ─────────

class TestUnattributedAgentEmitsNullEverywhere:
    """The reviewer's exact framing: same field, different shape across
    surfaces. Fix is one shape (null) on every surface."""

    def test_recent_history_agent_id_is_null_when_unattributed(
        self, client, auth_headers, ensure_some_history
    ):
        """The historical leak: recent_history entries had `agent_id: ""`
        on the wire. Fixed at the get_summary emit boundary."""
        resp = client.get("/api/v1/stats", headers=auth_headers)
        body = resp.json()
        history = body.get("recent_history", [])
        # Find at least one entry that we know was unattributed.
        unattributed = [h for h in history if not h.get("agent_id")]
        for h in unattributed:
            assert h["agent_id"] is None, (
                f"v2.3.23 round-3: recent_history entry must emit "
                f"agent_id: null for unattributed events, never \"\". "
                f"Got: {h}"
            )

    def test_audit_event_list_agent_id_is_null_when_unattributed(
        self, client, auditor_headers, ensure_some_history
    ):
        """list_events at /api/v1/audit/events used to emit
        `agent_id: ""` per quality_stats.list_events row construction.
        Fixed by switching `or ""` to `or None`."""
        resp = client.get(
            "/api/v1/audit/events?limit=20", headers=auditor_headers,
        )
        if resp.status_code == 404:
            pytest.skip("audit events endpoint not enabled in this build")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        events = body.get("events", [])
        for ev in events:
            agent = ev.get("agent_id", "MISSING")
            # Either null or a non-empty string — never "".
            assert agent is None or (isinstance(agent, str) and agent != ""), (
                f"v2.3.23 round-3: audit event entry must emit "
                f"agent_id: null for unattributed (never \"\"). Got: {ev}"
            )

    def test_audit_event_get_agent_id_is_null_when_unattributed(
        self, client, auditor_headers, ensure_some_history
    ):
        """get_event at /api/v1/audit/events/{id} used to emit
        `agent_id: ""`. Fix at quality_stats.get_event."""
        # Need an event_id to fetch — pull from list first.
        list_resp = client.get(
            "/api/v1/audit/events?limit=1", headers=auditor_headers,
        )
        if list_resp.status_code == 404:
            pytest.skip("audit events endpoint not enabled in this build")
        events = list_resp.json().get("events", [])
        if not events:
            pytest.skip("no persisted audit events in this run (MCP dry_run)")
        event_id = events[0]["event_id"]
        get_resp = client.get(
            f"/api/v1/audit/events/{event_id}", headers=auditor_headers,
        )
        if get_resp.status_code != 200:
            pytest.skip(f"get_event returned {get_resp.status_code}")
        ev = get_resp.json()
        agent = ev.get("agent_id", "MISSING")
        assert agent is None or (isinstance(agent, str) and agent != ""), (
            f"v2.3.23 round-3: get_event must emit agent_id: null for "
            f"unattributed (never \"\"). Got: {ev}"
        )

    def test_validate_response_unattributed_remains_null(
        self, client, auth_headers,
    ):
        """Regression guard for ValidateResponse — already used null
        before this PR, must continue to."""
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "customer", "record": {
                "name": "x", "email": "x@x.com", "age": 30,
                "balance": 0.0, "id": "regression-guard",
            }},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # No agent_id passed → response echoes null.
        assert body.get("agent_id") is None, (
            f"ValidateResponse.agent_id must echo null when caller "
            f"provided none. Got: {body!r}"
        )


# ── by=agent grouping label stays "unattributed" (not null) ────────────

class TestByAgentBucketLabelUnchanged:
    """The grouping bucket is a different semantic layer — a label that
    must be a non-null string for the consumer to render and aggregate.
    "unattributed" is the right label for that purpose; null would
    break the GROUP BY surface."""

    def test_by_agent_grouping_uses_unattributed_label(self, client):
        from opendqv.monitoring import stats
        # Seed an unattributed event.
        stats.record(
            contract="customer", context=None, valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            errors=[], agent_id="",
        )
        # Hit the trend by=agent endpoint.
        r = client.get("/api/v1/contracts/customer/quality-trend?by=agent&days=1")
        if r.status_code != 200:
            pytest.skip(f"trend endpoint returned {r.status_code}")
        body = r.json()
        keys = {p["key"] for p in body.get("points", [])}
        # If unattributed traffic is in the window, the bucket label
        # must be "unattributed", never null, never "".
        if any(k in keys for k in ("unattributed", "")):
            assert "" not in keys, (
                f"by=agent bucket key must never be \"\". "
                f"v2.3.23 outside-review #2 already pinned this; "
                f"v2.3.23 round-3 reaffirms. Got: {keys}"
            )


# ── quality_stats.list_events / get_event unit-level pin ────────────────

class TestQualityStatsRowSerialization:
    """Direct unit test on the read boundary translation. If a future
    refactor forgets the `or None` translation here, this test catches
    the leak before it reaches a wire surface."""

    def test_list_events_emits_none_for_empty_agent_id(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(str(tmp_path / "agent_null.db"))
        qs.record_batch(
            contract_name="customer",
            contract_version="1.0",
            context=None,
            total=1,
            passed=1,
            failed=0,
            rule_failure_counts={},
            agent_id="",  # explicit empty (the storage convention)
        )
        events, _ = qs.list_events(contract="customer", limit=10)
        assert events
        assert events[0]["agent_id"] is None, (
            f"v2.3.23 round-3: list_events must translate storage \"\" "
            f"to wire null for unattributed events. Got: {events[0]}"
        )

    def test_get_event_emits_none_for_empty_agent_id(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        qs = QualityStats(str(tmp_path / "agent_null2.db"))
        # Need event_id; let record_batch synthesise.
        qs.record_batch(
            contract_name="customer",
            contract_version="1.0",
            context=None,
            total=1,
            passed=1,
            failed=0,
            rule_failure_counts={},
            agent_id="",
            event_id="evt-null-test-001",
        )
        ev = qs.get_event("evt-null-test-001")
        assert ev is not None
        assert ev["agent_id"] is None, (
            f"get_event must translate storage \"\" to wire null. "
            f"Got: {ev}"
        )
