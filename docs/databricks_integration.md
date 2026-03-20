# Databricks Integration

> **Last reviewed:** 2026-03-13.
> Covers Databricks Runtime ≥14.x, Delta Lake ≥3.x, Databricks Asset Bundles.
> For generic Spark deployments (EMR, Dataproc, HDInsight), see [`spark_integration.md`](spark_integration.md).

OpenDQV integrates natively with the Databricks platform — Auto Loader ingestion, Delta Live Tables pipelines, Jobs/Asset Bundle workflows, Unity Catalog lineage, and multi-workspace federation. All approaches share the same pre-write validation pattern: validate before the Delta commit, quarantine rejects.

---

## `asset_id` Convention

Use the Unity Catalog three-level namespace as `asset_id`:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "databricks://acme-prod.cloud.databricks.com/catalog/schema/customer"
  #          databricks://{workspace-host}/{catalog}/{schema}/{table}
```

For non-Unity Catalog deployments, fall back to the generic Spark convention:

```yaml
  asset_id: "spark://default/analytics/customer"
  #          spark://{database}/{table}
```

---

## Approach 3 — Auto Loader with Validation

Auto Loader (`cloudFiles`) incrementally ingests files from cloud storage. Use `foreachBatch` to validate each loaded micro-batch before writing.

```python
import os
from pyspark.sql import SparkSession
from sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

spark = SparkSession.builder.appName("opendqv-autoloader").getOrCreate()

# Databricks Auto Loader reads new files from S3/ADLS/GCS incrementally
raw = (
    spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("cloudFiles.schemaLocation", "/mnt/schemas/orders")
    .load("s3://my-bucket/landing/orders/")
)


def validate_micro_batch(batch_df, batch_id):
    client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
    records = [row.asDict() for row in batch_df.collect()]
    if not records:
        return

    result = client.validate_batch(
        records,
        contract="orders",
    )
    clean = [records[r["index"]] for r in result["results"] if r["valid"]]
    rejected = [
        {"record": str(records[r["index"]]), "errors": str(r["errors"]), "batch_id": batch_id}
        for r in result["results"] if not r["valid"]
    ]

    if clean:
        spark.createDataFrame(clean).write.format("delta").mode("append").saveAsTable("analytics.orders")
    if rejected:
        spark.createDataFrame(rejected).write.format("delta").mode("append").saveAsTable("analytics.orders_quarantine")

    print(f"Batch {batch_id}: {len(clean)} written, {len(rejected)} quarantined")


query = (
    raw.writeStream
    .foreachBatch(validate_micro_batch)
    .option("checkpointLocation", "/mnt/checkpoints/autoloader-orders")
    .trigger(availableNow=True)   # run once and stop (batch mode)
    .start()
)
query.awaitTermination()
```

`trigger(availableNow=True)` processes all available files and stops — useful for scheduled Databricks Jobs that don't need continuous streaming.

---

## Approach 4 — Databricks Jobs: Pre-Task Validation Gate

In a Databricks Workflow (Jobs UI or Asset Bundle), add an OpenDQV validation task before the Delta write task. Fail the task if validation rejects too many records.

```python
# Task: validate_orders (runs before write_to_delta)
import os
from sdk import OpenDQVClient
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
client = OpenDQVClient(
    os.getenv("OPENDQV_URL", "http://opendqv:8000"),
    token=os.getenv("OPENDQV_TOKEN"),
)

# Read staging data
staging_df = spark.read.format("delta").table("staging.orders_landing")
records = [row.asDict() for row in staging_df.collect()]

run_id = dbutils.widgets.get("run_id")  # passed by Databricks job  # dbutils is only available in Databricks notebooks — use os.getenv("RUN_ID", "unknown") for job/wheel context
result = client.validate_batch(records, contract="orders")
summary = result["summary"]

rejection_rate = summary["failed"] / summary["total"] if summary["total"] else 0
THRESHOLD = float(os.getenv("REJECTION_THRESHOLD", "0.01"))  # fail if > 1% rejected

