# Observability

> **Last reviewed:** 2026-03-17.
> Covers Prometheus metrics, the trace log, distributed tracing correlation, the in-memory stats dashboard, and Grafana starter panels.

OpenDQV exposes observability through three complementary mechanisms: a Prometheus metrics endpoint, a tamper-evident per-record trace log, and an in-memory stats dashboard available via REST. All three can be used independently.

---

## Prometheus Metrics

Metrics are exposed at `GET /metrics` in Prometheus text format. The endpoint is mounted by `instrument_app()` in `monitoring.py` and is available as soon as the API server starts.

### Scrape endpoint

```
GET /metrics
Content-Type: text/plain; version=0.0.4
```

Add the following to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: opendqv
    static_configs:
      - targets: ["localhost:8000"]
```

---

### Metrics reference

| Metric | Type | Labels | Description |
|---|---|---|---|
| `request_latency_seconds` | Histogram | `method`, `endpoint` | End-to-end request processing time in seconds, measured by the middleware for every request |
| `request_count_total` | Counter | `method`, `endpoint`, `status` | Total number of HTTP requests, labelled with the HTTP status code |
| `api_errors_total` | Counter | `method`, `endpoint` | Total number of requests that raised an unhandled exception (5xx) |
| `validation_total` | Counter | `contract`, `context`, `result` | Total validation calls; `result` is `pass` or `fail` |
| `validation_errors_total` | Counter | `contract`, `context`, `field`, `rule` | Total field-level validation errors; one increment per failing field per call |
| `validation_latency_seconds` | Histogram | `contract`, `context`, `mode` | Validation engine latency; `mode` is `single` or `batch` |
| `opendqv_litestream_last_replication_age_seconds` | Gauge | — | Seconds since the last successful Litestream replication checkpoint. Set to `-1` when Litestream is not configured. Alert if this exceeds 300 seconds (5 minutes) |

**Label notes:**

- `context` is set to `"none"` when no context override is provided.
- `mode` distinguishes single-record (`/validate`) from batch (`/validate/batch`) calls.
- The `opendqv_litestream_last_replication_age_seconds` gauge is initialised to `-1` at startup and updated by the Litestream health check if the backup integration is configured.

---

## Recommended Alert Rules

The following PromQL expressions are ready to paste into Alertmanager or Grafana alerting:

```promql
# p99 validation latency > 500 ms
histogram_quantile(0.99, rate(validation_latency_seconds_bucket[5m])) > 0.5

# Rejection rate > 10% over 5 minutes
rate(validation_total{result="fail"}[5m]) / rate(validation_total[5m]) > 0.1

# Backup replication lag > 5 minutes
opendqv_litestream_last_replication_age_seconds > 300

# API error rate > 1%
rate(api_errors_total[5m]) / rate(request_count_total[5m]) > 0.01
```

Set the backup replication alert to a severity of `warning` at 300 s and `critical` at 900 s. The `-1` sentinel value (Litestream not configured) should be excluded from the alert condition with `> 0`.

---

## Trace Log

The trace log is a per-record, tamper-evident audit trail written as newline-delimited JSON (NDJSON). It is separate from the Prometheus metrics and designed for compliance use cases (21 CFR Part 11, ISO 27001, SOC 2).

### Enabling

```bash
export OPENDQV_TRACE_LOG=true
```

Accepts `true`, `1`, or `yes`. Disabled by default.

### Log path

```bash
export OPENDQV_TRACE_LOG_PATH=opendqv_trace.jsonl   # default
```

### Log rotation

```bash
export OPENDQV_TRACE_LOG_MAX_SIZE_MB=100   # rotate when file exceeds 100 MB (default)
export OPENDQV_TRACE_LOG_ROTATE=5          # keep 5 rotated segments (default)
```

Each rotated segment is a self-contained NDJSON file verifiable independently. The hash chain resets after rotation; verifiers process each segment separately.

### Entry schema

Each entry is a single JSON object on one line:

```json
{
    "ts": "2026-03-17T10:00:00.000+00:00",
    "contract": "customer",
    "context": "default",
    "record_index": 0,
    "valid": false,
    "error_count": 2,
    "warning_count": 0,
    "fields_validated": ["age", "email", "name"],
    "sensitive_fields_suppressed": ["national_id"],
    "failed_rules": ["email"],
    "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "entry_hash": "a1b2c3...",
    "hmac": "d4e5f6..."
}
```

| Field | Description |
|---|---|
| `ts` | ISO 8601 UTC timestamp of the validation call |
| `contract` | Contract name |
| `context` | Context name, or `"default"` if none was applied |
| `record_index` | Zero-based index of the record within the batch |
| `valid` | `true` if all error-severity rules passed |
| `error_count` | Number of error-severity rule failures |
| `warning_count` | Number of warning-severity rule failures |
| `fields_validated` | Sorted list of field names that were evaluated (values are never logged) |
| `sensitive_fields_suppressed` | Fields marked sensitive; their names are noted here but excluded from `fields_validated` and `failed_rules` |
| `failed_rules` | Field names of error/warning failures, with sensitive fields redacted |
| `prev_hash` | SHA-256 hash of the previous entry (`0`×64 for the first entry in a segment) |
| `entry_hash` | SHA-256 of `prev_hash + "|" + payload` — forms the tamper-evident chain |
| `hmac` | HMAC-SHA256 of the full entry (present only when `OPENDQV_TRACE_HMAC_KEY` is set) |

**Security note:** Field *values* are never logged. Only field names and pass/fail outcomes appear. Sensitive field names are listed in `sensitive_fields_suppressed` but their individual rule outcomes are excluded from `failed_rules`.

### HMAC signing

For regulated deployments, set a cryptographically random secret to add HMAC signatures:

```bash
export OPENDQV_TRACE_HMAC_KEY="$(openssl rand -hex 32)"
```

Without an HMAC key, entries are hash-chained but an adversary with filesystem access could reconstruct a valid chain by rewriting all entries. HMAC signing prevents this. OpenDQV emits a startup warning if the trace log is enabled without an HMAC key.

### Verifying the chain

The REST API exposes a verification endpoint:

```
GET /api/v1/trace/verify
```

To verify a specific file:

```
GET /api/v1/trace/verify?path=opendqv_trace.jsonl.1
```

For full details on the trace log schema see [`docs/rules/trace_log.md`](rules/trace_log.md).

---

## X-Trace-Id Correlation

Pass a `X-Trace-Id` header on any validate request to correlate validation outcomes with your upstream distributed trace:

```bash
curl -X POST http://localhost:8000/api/v1/validate/customer \
     -H "Content-Type: application/json" \
     -H "X-Trace-Id: abc123-your-trace-id" \
     -d '{"name":"Alice","email":"alice@example.com","age":30}'
