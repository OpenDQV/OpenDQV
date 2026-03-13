# OpenMetadata Integration Design

> **API last verified:** `openmetadata-ingestion v1.12.1` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [Check for updates](https://pypi.org/project/openmetadata-ingestion/)

OpenDQV and OpenMetadata serve complementary roles: OpenDQV validates data at the source and governs contracts, while OpenMetadata provides discovery, lineage, data quality dashboards, and governance visibility across the entire estate. This guide shows six approaches to connecting them, from a simple contract metadata push to a real-time webhook-driven pipeline with native Test Suite integration.

---

## Design Goals

1. **Contract-first** — OpenDQV contracts are the source of truth; OpenMetadata reflects their state, not the reverse
2. **Native DQ** — use OpenMetadata's Test Case / Test Suite model for quality scores rather than custom properties, keeping quality data first-class in the catalog
3. **Incremental** — read back `contract_hash` from OpenMetadata before writing to skip unchanged contracts and avoid redundant API calls
4. **Non-invasive** — no changes to OpenDQV internals; integration lives entirely in a thin sync script or connector

---

## What OpenDQV Is NOT

| OpenDQV does | OpenDQV does NOT |
|---|---|
| Validate records against contract rules | Replace OpenMetadata's lineage graph |
| Publish contract metadata (owner, version, hash) | Crawl your warehouse schemas |
| Emit quality trend scores (pass rate) | Manage OpenMetadata users or permissions |
| Fire webhooks on validation failure | Track column-level lineage across models |
| Expose `asset_id` for catalog linkage | Delete or deprecate OpenMetadata assets automatically |

---

## Pre-requisite — Create Custom Properties on the `Table` Entity

Before any sync can run, create 8 custom properties on the `Table` entity type in the OpenMetadata Admin panel under **Settings > Custom Properties > Table**.

| Property name | Type | Description |
|---|---|---|
| `contract_name` | `string` | OpenDQV contract identifier |
| `contract_version` | `string` | Semantic version string |
| `contract_status` | `string` | `active`, `draft`, or `deprecated` |
| `contract_hash` | `string` | SHA-256 hash of the contract YAML |
| `rule_count` | `string` | Total number of active rules |
| `owner_team` | `string` | Owning team name |
| `owner_email` | `string` | Owner contact email |
| `opendqv_description` | `string` | Contract description text |

---

## Approach 1 — Contract Metadata → Table Custom Properties

Push every active OpenDQV contract into the OpenMetadata `Table` entity that the contract governs, writing fields into the `extension` (custom properties) dict via the Python SDK.

### Field mapping

| OpenDQV field | OpenMetadata `extension` key |
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
# pip install openmetadata-ingestion requests
import requests
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
    OpenMetadataJWTClientConfig,
)
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.type.entityReference import EntityReference

OPENDQV_URL   = "http://localhost:8000"
OPENDQV_TOKEN = "<OPENDQV_TOKEN>"
OM_URL        = "http://openmetadata:8585/api"
OM_JWT        = "<OPENMETADATA_JWT_TOKEN>"

server_config = OpenMetadataConnection(
    hostPort=OM_URL,
    authProvider="openmetadata",
    securityConfig=OpenMetadataJWTClientConfig(jwtToken=OM_JWT),
)
metadata = OpenMetadata(server_config)

