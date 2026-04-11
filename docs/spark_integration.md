# Spark Integration

> **Last reviewed:** 2026-03-13.
> Covers PySpark ≥3.x, Delta Lake ≥3.x.
> Works on any Spark deployment — EMR, GCP Dataproc, HDInsight.
> For Databricks-specific approaches (Auto Loader, DLT, Jobs/Asset Bundles, Unity Catalog), see [`databricks_integration.md`](databricks_integration.md).

OpenDQV validates records before they are written to Delta Lake or any Spark-managed table. Once data is committed to a Delta table it enters the transaction log and is immediately visible to downstream jobs, dashboards, and models. Blocking at the write boundary — before the commit — is the cheapest and most effective quality gate in the Spark stack.

---

## `asset_id` Convention

Use the generic Spark two-level namespace as `asset_id`:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "spark://default/analytics/customer"
  #          spark://{database}/{table}
```

For AWS EMR with Glue Catalog:

```yaml
  asset_id: "spark://glue:{account_id}.{database}/{table}"
  #          e.g. spark://glue:123456789012.analytics/customer
```

For GCP Dataproc with BigQuery / GCS:

```yaml
  asset_id: "spark://dataproc:{project}/{dataset}/{table}"
```

---

## Approach 1 — Batch Write Gate: Validate Before Delta Write

Collect a Spark DataFrame partition, validate via `validate_batch`, write only clean records to the Delta table. Rejected records go to a quarantine Delta table.

```python
import os
from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import lit, to_json, struct
from opendqv.sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

spark = SparkSession.builder.appName("opendqv-validate").getOrCreate()
client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)


def validate_and_write(
    df: DataFrame,
    contract: str,
    target_table: str,
    quarantine_table: str = "default.rejected_records",
    chunk_size: int = 1000,
):
    """Validate all rows in df; write clean rows to target_table, rejected to quarantine."""
    rows = [row.asDict() for row in df.collect()]

    clean_rows, quarantine_rows = [], []

    # Chunk to respect OpenDQV batch size limits
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        result = client.validate_batch(chunk, contract=contract)
        for res, record in zip(result["results"], chunk):
            if res["valid"]:
                clean_rows.append(record)
            else:
                quarantine_rows.append({
                    "contract": contract,
                    "target_table": target_table,
                    "record": str(record),
                    "errors": str(res["errors"]),
                })

    if clean_rows:
        spark.createDataFrame(clean_rows).write.format("delta").mode("append").saveAsTable(target_table)

    if quarantine_rows:
        spark.createDataFrame(quarantine_rows).write.format("delta").mode("append").saveAsTable(quarantine_table)

    print(
        f"[OpenDQV] {len(clean_rows)} written to {target_table}; "
        f"{len(quarantine_rows)} quarantined"
    )
```

> **Memory note:** `.collect()` pulls all rows to the driver. For DataFrames that exceed driver memory, use Approach 2 (foreachBatch streaming) or chunk by Spark partition using `foreachPartition`.

### Using `foreachPartition` for large DataFrames

```python
def validate_partition(rows, contract: str, target_table: str):
    """Runs on each executor — instantiate the client per partition."""
    from opendqv.sdk import OpenDQVClient
    import os

    client = OpenDQVClient(
        os.getenv("OPENDQV_URL", "http://opendqv:8000"),
        token=os.getenv("OPENDQV_TOKEN"),
    )
    records = list(rows)
    if not records:
        return

    result = client.validate_batch([r.asDict() for r in records], contract=contract)
    clean = [records[r["index"]] for r in result["results"] if r["valid"]]
    # Write clean records from this partition to Delta
    spark.createDataFrame(clean).write.format("delta").mode("append").saveAsTable(target_table)


df.foreachPartition(lambda rows: validate_partition(rows, "customer", "analytics.customers"))
```

---

## Approach 2 — Structured Streaming with `foreachBatch`

For streaming pipelines reading from Kafka, Kinesis, or any streaming source, use `foreachBatch` to validate each micro-batch before writing to Delta.

```python
import os
from pyspark.sql import SparkSession
from opendqv.sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

