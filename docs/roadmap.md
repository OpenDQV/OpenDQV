# OpenDQV Roadmap

> Last updated: 2026-03-13. All items are planned; none are committed delivery dates.

---

## Near-term (Q2 2026)

| Area | Item |
|------|------|
| dbt | `trace_id` propagation from dbt job runs → OpenDQV trace log |
| GX | Incremental hash-based contract sync in CI/CD |
| Soda | `export-soda` CLI command — generate Soda `checks.yml` from OpenDQV contracts |
| Orchestrators | `AsyncOpenDQVClient` recommended path for high-throughput Airflow / Prefect pipelines |
| Kafka | Async batch validation path for throughput above ~500 msg/s |
| Monte Carlo | `validation.failed` webhook → MC custom event receiver |

## Mid-term (Q3 2026)

| Area | Item |
|------|------|
| dbt | `opendqv_dbt` macro package for singular tests (Q3 2026) |
| GX | `mostly` threshold mapping — map OpenDQV `warning` severity → GX `mostly` |
| GX | Webhook-triggered Checkpoint runs for near-real-time GX correlation |
| Orchestrators | `apache-airflow-providers-opendqv` — native `OpenDQVValidateOperator` |
| Orchestrators | Prefect `OpenDQVCredentials` block |
| Kafka | Webhook → Kafka alert topic for rejection cluster monitoring |
| Monte Carlo | Quality-trend dashboard using OpenDQV pass-rate data |
| Collibra | Webhook → Collibra workflow trigger for `validation.failed` events |

## Mid-term continued

| Area | Item |
|------|------|
| Snowflake | External Function packaging — deploy OpenDQV validation as a Snowflake External Function with one-click setup |
| Snowflake | Streams + Tasks template — Terraform module for serverless event-driven validation |
| Databricks | Databricks Asset Bundle template — pre-built validation task for standard DAB workflow definitions |
| Purview | Packaged Azure Function for scheduled contract sync with Managed Identity auth |

## Longer-term (Q4 2026 – Q1 2027)

| Area | Item |
|------|------|
| dbt | Native dbt Cloud integration via webhook on dbt job failure |
| GX | Native GX Data Context plugin — push contract changes via GX API |
| GX | GX Checkpoint status back-channel → OpenDQV quality-trend endpoint |
| Orchestrators | `dagster-opendqv` library — typed asset checks and quality score sensor |
| Monte Carlo | Native Monte Carlo connector — table-level correlation, alert enrichment |
| Soda | Soda Cloud native connector — push contract metadata to Soda dataset catalog |
| Kafka | Native Kafka Connect sink connector with built-in OpenDQV validation |
| Kafka | Kafka Streams transformer (stateful, `unique` rule support) |
| Kafka | Schema Registry integration — Avro/Protobuf schemas → OpenDQV contracts |
| Snowflake | Snowflake Native App packaging |
| Databricks | Databricks Partner Connect integration |
| Purview | Native Purview Data Quality integration (pending GA API) |
| Collibra | Packaged connector with OAuth 2.0, scheduled sync, incremental updates |
| Collibra | Native Collibra Data Quality Module integration |

## Beyond (Q2 2027+)

| Area | Item |
|------|------|
| Soda | Aggregate rule types (`row_count`, `freshness`) in OpenDQV contracts |
| Core | Streaming `unique` rule via stateful stream processor integration |
| Core | Pass-rate SLA alerts — notify when contract pass rate drops below threshold |

---

## Principles

Roadmap items are added when there is concrete user demand or a clear integration gap. Items that have been implemented are removed from this file — see [CHANGELOG.md](../CHANGELOG.md) for what shipped and when.

Features marked as "planned" do not have committed delivery dates. If you need a specific integration urgently, open an issue at [https://github.com/OpenDQV/OpenDQV/issues](https://github.com/OpenDQV/OpenDQV/issues).
