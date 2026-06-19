"""
v2.3.23 round-3 review — effective_since description disambiguates
default-window truncation from retention boundary.

Persona B 2026-04-28 outside review #3 P2:
> "effective_since retention boundary opacity"

The reviewer couldn't tell from the field description whether
effective_since marks "data starts here" (i.e. the engine has no
events older than this — a retention boundary) or "this is the lower
bound the query applied" (default-window truncation). They are
operationally different: the former implies no older events exist;
the latter implies older events may exist but were excluded.

Fix: re-word AuditEventListResponse.effective_since description to:
  - state explicitly it is the lower bound applied to this query
  - explain it equals `since` when supplied; (now-24h) otherwise
  - explicitly disclaim that it is NOT a retention boundary
  - point to the workaround (pass explicit `since`)

Doc-only fix — no behaviour change.
"""


class TestEffectiveSinceDescription:
    def test_response_field_disclaims_retention_boundary(self):
        from opendqv.api.models import AuditEventListResponse
        field = AuditEventListResponse.model_fields["effective_since"]
        desc = (field.description or "").lower()
        assert "lower" in desc, (
            f"v2.3.23 round-3: effective_since description must say "
            f"'lower-bound' to anchor reader on what the field is. "
            f"Got: {desc!r}"
        )
        assert "not a retention boundary" in desc, (
            f"v2.3.23 round-3: description must explicitly disclaim "
            f"retention semantics so a regulated consumer doesn't read "
            f"the field as 'data starts here'. Got: {desc!r}"
        )
        assert "default-window" in desc or "default window" in desc, (
            f"description must name the truncation by its formal name. "
            f"Got: {desc!r}"
        )

    def test_query_param_description_matches(self):
        """The `since` query param description must point to the same
        explanation so a consumer reading either reaches the same model.

        Read from the OpenAPI schema rather than introspecting
        ``app.routes`` directly: fastapi 0.137 wraps included routers in
        an internal ``_IncludedRouter`` object instead of flattening their
        routes onto the parent app, so the old ``isinstance(r, APIRoute)``
        scan no longer finds prefixed routes. The published schema is the
        consumer-facing contract and is stable across fastapi internals.
        """
        from fastapi.testclient import TestClient
        from opendqv.main import app

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
        path_item = next(
            item for path, item in spec["paths"].items()
            if path.endswith("/audit/events") and "get" in item
        )
        since_param = next(
            p for p in path_item["get"]["parameters"] if p["name"] == "since"
        )
        desc = (since_param.get("description") or "").lower()
        assert "not a retention boundary" in desc, (
            f"`since` query param description must mirror the response "
            f"field's retention disclaimer. Got: {desc!r}"
        )