spark = SparkSession.builder.appName("opendqv-streaming").getOrCreate()

# Read from Kafka (or any streaming source)
raw_stream = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", os.getenv("KAFKA_BROKERS", "localhost:9092"))
    .option("subscribe", "raw.orders")
    .load()
)

import json
from pyspark.sql.functions import col, from_json, schema_of_json

# Parse JSON values from Kafka
orders_schema = spark.read.json(
    spark.sparkContext.parallelize(['{"order_id":"x","amount":1.0,"status":"pending"}'])
).schema
orders_stream = raw_stream.select(
    from_json(col("value").cast("string"), orders_schema).alias("data")
).select("data.*")


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
    orders_stream.writeStream
    .foreachBatch(validate_micro_batch)
    .option("checkpointLocation", "/tmp/checkpoints/opendqv-orders")
    .trigger(processingTime="30 seconds")
    .start()
)
query.awaitTermination()
```

---

## AWS EMR

**Glue Catalog `asset_id`:** Use the format `spark://glue:{account_id}.{database}/{table}` to link contracts to Glue-managed tables.

**EMR Serverless:** Deploy OpenDQV as a sidecar container in the same VPC subnet as the EMR Serverless job. Set `OPENDQV_URL` to the sidecar's internal endpoint. The `foreachBatch` pattern (Approach 2) works without modification on EMR Serverless.

```python
# EMR Serverless — same foreachBatch pattern; adjust checkpoint to S3
query = (
    orders_stream.writeStream
    .foreachBatch(validate_micro_batch)
    .option("checkpointLocation", "s3://my-bucket/checkpoints/opendqv-orders")
    .trigger(processingTime="30 seconds")
    .start()
)
```

---

## GCP Dataproc

**GCS trigger:** Use `readStream.format("cloudFiles")` with a GCS path, or use a Pub/Sub subscription as the streaming source. The `foreachBatch` pattern (Approach 2) applies without modification.

**Dataproc Serverless:** Deploy OpenDQV as a Cloud Run sidecar or in the same VPC as the Dataproc Serverless batch workload. Set `OPENDQV_URL` to the Cloud Run internal URL. The batch gate (Approach 1) is the simplest fit for Dataproc Serverless batch jobs.

```python
# Dataproc — GCS streaming source example
raw_stream = (
    spark.readStream.format("json")
    .option("path", "gs://my-bucket/landing/orders/")
    .load()
)
# Then apply the same foreachBatch pattern as Approach 2
```

---

## Limitations

| Limitation | Detail |
|---|---|
| `.collect()` memory | Collecting a large DataFrame to the driver for batch validation is memory-bounded. Use `foreachPartition` or the streaming approach for DataFrames larger than ~10M rows |
| `unique` rule in distributed mode | The `unique` rule requires the full dataset. In partitioned Spark jobs, per-partition validation cannot detect cross-partition duplicates — use a global deduplication step before validation |
| Structured Streaming latency | `foreachBatch` adds one OpenDQV round-trip per micro-batch trigger interval. Keep trigger intervals ≥10s for small batches; tune down for high-volume streams |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Add `validate_batch` before every Delta write in batch jobs (Approach 1) |
| **Now** | Set `asset_id` to the `spark://` convention for lineage linkage |
| **Now** | Pass `batch_id` as `trace_id` for end-to-end correlation |
| **Planned — based on community demand** | Switch Kafka → Delta pipelines to `foreachBatch` streaming validation (Approach 2) |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Spark features.

---

## See Also

- [`databricks_integration.md`](databricks_integration.md) — Auto Loader, DLT, Jobs/Asset Bundles, Unity Catalog, multi-workspace federation
- [`snowflake_integration.md`](snowflake_integration.md) — Snowflake integration (same pre-write pattern)
- [`kafka_integration.md`](kafka_integration.md) — validate before committing Kafka offset (pairs with Approach 2)
- [`orchestrator_integration.md`](orchestrator_integration.md) — Airflow, Prefect, Dagster gate pattern
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
