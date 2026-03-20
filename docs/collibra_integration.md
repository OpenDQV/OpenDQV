# Collibra Integration

> **API last verified:** Collibra REST API v2 — 2026-03-13.
> Snippets are examples; adjust asset type IDs to match your Collibra domain configuration.
> [Collibra REST API documentation](https://developer.collibra.com/rest/)

Collibra is the dominant enterprise data governance catalog. OpenDQV contracts surface as first-class governance artefacts in Collibra: validation rules map to Collibra Data Rules, contracts map to Data Sets, and quality trend data populates Data Quality Dimension scores. The `asset_id` field is the bridge — set it to the Collibra asset reference for the dataset being governed.

---

## `asset_id` Convention for Collibra

Collibra assets are identified by a qualified name in the format `{community}/{domain}/{asset_name}`. Use this as the `asset_id` in your OpenDQV contracts:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "Data Governance/Customer Domain/Customer Master"
  rules:
    - name: email_valid
      type: regex
      field: email
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
      severity: error
```

See [`index.md`](index.md) for the full cross-catalog `asset_id` URN table.

---

## Approach 1 — Sync Contract Metadata as Collibra Data Set Custom Attributes

Push OpenDQV contract metadata (version, owner, rule count, hash) to a Collibra Data Set asset using the `PATCH /rest/2.0/attributes` endpoint. Use `contract_hash` to skip unchanged contracts.

### Prerequisites

Create these custom attribute types in your Collibra tenant under **Settings > Attribute Types** (type `String`):

- `opendqv_contract_version`
- `opendqv_contract_hash`
- `opendqv_owner`
- `opendqv_rule_count`

### Sync script

```python
import requests

OPENDQV_URL = "http://opendqv:8000"
COLLIBRA_BASE = "https://<your-tenant>.collibra.com/rest/2.0"
COLLIBRA_AUTH = ("admin@example.com", "<collibra_password>")

def get_opendqv_contracts():
    return requests.get(
        f"{OPENDQV_URL}/api/v1/registry",
        headers={"Authorization": "Bearer <OPENDQV_TOKEN>"},
    ).json()

def find_collibra_asset(asset_id: str) -> str | None:
    """Look up Collibra asset UUID by qualifiedName (= asset_id)."""
    parts = asset_id.split("/")
    if len(parts) != 3:
        return None
    community, domain, name = parts
    resp = requests.get(
        f"{COLLIBRA_BASE}/assets",
        auth=COLLIBRA_AUTH,
        params={"name": name, "nameMatchMode": "EXACT", "domainName": domain},
    )
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None

def patch_attributes(asset_uuid: str, attributes: dict):
    for attr_type, value in attributes.items():
        requests.patch(
            f"{COLLIBRA_BASE}/attributes",
            auth=COLLIBRA_AUTH,
            json={
                "assetId": asset_uuid,
                "typeId": attr_type,  # replace with actual attribute type UUID
                "value": str(value),
            },
        )

contracts = get_opendqv_contracts()
for contract in contracts:
    asset_id = contract.get("asset_id", "")
    asset_uuid = find_collibra_asset(asset_id)
    if not asset_uuid:
        print(f"No Collibra asset found for: {asset_id} — skipping")
        continue

    patch_attributes(asset_uuid, {
        "opendqv_contract_version": contract.get("version", ""),
        "opendqv_contract_hash": contract.get("contract_hash", ""),
        "opendqv_owner": contract.get("owner", ""),
        "opendqv_rule_count": contract.get("rule_count", 0),
    })
    print(f"Updated Collibra asset: {asset_id}")
```

---

## Approach 2 — Quality Trend → Collibra Data Quality Dimension Score

OpenDQV's `GET /api/v1/contracts/{name}/quality-trend` returns daily pass rates. Push these to a Collibra Data Quality Dimension score to populate governance dashboards.

```python
import requests
from datetime import datetime

COLLIBRA_BASE = "https://<your-tenant>.collibra.com/rest/2.0"
COLLIBRA_AUTH = ("admin@example.com", "<collibra_password>")
OPENDQV_URL = "http://opendqv:8000"

def push_quality_score(asset_uuid: str, score: float, measured_at: str):
    """Push a quality score (0.0 – 1.0) to a Collibra Data Quality Dimension."""
    requests.post(
        f"{COLLIBRA_BASE}/dataQuality/metrics",
        auth=COLLIBRA_AUTH,
        json={
            "assetId": asset_uuid,
            "metricType": "COMPLETENESS",  # or ACCURACY, VALIDITY, etc.
            "value": score,
            "measuredAt": measured_at,
            "source": "OpenDQV",
        },
    )

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": "Bearer <OPENDQV_TOKEN>"},
).json()

