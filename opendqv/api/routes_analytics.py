from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

import opendqv.api.deps as _d
from opendqv.core.quality_stats import quality_confidence
from opendqv.monitoring import stats, update_contract_counts
from opendqv.security.auth import get_current_user

from .models import (
    AnalyticsSummaryItem, AnalyticsSummaryResponse,
    RuleHeatmapItem, RuleHeatmapResponse,
    RuleVelocityBucket, RuleVelocityResponse,
    ObservationSummaryResponse, ObservationTrendPoint, ObservationFieldFailure,
)

sub_router = APIRouter()


@sub_router.get("/stats")
@_d._default_limit
async def get_stats(
    request: Request,
    window_hours: Optional[int] = Query(None, ge=1, le=8760, description="If set, return stats for only the last N hours"),
    agent_id: Optional[str] = Query(None, description="Filter to a specific agent / source system identity"),
    contract: Optional[str] = Query(None, description="Filter to a specific contract. Scopes by_contract, top_failing_fields, dimensions.by_severity, recent_history, and totals to that contract only."),
    include_system: bool = Query(False, description="If true, include OpenDQV system agents (agent_ids prefixed 'OpenDQV_SA_' — smoke probes, demos, MCP self-tests). Default false hides them from tenant-facing metrics."),
    user=Depends(get_current_user),
):
    """Get validation statistics for the monitoring dashboard.

    If agent_id is provided, results are scoped to that agent's traffic only —
    useful for per-source-system drill-down in monitoring dashboards.

    If contract is provided, the response is scoped to that contract: by_contract,
    top_failing_fields, dimensions.by_severity, recent_history, and totals
    (total_validations, total_pass, total_fail, pass_rate) reflect only that
    contract's events. Closes v2.3.17 N-7 / F-H — the dual-path drift where the
    proxy passed `contract` as a query param but the REST endpoint silently
    dropped it.
    """
    if agent_id:
        # Explicit agent filter — caller asked for that exact agent_id, suppression
        # is irrelevant (they get what they asked for).
        result = stats.get_windowed_summary_for_agent(window_hours or 24, agent_id)
    elif window_hours:
        result = stats.get_windowed_summary(window_hours, include_system=include_system)
    else:
        result = stats.get_summary(include_system=include_system)
    contracts = _d.registry.list_contracts()
    draft_count = sum(1 for c in contracts if c["status"] == "draft")
    active_count = sum(1 for c in contracts if c["status"] == "active")
    review_count = sum(1 for c in contracts if c["status"] == "review")
    result["governance"] = {
        "draft_count": draft_count,
        "active_count": active_count,
        "review_count": review_count,
    }
    update_contract_counts(draft=draft_count, active=active_count, review=review_count)

    if contract:
        result = _scope_summary_to_contract(result, contract)

    return result


def _scope_summary_to_contract(summary: dict, contract_name: str) -> dict:
    """Scope an unfiltered stats summary to a single contract.

    Filters by_contract, top_failing_fields, top_failing_fields_by_agent,
    recent_history, dimensions.by_severity, and recomputes totals
    (total_validations, total_pass, total_fail, pass_rate, pass_rate_ratio)
    from the scoped by_contract slice.

    Governance, latency (engine-wide), uptime, and window metadata are
    preserved unchanged. agent_id_filter is preserved (this can compose with
    agent_id filtering when both are set on get_windowed_summary_for_agent).
    """
    scoped = dict(summary)
    by_contract = summary.get("by_contract", {}) or {}
    scoped_by_contract = {
        k: v for k, v in by_contract.items() if k.startswith(f"{contract_name}:")
    }
    scoped["by_contract"] = scoped_by_contract

    scoped_pass = sum(v.get("pass", 0) for v in scoped_by_contract.values())
    scoped_fail = sum(v.get("fail", 0) for v in scoped_by_contract.values())
    scoped_total = scoped_pass + scoped_fail
    scoped["total_pass"] = scoped_pass
    scoped["total_fail"] = scoped_fail
    scoped["total_validations"] = scoped_total
    scoped["pass_rate"] = round(scoped_pass / scoped_total * 100, 1) if scoped_total > 0 else 0
    scoped["pass_rate_ratio"] = round(scoped_pass / scoped_total, 4) if scoped_total > 0 else 1.0

    scoped["top_failing_fields"] = [
        f for f in summary.get("top_failing_fields", []) or []
        if f.get("contract") == contract_name
    ]

    by_agent_failing = summary.get("top_failing_fields_by_agent", {}) or {}
    scoped["top_failing_fields_by_agent"] = {
        aid: [f for f in entries if f.get("contract") == contract_name]
        for aid, entries in by_agent_failing.items()
    }
    scoped["top_failing_fields_by_agent"] = {
        aid: entries for aid, entries in scoped["top_failing_fields_by_agent"].items() if entries
    }

    scoped["recent_history"] = [
        h for h in summary.get("recent_history", []) or []
        if h.get("contract") == contract_name
    ]

    severity_counts_attr = getattr(stats, "severity_counts", {}) or {}
    scoped_err = sum(
        v for (c, sev), v in severity_counts_attr.items()
        if c == contract_name and sev == "error"
    )
    scoped_warn = sum(
        v for (c, sev), v in severity_counts_attr.items()
        if c == contract_name and sev == "warning"
    )
    if "dimensions" in summary:
        scoped["dimensions"] = {
            **summary["dimensions"],
            "by_severity": {"error": scoped_err, "warning": scoped_warn},
        }

    scoped["contract_filter"] = contract_name
    return scoped


