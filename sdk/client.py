"""
OpenDQV Python SDK client.

A lightweight client for calling the OpenDQV validation API.
Designed for source system developers to integrate data quality checks
with minimal code.

Two client classes:
  OpenDQVClient      — synchronous, uses httpx.Client
  AsyncOpenDQVClient — async/await, uses httpx.AsyncClient
                       Safe for FastAPI endpoints and Kafka consumers.

Examples:
    # Synchronous
    client = OpenDQVClient("http://opendqv.internal:8000", token="...")
    result = client.validate({"email": "test@example.com"}, contract="customer")

    # Async (Kafka consumer, FastAPI handler)
    async with AsyncOpenDQVClient("http://opendqv.internal:8000", token="...") as client:
        async for msg in consumer:
            result = await client.validate(msg.value, contract="proof_of_play")
            if not result["valid"]:
                await dlq.send(msg)

    # FastAPI decorator (sync)
    @client.guard(contract="customer")
    async def create_customer(data: dict):
        db.insert(data)
"""

import functools
import inspect
import json
import os
from typing import Optional

import httpx


class ValidationError(Exception):
    """Raised when a record fails validation (only in strict/decorator mode)."""

    def __init__(self, errors: list, warnings: list = None):
        self.errors = errors
        self.warnings = warnings or []
        fields = [e["field"] for e in errors]
        super().__init__(f"OpenDQV validation failed on fields: {', '.join(fields)}")


