"""
api/routes.py — router assembly shim.

All shared state and helpers live in api/deps.py. This file assembles the
master router from domain sub-routers and re-exports the names that external
code (main.py, tests) currently imports from this module.
"""
import api.deps as _d
from api.deps import router
from api.routes_validation import sub_router as _validation_router
from api.routes_contracts import sub_router as _contracts_router
from api.routes_imports import sub_router as _imports_router
from api.routes_profiler import sub_router as _profiler_router
from api.routes_tokens import sub_router as _tokens_router
from api.routes_webhooks import sub_router as _webhooks_router
from api.routes_analytics import sub_router as _analytics_router
from api.routes_federation import sub_router as _federation_router

router.include_router(_validation_router)
router.include_router(_contracts_router)
router.include_router(_imports_router)
router.include_router(_profiler_router)
router.include_router(_tokens_router)
router.include_router(_webhooks_router)
router.include_router(_analytics_router)
router.include_router(_federation_router)


def __getattr__(name: str):
    """Module-level __getattr__ — delegates to api.deps for live lookup."""
    return getattr(_d, name)
