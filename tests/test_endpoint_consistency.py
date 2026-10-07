"""
Endpoint consistency tests — ACT-049-EC series (reworked for 3.0.0).

3.0.0 removed context overrides: a contract carries no `contexts:` block, so
`context` on POST /validate and /validate/batch is a pure tag (quality stats,
audit, metrics) and never changes which rules run. Any tag value is accepted
and the verdict is identical to the untagged call.

The rule-selecting `?context=` query parameter was removed from every other
endpoint (contract read, JSON Schema, code generation, GX/ODCS export, batch
file upload); the OpenAPI guard below keeps it from coming back.
"""
import io

import pytest


# ── ACT-049-EC-001: `context` on validate is a tag — any value, same verdict ──

RECORD = {"name": "Alice", "age": 10}


def _validate(client, auth_headers, context):
    body = {"contract": "customer", "record": RECORD}
    if context is not None:
        body["context"] = context
    return client.post("/api/v1/validate", json=body, headers=auth_headers)


def _validate_batch(client, auth_headers, context):
    body = {"contract": "customer", "records": [RECORD]}
    if context is not None:
        body["context"] = context
    return client.post("/api/v1/validate/batch", json=body, headers=auth_headers)


def _verdict(endpoint_id, body):
    if endpoint_id == "validate":
        return body["valid"], body["errors"]
    return [(r["valid"], r["errors"]) for r in body["results"]]


TAG_ENDPOINTS = [("validate", _validate), ("validate_batch", _validate_batch)]


@pytest.mark.parametrize("endpoint_id,call", TAG_ENDPOINTS, ids=[ep[0] for ep in TAG_ENDPOINTS])
@pytest.mark.parametrize("tag", ["nonexistent_ctx_xyz", "kids_app", "demo"])
def test_context_tag_is_accepted_and_never_changes_the_verdict(
    endpoint_id, call, tag, client, auth_headers
):
    untagged = call(client, auth_headers, None)
    tagged = call(client, auth_headers, tag)
    assert untagged.status_code == 200, untagged.text[:200]
    assert tagged.status_code == 200, (
        f"Endpoint '{endpoint_id}' rejected context tag {tag!r} with {tagged.status_code}: "
        f"{tagged.text[:200]}"
    )
    assert _verdict(endpoint_id, tagged.json()) == _verdict(endpoint_id, untagged.json())
    assert "context_warning" not in tagged.json()


# ── ACT-049-EC-002: no other endpoint declares a rule-selecting `context` ─────

# (method, path) of every endpoint that took `?context=` to select override
# rules before 3.0.0.
REMOVED_CONTEXT_PARAM = [
    ("get",  "/api/v1/contracts/{name}"),
    ("get",  "/api/v1/contracts/{name}/jsonschema"),
    ("post", "/api/v1/generate"),
    ("get",  "/api/v1/export/gx/{contract_name}"),
    ("get",  "/api/v1/export/odcs/{contract_name}"),
    ("post", "/api/v1/validate/batch/file"),
]


@pytest.mark.parametrize("method,path", REMOVED_CONTEXT_PARAM, ids=[p for _, p in REMOVED_CONTEXT_PARAM])
def test_removed_context_query_param_is_not_declared(method, path, client):
    spec = client.app.openapi()
    op = spec["paths"][path][method]
    params = [p["name"] for p in op.get("parameters", [])]
    assert "context" not in params, f"{method.upper()} {path} declares `context` again: {params}"


def test_batch_file_ignores_a_stray_context_query_param(client, auth_headers):
    """An old client still sending `?context=` gets the base verdict, not an error."""
    def run(params):
        return client.post(
            "/api/v1/validate/batch/file",
            params=params,
            files={"file": ("test.csv", io.BytesIO(b"name,age\nAlice,10\n"), "text/csv")},
            headers=auth_headers,
        )
    plain = run({"contract": "customer"})
    stray = run({"contract": "customer", "context": "kids_app"})
    assert plain.status_code == stray.status_code == 200, (plain.text[:200], stray.text[:200])
    assert stray.json()["summary"] == plain.json()["summary"]
