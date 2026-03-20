# OpenDQV Deployment Hardening Guide

This guide covers production hardening for OpenDQV. Work through each section before exposing the service to external traffic or handling regulated data.

---

## 1. Reverse Proxy Configuration

OpenDQV should never be exposed directly to the internet. Deploy it behind a reverse proxy (nginx, Caddy, or a cloud load balancer).

### Recommended nginx config

```nginx
server {
    listen 443 ssl;
    server_name opendqv.example.com;

    # TLS — see section 2
    ssl_certificate     /etc/ssl/certs/opendqv.crt;
    ssl_certificate_key /etc/ssl/private/opendqv.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Rate limiting (upstream enforcement — more reliable than in-process)
    limit_req_zone $binary_remote_addr zone=opendqv:10m rate=100r/m;
    limit_req zone=opendqv burst=20 nodelay;

    location /api/ {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Prevent request smuggling
        proxy_http_version 1.1;
        proxy_set_header   Connection "";

        # Size limits
        client_max_body_size 12m;  # slightly above OPENDQV_MAX_UPLOAD_MB
    }

    # Block direct access to internal endpoints from outside
    location /api/v1/admin/ {
        deny all;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name opendqv.example.com;
    return 301 https://$host$request_uri;
}
```

If your proxy sets `X-Forwarded-For`, set `TRUST_PROXY_HEADERS=true` in the OpenDQV environment. **Do not set this without a trusted proxy in front.**

---

## 2. TLS Configuration

