"""
OpenDQV — Open Data Quality Validation Service

Stateless validation service: source systems call this API before writing data.
Like a bouncer at the door — bad data doesn't get in.
"""

import logging
import tomllib
from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path

try:
    APP_VERSION = _pkg_version("opendqv")
except PackageNotFoundError:
    # Package not installed (dev environment) — read directly from pyproject.toml
    # so this never drifts from the single version source of truth.
    _pyproject = Path(__file__).parent / "pyproject.toml"
    APP_VERSION = tomllib.loads(_pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from strawberry.fastapi import GraphQLRouter

import config
from api.routes import router, limiter, set_registry as set_routes_registry
from security.auth import init_db as _init_auth_db
from api.graphql_schema import schema, set_registry as set_graphql_registry
from core.contracts import ContractRegistry
from core.worker_heartbeat import heartbeat as worker_heartbeat
from core.node_health import node_health
from core.isolation_log import isolation_log
from monitoring import instrument_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

logger = logging.getLogger("opendqv")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and graceful shutdown."""
    # ── Startup ───────────────────────────────────────────────────────
    logger.info("OpenDQV worker starting (pid=%d)", __import__("os").getpid())
    _init_auth_db()
    yield
    # ── Shutdown — flush any pending in-memory heartbeat counts ───────
    try:
        worker_heartbeat.flush()
        logger.info("Worker heartbeat flushed on shutdown")
    except Exception as exc:
        logger.debug("Heartbeat flush on shutdown failed (non-fatal): %s", exc)


app = FastAPI(
    title="OpenDQV",
    description=(
        "Open-source rule-based data quality validation service. "
        "Centralized, validation API — record values are never stored — shift-left DQ for the enterprise. "
        "Source systems call /api/v1/validate before writing data. "
        "Bad data is blocked at the door."
    ),
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Proxy headers (enable when behind a reverse proxy / load balancer) ─
# Set TRUST_PROXY_HEADERS=true in the environment to trust X-Forwarded-For.
# WARNING: only enable this when the server is not directly internet-facing.
if config.TRUST_PROXY_HEADERS:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    logger.info("ProxyHeadersMiddleware enabled (TRUST_PROXY_HEADERS=true)")

# ── Wire isolation log observer ──────────────────────────────────────
node_health.add_observer(isolation_log.observe_state_change)

# ── Security startup checks ───────────────────────────────────────────
_DEFAULT_SECRET = config.DEFAULT_SECRET_KEY
_sec_issues = []
if config.SECRET_KEY == _DEFAULT_SECRET:
    _sec_issues.append(
        "SECRET_KEY is the default — tokens issued by this node can be forged "
        "by anyone who reads config.py. "
        "Generate a real secret: SECRET_KEY=$(python -c \"import secrets; print(secrets.token_hex(32))\")"
    )
if config.IS_OPEN_MODE:
    _sec_issues.append(
        "AUTH_MODE=open — all callers are granted admin access without a token."
    )
if _sec_issues:
    _divider = "=" * 72
    _body = "\n".join(f"  • {issue}" for issue in _sec_issues)
    logger.critical(
        f"\n{_divider}\n"
        "  WARNING: DO NOT EXPOSE THIS NODE TO UNTRUSTED NETWORKS\n"
        f"{_body}\n"
        "  Set AUTH_MODE=token and SECRET_KEY to a cryptographically random value.\n"
        "  See docs/production_deployment.md\n"
        f"{_divider}"
    )
else:
    logger.info("AUTH_MODE=token — PAT authentication enabled.")

if config.DEMO_MODE:
    logger.warning("=" * 60)
    logger.warning("  DEMO MODE — AUTH_MODE=open, pre-seeded data")
    logger.warning("  API:  http://localhost:8080")
    logger.warning("  Docs: http://localhost:8080/docs")
    logger.warning("  UI:   http://localhost:8502")
    logger.warning("=" * 60)

# ── Initialize contract registry ─────────────────────────────────────
registry = ContractRegistry(config.CONTRACTS_DIR)
set_routes_registry(registry)
set_graphql_registry(registry)

# ── Wire rate limiter ────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Startup warnings when rate limiting is disabled on the hot path
_rl_off = config._RATE_LIMIT_OFF_VALUES
if config.RATE_LIMIT_VALIDATE.strip().lower() in _rl_off:
    logger.warning(
        "RATE_LIMIT_VALIDATE=off — per-IP rate limiting is DISABLED on POST /validate. "
        "Ensure your reverse proxy enforces rate limits before exposing this node to the internet."
    )
if config.RATE_LIMIT_DEFAULT.strip().lower() in _rl_off:
    logger.warning(
        "RATE_LIMIT_DEFAULT=off — per-IP rate limiting is DISABLED on all non-validate endpoints. "
        "Ensure your reverse proxy enforces rate limits before exposing this node to the internet."
    )

# ── X-Auth-Mode header — returned on every response for observability ─
# Monitoring systems can check any response for `X-Auth-Mode: open` to
# confirm that token auth is enforced before exposing a node to the internet.
@app.middleware("http")
async def add_auth_mode_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return response

# ── Mount REST API ───────────────────────────────────────────────────
app.include_router(router)

# ── Mount GraphQL ────────────────────────────────────────────────────
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

# ── Mount Prometheus metrics ─────────────────────────────────────────
instrument_app(app)


def _maker_checker_enforced() -> bool:
    """Maker-checker is enforced only in token mode. In open mode all callers get admin role."""
    return config.AUTH_MODE == "token"


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "OpenDQV",
        "version": APP_VERSION,
        "status": "ready",
        "auth_mode": config.AUTH_MODE,
        "maker_checker_enforced": _maker_checker_enforced(),
        "docs": "/docs",
        "graphql": "/graphql",
        "contracts_loaded": len(registry.list_contracts()),
    }


@app.get("/health", tags=["Health"])
async def health():
    heartbeats = worker_heartbeat.get_heartbeats()
    stale = worker_heartbeat.get_stale_workers(max_age_seconds=300)
    state = node_health.current_state()
    enforced = _maker_checker_enforced()
    if not enforced:
        logger.warning(
            "SECURITY: auth_mode=open — maker-checker bypassed, all callers have admin role. "
            "Never use open mode outside of local development."
        )
    # Minimal response — safe for unauthenticated callers on public internet.
    resp: dict = {
        "status": "healthy",
        "opendqv_node_state": state.value,
        "auth_mode": config.AUTH_MODE,
        "secret_key_insecure": config.SECRET_KEY == config.DEFAULT_SECRET_KEY,
    }
    # Extended detail — only exposed when OPENDQV_HEALTH_DETAIL=true (network-protected deployments).
    if config.HEALTH_DETAIL:
        resp.update({
            "auth_mode": config.AUTH_MODE,
            "maker_checker_enforced": enforced,
            "contracts_loaded": len(registry.list_contracts()),
            "worker_count": len({h["worker_pid"] for h in heartbeats}),
            "stale_worker_count": len({h["worker_pid"] for h in stale}),
            "isolated_since": node_health.isolated_since(),
            "rate_limit_validate": config.RATE_LIMIT_VALIDATE,
            "rate_limit_validate_active": config.RATE_LIMIT_VALIDATE.strip().lower() not in _rl_off,
        })
    return resp
