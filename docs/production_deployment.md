# Production Deployment Guide

This guide covers hardening an OpenDQV deployment for production use.
The default configuration (`AUTH_MODE=open`) is for local development only
and must not be exposed to untrusted networks.

---

## 1. Enable Token Authentication

Set `AUTH_MODE=token` in your environment before starting the API:

```bash
AUTH_MODE=token uvicorn main:app --host 0.0.0.0 --port 8000
```

Or in your Docker Compose override:

```yaml
environment:
  - AUTH_MODE=token
```

With `AUTH_MODE=token`, every API request must include a valid Personal Access Token (PAT)
in the `Authorization: Bearer <token>` header. Unauthenticated requests return `401`.

All responses include an `X-Auth-Mode` response header reflecting the active auth mode
(`open` or `token`). Monitoring systems can use this header to confirm that auth is enabled
in production — if any response returns `X-Auth-Mode: open`, auth is not enforced on that
deployment.

---

## 2. Generate a Secure SECRET_KEY

The `SECRET_KEY` is used to sign PATs. Use a cryptographically random value:

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

Set it in your environment alongside `AUTH_MODE`:

```bash
export SECRET_KEY="<your-generated-value>"
```

Never use the default `change-me-to-a-random-secret-key` value in any networked deployment.
The API logs a startup warning if the default value is detected.

---

## 3. Create Personal Access Tokens (PATs)

Once `AUTH_MODE=token` is active, generate PATs via the API:

```bash
# Generate a token for a source system (validator — default)
curl -X POST "http://localhost:8000/api/v1/tokens/generate?username=salesforce-prod"

# Generate a token for a data engineer (editor)
curl -X POST "http://localhost:8000/api/v1/tokens/generate?username=alice&role=editor"

# Generate a token for a governance lead (approver)
curl -X POST "http://localhost:8000/api/v1/tokens/generate?username=bob&role=approver"

# Generate a token for a compliance officer (auditor)
curl -X POST "http://localhost:8000/api/v1/tokens/generate?username=carol&role=auditor"

# Generate a token for an operator (admin)
curl -X POST "http://localhost:8000/api/v1/tokens/generate?username=ops&role=admin"
```

Store the returned `pat` value securely — it is shown only once and cannot be recovered.

---

## 4. Role Assignment

OpenDQV enforces a maker-checker model with six roles:

| Role | Intended for | Can validate | Can edit contracts | Can approve | Audit trail |
|------|-------------|:---:|:---:|:---:|:---:|
| `validator` (default) | Source systems, ETL pipelines | ✓ | — | — | — |
| `reader` | Dashboards, monitoring tools | ✓ | — | — | — |
| `auditor` | Compliance officers | ✓ | — | — | ✓ |
| `editor` | Data engineers authoring rules | ✓ | ✓ (DRAFT only) | — | — |
| `approver` | Governance leads | ✓ | — | ✓ | ✓ |
| `admin` | Platform operators | ✓ | ✓ | ✓ | ✓ |

Key separation of duties:
- **`editor` and `approver` must be different people.** An editor submits contracts for review; an approver reviews and promotes them. Neither can do both — this is the maker-checker principle.
- **`approver` cannot author.** Approvers can only review and approve/reject. They cannot add or edit rules. If they have concerns, they reject with a reason and return the contract to the editor.
- **ACTIVE contracts are immutable.** No role — including `admin` — may add, update, or delete rules on an ACTIVE contract. Attempts return `409 Conflict`.
- Use separate tokens for each source system (`validator` role) — one token per integration for clean audit trails and independent revocation.

## 4a. Contract Mutation Security Model

ACTIVE contracts enforce structural immutability for rule sets. This protects production validation behaviour from silent in-place changes — whether from misconfigured agents, compromised credentials, or bulk migration scripts.

### The fork workflow (canonical path for rule changes)

To change the rules on an ACTIVE contract:

