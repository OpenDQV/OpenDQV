# OpenDQV — Security Overview

**Version:** 1.0.0 | **Last updated:** 2026-03-25

---

## Authentication modes

OpenDQV supports two authentication modes, configured via the `AUTH_MODE` environment variable:

| Mode | Use case | Notes |
|------|----------|-------|
| `AUTH_MODE=open` | Local development, sandboxed demo | No token required. **Never use in production.** |
| `AUTH_MODE=token` | Staging and production | JWT Personal Access Tokens (PATs) required on all endpoints. |

Set `AUTH_MODE=token` in all production deployments. The default is `open` to reduce friction during local setup.

---

## RBAC roles

When `AUTH_MODE=token` is active, every request is authenticated and authorized against one of seven roles:

| Role | Typical capabilities |
|------|---------------------|
| `admin` | Full access: manage tokens, users, contracts, rules, system config |
| `approver` | Approve / reject contract state transitions; access audit chain |
| `editor` | Create and modify DRAFT contracts and rules; register/remove webhooks; import contracts |
| `validator` | Submit validation requests (source systems — Salesforce, SAP, etc.); read contracts |
| `auditor` | Read contracts; submit validation requests; access audit chain (`GET /trace/verify`) |
| `reader` | Read contracts; submit validation requests. Semantically distinct from `validator` — use `reader` for dashboards and human consumers, `validator` for automated source systems |

**Note on validation access:** all authenticated roles can call `/validate`. The distinction between `validator` and `reader` is semantic (source system identity vs. human consumer), not functional.

**Role-restricted endpoints:**
- `POST /contracts/reload` — `admin` only
- `POST /import/*` — `editor`, `admin`
- `POST /webhooks`, `DELETE /webhooks` — `editor`, `admin`
- `GET /trace/verify` — `auditor`, `approver`, `admin`
- `POST /contracts/{name}/{version}/approve`, `reject`, `submit-review` — `approver`/`editor` + `admin`

Roles are embedded in the JWT payload at token creation time. Elevation requires re-issuing a token with admin privileges.

---

## Known limitations

### `passlib` — `crypt` deprecation warning on Python 3.11+

**Severity:** None (warning only, no functional impact)

OpenDQV uses `passlib[bcrypt]` for password hashing. Passlib 1.7.4 (the latest release) unconditionally imports Python's built-in `crypt` module at load time, even when the bcrypt scheme is the only one configured. Python deprecated `crypt` in 3.11 and removed it in 3.13.

**Impact:** A `DeprecationWarning` appears in test output on Python 3.11. On Python 3.13, passlib's unconditional `crypt` import raises an `ImportError` at startup — use Python 3.11 or 3.12 for production deployments until this is resolved upstream.

**Root cause:** Passlib has been effectively unmaintained since 2020 and the fix has not been released. This is a known community issue.

**Mitigation:** OpenDQV's `CryptContext` is configured with `schemes=["bcrypt"]` only — the `crypt` code path is never invoked at runtime. The warning is cosmetic. A drop-in replacement (`passlib` fork or migration to `bcrypt` directly) is planned for a future release.

---

### `POST /tokens/revoke` — no ownership check

**Severity:** Low (disruptive, not privilege-escalating)

Any authenticated user can revoke any token by value via `POST /tokens/revoke`. There is no check that the token being revoked belongs to the caller.

**Impact:** A user with a valid token can revoke another user's active session. This is disruptive but does not grant elevated privileges — the attacker gains nothing except the ability to force re-authentication.

**Mitigation:**
- Use `AUTH_MODE=token` to ensure all revocation requests are authenticated.
- Monitor token revocation events in your audit log for anomalous patterns.
- For admin-scoped revocation (revoke all tokens for a user), use `POST /tokens/revoke/{username}`, which correctly enforces the `admin` role.

**Remediation plan:** A token ownership check (compare token subject claim against caller identity) is planned for a future release.

---

### `POST /tokens/revoke/{username}` — admin-only (enforced)

Revokes all tokens for a given username. Requires the `admin` role. This endpoint is correctly guarded.

---

## Security controls summary

| ID | Control | Detail |
|----|---------|--------|
| SEC-001 | ReDoS timeout | All regex rules evaluated via the `regex` library with a 0.5 s per-pattern timeout (configurable: `OPENDQV_REGEX_TIMEOUT`). Prevents catastrophic backtracking. |
| SEC-002 | Path traversal — contract files | `pathlib.resolve()` + containment check against `config.CONTRACTS_DIR`. Rejects any path that resolves outside the contracts directory. |
| SEC-003 | Authentication | JWT PATs with configurable expiry. `AUTH_MODE=open` disables auth for local development only. |
| SEC-004 | SQL injection — field names | DuckDB batch queries use `$param` binding for all user-supplied values. Field names are validated against the contract schema before use. |
| SEC-005 | RBAC enforcement | Role claims are verified on every authenticated request. No role elevation is possible without re-issuing a token. |
| SEC-006 | Path traversal — importers | Importer endpoints resolve file paths and verify containment before reading. |
| SEC-007 | Input size limits | Request body size is capped. Batch validation payloads are bounded by `OPENDQV_MAX_BATCH_SIZE`. |
| SEC-008 | Webhook SSRF protection | Outbound webhook URLs are validated against a blocklist: RFC 1918 private ranges, loopback (`127.0.0.0/8`, `::1`), and link-local (`169.254.0.0/16`, `fe80::/10`) are all rejected. |
| SEC-009 | Token role whitelist | `/tokens/generate` validates the `role` parameter against an enumerated set (`validator`, `reader`, `auditor`, `editor`, `approver`, `admin`). Unknown roles return HTTP 422. Prevents phantom roles from appearing in the audit log. |

