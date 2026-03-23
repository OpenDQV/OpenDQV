# Marmot Integration Guide

> **Marmot version:** This guide targets Marmot v0.x (MIT, https://github.com/marmotdata/marmot).
> Verify endpoint paths against your Marmot instance's Swagger UI (`/swagger/index.html`) before deploying.

OpenDQV and Marmot serve complementary roles: OpenDQV validates data at the write boundary and governs contracts; Marmot provides data discovery, lineage visualisation, and asset cataloging across your estate. Neither replaces the other — together they close the governance loop from "what data do we have?" to "is it valid before it lands?"

---

## Design Goals

1. **Contract-first** — OpenDQV contracts are the source of truth; Marmot reflects their state, not the reverse
2. **Incremental** — use `contract_hash` to skip unchanged contracts and avoid redundant API calls
3. **Non-invasive** — no changes to OpenDQV internals; integration lives in a thin sync script
4. **MCP-native** — both tools expose MCP servers; AI agents can query both in a single workflow

---

## What OpenDQV Is NOT

| OpenDQV does | OpenDQV does NOT |
|---|---|
| Validate records against contract rules | Replace Marmot's lineage graph |
| Publish contract metadata (owner, version, hash) | Crawl your warehouse schemas |
| Emit quality trend scores (7-day pass rate) | Manage Marmot users or teams |
| Fire webhooks on validation failure | Track column-level lineage across models |
| Expose `asset_id` for catalog linkage | Delete or archive Marmot assets automatically |

---

## Approach 1 — Contract Metadata → Marmot Asset

Push every active OpenDQV contract into Marmot as an asset, carrying contract metadata as asset properties. Set `asset_id` in the OpenDQV contract YAML to the Marmot asset identifier.

### `asset_id` convention for Marmot

```
marmot://{host}/assets/{marmot-asset-id}
```

**Example:**
```yaml
contract:
  name: customer_master
  asset_id: "marmot://marmot.internal/assets/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  version: "1.0"
  owner: "data-governance"
```

### Field mapping

| OpenDQV field | Marmot field |
|---|---|
| `name` | Asset name / `metadata.name` |
| `description` | Asset description |
| `version` | Custom metadata `contract_version` |
| `contract_hash` | Custom metadata `contract_hash` (used for skip logic) |
| `status` | Custom metadata `contract_status`; `archived` contracts skipped |
| `owner` | Custom metadata `contract_owner` |
| `owner_email` | Custom metadata `contract_owner_email` |
| `domain` | Marmot asset type / service tag |

### Python snippet

```python
# pip install requests
import requests

OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
MARMOT_URL = "http://marmot.internal"
MARMOT_TOKEN = "<MARMOT_API_TOKEN>"  # Bearer token from Marmot settings

headers_dqv = {"Authorization": f"Bearer {OPENDQV_TOKEN}"}
headers_marmot = {
    "Authorization": f"Bearer {MARMOT_TOKEN}",
    "Content-Type": "application/json",
}

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers=headers_dqv,
).json()

for contract in contracts:
    if contract.get("status") == "archived":
        print(f"Skipping archived contract: {contract['name']}")
        continue

    asset_payload = {
        "name": contract["name"],
        "description": contract.get("description", ""),
        "type": "dataset",
        "metadata": {
            "contract_version":     contract.get("version", ""),
            "contract_hash":        contract.get("contract_hash", ""),
            "contract_status":      contract.get("status", ""),
            "contract_owner":       contract.get("owner", ""),
            "contract_owner_email": contract.get("owner_email", ""),
        },
        "tags": ["opendqv", f"status:{contract.get('status', 'unknown')}"],
    }

    # Upsert: POST creates; PATCH updates if asset already exists by name
    resp = requests.post(
        f"{MARMOT_URL}/api/v1/assets",
        json=asset_payload,
        headers=headers_marmot,
    )
    if resp.status_code in (200, 201):
        print(f"Synced contract '{contract['name']}' → Marmot asset")
    else:
        print(f"Error syncing '{contract['name']}': {resp.status_code} {resp.text}")
```

> **Note:** Marmot's asset upsert behaviour (create vs update on duplicate name) depends on your Marmot version. Check the Swagger UI at `{MARMOT_URL}/swagger/index.html` for the current assets endpoint contract.

---

## Approach 2 — Webhook-Driven Quality Tagging

Register an OpenDQV webhook so Marmot assets are tagged in real time when a validation failure is detected — no polling required.

### Architecture

```
OpenDQV engine
     │
     │  POST /webhooks/marmot  (opendqv.validation.failed event)
     ▼
FastAPI receiver (this script)
     │
     │  PATCH /api/v1/assets/{id}  — add tag "opendqv:validation_failed"
     ▼
Marmot
     └── Asset tagged; visible in lineage and search views
```

### FastAPI receiver snippet

```python
# pip install fastapi uvicorn requests
from fastapi import FastAPI, Request
import requests

app = FastAPI()

MARMOT_URL = "http://marmot.internal"
MARMOT_TOKEN = "<MARMOT_API_TOKEN>"
OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"

def get_marmot_asset_id(contract_name: str) -> str | None:
    """Resolve Marmot asset ID from OpenDQV contract asset_id field."""
    resp = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract_name}",
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
    )
    if resp.status_code != 200:
        return None
    asset_id = resp.json().get("asset_id", "")
    # asset_id format: marmot://host/assets/{uuid}
    if asset_id.startswith("marmot://"):
        return asset_id.split("/assets/")[-1]
    return None

@app.post("/webhooks/marmot")
async def handle_validation_event(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    contract_name = payload["contract"]
    marmot_id = get_marmot_asset_id(contract_name)
    if not marmot_id:
        return {"status": "no_asset_id", "contract": contract_name}

    resp = requests.patch(
        f"{MARMOT_URL}/api/v1/assets/{marmot_id}",
        json={"tags": ["opendqv:validation_failed"]},
        headers={
            "Authorization": f"Bearer {MARMOT_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    print(
        f"Tagged Marmot asset {marmot_id} for contract '{contract_name}'"
        f" (trace={payload.get('trace_id')}, status={resp.status_code})"
    )
    return {"status": "ok"}
```

### Register the webhook

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENDQV_TOKEN" \
  -d '{
    "url": "http://your-receiver:9000/webhooks/marmot",
    "events": ["opendqv.validation.failed"],
    "secret": "your-webhook-secret"
  }'
