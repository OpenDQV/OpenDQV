# OpenDQV Documentation

OpenDQV Core is a centralised data quality validation API for the enterprise — validation is ephemeral, record values are never stored.

**Bad data blocked at the door. Not discovered three weeks later.**
A `422` at the point of write closes the feedback loop — producers see failures immediately and fix them upstream. This is why rejection rates drop over time.

The pattern: source systems call OpenDQV before writing. Bad records are blocked immediately.

## Core Concepts

- **Data Contracts** — Versioned YAML definitions of validation criteria for business entities
- **Severity** — Rules can block (`error`) or flag (`warning`)
- **Contexts** — Override rules per source system or region (e.g. billing vs. operations)
- **Ephemeral validation** — Record values are never stored or logged. Each validation request is independent. Metadata (tokens, contracts, audit history) is persisted in SQLite.
- **Maker-Checker** — Contract changes require approver/admin role; enforced in `token` auth mode

## Rule Types

| Type | Description |
|------|-------------|
| `not_empty` | Field must be present and non-empty |
| `regex` | Field must match a regular expression |
| `min` / `max` | Numeric field bounds |
| `range` | Numeric field between min and max |
| `min_length` / `max_length` | String length constraints |
| `date_format` | Must be a parseable date/datetime string |
| `unique` | No duplicate values in batch (batch mode only) |
| `min_age` / `max_age` | Date field implies age constraint |
| `compare` | **Cross-field:** `field` op `compare_to` (gt, lt, gte, lte, eq, neq) |
| `required_if` | **Conditional:** field required when another field equals a value |
| `lookup` | **Reference:** value must appear in a file (txt, CSV) or HTTP endpoint |

### Cross-Field Rules

Validate relationships between two fields in the same record:

```yaml
# impression_end must be strictly after impression_start
- name: impression_end_after_start
  type: compare
  field: impression_end
  compare_to: impression_start
  compare_op: gt
  error_message: "impression_end must be later than impression_start"

# refresh_rate_hz required only for DIGITAL panels
- name: refresh_rate_required_for_digital
  type: required_if
  field: refresh_rate_hz
  required_if:
    field: panel_type
    value: DIGITAL
  error_message: "refresh_rate_hz is required when panel_type is DIGITAL"
```

`compare` works with numbers, ISO 8601 date strings, and plain strings.
`required_if` is triggered only when the specified field equals the specified value.

### Lookup Rules — Local File or REST Endpoint

Validate a field against a reference list that changes at runtime:

```yaml
# Local file (one value per line)
- name: panel_id_valid
  type: lookup
  field: panel_id
  lookup_file: /app/data/active_panels.txt
  error_message: "panel_id not found in active panel registry"

# HTTP endpoint — JSON array or newline-delimited text
- name: panel_id_valid
  type: lookup
  field: panel_id
  lookup_file: https://registry.example.com/api/active-panels
  cache_ttl: 300   # cache for 5 minutes (default: 300s)
  error_message: "panel_id not found in active panel registry"
```

For CSV files, add `lookup_field: column_name`. Local files are loaded once and
cached in-process. HTTP endpoints are fetched on first use and cached for `cache_ttl`
seconds (default 300). Mount local files via Docker volume for production use.

## Contract Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique contract identifier |
| `version` | yes | Semver string (e.g. `"1.0"`) |
| `description` | no | Human-readable summary |
| `owner` | no | Team or individual responsible — surfaced in error responses for escalation routing |
| `owner_team` | no | Team identifier for BCBS 239 / governance audit. Synced to Marmot as `contractOwnerTeam` in the OpenLineage quality facet. |
| `status` | no | `active` (default), `draft`, or `archived` |
| `asset_id` | no | Catalog reference — link to Collibra, Atlan, DataHub, Marmot, or dbt model ref. Required for Marmot lineage push. |
| `downstream_consumers` | no | List of Marmot MRNs for downstream consumers of this asset (e.g. dashboards, dbt models). `push_quality_lineage.py` stitches direct lineage edges to each consumer automatically. Target MRNs must exist in Marmot. |
| `catalog_visible` | no | Boolean, default `true`. Set to `false` to exclude this contract from `push_quality_lineage.py` pushes and from Marmot `discover_data` responses via the proxy filter. |
| `rules` | yes | List of validation rules |
| `contexts` | no | Per-source-system or per-region rule overrides |

### `asset_id` — Catalog Linkage

The optional `asset_id` field links a contract to your data catalog. It is free-form
and follows the convention of your catalog:

