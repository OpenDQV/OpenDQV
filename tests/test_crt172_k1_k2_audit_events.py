"""
tests/test_crt172_k1_k2_audit_events.py — CRT172/K1+K2 acceptance.

Pins the audit event surface introduced in v2.3.9:
    GET /api/v1/audit/events                  — cursor-paginated list (K2)
    GET /api/v1/audit/events/{event_id}       — single fetch (K1)

Before v2.3.9:
    `event_id` was returned on every /validate response, but no
    endpoint accepted it back. There was no row-level retrieval
    of audit events; only aggregated stats. An auditor wanting to
    fetch "the validation event with id X" or "all events for
    caller_principal Y in the last hour" had no API surface.

From v2.3.9:
    K2 lists events with optional filters (contract, version,
    context, since, until, agent_id, caller_principal, valid,
    mode), cursor-paginated by (recorded_at, integer PK id).
    `valid=true` requires `failed=0 AND total_records>0` —
    vacuous zero-record rows do not match the filter (Sonnet
    review correctness flag).
    K1 returns the full row including JSON-decoded
    rule_failure_counts. Both auth-gated to admin + auditor.
"""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from opendqv.core.quality_stats import QualityStats


# ── In-process tests against QualityStats helper directly ──────────────
# Keep these independent of the running app so we can pin schema-level
# behaviour (cursor monotonicity, valid filter, has_more lookahead)
# without the integration-test seeding overhead.


@pytest.fixture
def qs(tmp_path):
    db = tmp_path / "audit.db"
    return QualityStats(str(db))


def _record(qs: QualityStats, **overrides):
    defaults = dict(
        contract_name="customer",
        contract_version="1.0",
        context=None,
        total=1,
        passed=1,
        failed=0,
        rule_failure_counts={},
        agent_id="",
        mode="enforcement",
        event_id="",
        caller_principal="",
    )
    defaults.update(overrides)
    qs.record_batch(**defaults)


class TestGetEventByIdHelper:

    def test_returns_full_row_with_decoded_rfc(self, qs):
        _record(qs, event_id="evt-1", failed=2, total=5, passed=3,
                rule_failure_counts={"r1": 1, "r2": 1})
        row = qs.get_event("evt-1")
        assert row is not None
        assert row["event_id"] == "evt-1"
        assert row["total_records"] == 5
        assert row["failed"] == 2
        assert row["rule_failure_counts"] == {"r1": 1, "r2": 1}

    def test_returns_none_for_unknown_event_id(self, qs):
        _record(qs, event_id="evt-1")
        assert qs.get_event("not-exists") is None

    def test_corrupt_rfc_json_falls_back_to_empty_dict(self, qs):
        """Defensive: a row with malformed JSON in rule_failure_counts must
        not crash the lookup."""
        _record(qs, event_id="evt-bad")
        # Corrupt the rule_failure_counts column directly.
        conn = sqlite3.connect(qs._db_path)
        conn.execute("UPDATE quality_stats SET rule_failure_counts = ? WHERE event_id = ?",
                     ("not-valid-json", "evt-bad"))
        conn.commit()
        conn.close()
        row = qs.get_event("evt-bad")
        assert row is not None
        assert row["rule_failure_counts"] == {}


