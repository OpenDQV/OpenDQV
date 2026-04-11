"""
Central configuration — all settings from environment variables.
"""

import os
import socket
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Engine version — single source of truth for regulatory audit trails.
# Priority: pyproject.toml (source / dev installs) → package metadata (pip install) → "unknown"
# tomllib is stdlib from Python 3.11, so no extra dependency.
try:
    import tomllib as _tomllib
    # pyproject.toml is one level above the opendqv/ package directory
    _pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if _pyproject.exists():
        ENGINE_VERSION = _tomllib.loads(_pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]
    else:
        ENGINE_VERSION = version("opendqv")
except Exception:
    try:
        ENGINE_VERSION = version("opendqv")
    except PackageNotFoundError:
        ENGINE_VERSION = "unknown"

# Paths
# _PACKAGE_DIR is where the opendqv/ package lives (used for py.typed etc.)
# BASE_DIR is the project root (one level up) — where contracts/, docs/ etc. live.
# For pip installs, BASE_DIR points to site-packages parent which won't have
# contracts/ — users must set OPENDQV_CONTRACTS_DIR or use `opendqv init`.
_PACKAGE_DIR = Path(__file__).resolve().parent
BASE_DIR = _PACKAGE_DIR.parent
CONTRACTS_DIR = Path(os.environ.get("OPENDQV_CONTRACTS_DIR", str(BASE_DIR / "contracts")))

# Security
DEFAULT_SECRET_KEY: str = "change-me-to-a-random-secret-key"
SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
ALGORITHM = "HS256"
TOKEN_EXPIRY_DAYS = int(os.environ.get("TOKEN_EXPIRY_DAYS", "30"))
DB_PATH = os.environ.get("OPENDQV_DB_PATH", str(BASE_DIR / "opendqv.db"))

# Auth mode: "open" (no auth, for dev/POC) or "token" (PAT required, for production)
AUTH_MODE = os.environ.get("AUTH_MODE", "open")

# Storage backend: "sqlite" (default, zero dependencies) or "postgres" (enterprise tier).
# When set to "postgres", OPENDQV_DB_URL must also be provided.
DB_BACKEND = os.environ.get("OPENDQV_DB_BACKEND", "sqlite")
DB_URL = os.environ.get("OPENDQV_DB_URL", "")

# Node identity — used to tag contract history snapshots.
# Set OPENDQV_NODE_ID in the environment for meaningful names (e.g. "eu-west-1", "singapore-prod").
# Defaults to the machine hostname so single-node deployments work without any config.
OPENDQV_NODE_ID = os.environ.get("OPENDQV_NODE_ID", socket.gethostname())

# API
API_URL = os.environ.get("API_URL", "http://localhost:8000")

# MCP remote mode — when OPENDQV_MCP_API_URL is set, the MCP server proxies all tool
# calls to the central OpenDQV API over HTTP instead of reading local files.
# This makes MCP validation events visible in the monitoring UI and ensures agents
# always see the live central contract version.
# Leave unset (default) for local/laptop mode — no network dependency.
MCP_API_URL = os.environ.get("OPENDQV_MCP_API_URL", "")
MCP_TOKEN = os.environ.get("OPENDQV_MCP_TOKEN", "")

RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "120/minute")
RATE_LIMIT_VALIDATE = os.environ.get("RATE_LIMIT_VALIDATE", "300/minute")
RATE_LIMIT_TOKENS = os.environ.get("RATE_LIMIT_TOKENS", "10/minute")

# Federation — presence of OPENDQV_UPSTREAM switches the node into federated mode.
# In standalone mode (default) these vars are unused and the node behaves exactly as today.
UPSTREAM_URL = os.environ.get("OPENDQV_UPSTREAM", "")
JOIN_TOKEN = os.environ.get("OPENDQV_JOIN_TOKEN", "")
IS_FEDERATED = bool(UPSTREAM_URL)

# Auth convenience predicates — derived from AUTH_MODE, never set independently.
IS_OPEN_MODE: bool = AUTH_MODE == "open"

# Rate-limit sentinel values — "off", "0", or "disabled" disable per-IP limiting.
_RATE_LIMIT_OFF_VALUES: frozenset = frozenset({"off", "0", "disabled"})

# Audit log mode.
# "basic"  — SHA-256 forward-linked hash chain, no cryptographic signing.
#            Safe for community use. AUDIT_MODE=basic is the only supported mode
#            until KMS infrastructure is configured.
# "signed" — ECDSA P-256 KMS-backed signing on top of the hash chain.
#            Enterprise/production only. Requires KMS configuration.
AUDIT_MODE = os.environ.get("AUDIT_MODE", "basic")

# Isolation policy — how long a node may operate without upstream contact before
# it must re-sync. After this window even fail-safe-open nodes stop accepting
# validation requests until they reconnect.
MAX_ISOLATION_HOURS = int(os.environ.get("OPENDQV_MAX_ISOLATION_HOURS", "72"))

