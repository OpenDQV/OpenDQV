# Snowflake Integration

> **Last reviewed:** 2026-03-13.
> Covers `snowflake-connector-python v3.x`, `snowflake-sqlalchemy v1.x`, Snowpipe REST API, Snowflake External Functions.
> [snowflake-connector-python on PyPI](https://pypi.org/project/snowflake-connector-python/)

OpenDQV validates records before they reach Snowflake. Once a record lands in a Snowflake table it is part of your queryable data — bad records that slip through become query results, JOIN noise, and downstream model failures. Blocking at the write boundary is the cheapest fix point.

---

## `asset_id` Convention for Snowflake

Use the fully-qualified Snowflake object path as `asset_id`:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "snowflake://acme-prod.eu-west-1/ANALYTICS/PUBLIC/CUSTOMERS"
  #          snowflake://{account}/{database}/{schema}/{table}
```

This format is also recognised by DataHub's Snowflake connector and Atlan's Snowflake connection, so the same `asset_id` links the OpenDQV contract to catalog lineage nodes in all three tools.

---

## Approach 1 — Python Connector: Validate Before Write

Call `validate_batch` on the outgoing records before executing the Snowflake `INSERT` or `COPY`. Only clean records enter the table; rejected records go to a quarantine table.

```python
import json
import snowflake.connector
import os
from sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)

conn = snowflake.connector.connect(
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    user=os.getenv("SNOWFLAKE_USER"),
    password=os.getenv("SNOWFLAKE_PASSWORD"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    database="ANALYTICS",
    schema="PUBLIC",
)
cur = conn.cursor()

def load_records(records: list[dict], contract: str, table: str):
    result = client.validate_batch(records, contract=contract)
    summary = result["summary"]

    clean = [records[r["index"]] for r in result["results"] if r["valid"]]
    rejected = [
        {"record": records[r["index"]], "errors": r["errors"]}
        for r in result["results"] if not r["valid"]
    ]

    if clean:
        # Parameterised insert — avoids SQL injection
        columns = ", ".join(clean[0].keys())
        placeholders = ", ".join(["%s"] * len(clean[0]))
        cur.executemany(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            [tuple(r.values()) for r in clean],
        )
        conn.commit()

    if rejected:
        cur.executemany(
            "INSERT INTO QUARANTINE.REJECTED_RECORDS (source_table, record_json, errors_json) "
            "VALUES (%s, PARSE_JSON(%s), PARSE_JSON(%s))",
            [(table, json.dumps(r["record"]), json.dumps(r["errors"])) for r in rejected],
        )
        conn.commit()

    print(
        f"Loaded {summary['passed']}/{summary['total']} records to {table}; "
        f"{summary['failed']} quarantined"
    )
```

---

## Approach 2 — Snowpipe: Validate Before Ingest

Snowpipe ingests data from S3/GCS/Azure Blob via a REST API call. Validate the records before staging the files, so only clean files enter the Snowpipe ingest queue.

```python
import json, os, boto3
from sdk import OpenDQVClient
from snowflake.snowpipe.core import SimpleIngestManager, StagedFile

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = "staging/orders"

client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
s3 = boto3.client("s3")

ingest_manager = SimpleIngestManager(
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    host=f"{os.getenv('SNOWFLAKE_ACCOUNT')}.snowflakecomputing.com",
    user=os.getenv("SNOWFLAKE_USER"),
    pipe="ANALYTICS.PUBLIC.ORDERS_PIPE",
    private_key=os.getenv("SNOWFLAKE_PRIVATE_KEY"),
)


def stage_and_ingest(records: list[dict], batch_id: str):
    result = client.validate_batch(records, contract="orders", trace_id=batch_id)
    clean = [records[r["index"]] for r in result["results"] if r["valid"]]

    if not clean:
        print(f"Batch {batch_id}: all {len(records)} records rejected — nothing staged")
        return

    # Write clean records to S3 as NDJSON
    key = f"{S3_PREFIX}/{batch_id}.ndjson"
    body = "\n".join(json.dumps(r) for r in clean)
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body.encode())

    # Tell Snowpipe to ingest the file
    ingest_manager.ingest_files([StagedFile(key, None)])
    print(f"Staged {len(clean)}/{len(records)} records → s3://{S3_BUCKET}/{key}")
