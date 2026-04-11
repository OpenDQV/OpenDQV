# Python SDK

Install the SDK via PyPI:

```bash
pip install opendqv
```

Two client classes — synchronous for standard use, async for event-driven pipelines.

---

## Synchronous client

```python
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient("http://opendqv.internal:8000", token="<YOUR_TOKEN>")

# Single record
result = client.validate(
    {"email": "alice@example.com", "age": 25, "name": "Alice"},
    contract="customer",
    context="salesforce",
)
if result["valid"]:
    print("Record passed")
else:
    for err in result["errors"]:
        print(f"  {err['field']}: {err['message']}")

# Batch
result = client.validate_batch(records, contract="customer")
print(f"{result['summary']['passed']}/{result['summary']['total']} passed")

# List contracts
for c in client.contracts():
    print(f"  {c['name']} v{c['version']} ({c['rule_count']} rules)")
```

---

## Async client (Kafka consumers, FastAPI, async ETL)

`AsyncOpenDQVClient` uses `httpx.AsyncClient` — it does not block the event loop.
Safe for use inside async Kafka consumers, FastAPI route handlers, and asyncio pipelines.

```python
from opendqv.sdk import AsyncOpenDQVClient

# Kafka consumer (aiokafka)
async def consume_impressions():
    async with AsyncOpenDQVClient("http://opendqv.internal:8000", token="<TOKEN>") as client:
        async for msg in consumer:
            result = await client.validate(msg.value, contract="proof_of_play", context="billing")
            if result["valid"]:
                await warehouse.insert(msg.value)
            else:
                await dead_letter_queue.send({
                    "record": msg.value,
                    "errors": result["errors"],
                    "contract_owner": result["owner"],  # for routing alerts
                })

# FastAPI decorator (async-native guard)
@app.post("/impressions")
@async_client.guard(contract="proof_of_play")
async def ingest_impression(data: dict):
    await db.insert(data)
    return {"status": "accepted"}
```

---

## Guard decorator

Automatically validate incoming data before your endpoint runs:

```python
from opendqv.sdk import OpenDQVClient, ValidationError

client = OpenDQVClient("http://opendqv.internal:8000", token="<TOKEN>")

@app.post("/customers")
@client.guard(contract="customer")
async def create_customer(data: dict):
    # Only runs if data passes validation
    db.insert(data)
    return {"status": "created"}
```

If validation fails, the decorator returns a `422` with per-field errors before your handler is called.

---

## LocalValidator — no server required

For scripts, ETL jobs, and CI pipelines that don't need an API server, `LocalValidator` runs
the full validation engine in-process against a local directory of YAML contracts.
No Docker, no network, no token.

```python
from opendqv.sdk.local import LocalValidator

validator = LocalValidator()  # reads from OPENDQV_CONTRACTS_DIR (or ./contracts/)

# Single record
result = validator.validate({"name": "Alice", "email": "alice@example.com"}, contract="customer")
if not result["valid"]:
    raise ValueError(result["errors"])

# Batch — works directly with DataFrames
import pandas as pd
df = pd.read_csv("customers.csv")
result = validator.validate_batch(df.to_dict("records"), contract="customer")
print(f"{result['summary']['passed']}/{result['summary']['total']} passed")

# Annotate DataFrame with validation results
validity = {r["index"]: r["valid"] for r in result["results"]}
df["_opendqv_valid"] = df.index.map(validity)
clean_df = df[df["_opendqv_valid"]]
```

`LocalValidator` uses the same rule engine as the API — results are identical.

Useful for:
- CI tests that validate sample records
- ETL scripts that validate before writing to Postgres or Snowflake
- Edge/IoT deployments without network access
- Django `Model.clean()` hooks via `LocalValidator` (see `docs/integrations/`)

See [docs/pandas_integration.md](pandas_integration.md) for the full DataFrame pattern and
[docs/postgres_integration.md](postgres_integration.md) for validate-before-INSERT.

---

## Contract linting

The SDK exposes a contract linter that validates the YAML contract itself (not records):

```python
result = validator.lint("customer")
# Returns: {"valid": bool, "errors": [...], "warnings": [...]}
```

CLI equivalent: `opendqv lint customer`

---

## Related

- [Quickstart](quickstart.md) — first validation in 15 minutes
- [API Reference](api_reference.md) — REST endpoints
- [Kafka integration](kafka_integration.md) — full async consumer pattern
- [Pandas integration](pandas_integration.md) — DataFrame validation
- [Postgres integration](postgres_integration.md) — validate-before-INSERT