if rejection_rate > THRESHOLD:
    raise ValueError(
        f"OpenDQV rejection rate {rejection_rate:.1%} exceeds threshold {THRESHOLD:.1%} "
        f"({summary['failed']}/{summary['total']} records rejected)"
    )

print(f"Validation passed: {summary['passed']}/{summary['total']} clean records")
```

In the Databricks Jobs YAML (Asset Bundle):

```yaml
# databricks.yml
resources:
  jobs:
    orders_ingestion:
      tasks:
        - task_key: validate_orders
          notebook_task:
            notebook_path: /Shared/opendqv/validate_orders
          libraries:
            - pypi:
                package: opendqv

        - task_key: write_to_delta
          depends_on:
            - task_key: validate_orders
          notebook_task:
            notebook_path: /Shared/pipelines/write_orders
```

The `depends_on` ensures `write_to_delta` only runs if `validate_orders` succeeds.

---

## Approach 5 — Delta Live Tables: Quarantine Expectation

In Delta Live Tables (DLT), use an OpenDQV-backed quarantine table alongside your main pipeline table. DLT handles the flow; OpenDQV provides the validation logic.

```python
import dlt
from pyspark.sql.functions import col
import requests, os, json

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

@dlt.table(name="orders_landing")
def orders_landing():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .load("s3://my-bucket/landing/orders/")
    )

@dlt.table(name="orders_clean", comment="Orders that passed OpenDQV validation")
@dlt.expect_all_or_drop({
    "opendqv_valid": "is_valid = true"
})
def orders_clean():
    from pyspark.sql.functions import udf
    from pyspark.sql.types import BooleanType

    @udf(returnType=BooleanType())
    def validate_record_udf(record_json: str) -> bool:
        record = json.loads(record_json)
        resp = requests.post(
            f"{OPENDQV_URL}/api/v1/validate",
            json={"contract": "orders", "record": record},
            headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
            timeout=5,
        )
        return resp.json().get("valid", False)

    from pyspark.sql.functions import to_json, struct
    return (
        dlt.read_stream("orders_landing")
        .withColumn(
            "is_valid",
            validate_record_udf(to_json(struct("*")))
        )
    )
```

> **DLT performance note:** The UDF makes one HTTP call per row. For high-volume DLT pipelines, use `foreachBatch` (Approach 3) instead — it batches calls to OpenDQV rather than calling once per record.

---

## Approach 6 — Unity Catalog: `asset_id` Linkage

Set `asset_id` to the Unity Catalog three-level identifier to link OpenDQV contracts to Unity Catalog tables. This enables cross-tool lineage in DataHub and Atlan when both are connected to the Databricks Unity Catalog lineage API.

```yaml
contract:
  name: customer
  asset_id: "databricks://acme-prod.cloud.databricks.com/main/analytics/customers"
```

```python
# Query the OpenDQV registry to find the contract for a Unity Catalog table
import requests, os

def find_contract_for_table(workspace_host: str, catalog: str, schema: str, table: str):
    asset_id = f"databricks://{workspace_host}/{catalog}/{schema}/{table}"
    contracts = requests.get(
        f"{os.getenv('OPENDQV_URL')}/api/v1/registry",
        headers={"Authorization": f"Bearer {os.getenv('OPENDQV_TOKEN')}"},
    ).json()
    return next((c for c in contracts if c.get("asset_id") == asset_id), None)
```

---

## Approach 7 — Federation-Aware: Multiple Databricks Workspaces

Route validation requests to the OpenDQV instance closest to each Databricks workspace:

```python
INSTANCES = {
    "acme-eu.cloud.databricks.com": "http://opendqv-eu.internal:8000",
    "acme-us.cloud.databricks.com": "http://opendqv-us.internal:8000",
}

def get_client(workspace_host: str) -> OpenDQVClient:
    url = INSTANCES.get(workspace_host, "http://opendqv.internal:8000")
    return OpenDQVClient(url, token=os.getenv("OPENDQV_TOKEN"))
