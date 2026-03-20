# OpenDQV Ecosystem Reference Stack

> **Last reviewed:** 2026-03-14. Tool landscape references (Soda, Great Expectations, Monte Carlo) reflect the ecosystem as of this date.

## The Modern Data Quality Stack

OpenDQV occupies a specific, well-defined position in the data quality ecosystem: **source-layer validation**. It is designed to complement — not replace — the tools that operate at other points in the data lifecycle.

This reference stack describes a layered architecture for deploying OpenDQV alongside Soda, Great Expectations, and Monte Carlo to cover all three moments in the data lifecycle.

---

## Stack Overview

```
Data Source                  Pipeline                     Warehouse / Lake
(Salesforce, API,            (Airbyte, dbt,               (Snowflake, Databricks,
 web forms, IoT)              Airflow, Prefect)              BigQuery, Redshift)
     │                             │                              │
     ▼                             ▼                              ▼
┌──────────┐              ┌──────────────┐              ┌──────────────────┐
│ OpenDQV  │              │  Soda /      │              │  Monte Carlo     │
│          │              │  Great       │              │                  │
│  Blocks  │              │  Expectations│              │                  │
│  at      │              │              │              │                  │
│  write   │              │  Checks at   │              │  Monitors at     │
│          │              │  pipeline    │              │  rest            │
└──────────┘              │  boundary    │              └──────────────────┘
     │                    └──────────────┘                       │
     │                             │                             │
     └─────── YAML contract ───────┘                            │
              (single source                                     │
               of truth)                    HMAC trace log ──────┘
                                            (tamper-evident,
                                             shipped to WORM)
```

### The three moments

| Moment | Tool | What it catches | When you find out |
|--------|------|-----------------|-------------------|
| **At write** | OpenDQV | Invalid records before they enter any system | Milliseconds — the `422` response blocks the write |
| **At pipeline** | Soda / Great Expectations | Data that passed write-time validation but drifted during transformation | Minutes to hours — batch scan |
| **At rest** | Monte Carlo / observability | Anomalies, schema drift, volume changes in live data | Continuous — alert-driven |

**None of these tools replaces the others.** A record that passes OpenDQV at the source can still be corrupted by a transformation. A clean warehouse table can still have anomalous distributions. All three layers are needed for a complete data quality posture.

---

## The "One Contract, Two Enforcement Points" Pattern

The most effective integration pattern:

**Use the same OpenDQV YAML contract to enforce rules at both the source boundary and the warehouse boundary.**

```
contracts/customer.yaml
       │
       ├──► OpenDQV  POST /validate  (write-time, milliseconds)
       │
       └──► dbt      schema.yml tests  (post-load, minutes)
                via: opendqv import-dbt / CLI
```

### How to set it up

**Step 1:** Author your rules once in OpenDQV YAML:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "urn:opendqv:acme:customer:1.0"
  rules:
    - name: email_valid
      field: email
      type: regex
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
      error_message: "Must be a valid email address"
    - name: age_range
      field: age
      type: range
      min: 0
      max: 120
      error_message: "Age must be between 0 and 120"
```

**Step 2:** Enforce at write time via OpenDQV:

```bash
curl -X POST http://opendqv.internal:8000/api/v1/validate \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"contract": "customer", "record": {...}}'
```

**Step 3:** Import the same contract into dbt for post-load enforcement:

```bash
# Export as dbt schema.yml
opendqv export-dbt --contract customer --out models/staging/schema.yml
```

Or import your existing dbt schema into OpenDQV:

```bash
opendqv import-dbt models/staging/schema.yml
```

**Result:** The same business rules enforced at two points in the data lifecycle, from a single YAML source of truth. No duplication. No drift between write-time and warehouse-time rules.

---

## OpenDQV + Soda Core

Soda operates inside your pipeline — after data lands in a staging area or warehouse. OpenDQV operates before data enters the pipeline.

**Joint reference architecture:**

```python
# Source system: call OpenDQV before writing
result = opendqv_client.validate(record, contract="orders")
if not result["valid"]:
    raise ValueError(f"Record rejected: {result['errors']}")
write_to_database(record)

