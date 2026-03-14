# Atlan Integration Design

> **API last verified:** `pyatlan v9.2.2` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [Check for updates](https://pypi.org/project/pyatlan/)

OpenDQV and Atlan serve complementary roles: OpenDQV is the validation and contract governance layer, while Atlan is the active metadata platform that surfaces data discovery, trust scores, and lineage to data consumers. This guide shows six approaches to connecting them, from a simple one-off metadata push to a real-time webhook-driven pipeline with column-level rule visibility.

---

## Design Goals

1. **SDK-first** — use `pyatlan` for all writes; the raw REST API is a last resort
2. **Custom metadata namespace** — all OpenDQV fields live under an `OpenDQV` namespace in Atlan, keeping them separate from native Atlan attributes
3. **Incremental** — read back current values from Atlan before writing to skip unchanged contracts
4. **Complementary roles** — OpenDQV owns contract authoring and validation; Atlan owns propagation, access control, and consumer-facing discoverability

---

## What OpenDQV Is NOT

| OpenDQV does | OpenDQV does NOT |
|---|---|
| Validate records against contract rules | Replace Atlan's metadata propagation policies |
| Publish contract metadata (owner, version, hash) | Manage Atlan access control or personas |
| Emit quality trend scores (pass rate) | Trigger Atlan Workflows or approval chains |
| Fire webhooks on validation failure | Crawl warehouse schemas or derive lineage |
| Expose `asset_id` for catalog linkage | Delete or deprecate Atlan assets automatically |

---

## Pre-requisite — Create the `OpenDQV` Custom Metadata Namespace

Before any sync can run, create the `OpenDQV` namespace in the Atlan Admin panel under **Admin > Custom Metadata > Add Custom Metadata**.

**Base namespace attributes (Approach 1):**

| Attribute name | Atlan type | Description |
|---|---|---|
| `contract_name` | `String` | OpenDQV contract identifier |
| `contract_version` | `String` | Semantic version string |
| `contract_status` | `String` | `active`, `draft`, or `archived` |
| `contract_hash` | `String` | SHA-256 hash of the contract YAML |
| `rule_count` | `Integer` | Total number of active rules |
| `owner_team` | `String` | Owning team name |
| `owner_email` | `String` | Owner contact email |
| `opendqv_description` | `String` | Contract description text |

---

## Approach 1 — Contract Metadata → Atlan Table Custom Metadata

Push every active OpenDQV contract into the Atlan `Table` entity that the contract governs, using the `OpenDQV` custom metadata namespace.

### Field mapping

| OpenDQV field | Atlan `OpenDQV` attribute |
|---|---|
| `name` | `contract_name` |
| `version` | `contract_version` |
| `status` | `contract_status` |
| `contract_hash` | `contract_hash` |
| `rule_count` | `rule_count` |
| `owner_team` | `owner_team` |
| `owner_email` | `owner_email` |
| `description` | `opendqv_description` |

### Python snippet

```python
# pip install pyatlan requests
import requests
from pyatlan.client import AtlanClient
from pyatlan.model.assets import Table
from pyatlan.model.custom_metadata import CustomMetadataDict

OPENDQV_URL   = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
ATLAN_URL     = "https://<your-tenant>.atlan.com"
ATLAN_TOKEN   = "<ATLAN_API_KEY>"

client = AtlanClient(base_url=ATLAN_URL, api_key=ATLAN_TOKEN)

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    if not asset_id:
        print(f"Skipping {contract['name']}: no asset_id set")
        continue

    # asset_id must be the Atlan qualifiedName for the table
    qualified_name = asset_id

    try:
        table = Table.updater(qualified_name=qualified_name, name=contract["name"])
        cm = CustomMetadataDict(client=client, name="OpenDQV")
        cm["contract_name"]       = contract["name"]
        cm["contract_version"]    = contract.get("version", "")
        cm["contract_status"]     = contract.get("status", "")
        cm["contract_hash"]       = contract.get("contract_hash", "")
        cm["rule_count"]          = contract.get("rule_count", 0)
        cm["owner_team"]          = contract.get("owner_team", "")
        cm["owner_email"]         = contract.get("owner_email", "")
        cm["opendqv_description"] = contract.get("description", "")
        table.set_custom_metadata(client=client, custom_metadata=cm)
        client.asset.save(table)
        print(f"Synced {contract['name']} → {qualified_name}")
    except Exception as e:
        print(f"Failed to sync {contract['name']}: {e}")
```

---

## Approach 2 — Quality Metrics → Atlan Custom Metadata

Extend the Approach 1 loop to also push the 7-day pass rate and top failing rule into Atlan.

### Additional attributes (add to `OpenDQV` namespace)

| Attribute name | Atlan type | Description |
|---|---|---|
| `quality_score_7d` | `Decimal` | Average pass rate × 100 over last 7 days |
| `quality_score_updated_at` | `String` | ISO-8601 timestamp of last score update |
| `top_failing_rule` | `String` | Rule name with highest failure rate in period |

### Python snippet

```python
# pip install pyatlan requests
# Extends Approach 1 — add inside the contract loop, after saving base metadata
from datetime import datetime, timezone

trend_resp = requests.get(
    f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}/quality-trend?days=7",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
)
if trend_resp.status_code == 200:
    trend = trend_resp.json()
    rates = [day["pass_rate"] for day in trend if "pass_rate" in day]
    avg_score = round((sum(rates) / len(rates)) * 100, 2) if rates else None
    top_failing = trend[0].get("top_failing_rule") if trend else None

    if avg_score is not None:
        table2 = Table.updater(qualified_name=qualified_name, name=contract["name"])
        cm2 = CustomMetadataDict(client=client, name="OpenDQV")
        cm2["quality_score_7d"]         = avg_score
        cm2["quality_score_updated_at"] = datetime.now(timezone.utc).isoformat()
        cm2["top_failing_rule"]         = top_failing or ""
        table2.set_custom_metadata(client=client, custom_metadata=cm2)
        client.asset.save(table2)
```

> **Atlan propagation policies:** Once `quality_score_7d` is set on a Table, Atlan propagation policies can automatically copy the score to downstream Column, Schema, and Database assets — but only if lineage exists between them. Configure propagation under **Admin > Propagation Policies** in Atlan; no code change is needed.

---

## Approach 3 — OpenDQV Webhook → Atlan Asset Custom Metadata Update

Register an OpenDQV webhook so Atlan is updated in real time when a validation failure occurs.

### Webhook payload → Atlan update mapping

| Webhook field | Atlan `OpenDQV` attribute |
|---|---|
| `contract` (name) | Resolve asset via `asset_id` / qualifiedName |
| `failed_count` | Used to decide whether to update (> 0 triggers write) |
| `pass_rate` | `quality_score_7d` (× 100) |
| `trace_id` | `last_failure_trace_id` |
| `timestamp` | `last_failure_at` |
| `top_failing_rule` | `last_failure_rule` |
| `failed_fields` (list) | `last_failure_fields` (comma-separated string) |

### Additional custom metadata attributes (add to `OpenDQV` namespace)

| Attribute name | Atlan type | Description |
|---|---|---|
| `last_failure_at` | `String` | ISO-8601 timestamp of most recent failure |
| `last_failure_rule` | `String` | Rule name that triggered the failure |
| `last_failure_fields` | `String` | Comma-separated list of failing fields |
| `last_failure_trace_id` | `String` | OpenDQV trace ID for the failed run |

### FastAPI receiver snippet

```python
# pip install pyatlan fastapi uvicorn requests
from fastapi import FastAPI, Request
from pyatlan.client import AtlanClient
from pyatlan.model.assets import Table
from pyatlan.model.fluent_search import FluentSearch
from pyatlan.model.fields.atlan_fields import CustomMetadataField
from pyatlan.model.custom_metadata import CustomMetadataDict
from datetime import datetime, timezone

app = FastAPI()
client = AtlanClient(
    base_url="https://<your-tenant>.atlan.com",
    api_key="<ATLAN_API_KEY>",
)

@app.post("/webhooks/atlan")
async def handle_validation_event(request: Request):
    payload = await request.json()
    if payload.get("event") != "validation.failed":
        return {"status": "ignored"}

    contract_name  = payload["contract"]
    pass_rate      = payload.get("pass_rate", 0)
    trace_id       = payload.get("trace_id", "")
    top_rule       = payload.get("top_failing_rule", "")
    failed_fields  = ", ".join(payload.get("failed_fields", []))
    ts             = datetime.now(timezone.utc).isoformat()

    # Find the asset by searching for matching OpenDQV contract_name metadata
    results = (
        FluentSearch()
        .where(CustomMetadataField(client=client, set_name="OpenDQV", attribute_name="contract_name").eq(contract_name))
        .execute(client)
    )

    for asset in results.current_page():
        table = Table.updater(qualified_name=asset.qualified_name, name=asset.name)
        cm3 = CustomMetadataDict(client=client, name="OpenDQV")
        cm3["quality_score_7d"]       = round(pass_rate * 100, 2)
        cm3["last_failure_at"]        = ts
        cm3["last_failure_rule"]      = top_rule
        cm3["last_failure_fields"]    = failed_fields
        cm3["last_failure_trace_id"]  = trace_id
        table.set_custom_metadata(client=client, custom_metadata=cm3)
        client.asset.save(table)
        print(f"Updated {asset.qualified_name} with failure data (trace={trace_id})")

    return {"status": "ok"}
```

### Register the webhook

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENDQV_TOKEN" \
  -d '{
    "url": "http://your-receiver:9000/webhooks/atlan",
    "events": ["validation.failed"],
    "secret": "your-webhook-secret"
  }'
