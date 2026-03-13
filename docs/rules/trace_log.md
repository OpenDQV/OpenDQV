# TRACE_LOG — Per-Record Tamper-Evident Validation Audit Log

TRACE_LOG writes a tamper-evident, per-record JSON audit trail for every call to `validate_record()` and `validate_batch()`. It is designed for regulated environments that require a complete, verifiable record of every validation decision.

---

## How to Enable

Set the environment variable before starting the server or running the CLI:

```bash
export OPENDQV_TRACE_LOG=true
```

Accepted values: `true`, `1`, `yes` (case-insensitive). Any other value (or the variable being absent) disables the log.

### Log File Location

The default log path is `opendqv_trace.jsonl` in the current working directory. Override with:

```bash
export OPENDQV_TRACE_LOG_PATH=/var/log/opendqv/trace.jsonl
```

The file is appended to on each run. It is safe to rotate externally; on next startup the in-memory hash chain state is reset and a new genesis entry begins.

---

## What Gets Logged

Each validated record produces exactly one JSON line. The schema is:

| Field | Type | Description |
|---|---|---|
| `ts` | string (ISO-8601 UTC) | Timestamp of the validation call |
| `contract` | string | Name of the data contract used |
| `context` | string | Context applied (`"default"` if none) |
| `record_index` | integer | Position of the record in the batch (0 for single-record calls) |
| `valid` | boolean | `true` if no errors were raised |
| `error_count` | integer | Number of ERROR-severity rule failures |
| `warning_count` | integer | Number of WARNING-severity rule failures |
| `fields_validated` | array of strings | Sorted list of field names checked, **excluding** sensitive fields |
| `sensitive_fields_suppressed` | array of strings | Sorted list of sensitive field **names** that were validated but withheld from `fields_validated` |
| `failed_rules` | array of strings | Field names that triggered failures, **excluding** sensitive fields |
| `prev_hash` | string (hex SHA-256) | Hash of the previous entry in the chain (`"000...000"` for the first entry) |
| `entry_hash` | string (hex SHA-256) | SHA-256 of `prev_hash + "|" + payload` |

### What is NEVER logged

- **Field values** — the actual data being validated is never written to the trace log under any circumstances.
- **Sensitive field validation outcomes** — which sensitive fields failed validation is not recorded. Only the fact that sensitive fields were present (by name) is noted in `sensitive_fields_suppressed`.
- **HTTP Authorization headers** — Bearer tokens and other auth credentials used in HTTP lookup rules are never logged.

---

## Security

### Sensitive Field Protection

Fields listed in `contract.sensitive_fields` (e.g. `national_id`, `date_of_birth`, `salary`) are handled as follows:

- Their names are moved from `fields_validated` to `sensitive_fields_suppressed`.
- Any failure attributed to a sensitive field is removed from `failed_rules`.
- Their values are never present in the log (values are never logged for any field).

### Auth Header Protection

HTTP lookup rules support a `lookup_auth_header` config that may contain Bearer tokens resolved from environment variables. These resolved credential strings are never passed to any logger call. The `_load_http_lookup_set` function logs only the URL on failure — never the Authorization header value.

---

## Tamper Evidence: Hash Chain

Each log entry is linked to the previous entry by a SHA-256 hash chain, using the same pattern as `ContractHistory`:

```
entry_hash = SHA-256( prev_hash + "|" + json_payload )
```

The first entry uses a genesis hash of 64 zero characters as `prev_hash`. Any modification to any entry's content breaks the chain from that point onwards.

### Verify via CLI

```bash
opendqv audit-verify --trace /var/log/opendqv/trace.jsonl
```

### Verify via API

```
GET /api/v1/trace/verify
GET /api/v1/trace/verify?log_path=/var/log/opendqv/trace.jsonl
```

Requires authentication. Returns:

```json
{"valid": true, "entries": 1024}
```

Or on tamper detection:

```json
{
  "valid": false,
  "broken_at": 42,
  "entries": 42,
  "error": "entry_hash mismatch at entry 42 — log may have been tampered"
}
```

---

## Example Log Entry

