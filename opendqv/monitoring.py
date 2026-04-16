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
        self._events: deque = deque(maxlen=10_000)  # (timestamp, contract, context, valid, latency_ms, agent_id)
        # Parallel stream for failed-rule events, keyed by agent. Larger cap — one
        # validation call can emit multiple rule failures. Used to build
        # top_failing_fields_by_agent in summary and windowed_summary.
        self._error_events: deque = deque(maxlen=50_000)  # (timestamp, contract, field, rule, agent_id)

    def record(self, contract: str, context: str, valid: bool, error_count: int,
               warning_count: int, latency_ms: float, errors: list = None, mode: str = "single",
               batch_size: int = 0, agent_id: str = ""):
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
            _now_ts = time.time()
            self._events.append((_now_ts, contract, ctx, valid, latency_ms, agent_id or ""))
            # Per-error event log for per-agent failure attribution.
            if not valid:
                for e in (errors or []):
                    self._error_events.append((
                        _now_ts, contract, e.get("field", "?"),
                        e.get("rule", "?"), agent_id or "",
                    ))

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
                "agent_id": agent_id or "",
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

    @staticmethod
    def _aggregate_by_agent(error_events, cutoff: float = 0.0) -> dict:
        """Aggregate error events by agent_id → list of top failing (contract, field, rule).

        Returns {agent_id: [{contract, field, rule, count}, ...top 10]} sorted by count.
        Rows with empty agent_id are grouped under "unattributed" so the story still shows.
        """
        from collections import defaultdict as _dd
        per_agent = _dd(lambda: _dd(int))
        for ts, contract, field, rule, agent_id in error_events:
            if ts < cutoff:
                continue
            aid = agent_id or "unattributed"
            per_agent[aid][(contract, field, rule)] += 1
        out = {}
        for aid, rule_map in per_agent.items():
            out[aid] = sorted(
                [{"contract": c, "field": f, "rule": r, "count": v}
                 for (c, f, r), v in rule_map.items()],
                key=lambda x: x["count"], reverse=True,
            )[:10]
        return out

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
                "top_failing_fields_by_agent": self._aggregate_by_agent(list(self._error_events)),
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
        by_agent: dict = defaultdict(lambda: {"pass": 0, "fail": 0})
        with self._lock:
            for ts, contract, ctx, valid, latency_ms, agent_id in self._events:
                if ts < cutoff:
                    continue
                key = f"{contract}:{ctx}"
                if valid:
                    windowed_totals[key]["pass"] += 1
                else:
                    windowed_totals[key]["fail"] += 1
                windowed_latencies.append(latency_ms)
                if agent_id:
                    if valid:
                        by_agent[agent_id]["pass"] += 1
                    else:
                        by_agent[agent_id]["fail"] += 1

        total_pass = sum(v["pass"] for v in windowed_totals.values())
        total_fail = sum(v["fail"] for v in windowed_totals.values())
        total = total_pass + total_fail
        # Reuse the full-summary structure but override the by_contract view
        summary = self.get_summary()
        summary["by_contract"] = dict(windowed_totals)
        # Recompute per-agent failure breakdown scoped to the same window.
        summary["top_failing_fields_by_agent"] = self._aggregate_by_agent(
            list(self._error_events), cutoff=cutoff,
        )
        summary["total_validations"] = total
        summary["total_pass"] = total_pass
        summary["total_fail"] = total_fail
        summary["pass_rate"] = round(total_pass / total * 100, 1) if total > 0 else 0
        summary["window_hours"] = window_hours
        if len(by_agent) > 1:
            summary["by_agent"] = {
                aid: {
                    "pass": v["pass"],
                    "fail": v["fail"],
                    "total": v["pass"] + v["fail"],
                    "pass_rate": round(v["pass"] / (v["pass"] + v["fail"]), 4) if (v["pass"] + v["fail"]) > 0 else 1.0,
                }
                for aid, v in sorted(by_agent.items(), key=lambda x: x[1]["pass"] + x[1]["fail"], reverse=True)
            }
        return summary

    def get_contract_latency(self, contract_name: str, window_hours: int) -> dict:
        """Compute latency stats for a single contract from the events window."""
        cutoff = time.time() - window_hours * 3600
        latencies = []
        with self._lock:
            for ts, contract, ctx, valid, latency_ms, agent_id in self._events:
                if ts >= cutoff and contract == contract_name:
                    latencies.append(latency_ms)
        if not latencies:
            return {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_size": 0}
        sorted_lat = sorted(latencies)
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

    def get_windowed_summary_for_agent(self, window_hours: int, agent_id: str) -> dict:
        """Return windowed summary scoped to a single agent_id."""
        now_ts = time.time()
        cutoff = now_ts - window_hours * 3600
        windowed_totals: dict = defaultdict(lambda: {"pass": 0, "fail": 0, "errors": 0, "warnings": 0})
        agent_latencies: list = []
        with self._lock:
            for ts, contract, ctx, valid, latency_ms, aid in self._events:
                if ts < cutoff or aid != agent_id:
                    continue
                key = f"{contract}:{ctx}"
                if valid:
                    windowed_totals[key]["pass"] += 1
                else:
                    windowed_totals[key]["fail"] += 1
                agent_latencies.append(latency_ms)
            # Scope top_failing_fields to this agent's errors in the window
            agent_field_counts: dict = defaultdict(int)
            for ts, contract, field, rule, aid in self._error_events:
                if ts < cutoff or aid != agent_id:
                    continue
                agent_field_counts[(contract, field, rule)] += 1
            # Effective window = min(requested window, actual uptime) — tells the
            # caller how much data actually covers this response. In-memory stats
            # reset on API restart, so a 24h request on a 20-min-old API covers
            # ~20 minutes, not 24 hours.
            uptime_seconds = (now_ts - self.started_at.timestamp())
        summary = self.get_summary()
        summary["by_contract"] = dict(windowed_totals)
        total_pass = sum(v["pass"] for v in windowed_totals.values())
        total_fail = sum(v["fail"] for v in windowed_totals.values())
        total = total_pass + total_fail
        summary["total_validations"] = total
        summary["total_pass"] = total_pass
        summary["total_fail"] = total_fail
        summary["pass_rate"] = round(total_pass / total * 100, 1) if total > 0 else 0
        summary["agent_id_filter"] = agent_id
        # Scope top_failing_fields to this agent
        summary["top_failing_fields"] = sorted(
            [{"contract": c, "field": f, "rule": r, "count": v}
             for (c, f, r), v in agent_field_counts.items()],
            key=lambda x: x["count"], reverse=True,
        )[:20]
        # top_failing_fields_by_agent is redundant when filtered — drop it to avoid confusion
        summary.pop("top_failing_fields_by_agent", None)
        # Scope recent_history to this agent's events only. History entries before
        # the agent_id field was added have agent_id="" and so naturally drop out
        # of a specific-agent filter (which is what we want — anonymous entries
        # cannot be attributed to any one agent).
        summary["recent_history"] = [
            h for h in summary["recent_history"]
            if h.get("agent_id") == agent_id
        ][-50:]
        # Scope latency stats to the filtered agent's events only
        if agent_latencies:
            sorted_lat = sorted(agent_latencies)
            n = len(sorted_lat)
            def _pct(p):
                idx = max(0, int(n * p / 100) - 1)
                return round(sorted_lat[idx], 1)
            summary["latency"] = {
                "avg_ms": round(sum(sorted_lat) / n, 1),
                "p50_ms": _pct(50),
                "p95_ms": _pct(95),
                "p99_ms": _pct(99),
                "sample_size": n,
            }
        else:
            summary["latency"] = {"avg_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "sample_size": 0}
        # Transparency: tell the caller how much time the response actually covers.
        # In-memory stats start when the API process starts; a 24h request on a
        # freshly-restarted API covers only uptime, not 24h of calendar time.
        summary["effective_window_seconds"] = round(min(window_hours * 3600, uptime_seconds), 1)
        summary["requested_window_hours"] = window_hours
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
