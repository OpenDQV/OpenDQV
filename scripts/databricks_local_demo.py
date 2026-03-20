#!/usr/bin/env python3
"""
Databricks / PySpark local simulation demo.

Uses PySpark in local[*] mode to simulate the Databricks Auto Loader and
foreachBatch validation pattern — no cluster, no cloud account required.

Prerequisites:
    pip install pyspark  # heavyweight dep, not in pyproject.toml extras

Usage:
    python scripts/databricks_local_demo.py

This script is a local simulation only. For production Databricks usage,
see docs/databricks_integration.md.
"""

import sys

# Check PySpark is available before importing anything else
try:
    from pyspark.sql import SparkSession
    from pyspark.sql.functions import col, udf
    from pyspark.sql.types import BooleanType, StringType, StructType, StructField
except ImportError:
    print("PySpark not installed. Install with: pip install pyspark")
    print("This is a heavyweight optional dependency not included in the core package.")
    sys.exit(1)

import json
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdk.local import LocalValidator


# ── Spark session (local mode) ────────────────────────────────────────────────

spark = (
    SparkSession.builder
    .appName("opendqv-local-demo")
    .master("local[*]")
    .config("spark.sql.shuffle.partitions", "2")  # keep it fast for local demo
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")


# ── Sample data ───────────────────────────────────────────────────────────────

SAMPLE_RECORDS = [
    # Clean records
    ("Alice", "alice@example.com", 30, "+447911123456", 85,
     "2024-01-15", "alice123", "securepass", 100.0, "c1"),
    ("Bob", "bob@example.com", 25, "+14155552671", 72,
     "2024-03-01", "bob_data", "p@ssword99", 500.0, "c2"),
    # Invalid records
    ("", "not-an-email", -1, "+447900000001", 200,
     "2024-01-15", "u", "short", -50.0, "c-bad1"),
    ("Carol", "carol@example.com", 35, "+14155559876", 90,
     "2024-06-01", "carol_dq", "carolpass1", 250.0, "c3"),
]

schema = StructType([
    StructField("name", StringType(), True),
    StructField("email", StringType(), True),
    StructField("age", StringType(), True),       # string for UDF simplicity
    StructField("phone", StringType(), True),
    StructField("score", StringType(), True),
    StructField("date", StringType(), True),
    StructField("username", StringType(), True),
    StructField("password", StringType(), True),
    StructField("balance", StringType(), True),
    StructField("id", StringType(), True),
])

df = spark.createDataFrame(
    [(r[0], r[1], str(r[2]), r[3], str(r[4]), r[5], r[6], r[7], str(r[8]), r[9])
     for r in SAMPLE_RECORDS],
    schema=schema,
)

print(f"\n{'='*60}")
print("OpenDQV — Databricks/PySpark Local Simulation Demo")
print(f"{'='*60}")
print(f"\nInput DataFrame ({df.count()} records):")
df.show(truncate=False)


# ── Pattern 1: foreachBatch (recommended for production) ─────────────────────

print("\nPattern 1: foreachBatch — batch-validate per micro-batch")
print("-" * 50)

validator = LocalValidator()


def validate_micro_batch(batch_df, batch_id=0):
    """Validate a Spark DataFrame batch using LocalValidator."""
    records = [row.asDict() for row in batch_df.collect()]
    if not records:
        print(f"  Batch {batch_id}: empty — skipping")
        return [], []

    result = validator.validate_batch(records, contract="customer")
    summary = result["summary"]

    clean_indices = {r["index"] for r in result["results"] if r["valid"]}
    rejected_indices = {r["index"] for r in result["results"] if not r["valid"]}

    clean = [records[i] for i in sorted(clean_indices)]
    rejected = [
        {"record": records[i], "errors": result["results"][i]["errors"]}
        for i in sorted(rejected_indices)
    ]

    print(f"  Batch {batch_id}: {summary['passed']}/{summary['total']} passed, "
          f"{summary['failed']} quarantined")
    return clean, rejected


clean_records, rejected_records = validate_micro_batch(df)

print(f"\n  Clean records ({len(clean_records)}):")
for r in clean_records:
    print(f"    ✓ {r['name']} <{r['email']}>")

print(f"\n  Quarantined records ({len(rejected_records)}):")
for item in rejected_records:
    print(f"    ✗ {item['record']['name']!r} — "
          f"{[e['field'] + ': ' + e['message'] for e in item['errors'][:2]]}")


# ── Pattern 2: mapPartitions UDF ─────────────────────────────────────────────

print("\nPattern 2: Spark UDF — per-record validation (simpler, lower throughput)")
print("-" * 50)


@udf(returnType=BooleanType())
def validate_record_udf(record_json: str) -> bool:
    """UDF wrapping LocalValidator — one call per record."""
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sdk.local import LocalValidator
    v = LocalValidator()
    record = json.loads(record_json)
    # Convert numeric string fields back to numbers for validation
    for key in ("age", "score", "balance"):
        try:
            record[key] = float(record[key])
        except (ValueError, KeyError):
            pass
    result = v.validate(record, contract="customer")
    return result["valid"]


# noqa: E402 — must import after UDF definition
from pyspark.sql.functions import to_json, struct  # noqa: E402

annotated_df = df.withColumn(
    "_opendqv_valid",
    validate_record_udf(to_json(struct([col(c) for c in df.columns])))
)

clean_df = annotated_df.filter(col("_opendqv_valid")).drop("_opendqv_valid")
rejected_df = annotated_df.filter(~col("_opendqv_valid")).drop("_opendqv_valid")

print(f"\n  Clean DataFrame ({clean_df.count()} records):")
clean_df.show(truncate=False)

print(f"  Quarantine DataFrame ({rejected_df.count()} records):")
rejected_df.show(truncate=False)


# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print("Demo complete.")
print(f"  foreachBatch pattern: {len(clean_records)} clean, {len(rejected_records)} quarantined")
print(f"  UDF pattern:          {clean_df.count()} clean, {rejected_df.count()} quarantined")
print(f"{'='*60}\n")

spark.stop()