class OpenDQVClient:
    """
    Client for the OpenDQV data quality validation API.

    Args:
        base_url: OpenDQV API base URL (e.g. "http://localhost:8000")
        token: Personal Access Token for authentication
        timeout: Request timeout in seconds (default 10)
        contract_cache_dir: Optional local directory for caching contract definitions.
            When set, successful contract fetches are written to ``<dir>/<name>.json``.
            If the API is unreachable, ``contract()`` falls back to the cached file.
            Designed for edge/IoT deployments and air-gapped environments.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 10.0,
        contract_cache_dir: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.contract_cache_dir = contract_cache_dir
        if contract_cache_dir:
            os.makedirs(contract_cache_dir, exist_ok=True)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def validate(
        self,
        record: dict,
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        record_id: Optional[str] = None,
        allow_draft: bool = False,
    ) -> dict:
        """
        Validate a single record against a data contract.

        Returns:
            dict with keys: valid (bool), errors (list), warnings (list),
            contract (str), version (str), record_id (str|None)

        Example:
            result = client.validate(
                {"email": "alice@example.com", "age": 25, "name": "Alice"},
                contract="customer",
            )
            if result["valid"]:
                print("Record passed all checks")
            else:
                for err in result["errors"]:
                    print(f"  {err['field']}: {err['message']}")
        """
        body = {"record": record, "contract": contract, "version": version}
        if context:
            body["context"] = context
        if record_id:
            body["record_id"] = record_id

        params = {}
        if allow_draft:
            params["allow_draft"] = "true"

        resp = self._client.post("/api/v1/validate", json=body, params=params)
        resp.raise_for_status()
        return resp.json()

    def validate_batch(
        self,
        records: list[dict],
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        allow_draft: bool = False,
    ) -> dict:
        """
        Validate a batch of records against a data contract.

        Returns:
            dict with keys: summary (dict), results (list), contract (str), version (str)

        Example:
            result = client.validate_batch(
                [{"email": "a@b.com", "age": 25}, {"email": "bad", "age": -1}],
                contract="customer",
            )
            print(f"{result['summary']['passed']}/{result['summary']['total']} passed")
        """
        body = {"records": records, "contract": contract, "version": version}
        if context:
            body["context"] = context

        params = {}
        if allow_draft:
            params["allow_draft"] = "true"

        resp = self._client.post("/api/v1/validate/batch", json=body, params=params)
        resp.raise_for_status()
        return resp.json()

    def contracts(self, include_all: bool = False) -> list[dict]:
        """
        List available data contracts.

        Returns:
            list of dicts with keys: name, version, description, owner, status, rule_count
        """
        params = {}
        if include_all:
            params["include_all"] = "true"
        resp = self._client.get("/api/v1/contracts", params=params)
        resp.raise_for_status()
        return resp.json()

    def contract(self, name: str, version: str = "latest") -> dict:
        """
        Get full detail of a data contract including its rules.

        Returns:
            dict with keys: name, version, description, owner, status, rules, contexts

        If ``contract_cache_dir`` is set, the result is cached locally. When the
        API is unreachable, the cached version is returned instead (degraded mode).
        """
        try:
            resp = self._client.get(f"/api/v1/contracts/{name}", params={"version": version})
            resp.raise_for_status()
            data = resp.json()
            if getattr(self, "contract_cache_dir", None):
                cache_path = os.path.join(self.contract_cache_dir, f"{name}.json")
                with open(cache_path, "w") as f:
                    json.dump(data, f)
            return data
        except httpx.RequestError:
            if getattr(self, "contract_cache_dir", None):
                cache_path = os.path.join(self.contract_cache_dir, f"{name}.json")
                if os.path.exists(cache_path):
                    with open(cache_path) as f:
                        return json.load(f)
            raise

    def lint(self, name: str) -> dict:
        """
        Lint a contract for logical errors.

        Returns a dict with keys: contract_name, passed, error_count, warning_count, issues.
        Raises httpx.HTTPStatusError (422) when errors are found, so callers can gate
        on exception type without inspecting the body.

        Example:
            try:
                result = client.lint("customer")
                print(f"Contract OK — {result['warning_count']} warning(s)")
            except httpx.HTTPStatusError as e:
                body = e.response.json()
                for issue in body["detail"]["issues"]:
                    print(f"  {issue['severity'].upper()}: {issue['message']}")
        """
        resp = self._client.get(f"/api/v1/contracts/{name}/lint")
        resp.raise_for_status()
        return resp.json()

    def guard(
        self,
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        record_param: str = "data",
    ):
        """
        Decorator that validates function input before execution.

        If validation fails, raises ValidationError instead of calling the function.
        Works with both sync and async functions.

        Args:
            contract: Contract name to validate against
            version: Contract version (default "latest")
            context: Optional context override
            record_param: Name of the function parameter containing the record data
                         (default "data"). Can also be the first positional arg.

        Example (FastAPI):
            @app.post("/customers")
            @client.guard(contract="customer")
            async def create_customer(data: dict):
                # This only runs if data passes validation
                return db.insert(data)

        Example (plain function):
            @client.guard(contract="customer", record_param="customer_data")
            def save_customer(customer_data: dict):
                db.insert(customer_data)
        """
        def decorator(func):
            if inspect.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    record = _extract_record(func, args, kwargs, record_param)
                    result = self.validate(record, contract, version=version, context=context)
                    if not result["valid"]:
                        raise ValidationError(result["errors"], result.get("warnings", []))
                    return await func(*args, **kwargs)
                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    record = _extract_record(func, args, kwargs, record_param)
                    result = self.validate(record, contract, version=version, context=context)
                    if not result["valid"]:
                        raise ValidationError(result["errors"], result.get("warnings", []))
                    return func(*args, **kwargs)
                return sync_wrapper
        return decorator

    def close(self):
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AsyncOpenDQVClient:
    """
    Async client for the OpenDQV data quality validation API.

    Uses httpx.AsyncClient — does not block the event loop. Safe for use in:
    - FastAPI route handlers
    - Kafka/Pulsar async consumers
    - asyncio ETL pipelines

    Args:
        base_url: OpenDQV API base URL (e.g. "http://localhost:8000")
        token: Personal Access Token for authentication
        timeout: Request timeout in seconds (default 10)

    Example (Kafka consumer):
        async with AsyncOpenDQVClient("http://opendqv.internal:8000", token="...") as client:
            async for msg in consumer:
                result = await client.validate(msg.value, contract="proof_of_play")
                if not result["valid"]:
                    await dead_letter_queue.send(msg)

    Example (FastAPI decorator):
        @app.post("/impressions")
        @async_client.guard(contract="proof_of_play")
        async def ingest_impression(data: dict):
            await db.insert(data)
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 10.0,
        contract_cache_dir: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.contract_cache_dir = contract_cache_dir
        if contract_cache_dir:
            os.makedirs(contract_cache_dir, exist_ok=True)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def validate(
        self,
        record: dict,
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        record_id: Optional[str] = None,
        allow_draft: bool = False,
    ) -> dict:
        """
        Validate a single record against a data contract. Async — does not block.

        Returns:
            dict with keys: valid (bool), errors (list), warnings (list),
            contract (str), version (str), record_id (str|None)
        """
        body = {"record": record, "contract": contract, "version": version}
        if context:
            body["context"] = context
        if record_id:
            body["record_id"] = record_id
        params = {"allow_draft": "true"} if allow_draft else {}
        resp = await self._client.post("/api/v1/validate", json=body, params=params)
        resp.raise_for_status()
        return resp.json()

    async def validate_batch(
        self,
        records: list[dict],
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        allow_draft: bool = False,
    ) -> dict:
        """
        Validate a batch of records against a data contract. Async — does not block.

        Returns:
            dict with keys: summary (dict), results (list), contract (str), version (str)
        """
        body = {"records": records, "contract": contract, "version": version}
        if context:
            body["context"] = context
        params = {"allow_draft": "true"} if allow_draft else {}
        resp = await self._client.post("/api/v1/validate/batch", json=body, params=params)
        resp.raise_for_status()
        return resp.json()

    async def contracts(self, include_all: bool = False) -> list[dict]:
        """
        List available data contracts. Async — does not block.

        Returns:
            list of dicts with keys: name, version, description, owner, status, rule_count
        """
        params = {}
        if include_all:
            params["include_all"] = "true"
        resp = await self._client.get("/api/v1/contracts", params=params)
        resp.raise_for_status()
        return resp.json()

    async def lint(self, name: str) -> dict:
        """
        Lint a contract for logical errors. Async — does not block.

        Returns a dict with keys: contract_name, passed, error_count, warning_count, issues.
        Raises httpx.HTTPStatusError (422) when errors are found.

        Example:
            try:
                result = await client.lint("customer")
            except httpx.HTTPStatusError as e:
                body = e.response.json()
                for issue in body["detail"]["issues"]:
                    print(f"  {issue['severity'].upper()}: {issue['message']}")
        """
        resp = await self._client.get(f"/api/v1/contracts/{name}/lint")
        resp.raise_for_status()
        return resp.json()

    async def contract(self, name: str, version: str = "latest") -> dict:
        """
        Get full detail of a data contract including its rules. Async — does not block.

        Returns:
            dict with keys: name, version, description, owner, status, rules, contexts

        If ``contract_cache_dir`` is set, the result is cached locally. When the
        API is unreachable, the cached version is returned instead (degraded mode).
        """
        try:
            resp = await self._client.get(f"/api/v1/contracts/{name}", params={"version": version})
            resp.raise_for_status()
            data = resp.json()
            if getattr(self, "contract_cache_dir", None):
                cache_path = os.path.join(self.contract_cache_dir, f"{name}.json")
                import asyncio
                await asyncio.to_thread(self._write_cache, cache_path, data)
            return data
        except httpx.RequestError:
            if getattr(self, "contract_cache_dir", None):
                cache_path = os.path.join(self.contract_cache_dir, f"{name}.json")
                if os.path.exists(cache_path):
                    import asyncio
                    return await asyncio.to_thread(self._read_cache, cache_path)
            raise

    @staticmethod
    def _write_cache(path: str, data: dict) -> None:
        with open(path, "w") as f:
            json.dump(data, f)

    @staticmethod
    def _read_cache(path: str) -> dict:
        with open(path) as f:
            return json.load(f)

    def guard(
        self,
        contract: str,
        *,
        version: str = "latest",
        context: Optional[str] = None,
        record_param: str = "data",
    ):
        """
        Decorator that validates function input before execution. Async-native.

        The decorated function must be a coroutine (async def).
        If validation fails, raises ValidationError instead of calling the function.

        Example:
            @app.post("/impressions")
            @async_client.guard(contract="proof_of_play")
            async def ingest_impression(data: dict):
                await db.insert(data)
        """
        def decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                record = _extract_record(func, args, kwargs, record_param)
                result = await self.validate(record, contract, version=version, context=context)
                if not result["valid"]:
                    raise ValidationError(result["errors"], result.get("warnings", []))
                return await func(*args, **kwargs)
            return wrapper
        return decorator

    async def close(self):
        """Close the underlying async HTTP client."""
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


def _extract_record(func, args, kwargs, record_param: str) -> dict:
    """Extract the record dict from function arguments."""
    # Check kwargs first
    if record_param in kwargs:
        return kwargs[record_param]

    # Check positional args by parameter name
    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    if record_param in params:
        idx = params.index(record_param)
        if idx < len(args):
            return args[idx]

    # Fallback: first positional arg
    if args:
        return args[0]

    raise ValueError(
        f"Could not find record data. Expected parameter '{record_param}' "
        f"in function {func.__name__}(). Pass the record as a keyword argument "
        f"or set record_param to match your parameter name."
    )
