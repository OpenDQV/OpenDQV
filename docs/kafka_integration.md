# Kafka Integration

> **Last reviewed:** 2026-03-13.

![OpenDQV + Kafka — validate before committing the offset, route to dead-letter topic](demo_kafka.gif)

*The Kafka pattern: validate each message before committing the offset — invalid records go to the dead-letter topic, valid records proceed. Consumers never process bad data and your warehouse stays clean at source.*

> Covers `confluent-kafka-python v2.x`, `aiokafka v0.x`, Apache Kafka ≥3.x.
> [confluent-kafka on PyPI](https://pypi.org/project/confluent-kafka/) · [aiokafka on PyPI](https://pypi.org/project/aiokafka/)

OpenDQV's write-time model is a natural fit for Kafka consumers: validate every record before committing the offset. Bad records never advance the consumer position — they go to a dead-letter topic instead. Downstream consumers receive only records that passed the contract.

---

## The Pattern

```
Kafka topic: raw.orders
       │
       ▼
┌─────────────────────────────┐
│  Consumer: validate before  │
│  committing offset          │
│  POST /api/v1/validate      │  ← OpenDQV
│  Reject → dead-letter topic │
│  Accept → commit offset     │
└─────────────────────────────┘
       │ (clean records only)
       ▼
Kafka topic: validated.orders
(or write directly to warehouse)
```

Bad records are routed to `dead-letter.orders` for review. Good records are forwarded to `validated.orders` or written to the destination system. The consumer never commits an offset for a record that failed validation — if the consumer restarts, it re-processes from the last committed offset.

---

## Approach 1 — Confluent Kafka Consumer (Synchronous)

The simplest integration: a Python consumer that calls OpenDQV before committing, routes rejects to a dead-letter topic.

```python
import json
import os
from confluent_kafka import Consumer, Producer, KafkaException

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

from opendqv.sdk import OpenDQVClient

client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)

consumer = Consumer({
    "bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092"),
    "group.id": "opendqv-orders-validator",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,  # CRITICAL — manual commit after validation
})

producer = Producer({
    "bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092"),
})

consumer.subscribe(["raw.orders"])

try:
    while True:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            raise KafkaException(msg.error())

        record = json.loads(msg.value())
        # Use Kafka message offset as trace_id for end-to-end correlation
        trace_id = f"kafka:{msg.topic()}:{msg.partition()}:{msg.offset()}"

        result = client.validate(record, contract="orders", record_id=trace_id)  # record_id echoed back in response for correlation

        if result["valid"]:
            # Forward clean record to downstream topic
            producer.produce(
                "validated.orders",
                key=msg.key(),
                value=msg.value(),
                headers=[("x-trace-id", trace_id.encode()), ("x-contract", b"orders")],
            )
            producer.poll(0)
            # Commit offset only after successful validation and produce
            consumer.commit(message=msg)
        else:
            # Route to dead-letter topic with error metadata
            producer.produce(
                "dead-letter.orders",
                key=msg.key(),
                value=json.dumps({
                    "original": record,
                    "errors": result["errors"],
                    "trace_id": trace_id,
                    "contract": result["contract"],
                    "contract_version": result["version"],
                }),
            )
            producer.poll(0)
            consumer.commit(message=msg)  # commit — we handled it via DLT
            print(f"Rejected offset {msg.offset()}: {[e['rule'] for e in result['errors']]}")

finally:
    producer.flush()
    consumer.close()
```

**Key points:**
- `enable.auto.commit: False` is required — you must commit manually after validation, not before
- Commit on rejection too (after routing to DLT), otherwise the consumer loops on the same bad record forever
- `trace_id` encodes the Kafka coordinates — correlate with OpenDQV trace log by offset

---

## Approach 2 — aiokafka Async Consumer

For high-throughput pipelines, use `aiokafka` with `AsyncOpenDQVClient` to validate records concurrently without blocking the event loop.

```python
import asyncio
import json
import os
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from opendqv.sdk import AsyncOpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")
KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "localhost:9092")
CONCURRENCY = int(os.getenv("VALIDATION_CONCURRENCY", "20"))

semaphore = asyncio.Semaphore(CONCURRENCY)


async def validate_and_route(client, producer, msg):
    """Validate one message; route to validated or dead-letter topic."""
    async with semaphore:
        record = json.loads(msg.value)
        trace_id = f"kafka:{msg.topic}:{msg.partition}:{msg.offset}"
        result = await client.validate(record, contract="orders", record_id=trace_id)  # record_id echoed back in response for correlation

        if result["valid"]:
            await producer.send(
                "validated.orders",
                key=msg.key,
                value=msg.value,
                headers=[("x-trace-id", trace_id.encode())],
            )
        else:
            payload = json.dumps({
                "original": record,
                "errors": result["errors"],
                "trace_id": trace_id,
            }).encode()
            await producer.send("dead-letter.orders", key=msg.key, value=payload)


async def main():
    consumer = AIOKafkaConsumer(
        "raw.orders",
        bootstrap_servers=KAFKA_BROKERS,
        group_id="opendqv-orders-validator-async",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BROKERS)

    async with AsyncOpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN) as client:
        await consumer.start()
        await producer.start()
        try:
            async for msg in consumer:
                await validate_and_route(client, producer, msg)
                await consumer.commit()
        finally:
            await producer.stop()
            await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Concurrency note:** The semaphore limits simultaneous in-flight OpenDQV requests to `CONCURRENCY` (default 20). Tune based on your OpenDQV instance's throughput capacity — see [`benchmark_throughput.md`](benchmark_throughput.md) for measured limits.

---

## Approach 3 — Batch Validation per Poll

For latency-tolerant pipelines, accumulate a poll batch and call `POST /api/v1/validate/batch` once per batch instead of once per record. Reduces HTTP round-trips significantly at high message rates.

```python
import json
import os
from confluent_kafka import Consumer, Producer, KafkaException
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient(
    os.getenv("OPENDQV_URL", "http://opendqv:8000"),
    token=os.getenv("OPENDQV_TOKEN"),
)

consumer = Consumer({
    "bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092"),
    "group.id": "opendqv-orders-batch-validator",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "max.poll.records": 500,
})
producer = Producer({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092")})

consumer.subscribe(["raw.orders"])

try:
    while True:
        msgs = consumer.consume(num_messages=500, timeout=1.0)
        if not msgs:
            continue

        records = [json.loads(m.value()) for m in msgs]
        trace_id = f"kafka:batch:{msgs[0].partition()}:{msgs[0].offset()}"

        result = client.validate_batch(records, contract="orders")

        for i, (msg, res) in enumerate(zip(msgs, result["results"])):
            if res["valid"]:
                producer.produce("validated.orders", key=msg.key(), value=msg.value())
            else:
                producer.produce(
                    "dead-letter.orders",
                    value=json.dumps({
                        "original": records[i],
                        "errors": res["errors"],
                        "batch_trace_id": trace_id,
                        "batch_index": i,
                    }).encode(),
                )

        producer.flush()
        # Commit after the whole batch is processed and produced
        consumer.commit()

        summary = result["summary"]
        print(
            f"Batch of {summary['total']}: "
            f"{summary['passed']} passed, {summary['failed']} rejected"
        )
finally:
    consumer.close()
```

---

## Approach 4 — Dead-Letter Topic Consumer (Review + Replay)

Rejected records land in `dead-letter.orders`. A separate consumer reads the DLT for review and — after fixing the source data or updating the contract — replays records back to `raw.orders`.

```python
import json
import os
from confluent_kafka import Consumer, Producer

consumer = Consumer({
    "bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092"),
    "group.id": "dead-letter-reviewer",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
})
replay_producer = Producer({"bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092")})

