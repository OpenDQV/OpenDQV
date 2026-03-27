"""Tests for the Python SDK client library."""

import pytest
from unittest.mock import MagicMock
from sdk.client import AsyncOpenDQVClient, OpenDQVClient, ValidationError, _extract_record


class TestSDKViaAPI:
    """
    Test SDK by mocking httpx to use FastAPI TestClient responses.
    This tests the SDK logic (request building, response parsing, guard decorator)
    without needing a real HTTP server.
    """

    @pytest.fixture
    def mock_client(self, client, auth_headers):
        """Create an SDK client that delegates to the FastAPI TestClient."""
        sdk = OpenDQVClient.__new__(OpenDQVClient)
        sdk.base_url = "http://testserver"

        # Create a mock httpx client that routes through FastAPI TestClient
        mock_http = MagicMock()

        def mock_post(path, json=None, params=None, **kwargs):
            """Route SDK POST calls through the FastAPI TestClient."""
            resp_obj = client.post(path, json=json, params=params, headers=auth_headers)
            # Create a mock response with the right interface
            mock_resp = MagicMock()
            mock_resp.status_code = resp_obj.status_code
            mock_resp.json.return_value = resp_obj.json()
            mock_resp.raise_for_status.side_effect = None if resp_obj.status_code < 400 else Exception(f"HTTP {resp_obj.status_code}")
            return mock_resp

        def mock_get(path, params=None, **kwargs):
            resp_obj = client.get(path, params=params, headers=auth_headers)
            mock_resp = MagicMock()
            mock_resp.status_code = resp_obj.status_code
            mock_resp.json.return_value = resp_obj.json()
            mock_resp.raise_for_status.side_effect = None if resp_obj.status_code < 400 else Exception(f"HTTP {resp_obj.status_code}")
            return mock_resp

        mock_http.post = mock_post
        mock_http.get = mock_get
        mock_http.close = MagicMock()
        sdk._client = mock_http
        return sdk

    def test_validate_valid_record(self, mock_client):
        result = mock_client.validate(
            {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            contract="customer",
        )
        assert result["valid"] is True
        assert result["contract"] == "customer"

    def test_validate_invalid_record(self, mock_client):
        result = mock_client.validate(
            {"email": "bad", "age": -5, "name": ""},
            contract="customer",
        )
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_with_context(self, mock_client):
        result = mock_client.validate(
            {"email": "kid@example.com", "age": 25, "name": "Kiddo"},
            contract="customer",
            context="kids_app",
        )
        age_errors = [e for e in result["errors"] if e["field"] == "age"]
        assert len(age_errors) > 0

    def test_validate_batch(self, mock_client):
        result = mock_client.validate_batch(
            [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad", "age": -5, "name": ""},
            ],
            contract="customer",
        )
        assert result["summary"]["total"] == 2
        assert result["summary"]["failed"] > 0

    def test_list_contracts(self, mock_client):
        contracts = mock_client.contracts()
        assert len(contracts) > 0
        names = [c["name"] for c in contracts]
        assert "customer" in names

    def test_get_contract_detail(self, mock_client):
        detail = mock_client.contract("customer")
        assert detail["name"] == "customer"
        assert len(detail["rules"]) > 0
        assert "status" in detail

    def test_validate_record_id_echoed(self, mock_client):
        result = mock_client.validate(
            {"email": "a@b.com"},
            contract="customer",
            record_id="my-tracking-id",
        )
        assert result["record_id"] == "my-tracking-id"


class TestGuardDecorator:
    """Test the @client.guard() decorator."""

    @pytest.fixture
    def mock_client(self, client, auth_headers):
        sdk = OpenDQVClient.__new__(OpenDQVClient)
        sdk.base_url = "http://testserver"
        mock_http = MagicMock()

        def mock_post(path, json=None, params=None, **kwargs):
            resp_obj = client.post(path, json=json, params=params, headers=auth_headers)
            mock_resp = MagicMock()
            mock_resp.status_code = resp_obj.status_code
            mock_resp.json.return_value = resp_obj.json()
            mock_resp.raise_for_status.side_effect = None if resp_obj.status_code < 400 else Exception(f"HTTP {resp_obj.status_code}")
            return mock_resp

        mock_http.post = mock_post
        mock_http.close = MagicMock()
        sdk._client = mock_http
        return sdk

    def test_guard_passes_valid_record(self, mock_client):
        @mock_client.guard(contract="customer")
        def save(data):
            return "saved"

        result = save({
            "email": "test@example.com", "age": 25, "name": "Alice",
            "id": "12345", "phone": "+1234567890", "balance": 100,
            "score": 85, "date": "2024-01-15", "username": "alice_w",
            "password": "securepass123",
        })
        assert result == "saved"

    def test_guard_blocks_invalid_record(self, mock_client):
        @mock_client.guard(contract="customer")
        def save(data):
            return "saved"

        with pytest.raises(ValidationError) as exc_info:
            save({"email": "bad", "age": -5, "name": ""})

        assert len(exc_info.value.errors) > 0
        assert "field" in exc_info.value.errors[0]

    def test_guard_with_kwarg(self, mock_client):
        @mock_client.guard(contract="customer", record_param="customer_data")
        def save(customer_data):
            return "saved"

        with pytest.raises(ValidationError):
            save(customer_data={"email": "bad", "name": ""})


class TestExtractRecord:
    """Test the record extraction helper."""

    def test_extract_from_kwargs(self):
        def func(data): pass
        result = _extract_record(func, (), {"data": {"a": 1}}, "data")
        assert result == {"a": 1}

    def test_extract_from_positional(self):
        def func(data): pass
        result = _extract_record(func, ({"a": 1},), {}, "data")
        assert result == {"a": 1}

    def test_extract_fallback_first_arg(self):
        def func(x): pass
        result = _extract_record(func, ({"a": 1},), {}, "data")
        assert result == {"a": 1}

    def test_extract_raises_on_missing(self):
        def func(): pass
        with pytest.raises(ValueError, match="Could not find record data"):
            _extract_record(func, (), {}, "data")


class TestLocalValidator:
    """LocalValidator — in-process validation without an API server."""

    def _contracts_dir(self):
        import os
        return os.path.join(os.path.dirname(__file__), "..", "contracts")

    def test_loads_contracts(self):
        from sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        names = [c["name"] for c in v.list_contracts()]
        assert "customer" in names

    def test_validate_valid_record(self):
        from sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        result = v.validate(
            {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            contract="customer",
        )
        assert result["valid"] is True
        assert result["contract"] == "customer"
        assert "version" in result

    def test_validate_invalid_record(self):
        from sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        result = v.validate({"email": "not-an-email", "age": -5}, contract="customer")
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_contract_not_found_raises(self):
        from sdk.local import LocalValidator, ContractNotFoundError
        v = LocalValidator(contracts_dir=self._contracts_dir())
        with pytest.raises(ContractNotFoundError, match="nonexistent"):
            v.validate({"email": "a@b.com"}, contract="nonexistent")

    def test_validate_batch_summary(self):
        from sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        records = [
            {"email": "a@b.com", "age": 25, "name": "Alice"},
            {"email": "bad",     "age": -1, "name": ""},
        ]
        result = v.validate_batch(records, contract="customer")
        assert result["summary"]["total"] == 2
        assert result["summary"]["passed"] == 1
        assert result["summary"]["failed"] == 1
        assert result["contract"] == "customer"

    def test_validate_batch_contract_not_found_raises(self):
        from sdk.local import LocalValidator, ContractNotFoundError
        v = LocalValidator(contracts_dir=self._contracts_dir())
        with pytest.raises(ContractNotFoundError):
            v.validate_batch([{"email": "a@b.com"}], contract="nope")

    def test_reload_does_not_raise(self):
        from sdk.local import LocalValidator
        v = LocalValidator(contracts_dir=self._contracts_dir())
        v.reload()  # smoke test — should not raise
        assert len(v.list_contracts()) > 0


# ── AsyncOpenDQVClient tests ──────────────────────────────────────────────────

def _make_async_mock_response(status_code: int, body):
    """Build a mock httpx response for async tests."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    if status_code >= 400:
        import httpx
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=mock_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


def _make_async_client(client_fixture, auth_headers_fixture):
    """Wire an AsyncOpenDQVClient to route through the FastAPI TestClient."""
    sdk = AsyncOpenDQVClient.__new__(AsyncOpenDQVClient)
    sdk.base_url = "http://testserver"
    sdk.contract_cache_dir = None

    mock_http = MagicMock()

    async def async_post(path, json=None, params=None, **kwargs):
        resp_obj = client_fixture.post(path, json=json, params=params, headers=auth_headers_fixture)
        return _make_async_mock_response(resp_obj.status_code, resp_obj.json())

    async def async_get(path, params=None, **kwargs):
        resp_obj = client_fixture.get(path, params=params, headers=auth_headers_fixture)
        return _make_async_mock_response(resp_obj.status_code, resp_obj.json())

    async def async_aclose():
        pass

    mock_http.post = async_post
    mock_http.get = async_get
    mock_http.aclose = async_aclose
    sdk._client = mock_http
    return sdk


class TestAsyncOpenDQVClient:
    """Tests for AsyncOpenDQVClient — mirrors sync client test coverage."""

    @pytest.fixture
    def async_client(self, client, auth_headers):
        return _make_async_client(client, auth_headers)

    @pytest.mark.asyncio
    async def test_validate_valid_record(self, async_client):
        result = await async_client.validate(
            {
                "email": "test@example.com", "age": 25, "name": "Alice",
                "id": "12345", "phone": "+1234567890", "balance": 100,
                "score": 85, "date": "2024-01-15", "username": "alice_w",
                "password": "securepass123",
            },
            contract="customer",
        )
        assert result["valid"] is True
        assert result["contract"] == "customer"

    @pytest.mark.asyncio
    async def test_validate_invalid_record(self, async_client):
        result = await async_client.validate(
            {"email": "bad", "age": -5, "name": ""},
            contract="customer",
        )
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    @pytest.mark.asyncio
    async def test_validate_with_record_id(self, async_client):
        result = await async_client.validate(
            {"email": "a@b.com"},
            contract="customer",
            record_id="async-tracking-id",
        )
        assert result["record_id"] == "async-tracking-id"

    @pytest.mark.asyncio
    async def test_validate_batch(self, async_client):
        result = await async_client.validate_batch(
            [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad", "age": -5, "name": ""},
            ],
            contract="customer",
        )
        assert result["summary"]["total"] == 2
        assert result["summary"]["failed"] > 0

    @pytest.mark.asyncio
    async def test_contracts(self, async_client):
        contracts = await async_client.contracts()
        assert len(contracts) > 0
        names = [c["name"] for c in contracts]
        assert "customer" in names

    @pytest.mark.asyncio
    async def test_contract_detail(self, async_client):
        detail = await async_client.contract("customer")
        assert detail["name"] == "customer"
        assert len(detail["rules"]) > 0

    @pytest.mark.asyncio
    async def test_lint_clean_contract(self, async_client):
        result = await async_client.lint("customer")
        assert result["passed"] is True
        assert result["error_count"] == 0

    @pytest.mark.asyncio
    async def test_context_manager(self, async_client):
        async with async_client as c:
            result = await c.validate(
                {"email": "test@example.com", "age": 25, "name": "Alice"},
                contract="customer",
            )
        assert "valid" in result


class TestAsyncGuardDecorator:
    """Tests for @async_client.guard() decorator."""

    @pytest.fixture
    def async_client(self, client, auth_headers):
        return _make_async_client(client, auth_headers)

    @pytest.mark.asyncio
    async def test_guard_passes_valid_record(self, async_client):
        @async_client.guard(contract="customer")
        async def save(data):
            return "saved"

        result = await save({
            "email": "test@example.com", "age": 25, "name": "Alice",
            "id": "12345", "phone": "+1234567890", "balance": 100,
            "score": 85, "date": "2024-01-15", "username": "alice_w",
            "password": "securepass123",
        })
        assert result == "saved"

    @pytest.mark.asyncio
    async def test_guard_blocks_invalid_record(self, async_client):
        @async_client.guard(contract="customer")
        async def save(data):
            return "saved"

        with pytest.raises(ValidationError) as exc_info:
            await save({"email": "bad", "age": -5, "name": ""})

        assert len(exc_info.value.errors) > 0

    @pytest.mark.asyncio
    async def test_guard_with_kwarg(self, async_client):
        @async_client.guard(contract="customer", record_param="customer_data")
        async def save(customer_data):
            return "saved"

        with pytest.raises(ValidationError):
            await save(customer_data={"email": "bad", "name": ""})


class TestSyncClientLint:
    """Tests for OpenDQVClient.lint() — new method on sync client."""

    @pytest.fixture
    def mock_client(self, client, auth_headers):
        sdk = OpenDQVClient.__new__(OpenDQVClient)
        sdk.base_url = "http://testserver"
        mock_http = MagicMock()

        def mock_get(path, params=None, **kwargs):
            resp_obj = client.get(path, params=params, headers=auth_headers)
            mock_resp = MagicMock()
            mock_resp.status_code = resp_obj.status_code
            mock_resp.json.return_value = resp_obj.json()
            mock_resp.raise_for_status.side_effect = (
                None if resp_obj.status_code < 400 else Exception(f"HTTP {resp_obj.status_code}")
            )
            return mock_resp

        mock_http.get = mock_get
        sdk._client = mock_http
        return sdk

    def test_lint_clean_contract_passes(self, mock_client):
        result = mock_client.lint("customer")
        assert result["passed"] is True
        assert result["error_count"] == 0

    def test_lint_unknown_contract_raises(self, mock_client):
        with pytest.raises(Exception):
            mock_client.lint("nonexistent_contract_xyz")
