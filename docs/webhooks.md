# Webhooks

OpenDQV can push real-time notifications to your services when validation events occur.
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

### Webhook Secret

Pass an optional `secret` string when registering a webhook. OpenDQV echoes it back in the `X-OpenDQV-Secret` header on every delivery so your receiver can verify the request came from OpenDQV:

```bash
curl -s -X POST http://localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://hooks.example.com/opendqv", "events": ["opendqv.validation.failed"], "secret": "my-shared-secret"}'
```

Your receiver should compare the `X-OpenDQV-Secret` header value to your expected secret. For stronger security, use a randomly generated 32+ character string and validate it server-side before processing the event.

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
  "contract": "customer",
  "version": "1.0",
  "valid": false,
  "errors": [
    {
      "field": "email",
      "rule": "email_format",
      "message": "email must match ^[^@]+@[^@]+\\.[^@]+$",
      "severity": "error"
    }
  ],
  "warnings": [],
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

**`opendqv.batch.failed`:**

```json
{
  "event": "opendqv.batch.failed",
  "contract": "banking_transaction",
  "version": "1.0",
  "summary": {
    "total": 100,
    "passed": 97,
    "failed": 3,
    "error_count": 3,
    "warning_count": 0
  }
}
```

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
- OpenDQV does not sign webhook payloads. If your receiver is public-facing, verify requests
  by IP allowlist or add a shared secret to your endpoint URL as a query parameter
  (`?secret=<value>`) and validate it in your handler.
- Webhook delivery is best-effort: if the endpoint returns a non-2xx status or times out,
  the event is not retried. For guaranteed delivery, use a message queue (SQS, Pub/Sub)
  as the webhook receiver and fan out from there.

---

## Example: Slack alert on validation failure

```python
import hmac
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/hooks/opendqv")
async def receive_hook(request: Request):
    body = await request.json()
    if body.get("event") == "opendqv.validation.failed":
        contract = body["contract"]
        errors = body.get("errors", [])
        msg = f":x: Validation failed on *{contract}*\n"
        for e in errors[:3]:
            msg += f"  • `{e['field']}`: {e['message']}\n"
        # post msg to Slack webhook URL
    return {"ok": True}
```