class TestListEventsHelper:

    def test_empty_table_returns_empty(self, qs):
        events, has_more = qs.list_events()
        assert events == []
        assert has_more is False

    def test_orders_by_recorded_at_desc_then_id_desc(self, qs):
        for i in range(5):
            _record(qs, event_id=f"evt-{i}")
            time.sleep(0.001)
        events, _ = qs.list_events(limit=10)
        # Most recent first.
        assert [e["event_id"] for e in events] == [f"evt-{i}" for i in (4, 3, 2, 1, 0)]

    def test_has_more_true_when_more_rows_exist(self, qs):
        for i in range(5):
            _record(qs, event_id=f"evt-{i}")
            time.sleep(0.001)
        events, has_more = qs.list_events(limit=3)
        assert len(events) == 3
        assert has_more is True

    def test_has_more_false_at_exact_boundary(self, qs):
        """If exactly `limit` rows exist, has_more is False — limit+1 lookahead
        returned only `limit` rows, so there is nothing further to page."""
        for i in range(3):
            _record(qs, event_id=f"evt-{i}")
            time.sleep(0.001)
        events, has_more = qs.list_events(limit=3)
        assert len(events) == 3
        assert has_more is False

    def test_cursor_pagination_walks_full_set_in_order(self, qs):
        for i in range(7):
            _record(qs, event_id=f"evt-{i}")
            time.sleep(0.001)
        seen: list = []
        cursor_r, cursor_id = None, None
        while True:
            events, has_more = qs.list_events(
                limit=2,
                cursor_recorded_at=cursor_r,
                cursor_id=cursor_id,
            )
            seen.extend(events)
            if not has_more or not events:
                break
            cursor_r, cursor_id = events[-1]["recorded_at"], events[-1]["id"]
        assert [e["event_id"] for e in seen] == [f"evt-{i}" for i in (6, 5, 4, 3, 2, 1, 0)]

    def test_filter_by_contract(self, qs):
        _record(qs, event_id="a", contract_name="customer")
        _record(qs, event_id="b", contract_name="invoice")
        events, _ = qs.list_events(contract="customer")
        assert [e["event_id"] for e in events] == ["a"]

    def test_filter_by_caller_principal(self, qs):
        _record(qs, event_id="a", caller_principal="alice")
        _record(qs, event_id="b", caller_principal="bob")
        events, _ = qs.list_events(caller_principal="alice")
        assert [e["event_id"] for e in events] == ["a"]

    def test_valid_true_excludes_zero_record_rows(self, qs):
        """Sonnet correctness flag: valid=True must require total_records > 0,
        not just failed = 0. A zero-record row trivially satisfies failed=0
        but represents no actual validation."""
        _record(qs, event_id="real-pass", total=1, passed=1, failed=0)
        _record(qs, event_id="vacuous-zero", total=0, passed=0, failed=0)
        events, _ = qs.list_events(valid=True)
        ids = {e["event_id"] for e in events}
        assert "real-pass" in ids
        assert "vacuous-zero" not in ids

    def test_valid_false_filters_to_failed_rows(self, qs):
        _record(qs, event_id="pass", total=1, passed=1, failed=0)
        _record(qs, event_id="fail", total=1, passed=0, failed=1)
        events, _ = qs.list_events(valid=False)
        assert [e["event_id"] for e in events] == ["fail"]


# ── REST API integration tests ─────────────────────────────────────────


