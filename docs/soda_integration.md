# Soda Core Integration

> **API last verified:** `soda-core v3.x` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [soda-core on PyPI](https://pypi.org/project/soda-core/)

OpenDQV blocks bad records before they reach the pipeline surface Soda scans. Soda then catches anything that changed or drifted after the data landed — schema drift, volume anomalies, referential integrity. The two tools are complementary: OpenDQV owns the write boundary; Soda owns the pipeline boundary.

---

## Approach 1 — Import Soda `checks.yml` into OpenDQV

If your team already has Soda `checks.yml` definitions, use them to bootstrap OpenDQV contracts. Already implemented.

### Usage

```bash
# Via REST API
curl -s -X POST http://localhost:8000/api/v1/import/soda \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"checks_yaml": "<contents of your checks.yml>"}' \
  | jq .yaml

# Via CLI
opendqv import-soda path/to/checks.yml
```

### Supported Soda check → OpenDQV rule mapping

| Soda check | OpenDQV rule type | Notes |
|---|---|---|
| `missing_count(column) = 0` | `not_empty` | Direct mapping |
| `duplicate_count(column) = 0` | `unique` | Batch mode only |
| `invalid_count(column) = 0` where `valid format: ...` | `regex` | Format pattern extracted |
| `min(column) >= N` | `min` | Numeric lower bound |
| `max(column) <= N` | `max` | Numeric upper bound |
| `avg(column) between N and M` | *not supported* | Aggregate; no per-record equivalent |
| `row_count between N and M` | *not supported* | Table-level; no per-record equivalent |
| `freshness(column) < Xh` | *not supported* | Temporal aggregate; no per-record equivalent |

For full importer details see [`importers.md`](importers.md).

### Example output

```yaml
contract:
  name: orders
  asset_id: "soda::orders"
  rules:
    - name: order_id_not_null
      type: not_empty
      field: order_id
      severity: error
    - name: order_id_unique
      type: unique
      field: order_id
      severity: error
    - name: amount_min
      type: min
      field: amount
      min: 0
      severity: error
```

---

## Approach 2 — Two-Layer Enforcement: OpenDQV at Source + Soda Post-Load

OpenDQV operates per-record at write-time (milliseconds). Soda operates at the aggregate/dataset level post-load (minutes to hours). The same field constraints can be expressed in both — OpenDQV blocks individual bad records; Soda confirms the resulting table meets aggregate quality expectations.

> **Aggregate vs per-record:** Soda `missing_count = 0` checks that no row in the whole table has a null in that column. OpenDQV `not_empty` checks every record individually as it arrives. For records that sneak through (e.g. written via a path that bypasses OpenDQV), Soda provides the safety net. See [`importers.md`](importers.md) for the semantic mapping table.

```
contracts/orders.yaml
       │
       ├──► OpenDQV  POST /validate  (write-time, per-record, milliseconds)
       │
       └──► Soda  checks.yml scan  (post-load, aggregate, minutes)
                via: opendqv import-soda (existing checks → contract)
```

### Source-side enforcement

```python
from opendqv.sdk import OpenDQVClient
import os

client = OpenDQVClient("http://opendqv:8000", token=os.getenv("OPENDQV_TOKEN"))

for record in incoming_records:
    result = client.validate(record, contract="orders")
    if not result["valid"]:
        raise ValueError(f"Record rejected: {result['errors']}")
    write_to_staging(record)
```

### Post-load Soda scan

```yaml
# soda/checks/orders.yml
checks for orders:
  - missing_count(order_id) = 0
  - duplicate_count(order_id) = 0
  - min(amount) >= 0
  - schema:
      name: Check schema matches OpenDQV contract
      fail:
        when required column missing:
          - order_id
          - amount
          - status
```

Run the Soda scan after your load job:

```bash
soda scan -d your_warehouse -c soda/configuration.yml soda/checks/orders.yml
```

---

## Approach 3 — Pre-Pipeline Gate: Only Clean Records Reach Soda's Scan Surface

Use OpenDQV as a gate before data lands in the warehouse table Soda scans. Bad records are quarantined at the source; Soda's scan surface is clean by construction.

```python
import os
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient("http://opendqv:8000", token=os.getenv("OPENDQV_TOKEN"))

clean_records = []
quarantine = []

for record in batch:
    result = client.validate(record, contract="orders")
    if result["valid"]:
        clean_records.append(record)
    else:
        quarantine.append({"record": record, "errors": result["errors"]})

# Only clean records land in the warehouse table Soda will scan
load_to_warehouse(clean_records)

# Quarantined records go to a dead-letter table for review
if quarantine:
    load_to_quarantine_table(quarantine)
    print(f"Quarantined {len(quarantine)} records — Soda scan surface unaffected")
```

**Why this matters:** Soda checks like `missing_count = 0` will fail if even one bad record lands in the table. By pre-filtering at source, you prevent spurious Soda failures caused by records that should never have been written.

---

## Approach 4 — Webhook-Driven: `opendqv.validation.failed` → Soda Cloud Notification

When OpenDQV rejects a batch of records, push a notification to Soda Cloud to flag the affected dataset.

### OpenDQV webhook payload

```json
{
  "event": "opendqv.validation.failed",
  "contract": "orders",
  "asset_id": "urn:opendqv:acme:orders:1.0",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "failed_count": 7,
  "timestamp": "2026-03-13T10:00:00Z"
}
```

### Receiver pushing to Soda Cloud

```python
from fastapi import FastAPI, Request
import requests, os

app = FastAPI()
SODA_CLOUD_API = "https://cloud.soda.io/api/v1"
SODA_API_KEY = os.getenv("SODA_API_KEY")

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    # Push custom event to Soda Cloud
    requests.post(
        f"{SODA_CLOUD_API}/custom-events",
        headers={"Authorization": f"Bearer {SODA_API_KEY}"},
        json={
            "type": "opendqv_rejection",
            "dataset": payload["contract"],
            "message": f"OpenDQV blocked {payload['failed_count']} records",
            "trace_id": payload["trace_id"],
            "timestamp": payload["timestamp"],
        },
    )
    return {"status": "forwarded"}
```

Register in OpenDQV:

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

See [`webhooks.md`](webhooks.md) for full webhook configuration.

---

## Approach 5 — Incremental via `contract_hash`

Skip Soda check generation for contracts that have not changed:

```python
import requests, json
from pathlib import Path

REGISTRY_URL = "http://localhost:8000/api/v1/registry"
HASH_STORE = Path(".soda_export_hashes.json")

known_hashes = json.loads(HASH_STORE.read_text()) if HASH_STORE.exists() else {}
contracts = requests.get(
    REGISTRY_URL,
    headers={"Authorization": "Bearer <token>"},
).json()

for contract in contracts:
    name = contract["name"]
    current_hash = contract["contract_hash"]

    if known_hashes.get(name) == current_hash:
        continue  # unchanged — skip

    # Re-generate Soda checks from updated contract
    import subprocess
    subprocess.run(
        ["opendqv", "export-soda", name,
         "--output", f"soda/checks/{name}.yml"],
        check=True,
    )
    known_hashes[name] = current_hash
    print(f"Updated Soda checks for: {name}")

HASH_STORE.write_text(json.dumps(known_hashes, indent=2))
```

---

## Approach 6 — Federation-Aware

In a federated deployment, generate Soda checks per OpenDQV region instance:

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
        import subprocess
        subprocess.run(
            ["opendqv", "--url", base_url,
             "export-soda", contract["name"],
             "--output", f"soda/checks/{region}/{contract['name']}.yml"],
            check=True,
        )
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Aggregate checks | Soda `row_count`, `freshness`, `avg`, `schema` checks have no per-record OpenDQV equivalent — maintain these in Soda directly |
| Soda Cloud vs Soda Core | The webhook approach targets Soda Cloud; Soda Core (open source) has no push notification endpoint |

---

## `asset_id` Conventions

| Source | Format |
|---|---|
| Soda import | `soda::{dataset_name}` |
| OpenDQV native | `urn:opendqv:{org}:{contract}:{version}` |
| Snowflake (via Soda) | `snowflake://{account}/{database}/{schema}/{table}` |

See [`index.md`](index.md) for the full cross-catalog URN table.

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Use `import/soda` to bootstrap contracts from existing `checks.yml` files |
| **Now** | Enforce per-record at write-time via `POST /api/v1/validate` |
| **Now** | Use OpenDQV as a pre-pipeline gate to keep Soda's scan surface clean |
| **Planned — based on community demand** | Add incremental hash-based sync into your CI/CD pipeline |
| **Planned — based on community demand** | Webhook-triggered Soda Cloud notifications on validation failure cluster |
| **Planned — based on community demand** | `export-soda` CLI command — generate Soda `checks.yml` from OpenDQV contracts |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Soda features including `export-soda` CLI command, Soda Cloud native connector, and aggregate rule types.

---

## See Also

- [`importers.md`](importers.md) — Soda importer implementation details
- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
- [`gx_integration.md`](gx_integration.md) — Great Expectations integration (same two-layer pattern)
- [`dbt_integration.md`](dbt_integration.md) — dbt integration