```

---

## Approach 4 — Column-Level Rule Mapping → Atlan Column Custom Metadata

For teams that want column-level rule visibility in Atlan, push per-field rule metadata using a separate `OpenDQV_Rules` namespace on Atlan `Column` assets.

### `OpenDQV_Rules` namespace attributes

| Attribute name | Atlan type | Description |
|---|---|---|
| `rule_names` | `String` | Comma-separated list of rule names on this column |
| `rule_types` | `String` | Comma-separated list of rule types (`not_empty`, `regex`, …) |
| `has_error_rules` | `Boolean` | True if any rule has severity `error` |
| `has_warning_rules` | `Boolean` | True if any rule has severity `warning` |

### Column `qualifiedName` derivation convention

Atlan column qualifiedNames follow the table qualifiedName with the column name appended:

```
{table_qualified_name}/{column_name}
```

Example: `default/snowflake-prod/analytics/public/customers/customer_id`

### Python snippet

```python
# pip install pyatlan requests
from pyatlan.model.assets import Column
from pyatlan.model.custom_metadata import CustomMetadataDict

contract_detail = requests.get(
    f"{OPENDQV_URL}/api/v1/registry/{contract['name']}",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

# Group rules by field
rules_by_field = {}
for rule in contract_detail.get("rules", []):
    field = rule.get("field")
    if not field:
        continue
    rules_by_field.setdefault(field, []).append(rule)

for field_name, rules in rules_by_field.items():
    col_qualified_name = f"{qualified_name}/{field_name}"
    rule_names  = ", ".join(r["name"] for r in rules)
    rule_types  = ", ".join(r["type"] for r in rules)
    has_error   = any(r.get("severity") == "error"   for r in rules)
    has_warning = any(r.get("severity") == "warning" for r in rules)

    try:
        col = Column.updater(qualified_name=col_qualified_name, name=field_name)
        cm4 = CustomMetadataDict(client=client, name="OpenDQV_Rules")
        cm4["rule_names"]        = rule_names
        cm4["rule_types"]        = rule_types
        cm4["has_error_rules"]   = has_error
        cm4["has_warning_rules"] = has_warning
        col.set_custom_metadata(client=client, custom_metadata=cm4)
        client.asset.save(col)
    except Exception as e:
        print(f"Skipping column {col_qualified_name}: {e}")
```

> **Column assets must exist:** Atlan `Column.updater` requires the column to already exist as an Atlan asset (crawled from the warehouse). If the column has not been crawled, the call will fail. Run an Atlan crawl before executing this approach.

---

## Approach 5 — Incremental Sync via Read-Back from Atlan

Instead of a local state file, read the `contract_hash` currently stored in Atlan and skip the write if it matches the OpenDQV hash.

```python
# pip install pyatlan requests
# No local state file needed — Atlan is the source of state
from pyatlan.model.fluent_search import FluentSearch
from pyatlan.model.assets import Table

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    if not asset_id:
        continue

    # Read current hash from Atlan
    results = (
        FluentSearch()
        .where(Table.QUALIFIED_NAME.eq(asset_id))
        .include_on_results(Table.CUSTOM_METADATA_NAMES)
        .execute(client)
    )
    current_page = results.current_page()
    if current_page:
        existing = current_page[0]
        cm = existing.get_custom_metadata("OpenDQV") or {}
        if cm.get("contract_hash") == contract.get("contract_hash"):
            print(f"Skipping {contract['name']}: hash unchanged")
            continue

    # ... emit to Atlan (Approach 1 logic) ...
```

---

## Approach 6 — Atlan Propagation Policies for Quality Score

Atlan can automatically propagate the `quality_score_7d` attribute from a Table to its downstream Column, Schema, and Database assets using lineage-based propagation policies. This requires no additional code — configure it once in the Atlan Admin UI.

**Steps:**

1. Open **Admin > Propagation Policies** in your Atlan tenant
2. Create a new policy with source type `Table`, attribute `OpenDQV::quality_score_7d`
3. Set direction to **downstream** and enable propagation to `Column`, `Schema`, and `Database`
4. Save the policy — Atlan will propagate on the next lineage-refresh cycle

> Propagation only works if lineage exists between the assets. If lineage has not been crawled, scores will not propagate. Run an Atlan lineage crawl before enabling this policy.

---

## Limitations

| Limitation | Detail |
|---|---|
| No lineage graph | OpenDQV does not know upstream/downstream models; Atlan lineage must be crawled from the warehouse separately |
| `qualifiedName` required | Approach 1 silently skips contracts without a valid `asset_id`; there is no automatic fallback |
| Aggregated trend only | `/quality-trend` returns daily aggregates; per-record failure detail is not pushed to Atlan |
| Propagation needs lineage | Approach 6 propagation policies only fire if Atlan lineage is populated |
| Namespace pre-creation | The `OpenDQV` (and `OpenDQV_Rules`) custom metadata namespace must be created in Atlan Admin before any sync runs |
| Column assets must exist | Approach 4 requires columns to have been crawled and to exist as Atlan assets |
| Webhook best-effort | OpenDQV webhooks are fire-and-forget; transient receiver downtime means missed events |
| `pyatlan` version pinning | The `pyatlan` API evolves quickly; pin your version (e.g. `pyatlan>=0.5,<1.0`) in `requirements.txt` |

---

## `asset_id` Conventions for Atlan

OpenDQV's `asset_id` field must be set to the Atlan `qualifiedName` of the asset the contract governs. The qualifiedName is the unique identifier Atlan uses to locate assets across all integrations.

**Atlan qualifiedName format:**

```
default/{connection_name}/{database}/{schema}/{table}
```

### Examples

| Platform | `asset_id` value |
|---|---|
| Snowflake | `default/snowflake-prod/analytics/public/customers` |
| BigQuery | `default/bigquery-prod/my_project/my_dataset/orders` |
| dbt (via Atlan crawl) | `default/dbt-prod/jaffle_shop/public/stg_orders` |

### Contract YAML snippet

```yaml
contract:
  name: customer
  asset_id: "default/snowflake-prod/analytics/public/customers"
  version: "1.0"
  owner: "data-governance"
  owner_team: "governance"
  owner_email: "governance@example.com"
  rules:
    - name: customer_id_not_null
      type: not_empty
      field: customer_id
```

> **Tip:** The easiest way to find the correct `qualifiedName` is to open the asset in the Atlan UI, click **More > Copy qualifiedName** from the asset detail panel. Paste the value directly into `asset_id`.

---

## Recommended Integration Path

| Phase | Action |
|---|---|
| **Now** | Create the `OpenDQV` custom metadata namespace in Atlan Admin |
| **Now** | Run Approach 1 as a cron job (daily) to push contract metadata to Atlan |
| **Now** | Set `asset_id` on contracts to full Atlan qualifiedNames |
| **Q2 2026** | Add Approach 5 (read-back incremental sync) to reduce API call volume |
| **Q2 2026** | Deploy Approach 3 (webhook receiver) for real-time failure updates |
| **Q3 2026** | Add Approach 2 (quality trend score) and configure Approach 6 propagation policies |
| **Q4 2026** | Add Approach 4 (column-level rules) for teams with fully crawled warehouse schemas |

---

## Roadmap

The following capabilities are **not yet implemented** and are planned for a future release.

- **Native Atlan crawl source** — an Atlan crawler that pulls OpenDQV contracts directly, without a sync script
- **Atlan → OpenDQV contract derivation** — bootstrap an OpenDQV contract from an existing Atlan Table asset's schema and tags
- **Atlan Workflow trigger on `archived`** — when a contract status changes to `archived`, fire an Atlan Workflow approval chain for stewardship review
- **`pyatlan` async client** — use `AsyncAtlanClient` for high-volume installations where sequential updates are too slow

---

## See Also

- `docs/asset_id_uri_convention.md` — naming rules for `asset_id` URNs and how they map to catalog identifiers
- `docs/catalog_integration.md` — overview of catalog integration (DataHub and Atlan)
- `docs/connector_sdk_spec.md` — connector interface for production catalog integrations