contracts = requests.get(
    f"{OPENDQV_URL}/api/v1/registry",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    if not asset_id:
        print(f"Skipping {contract['name']}: no asset_id set")
        continue

    try:
        table = metadata.get_by_name(entity=Table, fqn=asset_id)
        if not table:
            print(f"Table not found in OpenMetadata: {asset_id}")
            continue

        extension = {
            "contract_name":       contract["name"],
            "contract_version":    contract.get("version", ""),
            "contract_status":     contract.get("status", ""),
            "contract_hash":       contract.get("contract_hash", ""),
            "rule_count":          str(contract.get("rule_count", 0)),
            "owner_team":          contract.get("owner_team", ""),
            "owner_email":         contract.get("owner_email", ""),
            "opendqv_description": contract.get("description", ""),
        }

        metadata.patch(
            entity=Table,
            source=table,
            field_name="extension",
            field_value=extension,
        )

        # Set owner (user) and owners (team) if available
        if contract.get("owner"):
            owner_ref = EntityReference(
                id=contract["owner"],
                type="user",
                name=contract["owner"],
            )
            metadata.patch(entity=Table, source=table, field_name="owner", field_value=owner_ref)

        print(f"Synced {contract['name']} → {asset_id}")
    except Exception as e:
        print(f"Failed to sync {contract['name']}: {e}")

print(f"Processed {len(contracts)} contracts")
```

Replace `openmetadata:8585` with your OpenMetadata host. The JWT token is generated under **Settings > Bots** or from your user profile.

---

## Approach 2 — Quality Trend → Native Test Suite + Test Case Results

OpenMetadata has first-class data quality support via Test Suites and Test Cases — use it rather than writing scores into custom properties. This surfaces pass rates natively in the OpenMetadata DQ dashboard alongside warehouse-native tests.

### Design

Create one `TestSuite` per contract (if not already present), one `TestCase` (`opendqv_pass_rate`) under it, and push a `TestCaseResult` per day from the `/quality-trend?days=7` endpoint.

### Field mapping

| OpenDQV source | OpenMetadata field |
|---|---|
| `quality-trend` avg pass rate × 100 | `TestResultValue(name="pass_rate", value=str(score))` |
| score ≥ 80 | `TestCaseStatus.Success` |
| score < 80 | `TestCaseStatus.Failed` |
| trend day `timestamp` | `TestCaseResult.timestamp` (epoch ms) |

### Python snippet

```python
# pip install openmetadata-ingestion requests
# Extends Approach 1 — run after syncing base metadata
from datetime import datetime, timezone
from metadata.generated.schema.tests.testSuite import TestSuite
from metadata.generated.schema.tests.testCase import TestCase
from metadata.generated.schema.tests.testCaseResult import (
    TestCaseResult,
    TestResultValue,
    TestCaseStatus,
)
from metadata.generated.schema.api.tests.createTestSuite import CreateTestSuiteRequest
from metadata.generated.schema.api.tests.createTestCase import CreateTestCaseRequest

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    if not asset_id:
        continue

    suite_name = f"opendqv_{contract['name']}"

    # Create TestSuite if not exists
    suite = metadata.get_by_name(entity=TestSuite, fqn=suite_name)
    if not suite:
        suite = metadata.create_or_update(
            CreateTestSuiteRequest(
                name=suite_name,
                description=f"OpenDQV pass-rate suite for contract {contract['name']}",
            )
        )

    case_name = f"{suite_name}.opendqv_pass_rate"

    # Create TestCase if not exists
    test_case = metadata.get_by_name(entity=TestCase, fqn=case_name)
    if not test_case:
        test_case = metadata.create_or_update(
            CreateTestCaseRequest(
                name="opendqv_pass_rate",
                testSuite=suite.fullyQualifiedName,
                entityLink=f"<#E::table::{asset_id}>",
                testDefinition="tableCustomSQLQuery",
                description="OpenDQV contract pass rate (7-day rolling average)",
            )
        )

    # Push daily results
    trend_resp = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract['name']}/quality-trend?days=7",
        headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
    )
    if trend_resp.status_code != 200:
        continue

    trend = trend_resp.json()
    rates = [day["pass_rate"] for day in trend if "pass_rate" in day]
    avg_score = round((sum(rates) / len(rates)) * 100, 1) if rates else None

    if avg_score is not None:
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        metadata.add_test_case_results(
            test_case_result=TestCaseResult(
                timestamp=ts_ms,
                testCaseStatus=TestCaseStatus.Success if avg_score >= 80 else TestCaseStatus.Failed,
                result=f"OpenDQV 7-day avg pass rate: {avg_score}%",
                testResultValue=[
                    TestResultValue(name="pass_rate", value=str(avg_score))
                ],
            ),
            test_case_fqn=test_case.fullyQualifiedName.__root__,
        )
        print(f"Pushed TestCaseResult for {contract['name']}: score={avg_score}")