```bash
# 1. Create a new draft version (bumps version string, resets status to DRAFT)
curl -X POST "http://localhost:8000/api/v1/contracts/{name}/version?new_version=1.1" \
  -H "Authorization: Bearer $APPROVER_TOKEN"

# 2. Edit rules on the DRAFT
curl -X POST "http://localhost:8000/api/v1/contracts/{name}/rules" \
  -H "Authorization: Bearer $EDITOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "new_rule", "field": "amount", "type": "min", "min_value": 0, ...}'

# 3. Activate the new version (maker-checker — requires approver role)
curl -X POST "http://localhost:8000/api/v1/contracts/{name}/status?status=active" \
  -H "Authorization: Bearer $APPROVER_TOKEN"
```

The old ACTIVE version is automatically set to ARCHIVED at step 1. Both versions remain in the history table with full hash-chained audit trails.

### Draft patch counter

While a contract is in DRAFT state, each rule mutation auto-increments an internal patch counter suffix on the version string (e.g. `1.0-draft.1`, `1.0-draft.2`). This makes DRAFT iteration traceable in the history table without polluting the semantic version namespace. The draft suffix is discarded when the contract is activated — the caller specifies the final semantic version at step 1 above.

### Anomaly detection

Every attempted rule mutation on an ACTIVE contract is logged at `WARNING` level with structured fields:

```
rule_mutation_blocked contract=<name> op=<add_rule|update_rule|delete_rule> caller=<user> status=active
```

A cluster of `rule_mutation_blocked` warnings on a single contract is an anomaly signal — investigate the caller's credentials and intent. Configure your log aggregator (Datadog, Splunk, CloudWatch) to alert on this pattern.

---

## 5. Reverse Proxy / Port Exposure Checklist

Before exposing OpenDQV to the internet:

- [ ] Place behind a reverse proxy (nginx, Caddy, Traefik, or cloud load balancer)
- [ ] Terminate TLS at the proxy — never expose the API on plain HTTP
- [ ] Restrict direct access to port 8000; only the proxy port (443) should be public
- [ ] Set `AUTH_MODE=token` and a strong `SECRET_KEY`
- [ ] Enable rate limiting or confirm `RATE_LIMIT_VALIDATE` and `RATE_LIMIT_DEFAULT` are set
- [ ] Set `OPENDQV_HEALTH_DETAIL=false` (the default) — the `/health` endpoint is public
- [ ] Review and rotate PATs periodically; revoke tokens for departed team members

### nginx example

```nginx
server {
    listen 443 ssl;
    server_name opendqv.example.com;

    ssl_certificate     /etc/ssl/certs/opendqv.crt;
    ssl_certificate_key /etc/ssl/private/opendqv.key;

    location /api/ {
        proxy_pass         http://localhost:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
    }

    location /graphql {
        proxy_pass         http://localhost:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-Proto https;
    }
}

# Redirect HTTP → HTTPS
server {
    listen 80;
    server_name opendqv.example.com;
    return 301 https://$host$request_uri;
}
```

### Caddy example

```
opendqv.example.com {
    reverse_proxy /api/* localhost:8000
    reverse_proxy /graphql localhost:8000
    reverse_proxy /health localhost:8000
}
```

Caddy provisions TLS certificates automatically via Let's Encrypt.

---

## 6. Migration from Open Mode to Token Mode

If you have been running `AUTH_MODE=open` and want to migrate:

1. Generate PATs for all human users and automated callers (`/api/v1/tokens/generate`)
2. Update all callers to include `Authorization: Bearer <token>` in requests
3. Update the UI: set `UI_ACCESS_TOKEN=<admin-or-approver-token>` in the workbench environment
4. Set `AUTH_MODE=token` and restart the API
5. Verify `/health` returns `"auth_mode": "token"`
6. Confirm existing contracts and rules are accessible with the new tokens

---

## 7. Docker Compose Production Configuration

