"""Tests for MCP server business logic layer.

Tests the _tool_* handler functions and call_tool dispatcher directly via
asyncio.run(), matching the existing sync test style.  No MCP transport
(stdio) is exercised — that is MCP SDK infrastructure.
"""
import asyncio
import concurrent.futures
import json
import os
import sys
from pathlib import Path


# Ensure project root is on the path before importing mcp_server
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp import types
from mcp_server import (
    _tool_create_contract_draft,
    _tool_explain_error,
    _tool_get_contract,
    _tool_list_contracts,
    _tool_validate_batch,
    _tool_validate_record,
    _registry,
    call_tool,
)

# ── Fixtures / helpers ────────────────────────────────────────────────────────

VALID_BANKING_RECORD = {
    "transaction_id": "TXN001",
    "account_number": "ACC123",
    "transaction_date": "2024-01-15",
    "amount": 100.0,
    "currency": "USD",
    "transaction_type": "credit",
    "merchant_id": "MERCH01",
    "channel": "online",
    "merchant_category_code": "5411",
}


def _run(coro):
    """Run a coroutine synchronously.

    Uses asyncio.run() normally.  When called from inside a running event loop
    (e.g. after pytest-playwright sets one up), falls back to a worker thread
    so that asyncio.run() can create its own fresh loop there.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — use asyncio.run() directly (normal path).
        return asyncio.run(coro)
    # A loop is already running (playwright left it open). Execute in a thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _parse(result):
    """Assert result is a 1-item TextContent list and return parsed JSON."""
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].type == "text"
    return json.loads(result[0].text)


# ── TestMCPToolResponse ───────────────────────────────────────────────────────

class TestMCPToolResponse:
    """Every handler returns a 1-item list[TextContent] containing valid JSON."""

    def test_list_contracts_response_shape(self):
        result = _run(_tool_list_contracts({}))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], types.TextContent)
        assert result[0].type == "text"
        json.loads(result[0].text)  # must not raise

    def test_validate_record_response_shape(self):
        result = _run(_tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        ))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], types.TextContent)
        assert result[0].type == "text"
        json.loads(result[0].text)  # must not raise


# ── TestMCPListContracts ──────────────────────────────────────────────────────

class TestMCPListContracts:
    """_tool_list_contracts returns a JSON array of contract summaries."""

    def test_returns_array(self):
        data = _parse(_run(_tool_list_contracts({})))
        assert isinstance(data, list)

    def test_contains_active_contracts(self):
        data = _parse(_run(_tool_list_contracts({})))
        assert len(data) >= 3

    def test_age_compliance_record_present_with_correct_metadata(self):
        data = _parse(_run(_tool_list_contracts({})))
        matches = [c for c in data if c["name"] == "age_compliance_record"]
        assert len(matches) == 1
        contract = matches[0]
        assert contract["rule_count"] >= 14
        assert contract["status"] == "active"
        assert "version" in contract


# ── TestMCPValidateRecord ─────────────────────────────────────────────────────

class TestMCPValidateRecord:
    """_tool_validate_record validates a single record against a named contract."""

    def test_valid_record_returns_valid_true_no_errors(self):
        data = _parse(_run(_tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        )))
        assert data["valid"] is True
        assert data["errors"] == []

    def test_invalid_amount_returns_valid_false_with_error(self):
        record = dict(VALID_BANKING_RECORD, amount="a")
        data = _parse(_run(_tool_validate_record(
            {"contract": "banking_transaction", "record": record}
        )))
        assert data["valid"] is False
        assert len(data["errors"]) >= 1
        amount_errors = [e for e in data["errors"] if e["field"] == "amount"]
        assert len(amount_errors) >= 1
        assert amount_errors[0]["rule"] == "amount_min"

    def test_unknown_contract_returns_error_dict_no_exception(self):
        data = _parse(_run(_tool_validate_record(
            {"contract": "nonexistent", "record": {}}
        )))
        assert "error" in data
        assert "nonexistent" in data["error"]

    def test_result_includes_contract_and_version_keys(self):
        data = _parse(_run(_tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        )))
        assert "contract" in data
        assert data["contract"] == "banking_transaction"
        assert "version" in data

    def test_error_severity_is_error(self):
        record = dict(VALID_BANKING_RECORD, amount="a")
        data = _parse(_run(_tool_validate_record(
            {"contract": "banking_transaction", "record": record}
        )))
        amount_errors = [e for e in data["errors"] if e["field"] == "amount"]
        assert amount_errors[0]["severity"] == "error"


# ── TestMCPValidateBatch ──────────────────────────────────────────────────────

class TestMCPValidateBatch:
    """_tool_validate_batch validates up to 10,000 records in one call."""

    def test_batch_all_valid_summary_passed(self):
        records = [VALID_BANKING_RECORD] * 3
        data = _parse(_run(_tool_validate_batch(
            {"contract": "banking_transaction", "records": records}
        )))
        assert data["summary"]["passed"] == 3
        assert data["summary"]["failed"] == 0

    def test_batch_one_invalid_record_summary_failed(self):
        invalid_record = dict(VALID_BANKING_RECORD, transaction_id="")
        records = [VALID_BANKING_RECORD, invalid_record, VALID_BANKING_RECORD]
        data = _parse(_run(_tool_validate_batch(
            {"contract": "banking_transaction", "records": records}
        )))
        assert data["summary"]["failed"] == 1
        assert len(data["results"]) == 3
        assert data["results"][1]["valid"] is False

    def test_batch_over_10000_records_returns_error(self):
        data = _parse(_run(_tool_validate_batch(
            {"contract": "banking_transaction", "records": [{}] * 10001}
        )))
        assert "error" in data
        assert "10,000" in data["error"]

    def test_batch_unknown_contract_returns_error_dict_no_exception(self):
        data = _parse(_run(_tool_validate_batch(
            {"contract": "nonexistent", "records": [{}]}
        )))
        assert "error" in data


# ── TestMCPGetContract ────────────────────────────────────────────────────────

class TestMCPGetContract:
    """_tool_get_contract returns full contract detail including all rules."""

    def test_response_has_required_top_level_keys(self):
        data = _parse(_run(_tool_get_contract({"name": "banking_transaction"})))
        for key in ("name", "version", "status", "description", "owner", "rule_count", "rules"):
            assert key in data, f"Missing key: {key}"

    def test_rules_array_length_matches_rule_count(self):
        data = _parse(_run(_tool_get_contract({"name": "banking_transaction"})))
        assert len(data["rules"]) == data["rule_count"]

    def test_each_rule_has_required_keys(self):
        data = _parse(_run(_tool_get_contract({"name": "banking_transaction"})))
        for rule in data["rules"]:
            for key in ("name", "type", "field", "severity", "error_message"):
                assert key in rule, f"Rule missing key: {key}"

    def test_unknown_contract_returns_error_dict(self):
        data = _parse(_run(_tool_get_contract({"name": "nonexistent"})))
        assert "error" in data


# ── TestMCPExplainError ───────────────────────────────────────────────────────

class TestMCPExplainError:
    """_tool_explain_error returns plain-English remediation guidance."""

    def test_response_has_required_keys(self):
        data = _parse(_run(_tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        )))
        for key in ("contract", "field", "rule", "rule_type", "explanation",
                    "valid_examples", "invalid_examples", "constraint"):
            assert key in data, f"Missing key: {key}"

    def test_banking_amount_min_rule_type_and_constraint(self):
        data = _parse(_run(_tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        )))
        assert data["rule_type"] == "min"
        assert data["constraint"].get("min") == 0.01

    def test_explanation_is_non_empty_string(self):
        data = _parse(_run(_tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        )))
        assert isinstance(data["explanation"], str)
        assert len(data["explanation"]) > 0

    def test_unknown_contract_returns_error_dict(self):
        data = _parse(_run(_tool_explain_error(
            {"contract": "nonexistent", "field": "amount", "rule": "amount_min"}
        )))
        assert "error" in data

    def test_unknown_rule_returns_error_dict(self):
        data = _parse(_run(_tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "no_such_rule"}
        )))
        assert "error" in data


# ── TestMCPCallToolDispatch ───────────────────────────────────────────────────

class TestMCPCallToolDispatch:
    """call_tool dispatches to the correct handler by name."""

    def test_dispatch_list_contracts_returns_array(self):
        data = _parse(_run(call_tool("list_contracts", {})))
        assert isinstance(data, list)

    def test_dispatch_validate_record_returns_valid_key(self):
        data = _parse(_run(call_tool(
            "validate_record",
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD},
        )))
        assert "valid" in data

    def test_unknown_tool_returns_message_no_exception(self):
        result = _run(call_tool("notreal", {}))
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Unknown tool: notreal" in result[0].text

    def test_handler_exception_is_caught_returns_error_message(self):
        # Pass args that cause the handler to raise (missing required key)
        result = _run(call_tool("validate_record", {}))
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Error:" in result[0].text


# ── TestMCPCreateContractDraft ────────────────────────────────────────────────

_DRAFT_CONTRACT_NAME = "MCP_test_telemetry_pytest"
_DRAFT_RULES = [
    {"name": "sensor_id_required", "type": "not_empty", "field": "sensor_id",
     "error_message": "sensor_id is required"},
    {"name": "altitude_range", "type": "range", "field": "altitude_km",
     "min": 160.0, "max": 2000.0, "error_message": "altitude_km must be 160–2000 km"},
]


def _cleanup_draft(name: str):
    """Remove a test-created draft contract from disk and from the in-memory registry."""
    path = _registry.contracts_dir / f"{name}.yaml"
    if path.exists():
        path.unlink()
    _registry._contracts.pop(name, None)
    _registry._contract_paths.pop(name, None)


class TestMCPCreateContractDraft:
    """_tool_create_contract_draft creates DRAFT contracts with MCP_ prefix enforcement."""

    def setup_method(self):
        _cleanup_draft(_DRAFT_CONTRACT_NAME)
        # Ensure no stale env var bleeds between tests
        os.environ.pop("OPENDQV_AGENT_IDENTITY", None)

    def teardown_method(self):
        _cleanup_draft(_DRAFT_CONTRACT_NAME)
        os.environ.pop("OPENDQV_AGENT_IDENTITY", None)

    def test_create_draft_success_returns_created_true(self):
        data = _parse(_run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test telemetry contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        })))
        assert data["created"] is True
        assert data["name"] == _DRAFT_CONTRACT_NAME
        assert data["status"] == "draft"
        assert data["source"] == "mcp"
        assert data["proposed_by"] == "test@example.com"
        assert data["rule_count"] == 2

    def test_create_draft_contract_is_immediately_validatable(self):
        _run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test telemetry contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }))
        result = _parse(_run(_tool_validate_record({
            "contract": _DRAFT_CONTRACT_NAME,
            "record": {"sensor_id": "SAT-001", "altitude_km": 500.0},
        })))
        assert result["valid"] is True

    def test_create_draft_rejects_missing_mcp_prefix(self):
        data = _parse(_run(_tool_create_contract_draft({
            "name": "satellite_telemetry",
            "description": "Missing prefix",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        })))
        assert "error" in data
        assert "MCP_" in data["error"]

    def test_create_draft_rejects_missing_created_by_and_no_env_var(self):
        data = _parse(_run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            # no created_by, no OPENDQV_AGENT_IDENTITY env var
            "rules": _DRAFT_RULES,
        })))
        assert "error" in data
        assert "created_by" in data["error"] or "OPENDQV_AGENT_IDENTITY" in data["error"]

    def test_create_draft_uses_env_var_when_created_by_omitted(self):
        os.environ["OPENDQV_AGENT_IDENTITY"] = "env-user@example.com"
        data = _parse(_run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "rules": _DRAFT_RULES,
        })))
        assert data["created"] is True
        assert data["proposed_by"] == "env-user@example.com"

    def test_create_draft_rejects_duplicate_name(self):
        args = {
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }
        _run(_tool_create_contract_draft(args))
        data = _parse(_run(_tool_create_contract_draft(args)))
        assert "error" in data
        assert "already exists" in data["error"]

    def test_create_draft_rejects_invalid_rule(self):
        data = _parse(_run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": [{"not_a_valid_rule": True}],
        })))
        assert "error" in data

    def test_create_draft_yaml_file_written_to_disk(self):
        _run(_tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }))
        path = _registry.contracts_dir / f"{_DRAFT_CONTRACT_NAME}.yaml"
        assert path.exists()

    def test_dispatch_create_contract_draft_via_call_tool(self):
        data = _parse(_run(call_tool("create_contract_draft", {
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        })))
        assert data["created"] is True


# ── TestMCPRateLimiting ───────────────────────────────────────────────────────

import mcp_server as _mcp_server_module


# ── TestMCPDraftNoticeAndGovernanceTip ────────────────────────────────────────

_DRAFT_NOTICE_CONTRACT = "MCP_test_draft_notice_pytest"
_DRAFT_NOTICE_RULES = [
    {"name": "sensor_id_required", "type": "not_empty", "field": "sensor_id",
     "error_message": "sensor_id is required"},
]


class TestMCPDraftNoticeAndGovernanceTip:
    """draft_notice and governance_tip injection in validate_record / validate_batch."""

    def setup_method(self):
        _cleanup_draft(_DRAFT_NOTICE_CONTRACT)

    def teardown_method(self):
        _cleanup_draft(_DRAFT_NOTICE_CONTRACT)

    def _create_draft(self):
        _run(_tool_create_contract_draft({
            "name": _DRAFT_NOTICE_CONTRACT,
            "description": "Draft notice test contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_NOTICE_RULES,
        }))

    def test_validate_record_draft_contract_includes_draft_notice(self):
        self._create_draft()
        data = _parse(_run(_tool_validate_record({
            "contract": _DRAFT_NOTICE_CONTRACT,
            "record": {"sensor_id": "S1"},
        })))
        assert "draft_notice" in data
        assert "DRAFT" in data["draft_notice"]

    def test_validate_record_active_contract_has_no_draft_notice(self):
        data = _parse(_run(_tool_validate_record({
            "contract": "banking_transaction",
            "record": VALID_BANKING_RECORD,
        })))
        assert "draft_notice" not in data

    def test_validate_record_always_returns_governance_tip(self):
        data = _parse(_run(_tool_validate_record({
            "contract": "banking_transaction",
            "record": VALID_BANKING_RECORD,
        })))
        assert "governance_tip" in data
        assert isinstance(data["governance_tip"], str)
        assert len(data["governance_tip"]) > 0

    def test_validate_batch_always_returns_governance_tip(self):
        records = [VALID_BANKING_RECORD] * 2
        data = _parse(_run(_tool_validate_batch({
            "contract": "banking_transaction",
            "records": records,
        })))
        assert "governance_tip" in data
        assert isinstance(data["governance_tip"], str)
        assert len(data["governance_tip"]) > 0


# ── TestMCPRateLimiting ───────────────────────────────────────────────────────

class TestMCPRateLimiting:
    """ACT-045-06: Per-identity rate limit for draft creation (10/hour)."""

    def setup_method(self):
        # Clear rate limit counters and any stale draft contracts before each test
        _mcp_server_module._draft_creation_log.clear()
        for i in range(15):
            _cleanup_draft(f"MCP_rl_test_pytest_{i}")

    def teardown_method(self):
        _mcp_server_module._draft_creation_log.clear()
        for i in range(15):
            _cleanup_draft(f"MCP_rl_test_pytest_{i}")

    def test_rate_limit_blocks_at_11th_creation(self):
        """11th call with the same identity returns a rate-limit error."""
        identity = "ratelimit@example.com"
        for i in range(10):
            data = _parse(_run(_tool_create_contract_draft({
                "name": f"MCP_rl_test_pytest_{i}",
                "description": "RL test",
                "owner": "pytest",
                "created_by": identity,
                "rules": _DRAFT_RULES,
            })))
            assert "error" not in data, f"Unexpected error on creation {i}: {data}"
        # 11th attempt must be blocked
        data = _parse(_run(_tool_create_contract_draft({
            "name": "MCP_rl_test_pytest_10",
            "description": "RL test overflow",
            "owner": "pytest",
            "created_by": identity,
            "rules": _DRAFT_RULES,
        })))
        assert "error" in data
        assert "Rate limit" in data["error"]

    def test_different_identities_have_separate_counters(self):
        """10 creations for identity A do not block identity B."""
        identity_a = "identity_a@example.com"
        identity_b = "identity_b@example.com"
        for i in range(10):
            _run(_tool_create_contract_draft({
                "name": f"MCP_rl_test_pytest_{i}",
                "description": "RL test A",
                "owner": "pytest",
                "created_by": identity_a,
                "rules": _DRAFT_RULES,
            }))
        # Identity B should still be allowed
        data = _parse(_run(_tool_create_contract_draft({
            "name": "MCP_rl_test_pytest_10",
            "description": "RL test B",
            "owner": "pytest",
            "created_by": identity_b,
            "rules": _DRAFT_RULES,
        })))
        assert "error" not in data
        assert data.get("created") is True