```json
{
  "ts": "2026-03-09T12:00:00.123456+00:00",
  "contract": "patient_record",
  "context": "default",
  "record_index": 0,
  "valid": true,
  "error_count": 0,
  "warning_count": 0,
  "fields_validated": ["nhs_number", "postcode"],
  "sensitive_fields_suppressed": ["date_of_birth", "national_id"],
  "failed_rules": [],
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "entry_hash": "a3f2e1b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2"
}
```

---

---

## HMAC Signing (SEC-004)

By default, each TRACE_LOG entry is protected by a SHA-256 hash chain. This detects any modification, reordering, or truncation of the log — but an adversary with filesystem access could delete the log and reconstruct a valid chain from scratch.

**To prevent forgery**, set `OPENDQV_TRACE_HMAC_KEY` to a cryptographically random secret:

```bash
export OPENDQV_TRACE_HMAC_KEY=$(openssl rand -hex 32)
```

When set, every entry is additionally signed with HMAC-SHA256 over the full entry dict (excluding the `hmac` field itself). An adversary without the key cannot forge valid entries.

### HMAC Entry Format

When `OPENDQV_TRACE_HMAC_KEY` is set, each entry gains an additional field:

| Field | Type | Description |
|---|---|---|
| `hmac` | string (hex SHA-256) | HMAC-SHA256 signature of the entry dict (excluding the `hmac` field) |

### Startup Warning

If `OPENDQV_TRACE_LOG=true` is set but `OPENDQV_TRACE_HMAC_KEY` is not set, a **WARNING** is logged at startup:

```
TRACE_LOG is enabled but OPENDQV_TRACE_HMAC_KEY is not set. Entries are hash-chained
but not HMAC-signed. An adversary with filesystem access could reconstruct a valid chain.
```

This warning is intentional — it surfaces the limitation proactively in regulated environments.

### Verification with HMAC

The `verify_trace_log()` function (and `/api/v1/trace/verify` endpoint) will check HMAC signatures when `OPENDQV_TRACE_HMAC_KEY` is set:

```json
{"valid": true, "entries": 100, "hmac_verified": true, "hmac_key_present": true}
```

If the key is not set:

```json
{"valid": true, "entries": 100, "hmac_verified": false, "hmac_key_present": false}
```

### Backward Compatibility

Entries written before `OPENDQV_TRACE_HMAC_KEY` was configured do not have an `hmac` field. These **pre-HMAC entries** are accepted without HMAC verification. The `hmac_verified` flag in the result will be `false` if any entries lack HMAC signatures.

### Key Rotation

To rotate the HMAC key:

1. Archive the existing log file (it was signed with the old key)
2. Update `OPENDQV_TRACE_HMAC_KEY` to the new value
3. Restart the server — new entries will be signed with the new key
4. The old log remains verifiable using the old key value

---

## Runtime Behaviour of OPENDQV_TRACE_LOG

| Condition | Behaviour |
|---|---|
| `OPENDQV_TRACE_LOG` not set or empty | TRACE_LOG is fully disabled. No file is created. No I/O overhead. |
| `OPENDQV_TRACE_LOG=true` (no HMAC key) | Entries are written with hash-chain only. Startup WARNING emitted. |
| `OPENDQV_TRACE_LOG=true` + `OPENDQV_TRACE_HMAC_KEY` set | Entries are written with hash-chain **and** HMAC signature. |
| `OPENDQV_TRACE_LOG=true`, write fails | Error is logged but validation is **not** interrupted. |
| Log file does not exist at verify time | Returns `{"valid": true, "entries": 0}` — not an error. |

The TRACE_LOG subsystem never throws exceptions to the calling validation code. A write failure is logged at ERROR level but the validation result is returned normally.

---

## Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `OPENDQV_TRACE_LOG` | _(unset)_ | Set to `true` to enable TRACE_LOG |
| `OPENDQV_TRACE_LOG_PATH` | `opendqv_trace.jsonl` | Path to the trace log file |
| `OPENDQV_TRACE_HMAC_KEY` | _(unset)_ | HMAC-SHA256 signing key. Set for 21 CFR Part 11 / ISO 27001 deployments. |
