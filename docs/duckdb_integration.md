# DuckDB Integration

> **Last reviewed:** 2026-03-17.
> Covers DuckDB ≥0.9 via the Python client.

![OpenDQV + DuckDB — batch validation at scale, route clean vs quarantine](demo_duckdb.gif)

*Analytical-scale batch validation: load a DuckDB table, validate every row against a contract, route clean records to the target and rejects to quarantine — no row-by-row Python loops, no API server required.*

OpenDQV's batch API is a natural fit for DuckDB's analytical workload: load a table, validate every row against a contract, write clean records to the target table, and route rejects to a quarantine table. No row-by-row Python loops required — feed the whole DataFrame to `/api/v1/validate/batch` and let DuckDB handle the splits.

---

## Core Pattern

```python
import duckdb
import json
import urllib.request

OPENDQV = "http://localhost:8000"
TOKEN   = "your-token"  # omit if AUTH_MODE=open

def validate_batch(records: list[dict], contract: str) -> dict:
    body = json.dumps({"contract": contract, "records": records}).encode()
    req  = urllib.request.Request(
        f"{OPENDQV}/api/v1/validate/batch",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# Load from DuckDB
con = duckdb.connect("warehouse.duckdb")
df  = con.execute("SELECT * FROM orders").df()

# Validate
result = validate_batch(df.to_dict("records"), contract="orders")

# Split
valid_idx   = [i for i, r in enumerate(result["results"]) if r["valid"]]
invalid_idx = [i for i, r in enumerate(result["results"]) if not r["valid"]]

clean_df     = df.iloc[valid_idx]
quarantine_df = df.iloc[invalid_idx]

# Write back
con.execute("INSERT INTO orders_clean SELECT * FROM clean_df")
con.execute("INSERT INTO orders_quarantine SELECT * FROM quarantine_df")
print(f"{len(clean_df)} clean  |  {len(quarantine_df)} quarantined")
```

---

## Batch Size

The `/api/v1/validate/batch` endpoint handles up to 10,000 records per request. For larger tables, chunk with pandas or DuckDB:

```python
CHUNK = 5000
for start in range(0, len(df), CHUNK):
    chunk = df.iloc[start:start + CHUNK]
    result = validate_batch(chunk.to_dict("records"), contract="orders")
    # ... split and write as above
```

---

## Quarantine Schema

Store rejections with the validation errors alongside the original record:

```sql
CREATE TABLE orders_quarantine AS
SELECT o.*, v.errors
FROM orders o
JOIN (VALUES ...) AS v(row_idx, errors) ON o.rowid = v.row_idx
WHERE NOT v.valid;
```

Or simpler — add an `_errors` column to the DataFrame before writing:

```python
quarantine_df = quarantine_df.copy()
quarantine_df["_errors"] = [
    json.dumps(result["results"][i]["errors"]) for i in invalid_idx
]
```

---

## Using the Local Validator (no API required)

For single-machine batch jobs where running the Docker stack is overhead, use the SDK local validator:

```python
from opendqv.sdk.local import LocalValidator

validator = LocalValidator(contracts_dir="contracts/")

errors_by_row = []
for i, record in enumerate(df.to_dict("records")):
    result = validator.validate(record, contract="orders")
    if not result["valid"]:
        errors_by_row.append((i, result["errors"]))
```

The local validator runs in-process with no network hop — useful for CI pipelines and scripts that run before Docker is available.

---

## See Also

- [Pandas integration](pandas_integration.md) — row-by-row annotation and DataFrame splitting
- [Kafka integration](kafka_integration.md) — validate before committing the offset
- [Benchmark](benchmark_throughput.md) — throughput numbers at scale
