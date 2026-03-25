"""
Prometheus metrics with actual request timing middleware.

Also tracks validation-specific metrics (pass/fail per contract/context)
accessible via /metrics (Prometheus) and the in-memory stats API.
"""

import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from prometheus_client import Counter, Histogram, Gauge, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_TIME = Histogram(
    "request_latency_seconds",
    "Request processing time in seconds",
    ["method", "endpoint"],
)
REQUEST_COUNT = Counter(
    "request_count_total",
    "Total request count",
    ["method", "endpoint", "status"],
)
ERROR_COUNTER = Counter(
    "api_errors_total",
    "Total API errors",
    ["method", "endpoint"],
)
VALIDATION_COUNT = Counter(
    "validation_total",
    "Total validation calls",
    ["contract", "context", "result"],  # result: pass or fail
)
VALIDATION_ERRORS = Counter(
    "validation_errors_total",
    "Total field-level validation errors",
    ["contract", "context", "field", "rule"],
)
VALIDATION_LATENCY = Histogram(
    "validation_latency_seconds",
    "Validation latency in seconds",
    ["contract", "context", "mode"],  # mode: single or batch
)
LITESTREAM_REPLICATION_AGE = Gauge(
    "opendqv_litestream_last_replication_age_seconds",
    "Seconds since last successful Litestream replication checkpoint. "
    "Alert if > 300. Set to -1 when Litestream is not configured.",
)
# Initialise to -1 (not configured) — updated by Litestream health check if present.
LITESTREAM_REPLICATION_AGE.set(-1)

REJECTION_RATE = Gauge(
    "opendqv_rejection_rate",
    "Current rejection rate (0.0-1.0) per contract",
    ["contract"],
)
BATCH_SIZE = Histogram(
    "opendqv_batch_size",
    "Number of records per batch validation call",
    ["contract"],
    buckets=[1, 10, 50, 100, 500, 1000, 5000, 10000],
)
FAILURES_BY_SEVERITY = Counter(
    "opendqv_failures_by_severity_total",
    "Validation failures by severity level",
    ["contract", "severity"],
)
DRAFT_CONTRACT_COUNT = Gauge(
    "opendqv_draft_contract_count",
    "Number of contracts currently in DRAFT status",
)
ACTIVE_CONTRACT_COUNT = Gauge(
    "opendqv_active_contract_count",
    "Number of contracts currently in ACTIVE status",
)


# ── In-memory stats for dashboard (no external DB needed) ────────────

