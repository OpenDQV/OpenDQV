"""
Endpoint consistency tests — ACT-049-EC series.

Any endpoint that accepts a `context` parameter and calls get_rules_with_context()
must return 422 for an unknown context. This test family is parametrised over
every such endpoint so that when a new one is added, the developer must either
add it to the list below or make a conscious decision to exclude it.

Motivation: /validate/batch/file was missing the UnknownContextError
try/except that existed on the other two validate endpoints. It was found by
code review after external stress-testing, not by our own tests.

The parametrised structure means a new endpoint with a missing guard will cause
this test file to fail (by omission) rather than silently pass.
"""
import io
import pytest


# ── Registry of all context-accepting endpoints ──────────────────────────────
#
# Format: (endpoint_id, method, url_template, kwargs_factory)
# kwargs_factory receives the TestClient and auth_headers and returns the
# keyword arguments to pass to client.request().
#
# To add a new endpoint: append an entry here. The test will automatically
# verify that unknown context → 422.

def _validate_kwargs(client, auth_headers):
    return dict(
        json={"contract": "customer", "context": "nonexistent_ctx_xyz", "record": {"name": "Alice"}},
        headers=auth_headers,
    )

def _validate_batch_kwargs(client, auth_headers):
    return dict(
        json={"contract": "customer", "context": "nonexistent_ctx_xyz", "records": [{"name": "Alice"}]},
        headers=auth_headers,
    )

def _validate_batch_file_kwargs(client, auth_headers):
    csv_content = b"name,email\nAlice,alice@example.com\n"
    return dict(
        params={"contract": "customer", "context": "nonexistent_ctx_xyz"},
        files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
        headers=auth_headers,
    )

def _generate_kwargs(client, auth_headers):
    return dict(
        params={"contract_name": "customer", "target": "snowflake", "context": "nonexistent_ctx_xyz"},
        headers=auth_headers,
    )

def _export_gx_kwargs(client, auth_headers):
    return dict(
        params={"context": "nonexistent_ctx_xyz"},
        headers=auth_headers,
    )

def _export_odcs_kwargs(client, auth_headers):
    return dict(
        params={"context": "nonexistent_ctx_xyz"},
        headers=auth_headers,
    )


CONTEXT_ENDPOINTS = [
    ("validate",            "POST", "/api/v1/validate",                        _validate_kwargs),
    ("validate_batch",      "POST", "/api/v1/validate/batch",                  _validate_batch_kwargs),
    ("validate_batch_file", "POST", "/api/v1/validate/batch/file",             _validate_batch_file_kwargs),
    ("generate",            "POST", "/api/v1/generate",                        _generate_kwargs),
    ("export_gx",           "GET",  "/api/v1/export/gx/customer",              _export_gx_kwargs),
    ("export_odcs",         "GET",  "/api/v1/export/odcs/customer",            _export_odcs_kwargs),
]


# ── ACT-049-EC-001: unknown context → 422 on every context endpoint ───────────

@pytest.mark.parametrize(
    "endpoint_id,method,url,kwargs_factory",
    CONTEXT_ENDPOINTS,
    ids=[ep[0] for ep in CONTEXT_ENDPOINTS],
)
def test_unknown_context_returns_422(
    endpoint_id, method, url, kwargs_factory, client, auth_headers
):
    """Every endpoint that accepts context must return 422 for an unknown context.

    If this test fails for a newly added endpoint, it means that endpoint is
    missing UnknownContextError handling. Add:

        try:
            rules = registry.get_rules_with_context(contract, context)
        except UnknownContextError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    """
    kwargs = kwargs_factory(client, auth_headers)
    resp = client.request(method, url, **kwargs)
    assert resp.status_code == 422, (
        f"Endpoint '{endpoint_id}' ({method} {url}) returned {resp.status_code} "
        f"for unknown context — expected 422. Response: {resp.text[:200]}"
    )


# ── ACT-049-EC-002: known context is accepted on every context endpoint ───────

def _validate_known_ctx_kwargs(client, auth_headers):
    return dict(
        json={"contract": "customer", "context": "kids_app", "record": {"name": "Alice", "age": 10}},
        headers=auth_headers,
    )

def _validate_batch_known_ctx_kwargs(client, auth_headers):
    return dict(
        json={"contract": "customer", "context": "kids_app", "records": [{"name": "Alice", "age": 10}]},
        headers=auth_headers,
    )

def _validate_batch_file_known_ctx_kwargs(client, auth_headers):
    csv_content = b"name,age\nAlice,10\n"
    return dict(
        params={"contract": "customer", "context": "kids_app"},
        files={"file": ("test.csv", io.BytesIO(csv_content), "text/csv")},
        headers=auth_headers,
    )

def _generate_known_ctx_kwargs(client, auth_headers):
    return dict(
        params={"contract_name": "customer", "target": "snowflake", "context": "kids_app"},
        headers=auth_headers,
    )

def _export_gx_known_ctx_kwargs(client, auth_headers):
    return dict(
        params={"context": "kids_app"},
        headers=auth_headers,
    )

def _export_odcs_known_ctx_kwargs(client, auth_headers):
    return dict(
        params={"context": "kids_app"},
        headers=auth_headers,
    )


CONTEXT_ENDPOINTS_KNOWN = [
    ("validate",            "POST", "/api/v1/validate",                        _validate_known_ctx_kwargs),
    ("validate_batch",      "POST", "/api/v1/validate/batch",                  _validate_batch_known_ctx_kwargs),
    ("validate_batch_file", "POST", "/api/v1/validate/batch/file",             _validate_batch_file_known_ctx_kwargs),
    ("generate",            "POST", "/api/v1/generate",                        _generate_known_ctx_kwargs),
    ("export_gx",           "GET",  "/api/v1/export/gx/customer",              _export_gx_known_ctx_kwargs),
    ("export_odcs",         "GET",  "/api/v1/export/odcs/customer",            _export_odcs_known_ctx_kwargs),
]

@pytest.mark.parametrize(
    "endpoint_id,method,url,kwargs_factory",
    CONTEXT_ENDPOINTS_KNOWN,
    ids=[ep[0] for ep in CONTEXT_ENDPOINTS_KNOWN],
)
def test_known_context_is_accepted(
    endpoint_id, method, url, kwargs_factory, client, auth_headers
):
    """A valid context must not be rejected by any context-accepting endpoint."""
    kwargs = kwargs_factory(client, auth_headers)
    resp = client.request(method, url, **kwargs)
    assert resp.status_code not in (422, 404, 500), (
        f"Endpoint '{endpoint_id}' ({method} {url}) rejected valid context 'kids_app' "
        f"with {resp.status_code}. Response: {resp.text[:200]}"
    )
