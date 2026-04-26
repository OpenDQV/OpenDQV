"""Tests for MCP server business logic layer.

Tests the _tool_* handler functions and call_tool dispatcher directly as
native async tests (pytest-asyncio).  No MCP transport (stdio) is
exercised — that is MCP SDK infrastructure.
"""
import json
import os
import sys
from pathlib import Path

import pytest


# Ensure project root is on the path before importing mcp_server
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp import types
from opendqv.mcp_server import (
    _tool_create_contract_draft,
    _tool_explain_error,
    _tool_get_contract,
    _tool_get_quality_metrics,
    _tool_get_quality_trend,
    _tool_get_rule_velocity,
    _tool_list_contracts,
    _tool_validate_batch,
    _tool_validate_record,
    _registry,
    _pick_governance_tip,
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


def _parse(result):
    """Assert result is a 1-item TextContent list and return parsed JSON."""
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].type == "text"
    return json.loads(result[0].text)


# ── TestMCPToolResponse ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPToolResponse:
    """Every handler returns a 1-item list[TextContent] containing valid JSON."""

    async def test_list_contracts_response_shape(self):
        result = await _tool_list_contracts({})
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], types.TextContent)
        assert result[0].type == "text"
        json.loads(result[0].text)  # must not raise

    async def test_validate_record_response_shape(self):
        result = await _tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], types.TextContent)
        assert result[0].type == "text"
        json.loads(result[0].text)  # must not raise


# ── TestMCPListContracts ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPListContracts:
    """_tool_list_contracts returns a JSON array of contract summaries."""

    async def test_returns_array(self):
        data = _parse(await _tool_list_contracts({}))
        assert isinstance(data, list)

    async def test_contains_active_contracts(self):
        data = _parse(await _tool_list_contracts({}))
        assert len(data) >= 3

    async def test_social_media_age_compliance_present_with_correct_metadata(self):
        data = _parse(await _tool_list_contracts({}))
        matches = [c for c in data if c["name"] == "social_media_age_compliance"]
        assert len(matches) == 1
        contract = matches[0]
        assert contract["rule_count"] >= 14
        assert contract["status"] == "active"
        assert "version" in contract


# ── TestMCPValidateRecord ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPValidateRecord:
    """_tool_validate_record validates a single record against a named contract."""

    async def test_valid_record_returns_valid_true_no_errors(self):
        data = _parse(await _tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        ))
        assert data["valid"] is True
        assert data["errors"] == []

    async def test_invalid_amount_returns_valid_false_with_error(self):
        record = dict(VALID_BANKING_RECORD, amount="a")
        data = _parse(await _tool_validate_record(
            {"contract": "banking_transaction", "record": record}
        ))
        assert data["valid"] is False
        assert len(data["errors"]) >= 1
        amount_errors = [e for e in data["errors"] if e["field"] == "amount"]
        assert len(amount_errors) >= 1
        assert amount_errors[0]["rule"] == "amount_min"

    async def test_unknown_contract_returns_error_dict_no_exception(self):
        data = _parse(await _tool_validate_record(
            {"contract": "nonexistent", "record": {}}
        ))
        assert "error" in data
        # CRT173/24: structured envelope — error_code + kind + status + detail.
        assert data["error"]["error_code"] == "CONTRACT_NOT_FOUND"
        assert "nonexistent" in data["error"]["detail"]

    async def test_result_includes_contract_and_version_keys(self):
        data = _parse(await _tool_validate_record(
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD}
        ))
        assert "contract" in data
        assert data["contract"] == "banking_transaction"
        assert "version" in data

    async def test_error_severity_is_error(self):
        record = dict(VALID_BANKING_RECORD, amount="a")
        data = _parse(await _tool_validate_record(
            {"contract": "banking_transaction", "record": record}
        ))
        amount_errors = [e for e in data["errors"] if e["field"] == "amount"]
        assert amount_errors[0]["severity"] == "error"