consumer.subscribe(["dead-letter.orders"])

try:
    while True:
        msg = consumer.poll(timeout=1.0)
        if msg is None or msg.error():
            continue

        dead = json.loads(msg.value())
        print(f"Dead-letter record: contract={dead.get('contract')}, "
              f"errors={dead.get('errors')}")

        # Manual review / fix logic here — or route to a monitoring dashboard

        # When ready to replay (e.g. after contract update):
        # replay_producer.produce(
        #     "raw.orders",
        #     value=json.dumps(dead["original"]).encode(),
        # )
finally:
    replay_producer.flush()
    consumer.close()
```

---

## Approach 5 — Webhook Back-Channel: `opendqv.validation.failed` → Kafka Alert Topic

Publish a Kafka alert message when OpenDQV fires a `opendqv.validation.failed` webhook. Lets your existing Kafka-native alerting consumers pick up OpenDQV rejection events without polling.

```python
from fastapi import FastAPI, Request
from confluent_kafka import Producer
import json, os

app = FastAPI()
alert_producer = Producer({
    "bootstrap.servers": os.getenv("KAFKA_BROKERS", "localhost:9092"),
})

@app.post("/webhooks/opendqv")
async def handle_validation_failed(request: Request):
    payload = await request.json()
    if payload.get("event") != "opendqv.validation.failed":
        return {"status": "ignored"}

    alert_producer.produce(
        "opendqv.alerts",
        key=payload.get("contract", "unknown").encode(),
        value=json.dumps(payload).encode(),
        headers=[("x-event", b"opendqv.validation.failed")],
    )
    alert_producer.poll(0)
    return {"status": "published"}
