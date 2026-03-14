"""Tests for MCP server remote mode (OPENDQV_MCP_API_URL set).

All 6 tool handlers are tested with a mocked httpx.Client so no real network
calls are made. Confirms that each tool correctly proxies to the API endpoint
and returns the response body unchanged (or, for explain_error, reconstructs
explanation locally from the contract JSON the API returns).
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_server
from mcp_server import (
    _tool_create_contract_draft,
    _tool_explain_error,
    _tool_get_contract,
    _tool_list_contracts,
    _tool_validate_batch,
    _tool_validate_record,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _mock_response(body: dict | list, status_code: int = 200):
    """Return a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(body)
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()  # no-op unless status_code >= 400
    return resp


def _mock_client(**method_responses):
    """Build a mock httpx.Client where get/post return the given responses."""
    client = MagicMock()
    for method, response in method_responses.items():
        getattr(client, method).return_value = response
    return client


# ── list_contracts ────────────────────────────────────────────────────

def test_remote_list_contracts():
    api_payload = [
        {"name": "customer", "version": "1.0", "status": "active", "rule_count": 5}
    ]
    client = _mock_client(get=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_list_contracts({}))
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data[0]["name"] == "customer"
    client.get.assert_called_once_with("/api/v1/contracts")


# ── get_contract ──────────────────────────────────────────────────────

def test_remote_get_contract_latest():
    api_payload = {
        "name": "customer", "version": "1.0", "status": "active",
        "rules": [{"name": "email_valid", "type": "regex", "field": "email",
                   "severity": "error", "error_message": "Invalid email"}],
    }
    client = _mock_client(get=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_get_contract({"name": "customer"}))
    data = json.loads(result[0].text)
    assert data["name"] == "customer"
    client.get.assert_called_once_with("/api/v1/contracts/customer")


def test_remote_get_contract_specific_version():
    api_payload = {"name": "customer", "version": "2.0", "status": "active", "rules": []}
    client = _mock_client(get=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        _run(_tool_get_contract({"name": "customer", "version": "2.0"}))
    client.get.assert_called_once_with("/api/v1/contracts/customer?version=2.0")


# ── validate_record ───────────────────────────────────────────────────

def test_remote_validate_record_pass():
    api_payload = {"valid": True, "errors": [], "contract": "customer", "version": "1.0"}
    client = _mock_client(post=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_validate_record({
            "contract": "customer",
            "record": {"email": "alice@example.com"},
        }))
    data = json.loads(result[0].text)
    assert data["valid"] is True
    client.post.assert_called_once_with(
        "/api/v1/validate?allow_draft=true",
        json={"contract": "customer", "record": {"email": "alice@example.com"}},
    )


def test_remote_validate_record_fail():
    api_payload = {
        "valid": False,
        "errors": [{"field": "email", "rule": "email_format", "message": "Invalid email"}],
        "contract": "customer",
    }
    client = _mock_client(post=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_validate_record({
            "contract": "customer",
            "record": {"email": "not-an-email"},
        }))
    data = json.loads(result[0].text)
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "email"


def test_remote_validate_record_with_context():
    api_payload = {"valid": True, "errors": [], "contract": "customer"}
    client = _mock_client(post=_mock_response(api_payload))
    with patch.object(mcp_server, "_remote_client", client):
        _run(_tool_validate_record({
            "contract": "customer",
            "record": {"email": "x@x.com"},
            "context": "salesforce",
        }))
    call_kwargs = client.post.call_args
    assert call_kwargs[1]["json"]["context"] == "salesforce"


# ── validate_batch ────────────────────────────────────────────────────

def test_remote_validate_batch():
    api_payload = {
        "summary": {"total": 2, "passed": 2, "failed": 0},
        "results": [{"index": 0, "valid": True, "errors": []},
                    {"index": 1, "valid": True, "errors": []}],
        "contract": "customer",
    }
    client = _mock_client(post=_mock_response(api_payload))
    records = [{"email": "a@a.com"}, {"email": "b@b.com"}]
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_validate_batch({"contract": "customer", "records": records}))
    data = json.loads(result[0].text)
    assert data["summary"]["total"] == 2
    client.post.assert_called_once_with(
        "/api/v1/validate/batch?allow_draft=true",
        json={"contract": "customer", "records": records},
    )


