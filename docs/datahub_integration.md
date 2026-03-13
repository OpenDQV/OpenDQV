# DataHub Integration Design

> **API last verified:** `acryl-datahub v1.4.0.5` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [Check for updates](https://pypi.org/project/acryl-datahub/)

OpenDQV and DataHub serve complementary roles: OpenDQV validates data at the source and governs contracts, while DataHub provides lineage, discovery, and observability across your entire data estate. This guide shows six approaches to connecting them, from a simple one-off sync to a real-time webhook-driven pipeline.

---

## Design Goals

1. **Contract-first** — OpenDQV contracts are the source of truth; DataHub reflects their state, not the reverse
2. **Incremental** — use `contract_hash` to skip unchanged contracts and avoid redundant API calls
3. **Federation-aware** — check `/federation/status` before pulling contracts from a federated node
4. **Non-invasive** — no changes to OpenDQV internals; integration lives entirely in a thin sync script or connector

---

## What OpenDQV Is NOT

| OpenDQV does | OpenDQV does NOT |
|---|---|
| Validate records against contract rules | Replace DataHub's lineage graph |
| Publish contract metadata (owner, version, hash) | Crawl your warehouse schemas |
| Emit quality trend scores (pass rate) | Manage DataHub users or permissions |
| Fire webhooks on validation failure | Track column-level lineage across models |
| Expose `asset_id` for catalog linkage | Delete or deprecate DataHub assets automatically |

---

## Approach 1 — Contract Metadata → `DatasetProperties` + `Ownership`

Push every active OpenDQV contract into DataHub as a dataset entity with `DatasetProperties` and `Ownership` aspects.

### Field mapping

| OpenDQV field | DataHub aspect / field |
|---|---|
| `name` | `DatasetProperties.name`; also used in `DatasetUrn` |
| `description` | `DatasetProperties.description` |
| `version` | `DatasetProperties.customProperties["contract_version"]` |
| `contract_hash` | `DatasetProperties.customProperties["contract_hash"]` |
| `owner` | `Ownership.owners[0].owner` (`TECHNICAL_OWNER`) |
| `owner_team` | `Ownership.owners[1].owner` via `make_group_urn` |
| `owner_email` | `DatasetProperties.customProperties["owner_email"]` |
| `status` | `DatasetProperties.customProperties["contract_status"]`; `deprecated` contracts are skipped |

### Python snippet

```python
# pip install acryl-datahub requests
import requests
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    DatasetPropertiesClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
)
from datahub.emitter.mce_builder import make_dataset_urn, make_user_urn, make_group_urn

OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
DATAHUB_GMS = "http://datahub-gms:8080"
# DATAHUB_TOKEN = "<DATAHUB_TOKEN>"  # required for DataHub Cloud

emitter = DatahubRestEmitter(
    gms_server=DATAHUB_GMS,
    # token=DATAHUB_TOKEN,
)

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for contract in contracts:
    # Skip deprecated contracts
    if contract.get("status") == "deprecated":
        print(f"Skipping deprecated contract: {contract['name']}")
        continue

    # Resolve URN: prefer asset_id if it already is a DataHub URN
    asset_id = contract.get("asset_id", "")
    if asset_id.startswith("urn:li:dataset:"):
        urn = asset_id
    else:
        platform = "opendqv"
        urn = make_dataset_urn(platform=platform, name=contract["name"], env="PROD")

    custom_props = {
        "contract_version": contract.get("version", ""),
        "contract_hash":    contract.get("contract_hash", ""),
        "contract_status":  contract.get("status", ""),
        "owner_email":      contract.get("owner_email", ""),
    }

    emitter.emit_mcp(
        urn=urn,
        aspect=DatasetPropertiesClass(
            name=contract["name"],
            description=contract.get("description", ""),
            customProperties=custom_props,
        ),
    )

    owners = []
    if contract.get("owner"):
        owners.append(OwnerClass(
            owner=make_user_urn(contract["owner"]),
            type=OwnershipTypeClass.TECHNICAL_OWNER,
        ))
    if contract.get("owner_team"):
        owners.append(OwnerClass(
            owner=make_group_urn(contract["owner_team"]),
            type=OwnershipTypeClass.DATAOWNER,  # deprecated in v1.4; prefer DATA_STEWARD or TECHNICAL_OWNER
        ))
    if owners:
        emitter.emit_mcp(urn=urn, aspect=OwnershipClass(owners=owners))

print(f"Synced {len(contracts)} contracts to DataHub")
```

Replace `datahub-gms:8080` with your GMS host. For DataHub Cloud, uncomment `DATAHUB_TOKEN` and use your tenant's Cloud GMS endpoint.

---

## Approach 2 — Quality Trend → `DatasetProperties` Custom Properties

Extend the Approach 1 loop to also push the 7-day average pass rate into `DatasetProperties.customProperties`. (`DataQualityInfoClass` does not exist in `acryl-datahub`; `customProperties` is the correct target.)

### Field mapping

| OpenDQV source | DataHub field |
|---|---|
| `quality-trend` avg pass rate × 100 | `DatasetProperties.customProperties["quality_score_7d"]` |
| Top failing rule name | `DatasetProperties.customProperties["top_failing_rule"]` |

### Python snippet

```python
# pip install acryl-datahub requests
# Extends Approach 1 — add inside the contract loop, after emitting Ownership

trend_resp = requests.get(
    f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}/quality-trend?days=7",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
)
if trend_resp.status_code == 200:
    trend = trend_resp.json()
    rates = [day["pass_rate"] for day in trend if "pass_rate" in day]
    avg_score = round((sum(rates) / len(rates)) * 100, 1) if rates else None
    top_failing = trend[0].get("top_failing_rule") if trend else None

    if avg_score is not None:
        # DataQualityInfoClass does not exist in acryl-datahub; write into customProperties
        custom_props["quality_score_7d"] = str(avg_score)
        custom_props["top_failing_rule"] = top_failing or ""
```

---

## Approach 3 — Webhook-Driven `validation.failed` → Real-Time Tagging

Rather than polling, register an OpenDQV webhook and have DataHub updated the moment a validation failure is detected.

### Architecture

```
OpenDQV engine
     │
     │  POST /webhooks/datahub (validation.failed event)
     ▼
FastAPI receiver (this script)
     │
     │  emit_mcp(GlobalTagsClass + DataQualityInfoClass)
     ▼
DataHub GMS
     │
     └── Dataset entity gets tag "OpenDQV:validation_failed"
         and updated quality score
```

### Webhook payload → DataHub aspect mapping

| Webhook field | DataHub action |
|---|---|
| `contract` (name) | Resolve dataset URN |
| `failed_count` | Tag `OpenDQV:validation_failed` if > 0 |
| `pass_rate` | Update `DataQualityInfo.score` (× 100) |
| `top_failing_rule` | `DataQualityInfo.customProperties["top_failing_rule"]` |
| `trace_id` | `DatasetProperties.customProperties["last_trace_id"]` |

### FastAPI receiver snippet

```python
# pip install acryl-datahub fastapi uvicorn requests
from fastapi import FastAPI, Request
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass
from datahub.emitter.mce_builder import make_dataset_urn, make_tag_urn
import time

app = FastAPI()
emitter = DatahubRestEmitter(gms_server="http://datahub-gms:8080")

@app.post("/webhooks/datahub")
async def handle_validation_event(request: Request):
    payload = await request.json()
    if payload.get("event") != "validation.failed":
        return {"status": "ignored"}

    contract_name = payload["contract"]
    urn = make_dataset_urn(platform="opendqv", name=contract_name, env="PROD")

    # Add tag
    tag_urn = make_tag_urn("OpenDQV:validation_failed")
    emitter.emit_mcp(
        urn=urn,
        aspect=GlobalTagsClass(tags=[TagAssociationClass(tag=tag_urn)]),
    )

    print(f"Tagged {contract_name} with validation_failed (trace={payload.get('trace_id')})")
    return {"status": "ok"}
```

### Register the webhook

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENDQV_TOKEN" \
  -d '{
    "url": "http://your-receiver:9000/webhooks/datahub",
    "events": ["validation.failed"],
    "secret": "your-webhook-secret"
  }'