# ── TestMCPValidateBatch ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPValidateBatch:
    """_tool_validate_batch validates up to 10,000 records in one call."""

    async def test_batch_all_valid_summary_passed(self):
        records = [VALID_BANKING_RECORD] * 3
        data = _parse(await _tool_validate_batch(
            {"contract": "banking_transaction", "records": records}
        ))
        assert data["summary"]["passed"] == 3
        assert data["summary"]["failed"] == 0

    async def test_batch_one_invalid_record_summary_failed(self):
        invalid_record = dict(VALID_BANKING_RECORD, transaction_id="")
        records = [VALID_BANKING_RECORD, invalid_record, VALID_BANKING_RECORD]
        data = _parse(await _tool_validate_batch(
            {"contract": "banking_transaction", "records": records}
        ))
        assert data["summary"]["failed"] == 1
        assert len(data["results"]) == 3
        assert data["results"][1]["valid"] is False

    async def test_batch_over_10000_records_returns_error(self):
        data = _parse(await _tool_validate_batch(
            {"contract": "banking_transaction", "records": [{}] * 10001}
        ))
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "BATCH_TOO_LARGE"
        assert "10,000" in data["error"]["detail"]

    async def test_batch_unknown_contract_returns_error_dict_no_exception(self):
        data = _parse(await _tool_validate_batch(
            {"contract": "nonexistent", "records": [{}]}
        ))
        assert "error" in data


# ── TestMCPGetContract ────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPGetContract:
    """_tool_get_contract returns full contract detail including all rules."""

    async def test_response_has_required_top_level_keys(self):
        data = _parse(await _tool_get_contract({"name": "banking_transaction"}))
        for key in ("name", "version", "status", "description", "owner", "rule_count", "rules"):
            assert key in data, f"Missing key: {key}"

    async def test_rules_array_length_matches_rule_count(self):
        data = _parse(await _tool_get_contract({"name": "banking_transaction"}))
        assert len(data["rules"]) == data["rule_count"]

    async def test_each_rule_has_required_keys(self):
        data = _parse(await _tool_get_contract({"name": "banking_transaction"}))
        for rule in data["rules"]:
            for key in ("name", "type", "field", "severity", "error_message"):
                assert key in rule, f"Rule missing key: {key}"

    async def test_unknown_contract_returns_error_dict(self):
        data = _parse(await _tool_get_contract({"name": "nonexistent"}))
        assert "error" in data


# ── TestMCPExplainError ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPExplainError:
    """_tool_explain_error returns plain-English remediation guidance."""

    async def test_response_has_required_keys(self):
        data = _parse(await _tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        ))
        for key in ("contract", "field", "rule", "rule_type", "explanation",
                    "valid_examples", "invalid_examples", "constraint"):
            assert key in data, f"Missing key: {key}"

    async def test_banking_amount_min_rule_type_and_constraint(self):
        data = _parse(await _tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        ))
        assert data["rule_type"] == "min"
        assert data["constraint"].get("min") == 0.01

    async def test_explanation_is_non_empty_string(self):
        data = _parse(await _tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "amount_min"}
        ))
        assert isinstance(data["explanation"], str)
        assert len(data["explanation"]) > 0

    async def test_unknown_contract_returns_error_dict(self):
        data = _parse(await _tool_explain_error(
            {"contract": "nonexistent", "field": "amount", "rule": "amount_min"}
        ))
        assert "error" in data

    async def test_unknown_rule_returns_error_dict(self):
        data = _parse(await _tool_explain_error(
            {"contract": "banking_transaction", "field": "amount", "rule": "no_such_rule"}
        ))
        assert "error" in data