```

Register the webhook:

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

Downstream consumers subscribe to `opendqv.alerts` and trigger remediation, dashboards, or paging — using existing Kafka infrastructure rather than a separate alerting channel.

---

## Approach 6 — Incremental via `contract_hash`

Cache the contract hash on consumer startup. Re-fetch only when the hash changes rather than calling the registry on every poll cycle.

```python
import requests, os, time

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

_hash_cache: dict[str, str] = {}
_last_check: float = 0
HASH_TTL_SECONDS = 60  # re-check at most once per minute

def get_contract_hash(contract: str) -> str:
    global _last_check
    now = time.monotonic()
    if now - _last_check > HASH_TTL_SECONDS:
        resp = requests.get(
            f"{OPENDQV_URL}/api/v1/contracts",
            headers={"Authorization": f"Bearer {OPENDQV_TOKEN}"},
            timeout=5,
        )
        # Note: contract version-based comparison only; contract_hash is not returned by the list endpoint
        for c in resp.json():
            _hash_cache[c["name"]] = c.get("version", "")
        _last_check = now
    return _hash_cache.get(contract, "")

# Include hash in dead-letter metadata for point-in-time audit
current_hash = get_contract_hash("orders")
```

---

## Approach 7 — Federation-Aware: Route by Topic Prefix

In a federated deployment, select the OpenDQV instance based on the Kafka topic prefix (which typically encodes the domain or region).

```python
from opendqv.sdk import OpenDQVClient
import os

INSTANCES = {
    "eu.": os.getenv("OPENDQV_EU_URL", "http://opendqv-eu.internal:8000"),
    "us.": os.getenv("OPENDQV_US_URL", "http://opendqv-us.internal:8000"),
}
TOKEN = os.getenv("OPENDQV_TOKEN")

_clients: dict[str, OpenDQVClient] = {
    prefix: OpenDQVClient(url, token=TOKEN)
    for prefix, url in INSTANCES.items()
}

def get_client_for_topic(topic: str) -> OpenDQVClient:
    for prefix, client in _clients.items():
        if topic.startswith(prefix):
            return client
    return _clients["us."]  # default
```

---

## Limitations

| Limitation | Detail |
|---|---|
| `unique` rule in streaming | The `unique` rule requires the full batch to detect duplicates. It works in batch-poll mode (Approach 3) but not record-by-record. For cross-message uniqueness, use a stateful stream processor (Flink, Kafka Streams) or a Redis-backed deduplication layer |
| Transient OpenDQV failures | A network timeout to OpenDQV should not drop or DLT a record. Add retry logic with exponential backoff; only route to DLT on persistent validation failure (HTTP 422), not on connectivity errors (5xx, timeout) |
| Consumer lag under high rejection rates | If the rejection rate is high, the DLT producer can become a bottleneck. Monitor DLT lag independently and scale the DLT consumer |
| Offset ordering | Manual commit after routing means that a crash between produce and commit will re-process the message. Ensure your downstream topic consumers are idempotent or use exactly-once semantics (Kafka transactions) |

---

## `asset_id` Conventions for Kafka

Link OpenDQV contracts to Kafka topics using `asset_id`:

```yaml
contract:
  name: orders
  asset_id: "kafka://your-cluster/raw.orders"
  rules:
    - name: order_id_required
      type: not_empty
      field: order_id
```

Use `kafka://{cluster-id}/{topic}` as the convention. This allows catalog tools (DataHub, Collibra) to correlate the contract with the Kafka topic lineage node.

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Wrap your Kafka consumer poll loop with a single `validate()` call before committing (Approach 1) |
| **Now** | Route rejects to a dead-letter topic — never commit and silently drop |
| **Now** | Pass `kafka:{topic}:{partition}:{offset}` as `trace_id` for audit correlation |
| **Planned — based on community demand** | Switch to async batch validation (Approach 2+3) for throughput above ~500 msg/s |
| **Planned — based on community demand** | Add webhook → Kafka alert topic for rejection cluster monitoring (Approach 5) |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned Kafka-specific features, including:
- Native Kafka Connect sink connector with built-in OpenDQV validation
- Kafka Streams transformer (stateful, including `unique` rule support)
- Schema Registry integration — map Avro/Protobuf schemas to OpenDQV contracts automatically

---

## See Also

- [`benchmark_throughput.md`](benchmark_throughput.md) — measured throughput and latency at various concurrency levels
- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`orchestrator_integration.md`](orchestrator_integration.md) — Airflow, Prefect, Dagster gate pattern
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
