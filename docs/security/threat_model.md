# OpenDQV Threat Model — STRIDE Analysis

This document covers the seven primary attack surfaces identified during the initial security review. For each surface, the STRIDE category, current mitigation, residual risk, and recommended control are listed.

---

## Summary Table

| # | Attack Surface | STRIDE | Mitigated? | Residual Risk |
|---|---|---|---|---|
| 1 | Regex validation (ReDoS) | Denial of Service | Yes | Low |
| 2 | HTTP lookup fetching (SSRF) | SSRF / Info Disclosure | Yes + DNS rebinding fixed | Low |
| 3 | File path traversal in lookup_file | Elevation of Privilege | Yes | Low |
| 4 | Batch endpoint resource exhaustion | Denial of Service | Partial | Medium |
| 5 | TRACE_LOG information disclosure | Info Disclosure | Yes (HMAC optional) | Low–Medium |
| 6 | Contract YAML deserialization | Tampering / EoP | Yes | Low |
| 7 | API authentication and rate limiting | Spoofing / DoS | Partial (multi-worker gap) | Medium |
| 8 | SQL injection via DuckDB field names | Tampering / EoP | Yes (fixed 2026-03-10) | Low |

---

## 1. Regex Validation — ReDoS

**Threat:** A contract author with write access to the contracts directory crafts a pathological regex pattern (e.g. `(a+)+b`). When the validation engine applies the pattern against user-supplied input, catastrophic backtracking causes the worker process to consume 100% CPU and become unresponsive.

**STRIDE category:** Denial of Service

**Current mitigation:**
- The `regex` library (drop-in `re` replacement) is used with a configurable per-match timeout (`OPENDQV_REGEX_TIMEOUT`, default: 0.5 seconds).
- Patterns that would cause catastrophic backtracking are interrupted and return a non-match.
- A `regex_timeout` WARNING is logged to surface affected patterns.
- Patterns are compiled once at contract load time, not on every request.
- `_HAS_REGEX_LIB` is checked at startup; `test_regex_lib_available` fails the test suite if the library is absent, preventing silent degradation.

> **Note (2026-03-10 code review):** During a post-conference code review it was confirmed that `regex` was present in `requirements.txt` but had not been installed in the development venv, leaving protection silently inactive. The fix is to ensure `pip install -r requirements.txt` is run before any deployment. The `test_regex_lib_available` test acts as a canary — a failing test suite should block deployment.

**Residual risk:** Low. An attacker must already have write access to the contracts directory (insider threat or compromised CI/CD pipeline). The timeout limits blast radius to a single-pattern delay.

**Recommended control:** Monitor for `regex_timeout` log lines and review affected contract patterns for rewriting. Consider a pre-deployment regex complexity linter in CI.

---

## 2. HTTP Lookup Fetching — SSRF

**Threat:** A contract rule with `lookup_url` pointing to an internal service (e.g. `http://10.0.0.1/admin` or the AWS metadata endpoint `http://169.254.169.254/latest/meta-data/`) causes the OpenDQV server to make outbound HTTP requests to internal infrastructure during validation.

**STRIDE categories:** Server-Side Request Forgery, Information Disclosure

**Current mitigation:**
- Webhook URL validation (`_validate_webhook_url()`) blocks RFC 1918 IP ranges, loopback, and cloud metadata endpoints.
- **DNS rebinding fix (SEC-008):** Hostnames are now DNS-resolved at registration time via `socket.getaddrinfo()`. All returned IP addresses are checked against the blocked networks. DNS resolution failure causes the URL to be rejected (fail-closed).
- Only `http` and `https` schemes are permitted.

**Residual risk:** Low. An attacker would need authenticated webhook registration access. Time-of-check-time-of-use (TOCTOU) rebinding attacks are mitigated by resolving at registration. Egress firewall rules provide defense-in-depth.

