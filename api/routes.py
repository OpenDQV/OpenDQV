"""
REST API v1 for OpenDQV.

Core endpoints:
  POST /api/v1/validate       — single-record validation (the main use case)
  POST /api/v1/validate/batch  — batch validation
  GET  /api/v1/contracts       — list available contracts
  GET  /api/v1/contracts/{name} — get contract detail

Plus: token management, code generation (nice-to-have).
"""

import asyncio
import json
import os
import time
import logging
import threading
import uuid

import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from typing import Optional
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

import config
from core.contracts import ContractRegistry, validate_promotion_readiness
from core.rule_parser import ContractStatus
from core.validator import validate_record, validate_batch
from core.code_generator import generate_code
from core.importers.great_expectations import import_gx_suite, gx_suite_to_yaml, export_gx_suite
from core.importers.dbt import import_dbt_schema, dbt_schema_to_yaml
from core.importers.soda import import_soda_checks, soda_checks_to_yaml
from core.importers.csv_rules import import_csv_rules, csv_rules_to_yaml
from core.importers.odcs import import_odcs, odcs_to_yaml, contract_to_odcs_yaml
from core.importers.csvw import import_csvw, csvw_to_yaml
from core.importers.otel import import_otel, otel_to_yaml
from core.importers.ndc import import_ndc, ndc_to_yaml
from core.profiler import profile_records
from security.auth import get_current_user, get_current_role, create_pat, revoke_pat, revoke_by_username, list_tokens
from monitoring import stats
from core.webhooks import WebhookManager
from core.worker_heartbeat import heartbeat
from core.federation import FederationLog
from core.node_health import NodeHealthStateMachine
from core.isolation_log import IsolationLog
from core.quality_stats import QualityStats

# Federation singletons — share DB with the rest of the app
_federation_log = FederationLog(config.DB_PATH)
_node_health = NodeHealthStateMachine(config.DB_PATH)
_isolation_log = IsolationLog(config.DB_PATH)
_quality_stats = QualityStats(config.DB_PATH)
from .models import (
    ValidateRequest, ValidateResponse, FieldErrorResponse,
    BatchValidateRequest, BatchValidateResponse, BatchResultItem, BatchSummary,
    ContractInfo, ContractDetail, RuleInfo,
    QualityTrendPoint, QualityTrendResponse,
    ExplainErrorResponse,
    ContractHistoryResponse, ContractDiffResponse, ContractReloadResponse,
)
from core.explainer import explain_rule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["OpenDQV"])
limiter = Limiter(key_func=get_remote_address)


def _make_limit(rate_str: str):
    """
    Return a rate-limit decorator for the given rate string, or a no-op decorator
    when rate_str is "off", "0", or "disabled".

    Usage:
        RATE_LIMIT_VALIDATE=off   → no per-request counter check on hot path
        RATE_LIMIT_VALIDATE=300/minute  → standard slowapi limiting

    When "off", move rate limiting to your reverse proxy (nginx/Caddy/cloud LB)
    for both accuracy (no 4× worker multiplication) and performance (~14% overhead
    eliminated). See docs/runbook.md — "Rate Limiter Overhead at High Throughput".
    """
    if rate_str.strip().lower() in ("off", "0", "disabled"):
        def _noop(func):
            return func
        return _noop
    return limiter.limit(rate_str)


_validate_limit = _make_limit(config.RATE_LIMIT_VALIDATE)
_default_limit = _make_limit(config.RATE_LIMIT_DEFAULT)
_tokens_limit = _make_limit(config.RATE_LIMIT_TOKENS)

# SEC-009 / ACT-005: Global PII masking mode for error response 'value' fields.
# "false" (default) — values pass through unchanged
# "true"            — values replaced with "[REDACTED]"
# "hash"            — values replaced with sha256(str(value))[:12] (one-way, unlinkable to original)
MASK_RECORD_VALUES: str = os.environ.get("OPENDQV_MASK_RECORD_VALUES", "false").lower()

# SEC-010: /explain public access flag — when True, no auth required for /explain
EXPLAIN_PUBLIC: bool = os.environ.get("OPENDQV_EXPLAIN_PUBLIC", "false").lower() == "true"

# SEC-012: Upload file size limit
MAX_UPLOAD_MB: int = int(os.environ.get("OPENDQV_MAX_UPLOAD_MB", "10"))

def _parse_upload(content: bytes, filename: str):
    """Parse an uploaded CSV or Parquet file into a DataFrame.

    Raises HTTPException on size limit violation or parse failure.
    """
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
    """Raise HTTP 422 if contract_name is unsafe (path traversal, invalid chars).

    SEC-013: All contract names written to disk must match [A-Za-z0-9_-]{1,100}.
    This prevents directory traversal via names like '../../etc/passwd'.
    """
    if not _CONTRACT_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid contract name '{name}'. "
                "Names must contain only letters, digits, hyphens, and underscores (1–100 chars)."
            ),
        )


# Contract registry — initialized by main.py and set here
registry: ContractRegistry = None

# Webhook manager — persisted in SQLite
webhook_manager = WebhookManager(config.DB_PATH)

# SSE connection tracking — per-worker counter guarded by a lock.
# NOTE: with N Gunicorn workers the system-wide cap = MAX_SSE_CONNECTIONS × N.
# Set OPENDQV_MAX_SSE_CONNECTIONS to adjust per-worker limit.
_sse_lock = threading.Lock()
_sse_active = 0


def _mask_errors(errors: list, mask_mode: str = None) -> list:
    """
    SEC-009 / ACT-005: Apply PII masking to 'value' fields in error/warning dicts.

    Modes:
        "false"  — no masking (pass through)
        "true"   — replace value with "[REDACTED]"
        "hash"   — replace value with sha256(str(value))[:12] (one-way pseudonymisation)

    Args:
        errors:    List of error/warning dicts (may contain a 'value' key)
        mask_mode: Override mode string. If None, uses the module-level MASK_RECORD_VALUES.
    """
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


def set_registry(reg: ContractRegistry):
    global registry
    registry = reg


# ── Validation endpoints ─────────────────────────────────────────────

