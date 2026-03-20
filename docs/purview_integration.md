# Microsoft Purview Integration

> **API last verified:** Microsoft Purview REST API, Microsoft Graph API — 2026-03-13.
> Purview is available on Azure; authentication uses Microsoft Entra ID (formerly Azure AD).
> [Purview REST API documentation](https://learn.microsoft.com/en-us/azure/purview/)

Microsoft Purview is the dominant governance catalog in Azure-native and regulated-industry environments — NHS, financial services, defence, and public sector. OpenDQV contracts surface in Purview as governed Data Assets: validation rules appear as custom attributes, quality scores populate Purview's health metrics, and validation failures can trigger Purview workflow alerts.

---

## `asset_id` Convention for Purview

Purview identifies assets using a fully qualified name (FQN) in the format `{data_source_type}://{qualified_path}`. Use this as `asset_id` in your OpenDQV contracts:

```yaml
contract:
  name: customer
  version: "1.0"
  # Snowflake table scanned by Purview
  asset_id: "mssql://acme-sql-server.database.windows.net/AnalyticsDB/dbo/Customers"
  # Azure Data Lake path
  # asset_id: "https://acmestorage.dfs.core.windows.net/analytics/customers/"
  # Azure SQL
  # asset_id: "mssql://acme.database.windows.net/analytics/public/customers"
```

The FQN format matches what Purview's built-in scanners assign to assets, so `asset_id` links directly to the existing Purview asset without creating a new one.

---

## Approach 1 — Sync Contract Metadata as Purview Custom Attributes

Push OpenDQV contract metadata (version, owner, rule count, hash) to a Purview Data Asset as custom attributes using the Apache Atlas REST API that Purview exposes.

### Prerequisites

Create these custom attribute definitions in your Purview account under **Management > Custom attributes**:

- `opendqv_contract_version` (type: `string`)
- `opendqv_contract_hash` (type: `string`)
- `opendqv_owner` (type: `string`)
- `opendqv_rule_count` (type: `int`)

### Authentication

Purview uses Microsoft Entra ID (OAuth 2.0 client credentials):

```python
import requests
import os

PURVIEW_ACCOUNT = os.getenv("PURVIEW_ACCOUNT")  # e.g. "acme-purview"
PURVIEW_ENDPOINT = f"https://{PURVIEW_ACCOUNT}.purview.azure.com"
TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")

def get_purview_token() -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "https://purview.azure.com/.default",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
```

### Sync script

```python
OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")


def find_purview_asset(token: str, asset_id: str) -> str | None:
    """Look up Purview entity GUID by qualifiedName (= asset_id)."""
    resp = requests.get(
        f"{PURVIEW_ENDPOINT}/catalog/api/atlas/v2/search/basic",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": asset_id, "limit": 1},
    )
    results = resp.json().get("value", [])
    return results[0]["id"] if results else None


def update_purview_attributes(token: str, guid: str, attributes: dict):
    resp = requests.put(
        f"{PURVIEW_ENDPOINT}/catalog/api/atlas/v2/entity/guid/{guid}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "entity": {
                "guid": guid,
                "attributes": attributes,
            }
        },
    )
    resp.raise_for_status()


token = get_purview_token()
contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    guid = find_purview_asset(token, asset_id)
    if not guid:
        print(f"No Purview asset found for: {asset_id} — skipping")
        continue

    update_purview_attributes(token, guid, {
        "opendqv_contract_version": contract.get("version", ""),
        "opendqv_contract_hash": contract.get("contract_hash", ""),
        "opendqv_owner": contract.get("owner", ""),
        "opendqv_rule_count": contract.get("rule_count", 0),
    })
    print(f"Updated Purview asset: {asset_id}")
```

---

## Approach 2 — Quality Score → Purview Data Health

OpenDQV's `GET /api/v1/contracts/{name}/quality-trend` returns daily pass rates. Push these to Purview's data health endpoint to populate quality metrics in the Purview governance portal.

```python
def push_quality_score(token: str, guid: str, score: float, measured_at: str):
    """Push a pass-rate score (0.0–1.0) as a Purview data health metric."""
    resp = requests.post(
        f"{PURVIEW_ENDPOINT}/catalog/api/atlas/v2/entity/guid/{guid}/businessmetadata",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "opendqv_quality": {
                "pass_rate": score,
                "measured_at": measured_at,
                "source": "OpenDQV",
            }
        },
    )
    resp.raise_for_status()


token = get_purview_token()
contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for contract in contracts:
    guid = find_purview_asset(token, contract.get("asset_id", ""))
    if not guid:
        continue

    trend = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}/quality-trend?days=1",
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
    ).json()

    if trend:
        latest = trend[-1]
        push_quality_score(
            token=token,
            guid=guid,
            score=latest["pass_rate"],
            measured_at=latest["date"] + "T00:00:00Z",
        )
        print(f"Quality score pushed for {contract['name']}: {latest['pass_rate']:.2%}")
```

---

## Approach 3 — Webhook → Purview via Azure Event Hub

When OpenDQV fires `opendqv.validation.failed`, route the event to an Azure Event Hub. Purview can subscribe to Event Hub topics for custom event ingestion.

```python
from fastapi import FastAPI, Request
from azure.eventhub import EventHubProducerClient, EventData
import json, os

app = FastAPI()

EVENT_HUB_CONN = os.getenv("AZURE_EVENT_HUB_CONNECTION_STRING")
EVENT_HUB_NAME = os.getenv("AZURE_EVENT_HUB_NAME", "opendqv-events")

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    producer = EventHubProducerClient.from_connection_string(
        EVENT_HUB_CONN, eventhub_name=EVENT_HUB_NAME
    )
    async with producer:
        batch = await producer.create_batch()
        batch.add(EventData(json.dumps({
            "source": "OpenDQV",
            "event": "opendqv.validation.failed",
            "contract": payload["contract"],
            "asset_id": payload.get("asset_id"),
            "failed_count": payload["failed_count"],
            "trace_id": payload["trace_id"],
            "timestamp": payload["timestamp"],
        })))
        await producer.send_batch(batch)

    return {"status": "published"}
```

Register the webhook in OpenDQV:

```bash
curl -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-service.azurewebsites.net/webhooks/opendqv",
    "events": ["opendqv.validation.failed"],
    "secret": "<HMAC_SECRET>"
  }'
```

---

## Approach 4 — Incremental via `contract_hash`

Read back the `opendqv_contract_hash` attribute from Purview before syncing to skip unchanged contracts:

```python
def get_stored_hash(token: str, guid: str) -> str | None:
    resp = requests.get(
        f"{PURVIEW_ENDPOINT}/catalog/api/atlas/v2/entity/guid/{guid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    attrs = resp.json().get("entity", {}).get("attributes", {})
    return attrs.get("opendqv_contract_hash")


token = get_purview_token()
for contract in requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json():
    guid = find_purview_asset(token, contract.get("asset_id", ""))
    if not guid:
        continue
    if get_stored_hash(token, guid) == contract["contract_hash"]:
        continue  # unchanged — skip
    # ... sync metadata
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Purview API version | The Atlas v2 API endpoints used here are stable; the newer Microsoft Graph-based Purview API (`graph.microsoft.com/beta/security/informationProtection`) is separate and covers sensitivity labels, not data assets |
| Custom attribute prerequisites | Custom attributes must be created in Purview before the sync script can write them — the script will return `400` if they don't exist |
| Search accuracy | `find_purview_asset` uses a full-text search on `asset_id`; if your FQN contains special characters, URL-encode them before passing to the search query |
| Managed vs self-hosted Purview | All endpoints above target the cloud-hosted Purview service; on-premises Data Map deployments use a different base URL |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Set `asset_id` on contracts using the Purview FQN format |
| **Now** | Create custom attributes in Purview; run the metadata sync script |
| **Planned — based on community demand** | Schedule daily quality score push from OpenDQV trend → Purview health metrics |
| **Planned — based on community demand** | Add webhook → Event Hub pipeline for `opendqv.validation.failed` alerting |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Purview features including a packaged Azure Function for scheduled contract sync and native Purview Data Quality integration when the GA API stabilises.

---

## See Also

- [`catalog_integration.md`](catalog_integration.md) — catalog integration index
- [`collibra_integration.md`](collibra_integration.md) — Collibra integration (similar governance pattern)
- [`datahub_integration.md`](datahub_integration.md) — DataHub integration
- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`index.md`](index.md) — cross-catalog `asset_id` URN table
