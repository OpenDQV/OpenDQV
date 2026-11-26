# Webhooks

OpenDQV Core can push real-time notifications to your services when validation events occur.
Webhooks are useful for triggering downstream workflows — alerting, quarantine queues,
data quality dashboards, or incident management integrations — without polling the API.

---

## Registering a webhook

```bash
curl -X POST "http://localhost:8000/api/v1/webhooks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-service.example.com/hooks/opendqv",
    "events": ["opendqv.validation.failed", "opendqv.batch.failed"],
    "contracts": ["customer", "banking_transaction"]
  }'
```

**Body fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `url` | yes | HTTPS endpoint to receive POST notifications |
| `events` | no | Event types to subscribe to (see below). Defaults to all events. |
| `contracts` | no | Contract names to filter on. Defaults to all contracts. |

**Response:**

```json
{
  "status": "registered",
  "webhook": {
    "url": "https://your-service.example.com/hooks/opendqv",
    "events": ["opendqv.validation.failed", "opendqv.batch.failed"],
    "contracts": ["customer", "banking_transaction"]
  }
}
```

Webhooks are persisted in SQLite and survive server restarts.

### Verifying deliveries

OpenDQV Core does not sign deliveries and sends no secret header; `POST /webhooks` accepts only `url`, `events` and `contracts` and ignores any other key. To verify that a request came from your OpenDQV Core instance, put a shared secret in the webhook URL as a query parameter (`?secret=<value>`) and check it in your receiver, or restrict the receiver by IP allowlist. See [Security](#security).

---

## Event types

| Event | Triggered when |
|-------|----------------|
| `opendqv.validation.failed` | A single-record validation returns `valid: false` (at least one error-severity rule failed) |
| `opendqv.validation.warning` | A single-record validation returns `valid: true` but with non-empty `warnings` |
| `opendqv.batch.failed` | A batch validation has at least one record with `valid: false` |

Subscribe to all events by omitting the `events` field.

---

## Payload shape

All webhook events are delivered as HTTP POST with `Content-Type: application/json`:

**`opendqv.validation.failed` / `opendqv.validation.warning`:**

```json
{
  "event": "opendqv.validation.failed",
  "timestamp": "2026-11-26T10:15:00.123456+00:00",
  "contract": "customer",
  "contract_version": "0.1",
  "opendqv_node_id": "eu-west-prod",
  "context": null,
  "record_id": null,
  "valid": false,
  "error_count": 1,
  "warning_count": 0,
  "violations": [
    {
      "field": "email",
      "rule": "email_format",
      "message": "email must match ^[^@]+@[^@]+\\.[^@]+$",
      "severity": "error"
    }
  ]
}
```

`violations` holds the same error objects `/validate` returns (on `opendqv.validation.warning`, the warnings; `valid` is `true` and `error_count` is `0`).

**`opendqv.batch.failed`:**

```json
{
  "event": "opendqv.batch.failed",
  "timestamp": "2026-11-26T10:15:00.123456+00:00",
  "contract": "banking_transaction",
  "contract_version": "0.1",
  "opendqv_node_id": "eu-west-prod",
  "context": null,
  "total": 100,
  "passed": 97,
  "failed": 3,
  "error_count": 3,
  "violations": [
    {"field": "amount", "rule": "amount_positive", "message": "amount must be greater than 0", "severity": "error"}
  ]
}
```

`violations` is the error objects of the failed records, capped at 50; `error_count` is the uncapped total.

---

## Listing and removing webhooks

```bash
# List all registered webhooks
curl "http://localhost:8000/api/v1/webhooks" \
  -H "Authorization: Bearer $TOKEN"

# Remove a webhook by URL
curl -X DELETE "http://localhost:8000/api/v1/webhooks" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-service.example.com/hooks/opendqv"}'
```

---

## Security

- Webhooks should target HTTPS endpoints only. Plain HTTP receivers are accepted for local
  development but are not recommended for production.
- OpenDQV Core does not sign webhook payloads. If your receiver is public-facing, verify requests
  by IP allowlist or add a shared secret to your endpoint URL as a query parameter
  (`?secret=<value>`) and validate it in your handler.
- Webhook delivery is best-effort: if the endpoint returns a non-2xx status or times out,
  the event is not retried. For guaranteed delivery, use a message queue (SQS, Pub/Sub)
  as the webhook receiver and fan out from there.

---

## Example: Slack alert on validation failure

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/hooks/opendqv")
async def receive_hook(request: Request):
    body = await request.json()
    if body.get("event") == "opendqv.validation.failed":
        contract = body["contract"]
        violations = body.get("violations", [])
        msg = f":x: Validation failed on *{contract}*\n"
        for e in violations[:3]:
            msg += f"  • `{e['field']}`: {e['message']}\n"
        # post msg to Slack webhook URL
    return {"ok": True}
```
