# OpenDQV Operational Runbook

This runbook covers three scenarios: first deployment, day-2 operations, and incident response. It assumes you are operating a containerised OpenDQV deployment managed with Docker Compose.

---

## A. First Deployment

### Prerequisites

- Docker Engine 24+ and Docker Compose v2 (`docker compose version`)
- A `.env` file in the project root containing at minimum:

```env
SECRET_KEY=<minimum 32-character random string>
AUTH_MODE=token
WEB_CONCURRENCY=4
```

Generate a suitable `SECRET_KEY`:

```bash
openssl rand -hex 32
```

Never leave `SECRET_KEY` at the default value. The service will start but the security model is invalidated.

### Air-Gapped and Offline Deployments

OpenDQV has **no runtime internet dependencies**. Once the container image is pulled, it runs indefinitely without any network access — no telemetry, no external API calls, no license checks. This makes it suitable for:

- NHS and clinical secure zones
- Defence, classified, and government air-gapped environments
- Industrial control / SCADA networks with strict egress controls
- Any deployment where outbound traffic from the validation service is prohibited

Pull the image once on a connected machine, export it, and load it in the air-gapped environment:

```bash
# On connected machine:
docker pull opendqv/opendqv:latest
docker save opendqv/opendqv:latest | gzip > opendqv.tar.gz

# On air-gapped machine:
docker load < opendqv.tar.gz
docker compose up -d
```

Everything — contract loading, validation, audit logging, token management — works with zero outbound connectivity.

### Deploy

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The prod overlay enables Gunicorn with `WEB_CONCURRENCY=4` workers. Wait approximately 10 seconds for the service to initialise.

### Verify the Deployment is Healthy

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected response:

```json
{
  "status": "healthy",
  "opendqv_node_state": "online",
  "auth_mode": "token",
  "secret_key_insecure": false
}
```

When `OPENDQV_HEALTH_DETAIL=true`, the response also includes `maker_checker_enforced`, `contracts_loaded`, `worker_count`, `stale_worker_count`, and rate limit status.

If `auth_mode` shows `open`, your `.env` is not being read correctly. Confirm the file exists in the working directory and restart:

```bash
docker compose down && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Generate the First Admin Token

Once the service is healthy, create the initial token:

```bash
curl -s -X POST "http://localhost:8000/api/v1/tokens/generate?username=admin&role=admin" \
  -H "Authorization: Bearer <existing-admin-token>" | python3 -m json.tool
```

Save the returned `token` value immediately. It is not recoverable from the database (only the hash is stored).

All subsequent API calls require the header:

```
Authorization: Bearer <token>
```

---

## B. Day-2 Operations

### Adding a New Contract

1. Copy the contract YAML file into the `contracts/` directory on the host (this directory is volume-mounted into the container).
2. Reload the registry without restarting the service:

```bash
curl -s -X POST http://localhost:8000/api/v1/contracts/reload \
  -H "Authorization: Bearer <token>"
```

3. Confirm the contract loaded:

```bash
curl -s http://localhost:8000/api/v1/contracts \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```

### Changing a Contract's Lifecycle Status

Contracts move through three states: `draft` → `active` → `archived`.

- `draft`: validation requests against this contract return a 422 (blocked by lifecycle guard).
- `active`: normal operation.
- `archived`: contract is excluded from listings but remains in history.

To change status:

```bash
curl -s -X PATCH http://localhost:8000/api/v1/contracts/<contract_name>/status \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"status": "active"}'
```

Replace `<contract_name>` with the value of the `contract.name` field in the YAML (e.g. `sf_contact`).

### Rotating the SECRET_KEY

A `SECRET_KEY` rotation invalidates all existing tokens. Plan for a brief authentication outage.

1. Generate a new key: `openssl rand -hex 32`
2. Update `.env` with the new value.
3. Restart the service: `docker compose restart api`
4. Re-issue tokens for all service accounts using the create-token endpoint (see First Deployment above).

There is no token migration path. All tokens signed with the old key are immediately invalid after restart.

### Viewing Logs

