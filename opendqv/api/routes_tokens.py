from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.security.auth import get_current_user, get_current_role, create_pat, revoke_pat, revoke_by_username, list_tokens, VALID_ROLES

sub_router = APIRouter()


@sub_router.post("/tokens/generate")
@_d._tokens_limit
async def generate_token(
    request: Request,
    username: str = Query(..., description="Source system name (e.g. 'salesforce-prod', 'sap-hr')"),
    expiry_days: int = Query(None, description="Token lifetime in days (default: TOKEN_EXPIRY_DAYS from config)"),
    role: str = Query("validator", description="Token role: validator, reader, auditor, editor, approver, admin (default: validator)"),
    _current_user: str = Depends(get_current_user),
    caller_role: str = Depends(get_current_role),
):
    """
    Generate a PAT for a source system.

    Each source system should have its own token for audit trail and revocation.
    The token is included in the response — store it securely, it won't be shown again.

    Requires the 'admin' role in AUTH_MODE=token.
    In AUTH_MODE=open, elevated roles (admin, approver, editor) are capped to 'validator'.
    """
    if not config.IS_OPEN_MODE and caller_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Generating tokens requires the 'admin' role."
        )

    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{role}'. Must be one of: {sorted(VALID_ROLES)}"
        )

    effective_role = role
    if config.IS_OPEN_MODE and role in ("admin", "approver", "editor"):
        effective_role = "validator"

    result = create_pat(username, expiry_days=expiry_days, role=effective_role)
    return {
        "pat": result["token"],
        "username": result["username"],
        "expires_at": result["expires_at"],
        "expiry_days": result["expiry_days"],
        "role": result["role"],
    }


@sub_router.get("/tokens")
@_d._tokens_limit
async def list_all_tokens(
    request: Request,
    user=Depends(get_current_user),
    caller_role: str = Depends(get_current_role),
):
    """List all registered tokens with expiry info. Token values are not shown. Requires admin role."""
    if not config.IS_OPEN_MODE and caller_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Listing tokens requires the 'admin' role.",
        )
    return list_tokens()


@sub_router.post("/tokens/revoke")
@_d._tokens_limit
async def revoke_token(
    request: Request,
    token: str = Body(..., media_type="text/plain"),
    user=Depends(get_current_user),
    caller_role: str = Depends(get_current_role),
):
    """Revoke a specific PAT by token value. Requires admin role."""
    if not config.IS_OPEN_MODE and caller_role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Revoking tokens requires the 'admin' role."
        )
    return revoke_pat(token)


@sub_router.post("/tokens/revoke/{username}")
@_d._tokens_limit
async def revoke_system_tokens(request: Request, username: str, user=Depends(get_current_user), role: str = Depends(get_current_role)):
    """Revoke all tokens for a source system. Requires admin role."""
    if not config.IS_OPEN_MODE and role != "admin":
        raise HTTPException(status_code=403, detail="Revoking all tokens for a system requires the 'admin' role.")
    return revoke_by_username(username)