@router.post("/validate", response_model=ValidateResponse)
@_validate_limit
async def validate_single(
    request: Request,
    body: ValidateRequest,
    allow_draft: bool = Query(False, description="Allow validation against DRAFT contracts (for testing)"),
    as_of: Optional[str] = Query(
        None,
        description=(
            "ISO 8601 timestamp — validate against the contract version that was active at this point in time. "
            "Required for EMA clinical trial submissions, MiFIR regulatory reporting, and insurance claim disputes. "
            "Example: 2026-01-15T00:00:00Z"
        ),
    ),
    user=Depends(get_current_user),
):
    """
    Validate a single record against a data contract.

    This is the primary endpoint — source systems call this before writing data.
    Returns pass/fail with per-field errors. Designed for low latency.

    Only ACTIVE (and ARCHIVED) contracts can be validated against by default.
    Pass ?allow_draft=true to test DRAFT contracts during authoring.
    Pass ?as_of=<ISO8601> to validate against the contract version active at a historical point in time.
    """
    start = time.monotonic()
    trace_id = str(uuid.uuid4())
    client_ip = request.client.host if request.client else "unknown"

    # Point-in-time validation — reconstruct the historical contract state
    if as_of:
        contract = registry.contract_as_of(body.contract, as_of)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{body.contract}' not found in history at or before '{as_of}'.",
            )
    else:
        contract = registry.get(body.contract, body.version)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{body.contract}' version '{body.version}' not found",
            )

    # Status checks skipped for point-in-time (as_of) queries — historical state is authoritative
    if not as_of:
        if contract.status == ContractStatus.DRAFT and not allow_draft:
            if config.STRICT_DRAFT_VALIDATION:
                # Strict mode: serve last-active snapshot if available
                snapshot = getattr(contract, 'last_active_snapshot', None)
                if snapshot:
                    from fastapi.responses import JSONResponse
                    from core.rule_parser import Rule
                    snap_rules = [Rule(**r) for r in snapshot]
                    snap_result = validate_record(
                        body.record, snap_rules,
                        contract_name=contract.name, context=body.context,
                        sensitive_fields=getattr(contract, 'sensitive_fields', []),
                    )
                    logger.warning(
                        "draft-fallback contract=%s v%s — serving last-active snapshot",
                        contract.name, contract.version,
                    )
                    # Build the response manually so we can add the header
                    resp_data = ValidateResponse(
                        valid=snap_result["valid"],
                        record_id=body.record_id,
                        errors=[FieldErrorResponse(**e) for e in _mask_errors(snap_result["errors"])],
                        warnings=[FieldErrorResponse(**w) for w in _mask_errors(snap_result["warnings"])],
                        contract=contract.name,
                        version=contract.version,
                        owner=contract.owner or "",
                        engine_version=config.ENGINE_VERSION,
                    )
                    return JSONResponse(
                        content=resp_data.model_dump(),
                        headers={"X-Contract-Status": "draft-fallback"},
                    )
                # No snapshot — fall through to serve normally with a warning
            logger.warning(
                "Serving validation against DRAFT contract '%s' — promote to active for production use.",
                contract.name,
            )

        # Enforce validate_in_states — only allow validation against contracts in permitted states.
        # Skipped when allow_draft=true so that draft testing bypasses the state gate.
        if not allow_draft and hasattr(contract, 'validate_in_states') and contract.validate_in_states:
            if contract.status.value not in contract.validate_in_states:
                raise HTTPException(
                    status_code=422,
                    detail=f"Contract '{body.contract}' is in status '{contract.status.value}' which is not in validate_in_states {contract.validate_in_states}"
                )

    rules = registry.get_rules_with_context(contract, body.context)
    result = validate_record(
        body.record,
        rules,
        contract_name=contract.name,
        context=body.context,
        sensitive_fields=getattr(contract, 'sensitive_fields', []),
    )

    elapsed_ms = (time.monotonic() - start) * 1000
    # Structured audit log — all six fields required for BCBS 239 evidential trail:
    # caller identity, source IP, record identifier, contract+version, context, outcome.
    logger.info(
        "validate trace_id=%s caller=%s ip=%s record_id=%s contract=%s v%s context=%s "
        "valid=%s errors=%d warnings=%d %.1fms",
        trace_id, user, client_ip, body.record_id or "-",
        contract.name, contract.version, body.context or "default",
        result["valid"], len(result["errors"]), len(result["warnings"]), elapsed_ms,
    )
    stats.record(
        contract=contract.name, context=body.context, valid=result["valid"],
        error_count=len(result["errors"]), warning_count=len(result["warnings"]),
        latency_ms=elapsed_ms, errors=result["errors"], mode="single",
    )
    heartbeat.record_validation(contract.name, contract.version)

    # Webhook notifications (fire-and-forget)
    if not result["valid"]:
        await webhook_manager.notify("validation.failed", {
            "contract": contract.name,
            "contract_version": contract.version,
            "opendqv_node_id": config.OPENDQV_NODE_ID,
            "context": body.context,
            "record_id": body.record_id,
            "valid": False,
            "error_count": len(result["errors"]),
            "warning_count": len(result["warnings"]),
            "violations": result["errors"],
        })
    elif result["warnings"]:
        await webhook_manager.notify("validation.warning", {
            "contract": contract.name,
            "contract_version": contract.version,
            "opendqv_node_id": config.OPENDQV_NODE_ID,
            "context": body.context,
            "record_id": body.record_id,
            "valid": True,
            "error_count": 0,
            "warning_count": len(result["warnings"]),
            "violations": result["warnings"],
        })

    # ACT-038-05: include contract hash (entry_hash from hash chain) for audit evidence
    _latest_history = registry.get_history(contract.name)
    _contract_hash = _latest_history[-1]["entry_hash"] if _latest_history else None

    return ValidateResponse(
        valid=result["valid"],
        record_id=body.record_id,
        errors=[FieldErrorResponse(**e) for e in _mask_errors(result["errors"])],
        warnings=[FieldErrorResponse(**w) for w in _mask_errors(result["warnings"])],
        contract=contract.name,
        version=contract.version,
        owner=contract.owner or "",
        engine_version=config.ENGINE_VERSION,
        contract_hash=_contract_hash,
        owner_team=contract.owner_team,
    )


@router.post("/validate/batch", response_model=BatchValidateResponse)
@_validate_limit
async def validate_batch_endpoint(
    request: Request,
    body: BatchValidateRequest,
    allow_draft: bool = Query(False, description="Allow validation against DRAFT contracts"),
    user=Depends(get_current_user),
):
    """
    Validate a batch of records against a data contract.

    Uses DuckDB for high-throughput batch processing.
    Reduces network overhead vs. calling /validate per record.
    """
    start = time.monotonic()

    # Guard against oversized batches — prevents single large requests from
    # exhausting worker memory. Limit is configurable via OPENDQV_MAX_BATCH_ROWS.
    if len(body.records) > config.MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch size {len(body.records)} exceeds the maximum of "
                f"{config.MAX_BATCH_ROWS} records. Split into smaller batches or "
                f"increase OPENDQV_MAX_BATCH_ROWS for this deployment."
            ),
        )

    contract = registry.get(body.contract, body.version)
    if not contract:
        raise HTTPException(
            status_code=404,
            detail=f"Contract '{body.contract}' version '{body.version}' not found",
        )

    if contract.status == ContractStatus.DRAFT and not allow_draft:
        if not config.STRICT_DRAFT_VALIDATION:
            logger.warning(
                "Serving batch validation against DRAFT contract '%s' — promote to active for production use.",
                contract.name,
            )
        # In strict mode with no snapshot, fall through — batch serves normally

    # Enforce validate_in_states — only allow validation against contracts in permitted states.
    # Skipped when allow_draft=true so that draft testing bypasses the state gate.
    if not allow_draft and hasattr(contract, 'validate_in_states') and contract.validate_in_states:
        if contract.status.value not in contract.validate_in_states:
            raise HTTPException(
                status_code=422,
                detail=f"Contract '{body.contract}' is in status '{contract.status.value}' which is not in validate_in_states {contract.validate_in_states}"
            )

    trace_id = str(uuid.uuid4())
    rules = registry.get_rules_with_context(contract, body.context)
    result = validate_batch(
        body.records,
        rules,
        contract_name=contract.name,
        context=body.context,
        sensitive_fields=getattr(contract, 'sensitive_fields', []),
    )

    elapsed_ms = (time.monotonic() - start) * 1000
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "validate_batch trace_id=%s caller=%s ip=%s contract=%s v%s context=%s "
        "total=%d passed=%d failed=%d %.1fms",
        trace_id, user, client_ip, contract.name, contract.version, body.context or "default",
        result["summary"]["total"], result["summary"]["passed"],
        result["summary"]["failed"], elapsed_ms,
    )
    # Record stats for each row in the batch
    for r in result["results"]:
        stats.record(
            contract=contract.name, context=body.context, valid=r["valid"],
            error_count=len(r["errors"]), warning_count=len(r["warnings"]),
            latency_ms=elapsed_ms / max(len(result["results"]), 1),
            errors=r["errors"], mode="batch",
        )
    heartbeat.record_validation(contract.name, contract.version)

    # Webhook notification for batch failures (fire-and-forget)
    if result["summary"]["failed"] > 0:
        failed_errors = []
        for r in result["results"]:
            if not r["valid"]:
                failed_errors.extend(r["errors"])
        await webhook_manager.notify("batch.failed", {
            "contract": contract.name,
            "contract_version": contract.version,
            "opendqv_node_id": config.OPENDQV_NODE_ID,
            "context": body.context,
            "total": result["summary"]["total"],
            "passed": result["summary"]["passed"],
            "failed": result["summary"]["failed"],
            "error_count": len(failed_errors),
            "violations": failed_errors[:50],  # cap to avoid huge payloads
        })

    # Record quality stats for trend endpoint (best-effort, non-blocking)
    try:
        _quality_stats.record_batch(
            contract_name=contract.name,
            contract_version=contract.version,
            context=body.context,
            total=result["summary"]["total"],
            passed=result["summary"]["passed"],
            failed=result["summary"]["failed"],
            rule_failure_counts=result["summary"].get("rule_failure_counts", {}),
        )
    except Exception:
        logger.exception("quality_stats.record_batch failed — non-blocking")

    # ACT-038-05: include contract hash (entry_hash from hash chain) for audit evidence
    _latest_history = registry.get_history(contract.name)
    _contract_hash = _latest_history[-1]["entry_hash"] if _latest_history else None

    return BatchValidateResponse(
        summary=BatchSummary(**result["summary"]),
        results=[
            BatchResultItem(
                index=r["index"],
                valid=r["valid"],
                errors=[FieldErrorResponse(**e) for e in _mask_errors(r["errors"])],
                warnings=[FieldErrorResponse(**w) for w in _mask_errors(r["warnings"])],
            )
            for r in result["results"]
        ],
        contract=contract.name,
        version=contract.version,
        owner=contract.owner or "",
        engine_version=config.ENGINE_VERSION,
        contract_hash=_contract_hash,
    )


