# Connector SDK Specification

> **Target audience:** integration engineers building connectors between OpenDQV and source systems (databases, message brokers, SaaS platforms).

## Overview

A connector is any process that calls OpenDQV's `/api/v1/validate` or `/api/v1/validate/batch` endpoint on behalf of a source system. This document specifies:

1. The **wire protocol** (request/response shapes)
2. The **trace log schema** that connectors must emit
3. The **retry and error-handling contract**
4. **Reference implementations** for common integration patterns

---

## 1. Wire Protocol

### Single-record validation

```http
POST /api/v1/validate
Authorization: Bearer <PAT>
Content-Type: application/json

{
  "record":      { "<field>": "<value>", ... },
  "contract":    "<contract-name>",
  "version":     "latest",
  "context":     "<optional-context>",
  "record_id":   "<optional-caller-correlation-id>"
}
```

**Response (200 OK):**
```json
{
  "valid":           true,
  "record_id":       "<echo>",
  "errors":          [],
  "warnings":        [],
  "contract":        "customer",
  "version":         "1.2",
  "owner":           "data-team@example.com",
  "engine_version":  "1.0.0"
}
```

`engine_version` is required for EMA clinical trial submissions, MiFIR regulatory reporting, and Basel III audit trails. Connectors **must** persist this field alongside every record.

### Batch validation

```http
POST /api/v1/validate/batch
Authorization: Bearer <PAT>
Content-Type: application/json

{
  "records":  [ { ... }, { ... } ],
  "contract": "<contract-name>",
  "version":  "latest",
  "context":  "<optional-context>"
}
```

Maximum batch size: `OPENDQV_MAX_BATCH_ROWS` (default 10,000). Split larger datasets into multiple calls.

### Point-in-time validation

```http
POST /api/v1/validate?as_of=2026-01-15T00:00:00Z
```

Use `?as_of=<ISO8601>` to validate a record against the contract version that was active at a specific historical timestamp. Required for:
- EMA clinical trial retrospective checks
- Insurance claim dispute resolution
- MiFIR T+1 regulatory reporting

---

## 2. Trace Log Schema

Every connector **must** emit a structured trace log entry for each record it validates. The schema below is required for BCBS 239 evidential trail compliance.

```json
{
  "schema_version":  "1",
  "ts":              "2026-03-10T14:22:01.123Z",
  "trace_id":        "550e8400-e29b-41d4-a716-446655440000",
  "connector":       "kafka-consumer-v2.1",
  "record_id":       "<caller-correlation-id>",
  "contract":        "customer",
  "contract_version": "1.2",
  "engine_version":  "1.0.0",
  "context":         "salesforce",
  "valid":           true,
  "error_count":     0,
  "warning_count":   0,
  "latency_ms":      4.2,
  "outcome":         "pass"
}
```

### Required fields

| Field              | Type    | Description |
|--------------------|---------|-------------|
| `schema_version`   | string  | Always `"1"` |
| `ts`               | string  | ISO 8601 UTC timestamp |
| `trace_id`         | string  | UUID v4 |
| `connector`        | string  | Connector name and version |
| `contract`         | string  | Contract name evaluated |
| `contract_version` | string  | Version evaluated (resolved "latest" to exact) |
| `engine_version`   | string  | From validate response `engine_version` field |
| `valid`            | boolean | Pass/fail outcome |
| `outcome`          | string  | `"pass"`, `"fail"`, `"error"`, `"degraded"` |

### Optional fields

| Field          | Type   | Description |
|----------------|--------|-------------|
| `record_id`    | string | Caller's correlation ID |
| `context`      | string | Context override used |
| `error_count`  | int    | Number of blocking errors |
| `warning_count`| int    | Number of warnings |
| `latency_ms`   | float  | Round-trip latency |

---

## 3. Error Handling and Retry Contract

### HTTP status codes

| Code | Meaning | Retry? |
|------|---------|--------|
| 200  | Validation completed (may be `valid: false`) | — |
| 400  | Bad request (batch too large, malformed JSON) | No — fix the request |
| 401  | Missing or expired PAT | No — renew token |
| 404  | Contract not found | No — check contract name |
| 422  | Contract not in a validatable state | No — use `?allow_draft=true` |
| 429  | Rate limit exceeded | Yes — exponential backoff |
| 500  | Server error | Yes — exponential backoff |
| 503  | Service unavailable | Yes — exponential backoff |

### Retry policy (recommended)

```
base_delay = 0.5s
max_delay  = 30s
max_retries = 5
backoff_multiplier = 2.0

delay = min(base_delay * (backoff_multiplier ** attempt), max_delay)
```

### Degraded mode

When the OpenDQV API is unreachable:

1. Log `outcome: "degraded"` in the trace log
2. If `contract_cache_dir` is configured in the SDK, fall back to the cached contract
3. Apply the fail-safe policy configured for the connector:
   - `fail_open`: allow the record through, log as `outcome: "degraded"`
   - `fail_closed`: reject the record, log as `outcome: "error"`

---

## 4. Reference Implementations

### Python (using the OpenDQV SDK)

```python
from sdk.client import OpenDQVClient

client = OpenDQVClient(
    "https://opendqv.internal:8000",
    token="pat_...",
    contract_cache_dir="/var/cache/opendqv",  # offline resilience
)

result = client.validate(record, contract="customer")
if not result["valid"]:
    for err in result["errors"]:
        logger.warning("Field %s: %s", err["field"], err["message"])
    dead_letter_queue.send(record)
```

### Kafka consumer

```python
async with AsyncOpenDQVClient(base_url, token=token, contract_cache_dir=cache_dir) as client:
    async for msg in consumer:
        result = await client.validate(msg.value, contract="transactions")
        if not result["valid"]:
            await dlq.send(msg)
```

### Airflow operator

```python
from sdk.client import OpenDQVClient

def validate_task(records: list[dict], contract: str) -> dict:
    client = OpenDQVClient(Variable.get("OPENDQV_URL"), token=Variable.get("OPENDQV_TOKEN"))
    return client.validate_batch(records, contract=contract)
```

---

## 5. Contract Version Discovery

> **v1.0.0 note:** Version pinning — requesting validation against a specific older version — is not implemented. All validation requests resolve to the currently `ACTIVE` version regardless of the `version` field passed. Pass `"latest"` or the current active version string; both resolve identically. Version pinning is a post-v1.0.0 roadmap item. See `docs/contract_versioning.md` for details.

Connectors should resolve `"latest"` to the exact version at the start of each batch run, and log it:

```python
detail = client.contract("customer")
version = detail["version"]  # e.g. "1.2"
# Now pass version= explicitly to validate_batch for reproducible logs
result = client.validate_batch(records, contract="customer", version=version)
```

This ensures that the `contract_version` field in the trace log reflects the actual version evaluated, even if the contract is updated mid-run.