**Recommended control:** Configure host-level egress firewall rules to block outbound connections to RFC 1918 ranges from the OpenDQV container. This provides a network-layer backstop independent of the application check.

---

## 3. File Path Traversal in lookup_file

**Threat:** A contract rule specifies `lookup_file: ../../etc/passwd`, causing the validator to read arbitrary files from the host filesystem when loading lookup sets.

**STRIDE category:** Elevation of Privilege

**Current mitigation:**
- `_check_lookup_path_safe()` in `core/validator.py` resolves the path via `pathlib.Path.resolve()` and verifies it lies within `OPENDQV_CONTRACTS_DIR`.
- Traversal attempts raise `ValueError` and the rule fails closed.
- Importers (CSVW, OTel, NDC) scan generated rules for `lookup_file` paths and apply the same check (SEC-006).

**Residual risk:** Low. The check is applied at the entry point (`_load_lookup_set`) and at import time for all three importers.

**Recommended control:** Ensure `OPENDQV_CONTRACTS_DIR` resolves to a directory that does not contain sensitive files. Consider running the worker with a read-only filesystem mount except for the contracts directory.

---

## 4. Batch Endpoint Resource Exhaustion

**Threat:** An authenticated caller submits a batch validation request with the maximum allowed records (10,000 by default), each with a large number of fields, consuming excessive CPU and memory. Repeated requests could degrade service for other callers.

**STRIDE category:** Denial of Service

**Current mitigation:**
- `POST /validate/batch` rejects requests exceeding `MAX_BATCH_ROWS` (default: 10,000) with HTTP 400.
- `POST /validate/batch/file` enforces a configurable file size limit (`OPENDQV_MAX_UPLOAD_MB`, default: 10MB) with HTTP 413.
- Rate limiting (`RATE_LIMIT_VALIDATE`) limits requests per IP per minute.
- SSE connections are capped at `MAX_SSE_CONNECTIONS` per worker.

**Residual risk:** Medium. The row limit is configurable but has a default. A caller with a high rate limit allowance can still submit multiple maximum-size batches. Memory consumption scales with record size, not just count.

**Recommended control:** Set `MAX_BATCH_ROWS` conservatively for production deployments. Consider adding a maximum per-record field count or total payload size check. Use per-user rate limiting (requires Redis backend) rather than per-IP for authenticated callers.

---

## 5. TRACE_LOG Information Disclosure

**Threat:** An attacker with filesystem read access exfiltrates the TRACE_LOG file and extracts information about validation outcomes, contract names, and field names (though not field values).

**STRIDE category:** Information Disclosure

**Current mitigation:**
- Field **values** are never written to the trace log under any circumstances.
- Sensitive field **names** are moved to `sensitive_fields_suppressed`; their validation outcomes are suppressed from `failed_rules`.
- HTTP Authorization headers are never passed to any logger call.
- The hash chain detects any modification, reordering, or truncation.
- **HMAC signing (SEC-004):** When `OPENDQV_TRACE_HMAC_KEY` is set, each entry is signed with HMAC-SHA256. An attacker without the key cannot forge valid entries.
- A startup WARNING is emitted if TRACE_LOG is enabled without an HMAC key.

**Residual risk:** Low (with HMAC key set) to Medium (without). Without HMAC, an adversary with filesystem write access could reconstruct a valid chain after deleting entries. The field names and contract names visible in the log may assist reconnaissance.

**Recommended control:** Set `OPENDQV_TRACE_HMAC_KEY` to a cryptographically random 32-byte secret. Write the trace log to WORM storage (S3 Object Lock, Azure Immutable Blob Storage). Restrict filesystem read access to the log directory to the OpenDQV process user only.

---

## 6. Contract YAML Deserialization

**Threat:** A maliciously crafted YAML file in the contracts directory uses YAML tags (e.g. `!!python/object/apply:os.system`) to execute arbitrary code when loaded.

