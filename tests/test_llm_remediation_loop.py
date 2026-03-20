"""
Tests for the error remediation loop (docs/llm_integration.md §5 / scripts/llm_remediation_loop.py).

Strategy:
  - validate/explain calls hit the real running API on localhost:8000 — confirms the
    actual OpenDQV behaviour is as the doc describes
  - fix_with_claude is mocked — no Anthropic SDK or API key required

Tests are skipped automatically if the API is unreachable.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import llm_remediation_loop as _loop


# ── Helpers ────────────────────────────────────────────────────────────

def _claude_response(text: str):
    """Build a mock anthropic messages.create() return value with content[0].text = text."""
    item = MagicMock()
    item.text = text
    resp = MagicMock()
    resp.content = [item]
    return resp


def _claude_mock(text: str) -> MagicMock:
    """Return a mock anthropic module wired so Anthropic().messages.create() returns text."""
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value.messages.create.return_value = _claude_response(text)
    return mock_anthropic


# ── Skip marker ────────────────────────────────────────────────────────

def _api_available() -> bool:
    try:
        r = _requests.get("http://localhost:8000/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


api_available = pytest.mark.skipif(
    not _api_available(),
    reason="OpenDQV API not running on localhost:8000",
)


# ── Unit tests: fix_with_claude fence stripping ───────────────────────

class TestFixWithClaude:
    """fix_with_claude parses Claude's response correctly for all output formats."""

    def test_strips_json_fence(self):
        """Strips ```json ... ``` before parsing."""
        with patch.dict(sys.modules, {"anthropic": _claude_mock('```json\n{"amount": 25.0}\n```')}):
            result = _loop.fix_with_claude(
                {"amount": "£25.00"},
                [{"field": "amount", "message": "not a number",
                  "explanation": "must be float", "valid_examples": [1.0]}],
            )
        assert result == {"amount": 25.0}

    def test_strips_plain_fence(self):
        """Strips ``` fences without 'json' prefix."""
        with patch.dict(sys.modules, {"anthropic": _claude_mock('```\n{"amount": 25.0}\n```')}):
            result = _loop.fix_with_claude(
                {"amount": "£25.00"},
                [{"field": "amount", "message": "not a number",
                  "explanation": "must be float", "valid_examples": [1.0]}],
            )
        assert result == {"amount": 25.0}

    def test_bare_json(self):
        """Handles bare JSON with no fences."""
        with patch.dict(sys.modules, {"anthropic": _claude_mock('{"amount": 25.0}')}):
            result = _loop.fix_with_claude(
                {"amount": "bad"},
                [{"field": "amount", "message": "x", "explanation": "y", "valid_examples": []}],
            )
        assert result["amount"] == 25.0

    def test_preserves_all_fields(self):
        """Corrected record retains all original fields (Claude must not strip extras)."""
        fixed = '{"amount": 25.0, "currency": "GBP", "transaction_id": "TXN-001"}'
        with patch.dict(sys.modules, {"anthropic": _claude_mock(fixed)}):
            result = _loop.fix_with_claude(
                {"amount": "bad", "currency": "GBP", "transaction_id": "TXN-001"},
                [{"field": "amount", "message": "x", "explanation": "y", "valid_examples": []}],
            )
        assert result["currency"] == "GBP"
        assert result["transaction_id"] == "TXN-001"
        assert result["amount"] == 25.0


# ── Unit tests: loop logic (fully mocked) ────────────────────────────