# Pipeline: Soda checks after transformation
# soda/checks/orders.yml references the same field names and constraints
# Soda catches drift that OpenDQV couldn't — e.g. data corrupted during JOIN
```

The `asset_id` field in OpenDQV contracts (`urn:opendqv:{org}:{contract}:{version}`) links the contract to a Soda dataset, enabling cross-tool traceability.

---

## OpenDQV + Great Expectations

Great Expectations and OpenDQV share a bidirectional import/export path:

```bash
# Import GX suite into OpenDQV
curl -X POST http://localhost:8000/api/v1/import/gx \
  -H "Authorization: Bearer <token>" \
  -d '{"suite_json": "<GX expectation suite JSON>"}'

# Export OpenDQV contract as GX suite (via CLI)
opendqv export-gx customer
```

For teams already using GX, OpenDQV can act as the runtime enforcement layer while GX handles batch expectation validation — using the same underlying rules.

---

## OpenDQV + Monte Carlo (Observability)

Monte Carlo monitors data *after* it lands. OpenDQV blocks data *before* it lands. The HMAC trace log connects them:

- Every OpenDQV validation decision is logged with a timestamp, record ID, contract version, and outcome
- The trace log is JSONL — ship it to any log aggregator (Datadog, Splunk, CloudWatch)
- Monte Carlo can ingest the trace log to correlate validation rejections with downstream anomalies

**Signal:** If Monte Carlo detects an anomaly in your warehouse on Tuesday, the OpenDQV trace log tells you whether the affected records passed or failed validation at the source — and when.

A native Monte Carlo → OpenDQV connector is not yet available.

---

## OpenDQV + Catalog Tools (Atlan, Microsoft Purview, Collibra)

The `asset_id` field uses a standardised URI format:

```
urn:opendqv:{organisation}:{contract_name}:{version}
```

Example:
```yaml
contract:
  name: customer
  version: "2.1"
  asset_id: "urn:opendqv:acme-corp:customer:2.1"
```

This URI can be used to link an OpenDQV contract to:
- An **Atlan** asset (data product, table, column group)
- A **Microsoft Purview** data asset
- A **Collibra** business term or data set

Data catalog tools can pull contract definitions via `GET /api/v1/contracts/{name}` and display validation rules alongside lineage and ownership metadata — giving data consumers full visibility into what rules govern a dataset before they query it.

---

## Air-Gapped and Offline Deployments

OpenDQV has **no runtime internet dependencies**. Once the container image is pulled:

- No external API calls
- No telemetry
- No license server checks
- Runs indefinitely without network access

This makes it suitable for:
- NHS and healthcare secure zones
- Defence and classified environments
- Industrial / SCADA networks
- Development environments without internet access
- Organisations with strict egress controls

See [docs/runbook.md](runbook.md) for the air-gapped deployment note.

---

## Profiling and Drift Monitoring

OpenDQV validates records at the point of write — it does not monitor data distributions over time or detect schema drift. For those capabilities, use:

| Tool | Purpose |
|------|---------|
| [Great Expectations](https://greatexpectations.io) | Batch profiling, distribution expectations, data docs |
| [Soda](https://www.soda.io) | Aggregate checks: row_count, freshness, schema change detection |
| [Evidently](https://evidentlyai.com) | ML model and dataset drift monitoring |
| [Monte Carlo](https://www.montecarlodata.com) | Data observability, anomaly detection at warehouse scale |

OpenDQV's built-in `profile_records()` function generates *suggested* validation rules from a sample of records — a one-time bootstrapping aid to help you write your first contract. Once those rules are reviewed and activated, OpenDQV enforces them at write time. GX or Soda then validates the results at the pipeline layer.

**The pattern:** OpenDQV (write boundary) + Soda/GX (pipeline layer) + Monte Carlo (warehouse observability) covers the full data quality lifecycle without duplication.

---

## Further Reading

- [dbt Integration Guide](dbt_integration.md)
- [Great Expectations Integration](gx_integration.md)
- [Soda Core Integration](soda_integration.md)
- [Monte Carlo Integration](montecarlo_integration.md)
- [Orchestrator Integration — Airflow, Prefect, Dagster](orchestrator_integration.md)
- [Kafka Integration](kafka_integration.md)
- [Snowflake Integration](snowflake_integration.md)
- [Spark Integration](spark_integration.md)
- [Databricks Integration](databricks_integration.md)
- [Collibra Integration](collibra_integration.md)
- [Microsoft Purview Integration](purview_integration.md)
- [LLM / AI Agent Integration](llm_integration.md)
- [Roadmap](roadmap.md)
- [Security Hardening](security/hardening.md)
- [API Reference](../README.md#api-reference)