```

---

## Approach 3 — Webhook-Driven `validation.failed` → Real-Time Test Case Result

Rather than polling, register an OpenDQV webhook and push a `TestCaseResult` the moment a validation failure is detected.

### Architecture

```
OpenDQV engine
     │
     │  POST /webhooks/openmetadata (validation.failed event)
     ▼
FastAPI receiver (this script)
     │
     │  add_test_case_results(TestCaseResult(testCaseStatus="Failed", ...))
     ▼
OpenMetadata
     │
     └── TestCase "opendqv_pass_rate" gets a Failed result
         visible in the DQ dashboard immediately
```

### Webhook payload → TestCaseResult mapping

| Webhook field | OpenMetadata field |
|---|---|
| `contract` (name) | Resolve `TestCase` FQN via `opendqv_{contract}.opendqv_pass_rate` |
| `pass_rate` | `TestResultValue(name="pass_rate", value=str(pass_rate × 100))` |
| `failed_count` > 0 | `TestCaseStatus.Failed` |
| `trace_id` | Appended to `result` string for traceability |
| `timestamp` | `TestCaseResult.timestamp` (epoch ms) |

### FastAPI receiver snippet

```python
# pip install openmetadata-ingestion fastapi uvicorn requests
from fastapi import FastAPI, Request
from datetime import datetime, timezone
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
    OpenMetadataJWTClientConfig,
)
from metadata.generated.schema.tests.testCase import TestCase
from metadata.generated.schema.tests.testCaseResult import (
    TestCaseResult,
    TestResultValue,
    TestCaseStatus,
)

app = FastAPI()
server_config = OpenMetadataConnection(
    hostPort="http://openmetadata:8585/api",
    authProvider="openmetadata",
    securityConfig=OpenMetadataJWTClientConfig(jwtToken="<OPENMETADATA_JWT_TOKEN>"),
)
metadata = OpenMetadata(server_config)

@app.post("/webhooks/openmetadata")
async def handle_validation_event(request: Request):
    payload = await request.json()
    if payload.get("event") != "validation.failed":
        return {"status": "ignored"}

    contract_name = payload["contract"]
    pass_rate     = payload.get("pass_rate", 0)
    trace_id      = payload.get("trace_id", "")
    score         = round(pass_rate * 100, 1)
    ts_ms         = int(datetime.now(timezone.utc).timestamp() * 1000)

    case_fqn = f"opendqv_{contract_name}.opendqv_pass_rate"
    test_case = metadata.get_by_name(entity=TestCase, fqn=case_fqn)
    if not test_case:
        return {"status": "test_case_not_found", "fqn": case_fqn}

    metadata.add_test_case_results(
        test_case_result=TestCaseResult(
            timestamp=ts_ms,
            testCaseStatus=TestCaseStatus.Failed,
            result=f"Validation failed — pass_rate={score}% trace={trace_id}",
            testResultValue=[TestResultValue(name="pass_rate", value=str(score))],
        ),
        test_case_fqn=test_case.fullyQualifiedName.__root__,
    )
    print(f"Pushed Failed TestCaseResult for {contract_name} (trace={trace_id})")
    return {"status": "ok"}
```

### Register the webhook

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENDQV_TOKEN" \
  -d '{
    "url": "http://your-receiver:9000/webhooks/openmetadata",
    "events": ["validation.failed"],
    "secret": "your-webhook-secret"
  }'
```

---

## Approach 4 — Rule-Level Test Cases (One TestCase per OpenDQV Rule)

For teams that want granular rule visibility in OpenMetadata, create one `TestCase` per OpenDQV rule under the contract's `TestSuite`, mapping to standard OpenMetadata test definitions.

### Rule type → OpenMetadata test definition mapping