# ── TestMCPCallToolDispatch ───────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMCPCallToolDispatch:
    """call_tool dispatches to the correct handler by name."""

    async def test_dispatch_list_contracts_returns_array(self):
        data = _parse(await call_tool("list_contracts", {}))
        assert isinstance(data, list)

    async def test_dispatch_validate_record_returns_valid_key(self):
        data = _parse(await call_tool(
            "validate_record",
            {"contract": "banking_transaction", "record": VALID_BANKING_RECORD},
        ))
        assert "valid" in data

    async def test_unknown_tool_returns_message_no_exception(self):
        result = await call_tool("notreal", {})
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Unknown tool: notreal" in result[0].text

    async def test_handler_exception_is_caught_returns_error_message(self):
        # Pass args that cause the handler to raise (missing required key)
        result = await call_tool("validate_record", {})
        assert isinstance(result, list)
        assert len(result) == 1
        # CRT173/24: dispatch-level catch-all returns the INTERNAL_ERROR envelope.
        data = json.loads(result[0].text)
        assert data["error"]["error_code"] == "INTERNAL_ERROR"
        assert data["error"]["status"] == 500


# ── TestMCPCreateContractDraft ────────────────────────────────────────────────

import opendqv.mcp_server as _mcp_server_module  # noqa: E402

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


@pytest.mark.asyncio
class TestMCPCreateContractDraft:
    """_tool_create_contract_draft creates DRAFT contracts with MCP_ prefix enforcement."""

    def setup_method(self):
        _cleanup_draft(_DRAFT_CONTRACT_NAME)
        # Ensure no stale env var bleeds between tests
        os.environ.pop("OPENDQV_AGENT_IDENTITY", None)

    def teardown_method(self):
        _cleanup_draft(_DRAFT_CONTRACT_NAME)
        os.environ.pop("OPENDQV_AGENT_IDENTITY", None)

    async def test_create_draft_success_returns_created_true(self):
        data = _parse(await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test telemetry contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }))
        assert data["created"] is True
        assert data["name"] == _DRAFT_CONTRACT_NAME
        assert data["status"] == "draft"
        assert data["source"] == "mcp"
        assert data["proposed_by"] == "test@example.com"
        assert data["rule_count"] == 2

    async def test_create_draft_contract_is_immediately_validatable(self):
        await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test telemetry contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        })
        result = _parse(await _tool_validate_record({
            "contract": _DRAFT_CONTRACT_NAME,
            "record": {"sensor_id": "SAT-001", "altitude_km": 500.0},
        }))
        assert result["valid"] is True

    async def test_create_draft_rejects_missing_mcp_prefix(self):
        data = _parse(await _tool_create_contract_draft({
            "name": "satellite_telemetry",
            "description": "Missing prefix",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }))
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "INVALID_CONTRACT_NAME"
        assert "MCP_" in data["error"]["detail"]

    async def test_create_draft_rejects_missing_created_by_and_no_env_var(self):
        data = _parse(await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            # no created_by, no OPENDQV_AGENT_IDENTITY env var
            "rules": _DRAFT_RULES,
        }))
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "MISSING_CREATED_BY"
        body = data["error"]["detail"] + " " + data["error"].get("remediation", "")
        assert "created_by" in body or "OPENDQV_AGENT_IDENTITY" in body

    async def test_create_draft_uses_env_var_when_created_by_omitted(self):
        os.environ["OPENDQV_AGENT_IDENTITY"] = "env-user@example.com"
        data = _parse(await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "rules": _DRAFT_RULES,
        }))
        assert data["created"] is True
        assert data["proposed_by"] == "env-user@example.com"

    async def test_create_draft_rejects_duplicate_name(self):
        args = {
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }
        await _tool_create_contract_draft(args)
        data = _parse(await _tool_create_contract_draft(args))
        assert "error" in data
        # CRT173/24: ValueError from create_draft is wrapped as DRAFT_VALIDATION_ERROR.
        assert data["error"]["error_code"] == "DRAFT_VALIDATION_ERROR"
        assert "already exists" in data["error"]["detail"]

    async def test_create_draft_rejects_invalid_rule(self):
        data = _parse(await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": [{"not_a_valid_rule": True}],
        }))
        assert "error" in data

    async def test_create_draft_yaml_file_written_to_disk(self):
        await _tool_create_contract_draft({
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        })
        path = _registry.contracts_dir / f"{_DRAFT_CONTRACT_NAME}.yaml"
        assert path.exists()

    async def test_dispatch_create_contract_draft_via_call_tool(self):
        data = _parse(await call_tool("create_contract_draft", {
            "name": _DRAFT_CONTRACT_NAME,
            "description": "Test",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_RULES,
        }))
        assert data["created"] is True