for contract in contracts:
    asset_uuid = find_collibra_asset(contract.get("asset_id", ""))
    if not asset_uuid:
        continue

    trend = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}/quality-trend?days=1",
        headers={"Authorization": "Bearer <OPENDQV_TOKEN>"},
    ).json()

    if trend:
        latest = trend[-1]
        push_quality_score(
            asset_uuid=asset_uuid,
            score=latest["pass_rate"],
            measured_at=latest["date"] + "T00:00:00Z",
        )
        print(f"Pushed quality score for {contract['name']}: {latest['pass_rate']:.2%}")
```

---

## Approach 3 — Webhook → Collibra Workflow Trigger on `opendqv.validation.failed`

When OpenDQV rejects records, trigger a Collibra workflow (e.g. a data stewardship review task) via the Collibra workflow REST API.

```python
from fastapi import FastAPI, Request
import requests, os

app = FastAPI()
COLLIBRA_BASE = "https://<your-tenant>.collibra.com/rest/2.0"
COLLIBRA_AUTH = (os.getenv("COLLIBRA_USER"), os.getenv("COLLIBRA_PASSWORD"))

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    contract = payload["contract"]
    asset_id = payload.get("asset_id", contract)
    asset_uuid = find_collibra_asset(asset_id)

    if asset_uuid:
        # Trigger a Collibra workflow on the affected asset
        requests.post(
            f"{COLLIBRA_BASE}/workflows/instances",
            auth=COLLIBRA_AUTH,
            json={
                "workflowDefinitionId": os.getenv("COLLIBRA_WORKFLOW_ID"),
                "businessItemIds": [asset_uuid],
                "variables": [
                    {"name": "opendqv_failed_count", "value": payload["failed_count"]},
                    {"name": "opendqv_trace_id", "value": payload["trace_id"]},
                    {"name": "opendqv_contract", "value": contract},
                ],
            },
        )

    return {"status": "workflow_triggered", "asset": asset_id}
```

Set `COLLIBRA_WORKFLOW_ID` to the UUID of your Collibra workflow definition. The workflow is triggered per `opendqv.validation.failed` event — configure Collibra deduplication if the same asset fires frequently.

---

## Approach 4 — Rule-Level: One Collibra Data Rule per OpenDQV Rule

For fine-grained governance, create one Collibra Data Rule asset per OpenDQV validation rule. This gives data stewards rule-level visibility in Collibra.

```python
import requests

COLLIBRA_BASE = "https://<your-tenant>.collibra.com/rest/2.0"
COLLIBRA_AUTH = ("admin@example.com", "<collibra_password>")
DATA_RULE_TYPE_ID = "<collibra-data-rule-asset-type-uuid>"  # from Settings > Asset Types

def create_or_update_data_rule(asset_uuid: str, rule: dict, contract_name: str):
    rule_name = f"{contract_name}.{rule['name']}"

    # Check if it exists
    existing = requests.get(
        f"{COLLIBRA_BASE}/assets",
        auth=COLLIBRA_AUTH,
        params={"name": rule_name, "nameMatchMode": "EXACT"},
    ).json().get("results", [])

    payload = {
        "name": rule_name,
        "displayName": f"{rule['name']} ({rule.get('type', 'unknown')})",
        "typeId": DATA_RULE_TYPE_ID,
        "description": rule.get("error_message", ""),
        "domainId": "<target-domain-uuid>",
        "relations": [{"typeId": "<governed-by-relation-type>", "targetId": asset_uuid}],
    }

    if existing:
        requests.patch(
            f"{COLLIBRA_BASE}/assets/{existing[0]['id']}",
            auth=COLLIBRA_AUTH,
            json={"description": payload["description"]},
        )
    else:
        requests.post(f"{COLLIBRA_BASE}/assets", auth=COLLIBRA_AUTH, json=payload)