```

---

## Approach 4 — OpenDQV Rules → DataHub Assertions

Map OpenDQV contract rules to DataHub Assertion entities for richer governance visibility.

### Rule type → DataHub assertion type

| OpenDQV rule type | DataHub assertion type | Notes |
|---|---|---|
| `not_empty` | `DATASET_COLUMN` / `NOT_NULL` | Direct mapping |
| `unique` | `DATASET_COLUMN` / `UNIQUENESS` | Direct mapping |
| `regex` | `DATASET_COLUMN` / `FIELD_VALUES` | Pattern passed as param |
| `range` | `DATASET_COLUMN` / `FIELD_VALUES` | `min_value` / `max_value` params |
| `min` | `DATASET_COLUMN` / `FIELD_VALUES` | `min_value` only |
| `max` | `DATASET_COLUMN` / `FIELD_VALUES` | `max_value` only |
| `min_length` | *not supported* | No DataHub native equivalent |
| `max_length` | *not supported* | No DataHub native equivalent |
| `lookup` | *not supported* | Requires cross-dataset join context |
| `custom` | *not supported* | No DataHub assertion equivalent |

> **Note:** Assertion URNs must be pre-registered in DataHub before emitting results against them. The tag-based approach in Approach 3 is simpler and requires no pre-registration. Use Assertions only if your team actively monitors them in the DataHub UI.

---

## Approach 5 — Incremental Sync with `contract_hash`

Avoid pushing unchanged contracts by persisting the last-seen hash in a local state file.

```python
# pip install acryl-datahub requests
import json, os, requests