@sub_router.get("/agents")
@_d._default_limit
async def list_agents_endpoint(
    request: Request,
    window_hours: int = Query(24, ge=1, le=8760, description="Look-back window in hours"),
    include_system: bool = Query(False, description="If true, include OpenDQV system agents (OpenDQV_SA_* prefix). Default false suppresses them from customer-facing views."),
    user=Depends(get_current_user),
):
    """Return the agents (source systems) that emitted traffic in the window.

    Each entry: agent_id, total_validations, total_pass, total_fail, pass_rate,
    last_seen, is_system_agent. Sorted by total_validations desc. OpenDQV system
    agents (agent_ids prefixed 'OpenDQV_SA_') are suppressed by default;
    pass include_system=true for diagnostic views. Closes the v2.3.x gap where
    operators had to filter by agent_id without first being able to enumerate
    them.
    """
    return {
        "window_hours": window_hours,
        "agents": stats.list_agents(window_hours, include_system=include_system),
        "include_system": include_system,
    }


@sub_router.get("/rejection-summary")
@_d._default_limit
async def get_rejection_summary(
    request: Request,
    limit: int = Query(10, ge=1, le=50, description="Max number of contracts to return"),
    user=Depends(get_current_user),
):
    """Top failing contracts and rules over the in-memory validation window.

    Returns contracts sorted by rejection rate (worst first), each with
    total validations, failure count, pass rate, and top failing rules.
    """
    summary = stats.get_summary()
    by_contract = summary["by_contract"]
    top_fields = summary["top_failing_fields"]

    contract_stats = {}
    for key, data in by_contract.items():
        contract_name = key.split(":")[0]
        if contract_name not in contract_stats:
            contract_stats[contract_name] = {"pass": 0, "fail": 0}
        contract_stats[contract_name]["pass"] += data["pass"]
        contract_stats[contract_name]["fail"] += data["fail"]

    result = []
    for contract_name, _cdata in contract_stats.items():
        total = _cdata["pass"] + _cdata["fail"]
        if total == 0:
            continue
        pass_rate = round(_cdata["pass"] / total, 4)
        top_rules = [
            {
                "rule": f["rule"],
                "field": f["field"],
                "failures": f["count"],
                "failure_rate_pct": round(f["count"] / total * 100, 1) if total > 0 else 0,
            }
            for f in top_fields if f["contract"] == contract_name
        ][:5]
        result.append({
            "contract": contract_name,
            "total_validations": total,
            "failed": _cdata["fail"],
            "pass_rate": pass_rate,
            "top_failing_rules": top_rules,
        })

    result.sort(key=lambda x: x["pass_rate"])
    return result[:limit]


@sub_router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
@_d._default_limit
async def get_analytics_summary(
    request: Request,
    days: int = Query(7, ge=1, le=365, description="Analytics window in calendar days"),
    user=Depends(get_current_user),
):
    """
    Cross-contract pass rate summary — DuckDB OLAP over SQLite quality data.

    Returns every contract that has validation records in the last N days,
    sorted by pass_rate ascending (worst-performing contracts first).

    Backed by DuckDB reading the SQLite quality_stats table directly — no data
    duplication from the OLTP write path.
    """
    items = _d._quality_analytics.cross_contract_summary(days=days)
    return AnalyticsSummaryResponse(
        days=days,
        contracts=[AnalyticsSummaryItem(**i) for i in items],
        total_contracts=len(items),
    )


