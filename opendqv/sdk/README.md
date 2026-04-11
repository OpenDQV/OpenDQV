# OpenDQV Python SDK

Python client for the OpenDQV data quality validation engine.

Supports synchronous and asynchronous usage, a `@guard()` decorator for
function-level enforcement, and a local validator that runs in-process
without a server.

---

## Installation

```bash
pip install opendqv
```

For async support, `httpx` is included automatically.

---

## Quick start — sync client

```python
from sdk.client import OpenDQVClient

client = OpenDQVClient("http://localhost:8000", token="your-pat")

# Validate a single record
result = client.validate(
    {"name": "Alice", "age": 30, "email": "alice@example.com"},
    contract="customer",
)
print(result["valid"])   # True / False
print(result["errors"])  # list of rule violations

# Validate a batch
batch_result = client.validate_batch(
    [{"name": "Alice", ...}, {"name": "Bob", ...}],
    contract="customer",
)
print(batch_result["summary"])  # {"total": 2, "passed": 2, "failed": 0}
```

---

## Quick start — async client

```python
import asyncio
from sdk.client import AsyncOpenDQVClient

async def main():
    async with AsyncOpenDQVClient("http://localhost:8000", token="your-pat") as client:
        result = await client.validate(
            {"name": "Alice", "age": 30, "email": "alice@example.com"},
            contract="customer",
        )
        print(result["valid"])

asyncio.run(main())
```

---

## `@guard()` decorator

Block a function from running when the record fails validation.

```python
from sdk.client import OpenDQVClient, ValidationError

client = OpenDQVClient("http://localhost:8000", token="your-pat")

@client.guard(contract="customer")
def save_customer(data: dict):
    # Only reached if the record is valid
    db.insert(data)

try:
    save_customer(data={"name": "Alice", "age": 30, "email": "alice@example.com"})
except ValidationError as e:
    print(e.errors)    # list of violations
    print(e.warnings)  # list of warnings
```

Works on `async def` functions too — the decorator auto-detects and wraps correctly.

```python
@client.guard(contract="customer")
async def async_save(data: dict):
    await db.insert(data)
```

---

## Local validator — no server required

Validates records in-process using YAML contracts from disk. Useful for
unit tests, CLI tools, or environments without a running server.

```python
from sdk.local import LocalValidator, ContractNotFoundError

# Reads contracts from the OPENDQV_CONTRACTS_DIR env var,
# or falls back to ./contracts/ in the current directory.
v = LocalValidator()

result = v.validate(
    {"name": "Alice", "age": 30, "email": "alice@example.com"},
    contract="customer",
)
print(result["valid"])

# Validate a batch locally
batch = v.validate_batch(
    [{"name": "Alice", ...}, {"name": "Bob", ...}],
    contract="customer",
)
print(batch["summary"])

# List available contracts
contracts = v.list_contracts()  # ["customer", "product", ...]

# Reload contracts from disk (picks up YAML edits without restarting)
v.reload()
```

Raises `ContractNotFoundError` when the named contract does not exist.

---

## Contract introspection

```python
# List all active contracts
contracts = client.contracts()
# [{"name": "customer", "version": "1.0", "status": "active", ...}, ...]

# Include draft contracts
contracts = client.contracts(include_all=True)

# Fetch a specific contract with its rules
contract = client.contract("customer")
print(contract["rules"])

# Lint a contract for logical errors
result = client.lint("customer")
print(result["passed"])  # True / False
```

---

## Auth

Generate a PAT from the OpenDQV API or CLI:

```bash
# CLI
opendqv tokens generate --role validator --description "my-service"

# API
POST /api/v1/tokens/generate
{"role": "validator", "description": "my-service"}
```

Pass the token to the client:

```python
client = OpenDQVClient("http://localhost:8000", token="odqv_...")
```

---

## Observation-only mode

Log violations without rejecting records — useful for shadow validation
before enforcing a new contract:

```python
result = client.validate(record, contract="customer", observe_only=True)
# result["mode"] == "observe"
# result["valid"] is always True — record is never rejected
# result["would_have_failed"] == True if there were violations
```

---

## Contract caching

Cache contract definitions locally for degraded-mode operation (API down):

```python
client = OpenDQVClient(
    "http://localhost:8000",
    token="your-pat",
    contract_cache_dir="/var/cache/opendqv",
)

# Subsequent calls to client.contract("customer") write to the cache.
# If the API is unreachable, the cached version is returned instead.
```