```yaml
contract:
  name: customer
  asset_id: "urn:opendqv:customer"          # simple URN
  # asset_id: "ref:dbt::project.customer"  # dbt model ref
  # asset_id: "urn:li:dataset:(urn:li:dataPlatform:snowflake,db.schema.customer,PROD)"  # DataHub
```

`asset_id` is returned in `GET /api/v1/contracts` and `GET /api/v1/contracts/{name}`.

### Catalog URN Conventions

Use the following `asset_id` format for interoperability with common data catalog tools:

| Catalog | Format |
|---------|--------|
| dbt Cloud | `dbt://cloud.getdbt.com/projects/{project_id}/models/{model_name}` |
| DataHub | `urn:li:dataset:(urn:li:dataPlatform:dbt,{project}.{model},PROD)` |
| Atlan | `default/{connection_name}/{database}/{schema}/{table}` |
| Collibra | `{community}/{domain}/{asset_name}` |
| PostgreSQL | `postgres://{host}/{database}/{schema}/{table}` |
| Snowflake | `snowflake://{account}/{database}/{schema}/{table}` |
| Databricks / Unity Catalog | `databricks://{workspace-host}/{catalog}/{schema}/{table}` |
| Microsoft Purview | `{data_source_type}://{host}/{database}/{schema}/{table}` |
| OpenMetadata | `{service}.{database}.{schema}.{table}` |

The `asset_id` field is free-form — use whatever convention matches your catalog. See [dbt integration guide](dbt_integration.md) for dbt-specific patterns.

## Observability — `trace_id`

Pass a `trace_id` header (or query param) on every validation call to correlate
OpenDQV decisions with your application traces:

```bash
curl -s -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -H "X-Trace-Id: 4bf92f3577b34da6a3ce929d0e0e4736" \
  -d '{"record": {...}, "contract": "customer"}'
```

OpenDQV echoes the `X-Trace-Id` back in the response headers and logs it at
`DEBUG` level. Use this to correlate validation failures with upstream service
traces in your observability platform (Jaeger, Zipkin, Datadog, etc.).

## Contract History — `approved_by`

Every contract change is written to an append-only, hash-chained audit log.
The `approved_by` field records the identity of the approver when a contract
transitions from `REVIEW` to `ACTIVE` under the maker-checker workflow:

```
GET /api/v1/contracts/{name}/history
→ [{
    "version": "1.1",
    "status": "active",
    "approved_by": "jane.doe@example.com",
    "entry_hash": "a3f...",
    "updated_at": "2026-03-09T12:00:00Z"
  }, ...]
```

## API and SDK

| Document | Purpose |
|----------|---------|
| [API Reference](api_reference.md) | All 50 REST endpoints, batch validation, importers |
| [Python SDK](sdk.md) | Sync/async clients, LocalValidator, guard decorator |
| [CLI Reference](cli.md) | All 18 CLI commands — validate, import, export, lifecycle, code generation |
| [MCP Server](mcp.md) | Claude Desktop and Cursor integration; contract discovery; write guardrails |

## Getting Started

| Document | Purpose |
|----------|---------|
| [README](../README.md) | Quick start — first validation in 5 minutes |
| [Quickstart](quickstart.md) | Build your first contract in 15 minutes |
| [Beginner Setup](beginner-quickstart.md) | No prior GitHub or Python experience needed |
| [Architecture](architecture.md) | Project structure, data flow, security controls |

## Contract Authoring

See [CONTRIBUTING](../CONTRIBUTING.md) for full authoring guide including cross-field
rules, lookup rules, and the `allowed_values` pattern.

## Operations

| Document | Purpose |
|----------|---------|
| [Administration](administration.md) | Auth modes, RBAC roles, token management, maker-checker workflow |
| [Production Deployment](production_deployment.md) | Token auth, TLS, Docker Compose, hardening |
| [Streamlit Workbench](ui.md) | Governance UI — 12 sections, monitoring, code export, import |
| [Code Generation](code_generation.md) | Push-down validation for Salesforce, JS, Snowflake, Spark SQL, BigQuery |
| [Observability](observability.md) | Prometheus metrics, alert rules, trace log, Grafana starter panels |
| [Runbook](runbook.md) | Deployment, day-2 operations, incident response |

## dbt Integration

See [dbt integration design](dbt_integration.md) for the full guide including
schema import, pre-hook patterns, and the dbt macro roadmap.

## Security

See [SECURITY.md](../SECURITY.md) for the vulnerability disclosure policy and deployment checklist.