class TestLoopLogicMocked:
    """validate_and_fix loop logic with mocked requests and anthropic."""

    def _mock_validate(self, valid: bool, errors=None):
        resp = MagicMock()
        resp.json.return_value = {"valid": valid, "errors": errors or [], "warnings": []}
        return resp

    def _mock_explain(self, explanation="must be a float", valid_examples=None):
        resp = MagicMock()
        resp.json.return_value = {
            "explanation": explanation,
            "valid_examples": valid_examples or [1.0, 2.0],
        }
        return resp

    def test_passes_on_first_attempt(self):
        """Valid record returns immediately — no explain or Claude calls."""
        with patch("llm_remediation_loop.requests") as mock_req:
            mock_req.post.return_value = self._mock_validate(True)
            outcome = _loop.validate_and_fix("banking_transaction", {"amount": 25.0})

        assert outcome["status"] == "ok"
        assert outcome["attempts"] == 1
        mock_req.get.assert_not_called()

    def test_fix_on_second_attempt(self):
        """Fails attempt 1, Claude fixes, passes attempt 2."""
        errors = [{"field": "amount", "rule": "amount_min", "message": "must be > 0"}]
        with patch("llm_remediation_loop.requests") as mock_req, \
             patch.dict(sys.modules, {"anthropic": _claude_mock('{"amount": 25.0}')}):
            mock_req.post.side_effect = [
                self._mock_validate(False, errors),
                self._mock_validate(True),
            ]
            mock_req.get.return_value = self._mock_explain()
            outcome = _loop.validate_and_fix("banking_transaction", {"amount": "£25.00"})

        assert outcome["status"] == "ok"
        assert outcome["attempts"] == 2
        assert mock_req.post.call_count == 2
        mock_req.get.assert_called_once()

    def test_escalates_after_max_retries(self):
        """Corrupt data is never written — escalated after MAX_RETRIES."""
        errors = [{"field": "amount", "rule": "amount_min", "message": "must be > 0"}]
        with patch("llm_remediation_loop.requests") as mock_req, \
             patch.dict(sys.modules, {"anthropic": _claude_mock('{"amount": "still bad"}')}):
            mock_req.post.return_value = self._mock_validate(False, errors)
            mock_req.get.return_value = self._mock_explain()
            outcome = _loop.validate_and_fix("banking_transaction", {"amount": "bad"})

        assert outcome["status"] == "escalate"
        assert "escalate" in outcome["message"].lower()
        assert mock_req.post.call_count == _loop.MAX_RETRIES + 1

    def test_one_explain_call_per_error(self):
        """One explain_error GET per failing rule, not one per attempt."""
        errors = [
            {"field": "amount", "rule": "amount_min", "message": "must be > 0"},
            {"field": "currency", "rule": "currency_lookup", "message": "invalid currency"},
        ]
        with patch("llm_remediation_loop.requests") as mock_req, \
             patch.dict(sys.modules,
                        {"anthropic": _claude_mock('{"amount": 10.0, "currency": "GBP"}')}):
            mock_req.post.side_effect = [
                self._mock_validate(False, errors),
                self._mock_validate(True),
            ]
            mock_req.get.return_value = self._mock_explain()
            _loop.validate_and_fix("banking_transaction", {"amount": "bad", "currency": "XX"})

        assert mock_req.get.call_count == 2

    def test_explain_url_contains_contract_field_rule(self):
        """explain_error GET URL must contain contract name, field, and rule name."""
        errors = [{"field": "amount", "rule": "amount_min", "message": "x"}]
        with patch("llm_remediation_loop.requests") as mock_req, \
             patch.dict(sys.modules, {"anthropic": _claude_mock('{"amount": 1.0}')}):
            mock_req.post.side_effect = [
                self._mock_validate(False, errors),
                self._mock_validate(True),
            ]
            mock_req.get.return_value = self._mock_explain()
            _loop.validate_and_fix("banking_transaction", {"amount": "x"})

        url = mock_req.get.call_args[0][0]
        assert "banking_transaction" in url
        assert "amount" in url
        assert "amount_min" in url


# ── Integration tests (real API on localhost:8000) ─────────────────────

class TestLoopIntegration:
    """End-to-end loop with real OpenDQV API; Claude mocked."""

    @api_available
    def test_valid_record_passes_first_attempt(self):
        """Genuinely valid record passes on attempt 1; Claude never called."""
        record = {
            "transaction_id": "TXN-TEST-001",
            "account_number": "ACC123456",
            "transaction_date": "2026-03-14",
            "amount": 100.0,
            "currency": "GBP",
            "transaction_type": "credit",
            "channel": "online",
            "merchant_id": "MERCH-001",
            "merchant_category_code": "5411",
        }
        mock_a = _claude_mock("{}")  # should never be called
        with patch.dict(sys.modules, {"anthropic": mock_a}):
            outcome = _loop.validate_and_fix("banking_transaction", record)

        assert outcome["status"] == "ok"
        assert outcome["attempts"] == 1
        mock_a.Anthropic.return_value.messages.create.assert_not_called()

    @api_available
    def test_invalid_record_fixed_by_mock_claude(self):
        """Invalid record: real API returns errors, mock Claude fixes, real API confirms."""
        bad_record = {
            "transaction_id": "TXN-TEST-002",
            "account_number": "ACC123",
            "transaction_date": "2026-03-14",
            "amount": -1.0,   # below minimum
            "currency": "GBP",
            "transaction_type": "credit",
            "channel": "online",
            "merchant_id": "MERCH-001",
            "merchant_category_code": "5411",
        }
        fixed = {**bad_record, "amount": 10.0}
        with patch.dict(sys.modules, {"anthropic": _claude_mock(json.dumps(fixed))}):
            outcome = _loop.validate_and_fix("banking_transaction", bad_record)

        assert outcome["status"] == "ok"
        assert outcome["record"]["amount"] == 10.0
        assert outcome["attempts"] == 2

    @api_available
    def test_explain_error_endpoint_returns_useful_hints(self):
        """The real explain_error endpoint returns non-empty explanation and valid_examples."""
        resp = _requests.get(
            "http://localhost:8000/api/v1/contracts/banking_transaction"
            "/explain/amount/amount_min",
            timeout=5,
        ).json()
        assert resp.get("explanation"), "explain_error returned empty explanation"
        assert resp.get("valid_examples"), "explain_error returned no valid examples"
        assert "amount" in resp["explanation"].lower()