**STRIDE categories:** Tampering, Elevation of Privilege

**Current mitigation:**
- All YAML is parsed with `yaml.safe_load()` throughout the codebase. Arbitrary Python object instantiation via YAML tags is not possible with the safe loader.
- Contract files are loaded from `OPENDQV_CONTRACTS_DIR` only.

**Residual risk:** Low. The safe_load restriction eliminates the deserialization vector. Risk remains if a third-party library called during contract processing uses unsafe YAML loading.

**Recommended control:** Audit all third-party library calls that accept YAML input. Add a CI check (`grep -r "yaml.load(" --include="*.py"`) to detect any unsafe `yaml.load()` calls introduced by future contributors.

---

## 7. API Authentication and Rate Limiting

**Threat:** An unauthenticated caller accesses protected endpoints (bypassing `AUTH_MODE=token`), or a legitimate caller abuses a high rate limit allowance to exhaust resources or extract data.

**STRIDE categories:** Spoofing, Denial of Service

**Current mitigation:**
- `AUTH_MODE=token` enforces Bearer token authentication on all protected endpoints.
- `AUTH_MODE=open` is documented as unsafe for public-facing deployments and triggers a `maker_checker_enforced: false` flag in `/health`.
- Rate limiting via `slowapi` is applied to all validation and default endpoints.
- `/trace/verify` requires authentication (SEC-005).
- `/explain` is auth-gated by default; `OPENDQV_EXPLAIN_PUBLIC=true` allows unauthenticated access if explicitly configured (SEC-010).

**Residual risk:** Medium. The in-memory rate limiter has a known 4× effective rate with multiple Gunicorn workers (see SECURITY.md section 1). JWT tokens do not support revocation without a database check; revoked tokens may be accepted until expiry if the DB is unavailable.

**Recommended control:** Use `AUTH_MODE=token` for all internet-facing deployments. Deploy a reverse proxy (nginx, Caddy) with rate limiting rules upstream. Use Redis-backed rate limiting for shared-counter accuracy across workers. Set token expiry (`TOKEN_EXPIRY_DAYS`) to a short duration (e.g. 90 days) and rotate tokens regularly.

---

## 8. SQL Injection via DuckDB Field Names

*Identified and fixed: 2026-03-10 post-conference code review.*

**Threat:** The batch validation engine (`validate_batch` in `core/validator.py`) generates DuckDB SQL queries by interpolating field names from contract rules directly into f-strings. A contract rule with a field name containing a double-quote character (e.g. `email"--`) would break out of SQL identifier quoting and allow injection of arbitrary SQL into an in-memory DuckDB instance.

**STRIDE category:** Tampering, Elevation of Privilege

**Attack path:**
1. An attacker with write access to the contracts directory (or a compromised CI/CD pipeline) crafts a contract rule with `field: 'target"--'`.
2. The batch endpoint generates a query: `SELECT __idx__ FROM data WHERE "target"--" IS NULL ...`
3. The double-quote terminates the identifier; `--` comments out the remainder, altering query semantics.

**Current mitigation (fixed 2026-03-10):**
- `Rule._post_parse()` in `core/rule_parser.py` validates field names at contract parse time.
- Field names containing `"`, `\`, `;`, null bytes (`\x00`), or other control characters raise a `ValueError` and prevent the contract from loading.
- Allowed characters: letters, digits, underscore, hyphen, space, dot — all safe when double-quoted as SQL identifiers.
- 7 regression tests added in `TestFieldNameSQLInjection` in `tests/test_security.py`.

**Residual risk:** Low. The validation is applied at contract load time, before any SQL is generated. An attacker must still have write access to the contracts directory; they cannot inject via the validation API itself (records are never used as SQL identifiers).

**Recommended control:** Treat the contracts directory as a privileged asset — equivalent to application source code. Restrict write access to CI/CD pipelines with signed commits. Review contract YAML diffs in pull requests before merge.