# ── explain_error ─────────────────────────────────────────────────────

def test_remote_explain_error_not_empty():
    """explain_error in remote mode: fetches contract, reconstructs Rule, explains locally."""
    contract_detail = {
        "name": "customer", "version": "1.0", "status": "active",
        "rules": [
            {"name": "email_required", "type": "not_empty", "field": "email",
             "severity": "error", "error_message": "email is required"},
        ],
    }
    client = _mock_client(get=_mock_response(contract_detail))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_explain_error({
            "contract": "customer", "field": "email", "rule": "email_required",
        }))
    data = json.loads(result[0].text)
    assert data["rule_type"] == "not_empty"
    assert "required" in data["explanation"].lower()
    assert data["valid_examples"]
    client.get.assert_called_once_with("/api/v1/contracts/customer")


def test_remote_explain_error_rule_not_found():
    contract_detail = {"name": "customer", "version": "1.0", "status": "active", "rules": []}
    client = _mock_client(get=_mock_response(contract_detail))
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_explain_error({
            "contract": "customer", "field": "email", "rule": "nonexistent_rule",
        }))
    data = json.loads(result[0].text)
    assert "error" in data


def test_remote_explain_error_contract_not_found():
    not_found = _mock_response({"detail": "Not found"}, status_code=404)
    not_found.raise_for_status = MagicMock()  # no-op; we check status_code manually
    client = _mock_client(get=not_found)
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_explain_error({
            "contract": "nonexistent", "field": "f", "rule": "r",
        }))
    data = json.loads(result[0].text)
    assert "error" in data


# ── create_contract_draft ─────────────────────────────────────────────

def test_remote_create_contract_draft_with_reload():
    """Draft is written locally; remote API reload is called."""
    reload_resp = _mock_response({"status": "ok"})
    client = _mock_client(post=reload_resp)

    args = {
        "name": "MCP_remote_test_xyz",
        "description": "Remote mode test contract",
        "owner": "Test Team",
        "created_by": "remote-test@example.com",
        "rules": [{"name": "id_required", "type": "not_empty", "field": "id"}],
    }
    with patch.object(mcp_server, "_remote_client", client):
        result = _run(_tool_create_contract_draft(args))

    data = json.loads(result[0].text)
    assert data["created"] is True
    assert data["name"] == "MCP_remote_test_xyz"
    # Reload endpoint was called
    client.post.assert_called_once_with("/api/v1/contracts/reload")
    # Message mentions the API notification
    assert "central API" in data["message"]

    # Clean up the YAML written to contracts/
    import config as _cfg
    yaml_path = _cfg.CONTRACTS_DIR / "MCP_remote_test_xyz.yaml"
    if yaml_path.exists():
        yaml_path.unlink()


def test_remote_create_contract_draft_reload_failure_graceful():
    """If the reload call fails, response still succeeds with a note."""
    import httpx
    failing_client = MagicMock()
    failing_client.post.side_effect = httpx.ConnectError("connection refused")

    args = {
        "name": "MCP_remote_reload_fail_xyz",
        "description": "Test reload failure",
        "owner": "Test",
        "created_by": "test@example.com",
        "rules": [{"name": "x", "type": "not_empty", "field": "x"}],
    }
    with patch.object(mcp_server, "_remote_client", failing_client):
        result = _run(_tool_create_contract_draft(args))

    data = json.loads(result[0].text)
    assert data["created"] is True
    assert "could not notify" in data["message"]

    import config as _cfg
    yaml_path = _cfg.CONTRACTS_DIR / "MCP_remote_reload_fail_xyz.yaml"
    if yaml_path.exists():
        yaml_path.unlink()
