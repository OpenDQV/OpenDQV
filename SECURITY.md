# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (main branch) | ✅ |
| older tagged releases | ⚠️ Best-effort |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues by email to: **opendqv@bgmsconsultants.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected versions / components
- Potential impact assessment

We follow a **90-day coordinated disclosure** policy (aligned with Google Project Zero):
- We will acknowledge receipt within **48 hours**
- We will provide an initial assessment within **7 days**
- We will aim to release a fix within **90 days** of the report
- We will credit reporters in the release notes unless anonymity is requested

Security advisories are published as [GitHub Security Advisories](https://github.com/opendqv/OpenDQV/security/advisories) and feed into the GitHub Advisory Database.

---

## Known Limitations and Mitigations

The following limitations are known, disclosed proactively, and should be reviewed before any production deployment.

### 1. Rate Limiter — 4× Effective Rate with Multiple Workers

> ⚠️ **Impact: Medium** — Known architectural limitation. See workarounds below.

`RATE_LIMIT_VALIDATE` and `RATE_LIMIT_DEFAULT` use `slowapi`'s `InMemoryRateLimiter`, which maintains counters **per Gunicorn worker process**. With the default `WEB_CONCURRENCY=4`, the effective per-IP rate limit is **4× the configured value** (e.g. `300/minute` configured → up to `1,200/minute` effective).

> **Note:** This is a fundamental limitation of in-process rate limiting in multi-worker deployments, not a bug. It is disclosed here so operators can make an informed configuration choice. For regulated environments, use one of the workarounds below.

**Workarounds (in order of preference for production):**

1. **Reverse proxy rate limiting (recommended):** Configure your nginx, Caddy, or cloud load balancer to enforce rate limits upstream before requests reach OpenDQV workers. This is the most reliable approach and is independent of the number of workers.
2. **Redis-backed rate limiter:** Set `RATE_LIMIT_BACKEND=redis` and configure `REDIS_URL` — this shares counters across all workers via Redis, giving accurate per-IP limiting.
3. **Single worker:** Set `WEB_CONCURRENCY=1` for strict single-process rate enforcement. This reduces throughput to roughly 1× but gives accurate rate limiting without external dependencies.

### 2. Webhook SSRF — DNS Resolution at Registration Time

> ℹ️ **Fixed in current release.** (webhook registration is an authenticated, privileged operation)

Webhook URL validation blocks RFC 1918 IP ranges (10.x, 172.16.x, 192.168.x), loopback (127.x), and link-local (169.254.x) addresses. Hostnames are now **DNS-resolved at registration time** via `socket.getaddrinfo()` and all returned IPs are checked against the blocked ranges. Registration **fails closed** on DNS resolution failure (NXDOMAIN).

**Residual risk:** DNS rebinding after registration (TTL expiry between registration and webhook dispatch) is not mitigated by this check alone. For high-security deployments, also configure egress firewall rules on the host to block outbound connections to RFC 1918 ranges from the OpenDQV container.

### 3. Open Authentication Mode (Default)

> ⚠️ **Impact: High if exposed to the internet**

The default `AUTH_MODE=open` disables all authentication. This is intentional for local development and air-gapped environments. **Never expose `AUTH_MODE=open` to the public internet.**

**Maker-checker is fully bypassed in open mode.** In `AUTH_MODE=open`, `get_current_role()` returns `"admin"` for all callers regardless of credentials. This means the separation-of-duties control (writers cannot promote contracts to ACTIVE; only approvers/admins can) is entirely inoperative. The `/health` endpoint will report `"maker_checker_enforced": false` to make this visible. Any deployment where governance controls are required must use `AUTH_MODE=token`.

**Mitigation:** Set `AUTH_MODE=token` in `.env` for any deployment reachable from outside your local network, or where data governance controls (BCBS 239, FCA Consumer Duty) apply.

### 4. SQLite Persistence — Single-File, No Encryption at Rest

> ℹ️ **Impact: Informational**

OpenDQV stores tokens, webhook registrations, contract history, and federation events in a SQLite file (`opendqv.db`). This file is not encrypted at rest. Access to the host filesystem is equivalent to access to all stored metadata.

**Mitigation:** Use filesystem-level encryption (LUKS, dm-crypt) if the host stores sensitive metadata. The validation payloads themselves are never persisted.

### 5. TRUST_PROXY_HEADERS — IP Spoofing Risk if Misconfigured

> ⚠️ **Impact: Medium** (defeats per-IP rate limiting)

Setting `TRUST_PROXY_HEADERS=true` without a trusted reverse proxy allows clients to inject arbitrary `X-Forwarded-For` headers, defeating per-IP rate limiting. See [Running Behind a Reverse Proxy](#running-behind-a-reverse-proxy) in README.md.

---

### 6. TRACE_LOG Hash Chain — Tamper-Detectable, Not Tamper-Proof Without HMAC

> ⚠️ **Impact: Medium** (relevant for 21 CFR Part 11, ISO 27001 deployments)

The default SHA-256 hash chain in TRACE_LOG detects truncation and reordering but does not prevent complete reconstruction by an attacker who can write to the log file. An adversary with filesystem access could delete the log and regenerate a valid chain.

**Mitigation:** Set `OPENDQV_TRACE_HMAC_KEY` to a cryptographically random secret. When set, each log entry is additionally signed with HMAC-SHA256. An attacker without the key cannot forge valid entries. For 21 CFR Part 11 regulated environments, write the TRACE_LOG to WORM storage (S3 Object Lock, Azure Immutable Blob) or a write-once append-only service.

**Runtime behaviour:**

| Setting | Behaviour |
|---|---|
| `OPENDQV_TRACE_LOG` not set | TRACE_LOG fully disabled. No file created. No I/O overhead. |
| `OPENDQV_TRACE_LOG=true` (no HMAC key) | Entries written with hash-chain only. **Startup WARNING emitted** to surface the limitation. |
| `OPENDQV_TRACE_LOG=true` + `OPENDQV_TRACE_HMAC_KEY` set | Entries written with hash-chain **and** HMAC-SHA256 signature. |
| Write failure | Logged at ERROR level; validation is **not** interrupted. |

To disable TRACE_LOG at runtime without restarting: unset `OPENDQV_TRACE_LOG` and send a SIGHUP to the worker (or restart the container). New validation calls will immediately stop writing entries.

### 7. ReDoS — Regex Denial of Service via Pathological Patterns

> ⚠️ **Impact: High** (exploitable by a contract author with write access to contracts directory)

**Fixed in current release.** Validation now uses the `regex` library (drop-in `re` replacement) with a configurable per-pattern timeout (`OPENDQV_REGEX_TIMEOUT`, default: 0.5 seconds). A pattern that would cause catastrophic backtracking is interrupted and treated as a non-match. A `regex_timeout` warning is logged.

Analogous CVE: Pydantic CVE-2024-3772 (ReDoS via crafted email strings).

**Mitigation:** Keep `regex>=2024.0.0` in your dependency set. Monitor logs for `regex_timeout` warnings — these indicate contract patterns that should be reviewed for catastrophic backtracking.

### 8. Path Traversal in lookup_file (Local Paths)

> ⚠️ **Impact: High** (exploitable by a contract author with write access to contracts directory)

**Fixed in current release.** The `lookup_file` field in contract rules now resolves all paths via `pathlib.Path.resolve()` and verifies the resolved path lies within the configured `OPENDQV_CONTRACTS_DIR`. Traversal attempts (e.g. `../../etc/passwd`) raise a `ValueError` and the rule fails closed.

Analogous CVE: Pydantic-AI GHSA-wjp5-868j-wqv7 (path traversal via CDN URL).

---

## Security Hardening (What Is Already Protected)

- **ReDoS protection**: The `regex` library enforces a per-pattern timeout (`OPENDQV_REGEX_TIMEOUT`). Pathological patterns are interrupted, not queued indefinitely.
- **Path traversal**: `lookup_file` local paths are canonicalized via `pathlib.resolve()` and restricted to `OPENDQV_CONTRACTS_DIR`.
- **Webhook SSRF**: RFC 1918, loopback, and cloud metadata IP ranges are blocked at webhook registration (`_validate_webhook_url()` in `core/webhooks.py`)
- **Batch OOM**: `POST /validate/batch` rejects requests exceeding `MAX_BATCH_ROWS` (default: 10,000) with HTTP 400
- **SSE connection exhaustion**: `GET /federation/events` enforces `MAX_SSE_CONNECTIONS` (default: 50 per worker) with HTTP 429
- **Regex compilation**: Rules compile regex patterns once at load time (`compiled_pattern` on `Rule` model), not on every request
- **YAML parsing**: All YAML is parsed with `yaml.safe_load()` — arbitrary Python object instantiation via YAML tags is not possible
- **Sensitive fields**: Contract-level `sensitive_fields` suppresses field values from TRACE_LOG, error responses, and the `/explain` endpoint
- **Graceful shutdown**: In-flight heartbeat counts are flushed on SIGTERM via lifespan context manager
- **Proxy headers**: `TRUST_PROXY_HEADERS` flag is `false` by default; `ProxyHeadersMiddleware` is not active unless explicitly enabled

---

## Dependency Provenance

Dependencies are specified in `requirements.txt`. `pip-audit` runs in CI on every push and pull request (see `.github/workflows/ci.yml` — "Security audit" step) to detect known CVEs in the dependency tree. The audit result is visible in each GitHub Actions run. An SBOM (Software Bill of Materials) is generated with each release.

---

## Mandatory Deployment Checklist

Before any deployment that will handle real financial data or be accessible to users beyond the deploying engineer, verify every item below. Call `GET /health` and confirm the response values.

### Environment

- [ ] `AUTH_MODE=token` — confirm `/health?` detail enabled response contains `"auth_mode": "token"`
- [ ] `MAKER_CHECKER_ENFORCED` — confirm detail-enabled `/health` response contains `"maker_checker_enforced": true`
- [ ] `SECRET_KEY` is set to a cryptographically random value (minimum 32 characters, not the default)
- [ ] `UI_ACCESS_TOKEN` is set to a strong shared secret (minimum 32 characters) in `.env`
- [ ] `DATABASE_URL` is not pointing to a SQLite file if this is a production-scale deployment
- [ ] `OPENDQV_NODE_ID` is set to a unique identifier for this node (required for multi-node deployments)
- [ ] `OPENDQV_HEALTH_DETAIL` is **not** set to `true` unless `/health` is protected behind network controls or authenticated reverse proxy (default `false` reveals only `status` + `node_state`)

### Network

- [ ] API port (8000) is not directly exposed to the public internet; sits behind a reverse proxy or VPN
- [ ] UI port (8501) is bound to `127.0.0.1` (default in `docker-compose.yml`) or behind an authenticated reverse proxy
- [ ] TLS is terminated at the load balancer or reverse proxy — HTTP-only communication is not acceptable for production

### Compose file

- [ ] Using `docker-compose.yml` (base) or `docker-compose.yml -f docker-compose.prod.yml` — **not** `docker-compose.dev.yml`
- [ ] Confirm no live source mounts are active: `docker inspect <api_container> | grep Mounts` should show only `db-data` and `contracts/`

### Verification smoke tests

- [ ] `POST /api/v1/contracts/customer/status?status=active` with a writer credential returns HTTP 403
- [ ] `POST /api/v1/contracts/customer/version?new_version=99.0` with a writer credential returns HTTP 403
- [ ] `POST /api/v1/validate` with a valid record and a known contract returns HTTP 200 with `valid: true`
- [ ] A log line for the validation above contains `trace_id=`, `caller=`, `ip=`, `record_id=`, `contract=`, `valid=`

### Batch audit check (if batch validation is in scope)

- [ ] `POST /api/v1/validate/batch` log line contains `caller=` identifying the submitting service account

This checklist is required as a deployment sign-off artefact for any regulated financial services deployment. Retain a copy with the deployment runbook.

---

## Contact

- Security reports: opendqv@bgmsconsultants.com
- General issues: https://github.com/opendqv/OpenDQV/issues
- Discussions: https://github.com/opendqv/OpenDQV/discussions