| OpenDQV rule type | OpenMetadata test definition | Notes |
|---|---|---|
| `not_empty` | `columnValuesToBeNotNull` | Direct mapping |
| `unique` | `columnValuesToBeUnique` | Direct mapping |
| `regex` | `columnValuesToMatchRegex` | Pass `regex` param |
| `range` | `columnValuesToBeBetween` | Pass `minValue` / `maxValue` params |
| `min` | `columnValuesToBeBetween` | `minValue` only |
| `max` | `columnValuesToBeBetween` | `maxValue` only |
| `min_length` | *skipped* | No standard OM equivalent |
| `max_length` | *skipped* | No standard OM equivalent |
| `lookup` | *skipped* | Requires cross-dataset join context |
| `custom` | *skipped* | No standard OM equivalent |

> **Note:** Test definitions listed above (`columnValuesToBeNotNull`, etc.) are built into OpenMetadata by default and require no pre-creation. Skipped rule types are not mapped — they are silently ignored. The `TestSuite` for the contract must already exist (created via Approach 2) before this approach can run.

### Python snippet

```python
# pip install openmetadata-ingestion requests
# Requires Approach 2 TestSuite to exist for the contract

RULE_DEFINITION_MAP = {
    "not_empty": "columnValuesToBeNotNull",
    "unique":    "columnValuesToBeUnique",
    "regex":     "columnValuesToMatchRegex",
    "range":     "columnValuesToBeBetween",
    "min":       "columnValuesToBeBetween",
    "max":       "columnValuesToBeBetween",
}

contract_detail = requests.get(
    f"{OPENDQV_URL}/api/v1/registry/{contract['name']}",
    headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
).json()

suite_name = f"opendqv_{contract['name']}"

for rule in contract_detail.get("rules", []):
    rule_type = rule.get("type")
    test_def  = RULE_DEFINITION_MAP.get(rule_type)
    if not test_def:
        continue

    field    = rule.get("field", "")
    params   = {}
    if rule_type == "regex":
        params = {"regex": rule.get("pattern", "")}
    elif rule_type in ("range", "min", "max"):
        if "min" in rule:
            params["minValue"] = rule["min"]
        if "max" in rule:
            params["maxValue"] = rule["max"]

    entity_link = f"<#E::table::{asset_id}::columns::{field}>" if field else f"<#E::table::{asset_id}>"

    try:
        metadata.create_or_update(
            CreateTestCaseRequest(
                name=rule["name"],
                testSuite=suite_name,
                entityLink=entity_link,
                testDefinition=test_def,
                parameterValues=[{"name": k, "value": str(v)} for k, v in params.items()],
                description=f"OpenDQV rule: {rule.get('description', rule['name'])}",
            )
        )
        print(f"Created TestCase {rule['name']} ({test_def}) for {contract['name']}")
    except Exception as e:
        print(f"Skipping rule {rule['name']}: {e}")
```

---

## Approach 5 — Incremental Sync via Read-Back

Instead of a local state file, read the `contract_hash` currently stored in OpenMetadata's `extension` and skip the write if it matches the OpenDQV hash. OpenMetadata is the source of state.

```python
# pip install openmetadata-ingestion requests
# No local state file needed — OpenMetadata is the source of state

synced, skipped = 0, 0

for contract in contracts:
    asset_id = contract.get("asset_id", "")
    if not asset_id:
        continue

    try:
        table = metadata.get_by_name(entity=Table, fqn=asset_id)
        if table and table.extension:
            stored_hash = table.extension.__root__.get("contract_hash", "")
            if stored_hash == contract.get("contract_hash", ""):
                print(f"Skipping {contract['name']}: hash unchanged")
                skipped += 1
                continue
    except Exception:
        pass  # Table not found — proceed with write

    # ... emit to OpenMetadata (Approach 1 logic) ...
    synced += 1

print(f"Synced {synced}, skipped {skipped} (hash unchanged)")
```

---

## Approach 6 — Federation-Aware Sync

When OpenDQV runs in a federated topology, check node availability before pulling contracts.