# Draft validation behaviour.
# False (default): DRAFT contracts serve validation normally; a WARNING is logged.
# True: DRAFT contracts serve the last-active ruleset snapshot with X-Contract-Status: draft-fallback.
#       If no snapshot exists the request is served normally (safe default).
STRICT_DRAFT_VALIDATION = os.environ.get("OPENDQV_STRICT_DRAFT_VALIDATION", "false").lower() == "true"

# Contract edit mode — hook for future enterprise maker-checker governance.
# "auto" (default): rule edits take effect immediately on the live contract.
#   No status transition is performed. The flag exists as a code anchor for
#   future enterprise governance mode — do NOT implement auto-draft transitions.
# "maker_checker": reserved for enterprise tier (opendqv-enterprise package).
#   In OSS, behaves identically to "auto".
CONTRACT_EDIT_MODE = os.environ.get("OPENDQV_CONTRACT_EDIT_MODE", "auto")

# Batch validation — maximum records per batch call.
# Prevents single large batches from OOM-ing a worker (DoS protection).
# Override with OPENDQV_MAX_BATCH_ROWS for operators who need larger batches.
MAX_BATCH_ROWS = int(os.environ.get("OPENDQV_MAX_BATCH_ROWS", "10000"))

# Proxy headers — set to "true" when deployed behind a reverse proxy.
# Enables X-Forwarded-For trust so rate limiting and audit logs record the
# real client IP rather than the proxy IP. Disabled by default to prevent
# header spoofing on direct-internet deployments.
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "false").lower() == "true"

# SSE connection cap — max concurrent /federation/events clients per worker.
# Prevents SSE long-poll connections from starving validation endpoints.
# With 4 workers the system-wide cap = MAX_SSE_CONNECTIONS × 4.
MAX_SSE_CONNECTIONS = int(os.environ.get("OPENDQV_MAX_SSE_CONNECTIONS", "50"))

# Health endpoint detail level.
# false (default): /health returns only status + node_state — safe for public internet.
# true: /health returns full config details (auth_mode, maker_checker_enforced, worker counts).
#       Enable only when /health is protected by network controls or reverse-proxy auth.
HEALTH_DETAIL = os.environ.get("OPENDQV_HEALTH_DETAIL", "false").lower() == "true"

# Demo mode — set by docker-compose.demo.yml to show a startup banner and confirm
# the environment is intentionally pre-seeded and running with AUTH_MODE=open.
DEMO_MODE: bool = os.environ.get("DEMO_MODE", "false").lower() == "true"


def validate_config() -> None:
    """
    Validate all configuration values at startup.

    Raises ValueError with a clear message if any env var is misconfigured.
    Call this once from the application lifespan so bad config surfaces
    immediately rather than crashing mid-request.
    """
    import re

    # AUTH_MODE
    if AUTH_MODE not in {"open", "token"}:
        raise ValueError(
            f"AUTH_MODE='{AUTH_MODE}' is invalid. Must be 'open' or 'token'."
        )

    # DB_BACKEND
    if DB_BACKEND not in {"sqlite", "postgres"}:
        raise ValueError(
            f"OPENDQV_DB_BACKEND='{DB_BACKEND}' is invalid. Must be 'sqlite' or 'postgres'."
        )

    # DB_URL required when postgres
    if DB_BACKEND == "postgres" and not DB_URL:
        raise ValueError(
            "OPENDQV_DB_URL must be set when OPENDQV_DB_BACKEND=postgres."
        )

    # Integer env vars: name → (value, min_allowed)
    _int_checks = {
        "TOKEN_EXPIRY_DAYS": (TOKEN_EXPIRY_DAYS, 1),
        "OPENDQV_MAX_BATCH_ROWS": (MAX_BATCH_ROWS, 1),
        "OPENDQV_MAX_SSE_CONNECTIONS": (MAX_SSE_CONNECTIONS, 1),
        "OPENDQV_MAX_ISOLATION_HOURS": (MAX_ISOLATION_HOURS, 1),
    }
    for var_name, (value, min_val) in _int_checks.items():
        if not isinstance(value, int) or value < min_val:
            raise ValueError(
                f"{var_name}={value!r} is invalid. Must be an integer >= {min_val}."
            )

    # RATE_LIMIT format: "<number>/(second|minute|hour)"
    _rate_limit_pattern = re.compile(r"^\d+/(second|minute|hour)$")
    for var_name, value in [
        ("RATE_LIMIT_DEFAULT", RATE_LIMIT_DEFAULT),
        ("RATE_LIMIT_VALIDATE", RATE_LIMIT_VALIDATE),
        ("RATE_LIMIT_TOKENS", RATE_LIMIT_TOKENS),
    ]:
        # "off", "0", "disabled" are valid sentinel values
        if value not in _RATE_LIMIT_OFF_VALUES and not _rate_limit_pattern.match(value):
            raise ValueError(
                f"{var_name}='{value}' is invalid. "
                f"Expected format '<number>/(second|minute|hour)' or one of: "
                f"{sorted(_RATE_LIMIT_OFF_VALUES)}."
            )
