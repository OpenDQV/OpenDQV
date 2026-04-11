import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.security.auth import get_current_user

sub_router = APIRouter()


@sub_router.get("/federation/status")
@_d._default_limit
async def federation_status(request: Request, user=Depends(get_current_user)):
    """
    Return the federation status of this node.

    Standalone nodes: is_federated=False, upstream_url="".
    Federated nodes: is_federated=True, upstream_url set.

    The node_state reflects the current health state machine state:
    online / degraded / isolated.
    """
    return {
        "opendqv_node_id": config.OPENDQV_NODE_ID,
        "is_federated": config.IS_FEDERATED,
        "upstream_url": config.UPSTREAM_URL or None,
        "opendqv_node_state": _d._node_health.current_state().value,
        "audit_mode": config.AUDIT_MODE,
        "contracts_loaded": len(_d.registry.list_contracts()),
        "time_in_state_seconds": _d._node_health.time_in_current_state(),
        "isolated_since": _d._node_health.isolated_since(),
    }


@sub_router.get("/federation/log")
@_d._default_limit
async def federation_log_endpoint(
    request: Request,
    since: int = Query(0, description="Return events with lsn > this value (replication cursor)"),
    contract: str = Query(None, description="Filter by contract name"),
    user=Depends(get_current_user),
):
    """
    Return federation log events since a given LSN.

    The LSN (log sequence number) is the primary key of the federation_log table
    and acts as a replication cursor. Downstream nodes call this endpoint to pull
    changes they haven't yet processed:

        GET /api/v1/federation/log?since=42

    Returns all events with lsn > 42, ordered by lsn ascending. The caller
    advances its local cursor to the highest lsn in the response.

    In standalone mode this log is empty unless events were manually inserted.
    """
    events = _d._federation_log.get_since(since, contract_name=contract)
    return {
        "opendqv_node_id": config.OPENDQV_NODE_ID,
        "since": since,
        "count": len(events),
        "events": events,
    }


@sub_router.get("/federation/health")
@_d._default_limit
async def federation_health(
    request: Request,
    log_limit: int = Query(20, description="Maximum health log entries to return"),
    user=Depends(get_current_user),
):
    """
    Return detailed node health data for the federation control plane.

    Includes:
    - Current node state (online / degraded / isolated)
    - Recent state transition log
    - Open isolation events (currently in isolation)
    - Recent isolation event history (compliance audit trail)

    The control plane dashboard polls this endpoint to surface stale or
    isolated nodes that require governance review.
    """
    return {
        "opendqv_node_id": config.OPENDQV_NODE_ID,
        "opendqv_node_state": _d._node_health.current_state().value,
        "time_in_state_seconds": _d._node_health.time_in_current_state(),
        "isolated_since": _d._node_health.isolated_since(),
        "health_log": _d._node_health.get_log(limit=log_limit),
        "open_isolation_events": _d._isolation_log.get_open_events(),
        "recent_isolation_events": _d._isolation_log.get_events(limit=log_limit),
    }


@sub_router.post("/federation/register")
@_d.limiter.limit("5/minute")
async def federation_register(
    request: Request,
    body: dict = Body({}, description="Node registration payload"),
):
    """
    Register this node with an upstream authority node.

    This endpoint is a stub in the OSS tier. Node registration — including
    join token validation, topology recording, and contract bootstrapping —
    is part of the enterprise federation tier.

    To enable federation:
    1. Set OPENDQV_UPSTREAM=https://your-authority-node:8000
    2. Set OPENDQV_JOIN_TOKEN=<token-issued-by-authority>
    3. Restart the node — bootstrap happens automatically on startup

    See https://opendqv.io/enterprise for access to the federation tier.
    """
    raise HTTPException(
        status_code=501,
        detail={
            "error": "federation_not_enabled",
            "message": (
                "Node registration requires the enterprise federation tier. "
                "In the OSS tier, set OPENDQV_UPSTREAM and OPENDQV_JOIN_TOKEN "
                "environment variables for automatic bootstrap on startup."
            ),
            "docs": "https://opendqv.io/enterprise",
        },
    )