```python
# pip install openmetadata-ingestion requests
import requests

OPENDQV_URL   = "http://localhost:8000"
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
    # ... emit to OpenMetadata (Approach 1 logic) ...
    print(f"Synced {len(contracts)} contracts from node {node['id']}")
```

---

## Limitations

| Limitation | Detail |
|---|---|
| No lineage graph | OpenDQV does not know upstream/downstream models; OpenMetadata lineage must be crawled from the warehouse separately |
| Aggregated trend only | `/quality-trend` returns daily aggregates, not per-record failure detail |
| Custom properties must be pre-created | The 8 `Table` custom properties must be created in OpenMetadata Admin before Approach 1 can run |
| Column test definitions must pre-exist | Approach 4 requires standard test definitions (`columnValuesToBeNotNull`, etc.) to exist in OpenMetadata — they do by default for standard types |
| Webhook best-effort | OpenDQV webhooks are fire-and-forget; transient receiver downtime means missed events |
| FQN required (no fallback) | `asset_id` must be the exact OpenMetadata fully qualified name; there is no automatic fallback or search by name |
| `TestSuite` pre-registration needed | Approach 3 and Approach 4 require the `TestSuite` to already exist (created via Approach 2) before pushing results |

---

## `asset_id` Conventions for OpenMetadata

OpenDQV's `asset_id` field must be set to the OpenMetadata fully qualified name (FQN) of the asset the contract governs.

**OpenMetadata FQN format:**

```
{service}.{database}.{schema}.{table}
```

### Examples

| Platform | `asset_id` value |
|---|---|
| Snowflake | `production-snowflake.analytics.public.customers` |
| BigQuery | `bigquery-prod.my_project.my_dataset.orders` |
| dbt (via OM crawl) | `dbt-prod.jaffle_shop.public.stg_orders` |

### Contract YAML snippet

```yaml
contract:
  name: customer
  asset_id: "production-snowflake.analytics.public.customers"
  version: "1.0"
  owner: "data-governance"
  owner_team: "governance"
  owner_email: "governance@example.com"
  rules:
    - name: customer_id_not_null
      type: not_empty
      field: customer_id
```

> **Tip:** The easiest way to find the correct FQN is to open the asset in the OpenMetadata UI and copy it from the asset detail panel. The FQN is shown in the asset header and can be copied directly into `asset_id`.

---

## Recommended Integration Path

| Phase | Action |
|---|---|
| **Now** | Create the 8 custom properties on `Table` entities in OpenMetadata Admin |
| **Now** | Run Approach 1 as a cron job (daily) to push contract metadata to OpenMetadata |
| **Now** | Set `asset_id` on contracts to full OpenMetadata FQNs |
| **Q2 2026** | Add Approach 5 (read-back incremental sync) to reduce API call volume |
| **Q2 2026** | Deploy Approach 3 (webhook receiver) for real-time failure test case results |
| **Q3 2026** | Add Approach 2 (native Test Suite + Test Case results) for DQ dashboard visibility |
| **Q4 2026** | Add Approach 4 (rule-level test cases) for teams that monitor at rule granularity |

---

## Roadmap

The following capabilities are **not yet implemented** and are planned for a future release.

- **Native OM ingestion connector** — an `opendqv` Source plugin so catalog admins can pull contracts via a standard OpenMetadata ingestion recipe without writing Python
- **Auto TestSuite creation on contract publish** — when a contract is published in OpenDQV, the corresponding OpenMetadata `TestSuite` and `TestCase` are registered automatically
- **OpenMetadata → OpenDQV contract bootstrap** — derive an OpenDQV contract skeleton from an existing OpenMetadata `Table` entity's schema and column metadata
- **Kafka-based MCE emitter alternative** — replace REST-based result emission with a Kafka producer for high-volume installations

---

## See Also

- `docs/asset_id_uri_convention.md` — naming rules for `asset_id` URNs and how they map to catalog identifiers
- `docs/catalog_integration.md` — overview of catalog integration (DataHub and Atlan)
- `docs/connector_sdk_spec.md` — connector interface for production catalog integrations