```

---

## Approach 3 — Contract Rules → Marmot Asset Rules

Marmot has a native asset rules system (`/api/v1/assetrules`). Syncing OpenDQV contract rules into Marmot as asset rules gives Marmot users governance visibility into what constraints apply to each catalogued asset without leaving Marmot.

### Rule type mapping

| OpenDQV rule type | Marmot asset rule description |
|---|---|
| `not_empty` | Field `{field}` must not be null or empty |
| `unique` | Field `{field}` must be unique |
| `regex` | Field `{field}` must match pattern `{pattern}` |
| `range` | Field `{field}` must be between `{min_value}` and `{max_value}` |
| `allowed_values` | Field `{field}` must be one of `{values}` |
| `compare` | Field `{field}` must satisfy `{compare_op}` `{compare_to}` |

### Python snippet

```python
# pip install requests
import requests

OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
MARMOT_URL = "http://marmot.internal"
MARMOT_TOKEN = "<MARMOT_API_TOKEN>"

def sync_rules_to_marmot(contract_name: str, marmot_asset_id: str):
    contract = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract_name}",
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
    ).json()

    for rule in contract.get("rules", []):
        rule_payload = {
            "asset_id": marmot_asset_id,
            "name": rule.get("name", ""),
            "description": (
                f"OpenDQV rule: {rule.get('type')} on field '{rule.get('field')}'. "
                f"{rule.get('error_message', '')}"
            ),
            "metadata": {
                "opendqv_rule_type":  rule.get("type", ""),
                "opendqv_field":      rule.get("field", ""),
                "opendqv_severity":   rule.get("severity", "error"),
                "opendqv_contract":   contract_name,
            },
        }
        resp = requests.post(
            f"{MARMOT_URL}/api/v1/assetrules",
            json=rule_payload,
            headers={
                "Authorization": f"Bearer {MARMOT_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        print(f"  Rule '{rule.get('name')}': {resp.status_code}")
```

---

## Approach 4 — MCP Bridge (AI-Native Governance)

Both OpenDQV and Marmot expose MCP servers. An AI agent (Claude Desktop, Cursor, or any MCP-compatible tool) can call both in a single workflow — no custom integration code required.

### Example agentic workflow

```
User: "Which customer data assets have active validation failures?"

Agent calls:
  1. marmot.list_assets(type="dataset", tag="customer")
     → returns list of customer assets with their IDs

  2. opendqv.list_contracts()
     → returns active contracts

  3. For each contract with asset_id matching a Marmot asset:
     opendqv.get_contract(name="customer_master")
     → returns contract status, rule count, last_validated

  4. opendqv.validate_record(contract="customer_master", record={...})
     → validates a sample record, returns pass/fail

Agent responds: "3 customer assets have contracts. 'customer_master' had 12
validation failures in the last 24 hours. Top failing rule: email_format."
```

### MCP server configuration (Claude Desktop example)

```json
{
  "mcpServers": {
    "opendqv": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {
        "OPENDQV_URL": "http://localhost:8000",
        "OPENDQV_TOKEN": "<OPENDQV_TOKEN>"
      }
    },
    "marmot": {
      "command": "<marmot-mcp-command>",
      "env": {
        "MARMOT_URL": "http://marmot.internal",
        "MARMOT_TOKEN": "<MARMOT_TOKEN>"
      }
    }
  }
}
```

> See Marmot's MCP documentation for the correct server command and configuration.
> See OpenDQV's MCP guide (`docs/mcp.md`) for full tool listing.

This is the **governance-first** integration pattern: no scheduled jobs, no ETL pipelines — AI agents compose both tools on demand.

---

## Approach 5 — Contract as Lineage Node

Most data lineage tools draw the graph *after* data moves:

```
Source system → dbt model → Warehouse table → Dashboard
```

OpenDQV enforces at the **write boundary** — at every arrow in that graph. This approach makes OpenDQV contracts visible inside Marmot's lineage view, so governance teams can see not just where data flows but **what rules it had to satisfy to cross each boundary**.

### What it looks like

```
Postgres (source)
    │
    │  every INSERT validated against customer_master v1.2 ✓
    ▼
[OpenDQV contract: customer_master]   ← visible in Marmot lineage
    │  contract_hash: a3f9...  status: active
    │  12 rules  last_validated: 2026-03-23T14:32:00Z
    ▼
Snowflake (analytics.public.customers)
    │
    ▼
Dashboard
```

Each contract node carries: contract name, version, hash, status, rule count, and last validation timestamp. Any data that reached the warehouse passed through that checkpoint.

### How it works

No new OpenDQV internals are needed. The pattern uses `asset_id`, the Marmot assets API, and a `opendqv.validation.passed` webhook to keep the lineage node current.

**Step 1 — Register the contract as a Marmot lineage asset**

```python
# pip install requests
import requests

OPENDQV_URL = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
MARMOT_URL = "http://marmot.internal"
MARMOT_TOKEN = "<MARMOT_API_TOKEN>"

contract = requests.get(
    f"{OPENDQV_URL}/api/v1/contracts/customer_master",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

lineage_node = {
    "name": f"OpenDQV: {contract['name']}",
    "type": "validation_contract",
    "description": (
        f"OpenDQV contract enforced at the write boundary for this asset. "
        f"Version {contract.get('version')} · {len(contract.get('rules', []))} rules · "
        f"Status: {contract.get('status')}"
    ),
    "metadata": {
        "contract_name":    contract["name"],
        "contract_version": contract.get("version", ""),
        "contract_hash":    contract.get("contract_hash", ""),
        "contract_status":  contract.get("status", ""),
        "rule_count":       str(len(contract.get("rules", []))),
        "opendqv_url":      f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}",
    },
    "tags": ["opendqv", "write-time-enforcement", f"v{contract.get('version', 'unknown')}"],
}

resp = requests.post(
    f"{MARMOT_URL}/api/v1/assets",
    json=lineage_node,
    headers={
        "Authorization": f"Bearer {MARMOT_TOKEN}",
        "Content-Type": "application/json",
    },
)
print(f"Registered lineage node: {resp.status_code}")
```

**Step 2 — Link the contract node between source and destination assets in Marmot lineage**

Once the contract node exists as a Marmot asset, use Marmot's lineage API to insert it between the source and destination assets:

```
source_asset → contract_node → destination_asset
```

Refer to Marmot's lineage endpoint (`/api/v1/lineage`) in your instance's Swagger UI for the exact edge creation payload.

**Step 3 — Keep the node current with a webhook**

Register an OpenDQV webhook for `opendqv.contract.updated` to refresh the Marmot lineage node whenever the contract version or hash changes:

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENDQV_TOKEN" \
  -d '{
    "url": "http://your-receiver:9000/webhooks/marmot-lineage",
    "events": ["opendqv.contract.updated", "opendqv.contract.activated"],
    "secret": "your-webhook-secret"
  }'
```

### Why this matters

| Without Approach 5 | With Approach 5 |
|---|---|
| Marmot lineage shows data movement | Marmot lineage shows data movement **and** the validation checkpoint it passed through |
| "This data came from Postgres" | "This data came from Postgres and satisfied `customer_master` v1.2 (12 rules, hash a3f9...)" |
| Governance is implicit | Governance is **visible and auditable** in the lineage graph |
| Contract version unknown at query time | Contract version pinned to every lineage edge |

This is the pattern that closes the loop between write-time enforcement (OpenDQV) and discovery/lineage (Marmot) — making the bouncer visible at the door in every diagram your governance team draws.

---

## Incremental Sync with `contract_hash`

Avoid pushing unchanged contracts by persisting the last-seen hash in a local state file. Add this wrapper around any of the approaches above.

```python
import json, os

STATE_FILE = ".marmot_sync_state.json"

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

state = load_state()
contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

synced, skipped = 0, 0
for contract in contracts:
    name = contract["name"]
    current_hash = contract.get("contract_hash", "")
    if state.get(name) == current_hash:
        skipped += 1
        continue
    # ... sync to Marmot (Approach 1 logic) ...
    state[name] = current_hash
    synced += 1

save_state(state)
print(f"Synced {synced}, skipped {skipped} (hash unchanged)")
```

---

## Limitations

| Limitation | Detail |
|---|---|
| No lineage graph | OpenDQV does not track upstream/downstream models; Marmot lineage must be populated separately |
| Aggregated trend only | `/quality-trend` returns daily aggregates, not per-record results |
| No deletion propagation | Archiving a contract in OpenDQV does not remove the Marmot asset |
| Webhook best-effort | OpenDQV webhooks are fire-and-forget; transient receiver downtime means missed events |
| Marmot API stability | Marmot is pre-1.0; verify endpoint paths against your instance's Swagger UI before deploying |
| No Python SDK | Marmot has no official Python client; use `requests` against the REST API directly |

---

## `asset_id` Convention for Marmot

Set `asset_id` in the OpenDQV contract YAML to the Marmot asset URI:

```
marmot://{host}/assets/{marmot-asset-uuid}
```

**Contract YAML example:**

```yaml
contract:
  name: customer_master
  version: "1.0"
  asset_id: "marmot://marmot.internal/assets/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  owner: "data-governance"
  owner_email: "governance@example.com"
  rules:
    - name: customer_id_not_null
      type: not_empty
      field: customer_id
      error_message: "Customer ID is required (GDPR Article 30 controller identity)"
```

---

## Recommended Integration Path

| Phase | Action |
|---|---|
| **Now** | Run Approach 1 as a cron job (daily) to push contract metadata to Marmot |
| **Now** | Set `asset_id` on contracts to Marmot asset URIs for linked assets |
| **Planned** | Add incremental sync (hash-based) to reduce API call volume |
| **Planned** | Deploy Approach 2 (webhook receiver) for real-time failure tagging |
| **Planned** | Configure Approach 4 (MCP) if your team uses Claude Desktop or Cursor |

---

## See Also

- `docs/asset_id_uri_convention.md` — naming rules for `asset_id` URNs
- `docs/catalog_integration.md` — catalog integration index
- `docs/mcp.md` — OpenDQV MCP server tool listing
- Marmot REST API: `{MARMOT_URL}/swagger/index.html`
- Marmot GitHub: https://github.com/marmotdata/marmot
