"""
Prometheus metrics with actual request timing middleware.

Also tracks validation-specific metrics (pass/fail per contract/context)
accessible via /metrics (Prometheus) and the in-memory stats API.
"""

import time
import threading
from collections import defaultdict
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


# ── In-memory stats for dashboard (no external DB needed) ────────────

class ValidationStats:
    """Thread-safe in-memory validation statistics for the dashboard."""

    def __init__(self, max_history=500):
        self._lock = threading.Lock()
        self._max_history = max_history
        self.history = []  # list of validation event dicts
        self.totals = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": 0, "warnings": 0})
        self.field_errors = defaultdict(int)  # (contract, field, rule) -> count
        self.started_at = datetime.now(timezone.utc)

    def record(self, contract: str, context: str, valid: bool, error_count: int,
               warning_count: int, latency_ms: float, errors: list = None, mode: str = "single"):
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
            }


# Singleton instance
stats = ValidationStats()


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
