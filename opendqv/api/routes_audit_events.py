"""
api/routes_audit_events.py — CRT172/K1+K2 audit event surface.

Row-level retrieval and cursor-paginated listing over the quality_stats
audit table. One row per /validate or /validate/batch call, indexed by
the event_id returned in the original validation response.

Auth-gated to admin and auditor (matches /trace/verify and /config).

v2.4 caveat — per-contract auditor scoping:
    Today the auditor role is global. An auditor can pass any
    `contract` filter and read `caller_principal` values across all
    contracts. For multi-tenant SaaS deployments, per-contract
    auditor scoping must be added in security/auth.py before this
    endpoint can be safely exposed across tenants. Until then, this
    surface assumes a single-tenant trust boundary.
"""
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import opendqv.api.deps as _d
from opendqv.security.auth import get_current_role

from .models import (
    AuditEventDetail,
    AuditEventListItem,
    AuditEventListResponse,
)

sub_router = APIRouter()


def _require_audit_role(role: str) -> None:
    if role not in ("admin", "auditor"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' cannot read audit events. Required: admin or auditor.",
        )


def _encode_cursor(recorded_at: str, row_id: int) -> str:
    raw = json.dumps({"r": recorded_at, "i": row_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str) -> tuple[str, int]:
    pad = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return str(obj["r"]), int(obj["i"])
    except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {exc}") from exc


@sub_router.get(
    "/audit/events",
    response_model=AuditEventListResponse,
    tags=["Audit"],
)
async def list_audit_events(
    contract: Optional[str] = Query(None, description="Filter by contract name"),
    contract_version: Optional[str] = Query(None, description="Filter by contract version"),
    context: Optional[str] = Query(None, description="Filter by context override (e.g. 'salesforce')"),
    since: Optional[str] = Query(
        None,
        description=(
            "ISO 8601 UTC lower bound (inclusive). When omitted, the engine "
            "applies a 24-hour default window (`now − 24h`). The response "
            "echoes the value actually applied as `effective_since` so "
            "consumers can detect silent default-window truncation. "
            "effective_since is NOT a retention boundary — older events "
            "may still exist; pass an explicit `since` to retrieve them."
        ),
    ),
    until: Optional[str] = Query(None, description="ISO 8601 UTC end of window (exclusive)"),
    agent_id: Optional[str] = Query(None, description="Filter by caller-asserted agent_id"),
    caller_principal: Optional[str] = Query(
        None,
        description="Filter by server-derived caller_principal (JWT sub, or 'anonymous'). Trustable — cannot be spoofed.",
    ),
    valid: Optional[bool] = Query(
        None,
        description="True returns only events with failed=0 AND total_records>0. False returns only events with failed>0.",
    ),
    mode: Optional[str] = Query(None, description="'enforcement' or 'observation_only'"),
    cursor: Optional[str] = Query(None, description="Opaque cursor from a prior response's next_cursor"),
    limit: int = Query(100, ge=1, le=1000, description="Max events per page (1–1000)"),
    role: str = Depends(get_current_role),
):
    """
    List validation audit events (CRT172 / K2).

    One row per /validate or /validate/batch call. Returned in
    recorded_at DESC, id DESC order. Cursor pagination is one-way:
    pass the response's `next_cursor` back as `?cursor=` to fetch
    the next page.

    Auth-gated to admin and auditor.
    """
    _require_audit_role(role)

    if since is None:
        effective_since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    else:
        effective_since = since

    cursor_recorded_at: Optional[str] = None
    cursor_id: Optional[int] = None
    if cursor:
        cursor_recorded_at, cursor_id = _decode_cursor(cursor)

    events_raw, has_more = _d._quality_stats.list_events(
        contract=contract,
        contract_version=contract_version,
        context=context,
        since=effective_since,
        until=until,
        agent_id=agent_id,
        caller_principal=caller_principal,
        valid=valid,
        mode=mode,
        cursor_recorded_at=cursor_recorded_at,
        cursor_id=cursor_id,
        limit=limit,
    )

    next_cursor: Optional[str] = None
    if has_more and events_raw:
        last = events_raw[-1]
        next_cursor = _encode_cursor(last["recorded_at"], last["id"])

    # v2.3.23 P0-1 (Sonnet a348734a7798db94b): additive auth_mode field.
    # Read from config so a consuming system has machine-readable
    # evidence of the trust model. "open" mode means every caller is
    # admin per dev-default — regulated deployments should refuse to
    # trust the response.
    import opendqv.config as _config
    return AuditEventListResponse(
        events=[AuditEventListItem(**{k: v for k, v in e.items() if k != "id"}) for e in events_raw],
        has_more=has_more,
        next_cursor=next_cursor,
        effective_since=effective_since,
        limit=limit,
        auth_mode=_config.AUTH_MODE,
    )


@sub_router.get(
    "/audit/events/{event_id}",
    response_model=AuditEventDetail,
    tags=["Audit"],
)
async def get_audit_event(event_id: str, role: str = Depends(get_current_role)):
    """
    Fetch a single validation audit event by event_id (CRT172 / K1).

    Returns the full audit row including JSON-decoded rule_failure_counts.
    Per-record validation errors are not stored on this row — they live
    in the optional TRACE_LOG (AUDIT_MODE=signed). 404 if not found.

    Auth-gated to admin and auditor.
    """
    _require_audit_role(role)
    row = _d._quality_stats.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"event_id '{event_id}' not found")
    return AuditEventDetail(
        event_id=row["event_id"],
        contract=row["contract"],
        contract_version=row["contract_version"],
        context=row["context"],
        recorded_at=row["recorded_at"],
        total_records=row["total_records"],
        passed=row["passed"],
        failed=row["failed"],
        pass_rate_pct=row["pass_rate_pct"],
        rule_failure_counts=row["rule_failure_counts"],
        agent_id=row["agent_id"],
        caller_principal=row["caller_principal"],
        mode=row["mode"],
        effective_rule_hash=row.get("effective_rule_hash", ""),
        entry_hash=row.get("entry_hash", ""),
        content_hash=row.get("content_hash", ""),
    )