```

OpenDQV echoes the header in the response and records it in the trace log entry. If no `X-Trace-Id` is supplied, OpenDQV generates a UUID for that request and uses it as the trace identifier.

This allows you to join OpenDQV validation outcomes with traces in Jaeger, Zipkin, Datadog, or any other distributed tracing backend.

---

## In-Memory Stats Dashboard

```
GET /api/v1/stats/summary
```

Returns aggregated validation statistics accumulated since the API server started. No external database is required — statistics are held in a thread-safe in-memory structure (`ValidationStats` in `monitoring.py`).

**Response shape:**

```json
{
    "total_validations": 12450,
    "total_pass": 11980,
    "total_fail": 470,
    "pass_rate": 96.2,
    "total_errors": 830,
    "total_warnings": 120,
    "uptime_seconds": 86400.0,
    "by_contract": {
        "customer:none":   {"pass": 8000, "fail": 200, "errors": 400, "warnings": 50},
        "payments:none":   {"pass": 3980, "fail": 270, "errors": 430, "warnings": 70}
    },
    "top_failing_fields": [
        {"contract": "customer", "field": "email",       "rule": "email_format",   "count": 180},
        {"contract": "payments", "field": "amount",      "rule": "amount_positive", "count": 140}
    ],
    "recent_history": [
        {"ts": "...", "contract": "customer", "context": "none", "valid": true,
         "errors": 0, "warnings": 0, "latency_ms": 4.2, "mode": "single"},
        ...
    ]
}
```

| Field | Description |
|---|---|
| `total_validations` | Total pass + fail since startup |
| `pass_rate` | Percentage of validations that passed |
| `by_contract` | Per `contract:context` key breakdown of pass, fail, error, and warning counts |
| `top_failing_fields` | Top 20 `(contract, field, rule)` combinations by error frequency |
| `recent_history` | Last 50 validation events (ring buffer, max 500 events retained) |

The stats dashboard is ideal for the Streamlit governance workbench overview page and for quick health checks during deployment.

---

## Grafana Starter Panels

The following PromQL expressions can be pasted directly into Grafana panel editors.

**Validation rate over time:**

```promql
rate(validation_total[1m])
```

**Pass/fail ratio:**

```promql
rate(validation_total{result="pass"}[5m]) / rate(validation_total[5m])
```

**p95 latency by contract:**

```promql
histogram_quantile(0.95, sum by (contract, le) (rate(validation_latency_seconds_bucket[5m])))
```

**Top failing fields (last hour):**

```promql
topk(10, sum by (field) (rate(validation_errors_total[1h])))
```

Recommended panel types: time series for the first three, bar gauge for the top failing fields.

---

## Rate Limiter Note

OpenDQV's built-in rate limiter is configured via `RATE_LIMIT_VALIDATE`. When measuring throughput, be aware of a known 4× multiplication effect in the rate limit counter: see [`docs/benchmark_throughput.md`](benchmark_throughput.md) for the full explanation and recommended `RATE_LIMIT_VALIDATE=off` setting for benchmarking.