@router.get("/trace/verify", tags=["Audit"])
async def verify_trace_log_endpoint(
    log_path: str = Query(None, description="Path to trace log file (default: opendqv_trace.jsonl)"),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Verify the tamper-evident hash chain of the TRACE_LOG file. Requires auditor or admin role."""
    if role not in ("auditor", "approver", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot access the audit trail. Required: auditor, approver, or admin.")
    from core.trace_log import verify_trace_log as _verify
    result = _verify(log_path)
    return result


@router.post("/validate/batch/file", tags=["Validation"])
async def validate_batch_file(
    contract: str = Query(..., description="Contract name"),
    version: str = Query("latest"),
    context: str = Query(None),
    file: UploadFile = File(...),
    user: str = Depends(get_current_user),
):
    """
    Validate records from an uploaded CSV or Parquet file.

    Returns a summary and per-row results. DuckDB-powered.
    Max file size: 100MB.
    """
    dc = registry.get(contract, version)
    if not dc:
        raise HTTPException(status_code=404, detail=f"Contract '{contract}' not found")

    rules = registry.get_rules_with_context(dc, context)

    content = await file.read()
    filename = file.filename or ""
    df = _parse_upload(content, filename)

    records = df.to_dict(orient="records")
    result = validate_batch(records, rules)

    return {
        "filename": filename,
        "rows": len(records),
        **result,
    }


# ── Contract endpoints ───────────────────────────────────────────────

@router.get("/contracts", response_model=list[ContractInfo])
@_default_limit
async def list_contracts(
    request: Request,
    include_all: bool = Query(False, description="Include ARCHIVED contracts"),
):
    """List available data contracts. No auth required — contracts are public metadata."""
    return [ContractInfo(**c) for c in registry.list_contracts(include_all=include_all)]


@router.get("/contracts/{name}", response_model=ContractDetail)
@_default_limit
async def get_contract(request: Request, name: str, version: str = Query("latest")):
    """Get full detail of a data contract including its rules."""
    contract = registry.get(name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' version '{version}' not found")

    def _rule_values(r) -> list[str] | None:
        """Return the first valid value from a local lookup file, for workbench sample generation."""
        if r.type != "lookup" or not r.lookup_file or r.lookup_file.startswith("http"):
            return None
        try:
            fp = registry.contracts_dir / r.lookup_file
            with open(fp) as _f:
                vals = [line.strip() for line in _f if line.strip()]
            return vals if vals else None
        except Exception:
            return None

    return ContractDetail(
        name=contract.name,
        version=contract.version,
        description=contract.description,
        owner=contract.owner,
        status=contract.status.value,
        rules=[
            RuleInfo(
                name=r.name,
                type=r.type,
                field=r.field,
                severity=r.severity.value,
                error_message=r.error_message,
                values=_rule_values(r),
            )
            for r in contract.rules
        ],
        contexts=list(contract.contexts.keys()),
        asset_id=contract.asset_id,
        owner_team=contract.owner_team,
        owner_email=contract.owner_email,
    )


@router.get("/contracts/{name}/explain", tags=["Contracts"])
async def explain_contract(
    name: str,
    version: str = Query("latest"),
    authorization: Optional[str] = Header(None),
):
    """
    Return a plain-English description of a contract's validation rules.

    Designed for compliance officers, auditors, and non-technical reviewers
    who need to understand what the contract validates without reading YAML.
    Sensitive fields are suppressed from this output.

    Authentication: required unless OPENDQV_EXPLAIN_PUBLIC=true.
    """
    # SEC-010: enforce auth unless EXPLAIN_PUBLIC is set
    if not EXPLAIN_PUBLIC:
        from fastapi.security.utils import get_authorization_scheme_param
        from security import auth as _auth_mod
        from jose import JWTError, jwt as _jose_jwt
        if not authorization:
            raise HTTPException(status_code=401, detail="No token provided. Set AUTH_MODE=open to disable auth.")
        if config.AUTH_MODE != "open":
            scheme, token_val = get_authorization_scheme_param(authorization)
            if scheme.lower() != "bearer" or not token_val:
                raise HTTPException(status_code=401, detail="Invalid authorization header format")
            try:
                payload = _jose_jwt.decode(token_val, _auth_mod.SECRET_KEY, algorithms=[_auth_mod.ALGORITHM])
                if not payload.get("sub"):
                    raise HTTPException(status_code=401, detail="Invalid token payload")
            except JWTError:
                raise HTTPException(status_code=401, detail="Invalid or expired token")

    contract = registry.get(name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    lines = [
        f"Contract: {contract.name} (version {contract.version})",
        f"Description: {contract.description or '(none)'}",
        f"Owner: {contract.owner or '(none)'}",
        f"Status: {contract.status.value}",
        f"Rules: {len(contract.rules)} validation rule(s)",
        "",
        "Validation Rules",
        "=" * 40,
    ]

    sensitive = set(contract.sensitive_fields) if hasattr(contract, 'sensitive_fields') else set()

    for rule in contract.rules:
        if rule.field in sensitive:
            lines.append(f"\n[{rule.name}] Field: {rule.field} — SUPPRESSED (sensitive field)")
            continue

        desc_parts = [f"\n[{rule.name}]"]
        desc_parts.append(f"  Field: {rule.field}")
        desc_parts.append(f"  Severity: {rule.severity.value}")

        if rule.description:
            desc_parts.append(f"  Description: {rule.description}")

        # Human-readable rule description
        if rule.type == "not_empty":
            desc_parts.append(f"  Rule: '{rule.field}' must not be empty or null.")
        elif rule.type == "regex":
            if rule.negate:
                desc_parts.append(f"  Rule: '{rule.field}' must NOT match pattern: {rule.pattern}")
            else:
                desc_parts.append(f"  Rule: '{rule.field}' must match pattern: {rule.pattern}")
        elif rule.type == "range":
            desc_parts.append(f"  Rule: '{rule.field}' must be between {rule.min_value} and {rule.max_value}.")
        elif rule.type == "min":
            desc_parts.append(f"  Rule: '{rule.field}' must be at least {rule.min_value}.")
        elif rule.type == "max":
            desc_parts.append(f"  Rule: '{rule.field}' must be no more than {rule.max_value}.")
        elif rule.type == "min_length":
            desc_parts.append(f"  Rule: '{rule.field}' must be at least {rule.min_length} characters long.")
        elif rule.type == "max_length":
            desc_parts.append(f"  Rule: '{rule.field}' must be no more than {rule.max_length} characters long.")
        elif rule.type == "date_format":
            desc_parts.append(f"  Rule: '{rule.field}' must be a valid date.")
        elif rule.type == "lookup":
            desc_parts.append(f"  Rule: '{rule.field}' must be one of the values in the approved reference list.")
        elif rule.type == "compare":
            if rule.compare_to in ("today", "now"):
                desc_parts.append(f"  Rule: '{rule.field}' must be {rule.compare_op} today's date.")
            else:
                desc_parts.append(f"  Rule: '{rule.field}' must be {rule.compare_op} '{rule.compare_to}'.")
        elif rule.type == "required_if":
            ri = rule.required_if or {}
            desc_parts.append(f"  Rule: '{rule.field}' is required when '{ri.get('field')}' equals '{ri.get('value')}'.")
        elif rule.type == "forbidden_if":
            fi = rule.forbidden_if or {}
            desc_parts.append(f"  Rule: '{rule.field}' must be absent when '{fi.get('field')}' equals '{fi.get('value')}'.")
        elif rule.type == "checksum":
            desc_parts.append(f"  Rule: '{rule.field}' must have a valid {rule.checksum_algorithm} check digit.")
        elif rule.type == "cross_field_range":
            parts = []
            if rule.cross_min_field:
                parts.append(f"at least the value of '{rule.cross_min_field}'")
            if rule.cross_max_field:
                parts.append(f"at most the value of '{rule.cross_max_field}'")
            desc_parts.append(f"  Rule: '{rule.field}' must be {' and '.join(parts)}.")
        elif rule.type == "field_sum":
            desc_parts.append(f"  Rule: The sum of fields {rule.sum_fields} must equal {rule.sum_equals} (tolerance: {rule.sum_tolerance or 0}).")
        elif rule.type == "unique":
            if rule.group_by:
                desc_parts.append(f"  Rule: '{rule.field}' must be unique within each group of '{rule.group_by}'.")
            else:
                desc_parts.append(f"  Rule: '{rule.field}' must be unique across all records.")
        elif rule.type == "conditional_value":
            cond = rule.condition or {}
            desc_parts.append(f"  Rule: '{rule.field}' must equal '{rule.must_equal}' when '{cond.get('field')}' equals '{cond.get('value')}'.")
        elif rule.type in ("min_age", "max_age"):
            pass  # handled by min_age/max_age fields below
        else:
            desc_parts.append(f"  Rule: {rule.type} constraint on '{rule.field}'.")

        if rule.min_age is not None:
            desc_parts.append(f"  Age constraint: '{rule.field}' must indicate an age of at least {rule.min_age} years.")
        if rule.max_age is not None:
            desc_parts.append(f"  Age constraint: '{rule.field}' must indicate an age of no more than {rule.max_age} years.")
        if rule.condition:
            cond = rule.condition
            if "value" in cond:
                desc_parts.append(f"  Applies only when: '{cond['field']}' equals '{cond['value']}'.")
            elif "not_value" in cond:
                desc_parts.append(f"  Applies only when: '{cond['field']}' does not equal '{cond['not_value']}'.")

        desc_parts.append(f"  If violated: {rule.error_message}")
        lines.extend(desc_parts)

    if sensitive:
        lines.append("")
        lines.append(f"Note: {len(sensitive)} sensitive field(s) are suppressed from this output.")

    return {"contract": name, "version": contract.version, "explanation": "\n".join(lines)}


@router.get(
    "/contracts/{name}/explain/{field}/{rule_name}",
    response_model=ExplainErrorResponse,
    tags=["Contracts"],
    summary="Explain a validation rule failure",
    description=(
        "Returns a plain-English explanation of why a field failed a rule, including valid and invalid examples. "
        "Designed for LLM agents that need to understand and remediate validation errors without reading contract YAML. "
        "Call this when /validate returns errors and the agent needs to self-correct."
    ),
)
async def explain_error(
    name: str,
    field: str,
    rule_name: str,
    version: str = Query("latest", description="Contract version or 'latest'"),
):
    contract = registry.get(name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    matching = [r for r in contract.rules if r.name == rule_name and r.field == field]
    if not matching:
        # Also try matching by rule name alone (field may differ by alias)
        matching = [r for r in contract.rules if r.name == rule_name]
    if not matching:
        raise HTTPException(
            status_code=404,
            detail=f"Rule '{rule_name}' not found on field '{field}' in contract '{name}'"
        )

    rule = matching[0]
    info = explain_rule(rule)

    return ExplainErrorResponse(
        contract=name,
        field=rule.field,
        rule=rule.name,
        rule_type=info["rule_type"],
        explanation=info["explanation"],
        valid_examples=info["valid_examples"],
        invalid_examples=info["invalid_examples"],
        constraint=info["constraint"],
    )


@router.get("/contracts/{name}/quality-trend", response_model=QualityTrendResponse)
@_default_limit
async def get_quality_trend(
    request: Request,
    name: str,
    days: int = Query(7, ge=1, le=90, description="Number of calendar days to look back"),
    context: str | None = Query(None, description="Filter by context"),
):
    """
    Quality trend for a contract over the last N days.

    Returns daily aggregated pass rates and top failing rules,
    derived from batch validation history.
    Single-record validations are not included — use validate/batch
    to populate trend data.
    """
    # Verify contract exists
    c = registry.get(name)
    if c is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    points = _quality_stats.get_trend(name, days=days, context=context)
    return QualityTrendResponse(
        contract=name,
        days=days,
        context=context,
        points=[QualityTrendPoint(**p) for p in points],
        asset_id=c.asset_id,
    )


@router.get("/contracts/{name}/at")
@_default_limit
async def get_contract_at_timestamp(
    request: Request,
    name: str,
    timestamp: str = Query(..., description="ISO 8601 timestamp — returns the contract state active at this moment"),
    user=Depends(get_current_user),
):
    """
    Point-in-time contract reconstruction.

    Returns the exact contract state (rules, version, status) that was active at
    the requested timestamp. Uses the contract_history audit log.

    This is the foundation for regulatory evidence queries:
    'What validation rules were in force on 14 November 2025?'
    """
    history = registry.get_history(name)
    if not history:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found in history")

    snapshot = None
    for snap in history:
        if snap["updated_at"] <= timestamp:
            snapshot = snap

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"No snapshot for contract '{name}' at or before '{timestamp}'. "
                   f"Earliest available: {history[0]['updated_at']}",
        )

    return {
        "contract": name,
        "queried_at": timestamp,
        "snapshot_at": snapshot["updated_at"],
        "version": snapshot["version"],
        "status": snapshot["status"],
        "description": snapshot["description"],
        "owner": snapshot["owner"],
        "opendqv_node_id": snapshot["opendqv_node_id"],
        "rules": snapshot["rules"],
        "contexts": snapshot["contexts"],
    }


@router.post("/contracts/reload", response_model=ContractReloadResponse)
@limiter.limit("5/minute")
async def reload_contracts(request: Request, user=Depends(get_current_user)):
    """Reload contracts from disk. Useful after editing YAML files."""
    registry.reload()
    return {"status": "reloaded", "contracts": registry.list_contracts(include_all=True)}


@router.post("/contracts/{name}/status")
@limiter.limit("10/minute")
async def change_contract_status(
    request: Request,
    response: Response,
    name: str,
    status: str = Query(..., description="New status: draft, active, or archived"),
    version: str = Query("latest"),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Change a contract's lifecycle status.

    Governance workflow: draft → active → archived

    Maker-checker: promoting a contract to 'active' requires the 'approver' or
    'admin' role. Writers may only set contracts to 'draft' or 'archived'.
    This enforces separation of duties — the person who writes a contract cannot
    also be the person who promotes it to production.
    """
    try:
        new_status = ContractStatus(status.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be: draft, active, archived",
        )

    # Look up the contract first so we return 404 before any role check.
    contract = registry.get(name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' version '{version}' not found")

    # Maker-checker gate: activating a contract requires approver or admin role.
    if new_status == ContractStatus.ACTIVE and role not in ("admin", "approver"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Promoting a contract to 'active' requires the 'approver' or 'admin' role. "
                f"Current role: '{role}'. Request approval from a contract approver."
            ),
        )

    # ACT-046-07: MCP-sourced contracts must go through the review workflow before activation.
    if new_status == ContractStatus.ACTIVE and getattr(contract, "source", None) == "mcp":
        if not contract.proposed_at:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Contract '{name}' was created by an agent (source: mcp) and must complete "
                    f"the review workflow before activation. "
                    f"Call POST /api/v1/contracts/{name}/{contract.version}/submit-review first."
                ),
            )

    # ACT-046-06: Field-completeness gate before activation.
    if new_status == ContractStatus.ACTIVE:
        issues = validate_promotion_readiness(contract)
        if issues:
            raise HTTPException(
                status_code=422,
                detail=f"Contract '{name}' is not ready for activation: " + "; ".join(issues),
            )

    contract = registry.set_status(name, version, new_status)

    logger.info(
        "contract_status_change: name=%s version=%s status=%s caller=%s role=%s",
        name, version, new_status.value, user, role,
    )
    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return {
        "name": contract.name,
        "version": contract.version,
        "status": contract.status.value,
        "message": f"Contract '{name}' is now {contract.status.value}",
        "approved_by": user,
    }