STATE_FILE = ".datahub_sync_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

state = load_state()
contracts = requests.get(
    "http://localhost:8000/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

synced, skipped = 0, 0
for contract in contracts:
    name = contract["name"]
    current_hash = contract.get("contract_hash", "")
    if state.get(name) == current_hash:
        skipped += 1
        continue
    # ... emit to DataHub (Approach 1 logic) ...
    state[name] = current_hash
    synced += 1

save_state(state)
print(f"Synced {synced}, skipped {skipped} (hash unchanged)")
```

---

## Approach 6 — Federation-Aware Sync

When OpenDQV runs in a federated topology, check node availability before pulling contracts.

```python
# pip install acryl-datahub requests
import requests

OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"

fed_status = requests.get(
    f"{OPENDQV_URL}/federation/status",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for node in fed_status.get("nodes", []):
    if node.get("status") != "healthy":
        print(f"Skipping node {node['id']}: status={node.get('status')}")
        continue
    node_url = node["url"]
    contracts = requests.get(
        f"{node_url}/api/v1/registry",
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
    ).json()
    # ... emit to DataHub (Approach 1 logic) ...
    print(f"Synced {len(contracts)} contracts from node {node['id']}")
```

---

## Limitations

| Limitation | Detail |
|---|---|
| No lineage graph | OpenDQV does not know upstream/downstream models; DataHub lineage must be populated separately |
| Aggregated trend only | `/quality-trend` returns daily aggregates, not per-record results |
| No deletion propagation | Deleting a contract in OpenDQV does not remove the DataHub entity |
| `DataQualityInfo` aspect | `DataQualityInfoClass` does not exist in `acryl-datahub`; quality score is always written into `DatasetProperties.customProperties` |
| Webhook best-effort | OpenDQV webhooks are fire-and-forget; transient receiver downtime means missed events |
| GMS vs Cloud endpoint | Self-hosted uses `http://datahub-gms:8080`; DataHub Cloud requires a tenant-specific URL and API token |
| Assertion pre-registration | Approach 4 requires assertion URNs to be created in DataHub before results can be emitted |

---

## `asset_id` Conventions for DataHub

OpenDQV's `asset_id` field should be set to the DataHub `DatasetUrn` of the asset the contract governs.

**DataHub URN format:**

```
urn:li:dataset:(urn:li:dataPlatform:{platform},{database}.{schema}.{table},{env})
```

### Examples

| Platform | `asset_id` value |
|---|---|
| Snowflake | `urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.customers,PROD)` |
| BigQuery | `urn:li:dataset:(urn:li:dataPlatform:bigquery,my_project.my_dataset.orders,PROD)` |
| OpenDQV platform (no upstream) | `urn:li:dataset:(urn:li:dataPlatform:opendqv,customer,PROD)` |

### Contract YAML snippet

```yaml
contract:
  name: customer
  asset_id: "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.public.customers,PROD)"
  version: "1.0"
  owner: "data-governance"
  owner_team: "governance"
  owner_email: "governance@example.com"
  rules:
    - name: customer_id_not_null
      type: not_empty
      field: customer_id
```

When `asset_id` is a valid DataHub URN, Approach 1 uses it directly rather than constructing a synthetic `opendqv` platform URN — this creates a link to an existing DataHub entity rather than a new one.

---

## Recommended Integration Path

| Phase | Action |
|---|---|
| **Now** | Run Approach 1 as a cron job (daily) to push contract metadata and ownership to DataHub |
| **Now** | Set `asset_id` on contracts to full DataHub URNs for your warehouse platform |
| **Q2 2026** | Add Approach 5 (hash-based incremental sync) to reduce API call volume |
| **Q2 2026** | Deploy Approach 3 (webhook receiver) for real-time failure tagging |
| **Q3 2026** | Add Approach 2 (quality trend score) to surface pass rates in DataHub dashboards |
| **Q4 2026** | Evaluate Approach 4 (Assertions) if your team uses DataHub's assertion monitor UI |

---

## Roadmap

The following capabilities are **not yet implemented** and are planned for a future release.

- **Native ingestion source recipe** — a DataHub `Source` plugin (`datahub ingest -c opendqv_recipe.yml`) so catalog admins can pull contracts without writing Python
- **Auto assertion URN on publish** — when a contract is published, OpenDQV pre-registers assertion URNs in DataHub automatically
- **DataHub → OpenDQV import** — bootstrap a contract from a DataHub `DatasetProperties` entity
- **Kafka emitter** — replace REST emission with a Kafka-based MCE emitter for high-volume installations

---

## See Also

- `docs/asset_id_uri_convention.md` — naming rules for `asset_id` URNs and how they map to catalog identifiers
- `docs/catalog_integration.md` — overview of catalog integration (DataHub and Atlan)
- `docs/connector_sdk_spec.md` — connector interface for production catalog integrations
