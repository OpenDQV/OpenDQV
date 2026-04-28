"""
v2.3.23 P1-6 — validate response carries `persisted: bool` so a caller
can tell whether the event_id resolves in list_audit_events.

Persona B inside-view 2026-04-28:
  "MCP validations are silently un-audited. Despite returning event_id
   and mode: enforcement, none of my MCP-channel validations appeared
   in list_audit_events. The dry-run-via-MCP behaviour is documented
   in the tool description, but the response payload still says mode:
   enforcement and only would_have_failed: true (subtle) signals the
   dry-run state. Customer impact: a team that runs validations via
   MCP and trusts the audit log for evidence will discover their MCP
   traffic was never recorded — surfaced too late, in a regulator
   inspection."

Sonnet's pre-impl review (a5f385c20eba96e85): "Option 1: persisted:
bool. `persisted` describes the OUTCOME — what regulators actually
care about. `dry_run` echoes the request flag and is caller-
perspective, not record-perspective. Mode value change is rejected:
`mode` has existing semantics (enforcement vs observation_only)
distinct from dry-run. `persisted: false` is unambiguous: event_id
is real (idempotency token), event is not in the audit log."

Outcome-coupled test (Sonnet's regression guard requirement): one
test where dry_run=True asserts persisted: false AND asserts the
event_id is absent from list_audit_events.
"""



class TestPersistedFlagOnValidateResponse:
    def test_dry_run_response_carries_persisted_false(self, client):
        from opendqv.security.auth import create_pat
        validator = create_pat("p1-6-test-validator", role="validator")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "dry_run": True,
        }
        r = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        assert "persisted" in resp, (
            f"v2.3.23 P1-6: validate response must carry persisted "
            f"field so a caller can tell whether the event_id "
            f"resolves in the audit log. Got keys: {list(resp.keys())}"
        )
        assert resp["persisted"] is False, (
            f"dry_run=True must return persisted: false. Got: "
            f"{resp['persisted']!r}"
        )

    def test_persistent_validate_response_carries_persisted_true(self, client):
        from opendqv.security.auth import create_pat
        validator = create_pat("p1-6-persist-validator", role="validator")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Bob", "age": 25, "email": "b@b.co"},
        }  # default dry_run=False
        r = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        assert resp["persisted"] is True, (
            f"v2.3.23 P1-6: dry_run=False must return persisted: true. "
            f"Got: {resp['persisted']!r}"
        )

    def test_dry_run_event_id_does_not_resolve_in_audit_log(self, client):
        """The outcome-coupled test Sonnet directed: persisted: false
        AND event_id absent from list_audit_events."""
        from opendqv.security.auth import create_pat
        validator = create_pat("p1-6-outcome-validator", role="validator")["token"]
        admin = create_pat("p1-6-outcome-admin", role="admin")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Charlie", "age": 35, "email": "c@b.co"},
            "dry_run": True,
        }
        r1 = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r1.status_code == 200
        resp = r1.json()
        assert resp["persisted"] is False
        event_id = resp["event_id"]

        # Try to fetch the audit row — must 404.
        r2 = client.get(
            f"/api/v1/audit/events/{event_id}",
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert r2.status_code == 404, (
            f"v2.3.23 P1-6 outcome guard: dry_run validate returned "
            f"event_id={event_id!r} but audit lookup succeeded — the "
            f"persisted: false claim contradicts the audit log. Got "
            f"status: {r2.status_code}, body: {r2.text}"
        )

    def test_batch_dry_run_carries_persisted_false(self, client):
        from opendqv.security.auth import create_pat
        validator = create_pat("p1-6-batch-validator", role="validator")["token"]
        body = {
            "contract": "customer",
            "records": [
                {"name": "Alice", "age": 30, "email": "a@b.co"},
                {"name": "Bob", "age": 25, "email": "b@b.co"},
            ],
            "dry_run": True,
        }
        r = client.post(
            "/api/v1/validate/batch?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {validator}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        assert "persisted" in resp, list(resp.keys())
        assert resp["persisted"] is False
