"""
JWT PAT authentication.

Thread-safe per-request sqlite3 connections. SECRET_KEY from env vars.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

from fastapi import Depends, HTTPException, Header
import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

import config

VALID_ROLES: frozenset = frozenset(
    {"validator", "reader", "auditor", "editor", "approver", "admin"}
)

ALGORITHM = config.ALGORITHM
SECRET_KEY = config.SECRET_KEY
TOKEN_EXPIRY_DAYS = config.TOKEN_EXPIRY_DAYS
DB_PATH = config.DB_PATH

pwd_context = CryptContext(schemes=["bcrypt"])


@contextmanager
def get_db():
    """Thread-safe per-request database connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the tokens table."""
    with get_db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tokens "
            "(token TEXT PRIMARY KEY, username TEXT, expiry DATETIME, scopes TEXT, "
            "role TEXT NOT NULL DEFAULT 'validator')"
        )
        # Migrate: add role column if this is an existing DB without it
        try:
            conn.execute("ALTER TABLE tokens ADD COLUMN role TEXT NOT NULL DEFAULT 'validator'")
            conn.commit()
        except Exception:
            pass  # column already exists
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()


init_db()


def _ensure_utc(dt: datetime) -> datetime:
    """Return dt with UTC timezone attached; no-op if already tz-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def create_pat(username: str, scopes: str = "read:write", expiry_days: int = None, role: str = "validator") -> dict:
    """
    Create a new Personal Access Token.

    Args:
        username: Identifies the source system (e.g. "salesforce-prod", "sap-hr")
        scopes: Permission scopes (default "read:write")
        expiry_days: Override TOKEN_EXPIRY_DAYS for this token (e.g. 90, 365)

    Returns:
        dict with token, username, expires_at, expiry_days
    """
    days = expiry_days if expiry_days is not None else TOKEN_EXPIRY_DAYS
    expire = datetime.now(timezone.utc) + timedelta(days=days)
    to_encode = {"sub": username, "exp": expire, "scopes": scopes, "role": role}
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (token, username, expiry, scopes, role) VALUES (?, ?, ?, ?, ?)",
            (token, username, expire.isoformat(), scopes, role),
        )
        conn.commit()
    return {
        "token": token,
        "username": username,
        "expires_at": expire.isoformat(),
        "expiry_days": days,
        "role": role,
    }


def list_tokens() -> list[dict]:
    """List all active (non-expired) tokens with metadata."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT username, expiry, scopes, role FROM tokens ORDER BY username"
        ).fetchall()

    now = datetime.now(timezone.utc)
    result = []
    for username, expiry_str, scopes, role in rows:
        expiry = _ensure_utc(datetime.fromisoformat(expiry_str))
        days_remaining = (expiry - now).days
        result.append({
            "username": username,
            "expires_at": expiry.isoformat(),
            "days_remaining": max(days_remaining, 0),
            "expired": expiry < now,
            "scopes": scopes,
            "role": role,
        })
    return result


def revoke_pat(token: str) -> dict:
    """Revoke a PAT."""
    with get_db() as conn:
        conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
        conn.commit()
    return {"status": "revoked"}


def revoke_by_username(username: str) -> dict:
    """Revoke all tokens for a given source system."""
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tokens WHERE username = ?", (username,))
        conn.commit()
    return {"status": "revoked", "tokens_revoked": cursor.rowcount}


async def get_current_user(authorization: str = Header(None)) -> str:
    """
    FastAPI dependency — validates Bearer token, returns username.

    In open mode (AUTH_MODE=open), auth is skipped and returns "anonymous".
    In token mode (AUTH_MODE=token), a valid PAT is required.
    """
    if config.AUTH_MODE == "open":
        # Open mode — no auth required. Extract username from token if present, else anonymous.
        if authorization and authorization.startswith("Bearer "):
            token = authorization.split("Bearer ", 1)[1].strip()
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                return payload.get("sub", "anonymous")
            except InvalidTokenError:
                pass
        return "anonymous"

    # Token mode — full PAT validation
    if not authorization:
        raise HTTPException(status_code=401, detail="No token provided. Set AUTH_MODE=open to disable auth.")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = authorization.split("Bearer ", 1)[1].strip()

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        with get_db() as conn:
            row = conn.execute("SELECT expiry FROM tokens WHERE token = ?", (token,)).fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Token not found or revoked")

        expiry = _ensure_utc(datetime.fromisoformat(row[0]))
        if expiry < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Token expired")

        return username

    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_role(authorization: str = Header(None)) -> str:
    """
    FastAPI dependency — returns the role of the authenticated user.

    In open mode returns 'admin' (development convenience).
    In token mode extracts the role claim from the JWT.
    """
    if config.AUTH_MODE == "open":
        return "admin"

    if not authorization or not authorization.startswith("Bearer "):
        return "validator"  # unauthenticated → least-privileged role

    token = authorization.split("Bearer ", 1)[1].strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("role", "validator")
    except InvalidTokenError:
        return "validator"


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory — enforces that the caller holds one of the allowed roles.

    Usage in a route:
        @router.post("/admin/action")
        async def admin_action(_=Depends(require_role("admin", "governance_admin"))):
            ...
    """
    async def _check(
        username: str = Depends(get_current_user),
        role: str = Depends(get_current_role),
    ) -> str:
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' is not permitted for this action. "
                       f"Required: {list(allowed_roles)}",
            )
        return username
    return _check