class TestK2ListEndpointAuth:

    def test_admin_can_list(self, client: TestClient, admin_headers):
        resp = client.get("/api/v1/audit/events", headers=admin_headers)
        assert resp.status_code == 200

    def test_auditor_can_list(self, client: TestClient, auditor_headers):
        resp = client.get("/api/v1/audit/events", headers=auditor_headers)
        assert resp.status_code == 200

    def test_validator_role_forbidden(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/audit/events", headers=auth_headers)
        assert resp.status_code == 403

    def test_reader_role_forbidden(self, client: TestClient, reader_headers):
        resp = client.get("/api/v1/audit/events", headers=reader_headers)
        assert resp.status_code == 403


class TestK1FetchEndpointAuth:

    def test_admin_can_fetch_404_for_unknown(self, client: TestClient, admin_headers):
        resp = client.get("/api/v1/audit/events/not-a-real-id", headers=admin_headers)
        assert resp.status_code == 404

    def test_validator_forbidden(self, client: TestClient, auth_headers):
        resp = client.get("/api/v1/audit/events/anything", headers=auth_headers)
        assert resp.status_code == 403


class TestK2ResponseEnvelope:

    def test_envelope_always_includes_effective_since_and_has_more(
        self, client: TestClient, admin_headers
    ):
        body = client.get("/api/v1/audit/events", headers=admin_headers).json()
        assert "events" in body
        assert "has_more" in body
        assert "next_cursor" in body
        assert "effective_since" in body
        assert "limit" in body
        assert isinstance(body["has_more"], bool)
        assert body["limit"] == 100

    def test_default_window_is_24h_and_echoed(self, client: TestClient, admin_headers):
        from datetime import datetime, timezone
        body = client.get("/api/v1/audit/events", headers=admin_headers).json()
        eff = body["effective_since"]
        # Parse and confirm it is roughly 24 hours ago (allow 5 minute slack).
        eff_dt = datetime.fromisoformat(eff)
        delta_hours = (datetime.now(timezone.utc) - eff_dt).total_seconds() / 3600
        assert 23.5 < delta_hours < 24.5

    def test_explicit_since_is_echoed(self, client: TestClient, admin_headers):
        explicit = "2026-01-01T00:00:00+00:00"
        body = client.get(
            "/api/v1/audit/events",
            params={"since": explicit},
            headers=admin_headers,
        ).json()
        assert body["effective_since"] == explicit

    def test_invalid_cursor_returns_400(self, client: TestClient, admin_headers):
        resp = client.get(
            "/api/v1/audit/events?cursor=not-a-valid-cursor",
            headers=admin_headers,
        )
        assert resp.status_code == 400


class TestK1K2EndToEnd:
    """Validate a record, then look it up by event_id and find it in the listing."""

    def test_validate_then_fetch_and_list(
        self, client: TestClient, admin_headers, auth_headers
    ):
        v = client.post(
            "/api/v1/validate",
            json={"record": {"name": "A", "email": "a@example.com", "age": 25},
                  "contract": "customer"},
            headers=auth_headers,
        )
        assert v.status_code == 200
        event_id = v.json()["event_id"]

        # Background-task persistence runs out-of-band; brief poll until the
        # row lands or the assertion times out.
        deadline = time.monotonic() + 2.0
        detail = None
        while time.monotonic() < deadline:
            r = client.get(f"/api/v1/audit/events/{event_id}", headers=admin_headers)
            if r.status_code == 200:
                detail = r.json()
                break
            time.sleep(0.05)
        assert detail is not None, "audit row never landed"
        assert detail["event_id"] == event_id
        assert detail["contract"] == "customer"
        assert detail["total_records"] == 1
        assert "rule_failure_counts" in detail

        # And it should appear in the listing.
        listing = client.get("/api/v1/audit/events?limit=50", headers=admin_headers).json()
        ids = [e["event_id"] for e in listing["events"]]
        assert event_id in ids

    def test_cursor_round_trip_via_http(self, client: TestClient, admin_headers, auth_headers):
        # Generate three events.
        ids: list = []
        for i in range(3):
            v = client.post(
                "/api/v1/validate",
                json={"record": {"name": f"r{i}", "email": f"r{i}@x.com", "age": 25},
                      "contract": "customer"},
                headers=auth_headers,
            )
            ids.append(v.json()["event_id"])
            time.sleep(0.01)

        # Wait for persistence.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            listing = client.get(
                "/api/v1/audit/events?limit=100", headers=admin_headers
            ).json()
            if all(eid in [e["event_id"] for e in listing["events"]] for eid in ids):
                break
            time.sleep(0.05)

        page1 = client.get("/api/v1/audit/events?limit=2", headers=admin_headers).json()
        assert len(page1["events"]) == 2
        # If has_more, next_cursor must be set; otherwise it must be null.
        if page1["has_more"]:
            assert page1["next_cursor"] is not None
            page2 = client.get(
                f"/api/v1/audit/events?limit=2&cursor={page1['next_cursor']}",
                headers=admin_headers,
            ).json()
            page1_ids = [e["event_id"] for e in page1["events"]]
            page2_ids = [e["event_id"] for e in page2["events"]]
            # No overlap between consecutive pages.
            assert set(page1_ids).isdisjoint(set(page2_ids))


class TestSecretsNotInResponse:
    """Sentinel: the audit event surface must not leak any secret-bearing
    field. event_id, agent_id, caller_principal, mode are all expected.
    The contract column names that are forbidden are pinned here."""

    FORBIDDEN_KEYS = frozenset({
        "secret_key", "db_url", "join_token", "mcp_token", "authorization",
    })

    def _flatten_keys(self, obj, prefix=""):
        keys = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.add(k.lower())
                keys.update(self._flatten_keys(v, f"{prefix}.{k}"))
        elif isinstance(obj, list):
            for item in obj:
                keys.update(self._flatten_keys(item, prefix))
        return keys

    def test_list_response_has_no_forbidden_keys(self, client: TestClient, admin_headers):
        body = client.get("/api/v1/audit/events", headers=admin_headers).json()
        keys = self._flatten_keys(body)
        leaks = keys & self.FORBIDDEN_KEYS
        assert not leaks, f"Forbidden keys leaked into list response: {leaks}"