```bash
# Follow live logs from the API container
docker compose logs -f api

# Last 200 lines
docker compose logs --tail=200 api

# Streamlit UI logs
docker compose logs -f ui
```

### MCP Draft Creation Rate Limiter

The `create_contract_draft` MCP tool enforces a **10 drafts per hour per identity** cap using a sliding-window in-memory counter keyed on the `created_by` / `OPENDQV_AGENT_IDENTITY` value. When the limit is reached the tool returns an error message; no draft is created.

Operational notes:
- The counter is **per-process** — a multi-worker deployment (`WEB_CONCURRENCY=4`) gives each worker its own independent counter. This is the expected behaviour for the current single-node deployment.
- The counter is **not persisted** — a process restart clears it.
- There is no admin API to inspect or reset counters. If a legitimate agent hits the cap prematurely (e.g. from a retry loop or test harness), restart the API process to clear it: `docker compose restart api`.
- The limit is intentionally conservative for the v1.0 release. It will be configurable in a future release.

### Checking Rate Limit Status

The per-IP rate limit counters are in-memory, per worker. They are not directly inspectable via the API. To observe whether rate limiting is occurring, watch for `429 Too Many Requests` responses or check Prometheus metrics:

```bash
curl -s http://localhost:8000/metrics | grep rate_limit
```

See `SECURITY.md` for the known limitation that effective rate limits are multiplied by `WEB_CONCURRENCY`. For strict enforcement, use a reverse-proxy-level rate limiter.

### Rate Limit Counters Reset on Restart

Both the per-IP HTTP rate limiter and the MCP draft-creation rate limiter use **in-memory counters**. These counters are cleared on every process restart. This is expected behaviour, not a bug:

- If the API restarts mid-window, previously accumulated request counts for all clients are lost and the window effectively resets.
- In a multi-worker deployment (`WEB_CONCURRENCY=4`), each worker maintains its own independent counters, so effective limits are multiplied by the worker count.
- Heartbeat event counts are similarly in-memory per worker and are only flushed to the database on a **graceful** shutdown. A hard crash or OOM kill will lose the in-flight counts for that worker.

If you need strict, crash-safe rate limiting for a production deployment, enforce limits upstream at a reverse proxy (nginx, Caddy) or cloud load balancer.

### Rate Limiter Overhead at High Throughput

The in-process `slowapi` rate limiter checks a counter on **every request**, even when the
client is nowhere near the limit. Benchmarking has measured this overhead at approximately
**~14% throughput reduction** compared to running with limits disabled (158 req/s vs 180 req/s
on the same hardware).

For deployments targeting >150 req/s, the recommended approach is:

1. **Disable app-level rate limiting** (set very high or zero):
   ```
   RATE_LIMIT_VALIDATE=100000/minute
   RATE_LIMIT_DEFAULT=100000/minute
   ```
2. **Enforce limits upstream** at nginx, Caddy, or your cloud load balancer — this is both
   more performant and accurate (no 4× multiplication from multiple workers).

For low-to-medium throughput deployments the default `300/minute` is appropriate and the
overhead is negligible in absolute terms (~1–2ms per request budget).

---

## C. Incident Response

### API Pod Crash / Service Not Responding

This section covers container crash-loops, OOM kills, and any scenario where the API is not reachable.

1. Check container status:

```bash
docker compose ps
```

2. If any container is not `Up`, inspect its logs:

```bash
docker compose logs api
```

3. Attempt a health check directly:

```bash
curl -v http://localhost:8000/health
```

4. If the container is crash-looping, check for a missing or malformed `.env`:

```bash
docker compose config
```

5. Restart if needed:

```bash
docker compose restart api
```

### Contract Causing Unexpected Validation Failures

1. Identify the offending contract name from error responses or logs.
2. Set the contract to `archived` to remove it from active validation:

```bash
curl -s -X PATCH http://localhost:8000/api/v1/contracts/<contract_name>/status \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"status": "archived"}'
```

3. Reload:

```bash
curl -s -X POST http://localhost:8000/api/v1/contracts/reload \
  -H "Authorization: Bearer <token>"
```

4. Inspect contract version history to identify when the bad change was introduced:

```bash
curl -s http://localhost:8000/api/v1/contracts/<contract_name>/history \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```

### Contract Registry File Corrupted

OpenDQV uses SQLite in WAL mode (`synchronous=NORMAL`). A hard crash will **not** corrupt the database — WAL guarantees atomicity at the page level. In the unlikely event you suspect corruption:

1. Run an integrity check (stop writes first if possible):

```bash
sqlite3 opendqv.db "PRAGMA integrity_check"
```

Expected output: `ok`. Any other output indicates corruption.

2. If corrupted, stop the service and restore from backup:

```bash
docker compose stop api
```

Full backup and restore procedures are in [`docs/disaster-recovery.md`](disaster-recovery.md).

3. After restoring `opendqv.db`, restart the service. The contract cache is rebuilt automatically from the `contracts/` directory on every startup — no manual reload is needed.

> **Note:** `contracts/` and `opendqv.db` are on Docker volume mounts and survive container restarts. Data is only at risk if the host filesystem itself is damaged.

### Workbench Not Connecting to API

If the Streamlit UI shows a connection error on first run:

1. Confirm the API container is up and healthy:

```bash
docker compose ps
curl -s http://localhost:8000/health
```

2. Check that the `OPENDQV_API_URL` environment variable in the UI container resolves correctly. In the default Docker Compose setup this is `http://api:8000` (internal service name). If you are running bare-metal and the UI is connecting to `localhost`, ensure the API is bound to `0.0.0.0` and not `127.0.0.1` only.

3. If you recently changed the `SECRET_KEY` or rotated tokens, the token stored in the UI session may be stale. Clear the Streamlit session state and re-enter a valid token.

See the quickstart troubleshooting section (`docs/quickstart.md`) for more first-run issues.

### Token Compromised

Revoke the specific token immediately:

```bash
curl -s -X POST http://localhost:8000/api/v1/tokens/revoke \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"token_id": "<id_of_compromised_token>"}'
```

Issue a replacement token. If the admin token itself is compromised, rotate `SECRET_KEY` (see above), which invalidates all tokens.

### High Error Rate

Check aggregate validation statistics:

```bash
curl -s http://localhost:8000/api/v1/stats \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```

Look for an elevated `validation_errors` count or a specific contract with a high failure ratio. Cross-reference with recent contract changes via the version history endpoint.

### Data Breach Concern

OpenDQV does not persist validation payloads. The data passed in `POST /validate` and `POST /validate/batch` is processed in memory and discarded after the response is returned. What is persisted in `opendqv.db` is:

- Token metadata (name, role, hashed token value — not the plaintext token)
- Webhook registrations (URLs and event types)
- Contract version history (YAML snapshots)

If you suspect the `opendqv.db` file has been accessed by an unauthorised party, rotate all tokens and review webhook registrations for any you did not create. Notify your security team. The validation records themselves are not at risk of exfiltration from the database.

---

## D. Fail-Open vs Fail-Closed

When OpenDQV is unreachable (network partition, pod restart, container OOM), your calling pipeline must decide what to do with unvalidated data. There are two canonical patterns:

### Fail-Closed

**Definition:** If OpenDQV is unreachable, reject the data — do not allow it into the downstream system.

**Use when:**
- Regulated pipelines (financial transactions, AML/KYC, consent records)
- Compliance-critical flows where a bad record entering the system creates a regulatory liability
- Any pipeline where the cost of ingesting bad data exceeds the cost of dropping it

**SDK pattern:**

```python
import httpx
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient("http://opendqv.internal:8000", token=TOKEN, timeout=0.5)

try:
    result = client.validate(record, contract="order")
    if not result["valid"]:
        raise ValueError(f"Record failed validation: {result['errors']}")
except (httpx.TimeoutException, httpx.ConnectError) as exc:
    # Fail-closed: treat unreachable service as a validation failure.
    raise RuntimeError(f"OpenDQV unreachable — record rejected (fail-closed): {exc}") from exc
```

### Fail-Open

**Definition:** If OpenDQV is unreachable, allow the data through with a warning log. Validation is best-effort.