@router.post("/contracts/{name}/{version}/submit-review", tags=["Contracts"])
async def submit_for_review(
    name: str,
    version: str,
    body: dict = Body(...),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Submit a DRAFT contract for review (DRAFT → REVIEW)."""
    if role not in ("editor", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted for this action. Required: editor or admin.",
        )
    proposed_by = body.get("proposed_by") or user
    try:
        contract = registry.submit_for_review(name, version, proposed_by)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    return {"status": "submitted", "contract": name, "version": version, "proposed_by": proposed_by}


@router.post("/contracts/{name}/{version}/approve", tags=["Contracts"])
async def approve_contract(
    name: str,
    version: str,
    body: dict = Body(...),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Approve a REVIEW contract (REVIEW → ACTIVE). Requires approver role."""
    if role not in ("approver", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted for this action. Required: ['approver', 'admin']",
        )
    approved_by = body.get("approved_by") or user
    try:
        contract = registry.approve_contract(name, version, approved_by)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    return {"status": "approved", "contract": name, "version": version, "approved_by": approved_by}


@router.post("/contracts/{name}/{version}/reject", tags=["Contracts"])
async def reject_contract(
    name: str,
    version: str,
    body: dict = Body(...),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Reject a REVIEW contract (REVIEW → DRAFT). Requires approver role."""
    if role not in ("approver", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted for this action. Required: ['approver', 'admin']",
        )
    rejected_by = body.get("rejected_by") or user
    reason = body.get("reason", "")
    try:
        contract = registry.reject_contract(name, version, rejected_by, reason)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    return {"status": "rejected", "contract": name, "version": version, "rejected_by": rejected_by, "reason": reason}


# ── Contract versioning / history ─────────────────────────────────────

@router.get("/contracts/{name}/history", response_model=ContractHistoryResponse)
@_default_limit
async def get_contract_history(
    request: Request,
    name: str,
    user=Depends(get_current_user),
):
    """Get version history for a contract."""
    history = registry.get_history(name)
    if not history and not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return {"contract": name, "history": history}


@router.get("/contracts/{name}/diff", response_model=ContractDiffResponse)
@_default_limit
async def diff_contract_versions(
    request: Request,
    name: str,
    version_a: str = Query(..., description="First version to compare"),
    version_b: str = Query(..., description="Second version to compare"),
    user=Depends(get_current_user),
):
    """Compare two versions of a contract."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    try:
        diff = registry.diff_versions(name, version_a, version_b)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"One or both versions not found for contract '{name}'")
    return diff


@router.post("/contracts/{name}/version")
@limiter.limit("10/minute")
async def bump_contract_version(
    request: Request,
    response: Response,
    name: str,
    new_version: str = Query(..., description="New version string (e.g. '2.0')"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Create a new version of a contract.

    Snapshots the current version in history, bumps the version string to the
    requested value, and sets the new version to DRAFT status — enforcing the
    maker-checker requirement that a separate approver must promote it to ACTIVE.

    Requires 'approver' or 'admin' role. Version creation is a controlled change
    under BCBS 239 principle 10 (adaptability): every rule-set change must be
    traceable to an authorised individual. Writers must request a version bump
    from an approver, who then reviews and activates it.
    """
    # Maker-checker: version creation modifies the contract rule-set and must be
    # traceable to an authorised individual. Writers cannot create new versions.
    if role not in ("admin", "approver"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Creating a new contract version requires the 'approver' or 'admin' role. "
                f"Current role: '{role}'. Request a version bump from a contract approver."
            ),
        )

    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    old_version = contract.version

    if old_version == new_version:
        raise HTTPException(status_code=400, detail=f"New version must differ from current version '{old_version}'.")

    # Ensure current version is recorded in history
    registry.history.record_version(contract)

    # Update version string and reset to DRAFT — new versions always start in DRAFT.
    # The approver must explicitly promote to ACTIVE via POST /contracts/{name}/status.
    # This enforces the maker-checker separation on the new version.
    contract.version = new_version
    contract.status = ContractStatus.DRAFT

    # Re-index in the registry under the new version key
    if name in registry._contracts:
        registry._contracts[name][new_version] = contract

    # Record the new version snapshot
    registry.history.record_version(contract)

    logger.info(
        "version_bump caller=%s contract=%s old_version=%s new_version=%s status=draft",
        user, name, old_version, new_version,
    )

    # Compute diff
    try:
        diff = registry.diff_versions(name, old_version, new_version)
    except ValueError:
        diff = None

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return {
        "name": name,
        "old_version": old_version,
        "new_version": new_version,
        "status": "draft",
        "message": (
            f"Contract '{name}' bumped from v{old_version} to v{new_version}. "
            f"New version is in DRAFT status — an approver must activate it before it can be used."
        ),
        "diff": diff,
    }


@router.post("/contracts/{name}/rules", tags=["Contracts"])
@_default_limit
async def add_rule(
    request: Request,
    response: Response,
    name: str,
    body: dict = Body(...),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Add a new rule to a contract.
    Body: rule object (name, field, type, and type-specific fields).
    Writes atomically to YAML and records history.
    ACT-036-01 / ACT-046-05
    """
    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    # ACT-047-01: ACTIVE contracts are immutable — rule mutations are blocked for all callers.
    # To modify rules, fork via POST /contracts/{name}/version (creates a new DRAFT).
    if contract.status == ContractStatus.ACTIVE:
        logger.warning(
            "rule_mutation_blocked contract=%s op=add_rule caller=%s status=active",
            name, user,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{name}' is ACTIVE. Rule mutations are not permitted on active contracts. "
                f"To modify rules, use POST /api/v1/contracts/{name}/version to create a new draft version."
            ),
        )

    # ACT-036-04: CONTRACT_EDIT_MODE hook — in "auto" mode, rule edits take effect immediately.
    # "maker_checker" mode reserved for enterprise tier.
    if config.CONTRACT_EDIT_MODE != "auto":
        logger.info("contract_edit_mode=%s contract=%s op=add_rule", config.CONTRACT_EDIT_MODE, name)
    try:
        contract = registry.add_rule(name, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return {"status": "added", "contract": name, "rule": body.get("name"), "rule_count": len(contract.rules), "version": contract.version}


@router.put("/contracts/{name}/rules/{rule_name}", tags=["Contracts"])
@_default_limit
async def update_rule(
    request: Request,
    response: Response,
    name: str,
    rule_name: str,
    body: dict = Body(...),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Replace an existing rule in a contract.
    Body: complete rule object (replaces current definition).
    Returns a breaking_change_warning if type/pattern/min/max changed.
    ACT-036-01 / ACT-036-06 / ACT-046-05
    """
    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    # ACT-047-01: ACTIVE contracts are immutable — rule mutations are blocked for all callers.
    # To modify rules, fork via POST /contracts/{name}/version (creates a new DRAFT).
    if contract.status == ContractStatus.ACTIVE:
        logger.warning(
            "rule_mutation_blocked contract=%s op=update_rule caller=%s status=active",
            name, user,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{name}' is ACTIVE. Rule mutations are not permitted on active contracts. "
                f"To modify rules, use POST /api/v1/contracts/{name}/version to create a new draft version."
            ),
        )

    # ACT-036-04: CONTRACT_EDIT_MODE hook — in "auto" mode, rule edits take effect immediately.
    # "maker_checker" mode reserved for enterprise tier.
    if config.CONTRACT_EDIT_MODE != "auto":
        logger.info("contract_edit_mode=%s contract=%s op=update_rule", config.CONTRACT_EDIT_MODE, name)
    try:
        contract, breaking = registry.update_rule(name, rule_name, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    resp: dict = {"status": "updated", "contract": name, "rule": rule_name, "version": contract.version}
    if breaking:
        resp["breaking_change_warning"] = (
            "Modifying an existing rule may cause previously passing validations to fail. "
            "Consider bumping the contract version when promoting this draft to ACTIVE."
        )
    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return resp


@router.delete("/contracts/{name}/rules/{rule_name}", tags=["Contracts"])
@_default_limit
async def delete_rule(
    request: Request,
    response: Response,
    name: str,
    rule_name: str,
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Delete a rule from a contract.
    ACT-036-01 / ACT-046-05
    """
    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    # ACT-047-01: ACTIVE contracts are immutable — rule mutations are blocked for all callers.
    # To modify rules, fork via POST /contracts/{name}/version (creates a new DRAFT).
    if contract.status == ContractStatus.ACTIVE:
        logger.warning(
            "rule_mutation_blocked contract=%s op=delete_rule caller=%s status=active",
            name, user,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{name}' is ACTIVE. Rule mutations are not permitted on active contracts. "
                f"To modify rules, use POST /api/v1/contracts/{name}/version to create a new draft version."
            ),
        )

    # ACT-036-04: CONTRACT_EDIT_MODE hook — in "auto" mode, rule edits take effect immediately.
    # "maker_checker" mode reserved for enterprise tier.
    if config.CONTRACT_EDIT_MODE != "auto":
        logger.info("contract_edit_mode=%s contract=%s op=delete_rule", config.CONTRACT_EDIT_MODE, name)
    try:
        contract = registry.delete_rule(name, rule_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return {"status": "deleted", "contract": name, "rule": rule_name, "rule_count": len(contract.rules), "version": contract.version}


@router.get("/registry", tags=["Schema Registry"])
@_default_limit
async def list_schema_registry(
    request: Request,
    user=Depends(get_current_user),
):
    """
    Schema registry catalog — list all contracts with version, owner, and schema hash.

    Returns a machine-readable registry of all active data contracts. Suitable for
    integration with data catalog tools (DataHub, Atlan, Collibra) and CI pipelines
    that need to discover available contracts.
    ACT-038-02
    """
    contracts = registry.list_contracts(include_all=False)
    result = []
    for c in contracts:
        contract = registry.get(c["name"])
        history = registry.get_history(c["name"])
        latest_hash = history[-1]["entry_hash"] if history else None
        result.append({
            "name": c["name"],
            "version": c["version"],
            "status": c["status"],
            "owner": c.get("owner", ""),
            "owner_team": getattr(contract, "owner_team", None),
            "owner_email": getattr(contract, "owner_email", None),
            "description": c.get("description", ""),
            "rule_count": c["rule_count"],
            "schema_hash": latest_hash,
            "asset_id": c.get("asset_id"),
        })
    return {"registry": result, "count": len(result), "opendqv_node_id": config.OPENDQV_NODE_ID}


@router.get("/registry/{name}", tags=["Schema Registry"])
@_default_limit
async def get_schema_registry_entry(
    request: Request,
    name: str,
    user=Depends(get_current_user),
):
    """
    Schema registry entry — get a single contract in schema registry format.

    Returns the contract with its full rule definitions in a format suitable for
    downstream consumers that need to discover and validate data against this contract.
    Includes the schema_hash (from the audit hash chain) for integrity verification.
    ACT-038-02
    """
    contract = registry.get(name)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found in registry")
    history = registry.get_history(name)
    latest_hash = history[-1]["entry_hash"] if history else None
    rules_out = [
        {
            "name": r.name,
            "field": r.field,
            "type": r.type,
            "severity": r.severity.value if hasattr(r.severity, "value") else r.severity,
            "error_message": r.error_message or "",
        }
        for r in contract.rules
    ]
    return {
        "name": contract.name,
        "version": contract.version,
        "status": contract.status.value,
        "owner": contract.owner or "",
        "owner_team": getattr(contract, "owner_team", None),
        "owner_email": getattr(contract, "owner_email", None),
        "description": contract.description or "",
        "schema_hash": latest_hash,
        "asset_id": contract.asset_id,
        "rules": rules_out,
        "opendqv_node_id": config.OPENDQV_NODE_ID,
    }


# ── Code generation (nice-to-have) ───────────────────────────────────

@router.post("/generate")
@_default_limit
async def generate_code_endpoint(
    request: Request,
    contract_name: str = Query(..., description="Contract to generate code for"),
    target: str = Query(..., description="Target platform: snowflake, salesforce, js"),
    version: str = Query("latest"),
    context: str = Query(None, description="Optional context to apply (e.g. 'salesforce', 'kids_app')"),
    user=Depends(get_current_user),
):
    """Generate validation code for a target platform from a contract's rules."""
    contract = registry.get(contract_name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_name}' not found")

    rules = registry.get_rules_with_context(contract, context)
    code = generate_code(rules, target)
    return {"contract": contract.name, "target": target, "context": context, "code": code}


# ── GX Import ─────────────────────────────────────────────────────────

@router.post("/import/gx")
@_default_limit
async def import_great_expectations(
    request: Request,
    response: Response,
    suite: dict = Body(..., description="Great Expectations expectation suite JSON"),
    save: bool = Query(False, description="Save as YAML contract to disk and reload registry"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import a Great Expectations expectation suite and convert to an OpenDQV contract.

    Accepts both GX 0.x and 1.x suite formats. Maps supported expectation types
    to OpenDQV rules. Unsupported expectations are skipped and reported.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    import yaml as _yaml
    result = import_gx_suite(suite)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        import os
        contract_name = result["contract"]["name"]
        _validate_contract_name(contract_name)
        yaml_content = gx_suite_to_yaml(suite)
        _d = _yaml.safe_load(yaml_content)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_content = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w") as f:
            f.write(yaml_content)
        registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return result


@router.post("/import/dbt")
@_default_limit
async def import_dbt(
    request: Request,
    response: Response,
    schema: dict = Body(..., description="dbt schema.yml content as JSON"),
    save: bool = Query(False, description="Save contracts to disk and reload"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import a dbt schema.yml and convert to OpenDQV contracts.

    Parses both models and sources sections. Each model/source becomes a separate contract.
    """
    import yaml as _yaml
    result = import_dbt_schema(schema)
    for item in result["contracts"]:
        item["contract"]["source"] = "import"
        item["contract"]["status"] = "draft"

    if save:
        import os
        saved_files = []
        contracts_dir = str(config.CONTRACTS_DIR)
        for item in result["contracts"]:
            contract_name = item["contract"]["name"]
            _validate_contract_name(contract_name)
            pairs = dbt_schema_to_yaml(schema)
            for name, yaml_content in pairs:
                if name == contract_name:
                    _d = _yaml.safe_load(yaml_content)
                    _d["source"] = "import"
                    _d["status"] = "draft"
                    if created_by:
                        _d["created_by"] = created_by
                    yaml_content = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
                    file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
                    with open(file_path, "w") as f:
                        f.write(yaml_content)
                    saved_files.append(file_path)
                    break
        registry.reload()
        result["saved_to"] = saved_files
        result["message"] = f"Saved {len(saved_files)} contract(s)"

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return result


@router.post("/import/soda")
@_default_limit
async def import_soda(
    request: Request,
    response: Response,
    checks: dict = Body(..., description="Soda checks YAML content as JSON dict"),
    save: bool = Query(False, description="Save contracts to disk and reload"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import a Soda Core checks YAML and convert to OpenDQV contracts.

    Parses ``checks for <dataset>:`` blocks. Each dataset becomes a separate contract.
    Supports missing_count, duplicate_count, invalid_count, min, max, min_length, max_length.
    """
    import yaml as _yaml
    result = import_soda_checks(checks)
    for item in result.get("contracts", []):
        item["contract"]["source"] = "import"
        item["contract"]["status"] = "draft"

    if save:
        import os
        saved_files = []
        contracts_dir = str(config.CONTRACTS_DIR)
        pairs = soda_checks_to_yaml(checks)
        for name, yaml_content in pairs:
            _validate_contract_name(name)
            _d = _yaml.safe_load(yaml_content)
            _d["source"] = "import"
            _d["status"] = "draft"
            if created_by:
                _d["created_by"] = created_by
            yaml_content = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
            file_path = os.path.join(contracts_dir, f"{name}.yaml")
            with open(file_path, "w") as f:
                f.write(yaml_content)
            saved_files.append(file_path)
        registry.reload()
        result["saved_to"] = saved_files
        result["message"] = f"Saved {len(saved_files)} contract(s)"

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return result


@router.post("/import/csv")
@_default_limit
async def import_csv(
    request: Request,
    response: Response,
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("csv_import", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import a CSV rule definition and convert to an OpenDQV contract.

    Accepts CSV as plain text body. Expected columns: field, rule_type, value, severity, error_message.
    """
    import yaml as _yaml
    body_bytes = await request.body()
    csv_content = body_bytes.decode("utf-8")

    _validate_contract_name(contract_name)
    result = import_csv_rules(csv_content, contract_name)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        import os
        yaml_content = csv_rules_to_yaml(csv_content, contract_name)
        _d = _yaml.safe_load(yaml_content)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_content = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w") as f:
            f.write(yaml_content)
        registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return result


@router.post("/import/odcs")
@_default_limit
async def import_odcs_contract(
    request: Request,
    response: Response,
    contract_data: dict = Body(..., description="ODCS 3.1 contract as JSON dict"),
    save: bool = Query(False, description="Save as YAML contract to disk and reload registry"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import an Open Data Contract Standard (ODCS) 3.1 contract and convert to OpenDQV.

    Accepts ODCS 3.1 dict with apiVersion, kind, info, and schema sections.
    Maps quality checks (not_null, unique, regex, range, min, max, min_length,
    max_length, date_format) and field-level shortcuts (required, unique,
    minLength, maxLength) to OpenDQV rules.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    import yaml as _yaml
    result = import_odcs(contract_data)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        import os
        contract_name, yaml_content = odcs_to_yaml(contract_data)
        _validate_contract_name(contract_name)
        _d = _yaml.safe_load(yaml_content)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_content = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w") as f:
            f.write(yaml_content)
        registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    response.headers["X-Auth-Mode"] = config.AUTH_MODE
    return result


@router.post("/import/csvw")
@_default_limit
async def import_from_csvw(
    request: Request,
    response: Response,
    body: dict = Body(..., description="CSVW JSON-LD metadata document"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("csvw_import", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import contract rules from CSVW (CSV on the Web) W3C metadata.

    Accepts a CSVW JSON-LD metadata document and maps column definitions to
    OpenDQV validation rules. Supports required, pattern, range, length, and
    enum constraints.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    _validate_contract_name(contract_name)
    import yaml as _yaml
    try:
        result = import_csvw(body)
        yaml_output = csvw_to_yaml(body, contract_name)
        _d = _yaml.safe_load(yaml_output)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_output = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            import os
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w") as f:
                f.write(yaml_output)
            registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        response.headers["X-Auth-Mode"] = config.AUTH_MODE
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSVW import failed: {e}")


@router.post("/import/otel")
@_default_limit
async def import_from_otel(
    request: Request,
    response: Response,
    body: dict = Body(..., description="OTel semantic convention schema as JSON dict"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("otel_telemetry", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Import contract rules from OpenTelemetry semantic convention schema.

    Accepts an OTel attribute group definition and maps requirement levels,
    known enum attributes, and numeric ranges to OpenDQV validation rules.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    _validate_contract_name(contract_name)
    import yaml as _yaml
    try:
        result = import_otel(body)
        yaml_output = otel_to_yaml(body, contract_name)
        _d = _yaml.safe_load(yaml_output)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_output = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            import os
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w") as f:
                f.write(yaml_output)
            registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        response.headers["X-Auth-Mode"] = config.AUTH_MODE
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OTel import failed: {e}")


@router.post("/import/ndc")
@_default_limit
async def import_from_ndc(
    request: Request,
    response: Response,
    body: dict = Body(default={}, description="NDC importer configuration"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("pharma_dispense", description="Name for the generated contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
):
    """
    Generate NDC (National Drug Code) validation rules.

    Accepts an optional configuration dict specifying which field names to
    validate as NDC codes, desired severity, and format flags. Returns
    OpenDQV rules covering presence and format validation per FDA standard.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    _validate_contract_name(contract_name)
    import yaml as _yaml
    try:
        result = import_ndc(body)
        yaml_output = ndc_to_yaml(body, contract_name)
        _d = _yaml.safe_load(yaml_output)
        _d["source"] = "import"
        _d["status"] = "draft"
        if created_by:
            _d["created_by"] = created_by
        yaml_output = _yaml.dump(_d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            import os
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w") as f:
                f.write(yaml_output)
            registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        response.headers["X-Auth-Mode"] = config.AUTH_MODE
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"NDC import failed: {e}")


@router.get("/export/gx/{contract_name}")
@_default_limit
async def export_to_great_expectations(
    request: Request,
    contract_name: str,
    version: str = Query("latest"),
    context: str = Query(None, description="Optional context to apply before export"),
    user=Depends(get_current_user),
):
    """
    Export an OpenDQV contract as a Great Expectations expectation suite JSON.

    This enables bidirectional sync: import GX suites into OpenDQV for governance,
    then export back to keep GX pipelines aligned with the governed rules.
    """
    contract = registry.get(contract_name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_name}' not found")

    rules = registry.get_rules_with_context(contract, context)
    suite = export_gx_suite(contract.name, rules)
    suite["meta"]["contract_version"] = contract.version
    suite["meta"]["context"] = context
    return suite


@router.get("/export/odcs/{contract_name}")
@_default_limit
async def export_to_odcs(
    request: Request,
    contract_name: str,
    version: str = Query("latest"),
    context: str = Query(None, description="Optional context to apply before export"),
    user=Depends(get_current_user),
):
    """
    Export an OpenDQV contract as an ODCS 3.1 data contract YAML.

    Returns ODCS 3.1 YAML (apiVersion: v3.1.0, kind: DataContract) with quality
    checks mapped from OpenDQV rules. Suitable for use with OpenMetadata, Soda,
    Monte Carlo, and the Data Contract CLI.
    """
    contract = registry.get(contract_name, version)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_name}' not found")

    rules = registry.get_rules_with_context(contract, context)
    yaml_str = contract_to_odcs_yaml(
        contract_name=contract.name,
        rules=rules,
        version=contract.version,
        status=contract.status.value if hasattr(contract.status, "value") else str(contract.status),
        description=getattr(contract, "description", "") or "",
        owner=getattr(contract, "owner", "") or "",
    )
    from fastapi.responses import Response
    return Response(content=yaml_str, media_type="application/yaml")


# ── Profiler ──────────────────────────────────────────────────────────

@router.post("/profile")
@_default_limit
async def profile_data(
    request: Request,
    records: list[dict] = Body(...),
    contract_name: str = Query("profiled", description="Name for the generated contract"),
    save: bool = Query(False, description="Save as YAML contract"),
    user=Depends(get_current_user),
):
    """Analyze records and auto-generate an OpenDQV contract with suggested rules."""
    _validate_contract_name(contract_name)
    result = profile_records(records, contract_name=contract_name)

    if save:
        import os
        import yaml
        contract_data = {"contract": result["contract"]}
        yaml_content = yaml.dump(contract_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w") as f:
            f.write(yaml_content)
        registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    return result


@router.post("/profile/file", tags=["Profiler"])
@_default_limit
async def profile_file(
    request: Request,
    file: UploadFile = File(...),
    contract_name: str = Query("profiled", description="Name for the generated contract"),
    save: bool = Query(False, description="Save as YAML contract"),
    user=Depends(get_current_user),
):
    """
    Profile records from an uploaded CSV or Parquet file.

    Returns a field-level statistical profile and suggested contract rules.
    DuckDB-powered: includes mean, stddev, and percentiles for numeric fields.
    Max file size: configured via OPENDQV_MAX_UPLOAD_MB (default 10MB).
    """
    _validate_contract_name(contract_name)
    content = await file.read()
    filename = file.filename or ""
    df = _parse_upload(content, filename)

    records = df.to_dict(orient="records")
    result = profile_records(records, contract_name=contract_name)

    if save:
        import os
        import yaml
        contract_data = {"contract": result["contract"]}
        yaml_content = yaml.dump(contract_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w") as f:
            f.write(yaml_content)
        registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    result["filename"] = filename
    result["rows"] = len(records)
    return result


# ── Token management ─────────────────────────────────────────────────

@router.post("/tokens/generate")
@_tokens_limit
async def generate_token(
    request: Request,
    username: str = Query(..., description="Source system name (e.g. 'salesforce-prod', 'sap-hr')"),
    expiry_days: int = Query(None, description="Token lifetime in days (default: TOKEN_EXPIRY_DAYS from config)"),
    role: str = Query("validator", description="Token role: validator, reader, auditor, editor, approver, admin (default: validator)"),
    _current_user: str = Depends(get_current_user),
):
    """
    Generate a PAT for a source system.

    Each source system should have its own token for audit trail and revocation.
    The token is included in the response — store it securely, it won't be shown again.

    In AUTH_MODE=open, elevated roles (admin, approver, editor) are capped to 'validator'.
    Elevated tokens can only be issued in AUTH_MODE=token to prevent privilege escalation
    tokens from being persisted in development environments.
    """
    # Prevent privilege escalation in open mode: any token persisted with an
    # elevated role would remain valid after AUTH_MODE is later tightened to
    # "token". Cap to "validator" in open mode regardless of what was requested.
    effective_role = role
    if config.AUTH_MODE == "open" and role in ("admin", "approver", "editor"):
        effective_role = "validator"

    result = create_pat(username, expiry_days=expiry_days, role=effective_role)
    return {
        "pat": result["token"],
        "username": result["username"],
        "expires_at": result["expires_at"],
        "expiry_days": result["expiry_days"],
        "role": result["role"],
    }


@router.get("/tokens")
@_tokens_limit
async def list_all_tokens(request: Request, user=Depends(get_current_user)):
    """List all registered tokens with expiry info. Tokens values are not shown."""
    return list_tokens()


@router.post("/tokens/revoke")
@_tokens_limit
async def revoke_token(request: Request, token: str = Body(..., media_type="text/plain"), user=Depends(get_current_user)):
    """Revoke a specific PAT by token value. Requires authentication."""
    return revoke_pat(token)


@router.post("/tokens/revoke/{username}")
@_tokens_limit
async def revoke_system_tokens(request: Request, username: str, user=Depends(get_current_user), role: str = Depends(get_current_role)):
    """Revoke all tokens for a source system. Requires admin role."""
    if role != "admin":
        raise HTTPException(status_code=403, detail="Revoking all tokens for a system requires the 'admin' role.")
    return revoke_by_username(username)


# ── Webhooks ─────────────────────────────────────────────────────────

@router.post("/webhooks")
@_default_limit
async def register_webhook(
    request: Request,
    body: dict = Body(..., description="Webhook registration: {url, events?, contracts?}"),
    user=Depends(get_current_user),
):
    """
    Register a webhook to receive notifications on validation events.

    Body:
      - url (required): The URL to POST notifications to.
      - events (optional): List of event types to subscribe to.
        Valid: "validation.failed", "validation.warning", "batch.failed".
        Defaults to all events.
      - contracts (optional): List of contract names to filter on. Defaults to all.

    Webhooks are persisted in SQLite and survive server restarts.
    """
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required.")
    events = body.get("events")
    contracts = body.get("contracts")
    try:
        hook = webhook_manager.register(url=url, events=events, contracts=contracts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "registered", "webhook": hook}


@router.get("/webhooks")
@_default_limit
async def list_webhooks(
    request: Request,
    user=Depends(get_current_user),
):
    """List all registered webhooks."""
    return webhook_manager.list_hooks()


@router.delete("/webhooks")
@_default_limit
async def unregister_webhook(
    request: Request,
    body: dict = Body(..., description="Webhook to remove: {url}"),
    user=Depends(get_current_user),
):
    """Unregister a webhook by URL."""
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="'url' is required.")
    removed = webhook_manager.unregister(url)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No webhook registered for '{url}'.")
    return {"status": "unregistered", "url": url}


# ── Monitoring ────────────────────────────────────────────────────────

@router.get("/stats")
@_default_limit
async def get_stats(request: Request, user=Depends(get_current_user)):
    """Get validation statistics for the monitoring dashboard."""
    return stats.get_summary()


# ── Federation API (OSS skeleton) ─────────────────────────────────────
#
# These endpoints define the federation wire protocol. In standalone mode
# (IS_FEDERATED=False) they return local state with no upstream calls.
# The commercial two-phase commit implementation extends these without
# changing their shapes — callers can integrate against this API today.

@router.get("/federation/status")
@_default_limit
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
        "opendqv_node_state": _node_health.current_state().value,
        "audit_mode": config.AUDIT_MODE,
        "contracts_loaded": len(registry.list_contracts()),
        "time_in_state_seconds": _node_health.time_in_current_state(),
        "isolated_since": _node_health.isolated_since(),
    }


@router.get("/federation/log")
@_default_limit
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
    events = _federation_log.get_since(since, contract_name=contract)
    return {
        "opendqv_node_id": config.OPENDQV_NODE_ID,
        "since": since,
        "count": len(events),
        "events": events,
    }


@router.get("/federation/health")
@_default_limit
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
        "opendqv_node_state": _node_health.current_state().value,
        "time_in_state_seconds": _node_health.time_in_current_state(),
        "isolated_since": _node_health.isolated_since(),
        "health_log": _node_health.get_log(limit=log_limit),
        "open_isolation_events": _isolation_log.get_open_events(),
        "recent_isolation_events": _isolation_log.get_events(limit=log_limit),
    }


@router.post("/federation/register")
@limiter.limit("5/minute")
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


@router.get("/federation/sync-status")
@_default_limit
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
    local_contracts = registry.list_contracts()
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
                    await webhook_manager.notify("sync_diverged", {
                        "opendqv_node_id": config.OPENDQV_NODE_ID,
                        "peer": peer,
                        "diverged_contracts": [d["name"] for d in diverged],
                        "count": len(diverged),
                    })
        except Exception as exc:
            result["peer_error"] = str(exc)

    return result


@router.get("/federation/events")
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

    # Enforce per-worker SSE connection cap to prevent long-lived SSE clients
    # from starving the validation worker pool.
    global _sse_active
    with _sse_lock:
        if _sse_active >= config.MAX_SSE_CONNECTIONS:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"SSE connection limit reached ({config.MAX_SSE_CONNECTIONS} "
                    f"per worker). Retry after an existing client disconnects."
                ),
            )
        _sse_active += 1

    async def _event_stream():
        # Initialise cursors at the current high-water mark so only NEW events
        # are pushed — callers use /federation/log?since=N to replay history.
        existing = _federation_log.get_since(0)
        current_lsn = existing[-1]["lsn"] if existing else 0
        last_node_state = _node_health.current_state().value
        last_heartbeat_ts = time.monotonic()
        emitted = 0

        # Connected event — first yield so callers know the stream is live
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

            # ── New federation log events ────────────────────────────
            new_events = _federation_log.get_since(current_lsn)
            for event in new_events:
                current_lsn = event["lsn"]
                yield f"event: federation_log\ndata: {json.dumps(event)}\n\n"
                emitted += 1
                if limit and emitted >= limit:
                    return

            # ── Node state change ────────────────────────────────────
            new_state = _node_health.current_state().value
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

            # ── Heartbeat ping ───────────────────────────────────────
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
        """Wrap _event_stream to decrement the SSE counter on disconnect/finish."""
        global _sse_active
        try:
            async for chunk in _event_stream():
                yield chunk
        finally:
            with _sse_lock:
                _sse_active = max(0, _sse_active - 1)

    return StreamingResponse(
        _tracked_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering for SSE
        },
    )
