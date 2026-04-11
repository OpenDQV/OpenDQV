# Great Expectations Integration

> **API last verified:** `great-expectations v1.x` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [great-expectations on PyPI](https://pypi.org/project/great-expectations/)

OpenDQV blocks records at write-time; Great Expectations validates the same rules post-load. They operate at different moments in the data lifecycle but can share the same contract — one source of truth, two enforcement points.

---

## Design Goals

1. **Contract-first** — OpenDQV YAML is the source of truth; GX expectation suites are derived
2. **Bidirectional** — import GX suites into OpenDQV contracts; export OpenDQV contracts back to GX suites (both already implemented)
3. **Record vs dataset semantics** — OpenDQV enforces per-record at write-time; GX validates dataset-level expectations post-load; both approaches are documented here
4. **Non-invasive** — no changes required to your existing GX setup; OpenDQV is additive

---

## What OpenDQV Is NOT

| Claim | Reality |
|-------|---------|
| A replacement for Great Expectations | No — GX covers batch, profiling, and aggregate expectations OpenDQV does not |
| A GX plugin or extension | No — OpenDQV is a standalone API; integration is via import/export |
| A data store | No — OpenDQV never stores record values; it validates records and returns results |
| Required for GX to work | No — you can adopt OpenDQV incrementally without changing your GX setup |

---

## Approach 1 — Import a GX Expectation Suite into OpenDQV

If your team already has GX expectation suites, use them to bootstrap OpenDQV contracts. The importer is already implemented.

### Usage

```bash
# Via REST API
curl -s -X POST http://localhost:8000/api/v1/import/gx \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"suite_json": "<contents of your GX expectation suite JSON>"}' \
  | jq .yaml

# Via CLI
opendqv import-gx path/to/expectation_suite.json
```

### Supported GX expectation → OpenDQV rule mapping

| GX expectation | OpenDQV rule type | Notes |
|---|---|---|
| `expect_column_values_to_not_be_null` | `not_empty` | Direct mapping |
| `expect_column_values_to_be_unique` | `unique` | Batch mode only |
| `expect_column_values_to_match_regex` | `regex` | Pattern mapped directly |
| `expect_column_values_to_be_between` | `range` | `min_value` / `max_value` |
| `expect_column_value_lengths_to_be_between` | `min_length` / `max_length` | Split into two rules |
| `expect_column_values_to_be_in_set` | `regex` | Values converted to `^(a\|b\|c)$` pattern |
| `expect_table_row_count_to_be_between` | *not supported* | Dataset-level; no per-record equivalent |
| `expect_column_pair_values_*` | *not supported* | Cross-column; manual mapping required |

For full details on the importer see [`importers.md`](importers.md).

### Example output

```yaml
contract:
  name: customer
  asset_id: "gx::customer_suite"
  rules:
    - name: email_not_null
      type: not_empty
      field: email
      severity: error
    - name: email_format
      type: regex
      field: email
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
      severity: error
    - name: age_range
      type: range
      field: age
      min: 0
      max: 120
      severity: error
```

---

## Approach 2 — Export an OpenDQV Contract as a GX Expectation Suite

If you author contracts in OpenDQV and want to push them into GX for batch validation, use the `export-gx` command. Already implemented.

### Usage

```bash
# Print the GX expectation suite JSON to stdout
opendqv export-gx customer

# Write to a file for use in your GX project
opendqv export-gx customer --output great_expectations/expectations/customer.json
```

### Output format

The exporter produces a GX v1 expectation suite JSON. Each OpenDQV rule becomes one or more GX expectations:

```json
{
  "expectation_suite_name": "customer",
  "expectations": [
    {
      "expectation_type": "expect_column_values_to_not_be_null",
      "kwargs": {"column": "email"}
    },
    {
      "expectation_type": "expect_column_values_to_match_regex",
      "kwargs": {
        "column": "email",
        "regex": "^[^@]+@[^@]+\\.[^@]+$"
      }
    },
    {
      "expectation_type": "expect_column_values_to_be_between",
      "kwargs": {"column": "age", "min_value": 0, "max_value": 120}
    }
  ]
}
```

---

## Approach 3 — Two-Layer Enforcement: OpenDQV at Write + GX Post-Load

The "one contract, two enforcement points" pattern — the same OpenDQV YAML drives both write-time blocking and post-load batch validation.

```
contracts/customer.yaml
       │
       ├──► OpenDQV  POST /validate  (write-time, per-record, milliseconds)
       │
       └──► GX  Checkpoint run  (post-load, dataset-level, minutes)
                via: opendqv export-gx
```

### Setup

**Step 1:** Author rules once in OpenDQV YAML:

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
      severity: error
    - name: age_range
      field: age
      type: range
      min: 0
      max: 120
      severity: error
```

**Step 2:** Enforce at write time via OpenDQV:

```python
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient("http://opendqv:8000", token=os.getenv("OPENDQV_TOKEN"))
result = client.validate(record, contract="customer")
if not result["valid"]:
    raise ValueError(f"Record rejected: {result['errors']}")
write_to_database(record)
```

**Step 3:** Export to GX for post-load batch validation:

```bash
opendqv export-gx customer \
  --output great_expectations/expectations/customer.json
```

**Step 4:** Run your GX Checkpoint as usual:

```python
import great_expectations as gx

context = gx.get_context()
result = context.run_checkpoint(checkpoint_name="customer_checkpoint")
```

**Result:** The same business rules enforced at two points — write-time (OpenDQV) and post-load (GX) — from a single YAML source of truth. No rule duplication. No drift.

### Key semantic difference: `mostly` vs per-record

GX expectations support a `mostly` parameter (e.g. `mostly: 0.95` = 95% of rows must pass). OpenDQV enforces per-record — every record is evaluated independently, with no pass-rate threshold.

When exporting to GX, OpenDQV does **not** set `mostly` — expectations default to 100% pass rate. If you need a tolerance, set `mostly` manually in the exported GX suite.

---

## Approach 4 — Webhook-Driven: Trigger a GX Checkpoint on Validation Failure

Use OpenDQV webhooks to trigger a GX Checkpoint run when `opendqv.validation.failed` fires — letting GX scan the affected dataset immediately after a write-time rejection cluster is detected.

### Webhook payload (OpenDQV → your service)

```json
{
  "event": "opendqv.validation.failed",
  "contract": "customer",
  "asset_id": "urn:opendqv:acme:customer:1.0",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "failed_count": 12,
  "timestamp": "2026-03-13T10:00:00Z"
}
```

### Receiver (FastAPI)

```python
from fastapi import FastAPI, Request
import great_expectations as gx

app = FastAPI()

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    contract = payload["contract"]
    context = gx.get_context()
    result = context.run_checkpoint(checkpoint_name=f"{contract}_checkpoint")

    return {
        "status": "checkpoint_triggered",
        "contract": contract,
        "gx_success": result.success,
    }
```

Register the webhook in OpenDQV:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-service.internal/webhooks/opendqv",
    "events": ["opendqv.validation.failed"],
    "secret": "<HMAC_SECRET>"
  }'
```

See [`webhooks.md`](webhooks.md) for full webhook configuration options.

---

## Approach 5 — Incremental via `contract_hash`

Avoid re-exporting GX suites for contracts that have not changed. Poll `GET /api/v1/registry` and compare `contract_hash`:

```python
import requests, json, subprocess
from pathlib import Path

REGISTRY_URL = "http://localhost:8000/api/v1/registry"
HASH_STORE = Path(".gx_export_hashes.json")

known_hashes = json.loads(HASH_STORE.read_text()) if HASH_STORE.exists() else {}
contracts = requests.get(REGISTRY_URL, headers={"Authorization": "Bearer <token>"}).json()

for contract in contracts:
    name = contract["name"]
    current_hash = contract["contract_hash"]

    if known_hashes.get(name) == current_hash:
        continue  # unchanged — skip

    # Export updated contract to GX
    subprocess.run(
        ["opendqv", "export-gx", name,
         "--output", f"great_expectations/expectations/{name}.json"],
        check=True,
    )
    known_hashes[name] = current_hash
    print(f"Updated GX suite for: {name}")

HASH_STORE.write_text(json.dumps(known_hashes, indent=2))
```

---

## Approach 6 — Federation-Aware Sync

In a federated deployment (multiple OpenDQV instances per region or domain), export GX suites per instance and namespace by `asset_id` prefix:

```python
INSTANCES = {
    "eu-west": "http://opendqv-eu.internal:8000",
    "us-east": "http://opendqv-us.internal:8000",
}

for region, base_url in INSTANCES.items():
    contracts = requests.get(
        f"{base_url}/api/v1/registry",
        headers={"Authorization": "Bearer <token>"},
    ).json()
    for contract in contracts:
        subprocess.run(
            ["opendqv", "--url", base_url,
             "export-gx", contract["name"],
             "--output",
             f"great_expectations/expectations/{region}/{contract['name']}.json"],
            check=True,
        )
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Aggregate expectations | GX table-level expectations (row count, column existence) have no OpenDQV equivalent — maintain these in GX directly |
| `mostly` threshold | OpenDQV enforces per-record; GX `mostly` is not set on export — add manually if needed |
| Cross-expectation dependencies | GX has no cross-expectation ordering; OpenDQV `compare` and `required_if` rules export as independent expectations |
| GX v0.x vs v1.x | The exporter targets GX v1.x suite format; v0.x uses a different JSON schema |

---

## `asset_id` Conventions

| Source | Format |
|---|---|
| GX suite import | `gx::{suite_name}` |
| OpenDQV native | `urn:opendqv:{org}:{contract}:{version}` |
| DataHub | `urn:li:dataset:(urn:li:dataPlatform:snowflake,db.schema.table,PROD)` |
| Atlan | `default/{connection}/{database}/{schema}/{table}` |

See [`index.md`](index.md) for the full cross-catalog URN table.

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Use `import/gx` to bootstrap contracts from existing GX suites |
| **Now** | Use `export-gx` to generate GX suites from OpenDQV contracts |
| **Now** | Set `asset_id` on contracts for cross-tool traceability |
| **Planned — based on community demand** | Integrate incremental hash-based sync into your CI/CD pipeline |
| **Planned — based on community demand** | Add webhook-triggered Checkpoint runs for near-real-time correlation |
| **Planned — based on community demand** | Native GX Data Context plugin for push-based contract sync |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned GX features including native GX Data Context plugin, `mostly` threshold mapping, and Checkpoint status back-channel.

---

## See Also

- [`importers.md`](importers.md) — GX importer implementation details and supported expectation types
- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
- [`soda_integration.md`](soda_integration.md) — Soda Core integration (same two-layer pattern)
- [`dbt_integration.md`](dbt_integration.md) — dbt integration (bidirectional import/export)