---

## Audit Log Timestamp Integrity

OpenDQV's audit chain (`contract_history`) proves **sequence and immutability** — each entry's hash commits to the full history before it. It does **not** independently prove that the system clock was accurate when entries were written.

To address this, OpenDQV performs an NTP check at service startup and records the result in the node health log. The `opendqv audit-verify` command surfaces this data alongside chain integrity output:

```
Clock Synchronization
────────────────────────────────────────────────────────────
  Startup 2026-03-19T14:19:11+00:00  ✓ synced  skew=45ms  source=pool.ntp.org
```

If the NTP check detects significant skew (> 5 seconds) or cannot reach an NTP server, `audit-verify` emits a warning:

```
  WARNING: Clock skew or NTP unavailability detected at one or more startups.
           Audit timestamps may be unreliable.
```

**For production deployments:** ensure the host is synchronised to a reliable NTP source. See [`hardening.md`](hardening.md) for configuration requirements.

**Enterprise and regulated environments:** RFC 3161 trusted timestamp anchoring — cryptographic proof of timestamp accuracy from a trusted timestamp authority (TSA) — is the correct upgrade for environments with FCA, SOX, or GDPR audit obligations. This is available in the commercial offering.

---

## Supply chain security

### Dependency philosophy

OpenDQV's dependency tree is kept deliberately small. Every production dependency was chosen to solve a specific, well-scoped problem. The project does not depend on AI/ML meta-frameworks, language model clients (no LiteLLM, no OpenAI SDK, no LangChain), or packages that make outbound network connections at import or startup time.

This is a conscious production-readiness decision. Packages with large transitive dependency trees or packages that operate in immature ecosystems increase the attack surface for supply chain compromise — the vector exploited in attacks such as the LiteLLM `.pth` file credential exfiltration incident (March 2026).

### Production dependency rationale

| Package | Purpose | Supply chain notes |
|---|---|---|
| `fastapi`, `starlette`, `uvicorn`, `gunicorn` | Web framework and ASGI server | Widely audited; no outbound network calls at import |
| `pydantic` | Schema validation and contract model | No network access; deterministic |
| `pyyaml` | YAML parsing | Used via `safe_load()` only throughout the codebase — arbitrary code execution via YAML tags is not possible |
| `pandas` | Batch record processing | Large transitive tree (numpy, etc.); well-audited; no network calls at import |
| `duckdb` | In-process SQL engine for batch validation | Self-contained binary; no external connections |
| `PyJWT`, `cryptography`, `passlib[bcrypt]` | Authentication and token signing | Mature, narrow-scope crypto libraries |
| `urllib3`, `httpx`, `requests` | HTTP clients for webhook delivery and SDK | Standard libraries; no background threads or startup callbacks |
| `slowapi`, `prometheus-client` | Rate limiting and metrics | Narrow-scope; no outbound connections |
| `strawberry-graphql` | GraphQL schema layer | Version-pinned tightly in `pyproject.toml` |
| `regex` | ReDoS-safe regex evaluation | Drop-in `re` replacement; no network access; used specifically for SEC-001 |
| `rich`, `questionary` | CLI display | No network access |
| `mcp` | MCP server protocol (optional) | Newer ecosystem — see note below |

### What is not in this codebase

The following categories of packages are explicitly absent:

- **LLM/AI client libraries** — no LiteLLM, LangChain, OpenAI SDK, Anthropic SDK, or similar
- **Model serving frameworks** — no Triton, vLLM, Ollama, or similar
- **Data pipeline orchestrators** — no Airflow, Prefect, or Dagster clients
- **Packages that execute code at import** — all production dependencies are passive at import time

### The `mcp` package

`mcp` is the newest package in the dependency tree and operates in a smaller ecosystem than the others. It is optional — required only if you run `mcp_server.py`. For production deployments that do not use the MCP integration, install without it:

```bash
pip install opendqv        # no MCP
pip install opendqv[mcp]   # with MCP
```

If you do use MCP, pin `mcp` to an exact version in your deployment rather than a range, and review the release changelog when upgrading.

### Verifying your installation

**Audit for known CVEs:**

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

**Generate a hash-pinned lockfile** (prevents tampered packages from being installed silently):

```bash
pip install pip-tools
pip-compile requirements.txt --generate-hashes -o requirements.lock
pip install --require-hashes -r requirements.lock
```

**Scan the container image** for OS-layer and package CVEs before deployment (see [hardening.md — Container Image Scanning](hardening.md)):

```bash
trivy image opendqv:latest --ignore-unfixed --exit-code 1 --severity CRITICAL,HIGH
```

### If you are responding to a supply chain incident

If a package in the Python ecosystem you depend on has been reported as compromised:

1. Check whether OpenDQV lists it as a dependency: `grep -i <package-name> requirements.txt requirements-dev.txt`
2. If present, rotate any API keys or secrets that the OpenDQV process had access to on affected hosts
3. Rebuild the container image from scratch (`docker compose build --no-cache api`) — do not rely on layer caching
4. Run `pip-audit` against the rebuilt image to confirm the compromised version is not present
5. File an issue at the repository so the maintainers can assess and pin away from the affected version

---

## Reporting vulnerabilities

To report a security issue, follow the triage and disclosure process documented in:

[`docs/security/vulnerability_response_playbook.md`](vulnerability_response_playbook.md)

Please do not open public GitHub issues for security vulnerabilities. Use the private disclosure process described in the playbook.