@sub_router.get("/federation/sync-status")
@_d._default_limit
async def federation_sync_status(
    request: Request,
    peer: Optional[str] = Query(
        None,
        description=(
            "Peer node URL to compare with (e.g. https://peer.example.com:8000). "
            "Omit to return local contract inventory only."
        ),
    ),
    user=Depends(get_current_user),
):
    """
    Compare local contract versions with a peer node.

    Returns a diff showing which contracts have diverged — useful for:
    - Detecting schema drift between federated nodes
    - Triggering contract rollout verification
    - Manual federation health checks from CI/CD pipelines

    If peer is specified, fetches peer's /api/v1/contracts and diffs with local versions.
    Fires a `sync_diverged` webhook if any contracts have diverged.
    """
    local_contracts = _d.registry.list_contracts()
    local_index = {c["name"]: c["version"] for c in local_contracts}

    result = {
        "opendqv_node_id": config.OPENDQV_NODE_ID,
        "peer": peer,
        "local_contracts": [{"name": c["name"], "version": c["version"]} for c in local_contracts],
        "peer_contracts": [],
        "diverged": [],
        "peer_error": None,
    }

    if peer:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(f"{peer.rstrip('/')}/api/v1/contracts")
                resp.raise_for_status()
                peer_contracts = resp.json()
                result["peer_contracts"] = [
                    {"name": c["name"], "version": c["version"]} for c in peer_contracts
                ]
                peer_index = {c["name"]: c["version"] for c in peer_contracts}

                all_names = set(local_index) | set(peer_index)
                diverged = []
                for name in sorted(all_names):
                    local_v = local_index.get(name)
                    peer_v = peer_index.get(name)
                    if local_v != peer_v:
                        diverged.append({
                            "name": name,
                            "local_version": local_v,
                            "peer_version": peer_v,
                        })
                result["diverged"] = diverged

                if diverged:
                    await _d.webhook_manager.notify("sync_diverged", {
                        "opendqv_node_id": config.OPENDQV_NODE_ID,
                        "peer": peer,
                        "diverged_contracts": [d["name"] for d in diverged],
                        "count": len(diverged),
                    })
        except Exception as exc:
            result["peer_error"] = str(exc)

    return result


@sub_router.get("/federation/events")
async def federation_events(
    request: Request,
    poll_interval: float = Query(5.0, description="Polling interval in seconds (default 5, min 1, max 60)"),
    heartbeat_interval: float = Query(30.0, description="Heartbeat ping interval in seconds"),
    limit: int = Query(0, description="Stop after emitting this many events (0 = unlimited; use in tests/CI)"),
    user=Depends(get_current_user),
):
    """
    Server-Sent Events stream for real-time federation updates.

    Clients connect once and receive push notifications for:
    - federation_log: new sync events (push/ack/commit/reject/isolation_*)
    - node_state:     node health transitions (online/degraded/isolated)
    - heartbeat:      keep-alive ping (default every 30s)
    - connected:      sent immediately on connection to confirm stream is live

    Event format (SSE):
        event: <event_type>
        data: <JSON payload>

    The stream is backed by SQLite polling — compatible with all deployment
    modes. On PostgreSQL backends the commercial tier replaces polling with
    LISTEN/NOTIFY for sub-second latency.

    Clients should track the `lsn` field of `federation_log` events as their
    replication cursor, passing it back via `GET /federation/log?since=<lsn>`
    after reconnect to catch up on any events missed during disconnection.
    """
    poll_interval = max(1.0, min(60.0, poll_interval))
    heartbeat_interval = max(5.0, min(300.0, heartbeat_interval))

    with _d._sse_lock:
        if _d._sse_active >= config.MAX_SSE_CONNECTIONS:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"SSE connection limit reached ({config.MAX_SSE_CONNECTIONS} "
                    f"per worker). Retry after an existing client disconnects."
                ),
            )
        _d._sse_active += 1

    async def _event_stream():
        existing = _d._federation_log.get_since(0)
        current_lsn = existing[-1]["lsn"] if existing else 0
        last_node_state = _d._node_health.current_state().value
        last_heartbeat_ts = time.monotonic()
        emitted = 0

        connected_data = json.dumps({
            "opendqv_node_id": config.OPENDQV_NODE_ID,
            "opendqv_node_state": last_node_state,
            "cursor_lsn": current_lsn,
        })
        yield f"event: connected\ndata: {connected_data}\n\n"
        emitted += 1
        if limit and emitted >= limit:
            return

        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(poll_interval)
            now = time.monotonic()

            new_events = _d._federation_log.get_since(current_lsn)
            for event in new_events:
                current_lsn = event["lsn"]
                yield f"event: federation_log\ndata: {json.dumps(event)}\n\n"
                emitted += 1
                if limit and emitted >= limit:
                    return

            new_state = _d._node_health.current_state().value
            if new_state != last_node_state:
                last_node_state = new_state
                state_data = json.dumps({
                    "opendqv_node_id": config.OPENDQV_NODE_ID,
                    "state": new_state,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                yield f"event: node_state\ndata: {state_data}\n\n"
                emitted += 1
                if limit and emitted >= limit:
                    return

            if now - last_heartbeat_ts >= heartbeat_interval:
                last_heartbeat_ts = now
                ping_data = json.dumps({
                    "opendqv_node_id": config.OPENDQV_NODE_ID,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "cursor_lsn": current_lsn,
                })
                yield f"event: heartbeat\ndata: {ping_data}\n\n"
                emitted += 1
                if limit and emitted >= limit:
                    return

    async def _tracked_stream():
        try:
            async for chunk in _event_stream():
                yield chunk
        finally:
            with _d._sse_lock:
                _d._sse_active = max(0, _d._sse_active - 1)

    return StreamingResponse(
        _tracked_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
