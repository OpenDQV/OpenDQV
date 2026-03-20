# Pandas Integration

> **Last reviewed:** 2026-03-17.

![OpenDQV + Pandas — validate before writing, route clean vs quarantine rows](demo_pandas.gif)

*Validate a DataFrame before it touches your database: clean rows written, invalid rows quarantined — one call to `validate_batch()`, no row-by-row loops, no extra dependencies.*

> Covers pandas v2.x. Pandas is a core dependency — no extra install required.

OpenDQV's `LocalValidator.validate_batch()` takes a list of dicts, which is exactly what `df.to_dict('records')` produces. This makes DataFrame validation a single-line pattern with zero additional dependencies.

---

## Core Pattern

```python
from sdk.local import LocalValidator

validator = LocalValidator()  # loads contracts from OPENDQV_CONTRACTS_DIR

# Validate all rows in a DataFrame
records = df.to_dict("records")
result = validator.validate_batch(records, contract="customer")

print(f"{result['summary']['passed']}/{result['summary']['total']} rows passed")
```

---

## Annotate DataFrame with Validity Column

The standard pattern for downstream filtering: annotate the DataFrame with a `_opendqv_valid` column, then split into clean and quarantine frames.

```python
import pandas as pd
from sdk.local import LocalValidator

validator = LocalValidator()

df = pd.read_csv("customers.csv")
records = df.to_dict("records")

result = validator.validate_batch(records, contract="customer")

# NOTE: result["index"] is a 0-based positional offset within the batch.
# If your DataFrame has a non-default index (e.g. after set_index()),
# call df.reset_index(drop=True) before passing records to validate_batch().
# Build index → valid/invalid map
validity = {r["index"]: r["valid"] for r in result["results"]}
df["_opendqv_valid"] = df.index.map(validity)

clean_df = df[df["_opendqv_valid"]].drop(columns=["_opendqv_valid"])
rejected_df = df[~df["_opendqv_valid"]].drop(columns=["_opendqv_valid"])

print(f"{len(clean_df)} clean, {len(rejected_df)} quarantined")

# Write clean records to destination
clean_df.to_csv("customers_clean.csv", index=False)
rejected_df.to_csv("customers_quarantine.csv", index=False)
```

---

## Annotate with Error Details

To capture which fields failed and why:

```python
error_map = {
    r["index"]: r["errors"]
    for r in result["results"]
    if not r["valid"]
}

df["_opendqv_valid"] = df.index.map(lambda i: i not in error_map)
df["_opendqv_errors"] = df.index.map(lambda i: error_map.get(i, []))

# Rejected rows with error details
rejected_df = df[~df["_opendqv_valid"]][["name", "email", "_opendqv_errors"]]
```

---

## Local Simulation

The integration tests in `tests/test_pandas_integration.py` run locally with no external dependencies:

```bash
cd ~/OpenDQV && source .venv/bin/activate
pytest tests/test_pandas_integration.py -v
```

---

## Validate a Single Row

```python
row = df.iloc[0].to_dict()
result = validator.validate(row, contract="customer")

if not result["valid"]:
    for err in result["errors"]:
        print(f"  {err['field']}: {err['message']}")
```

---

## With the API Client (when OpenDQV API is running)

Swap `LocalValidator` for `OpenDQVClient` — the pattern is identical:

```python
from sdk import OpenDQVClient

client = OpenDQVClient(
    "http://opendqv:8000",
    token=os.getenv("OPENDQV_TOKEN"),
)

result = client.validate_batch(df.to_dict("records"), contract="customer")
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Memory | `to_dict("records")` loads the full DataFrame into Python dicts. For very large DataFrames (> 10M rows), validate in chunks using `df.iloc[i:i+10000]`. |
| `unique` rule | Uniqueness is checked within the batch only — not against existing data in your destination system. |
| Type coercion | Pandas may carry numpy types (e.g. `int64`, `float64`) in the dict values. The validator handles these correctly via DuckDB's type system. |

---

## Chunked Validation for Large DataFrames

```python
CHUNK_SIZE = 10_000

all_results = []
for start in range(0, len(df), CHUNK_SIZE):
    chunk = df.iloc[start:start + CHUNK_SIZE].to_dict("records")
    result = validator.validate_batch(chunk, contract="customer")
    # Re-index results to global DataFrame position
    for r in result["results"]:
        r["global_index"] = start + r["index"]
    all_results.extend(result["results"])

failed = [r for r in all_results if not r["valid"]]
print(f"{len(all_results) - len(failed)}/{len(all_results)} rows passed")
```

---

## See Also

- [`postgres_integration.md`](postgres_integration.md) — write clean DataFrames to Postgres
- [`snowflake_integration.md`](snowflake_integration.md) — write clean DataFrames to Snowflake
- [`databricks_integration.md`](databricks_integration.md) — validate PySpark DataFrames
- [`dbt_integration.md`](dbt_integration.md) — validate dbt seed files as DataFrames