@sub_router.get("/analytics/rule-heatmap", response_model=RuleHeatmapResponse)
@_d._default_limit
async def get_analytics_rule_heatmap(
    request: Request,
    days: int = Query(7, ge=1, le=365, description="Analytics window in calendar days"),
    user=Depends(get_current_user),
):
    """
    Top failing rules across all contracts — DuckDB OLAP over SQLite quality data.

    Returns up to 50 (contract, rule) pairs ranked by failure count descending.
    Use this to identify systemic data quality issues that span multiple contracts.

    Backed by DuckDB reading the SQLite quality_stats table directly — no data
    duplication from the OLTP write path.
    """
    items = _d._quality_analytics.rule_heatmap(days=days)
    return RuleHeatmapResponse(
        days=days,
        rules=[RuleHeatmapItem(**i) for i in items],
        total_rules=len(items),
    )


@sub_router.get("/analytics/rule-velocity", response_model=RuleVelocityResponse)
@_d._default_limit
async def get_analytics_rule_velocity(
    request: Request,
    contract: str = Query(..., description="Contract name"),
    window_hours: int = Query(24, ge=1, le=168, description="Look-back window in hours (1–168)"),
    bucket_minutes: int = Query(5, ge=1, le=60, description="Bucket width in minutes (1–60)"),
    user=Depends(get_current_user),
):
    """
    Time-series failure counts per rule for a single contract.

    Shows whether failures are accelerating or decelerating — the difference
    between a slow drip and a sudden spike. Returns the top 5 rules by total
    failures within the window, bucketed by bucket_minutes intervals.

    Use this when pass_rate is degrading to diagnose whether it's a sudden
    spike (fix the upstream source now) or a slow drip (investigate root cause).

    Requires reader role or above.
    """
    data = _d._quality_analytics.rule_failure_velocity(
        contract_name=contract,
        window_hours=window_hours,
        bucket_minutes=bucket_minutes,
    )
    # CRT170/J6: total validations underpinning this window → confidence band.
    try:
        totals = _d._quality_stats.get_windowed_totals(contract, window_hours)
        total_validations = int(totals.get("total", 0))
    except Exception:
        total_validations = 0
    confidence, confidence_note = quality_confidence(total_validations)
    return RuleVelocityResponse(
        contract=data["contract"],
        window_hours=data["window_hours"],
        bucket_minutes=data["bucket_minutes"],
        series={
            rule: [RuleVelocityBucket(**b) for b in buckets]
            for rule, buckets in data["series"].items()
        },
        data_confidence=confidence,
        confidence_note=confidence_note,
        total_validations=total_validations,
    )


@sub_router.get("/observation/summary", response_model=ObservationSummaryResponse)
@_d._default_limit
async def get_observation_summary(
    request: Request,
    days: int = Query(7, ge=1, le=90, description="Analytics window in calendar days"),
    contract: Optional[str] = Query(None, description="Filter to a single contract (default: all)"),
    user=Depends(get_current_user),
):
    """
    Cross-contract summary of observation-only validation runs.

    Shows total records validated in observation mode, how many would have
    failed under enforcement, and an enforcement readiness percentage.

    Use this to decide when a contract is ready to switch from observation
    mode to enforcement.

    Requires reader role or above.
    """
    data = _d._quality_analytics.observation_summary(days=days, contract=contract)
    return ObservationSummaryResponse(**data)


@sub_router.get("/observation/trend", response_model=list[ObservationTrendPoint])
@_d._default_limit
async def get_observation_trend(
    request: Request,
    contract: str = Query(..., description="Contract name"),
    days: int = Query(7, ge=1, le=90, description="Analytics window in calendar days"),
    user=Depends(get_current_user),
):
    """
    Daily time-series for one contract in observation mode.

    Returns a list of daily data points showing total records validated,
    how many would have failed, and how many would have passed.

    Requires reader role or above.
    """
    points = _d._quality_analytics.observation_trend(contract=contract, days=days)
    return [ObservationTrendPoint(**p) for p in points]


@sub_router.get("/observation/fields", response_model=list[ObservationFieldFailure])
@_d._default_limit
async def get_observation_fields(
    request: Request,
    contract: str = Query(..., description="Contract name"),
    days: int = Query(7, ge=1, le=90, description="Analytics window in calendar days"),
    user=Depends(get_current_user),
):
    """
    Top failing rules/fields for a contract in observation mode.

    Returns up to 50 rules ranked by failure count descending. Use this to
    identify which rules would cause the most rejections if enforcement were
    enabled.

    Requires reader role or above.
    """
    items = _d._quality_analytics.observation_fields(contract=contract, days=days)
    return [ObservationFieldFailure(**i) for i in items]