class ValidationStats:
    """Thread-safe in-memory validation statistics for the dashboard."""

    def __init__(self, max_history=500):
        self._lock = threading.Lock()
        self._max_history = max_history
        self.history = []  # list of validation event dicts
        self.totals = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": 0, "warnings": 0})
        self.field_errors = defaultdict(int)  # (contract, field, rule) -> count
        self.severity_counts = defaultdict(int)  # (contract, severity) -> count
        self.started_at = datetime.now(timezone.utc)
        self._latencies: list = []  # recent latency values for percentile computation
        self._events: deque = deque(maxlen=10_000)  # (timestamp, contract, context, valid, latency_ms)

    def record(self, contract: str, context: str, valid: bool, error_count: int,
               warning_count: int, latency_ms: float, errors: list = None, mode: str = "single",
               batch_size: int = 0):
        ctx = context or "none"
        with self._lock:
            # Update totals
            key = f"{contract}:{ctx}"
            if valid:
                self.totals[key]["pass"] += 1
            else:
                self.totals[key]["fail"] += 1
            self.totals[key]["errors"] += error_count
            self.totals[key]["warnings"] += warning_count

            # Track field-level errors
            for e in (errors or []):
                self.field_errors[(contract, e.get("field", "?"), e.get("rule", "?"))] += 1

            # Track severity counts
            for e in (errors or []):
                self.severity_counts[(contract, e.get("severity", "error"))] += 1

            # Track latency for percentile computation (ring buffer, last 1000)
            self._latencies.append(round(latency_ms, 1))
            if len(self._latencies) > 1000:
                self._latencies = self._latencies[-1000:]

            # Timestamped event log for windowed queries (capped at maxlen=10_000)
            self._events.append((time.time(), contract, ctx, valid, latency_ms))

            # Append to history (ring buffer)
            self.history.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "contract": contract,
                "context": ctx,
                "valid": valid,
                "errors": error_count,
                "warnings": warning_count,
                "latency_ms": round(latency_ms, 1),
                "mode": mode,
            })
            if len(self.history) > self._max_history:
                self.history = self.history[-self._max_history:]

        # Update Prometheus counters
        VALIDATION_COUNT.labels(contract=contract, context=ctx, result="pass" if valid else "fail").inc()
        VALIDATION_LATENCY.labels(contract=contract, context=ctx, mode=mode).observe(latency_ms / 1000)
        for e in (errors or []):
            VALIDATION_ERRORS.labels(
                contract=contract, context=ctx,
                field=e.get("field", "?"), rule=e.get("rule", "?"),
            ).inc()
        # Update rejection rate gauge for this contract
        _contract_keys = [k for k in self.totals if k.startswith(f"{contract}:")]
        _c_pass = sum(self.totals[k]["pass"] for k in _contract_keys)
        _c_fail = sum(self.totals[k]["fail"] for k in _contract_keys)
        _c_total = _c_pass + _c_fail
        if _c_total > 0:
            REJECTION_RATE.labels(contract=contract).set(_c_fail / _c_total)
        for e in (errors or []):
            FAILURES_BY_SEVERITY.labels(
                contract=contract, severity=e.get("severity", "error")
            ).inc()
        if batch_size > 0:
            BATCH_SIZE.labels(contract=contract).observe(batch_size)

    def get_summary(self) -> dict:
        with self._lock:
            total_pass = sum(v["pass"] for v in self.totals.values())
            total_fail = sum(v["fail"] for v in self.totals.values())
            total = total_pass + total_fail
            return {
                "total_validations": total,
                "total_pass": total_pass,
                "total_fail": total_fail,
                "pass_rate": round(total_pass / total * 100, 1) if total > 0 else 0,
                "total_errors": sum(v["errors"] for v in self.totals.values()),
                "total_warnings": sum(v["warnings"] for v in self.totals.values()),
                "uptime_seconds": (datetime.now(timezone.utc) - self.started_at).total_seconds(),
                "by_contract": dict(self.totals),
                "top_failing_fields": sorted(
                    [{"contract": k[0], "field": k[1], "rule": k[2], "count": v}
                     for k, v in self.field_errors.items()],
                    key=lambda x: x["count"], reverse=True,
                )[:20],
                "recent_history": list(self.history[-50:]),
                "latency": self._latency_stats(),
                "dimensions": {
                    "by_severity": {
                        "error": sum(v for (c, sev), v in self.severity_counts.items() if sev == "error"),
                        "warning": sum(v for (c, sev), v in self.severity_counts.items() if sev == "warning"),
                    },
                },
                "governance": {
                    "draft_count": 0,
                    "active_count": 0,
                    "review_count": 0,
                },
            }


    def get_windowed_summary(self, window_hours: int) -> dict:
        """Return pass/fail totals per contract:context key for events within the last window_hours.

        Returns a dict with the same shape as get_summary() but scoped to the time window.
        Keys not present in the window will be absent from by_contract.
        """
        cutoff = time.time() - window_hours * 3600
        windowed_totals: dict = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": 0, "warnings": 0})
        windowed_latencies: list = []
        with self._lock:
            for ts, contract, ctx, valid, latency_ms in self._events:
                if ts < cutoff:
                    continue
                key = f"{contract}:{ctx}"
                if valid:
                    windowed_totals[key]["pass"] += 1
                else:
                    windowed_totals[key]["fail"] += 1
                windowed_latencies.append(latency_ms)

        total_pass = sum(v["pass"] for v in windowed_totals.values())
        total_fail = sum(v["fail"] for v in windowed_totals.values())
        total = total_pass + total_fail
        # Reuse the full-summary structure but override the by_contract view
        summary = self.get_summary()
        summary["by_contract"] = dict(windowed_totals)
        summary["total_validations"] = total
        summary["total_pass"] = total_pass
        summary["total_fail"] = total_fail
        summary["pass_rate"] = round(total_pass / total * 100, 1) if total > 0 else 0
        summary["window_hours"] = window_hours
        return summary

    def _latency_stats(self) -> dict:
        """Compute avg/p50/p95/p99 from recent latency values. Called under self._lock."""
        if not self._latencies:
            return {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_size": 0}
        sorted_lat = sorted(self._latencies)
        n = len(sorted_lat)
        def _pct(p):
            idx = max(0, int(n * p / 100) - 1)
            return round(sorted_lat[idx], 1)
        return {
            "avg_ms": round(sum(sorted_lat) / n, 1),
            "p50_ms": _pct(50),
            "p95_ms": _pct(95),
            "p99_ms": _pct(99),
            "sample_size": n,
        }


# Singleton instance
stats = ValidationStats()


def update_contract_counts(draft: int, active: int, review: int = 0) -> None:
    """Update Prometheus gauges for contract lifecycle counts.

    Call this from api/routes.py on startup and after any contract
    lifecycle change (create, activate, archive).
    """
    DRAFT_CONTRACT_COUNT.set(draft)
    ACTIVE_CONTRACT_COUNT.set(active)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that records request timing and counts."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        try:
            response = await call_next(request)
            duration = time.time() - start
            REQUEST_TIME.labels(
                method=request.method, endpoint=request.url.path
            ).observe(duration)
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code,
            ).inc()
            return response
        except Exception:
            ERROR_COUNTER.labels(
                method=request.method, endpoint=request.url.path
            ).inc()
            raise


def instrument_app(app: FastAPI):
    """Mount Prometheus metrics endpoint and add timing middleware."""
    app.add_middleware(MetricsMiddleware)
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
