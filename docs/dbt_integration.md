# dbt Integration Design

> **API last verified:** `dbt ≥1.0`, `dbt_expectations ≥0.10.0`, `dbt_utils ≥1.0.0` — 2026-03-13.

![OpenDQV + dbt — import schema.yml as a validation contract](demo_dbt.gif)

*Import your existing dbt `schema.yml` column tests as an OpenDQV contract in one command — your dbt quality rules, now enforced at the write boundary before data ever reaches the warehouse.*

> Snippets are examples; pin your own version in `requirements.txt`.
> [Check for updates](https://pypi.org/project/dbt-core/)

OpenDQV and dbt serve complementary roles in the modern data stack:

- **dbt** transforms raw data into clean, tested models inside the warehouse
- **OpenDQV** validates data at source — before it enters the pipeline

The integration described here enables dbt test results to reference OpenDQV validation contracts, and allows dbt model schemas to be imported as OpenDQV contracts.

---

## Design Goals

1. **No lock-in** — works with any dbt project; no custom macros required
2. **Bidirectional** — import dbt `schema.yml` into OpenDQV contracts; export OpenDQV contracts back to dbt `schema.yml`
3. **Contract-first** — OpenDQV contracts are the source of truth; dbt tests are derived
4. **Traceable** — `asset_id` links OpenDQV contracts to dbt model refs

---

## Approach 1 — Import dbt `schema.yml` as an OpenDQV Contract

OpenDQV already ships a dbt importer (`core/importers/dbt.py`). It converts dbt
`schema.yml` column tests into OpenDQV validation rules.

### Supported dbt test → OpenDQV rule mapping

| dbt test         | OpenDQV rule type | Notes                          |
|------------------|-------------------|--------------------------------|
| `not_null`       | `not_empty`       | Direct mapping                 |
| `unique`         | `unique`          | Batch mode only                |
| `accepted_values`| `regex`           | OpenDQV `lookup` requires a lookup_file (file or URL); inline values are not supported. Values are converted to a regex pattern. Re-exports as `dbt_expectations.expect_column_values_to_match_regex` (requires dbt_expectations ≥0.8) — known round-trip limitation. |
| `relationships`  | *not supported*   | Requires cross-model context   |
| custom test      | *not supported*   | Requires manual mapping        |

> **dbt version compatibility:** dbt ≥1.0 uses `data_tests:` at the column level (renamed from `tests:`). Both keys are handled by the importer. Minimum `dbt_expectations` version for regex export: 0.8+ (the `packages.yml` example pins `>=0.10.0`).

### Usage

```bash
# Import a dbt schema.yml and print the resulting OpenDQV YAML
curl -s -X POST http://localhost:8000/api/v1/import/dbt \
  -H "Content-Type: application/json" \
  -d '{"schema_yaml": "<contents of models/schema.yml>"}' \
  | jq .yaml

# Or via the CLI
opendqv import-dbt models/schema.yml
```

The importer sets `asset_id` to `dbt::<model_name>`:

```yaml
contract:
  name: orders
  asset_id: "dbt::orders"
  rules:
    - name: order_id_not_null
      type: not_empty
      field: order_id
      severity: error
```

---

## Approach 2 — Export an OpenDQV Contract as dbt `schema.yml`

If you author contracts in OpenDQV and want to push column-level tests back into dbt,
use the `export-dbt` command. It converts OpenDQV validation rules to native dbt test
syntax that can be dropped straight into your dbt project.

### CLI usage

```bash
# Print dbt schema.yml to stdout
opendqv export-dbt orders

# Write directly to your dbt project
opendqv export-dbt orders --output models/staging/schema.yml
```

### Output format

The exporter produces a dbt v2 `schema.yml`. Each OpenDQV field becomes a column entry
and each rule becomes a test:

```yaml
version: 2
models:
  - name: orders
    description: ''
    columns:
      - name: order_id
        tests:
          - not_null
          - unique
      - name: status
        tests:
          - dbt_expectations.expect_column_values_to_match_regex:
              regex: '^(pending|confirmed|shipped|cancelled)$'
      - name: amount
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 1000000
```

### Rule → dbt test mapping

| OpenDQV rule type | dbt test |
|-------------------|----------|
| `not_empty`       | `not_null` |
| `unique`          | `unique` |
| `regex`           | `dbt_expectations.expect_column_values_to_match_regex` |
| `range`           | `dbt_utils.accepted_range` (with `min_value` / `max_value`) |
| `min`             | `dbt_utils.accepted_range` (`min_value` only) |
| `max`             | `dbt_utils.accepted_range` (`max_value` only) |
| `min_length`      | `dbt_expectations.expect_column_value_lengths_to_be_between` (`min_value`) |
| `max_length`      | `dbt_expectations.expect_column_value_lengths_to_be_between` (`max_value`) |
| all others        | skipped — reported to stderr |

Rules with unsupported types are skipped silently in the YAML output and reported
to `stderr` so you can address them without blocking the export:

```
[export-dbt] skipped rule 'fk_check' (type=relationships, field=customer_id): unsupported rule type for dbt export
```

### Required dbt packages

Add these to your `packages.yml` if you use `regex` or `range` rules:

```yaml
packages:
  - package: calogica/dbt_expectations
    version: [">=0.10.0", "<1.0.0"]
  - package: dbt-labs/dbt_utils
    version: [">=1.0.0", "<2.0.0"]
```

---

## Approach 3 — Pre-ingestion validation before dbt source tables

Source systems that write to dbt-managed tables can run OpenDQV before loading
data into your staging table. This catches quality issues at the boundary rather
than inside the warehouse.

### Pattern

```python
# In your ingestion script, before writing to the staging table:
import os
from opendqv.sdk import OpenDQVClient

# Pass the dbt job run ID as trace_id for end-to-end observability
dbt_run_id = os.getenv("DBT_JOB_RUN_ID", "local")

client = OpenDQVClient("http://opendqv:8000", token=os.getenv("OPENDQV_TOKEN"))
# Pass record_id= on individual validate() calls for per-record correlation
result = client.validate_batch(records, contract="orders")

if not result["summary"]["failed"] == 0:
    raise ValueError(f"OpenDQV blocked {result['summary']['failed']} records — ingestion aborted")

# Write clean records to staging
staging_table.insert(records)
```

dbt then runs against a staging table that has already passed OpenDQV validation.

---

## Approach 4 — Quality Trend as dbt Exposure Metadata

OpenDQV's `GET /api/v1/contracts/{name}/quality-trend` endpoint returns daily
pass rates. This data can be used to annotate dbt exposures:

```yaml
# exposures.yml
exposures:
  - name: customer_data_quality
    type: analysis
    owner:
      name: Data Governance Team
    description: >
      OpenDQV daily pass rate for the customer contract.
      Source: GET /api/v1/contracts/customer/quality-trend?days=30
    url: http://opendqv:8000/api/v1/contracts/customer/quality-trend
    depends_on:
      - ref('stg_customers')
```

---

## Recommended Integration Path (2026)

| Phase | Action |
|-------|--------|
| **Now** | Use the dbt importer (`/api/v1/import/dbt`) to bootstrap contracts from existing `schema.yml` files |
| **Now** | Use `export-dbt <contract>` to generate dbt column tests from OpenDQV contracts |
| **Now** | Set `asset_id` on contracts to match dbt model refs for catalog linkage |
| **Planned — based on community demand** | Add `trace_id` to dbt model runs; pass to OpenDQV for end-to-end lineage |
| **Planned — based on community demand** | Publish `opendqv_dbt` macro package for singular tests |
| **Planned — based on community demand** | Native dbt Cloud integration via webhook trigger on dbt job failure |

---

## Contract `asset_id` Conventions for dbt

When importing from dbt, use the following `asset_id` format for catalog interoperability:

| Catalog | Format |
|---------|--------|
| dbt Cloud | `dbt://cloud.getdbt.com/projects/{project_id}/models/{model_name}` |
| DataHub | `urn:li:dataset:(urn:li:dataPlatform:dbt,{project}.{model},PROD)` |
| Atlan | `default/{connection_name}/{database}/{schema}/{table}` |
| Collibra | `{community}/{domain}/{asset_name}` |

The `asset_id` field is free-form — use whatever convention matches your catalog.

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned dbt features including the `opendqv_dbt` macro package for singular tests and native dbt Cloud integration.

---

## See Also

- [REST API reference](../README.md)
- [Contract authoring guide](../CONTRIBUTING.md)
- [Runbook](runbook.md)
- dbt importer source: `core/importers/dbt.py`
- SDK: `sdk.py` — `OpenDQVClient`, `AsyncOpenDQVClient`