```

---

## Approach 3 — External Function: Call OpenDQV from Within Snowflake SQL

Snowflake External Functions allow SQL to call an external REST API. This enables OpenDQV validation as a SQL expression — useful for validating records already in a Snowflake staging table before promoting them to production.

### Create the External Function

```sql
-- 1. Create an API integration pointing at your OpenDQV instance
CREATE OR REPLACE API INTEGRATION opendqv_api
    API_PROVIDER = aws_api_gateway          -- or azure_api_management / google_api_gateway
    API_AWS_ROLE_ARN = 'arn:aws:iam::...:role/snowflake-opendqv'
    ENABLED = true
    API_ALLOWED_PREFIXES = ('https://opendqv.your-domain.com/api/v1/');

-- 2. Create the external function
CREATE OR REPLACE EXTERNAL FUNCTION opendqv_validate(contract VARCHAR, record VARIANT)
    RETURNS VARIANT
    API_INTEGRATION = opendqv_api
    AS 'https://opendqv.your-domain.com/api/v1/validate';
```

### Use it in SQL

```sql
-- Validate a staging table row-by-row and flag failures
SELECT
    id,
    record_data,
    opendqv_validate('customer', record_data) AS validation_result,
    validation_result:valid::BOOLEAN          AS is_valid,
    validation_result:errors                  AS errors
FROM STAGING.CUSTOMER_LANDING
WHERE is_valid = FALSE;

-- Insert only clean records into production
INSERT INTO ANALYTICS.PUBLIC.CUSTOMERS
SELECT id, email, age, name
FROM STAGING.CUSTOMER_LANDING
WHERE opendqv_validate('customer', OBJECT_CONSTRUCT(*)):valid::BOOLEAN = TRUE;
```

> **Performance note:** External Functions add network latency per row. For large tables (> 100k rows) use the batch approach (Approach 1) instead. External Functions are best suited for interactive validation of small landing tables or spot-checking.

---

## Approach 4 — Snowflake Streams and Tasks: Event-Driven Validation

Use a Snowflake Stream to detect new rows in a staging table, then a Task to call OpenDQV via a webhook — quarantining invalid rows before they accumulate.

```sql
-- 1. Create a stream on the staging table
CREATE OR REPLACE STREAM orders_landing_stream
    ON TABLE STAGING.PUBLIC.ORDERS_LANDING;

-- 2. Create a task that fires when new rows appear
CREATE OR REPLACE TASK validate_new_orders
    WAREHOUSE = COMPUTE_WH
    SCHEDULE = '1 minute'
    WHEN SYSTEM$STREAM_HAS_DATA('orders_landing_stream')
AS
    -- Call the external function on new rows
    INSERT INTO STAGING.PUBLIC.ORDERS_QUARANTINE
        (original_record, errors_json, detected_at)
    SELECT
        OBJECT_CONSTRUCT(*) AS original_record,
        opendqv_validate('orders', OBJECT_CONSTRUCT(*)):errors AS errors_json,
        CURRENT_TIMESTAMP()
    FROM orders_landing_stream
    WHERE opendqv_validate('orders', OBJECT_CONSTRUCT(*)):valid::BOOLEAN = FALSE;

-- 3. Activate the task
ALTER TASK validate_new_orders RESUME;
```

This pattern is fully serverless within Snowflake — no external consumer process required.

---

## Approach 5 — Incremental via `contract_hash`

Store the current `contract_hash` in a Snowflake table and only re-validate landing data when the contract has changed.

```python
import requests, os

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

def get_stored_hash(cur, contract: str) -> str | None:
    cur.execute(
        "SELECT contract_hash FROM GOVERNANCE.CONTRACT_HASHES WHERE contract_name = %s",
        (contract,),
    )
    row = cur.fetchone()
    return row[0] if row else None

def update_stored_hash(cur, contract: str, new_hash: str):
    cur.execute(
        "MERGE INTO GOVERNANCE.CONTRACT_HASHES t USING (SELECT %s name, %s hash) s "
        "ON t.contract_name = s.name "
        "WHEN MATCHED THEN UPDATE SET contract_hash = s.hash "
        "WHEN NOT MATCHED THEN INSERT (contract_name, contract_hash) VALUES (s.name, s.hash)",
        (contract, new_hash),
    )

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/contracts",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

# Note: contract version-based comparison only; contract_hash is not returned by the list endpoint
for c in contracts:
    stored = get_stored_hash(cur, c["name"])
    if stored == c.get("version", ""):
        print(f"Contract {c['name']} unchanged — skipping re-validation")
        continue
    # ... re-validate landing table for this contract
    update_stored_hash(cur, c["name"], c.get("version", ""))
```

---

## Approach 6 — Federation-Aware: Multiple Snowflake Accounts

In multi-account deployments (e.g. EU and US Snowflake accounts), route validation to the OpenDQV instance co-located with the account to minimise latency:

```python
INSTANCES = {
    "acme-eu.eu-west-1": "http://opendqv-eu.internal:8000",
    "acme-us.us-east-1": "http://opendqv-us.internal:8000",
}