- Terminate TLS at the reverse proxy or load balancer.
- Minimum TLS 1.2; TLS 1.3 preferred.
- Use certificates from a trusted CA (Let's Encrypt via certbot for internal deployments, ACM/DigiCert for production).
- HTTP-only communication is not acceptable for any deployment handling real data.

### Docker Compose (production profile)

```yaml
services:
  api:
    environment:
      - OPENDQV_BASE_URL=https://opendqv.example.com
```

---

## 3. AUTH_MODE

| Deployment Type | Required Setting |
|---|---|
| Local development / air-gapped | `AUTH_MODE=open` (acceptable — no external access) |
| Internal tooling (VPN-only) | `AUTH_MODE=token` (recommended) |
| Internet-facing / regulated | `AUTH_MODE=token` (mandatory) |
| Financial services (BCBS 239, FCA) | `AUTH_MODE=token` + maker-checker enforced |

Always confirm via `/health`:

```json
{"auth_mode": "token", "maker_checker_enforced": true}
```

---

## 4. NTP Clock Synchronisation

OpenDQV records all audit timestamps as UTC and performs an NTP check at startup. Without NTP, `opendqv audit-verify` will report clock status as `unavailable` and auditors cannot trust timestamp accuracy.

**Configure NTP on the host before deploying to production.**

### Linux (systemd-timesyncd — most distros)

```bash
timedatectl set-ntp true
timedatectl status   # confirm: "NTP service: active" and "System clock synchronized: yes"
```

### Linux (chrony — recommended for stricter environments)

```bash
apt-get install -y chrony          # or: yum install chrony
systemctl enable --now chronyd
chronyc tracking                   # verify: "System time" offset < 1s
```

### Cloud provider NTP endpoints

| Provider | NTP endpoint |
|----------|-------------|
| AWS | `169.254.169.123` |
| GCP | `metadata.google.internal` |
| Azure | `time.windows.com` |

### Docker containers

Use `--privileged` flag or ensure the host NTP is synchronised — containers inherit the host clock. There is no per-container NTP; clock accuracy is the host's responsibility.

### Verification

After deploying with NTP active, restart OpenDQV and run:

```bash
opendqv audit-verify
```

The Clock Synchronization section should show `✓ synced` with skew under a few hundred milliseconds. Skew consistently above 5 seconds indicates a misconfigured or unreachable NTP source.

---

## 5. TRACE_LOG WORM Storage Setup

For 21 CFR Part 11 or ISO 27001 compliance, write the trace log to write-once read-many (WORM) storage.

### AWS S3 Object Lock

```bash
# Create bucket with Object Lock enabled
aws s3api create-bucket \
  --bucket opendqv-trace-logs \
  --region eu-west-1 \
  --object-lock-enabled-for-bucket

# Set default retention (COMPLIANCE mode, 7 years)
aws s3api put-object-lock-configuration \
  --bucket opendqv-trace-logs \
  --object-lock-configuration '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE","Years":7}}}'
```

Mount the log path to an S3-backed FUSE filesystem or use a sidecar that streams the log to S3 in real-time.

### Azure Immutable Blob Storage

Enable Immutable Blob Storage with a time-based retention policy on the container where trace logs are stored.

### Kubernetes (recommended)

Use a sidecar container that tails `opendqv_trace.jsonl` and streams to a WORM-capable log sink (AWS CloudWatch Logs with retention + S3 archival, Splunk with immutable index, etc.).

---

## 5. HMAC Key Management

The `OPENDQV_TRACE_HMAC_KEY` secret is used to sign every TRACE_LOG entry. Protect it accordingly.

### Initial setup

```bash
# Generate a cryptographically random 32-byte key
export OPENDQV_TRACE_HMAC_KEY=$(openssl rand -hex 32)
```

Store in your secrets manager (AWS Secrets Manager, HashiCorp Vault, Kubernetes Secret):

```bash
# Kubernetes
kubectl create secret generic opendqv-hmac \
  --from-literal=OPENDQV_TRACE_HMAC_KEY=$(openssl rand -hex 32)
```

### Key rotation procedure

1. Archive the existing trace log to long-term WORM storage with a label indicating the key version used.
2. Update the `OPENDQV_TRACE_HMAC_KEY` secret in your secrets manager.
3. Restart the OpenDQV pods/containers — new entries will use the new key.
4. Record the old key in an offline key archive (needed to verify historical log entries).

**Do not reuse HMAC keys.** Each key rotation should use a freshly generated secret.

---

## 6. Monitoring Alerts

Set up alerts for the following log patterns. These indicate active attacks or misconfigurations requiring immediate investigation.

### Critical alerts (page on-call)

| Log pattern | Meaning | Action |
|---|---|---|
| `path traversal rejected` | Someone tried to read a file outside CONTRACTS_DIR via `lookup_file` | Audit contract files; rotate credentials |
| `HMAC mismatch at entry` | Trace log entry has been tampered | Preserve log for forensics; incident response |
| `entry_hash mismatch at entry` | Trace log hash chain broken | Preserve log for forensics; incident response |

### Warning alerts (ticket to security team)

| Log pattern | Meaning | Action |
|---|---|---|
| `regex_timeout` | A contract rule hit the ReDoS timeout | Review the pattern; consider rewriting |
| `Webhook URL targets a private/reserved IP` | SSRF attempt via webhook registration | Review who registered the webhook |
| `webhook delivery failed` | Outbound webhook delivery failing | Check webhook URL; may indicate DNS issue |
| `TRACE_LOG is enabled but OPENDQV_TRACE_HMAC_KEY is not set` | HMAC signing disabled | Set the key before next deployment |
| `SECRET_KEY is set to the default insecure value` | JWT signing key is the well-known default — all tokens can be forged | Set `SECRET_KEY` to a random 32-byte hex string immediately |

### Elasticsearch / Splunk query examples

```
# Grafana Loki
{app="opendqv"} |= "path traversal rejected"

# Splunk
index=opendqv "path traversal rejected" | stats count by host

# CloudWatch Insights
fields @timestamp, @message
| filter @message like /path traversal rejected/
| sort @timestamp desc
```

---

## 7. Performance Baseline (Security Features Active)

The following are the **definitive reference benchmarks** — measured with all security features fully enabled: `regex` library active (ReDoS protection on), `RATE_LIMIT_VALIDATE=off` / `RATE_LIMIT_DEFAULT=off` (as recommended for reverse-proxy deployments), fresh DB on every run, 50-request warm-up excluded from results.

| Run | Requests | Throughput | p50 | p90 | p99 | p99.9 | Errors |
|-----|----------|-----------|-----|-----|-----|-------|--------|
| 1 min | 11,692 | **194.9 req/s** | 4.16 ms | 8.40 ms | 13.99 ms | 20.15 ms | 0 |
| 5 min | 60,887 | **203.0 req/s** | 3.87 ms | 8.21 ms | 13.34 ms | 19.78 ms | 0 |
| 10 min | 119,545 | **199.2 req/s** | 4.00 ms | 8.27 ms | 13.46 ms | 20.40 ms | 0 |

**Sustained throughput: ~199 req/s. p50 ~4 ms, p99 ~14 ms. Zero errors across all runs.**

For capacity planning use **~199 req/s** as the single-worker ceiling with security enabled. All previously published figures (~208 req/s, 176 req/s) used either the unprotected `re` fallback or inconsistent rate-limit config — discard those.

**With app-level rate limiting enabled:** expect ~14% reduction (~171 req/s). For deployments handling >150 req/s, disable app-level limiting (`RATE_LIMIT_VALIDATE=off`) and enforce rate limits at the reverse proxy instead.

---

## 9. Multi-Worker Rate Limit Workaround

The in-process rate limiter (`slowapi`) maintains per-worker counters. With `WEB_CONCURRENCY=4`, the effective rate is 4× the configured value.

**For regulated deployments where rate limits must be strictly enforced:**

**Option A: Reverse proxy rate limiting (recommended)**

Configure your nginx/Caddy to enforce rate limits before requests reach OpenDQV:

```nginx
limit_req_zone $binary_remote_addr zone=opendqv_api:10m rate=100r/m;
```

**Option B: Redis-backed rate limiter**

```bash
RATE_LIMIT_BACKEND=redis
REDIS_URL=redis://redis:6379/0
```

This shares counters across all workers, giving accurate per-IP rates at the cost of a Redis dependency.

**Option C: Single worker**

```bash
WEB_CONCURRENCY=1
```

Accurate rate limiting but reduced throughput. Acceptable for low-traffic internal deployments.

---

## 10. Docker Container Hardening

```yaml
# docker-compose.prod.yml
services:
  api:
    read_only: true                          # Read-only root filesystem
    tmpfs:
      - /tmp                                 # Allow temp writes
    security_opt:
      - no-new-privileges:true              # Prevent privilege escalation
    cap_drop:
      - ALL                                  # Drop all Linux capabilities
    cap_add:
      - NET_BIND_SERVICE                    # Only re-add what's needed
    user: "1000:1000"                        # Run as non-root
    environment:
      - AUTH_MODE=token
      - OPENDQV_TRACE_HMAC_KEY_FILE=/run/secrets/hmac_key
    secrets:
      - hmac_key

secrets:
  hmac_key:
    external: true
```

---

## 11. Environment Variable Reference (Hardening-Relevant)

| Variable | Secure Value | Notes |
|---|---|---|
| `AUTH_MODE` | `token` | Never use `open` in production |
| `SECRET_KEY` | 32+ random chars | Use `openssl rand -hex 32`. **A startup WARNING is emitted if the default value is detected.** |
| `OPENDQV_TRACE_HMAC_KEY` | 32+ random chars | Required for tamper-proof logs |
| `OPENDQV_TRACE_LOG` | `true` | Enable for regulated environments |
| `OPENDQV_TRACE_LOG_PATH` | `/var/log/opendqv/trace.jsonl` | Use WORM-backed path |
| `OPENDQV_MAX_UPLOAD_MB` | `10` (default) | Reduce for tighter resource control |
| `TRUST_PROXY_HEADERS` | `true` only behind trusted proxy | Never set without a proxy |
| `WEB_CONCURRENCY` | `1` or use Redis backend | See rate limiting section |
| `OPENDQV_REGEX_TIMEOUT` | `0.5` (default) | Lower for tighter CPU protection |
| `OPENDQV_EXPLAIN_PUBLIC` | `false` (default) | Only set `true` for public contract browsers |

---

## 12. Container Image Scanning

Container images should be scanned with **Trivy** before deployment. A `container-scan` CI job is planned but not yet automated — run Trivy locally or in your deployment pipeline until the CI job is added. When configured, results should be uploaded to the GitHub Security tab as SARIF. CRITICAL findings with a fix should block the build; HIGH findings without an upstream fix are surfaced as warnings only (`--ignore-unfixed`).

### Baseline scan — 2026-03-10

First scan of `opendqv:ci` (base: `python:3.11-slim`) identified two HIGH findings, both resolved before merge:

| CVE | Package | Installed | Fixed | Root cause | Resolution |
|---|---|---|---|---|---|
| CVE-2026-23949 | `jaraco.context` | 5.3.0 | 6.1.0 | Path traversal via malicious tar archives | Upgraded `setuptools>=82.0.0`; setuptools vendors its own copies in `_vendor/` — upgrading the top-level package alone is insufficient |
| CVE-2026-24049 | `wheel` | 0.45.1 | 0.46.2 | Privilege escalation via malicious wheel file | Same — resolved by upgrading `setuptools>=82.0.0` which vendors `wheel 0.46.3` |

Both CVEs affect `setuptools/_vendor/` — internal copies used only during `pip install`. They are not reachable at runtime. The fix is to pre-pin `setuptools>=82.0.0` in the Dockerfile before `pip install -r requirements.txt` runs.

Post-fix scan result: **0 CRITICAL, 0 HIGH** across all packages and OS layers.

### Keeping the image clean

- When the `container-scan` CI job is added, it should run on every push to `main` and `develop` and on all PRs to `main`.
- When a new HIGH/CRITICAL finding appears: check `--ignore-unfixed` status. If a fix exists, bump the relevant package in `requirements.txt` or the Dockerfile pre-pin line and rebuild.
- `python:3.11-slim` OS-layer findings (Debian packages) with no upstream fix are automatically excluded by `--ignore-unfixed`. Monitor these periodically — bump the base image tag when a patched slim image is released.
