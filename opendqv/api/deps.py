import asyncio
import os
import logging
import threading

from fastapi import APIRouter, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

import opendqv.config as config
from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import ContractStatus
from opendqv.core.worker_heartbeat import heartbeat
from opendqv.core.webhooks import WebhookManager
from opendqv.core.federation import FederationLog
from opendqv.core.node_health import NodeHealthStateMachine
from opendqv.core.isolation_log import IsolationLog
from opendqv.core.quality_stats import QualityStats
from opendqv.core.quality_analytics import QualityAnalytics
from opendqv.core.explainer import quick_fix

_federation_log = FederationLog(config.DB_PATH)
_node_health = NodeHealthStateMachine(config.DB_PATH)
_isolation_log = IsolationLog(config.DB_PATH)
_quality_stats = QualityStats(config.DB_PATH)
_quality_analytics = QualityAnalytics(config.DB_PATH)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["OpenDQV"])
limiter = Limiter(key_func=get_remote_address)


def _make_limit(rate_str: str):
    if rate_str.strip().lower() in config._RATE_LIMIT_OFF_VALUES:
        def _noop(func):
            return func
        return _noop
    return limiter.limit(rate_str)


_validate_limit = _make_limit(config.RATE_LIMIT_VALIDATE)
_default_limit = _make_limit(config.RATE_LIMIT_DEFAULT)
_tokens_limit = _make_limit(config.RATE_LIMIT_TOKENS)


def _get_contract_or_404(name: str):
    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return contract


def _get_contract_versioned_or_404(name: str, version: str):
    contract = registry.get(name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' version '{version}' not found")
    return contract


def _get_contract_hash(contract_name: str):
    history = registry.get_history(contract_name)
    if not history:
        return None, None
    last = history[-1]
    return last.get("entry_hash"), last.get("content_hash")


def _check_validate_in_states(contract, contract_name: str, allow_draft: bool) -> None:
    if not allow_draft and hasattr(contract, 'validate_in_states') and contract.validate_in_states:
        if contract.status.value not in contract.validate_in_states:
            raise HTTPException(
                status_code=422,
                detail=f"Contract '{contract_name}' is in status '{contract.status.value}' which is not in validate_in_states {contract.validate_in_states}"
            )


def _assert_contract_mutable(contract, name: str, user: str, op: str) -> None:
    if contract.status == ContractStatus.ACTIVE:
        logger.warning(
            "rule_mutation_blocked contract=%s op=%s caller=%s status=active",
            name, op, user,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{name}' is ACTIVE. Rule mutations are not permitted on active contracts. "
                f"To modify rules, use POST /api/v1/contracts/{name}/version to create a new draft version."
            ),
        )
    if config.CONTRACT_EDIT_MODE != "auto":
        logger.info("contract_edit_mode=%s contract=%s op=%s", config.CONTRACT_EDIT_MODE, name, op)


MASK_RECORD_VALUES: str = os.environ.get("OPENDQV_MASK_RECORD_VALUES", "false").lower()

EXPLAIN_PUBLIC: bool = os.environ.get("OPENDQV_EXPLAIN_PUBLIC", "false").lower() == "true"

MAX_UPLOAD_MB: int = int(os.environ.get("OPENDQV_MAX_UPLOAD_MB", "10"))


async def _async_record_quality_stats(
    contract_name: str,
    contract_version: str,
    context,
    total: int,
    passed: int,
    failed: int,
    rule_failure_counts: dict,
    agent_id: str,
    mode: str = "enforcement",
    event_id: str = "",
    caller_principal: str = "",
    effective_rule_hash: str = "",
    entry_hash: str = "",
    content_hash: str = "",
) -> None:
    try:
        await asyncio.to_thread(
            _quality_stats.record_batch,
            contract_name=contract_name,
            contract_version=contract_version,
            context=context,
            total=total,
            passed=passed,
            failed=failed,
            rule_failure_counts=rule_failure_counts,
            agent_id=agent_id,
            mode=mode,
            event_id=event_id,
            caller_principal=caller_principal,
            effective_rule_hash=effective_rule_hash,
            entry_hash=entry_hash,
            content_hash=content_hash,
        )
    except Exception:
        logger.exception("async quality_stats.record_batch failed — stats may be incomplete")


async def _async_heartbeat(contract_name: str, contract_version: str) -> None:
    try:
        await asyncio.to_thread(heartbeat.record_validation, contract_name, contract_version)
    except Exception:
        logger.exception("async heartbeat.record_validation failed")


def _parse_upload(content: bytes, filename: str):
    import io
    import pandas as pd

    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_UPLOAD_MB}MB limit. "
                   f"Set OPENDQV_MAX_UPLOAD_MB to increase. Received: {len(content) // 1024}KB",
        )
    try:
        if filename.endswith(".parquet"):
            return pd.read_parquet(io.BytesIO(content))
        elif filename.endswith(".csv"):
            return pd.read_csv(io.BytesIO(content))
        else:
            try:
                return pd.read_csv(io.BytesIO(content))
            except Exception:
                raise HTTPException(status_code=400, detail="Unsupported file format. Use CSV or Parquet.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file format. Expected CSV or Parquet.")


import re as _re

_CONTRACT_NAME_RE = _re.compile(r'^[A-Za-z0-9_-]{1,100}$')

def _validate_contract_name(name: str) -> None:
    if not _CONTRACT_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid contract name '{name}'. "
                "Names must contain only letters, digits, hyphens, and underscores (1–100 chars)."
            ),
        )


registry: ContractRegistry = None

webhook_manager = WebhookManager(config.DB_PATH)

_sse_lock = threading.Lock()
_sse_active = 0


def _mask_errors(errors: list, mask_mode: str = None) -> list:
    import hashlib as _hl
    mode = MASK_RECORD_VALUES if mask_mode is None else str(mask_mode).lower()
    if mode in ("false", "0", "no", ""):
        return errors
    result = []
    for e in errors:
        if "value" in e:
            raw = str(e["value"])
            if mode == "hash":
                masked = _hl.sha256(raw.encode()).hexdigest()[:12]
            else:
                masked = "[REDACTED]"
            e = {**e, "value": masked}
        result.append(e)
    return result


def _add_suggested_fixes(errors: list, rules: list) -> list:
    rule_meta = {r.name: (r.type, getattr(r, "compare_to", "") or "") for r in rules}
    result = []
    for e in errors:
        rule_type, compare_to = rule_meta.get(e.get("rule", ""), ("", ""))
        fix = (
            quick_fix(rule_type, e.get("message", ""), compare_to=compare_to)
            if rule_type
            else None
        )
        result.append({**e, "suggested_fix": fix})
    return result


def set_registry(reg: ContractRegistry):
    global registry
    registry = reg