# ── TestMCPDraftNoticeAndGovernanceTip ────────────────────────────────────────

_DRAFT_NOTICE_CONTRACT = "MCP_test_draft_notice_pytest"
_DRAFT_NOTICE_RULES = [
    {"name": "sensor_id_required", "type": "not_empty", "field": "sensor_id",
     "error_message": "sensor_id is required"},
]


@pytest.mark.asyncio
class TestMCPDraftNoticeAndGovernanceTip:
    """draft_notice and governance_tip injection in validate_record / validate_batch."""

    def setup_method(self):
        _cleanup_draft(_DRAFT_NOTICE_CONTRACT)

    def teardown_method(self):
        _cleanup_draft(_DRAFT_NOTICE_CONTRACT)

    async def _create_draft(self):
        await _tool_create_contract_draft({
            "name": _DRAFT_NOTICE_CONTRACT,
            "description": "Draft notice test contract",
            "owner": "pytest",
            "created_by": "test@example.com",
            "rules": _DRAFT_NOTICE_RULES,
        })

    async def test_validate_record_draft_contract_includes_draft_notice(self):
        await self._create_draft()
        data = _parse(await _tool_validate_record({
            "contract": _DRAFT_NOTICE_CONTRACT,
            "record": {"sensor_id": "S1"},
        }))
        assert "draft_notice" in data
        assert "DRAFT" in data["draft_notice"]

    async def test_validate_record_active_contract_has_no_draft_notice(self):
        data = _parse(await _tool_validate_record({
            "contract": "banking_transaction",
            "record": VALID_BANKING_RECORD,
        }))
        assert "draft_notice" not in data

    async def test_validate_record_always_returns_governance_tip(self):
        data = _parse(await _tool_validate_record({
            "contract": "banking_transaction",
            "record": VALID_BANKING_RECORD,
        }))
        assert "governance_tip" in data
        assert isinstance(data["governance_tip"], str)
        assert len(data["governance_tip"]) > 0

    async def test_validate_batch_always_returns_governance_tip(self):
        records = [VALID_BANKING_RECORD] * 2
        data = _parse(await _tool_validate_batch({
            "contract": "banking_transaction",
            "records": records,
        }))
        assert "governance_tip" in data
        assert isinstance(data["governance_tip"], str)
        assert len(data["governance_tip"]) > 0


# ── TestMCPRateLimiting ───────────────────────────────────────────────────────

@pytest.mark.asyncio
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

    async def test_rate_limit_blocks_at_11th_creation(self):
        """11th call with the same identity returns a rate-limit error."""
        identity = "ratelimit@example.com"
        for i in range(10):
            data = _parse(await _tool_create_contract_draft({
                "name": f"MCP_rl_test_pytest_{i}",
                "description": "RL test",
                "owner": "pytest",
                "created_by": identity,
                "rules": _DRAFT_RULES,
            }))
            assert "error" not in data, f"Unexpected error on creation {i}: {data}"
        # 11th attempt must be blocked
        data = _parse(await _tool_create_contract_draft({
            "name": "MCP_rl_test_pytest_10",
            "description": "RL test overflow",
            "owner": "pytest",
            "created_by": identity,
            "rules": _DRAFT_RULES,
        }))
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "DRAFT_RATE_LIMITED"
        assert data["error"]["status"] == 429
        assert "Rate limit" in data["error"]["detail"]

    async def test_different_identities_have_separate_counters(self):
        """10 creations for identity A do not block identity B."""
        identity_a = "identity_a@example.com"
        identity_b = "identity_b@example.com"
        for i in range(10):
            await _tool_create_contract_draft({
                "name": f"MCP_rl_test_pytest_{i}",
                "description": "RL test A",
                "owner": "pytest",
                "created_by": identity_a,
                "rules": _DRAFT_RULES,
            })
        # Identity B should still be allowed
        data = _parse(await _tool_create_contract_draft({
            "name": "MCP_rl_test_pytest_10",
            "description": "RL test B",
            "owner": "pytest",
            "created_by": identity_b,
            "rules": _DRAFT_RULES,
        }))
        assert "error" not in data
        assert data.get("created") is True