| Document | Purpose |
|----------|---------|
| [Security Hardening](security/hardening.md) | Reverse proxy, TLS, WORM storage, HMAC rotation |
| [Threat Model](security/threat_model.md) | STRIDE analysis across all 7 attack surfaces |
| [Vulnerability Response Playbook](security/vulnerability_response_playbook.md) | Internal triage, disclosure, DORA/GDPR notification |

### CI Security Pipeline

Every push and pull request runs four security layers automatically:

| Layer | Tool | What it catches |
|---|---|---|
| Static analysis | bandit | Insecure Python patterns (hardcoded secrets, `eval`, subprocess injection) |
| Dependency CVEs | pip-audit | Known CVEs in Python packages at install time |
| Container image scan | **Trivy** | OS-layer CVEs in `python:3.11-slim` + secrets accidentally baked into the image |
| SBOM | cyclonedx-bom | Full software bill of materials, archived as a CI artifact |

Trivy results are uploaded to the **GitHub Security tab** (SARIF format) after every run. CRITICAL findings with an available fix block the build; HIGH findings without a fix are surfaced as warnings only (`--ignore-unfixed`). This matches the approach recommended in the [Vulnerability Response Playbook](security/vulnerability_response_playbook.md).

## Ecosystem

OpenDQV Core is the source-layer anchor of the modern data quality stack — designed to complement Soda, Great Expectations, dbt, and observability tools, not replace them. For organizations using Data Mesh, OpenDQV serves as a write-time quality enforcement layer at the data product port — validating records before they leave the producer's boundary.

| Document | Purpose |
|----------|---------|
| [Ecosystem Reference Stack](ecosystem_reference_stack.md) | One contract, two enforcement points — OpenDQV + dbt + Soda + Monte Carlo |
| [dbt Integration](dbt_integration.md) | Bidirectional import/export with dbt schema.yml |
| [Great Expectations Integration](gx_integration.md) | Import GX suites; export to GX; two-layer enforcement |
| [Soda Core Integration](soda_integration.md) | Import Soda checks.yml; pre-pipeline gate; webhook correlation |
| [Monte Carlo Integration](montecarlo_integration.md) | Trace log shipping; webhook correlation; asset_id bridge |
| [Orchestrator Integration](orchestrator_integration.md) | Airflow, Prefect, Dagster — pre-load validation gate |
| [Salesforce Integration](salesforce_integration.md) | Push-down Apex; HTTP callout before trigger; live governance |
| [Kafka Integration](kafka_integration.md) | Validate before committing offset; dead-letter topic pattern; async batch |
| [Postgres Integration](postgres_integration.md) | Validate before INSERT; quarantine table pattern; psycopg2 |
| [Snowflake Integration](snowflake_integration.md) | Python connector; Snowpipe; External Function; Streams & Tasks |
| [Spark Integration](spark_integration.md) | Delta Lake batch; Structured Streaming foreachBatch; EMR, Dataproc, HDInsight |
| [Databricks Integration](databricks_integration.md) | Delta Lake; DLT quarantine; Jobs/Asset Bundles gate; Unity Catalog; multi-workspace |
| [DuckDB Integration](duckdb_integration.md) | Local batch validation; in-process analytics; zero-copy DataFrames |
| [Pandas Integration](pandas_integration.md) | DataFrame validation; validate before to_sql(); LocalValidator |
| [Collibra Integration](collibra_integration.md) | Contract metadata sync; DQ scores; workflow triggers; rule-level mapping |
| [Microsoft Purview Integration](purview_integration.md) | Azure governance catalog; custom attributes; Event Hub webhook |
| [DataHub Integration](datahub_integration.md) | Metadata sync; quality trends; webhook tagging; custom assertions |
| [OpenMetadata Integration](openmetadata_integration.md) | Contract metadata; native Test Suite; webhook triggers; column-level rules |
| [LLM / AI Agent Integration](llm_integration.md) | Claude tool use, LangChain, MCP server, error remediation loop, security |
| [MCP Server](mcp.md) | Claude Desktop and Cursor integration; contract discovery; write guardrails |
| [Import Formats](importers.md) | GX, dbt, Soda, CSV, ODCS, CSVW, OTel, NDC — auto-generate contracts from existing schemas |
| [Data Profiler](profiler.md) | Auto-generate a contract from a sample of records |
| [Webhooks](webhooks.md) | Push validation events to external services |
| [Roadmap](roadmap.md) | Planned integrations and features based on community demand |
| [Contexts](contexts.md) | Per-source-system rule overrides; multi-tenant SaaS patterns; regional compliance |
| [Federation](federation.md) | Multi-node contract synchronisation; 2PC event flow; isolation handling |
| [Custom Rules](custom_rules.md) | Add domain-specific rule types in 3 steps |
