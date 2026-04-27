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

# CRT173 / items 26-28 — reserved prefix for OpenDQV-owned system agents
# (smoke, probe, demo, mcp, perf). Default-suppressed from customer-visible
# metrics to keep tenant views clean of dev/test traffic. The prefix is
# self-documenting in audit rows; clients pass include_system=True for
# diagnostics. See README "Reserved agent_id prefix" section.
SYSTEM_AGENT_PREFIX = "OpenDQV_SA_"


def _is_system_agent(agent_id: str) -> bool:
    """True if agent_id is an OpenDQV-owned system agent (OpenDQV_SA_* prefix)."""
    return bool(agent_id) and agent_id.startswith(SYSTEM_AGENT_PREFIX)


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
    def _aggregate_by_agent(error_events, cutoff: float = 0.0,
                            include_system: bool = False) -> dict:
        """Aggregate error events by agent_id → list of top failing (contract, field, rule).

        Returns {agent_id: [{contract, field, rule, count, [field_provenance]}, ...top 10]}
        sorted by count.

        Field-name honesty (v2.3.17 N-8 / F-K fix): when an error event carries
        the sentinel field "?" — most often because it was synthesised from the
        SQLite quality_stats aggregate during in-memory hydration, where only
        rule_failure_counts are persisted and field names are not — the aggregate
        output emits ``{"field": null, "field_provenance": "unavailable"}`` rather
        than the literal "?" string. This signals to consumers that the field
        could not be recovered from persistence, rather than implying the
        validator emitted "?" as the field name.

        Rows with empty agent_id are grouped under "unattributed" so the story
        still shows. System agents (OpenDQV_SA_*) are suppressed unless
        include_system=True.
        """
        from collections import defaultdict as _dd
        per_agent = _dd(lambda: _dd(int))
        for ts, contract, field, rule, agent_id in error_events:
            if ts < cutoff:
                continue
            if not include_system and _is_system_agent(agent_id):
                continue
            aid = agent_id or "unattributed"
            per_agent[aid][(contract, field, rule)] += 1
        out = {}
        for aid, rule_map in per_agent.items():
            entries = []
            for (c, f, r), v in rule_map.items():
                if f == "?":
                    entries.append({
                        "contract": c, "field": None, "rule": r, "count": v,
                        "field_provenance": "unavailable",
                    })
                else:
                    entries.append({"contract": c, "field": f, "rule": r, "count": v})
            entries.sort(key=lambda x: x["count"], reverse=True)
            out[aid] = entries[:10]
        return out

    def get_summary(self, include_system: bool = False) -> dict:
        with self._lock:
            total_pass = sum(v["pass"] for v in self.totals.values())
            total_fail = sum(v["fail"] for v in self.totals.values())
            total = total_pass + total_fail
            _err_violations = sum(v["errors"] for v in self.totals.values())
            _warn_violations = sum(v["warnings"] for v in self.totals.values())
            recent = list(self.history[-50:])
            if not include_system:
                recent = [h for h in recent if not _is_system_agent(h.get("agent_id", ""))]
            return {
                "total_validations": total,
                "total_pass": total_pass,
                "total_fail": total_fail,
                # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp). The bare
                # `pass_rate` field and the `pass_rate_ratio` companion are
                # both removed in this release — pass_rate_pct is the single
                # canonical wire field across every surface (REST + MCP +
                # storage + audit). Empty-history case returns 100.0
                # (vacuously perfect).
                "pass_rate_pct": round(total_pass / total * 100, 1) if total > 0 else 100.0,
                # *_violations are sums of per-record rule violations: a single
                # failing record with N broken rules contributes N. total_fail is
                # a record count. The two are equal only when each failing record
                # breaks exactly one rule.
                "total_error_violations": _err_violations,
                "total_warning_violations": _warn_violations,
                # Deprecated aliases — kept additive in v2.3.13 for wire compat,
                # will be removed in v2.4. Names mismatch the math (they count
                # violations, not error/warning records).
                "total_errors": _err_violations,
                "total_warnings": _warn_violations,
                "uptime_seconds": int((datetime.now(timezone.utc) - self.started_at).total_seconds()),
                "by_contract": dict(self.totals),
                "top_failing_fields": sorted(
                    [{"contract": k[0], "field": k[1], "rule": k[2], "count": v}
                     for k, v in self.field_errors.items()],
                    key=lambda x: x["count"], reverse=True,
                )[:20],
                "top_failing_fields_by_agent": self._aggregate_by_agent(
                    list(self._error_events), include_system=include_system,
                ),
                "recent_history": recent,
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
                "include_system": include_system,
            }


    def get_windowed_summary(self, window_hours: int, include_system: bool = False) -> dict:
        """Return pass/fail totals per contract:context key for events within the last window_hours.

        Returns a dict with the same shape as get_summary() but scoped to the time window.
        Keys not present in the window will be absent from by_contract.
        System agents (OpenDQV_SA_*) are suppressed from by_agent and
        top_failing_fields_by_agent unless include_system=True.
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
                    if not include_system and _is_system_agent(agent_id):
                        continue
                    if valid:
                        by_agent[agent_id]["pass"] += 1
                    else:
                        by_agent[agent_id]["fail"] += 1

        total_pass = sum(v["pass"] for v in windowed_totals.values())
        total_fail = sum(v["fail"] for v in windowed_totals.values())
        total = total_pass + total_fail
        # Reuse the full-summary structure but override the by_contract view
        summary = self.get_summary(include_system=include_system)
        summary["by_contract"] = dict(windowed_totals)
        # Recompute per-agent failure breakdown scoped to the same window.
        summary["top_failing_fields_by_agent"] = self._aggregate_by_agent(
            list(self._error_events), cutoff=cutoff, include_system=include_system,
        )
        summary["total_validations"] = total
        summary["total_pass"] = total_pass
        summary["total_fail"] = total_fail
        # v2.3.18 Q3: single canonical pass_rate_pct everywhere.
        summary["pass_rate_pct"] = round(total_pass / total * 100, 1) if total > 0 else 100.0
        # Window field semantics (CRT173 finding 22):
        #   window_hours              = caller's requested window, in hours.
        #   effective_window_seconds  = min(requested, actual data coverage),
        #                               in seconds. Coverage is the larger of
        #                               API uptime and the age of the oldest
        #                               event in the deque (which counts
        #                               hydrated-from-persistent-store events
        #                               even when the process just started).
        #                               Diverges from window_hours only when
        #                               the API has been up for less than the
        #                               requested window AND there is no
        #                               hydrated history.
        #   requested_window_hours    = DEPRECATED v2.3.14, removed v2.4.
        #                               Always equal to window_hours; emitted
        #                               for back-compat only.
        summary["window_hours"] = window_hours
        summary["effective_window_seconds"] = self._effective_window_seconds(window_hours)
        summary["requested_window_hours"] = window_hours
        if len(by_agent) > 1:
            summary["by_agent"] = {
                aid: {
                    "pass": v["pass"],
                    "fail": v["fail"],
                    "total": v["pass"] + v["fail"],
                    "pass_rate_pct": round(v["pass"] / (v["pass"] + v["fail"]) * 100, 1) if (v["pass"] + v["fail"]) > 0 else 100.0,
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
            return {
                "avg_ms": None, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "p99_9_ms": None, "max_ms": None,
                "sample_size": 0,
            }
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
            "p99_9_ms": _pct(99.9),
            "max_ms": round(sorted_lat[-1], 1),
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
        summary = self.get_summary()
        summary["by_contract"] = dict(windowed_totals)
        total_pass = sum(v["pass"] for v in windowed_totals.values())
        total_fail = sum(v["fail"] for v in windowed_totals.values())
        total = total_pass + total_fail
        summary["total_validations"] = total
        summary["total_pass"] = total_pass
        summary["total_fail"] = total_fail
        # v2.3.18 Q3: single canonical pass_rate_pct everywhere.
        summary["pass_rate_pct"] = round(total_pass / total * 100, 1) if total > 0 else 100.0
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
        # Transparency: min(requested window, actual data coverage).
        summary["effective_window_seconds"] = self._effective_window_seconds(window_hours)
        summary["requested_window_hours"] = window_hours
        # CRT167: drop fields that cannot be accurately scoped from the inherited
        # get_summary() view. total_errors/total_warnings/dimensions.by_severity
        # are lifetime counts across all agents; leaving them in creates misleading
        # per-record vs aggregate contradictions. A missing key forces the UI to
        # say "scoped view — totals unavailable", which is strictly better than a
        # plausible-but-wrong number. v2.4 will replace the inherit-then-override
        # shape entirely; this is the minimum principled fix today.
        summary.pop("total_errors", None)
        summary.pop("total_warnings", None)
        summary.pop("dimensions", None)
        return summary

    def _effective_window_seconds(self, requested_window_hours: int) -> float:
        """Return min(requested window, actual data coverage).

        Coverage is the larger of (a) API uptime and (b) age of the oldest
        event in the in-memory deque. (b) allows hydrated-from-persistent-store
        events to count, even when the process just started. Without this, a
        restarted API would claim only seconds of coverage despite having days
        of valid hydrated data.
        """
        now_ts = time.time()
        uptime = now_ts - self.started_at.timestamp()
        with self._lock:
            oldest_ts = self._events[0][0] if self._events else now_ts
        oldest_age = now_ts - oldest_ts
        coverage = max(uptime, oldest_age)
        return round(min(requested_window_hours * 3600, coverage), 1)

    def list_agents(self, window_hours: int = 24, include_system: bool = False) -> list:
        """Return per-agent totals seen in the last window_hours from _events deque.

        Each entry: {agent_id, total_validations, total_pass, total_fail,
        pass_rate_pct, last_seen, is_system_agent}. Sorted by total_validations desc.
        Records with empty agent_id are excluded. System agents (OpenDQV_SA_*)
        are suppressed unless include_system=True.
        """
        cutoff = time.time() - window_hours * 3600
        per_agent: dict = {}
        with self._lock:
            for ts, _contract, _ctx, valid, _latency_ms, agent_id in self._events:
                if ts < cutoff or not agent_id:
                    continue
                if not include_system and _is_system_agent(agent_id):
                    continue
                a = per_agent.setdefault(
                    agent_id,
                    {"total_validations": 0, "total_pass": 0, "total_fail": 0, "last_seen_ts": 0.0},
                )
                a["total_validations"] += 1
                if valid:
                    a["total_pass"] += 1
                else:
                    a["total_fail"] += 1
                if ts > a["last_seen_ts"]:
                    a["last_seen_ts"] = ts
        out = []
        for aid, v in per_agent.items():
            t = v["total_validations"]
            out.append({
                "agent_id": aid,
                "total_validations": t,
                "total_pass": v["total_pass"],
                "total_fail": v["total_fail"],
                # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp).
                "pass_rate_pct": round(v["total_pass"] / t * 100, 1) if t > 0 else 100.0,
                "last_seen": datetime.fromtimestamp(
                    v["last_seen_ts"], tz=timezone.utc
                ).isoformat(),
                "is_system_agent": _is_system_agent(aid),
            })
        out.sort(key=lambda x: x["total_validations"], reverse=True)
        return out

    def _latency_stats(self) -> dict:
        """Compute avg/p50/p95/p99/p99.9/max from recent latency values. Called under self._lock."""
        if not self._latencies:
            return {
                "avg_ms": None, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "p99_9_ms": None, "max_ms": None,
                "sample_size": 0,
            }
        sorted_lat = sorted(self._latencies)
        n = len(sorted_lat)
        def _pct(p):
            idx = max(0, int(n * p / 100) - 1)
            return round(sorted_lat[idx], 1)
        def _pct_f(p):
            idx = max(0, int(n * p / 100) - 1)
            return round(sorted_lat[idx], 1)
        return {
            "avg_ms": round(sum(sorted_lat) / n, 1),
            "p50_ms": _pct(50),
            "p95_ms": _pct(95),
            "p99_ms": _pct(99),
            "p99_9_ms": _pct_f(99.9),
            "max_ms": round(sorted_lat[-1], 1),
            "sample_size": n,
        }


# Singleton instance
stats = ValidationStats()


def hydrate_stats_from_persistent_store(
    stats_instance: "ValidationStats",
    db_path: str,
    window_hours: int = 336,  # 14 days
) -> dict:
    """Populate in-memory monitoring deques from the SQLite quality_stats table.

    On API restart, the in-memory window (`_events`, `_error_events`) is empty
    and takes real-world time to refill. Dashboards that poll /stats then show
    empty or tiny windows until enough traffic accumulates. This function
    synthesises per-record events from the persisted aggregates so the in-memory
    window reflects the real production history immediately.

    Synthesis:
     - Each quality_stats row stores an aggregate call: total/passed/failed and
       rule_failure_counts. We emit `passed` valid=True and `failed` valid=False
       events, plus one error event per rule_failure_count entry.
     - Timestamps are assembled from recorded_at with small deterministic
       micro-offsets to keep ordering stable in the deque.
     - Field name is not stored in the aggregate, so error events get field="?";
       rule name is preserved (which is what top_failing_fields_by_agent keys on
       in practice).

    Deques are bounded (maxlen=10k for _events, 50k for _error_events) — older
    synthesised events are silently dropped if the window is very busy. That's
    correct: we care about the most recent state.

    Returns a dict with counts for logging: {events, errors, rows_read}.
    """
    import sqlite3
    import json
    from datetime import timedelta as _td

    cutoff = (datetime.now(timezone.utc) - _td(hours=window_hours)).isoformat()

    try:
        conn = sqlite3.connect(db_path, timeout=5)
    except sqlite3.OperationalError:
        # DB unavailable — silently skip hydration (fresh install, wrong path)
        return {"events": 0, "errors": 0, "rows_read": 0, "skipped": True}

    try:
        rows = conn.execute(
            """
            SELECT contract_name, context, recorded_at, total_records, passed,
                   failed, rule_failure_counts, agent_id, mode
            FROM quality_stats
            WHERE recorded_at > ?
            ORDER BY recorded_at ASC
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return {"events": 0, "errors": 0, "rows_read": 0, "skipped": True}
    conn.close()

    events_added = 0
    errors_added = 0

    for contract, context, recorded_at, total, passed, failed, rule_failures_json, agent_id, mode in rows:
        try:
            ts_dt = datetime.fromisoformat(recorded_at)
            ts = ts_dt.timestamp()
        except (ValueError, TypeError):
            continue
        ctx = context or "none"
        aid = agent_id or ""
        # Synthesize per-record events with tiny micro-offsets
        for i in range(passed or 0):
            stats_instance._events.append((ts + i * 0.0001, contract, ctx, True, 0.3, aid))
            events_added += 1
        for i in range(failed or 0):
            stats_instance._events.append((ts + ((passed or 0) + i) * 0.0001, contract, ctx, False, 0.3, aid))
            events_added += 1
        # Synthesize error_events from rule_failure_counts
        try:
            rule_failures = json.loads(rule_failures_json) if rule_failures_json else {}
        except (ValueError, TypeError):
            rule_failures = {}
        for rule_name, count in rule_failures.items():
            for i in range(count or 0):
                stats_instance._error_events.append((ts + i * 0.0001, contract, "?", rule_name, aid))
                errors_added += 1

    return {"events": events_added, "errors": errors_added, "rows_read": len(rows), "skipped": False}


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