# Sync all rules for all contracts
contracts_detail = [
    requests.get(
        f"http://opendqv:8000/api/v1/contracts/{c['name']}",
        headers={"Authorization": "Bearer <OPENDQV_TOKEN>"},
    ).json()
    for c in requests.get(
        "http://opendqv:8000/api/v1/registry",
        headers={"Authorization": "Bearer <OPENDQV_TOKEN>"},
    ).json()
]

for contract in contracts_detail:
    asset_uuid = find_collibra_asset(contract.get("contract", {}).get("asset_id", ""))
    if not asset_uuid:
        continue
    for rule in contract.get("contract", {}).get("rules", []):
        create_or_update_data_rule(asset_uuid, rule, contract["contract"]["name"])
```

---

## Approach 5 — Incremental via `contract_hash` Read-Back

Before syncing, read back the `opendqv_contract_hash` attribute from Collibra and skip contracts whose hash has not changed:

```python
def get_stored_hash(asset_uuid: str) -> str | None:
    resp = requests.get(
        f"{COLLIBRA_BASE}/attributes",
        auth=COLLIBRA_AUTH,
        params={"assetId": asset_uuid, "typePublicId": "opendqv_contract_hash"},
    )
    results = resp.json().get("results", [])
    return results[0]["value"] if results else None

contracts = get_opendqv_contracts()
for contract in contracts:
    asset_uuid = find_collibra_asset(contract.get("asset_id", ""))
    if not asset_uuid:
        continue

    stored_hash = get_stored_hash(asset_uuid)
    if stored_hash == contract["contract_hash"]:
        continue  # unchanged — skip

    # ... sync metadata, quality score, rules
    print(f"Syncing updated contract: {contract['name']}")
```

---

## Approach 6 — Federation-Aware

Route Collibra sync requests to the correct OpenDQV instance based on `asset_id` community prefix:

```python
INSTANCES = {
    "Customer Domain": "http://opendqv-customer.internal:8000",
    "Finance Domain": "http://opendqv-finance.internal:8000",
}

def get_instance_for_asset(asset_id: str) -> str:
    """Infer OpenDQV instance from Collibra community in asset_id."""
    parts = asset_id.split("/")
    community = parts[0] if parts else ""
    for domain_prefix, url in INSTANCES.items():
        if community.startswith(domain_prefix):
            return url
    return "http://opendqv.internal:8000"  # default
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Asset type IDs | Collibra type UUIDs vary per tenant — the script above uses placeholder values; replace with IDs from your Collibra **Settings > Asset Types** |
| Authentication | The examples use basic auth; production Collibra deployments typically use OAuth 2.0 or API key — update `auth` accordingly |
| Workflow trigger | Collibra workflow IDs are tenant-specific; capture them from your Collibra admin before deploying |
| API rate limits | Collibra REST API may rate-limit bulk attribute patches; add retry with exponential backoff for large contract sets |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Set `asset_id` on contracts using `{community}/{domain}/{asset_name}` format |
| **Now** | Run the metadata sync script to push contract version, owner, and hash to Collibra |
| **Planned — based on community demand** | Schedule daily quality score push from OpenDQV trend → Collibra DQ Dimension |
| **Planned — based on community demand** | Add webhook → Collibra workflow trigger for `opendqv.validation.failed` events |
| **Planned — based on community demand** | Rule-level Data Rule sync for full stewardship visibility |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Collibra features including the packaged connector and native Data Quality Module integration.

---

## See Also

- [`catalog_integration.md`](catalog_integration.md) — catalog integration index
- [`datahub_integration.md`](datahub_integration.md) — DataHub integration
- [`atlan_integration.md`](atlan_integration.md) — Atlan integration
- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`index.md`](index.md) — cross-catalog `asset_id` URN table
