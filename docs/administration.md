# Administration

This guide covers authentication, role-based access control, token management, and the
maker-checker governance workflow.

---

## Authentication modes

| Mode | Setting | When to use |
|------|---------|-------------|
| Open | `AUTH_MODE=open` | Local development, Docker quick-start. No token required. |
| Token | `AUTH_MODE=token` | Production. Every request must include `Authorization: Bearer <token>`. |

Set in `.env` or as an environment variable. Default is `open`.

> ⚠️ Never deploy with `AUTH_MODE=open` outside a local machine. See [SECURITY.md](../SECURITY.md).

---

## Roles

OpenDQV uses six roles. Assign the least-privileged role that covers the use case.

| Role | Intended for | Validate | Read contracts | Edit contracts | Approve | Audit chain | Manage tokens |
|------|-------------|:---:|:---:|:---:|:---:|:---:|:---:|
| `validator` | Source systems (Salesforce, SAP, your app) | ✓ | ✓ | — | — | — | — |
| `reader` | Dashboards, human consumers | ✓ | ✓ | — | — | — | — |
| `auditor` | Compliance reviewers | ✓ | ✓ | — | — | ✓ | — |
| `editor` | Data engineers authoring rules | ✓ | ✓ | ✓ (DRAFT only) | — | — | — |
| `approver` | Governance leads | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| `admin` | Operators | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

The **maker-checker principle** is enforced: the editor who submits a contract for review cannot
be the approver who promotes it to ACTIVE. Use separate tokens with separate roles.

---

## Creating tokens

**Via CLI (recommended for initial setup):**

```bash
# Admin token for the operator — create this first
python -m cli token-generate ops-admin --role admin

# Validator token for a source system
python -m cli token-generate salesforce-prod --role validator

# Editor token for a data engineer
python -m cli token-generate alice-data-eng --role editor

# Approver token for a governance lead
python -m cli token-generate bob-governance --role approver
```

**Via API (requires an existing admin token):**

```bash
curl -s -X POST http://localhost:8000/api/v1/tokens/generate \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"username": "salesforce-prod", "role": "validator"}'
```

The response includes the token value. **It is shown once — save it immediately.**

---

## Listing tokens

```bash
curl -s http://localhost:8000/api/v1/tokens \
  -H "Authorization: Bearer <admin-token>"
```

Returns all tokens with username, role, expiry, and days remaining. Token values are not shown.

---

## Revoking tokens

```bash
# Revoke a specific token by value
curl -s -X POST http://localhost:8000/api/v1/tokens/revoke \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: text/plain" \
  --data "opendqv_the_token_to_revoke"

# Revoke all tokens for a system account
curl -s -X POST http://localhost:8000/api/v1/tokens/revoke/salesforce-prod \
  -H "Authorization: Bearer <admin-token>"
```

---

## Recommended setup for production

1. **Bootstrap:** Start in `AUTH_MODE=open`, create your first admin token via CLI.
2. **Switch to token mode:** Set `AUTH_MODE=token` in `.env` and restart.
3. **Create role-specific tokens:** One `validator` token per source system, one `editor` per engineer, one `approver` per governance lead.
4. **Never give source systems admin tokens.** A Salesforce integration only needs `validator`.
5. **Rotate tokens on a schedule** using `revoke/{username}` + `generate` — there is no automatic expiry enforcement beyond the configured `TOKEN_EXPIRY_DAYS`.

---

## Maker-checker workflow example

```bash
# 1. Alice (editor) adds a rule to a DRAFT contract
curl -s -X POST http://localhost:8000/api/v1/contracts/customer/rules \
  -H "Authorization: Bearer <alice-editor-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "postcode_format", "type": "regex", "field": "postcode",
       "pattern": "^[A-Z]{1,2}[0-9][0-9A-Z]?\\s[0-9][A-Z]{2}$",
       "severity": "error", "error_message": "Invalid UK postcode"}'

# 2. Alice submits for review
curl -s -X POST http://localhost:8000/api/v1/contracts/customer/1.1/submit-review \
  -H "Authorization: Bearer <alice-editor-token>" \
  -H "Content-Type: application/json" \
  -d '{"proposed_by": "alice@example.com"}'

# 3. Bob (approver) reviews and approves — Alice cannot approve her own submission
curl -s -X POST http://localhost:8000/api/v1/contracts/customer/1.1/approve \
  -H "Authorization: Bearer <bob-approver-token>" \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "bob@example.com"}'
```

Every transition is recorded in the immutable hash-chained contract history (`GET /api/v1/contracts/customer/history`).

---

## Configuration reference

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTH_MODE` | `open` or `token` | `open` |
| `SECRET_KEY` | JWT signing key — **change for production** | `change-me-...` |
| `TOKEN_EXPIRY_DAYS` | Default token lifetime in days | `30` |
| `RATE_LIMIT_VALIDATE` | Rate limit for validation endpoints | `300/minute` |
| `RATE_LIMIT_DEFAULT` | Rate limit for other endpoints | `120/minute` |
| `RATE_LIMIT_TOKENS` | Rate limit for token management | `10/minute` |
| `TRUST_PROXY_HEADERS` | Trust X-Forwarded-For from a reverse proxy | `false` |
| `OPENDQV_CONTRACTS_DIR` | Contracts directory path | `./contracts` |
| `OPENDQV_DB_PATH` | SQLite DB path (tokens, webhooks, contract history) | `./opendqv.db` |
| `OPENDQV_MAX_BATCH_ROWS` | Max records per batch validation request | `10000` |
| `WEB_CONCURRENCY` | Number of Gunicorn workers | `1` |
| `MARMOT_URL` | Marmot catalog base URL — enables catalog deep-links | _(unset)_ |

---

## Running behind a reverse proxy

If OpenDQV runs behind nginx, Caddy, Traefik, or a cloud load balancer (AWS ALB, GCP GCLB),
set `TRUST_PROXY_HEADERS=true` in `.env` to enable correct per-IP rate limiting using
`X-Forwarded-For`.

> ⚠️ Do not set `TRUST_PROXY_HEADERS=true` without a proxy. If the API is directly internet-facing,
> this allows clients to inject arbitrary `X-Forwarded-For` headers, defeating per-IP rate limiting.

Supported topologies:

| Deployment | Setting |
|------------|---------|
| Direct (no proxy) | `TRUST_PROXY_HEADERS=false` (default) |
| nginx / Caddy / Traefik in front | `TRUST_PROXY_HEADERS=true` |
| AWS ALB / GCP GCLB | `TRUST_PROXY_HEADERS=true` |
| Kubernetes ingress controller | `TRUST_PROXY_HEADERS=true` |

Minimal nginx config:

```nginx
location / {
    proxy_pass http://opendqv:8000;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host $host;
}
```

---

## Related

- [Security](../SECURITY.md) — deployment checklist, threat model, hardening
- [Production Deployment](production_deployment.md) — token auth, TLS, Docker Compose
- [API Reference](api_reference.md) — token endpoints
- [Contract Versioning](contract_versioning.md) — lifecycle states, DRAFT → ACTIVE
