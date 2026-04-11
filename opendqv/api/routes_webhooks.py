from fastapi import APIRouter, Body, Depends, HTTPException, Request

import opendqv.api.deps as _d
from opendqv.security.auth import get_current_user, get_current_role

sub_router = APIRouter()


@sub_router.post("/webhooks")
@_d._default_limit
async def register_webhook(
    request: Request,
    body: dict = Body(..., description="Webhook registration: {url, events?, contracts?}"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Register a webhook to receive notifications on validation events.

    Body:
      - url (required): The URL to POST notifications to.
      - events (optional): List of event types to subscribe to.
        Valid: "opendqv.validation.failed", "opendqv.validation.warning", "opendqv.batch.failed".
        Defaults to all events.
      - contracts (optional): List of contract names to filter on. Defaults to all.

    Webhooks are persisted in SQLite and survive server restarts.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required.")
    events = body.get("events")
    contracts = body.get("contracts")
    try:
        hook = _d.webhook_manager.register(url=url, events=events, contracts=contracts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "registered", "webhook": hook}


@sub_router.get("/webhooks")
@_d._default_limit
async def list_webhooks(
    request: Request,
    user=Depends(get_current_user),
):
    """List all registered webhooks."""
    return _d.webhook_manager.list_hooks()


@sub_router.delete("/webhooks")
@_d._default_limit
async def unregister_webhook(
    request: Request,
    body: dict = Body(..., description="Webhook to remove: {url}"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Unregister a webhook by URL."""
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required.")
    removed = _d.webhook_manager.unregister(url)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No webhook registered for '{url}'.")
    return {"status": "unregistered", "url": url}
