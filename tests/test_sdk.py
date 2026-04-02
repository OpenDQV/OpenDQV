"""
SDK unit tests — covers sdk/client.py and sdk/local.py.

Uses unittest.mock to avoid real HTTP calls for the client tests.
LocalValidator tests use the temp contracts dir set up by conftest.py.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sdk.client import (
    AsyncOpenDQVClient,
    OpenDQVClient,
    ValidationError,
    _extract_record,
)
from sdk.local import ContractNotFoundError, LocalValidator


# ---------------------------------------------------------------------------
# LocalValidator — no HTTP, uses temp contracts dir from conftest
# ---------------------------------------------------------------------------

class TestLocalValidator:
    def test_validate_clean_record(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        result = v.validate({"name": "Alice", "age": 30, "email": "alice@example.com"}, contract="customer")
        assert isinstance(result, dict)
        assert "valid" in result
        assert result["contract"] == "customer"

    def test_validate_returns_result_on_bad_record(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        result = v.validate({}, contract="customer")
        assert "valid" in result

    def test_validate_unknown_contract_raises(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        with pytest.raises(ContractNotFoundError, match="not found"):
            v.validate({"x": 1}, contract="__nonexistent_contract__")

    def test_list_contracts_returns_list(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        contracts = v.list_contracts()
        assert isinstance(contracts, list)
        assert len(contracts) > 0

    def test_validate_batch_clean(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        records = [
            {"name": "Alice", "age": 30, "email": "a@b.com"},
            {"name": "Bob", "age": 25, "email": "b@b.com"},
        ]
        result = v.validate_batch(records, contract="customer")
        assert "summary" in result
        assert result["contract"] == "customer"

    def test_validate_batch_unknown_contract_raises(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        with pytest.raises(ContractNotFoundError):
            v.validate_batch([{"x": 1}], contract="__nonexistent__")

    def test_reload_does_not_raise(self):
        v = LocalValidator(contracts_dir=os.environ["OPENDQV_CONTRACTS_DIR"])
        v.reload()

    def test_default_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENDQV_CONTRACTS_DIR", os.environ["OPENDQV_CONTRACTS_DIR"])
        v = LocalValidator()
        assert v.contracts_dir is not None

    def test_cwd_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENDQV_CONTRACTS_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        v = LocalValidator()
        assert str(v.contracts_dir).endswith("contracts")


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------

class TestValidationError:
    def test_message_includes_fields(self):
        err = ValidationError([{"field": "email", "message": "invalid"}])
        assert "email" in str(err)

    def test_warnings_default_empty(self):
        err = ValidationError([{"field": "age", "message": "too low"}])
        assert err.warnings == []

    def test_warnings_stored(self):
        err = ValidationError(
            [{"field": "age", "message": "low"}],
            [{"field": "name", "message": "warn"}],
        )
        assert len(err.warnings) == 1


# ---------------------------------------------------------------------------
# _extract_record
# ---------------------------------------------------------------------------

class TestExtractRecord:
    def test_from_kwargs(self):
        def fn(data): pass
        assert _extract_record(fn, (), {"data": {"x": 1}}, "data") == {"x": 1}

    def test_from_positional(self):
        def fn(data): pass
        assert _extract_record(fn, ({"x": 2},), {}, "data") == {"x": 2}

    def test_fallback_first_arg(self):
        def fn(other): pass
        assert _extract_record(fn, ({"x": 3},), {}, "data") == {"x": 3}

    def test_raises_when_no_args(self):
        def fn(): pass
        with pytest.raises(ValueError, match="Could not find record"):
            _extract_record(fn, (), {}, "data")


# ---------------------------------------------------------------------------
# OpenDQVClient (sync) — mock httpx
# ---------------------------------------------------------------------------

def _make_client(token="test-token", cache_dir=None):
    with patch("sdk.client.httpx.Client"):
        client = OpenDQVClient("http://localhost:8000", token=token,
                               contract_cache_dir=cache_dir)
    return client


def _sync_resp(data):
    r = MagicMock()
    r.json.return_value = data
    return r


class TestOpenDQVClientValidate:
    def test_posts_correct_body(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        result = client.validate({"email": "a@b.com"}, contract="customer")
        assert result["valid"] is True
        body = client._client.post.call_args[1]["json"]
        assert body["contract"] == "customer"
        assert body["version"] == "latest"

    def test_passes_context(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        client.validate({"x": 1}, contract="c", context="eu")
        assert client._client.post.call_args[1]["json"]["context"] == "eu"

    def test_observe_only(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        client.validate({"x": 1}, contract="c", observe_only=True)
        assert client._client.post.call_args[1]["json"]["observe_only"] is True

    def test_allow_draft_param(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        client.validate({"x": 1}, contract="c", allow_draft=True)
        assert client._client.post.call_args[1]["params"].get("allow_draft") == "true"

    def test_record_id_included(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        client.validate({"x": 1}, contract="c", record_id="rec-123")
        assert client._client.post.call_args[1]["json"]["record_id"] == "rec-123"

    def test_raises_on_http_error(self):
        client = _make_client()
        client._client.post.return_value = MagicMock(
            raise_for_status=MagicMock(side_effect=httpx.HTTPStatusError(
                "err", request=MagicMock(), response=MagicMock())))
        with pytest.raises(httpx.HTTPStatusError):
            client.validate({"x": 1}, contract="c")


class TestOpenDQVClientBatch:
    def test_posts_records(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"summary": {"total": 2}, "results": []})
        records = [{"x": 1}, {"x": 2}]
        result = client.validate_batch(records, contract="customer")
        assert result["summary"]["total"] == 2
        assert client._client.post.call_args[1]["json"]["records"] == records

    def test_observe_only_batch(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"summary": {}, "results": []})
        client.validate_batch([{"x": 1}], contract="c", observe_only=True)
        assert client._client.post.call_args[1]["json"]["observe_only"] is True

    def test_allow_draft_batch(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"summary": {}, "results": []})
        client.validate_batch([{"x": 1}], contract="c", allow_draft=True)
        assert client._client.post.call_args[1]["params"].get("allow_draft") == "true"


class TestOpenDQVClientContracts:
    def test_contracts_list(self):
        client = _make_client()
        client._client.get.return_value = _sync_resp([{"name": "customer"}])
        assert isinstance(client.contracts(), list)

    def test_include_all_param(self):
        client = _make_client()
        client._client.get.return_value = _sync_resp([])
        client.contracts(include_all=True)
        assert client._client.get.call_args[1]["params"].get("include_all") == "true"

    def test_contract_by_name(self):
        client = _make_client()
        client._client.get.return_value = _sync_resp({"name": "customer", "rules": []})
        assert client.contract("customer")["name"] == "customer"

    def test_contract_cache_fallback(self, tmp_path):
        client = _make_client(cache_dir=str(tmp_path))
        cached = {"name": "customer", "rules": [], "cached": True}
        (tmp_path / "customer.json").write_text(json.dumps(cached), encoding="utf-8")
        client._client.get.side_effect = httpx.RequestError("down")
        result = client.contract("customer")
        assert result["cached"] is True

    def test_contract_raises_when_no_cache(self):
        client = _make_client()
        client._client.get.side_effect = httpx.RequestError("down")
        with pytest.raises(httpx.RequestError):
            client.contract("customer")

    def test_lint(self):
        client = _make_client()
        client._client.get.return_value = _sync_resp({"passed": True, "error_count": 0})
        assert client.lint("customer")["passed"] is True


class TestOpenDQVClientContextManager:
    def test_context_manager_closes(self):
        client = _make_client()
        with client as c:
            assert c is client
        client._client.close.assert_called_once()

    def test_close_directly(self):
        client = _make_client()
        client.close()
        client._client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Guard decorator — sync
# ---------------------------------------------------------------------------

class TestGuardDecoratorSync:
    def _passing_client(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({"valid": True, "errors": [], "warnings": []})
        return client

    def _failing_client(self):
        client = _make_client()
        client._client.post.return_value = _sync_resp({
            "valid": False,
            "errors": [{"field": "email", "message": "invalid"}],
            "warnings": []})
        return client

    def test_allows_clean_record(self):
        @self._passing_client().guard(contract="customer")
        def save(data: dict):
            return "saved"
        assert save(data={"email": "a@b.com"}) == "saved"

    def test_raises_on_failure(self):
        @self._failing_client().guard(contract="customer")
        def save(data: dict):
            return "saved"
        with pytest.raises(ValidationError) as exc_info:
            save(data={"email": "bad"})
        assert "email" in str(exc_info.value)

    def test_wraps_async_function(self):
        import asyncio
        client = self._passing_client()

        @client.guard(contract="customer")
        async def async_save(data: dict):
            return "async_saved"

        result = asyncio.get_event_loop().run_until_complete(
            async_save(data={"email": "a@b.com"}))
        assert result == "async_saved"

    def test_custom_record_param(self):
        @self._passing_client().guard(contract="customer", record_param="payload")
        def save(payload: dict):
            return "saved"
        assert save(payload={"email": "a@b.com"}) == "saved"


# ---------------------------------------------------------------------------
# AsyncOpenDQVClient
# ---------------------------------------------------------------------------

def _make_async_client(cache_dir=None):
    with patch("sdk.client.httpx.AsyncClient"):
        client = AsyncOpenDQVClient("http://localhost:8000", token="test-token",
                                    contract_cache_dir=cache_dir)
    return client


def _async_resp(data):
    r = AsyncMock()
    r.raise_for_status = MagicMock()  # raise_for_status is sync in httpx
    r.json = MagicMock(return_value=data)  # json() is also sync in httpx
    return r


class TestAsyncOpenDQVClientValidate:
    @pytest.mark.asyncio
    async def test_validate_posts_body(self):
        client = _make_async_client()
        client._client.post = AsyncMock(return_value=_async_resp(
            {"valid": True, "errors": [], "warnings": []}))
        result = await client.validate({"email": "a@b.com"}, contract="customer")
        assert result["valid"] is True
        assert client._client.post.call_args[1]["json"]["contract"] == "customer"

    @pytest.mark.asyncio
    async def test_validate_batch(self):
        client = _make_async_client()
        client._client.post = AsyncMock(return_value=_async_resp(
            {"summary": {"total": 1}, "results": []}))
        result = await client.validate_batch([{"x": 1}], contract="c")
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_contracts_list(self):
        client = _make_async_client()
        client._client.get = AsyncMock(return_value=_async_resp([{"name": "customer"}]))
        result = await client.contracts()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_lint(self):
        client = _make_async_client()
        client._client.get = AsyncMock(return_value=_async_resp(
            {"passed": True, "error_count": 0}))
        result = await client.lint("customer")
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_context_manager(self):
        client = _make_async_client()
        client._client.aclose = AsyncMock()
        async with client as c:
            assert c is client
        client._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_guard_allows_clean(self):
        client = _make_async_client()
        client._client.post = AsyncMock(return_value=_async_resp(
            {"valid": True, "errors": [], "warnings": []}))

        @client.guard(contract="customer")
        async def save(data: dict):
            return "saved"

        assert await save(data={"email": "a@b.com"}) == "saved"

    @pytest.mark.asyncio
    async def test_guard_raises_on_failure(self):
        client = _make_async_client()
        client._client.post = AsyncMock(return_value=_async_resp({
            "valid": False,
            "errors": [{"field": "email", "message": "invalid"}],
            "warnings": []}))

        @client.guard(contract="customer")
        async def save(data: dict):
            return "saved"

        with pytest.raises(ValidationError):
            await save(data={"email": "bad"})

    @pytest.mark.asyncio
    async def test_contract_cache_write_and_fallback(self, tmp_path):
        client = _make_async_client(cache_dir=str(tmp_path))
        data = {"name": "customer", "rules": []}
        client._client.get = AsyncMock(return_value=_async_resp(data))
        result = await client.contract("customer")
        assert result["name"] == "customer"
        assert (tmp_path / "customer.json").exists()

        # Fallback path — API down, read from cache
        client._client.get = AsyncMock(side_effect=httpx.RequestError("down"))
        result = await client.contract("customer")
        assert result["name"] == "customer"