**Generate the required secrets first**, then save the config file — the compose file references them via `${SECRET_KEY}` and `${UI_ACCESS_TOKEN}` and will fail to start if they are not set.

```bash
# Generate SECRET_KEY
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Generate UI_ACCESS_TOKEN (requires the API to be running in open mode first)
UI_ACCESS_TOKEN=$(curl -s -X POST \
  "http://localhost:8000/api/v1/tokens/generate?username=workbench&role=approver" \
  | python -c "import sys,json; print(json.load(sys.stdin)['pat'])")

# Write to .env
echo "SECRET_KEY=$SECRET_KEY" >> .env
echo "UI_ACCESS_TOKEN=$UI_ACCESS_TOKEN" >> .env
```

Then save this as `docker-compose.prod.yml` alongside your main `docker-compose.yml`:

```yaml
# docker-compose.prod.yml — production overrides
# Usage: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

services:
  api:
    restart: always
    environment:
      - AUTH_MODE=token
      - SECRET_KEY=${SECRET_KEY}            # set in your .env or CI secrets
      - OPENDQV_CONTRACTS_DIR=/app/contracts
      - OPENDQV_DB_PATH=/app/data/opendqv.db
      - OPENDQV_HEALTH_DETAIL=false
      - RATE_LIMIT_VALIDATE=200/minute
      - RATE_LIMIT_DEFAULT=60/minute
    volumes:
      - contracts_data:/app/contracts       # persist contracts across restarts
      - db_data:/app/data                   # persist SQLite DB and audit log
    deploy:
      resources:
        limits:
          memory: 512m

  ui:
    restart: always
    environment:
      - API_URL=http://api:8000
      - UI_ACCESS_TOKEN=${UI_ACCESS_TOKEN}  # approver or admin token
    deploy:
      resources:
        limits:
          memory: 256m

volumes:
  contracts_data:
  db_data:
```

Mount persistent volumes for contracts and the SQLite database so data survives restarts.

---

## 8. SQLite Scaling Limits and Upgrade Path

OpenDQV uses SQLite by default. SQLite is suitable for most deployments up to moderate
concurrent load, but has known limits that operators should be aware of before going to
production at scale.

### When SQLite is fine

- Single-node deployments with up to ~50 concurrent validation clients
- Write throughput below ~200 writes/second to the audit/stats tables
- No requirement for multi-node shared state (federated mode excluded)
- Development, staging, and single-tenant production use

### When to consider PostgreSQL

- **Multi-node / federated deployments** — SQLite is local to each node; the federation
  log backend is not yet implemented for SQLite (it raises `NotImplementedError` for
  cross-node sync). Use PostgreSQL if you need a shared audit log across nodes.
- **High write concurrency** — SQLite uses file-level write locking. Under sustained
  concurrent write load (auth token creation, audit log entries, quality stats), you may
  see `database is locked` errors. Switch to PostgreSQL (`OPENDQV_DB_BACKEND=postgres`)
  to eliminate these.
- **>500 req/s sustained** — at this throughput, SQLite write contention becomes a ceiling.
  PostgreSQL removes it.

### Switching to PostgreSQL

```bash
# .env or environment
OPENDQV_DB_BACKEND=postgres
OPENDQV_DB_URL=postgresql://user:password@host:5432/opendqv
```

Install the postgres extra:
```bash
pip install opendqv[postgres]
```

The schema is created automatically on first startup. Existing SQLite data is not
migrated automatically — export audit history before switching if continuity matters.

### SQLite operational notes

- Always mount the SQLite file on a persistent volume (see section 7 above)
- Do not run two API processes pointing at the same SQLite file without connection pooling
- Back up the `.db` file regularly — see `docs/disaster-recovery.md`
- The `opendqv audit-verify` CLI command verifies the hash chain integrity of the SQLite
  audit log at any time without stopping the service

---

## 9. Further Reading

- `docs/runbook.md` — operational runbook for common tasks
- `docs/disaster-recovery.md` — backup and recovery procedures
