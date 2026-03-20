# Monte Carlo Integration

> **API last verified:** Monte Carlo REST API — 2026-03-13.
> [Monte Carlo documentation](https://docs.getmontecarlo.com/)

Monte Carlo monitors data at-rest anomalies — schema drift, volume changes, distribution shifts — after data lands in your warehouse. OpenDQV operates at the other end: it blocks bad records before they are written. The OpenDQV HMAC trace log is the bridge that connects write-time decisions to Monte Carlo alerts.

---

## Approach 1 — Trace Log Shipping

Every OpenDQV validation decision is written to a JSONL trace log with a timestamp, record ID, contract version, trace ID, and outcome. Ship this log to your aggregator; Monte Carlo ingests it as a custom event source.

### JSONL trace log format

```json
{"timestamp": "2026-03-13T10:00:00Z", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "contract": "customer", "contract_version": "1.0", "asset_id": "urn:opendqv:acme:customer:1.0", "valid": false, "failed_rules": ["email_valid"], "record_id": "cust-9912"}
{"timestamp": "2026-03-13T10:00:01Z", "trace_id": "7c0c9e2b1f4a4d8e9b2c3d4e5f6a7b8c", "contract": "customer", "contract_version": "1.0", "asset_id": "urn:opendqv:acme:customer:1.0", "valid": true, "failed_rules": [], "record_id": "cust-9913"}
```

### Field mapping: OpenDQV trace log → Monte Carlo custom event

| OpenDQV field | Monte Carlo custom event field |
|---|---|
| `timestamp` | `event_time` |
| `trace_id` | `external_id` |
| `contract` | `dataset` |
| `asset_id` | `table_id` (when matches MC monitored table) |
| `valid: false` | `event_type: data_quality_failure` |
| `failed_rules` | `metadata.failed_rules` |
| `contract_version` | `metadata.contract_version` |

### Shipping via Fluent Bit (recommended for production)

Fluent Bit tails the JSONL log, parses each line as JSON, and forwards to your aggregator. This is the correct production approach — it handles rotation, backpressure, and retries correctly.

```ini
# fluent-bit.conf
[INPUT]
    Name              tail
    Path              /var/log/opendqv/trace.jsonl
    Parser            json
    Tag               opendqv.trace
    Refresh_Interval  5
    Read_from_Head    False

[FILTER]
    Name   record_modifier
    Match  opendqv.trace
    Record service opendqv
    Record source  opendqv

# Forward to Datadog
[OUTPUT]
    Name        datadog
    Match       opendqv.trace
    Host        http-intake.logs.datadoghq.com
    TLS         On
    apikey      ${DD_API_KEY}
    dd_service  opendqv
    dd_source   opendqv
    dd_tags     env:prod

# --- OR forward to Splunk HEC ---
# [OUTPUT]
#     Name        splunk
#     Match       opendqv.trace
#     Host        splunk.internal
#     Port        8088
#     Splunk_Token ${SPLUNK_HEC_TOKEN}
#     TLS         On

# --- OR forward to CloudWatch ---
# [OUTPUT]
#     Name              cloudwatch_logs
#     Match             opendqv.trace
#     region            eu-west-1
#     log_group_name    /opendqv/trace
#     log_stream_prefix opendqv-
#     auto_create_group true
```

Mount the trace log into the Fluent Bit container via a shared volume (same volume used by the OpenDQV container for `/var/log/opendqv`).

From your aggregator, configure a Monte Carlo custom event ingest pipeline via the Monte Carlo UI under **Monitors > Custom Events**.

---

## Approach 2 — Webhook Correlation

When OpenDQV rejects records (`opendqv.validation.failed`), push a custom event directly to Monte Carlo to flag the affected asset.

### FastAPI webhook receiver

```python
from fastapi import FastAPI, Request
import requests, os

app = FastAPI()
MC_API_URL = "https://api.getmontecarlo.com/graphql"
MC_API_KEY = os.getenv("MONTE_CARLO_API_KEY")
MC_API_SECRET = os.getenv("MONTE_CARLO_API_SECRET")

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    # Push to Monte Carlo via GraphQL mutation
    mutation = """
    mutation CreateEvent($input: CreateEventInput!) {
      createOrUpdateLineage(input: $input) {
        output { type }
      }
    }
    """
    requests.post(
        MC_API_URL,
        headers={
            "x-mcd-id": MC_API_KEY,
            "x-mcd-token": MC_API_SECRET,
        },
        json={
            "query": mutation,
            "variables": {
                "input": {
                    "source": "opendqv",
                    "externalId": payload["trace_id"],
                    "timestamp": payload["timestamp"],
                    "dataset": payload["contract"],
                    "metadata": {
                        "failed_count": payload["failed_count"],
                        "asset_id": payload.get("asset_id"),
                    },
                }
            },
        },
    )
    return {"status": "forwarded", "contract": payload["contract"]}
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

See [`webhooks.md`](webhooks.md) for full webhook configuration and HMAC signing.

---

## Approach 3 — `asset_id` Bridge

The `asset_id` field on an OpenDQV contract can be set to match the Monte Carlo table identifier for the same asset. This enables cross-tool correlation: when Monte Carlo fires an alert on a table, you can query OpenDQV's quality-trend endpoint for the same asset to see write-time rejection history.

### Setting the bridge

```yaml
contract:
  name: customer
  version: "1.0"
  # asset_id matches the table identifier in Monte Carlo
  asset_id: "snowflake://acme-prod/analytics/public/customers"
  rules:
    - name: email_valid
      type: regex
      field: email
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
```

### Querying write-time history when MC fires an alert

```python
import requests

# asset_id from the Monte Carlo alert
asset_id = "snowflake://acme-prod/analytics/public/customers"

# Find the OpenDQV contract for this asset
contracts = requests.get(
    "http://opendqv:8000/api/v1/registry",
    headers={"Authorization": "Bearer <token>"},
).json()

matching = [c for c in contracts if c.get("asset_id") == asset_id]
if matching:
    contract_name = matching[0]["name"]
    trend = requests.get(
        f"http://opendqv:8000/api/v1/contracts/{contract_name}/quality-trend?days=7",
        headers={"Authorization": "Bearer <token>"},
    ).json()
    print(f"OpenDQV 7-day pass rate for {contract_name}: {trend}")
```

---

## Approach 4 — Federation-Aware

In a federated deployment, each OpenDQV instance covers a region or domain. Correlate Monte Carlo alerts with the correct regional instance using `asset_id` prefix routing:

```python
INSTANCES = {
    "eu-west": "http://opendqv-eu.internal:8000",
    "us-east": "http://opendqv-us.internal:8000",
}

def find_contract_for_asset(asset_id: str) -> dict | None:
    for region, base_url in INSTANCES.items():
        contracts = requests.get(
            f"{base_url}/api/v1/registry",
            headers={"Authorization": "Bearer <token>"},
        ).json()
        for c in contracts:
            if c.get("asset_id") == asset_id:
                return {"region": region, "base_url": base_url, **c}
    return None
```

---

## Limitations

| Limitation | Detail |
|---|---|
| No native MC connector | Monte Carlo does not yet have an OpenDQV connector in its integration marketplace |
| GraphQL API changes | Monte Carlo's mutation API evolves; pin the API version and test on MC upgrades |
| JSONL log volume | High-throughput deployments produce large trace logs — use log sampling or a structured log shipper (Fluent Bit, Datadog agent) rather than the shell tail loop above |
| Alert correlation latency | Trace log shipping is async; real-time correlation requires the webhook approach (Approach 2) |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Set `asset_id` on contracts to match Monte Carlo table identifiers |
| **Now** | Ship JSONL trace log to your log aggregator; configure MC custom event ingest |
| **Planned — based on community demand** | Add `opendqv.validation.failed` webhook → MC custom event receiver |
| **Planned — based on community demand** | Build quality-trend dashboard in MC using OpenDQV pass-rate data |
| **Planned — based on community demand** | Native MC connector (planned) |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Monte Carlo features including the native connector and pass-rate monitor source.

---

## See Also

- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
- [`gx_integration.md`](gx_integration.md) — Great Expectations integration
- [`soda_integration.md`](soda_integration.md) — Soda Core integration
