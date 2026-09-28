"""The OpenAPI 3.0 description OpenDQV ships for Salesforce External Services.

The API serves OpenAPI 3.1 at /openapi.json; External Services registration
accepts 2.0 and 3.0. docs/salesforce/opendqv-validate-openapi-3.0.json describes
POST /api/v1/validate alone. These tests pin it to what the API actually accepts
and returns, so the file cannot drift from the route it describes — the
Salesforce guide's earlier trigger-callout section was "tested and working"
until it was not; this one is checked on every run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs" / "salesforce" / "opendqv-validate-openapi-3.0.json"
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _served(client, auth_headers) -> dict:
    return client.get("/openapi.json", headers=auth_headers).json()


def test_it_is_an_openapi_3_0_document_not_the_served_3_1():
    assert SPEC["openapi"].startswith("3.0."), "External Services accepts 2.0 / 3.0, not 3.1"
    assert list(SPEC["paths"]) == ["/api/v1/validate"]
    assert "post" in SPEC["paths"]["/api/v1/validate"]
    assert SPEC["servers"][0]["url"].startswith("https://"), "a placeholder the reader replaces"


def test_every_request_field_is_one_the_api_accepts(client, auth_headers):
    served = _served(client, auth_headers)
    op = served["paths"]["/api/v1/validate"]["post"]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    accepted = set(served["components"]["schemas"][ref]["properties"])
    described = set(SPEC["components"]["schemas"]["ValidateRequest"]["properties"])
    assert described <= accepted, described - accepted
    assert set(SPEC["components"]["schemas"]["ValidateRequest"]["required"]) == {"contract", "record"}


def test_every_response_field_is_one_the_api_returns(client, auth_headers):
    served = _served(client, auth_headers)
    op = served["paths"]["/api/v1/validate"]["post"]
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    returned = set(served["components"]["schemas"][ref]["properties"])
    described = set(SPEC["components"]["schemas"]["ValidateResponse"]["properties"])
    assert described <= returned, described - returned
    err = set(served["components"]["schemas"]["FieldErrorResponse"]["properties"])
    assert set(SPEC["components"]["schemas"]["FieldError"]["properties"]) <= err


def test_mode_values_match_the_engine():
    assert SPEC["components"]["schemas"]["ValidateResponse"]["properties"]["mode"]["enum"] == [
        "enforcement", "observation_only"]


@pytest.mark.parametrize("observe_only,expect_valid", [(False, False), (True, False)])
def test_a_request_shaped_by_the_spec_gets_a_response_shaped_by_it(client, auth_headers, observe_only, expect_valid):
    """A failing record: valid is false in BOTH modes; observation mode adds
    would_have_failed and a mode the flow must branch on — which is why the
    guide says proceed when valid is true OR mode is observation_only."""
    body = {"contract": "customer", "record": {"name": "", "email": "not-an-email", "age": 30},
            "record_id": "003dL00001TbdobQAA", "observe_only": observe_only, "agent_id": "sf-screen-flow"}
    r = client.post("/api/v1/validate", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["valid"] is expect_valid
    assert out["mode"] == ("observation_only" if observe_only else "enforcement")
    assert out["record_id"] == "003dL00001TbdobQAA", "the Salesforce Id round-trips on the single-record route"
    assert out["errors"] and {"field", "message", "error_code"} <= set(out["errors"][0])
    if observe_only:
        assert out["would_have_failed"] is True
        assert out["valid"] is True or out["mode"] == "observation_only", "the flow's proceed condition"
    for k in SPEC["components"]["schemas"]["ValidateResponse"]["required"]:
        assert k in out


def test_the_batch_route_has_no_record_id_only_an_index(client, auth_headers):
    """Documented in the guide: batch results are keyed by submission index."""
    served = _served(client, auth_headers)
    op = served["paths"]["/api/v1/validate/batch"]["post"]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert "record_id" not in served["components"]["schemas"][ref]["properties"]
    r = client.post("/api/v1/validate/batch", json={"contract": "customer", "records": [
        {"name": "A", "email": "a@example.com", "age": 30}, {"name": "", "email": "x", "age": 30}]},
        headers=auth_headers)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [x["index"] for x in results] == [0, 1] and all("event_id" in x for x in results)
    assert "record_id" not in results[0]
