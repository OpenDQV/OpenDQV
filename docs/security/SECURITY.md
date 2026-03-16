# OpenDQV — Security Overview

**Version:** 1.0.0 | **Last updated:** 2026-03-16

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
| `approver` | Approve / reject contract state transitions |
| `editor` | Create and modify contracts and rules |
| `validator` | Submit validation requests and run validations (source systems — Salesforce, SAP, etc.) |
| `auditor` | Read-only access to audit logs and validation history |
| `reader` | Read-only access to contracts and rules |

Roles are embedded in the JWT payload at token creation time. Elevation requires re-issuing a token with admin privileges.

---

## Known limitations

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

---

## Reporting vulnerabilities

To report a security issue, follow the triage and disclosure process documented in:

[`docs/security/vulnerability_response_playbook.md`](vulnerability_response_playbook.md)

Please do not open public GitHub issues for security vulnerabilities. Use the private disclosure process described in the playbook.