def get_client(snowflake_account: str) -> OpenDQVClient:
    url = INSTANCES.get(snowflake_account, "http://opendqv.internal:8000")
    return OpenDQVClient(url, token=os.getenv("OPENDQV_TOKEN"))
```

---

## Local Development with DuckDB

DuckDB is already a core OpenDQV dependency. Use it to develop and test the Snowflake validation pattern locally — no Snowflake account or credits required.

### Validate records extracted from a DuckDB table

```python
import duckdb
from sdk.local import LocalValidator

validator = LocalValidator()

conn = duckdb.connect(":memory:")
conn.execute("""
    CREATE TABLE customers AS SELECT * FROM (VALUES
        ('Alice', 'alice@example.com', 30),
        ('Bob',   'bad-email',          -1)
    ) t(name, email, age)
""")

records = conn.execute("SELECT * FROM customers").fetchdf().to_dict("records")
result = validator.validate_batch(records, contract="customer")

clean = [records[r["index"]] for r in result["results"] if r["valid"]]
rejected = [records[r["index"]] for r in result["results"] if not r["valid"]]

print(f"{len(clean)} clean, {len(rejected)} quarantined")
```

### The quarantine pattern

```python
clean_indices = {r["index"] for r in result["results"] if r["valid"]}
rejected_indices = {r["index"] for r in result["results"] if not r["valid"]}

# Write clean records to "production" DuckDB table
if clean_indices:
    clean_rows = [records[i] for i in sorted(clean_indices)]
    clean_df = pd.DataFrame(clean_rows)
    conn.execute("CREATE TABLE customers_clean AS SELECT * FROM clean_df")

# Write rejected records to "quarantine" DuckDB table
if rejected_indices:
    # In Snowflake, this would be: INSERT INTO QUARANTINE.REJECTED_RECORDS
    rejected_rows = [records[i] for i in sorted(rejected_indices)]
```

### Run the local tests

```bash
cd ~/OpenDQV && source .venv/bin/activate
pytest tests/test_duckdb_integration.py -v
```

The DuckDB integration tests cover:
- Batch validation from DuckDB tables
- Clean/rejected record splitting
- The quarantine pattern
- DuckDB `fetchdf()` → `to_dict("records")` roundtrip

> **DuckDB vs. Snowflake UDF:** The Snowflake JS UDF (`opendqv_validate`) deploys the compiled rule logic into Snowflake for SQL-level validation. The local DuckDB path uses the Python `LocalValidator` instead. The validation logic is identical — the UDF is a push-down snapshot of the same rules.

---

## Limitations

| Limitation | Detail |
|---|---|
| External Function latency | One HTTP round-trip per row; avoid on tables > 100k rows — use Approach 1 or 2 |
| External Function row limit | Snowflake sends rows in batches of up to 500 per call; ensure your OpenDQV instance handles concurrent batch calls |
| Snowpipe and schema evolution | If the staging file schema changes, validate against the updated contract before staging — Snowpipe will reject files that don't match the pipe's COPY INTO column list |
| Streams and Tasks compute cost | The Task (Approach 4) consumes Snowflake credits — size your warehouse appropriately for the expected stream volume |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Add `validate_batch` before every Python connector `executemany` / Snowpipe staging call |
| **Now** | Set `asset_id` to `snowflake://{account}/{database}/{schema}/{table}` for catalog linkage |
| **Planned — based on community demand** | Create an External Function for SQL-level validation of landing tables |
| **Planned — based on community demand** | Add Streams + Tasks for serverless event-driven validation within Snowflake |
| **Planned — based on community demand** | Snowflake Native App packaging (planned) |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Snowflake features including Snowflake Native App packaging and Schema Registry integration.

---

## See Also

- [`spark_integration.md`](spark_integration.md) — Generic Spark integration: EMR, Dataproc, HDInsight (same pre-write pattern)
- [`databricks_integration.md`](databricks_integration.md) — Databricks: Auto Loader, DLT, Jobs/Asset Bundles, Unity Catalog
- [`kafka_integration.md`](kafka_integration.md) — validate before committing Kafka offset
- [`orchestrator_integration.md`](orchestrator_integration.md) — Airflow, Prefect, Dagster gate pattern
- [`datahub_integration.md`](datahub_integration.md) — sync contracts to DataHub (Snowflake lineage)
- [`collibra_integration.md`](collibra_integration.md) — Collibra governance catalog
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