```

Pass `workspace_host` as a Databricks Job widget parameter to route dynamically at runtime.

---

## Local Development with PySpark

Use PySpark in `local[*]` mode to develop and test the Databricks foreachBatch and UDF patterns on your laptop — no workspace, no cluster, no cloud billing required.

### Prerequisites

```bash
pip install pyspark   # heavyweight dep — not in pyproject.toml extras
```

### Run the demo script

```bash
cd ~/OpenDQV && source .venv/bin/activate
python scripts/databricks_local_demo.py
```

The demo covers two patterns:

**Pattern 1 — foreachBatch (recommended for production)**

```python
from pyspark.sql import SparkSession
from sdk.local import LocalValidator

spark = SparkSession.builder.master("local[*]").appName("opendqv").getOrCreate()
validator = LocalValidator()

def validate_micro_batch(batch_df, batch_id):
    records = [row.asDict() for row in batch_df.collect()]
    result = validator.validate_batch(records, contract="customer")

    clean = [records[r["index"]] for r in result["results"] if r["valid"]]
    rejected = [records[r["index"]] for r in result["results"] if not r["valid"]]

    if clean:
        spark.createDataFrame(clean).write.format("delta").mode("append").saveAsTable("analytics.customers")
    if rejected:
        spark.createDataFrame(rejected).write.format("delta").mode("append").saveAsTable("analytics.customers_quarantine")

    print(f"Batch {batch_id}: {len(clean)} written, {len(rejected)} quarantined")
```

**Pattern 2 — Spark UDF (per-record)**

```python
from pyspark.sql.functions import to_json, struct, col, udf
from pyspark.sql.types import BooleanType

@udf(returnType=BooleanType())
def validate_record_udf(record_json: str) -> bool:
    import json
    from sdk.local import LocalValidator
    v = LocalValidator()
    result = v.validate(json.loads(record_json), contract="customer")
    return result["valid"]

annotated_df = df.withColumn(
    "_opendqv_valid",
    validate_record_udf(to_json(struct([col(c) for c in df.columns])))
)
clean_df = annotated_df.filter(col("_opendqv_valid") == True)
```

> **Performance note:** The UDF re-initialises `LocalValidator` for every record (each Spark task is isolated). For production Databricks pipelines, use `foreachBatch` — it initialises the validator once per batch, not once per row.

> **PySpark as optional dep:** `pyspark` is not in `pyproject.toml` extras because it is very large (~300 MB). Install it separately for local development or use it from the Databricks cluster where it is pre-installed.

---

## Limitations

| Limitation | Detail |
|---|---|
| `.collect()` memory | Collecting a large DataFrame to the driver for batch validation is memory-bounded. Use `foreachPartition` or the streaming approach for DataFrames larger than ~10M rows |
| UDF HTTP calls in DLT | One HTTP call per row in the DLT UDF (Approach 5) is slow at scale — use `foreachBatch` for production DLT pipelines above ~100k rows per trigger |
| `unique` rule in distributed mode | The `unique` rule requires the full dataset. In partitioned Spark jobs, per-partition validation cannot detect cross-partition duplicates — use a global deduplication step before validation |
| Structured Streaming latency | `foreachBatch` adds one OpenDQV round-trip per micro-batch trigger interval. Keep trigger intervals ≥10s for small batches; tune down for high-volume streams |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Add `validate_batch` before every Delta write in batch jobs |
| **Now** | Set `asset_id` to Unity Catalog three-level path for lineage linkage |
| **Now** | Pass `run_id` / `batch_id` as `trace_id` for end-to-end correlation |
| **Planned — based on community demand** | Switch Kafka → Delta pipelines to `foreachBatch` streaming validation |
| **Planned — based on community demand** | Add OpenDQV validation task to Databricks Asset Bundle workflow definitions |
| **Planned — based on community demand** | Databricks Partner Connect integration (planned) |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Databricks features including Databricks Partner Connect integration and a native Delta Live Tables expectation type backed by OpenDQV contracts.

---

## See Also

- [`spark_integration.md`](spark_integration.md) — Generic Spark: EMR, Dataproc, HDInsight (batch gate, foreachBatch streaming)
- [`kafka_integration.md`](kafka_integration.md) — validate before committing Kafka offset (pairs with Auto Loader / foreachBatch)
- [`orchestrator_integration.md`](orchestrator_integration.md) — Airflow, Prefect, Dagster gate pattern
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