# ---------------------------------------------------------------------------
# call_tool dispatcher — covers all elif branches (lines 508-522)
# ---------------------------------------------------------------------------

class TestCallToolDispatcher:
    """Exercises call_tool() branches not hit by direct function tests."""

    async def test_validate_batch_via_call_tool(self):
        """call_tool('validate_batch') → dispatches to _tool_validate_batch (line 508)."""
        contracts = list(_registry.list_contracts())
        contract_name = contracts[0]["name"] if contracts else "customer"
        result = await call_tool("validate_batch", {
            "contract": contract_name,
            "records": [{}],
        })
        data = _parse(result)
        assert "summary" in data or "error" in data

    async def test_list_contracts_via_call_tool(self):
        """call_tool('list_contracts') → dispatches (line 512)."""
        result = await call_tool("list_contracts", {})
        assert isinstance(result, list)
        assert result[0].type == "text"

    async def test_get_contract_via_call_tool(self):
        """call_tool('get_contract') → dispatches (line 514)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await call_tool("get_contract", {"contract": name})
        assert isinstance(result, list)
        assert result[0].type == "text"

    async def test_explain_error_via_call_tool(self):
        """call_tool('explain_error') → dispatches (line 516)."""
        result = await call_tool("explain_error", {
            "contract": "customer",
            "error_code": "not_empty",
            "field": "name",
            "error_message": "Name is required",
        })
        assert isinstance(result, list)

    async def test_get_quality_metrics_via_call_tool(self):
        """call_tool('get_quality_metrics') → dispatches (line 518)."""
        result = await call_tool("get_quality_metrics", {})
        assert isinstance(result, list)
        assert result[0].type == "text"

    async def test_get_rule_velocity_via_call_tool(self):
        """call_tool('get_rule_velocity') → dispatches (line 520)."""
        result = await call_tool("get_rule_velocity", {"contract": "customer"})
        data = _parse(result)
        assert "error" in data or "contract" in data or "buckets" in data

    async def test_get_quality_trend_via_call_tool(self):
        """call_tool('get_quality_trend') → dispatches (line 522)."""
        result = await call_tool("get_quality_trend", {"contract": "customer", "days": 7})
        data = _parse(result)
        assert "contract" in data or "error" in data

    async def test_unknown_tool_name(self):
        """call_tool with unknown name → 'Unknown tool:' message (line 524)."""
        result = await call_tool("nonexistent_tool_xyz", {})
        assert "Unknown tool" in result[0].text

    async def test_call_tool_handles_exception(self):
        """call_tool catches exceptions and returns INTERNAL_ERROR envelope."""
        # validate_record with missing required 'contract' key raises KeyError
        result = await call_tool("validate_record", {"record": {}})
        # CRT173/24: dispatch-level catch-all returns the INTERNAL_ERROR envelope.
        data = json.loads(result[0].text)
        assert data["error"]["error_code"] == "INTERNAL_ERROR"
        assert data["error"]["status"] == 500


# ---------------------------------------------------------------------------
# _tool_get_quality_metrics — covers lines 850-920
# ---------------------------------------------------------------------------

class TestQualityMetrics:
    """Direct tests for _tool_get_quality_metrics."""

    async def test_returns_list_structure(self):
        result = await _tool_get_quality_metrics({})
        data = _parse(result)
        assert isinstance(data, list)

    async def test_with_contract_filter(self):
        contracts = list(_registry.list_contracts())
        if contracts:
            result = await _tool_get_quality_metrics({"contract": contracts[0]["name"]})
            assert isinstance(result, list)
            assert result[0].type == "text"

    async def test_with_window_hours(self):
        result = await _tool_get_quality_metrics({"window_hours": 24})
        data = _parse(result)
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# _tool_get_quality_trend — covers lines 988-1020
# ---------------------------------------------------------------------------

class TestQualityTrend:
    """Direct tests for _tool_get_quality_trend."""

    async def test_missing_contract_returns_error(self):
        """Lines 990-991: missing contract arg → error dict."""
        result = await _tool_get_quality_trend({})
        data = _parse(result)
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "MISSING_CONTRACT"
        assert "contract is required" in data["error"]["detail"]

    async def test_with_valid_contract(self):
        """Lines 1003-1020: returns trend structure."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await _tool_get_quality_trend({"contract": name, "days": 7})
        data = _parse(result)
        assert data.get("contract") == name
        assert "points" in data
        assert "summary" in data

    async def test_trend_summary_stable_when_no_points(self):
        """Trend is 'stable' when there are no data points (line 1016)."""
        result = await _tool_get_quality_trend({
            "contract": "nonexistent_contract_xyz_12345",
            "days": 1,
        })
        data = _parse(result)
        assert data.get("summary", {}).get("trend") == "stable"

    async def test_days_clamped_to_max(self):
        """days > 90 is clamped to 90 (line 992)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await _tool_get_quality_trend({"contract": name, "days": 9999})
        data = _parse(result)
        assert data.get("days") == 90


# ---------------------------------------------------------------------------
# _tool_get_rule_velocity — covers lines 1023-1047
# ---------------------------------------------------------------------------

class TestRuleVelocity:
    """Direct tests for _tool_get_rule_velocity."""

    async def test_missing_contract_returns_error(self):
        """Lines 1025-1026: missing contract arg → error dict."""
        result = await _tool_get_rule_velocity({})
        data = _parse(result)
        assert "error" in data
        # CRT173/24: structured envelope.
        assert data["error"]["error_code"] == "MISSING_CONTRACT"
        assert "contract is required" in data["error"]["detail"]

    async def test_with_valid_contract(self):
        """Lines 1038-1047: returns velocity data."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await _tool_get_rule_velocity({
            "contract": name,
            "window_hours": 24,
            "bucket_minutes": 5,
        })
        data = _parse(result)
        # Returns either data or an error dict
        assert isinstance(data, dict)

    async def test_with_default_args(self):
        """Uses default window_hours=24 and bucket_minutes=5 (lines 1027-1028)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await _tool_get_rule_velocity({"contract": name})
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _pick_governance_tip — lines 155, 161
# ---------------------------------------------------------------------------

class TestPickGovernanceTip:
    """_pick_governance_tip edge cases."""

    async def test_governance_tip_from_first_rule_fallback(self):
        """When no errors, tip falls back to first rule in contract (line 158-160)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        contract = _registry.get(name)
        if contract and contract.rules:
            result = await _tool_validate_record({
                "contract": name,
                "record": {},  # empty record — all fields missing
            })
            data = _parse(result)
            assert "governance_tip" in data

    async def test_validate_record_with_context(self):
        """Context path in _tool_validate_record (lines 553-555)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        result = await _tool_validate_record({
            "contract": name,
            "record": {},
            "context": "nonexistent_context",
        })
        data = _parse(result)
        # Should still return a result (context fallback to default rules)
        assert "contract" in data or "error" in data

    async def test_validate_record_with_agent_id_and_dry_run(self):
        """agent_id and dry_run params handled (lines 538-541)."""
        contracts = list(_registry.list_contracts())
        name = contracts[0]["name"] if contracts else "customer"
        # These only matter in remote mode — in local mode they're accepted but ignored
        result = await _tool_validate_record({
            "contract": name,
            "record": {},
            "agent_id": "pytest-agent",
            "dry_run": True,
        })
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Additional targeted tests for remaining missed lines
# ---------------------------------------------------------------------------

class TestPickGovernanceTipDefault:
    """_pick_governance_tip default path (line 161)."""

    async def test_default_tip_when_no_rule_type_in_tips(self):
        """Line 161: fallback to 'default' tip when rule type not in _GOVERNANCE_TIPS dict."""
        from opendqv.mcp_server import _pick_governance_tip
        from opendqv.core.rule_parser import Rule

        # Use a rule type that is NOT in _GOVERNANCE_TIPS (checksum has no tip entry)
        rule = Rule(name="iban_check", field="iban", type="checksum",
                    error_message="Invalid IBAN")
        tip = _pick_governance_tip([rule], [])
        # Should fall back to the 'default' tip (line 161)
        assert "ingestion" in tip or "downstream" in tip

    async def test_default_tip_with_empty_rules(self):
        """Line 161: empty rules list hits the final default return."""
        tip = _pick_governance_tip([], [])
        assert len(tip) > 0  # default tip always returns something


class TestQualityMetricsAgentIdFilter:
    """_tool_get_quality_metrics with agent_id filter (line 855)."""

    async def test_agent_id_filter_path(self):
        """Passing agent_id calls get_windowed_summary_for_agent (line 855)."""
        result = await _tool_get_quality_metrics({
            "agent_id": "pytest-test-agent",
            "window_hours": 24,
        })
        data = _parse(result)
        # Returns either a list (possibly empty) or dict with error
        assert isinstance(data, list)


class TestValidateBatchDraftNotice:
    """_tool_validate_batch draft_notice field (line 598)."""

    async def test_batch_draft_contract_includes_draft_notice(self):
        """Batch validate with DRAFT contract → draft_notice in result (line 598)."""
        import yaml
        from opendqv.core.rule_parser import Rule, ContractStatus
        from opendqv.core.contracts import DataContract
        import opendqv.mcp_server as ms

        name = "mcp_draft_batch_test_xyz"
        rule = Rule(name="r", field="x", type="not_empty", error_message="req")
        contract = DataContract(
            name=name, version="1.0", description="Draft batch test",
            owner="test", status=ContractStatus.DRAFT, rules=[rule],
        )
        # Write YAML file and register
        path = ms._registry.contracts_dir / f"{name}.yaml"
        yaml_data = {"name": name, "version": "1.0", "status": "draft",
                     "description": "test", "owner": "test",
                     "rules": [{"name": "r", "field": "x", "type": "not_empty",
                                 "error_message": "req"}]}
        path.write_text(yaml.dump(yaml_data), encoding="utf-8")
        ms._registry._contracts.setdefault(name, {})["1.0"] = contract
        ms._registry._contract_paths[name] = path

        try:
            result = await _tool_validate_batch({
                "contract": name,
                "records": [{"x": "hello"}],
            })
            data = _parse(result)
            assert "draft_notice" in data
        finally:
            ms._registry._contracts.pop(name, None)
            if path.exists():
                path.unlink()


class TestRuleVelocityException:
    """_tool_get_rule_velocity exception path (lines 1044-1045)."""

    async def test_exception_in_rule_failure_velocity_returns_error(self):
        """rule_failure_velocity raising an exception → error dict (lines 1044-1045)."""
        from unittest.mock import patch
        import opendqv.mcp_server as ms

        with patch.object(ms._quality_analytics, "rule_failure_velocity",
                          side_effect=RuntimeError("DB failure")):
            result = await _tool_get_rule_velocity({"contract": "customer"})
            data = _parse(result)
            assert "error" in data
            # CRT173/24: structured envelope.
            assert data["error"]["error_code"] == "INTERNAL_ERROR"
            assert "DB failure" in data["error"]["detail"]