**Use when:**
- High-availability ingestion where a transient DQ service outage must not block data flow
- Non-critical pipelines where missing some bad records is acceptable
- Kafka consumer hot paths where backpressure from a down service would cause consumer lag

**SDK pattern:**

```python
import logging
import httpx
from opendqv.sdk import AsyncOpenDQVClient

logger = logging.getLogger(__name__)

async def validate_or_pass(client, record, contract):
    try:
        result = await client.validate_batch([record], contract=contract, timeout=0.5)
        return result["results"][0]["valid"]
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        # Fail-open: log and allow through. Ensure your DLQ or alerting catches the gap.
        logger.warning("OpenDQV unreachable (fail-open), record admitted without validation: %s", exc)
        return True
```

### Recommended Timeout

Set a **500 ms timeout** as the default for all OpenDQV calls. This is short enough to avoid blocking hot data paths but long enough to tolerate transient network jitter. Adjust down (250 ms) for real-time consumer paths or up (2 s) for low-frequency batch triggers.

### Circuit Breaker Recommendation

Avoid hammering a down OpenDQV endpoint with retries on every record. Use one of:

- **`tenacity`** — exponential backoff with a circuit-breaker decorator:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, CircuitBreaker

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.1, max=1.0))
def validated_write(record):
    result = client.validate(record, contract="order")
    ...
```

- **Simple failure counter** — track consecutive failures and skip OpenDQV once a threshold is reached, resetting after a cool-down period (e.g., 30 seconds). This avoids adding a library dependency for simpler pipelines.

For production Kafka pipelines, the `aiokafka` example in [kafka_integration.md](kafka_integration.md) shows the fail-open pattern with immediate commit on OpenDQV failure.

---

## F. Postgres Backend Configuration

This section applies only when using the **Postgres storage backend** for contract history and federation logs (`OPENDQV_DB_BACKEND=postgres`). The default backend is SQLite — skip this section unless you are explicitly switching backends.

### Required environment variables

```env
OPENDQV_DB_BACKEND=postgres
OPENDQV_DB_URL=postgresql://opendqv:opendqv@localhost:5432/opendqv
```

### Starting the Postgres service (dev/test)

The Postgres service is defined in `docker-compose.dev.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d
```

Wait for readiness before starting the API:

```bash
until docker compose -f docker-compose.yml -f docker-compose.dev.yml exec postgres \
    pg_isready -U opendqv -q 2>/dev/null; do
    echo "Waiting for Postgres..."; sleep 1
done && echo "Postgres ready"
```

### Schema initialisation

`PostgresContractHistoryBackend` auto-creates the `contract_history` table on first use via `_init_db()`. No manual DDL is required.

### SQLite → Postgres migration

**Migration is not currently supported.** The Postgres backend starts fresh — existing SQLite contract history is not carried over. If you need to preserve history, remain on SQLite until a migration path is available. Migration tooling is a roadmap item.

### Teardown

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml stop postgres
```

Data is persisted in the `opendqv_pg_dev` Docker volume and survives container restarts. To wipe it entirely: `docker volume rm opendqv_opendqv_pg_dev`.

---

## E. Clean Restart Procedure

Use this when you need to fully reset the running service — after a configuration change, suspected memory leak, or as a first diagnostic step.

### Docker Compose

```bash
# Stop all containers
docker compose down

# Start fresh (prod overlay)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Verify health
curl -s http://localhost:8000/health | python3 -m json.tool
```

`docker compose down` does not remove named volumes, so `contracts/` and `opendqv.db` are preserved. Your contracts, tokens, and audit history survive the restart.

> **Production note:** The default `docker-compose.yml` does not set a `restart:` policy. For unattended production deployments, add `restart: unless-stopped` to the `api` service in your production overlay so the container recovers automatically after a host reboot or OOM kill.

### Bare-Metal / Systemd

```bash
# Restart the service unit
sudo systemctl restart opendqv

# Check status
sudo systemctl status opendqv

# Verify health
curl -s http://localhost:8000/health | python3 -m json.tool
```

If the service fails to start, check the journal:

```bash
sudo journalctl -u opendqv -n 100 --no-pager
```

---

*Last updated: 2026-03-12*
