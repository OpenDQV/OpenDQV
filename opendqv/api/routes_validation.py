import time
import logging

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.core._uuid7 import uuid7
from opendqv.core.contracts import UnknownContextError, _compute_effective_rule_hash
from opendqv.core.rule_parser import ContractStatus, Rule
from opendqv.core.validator import validate_record, validate_batch
from opendqv.security.auth import get_current_user, get_current_role
from opendqv.monitoring import stats

from .models import (
    ValidateRequest, ValidateResponse, FieldErrorResponse,
    BatchValidateRequest, BatchValidateResponse, BatchResultItem, BatchSummary,
)

logger = logging.getLogger(__name__)

sub_router = APIRouter()


@sub_router.post("/validate", response_model=ValidateResponse)
@_d._validate_limit
async def validate_single(
    request: Request,
    body: ValidateRequest,
    background_tasks: BackgroundTasks,
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
    event_id = str(uuid7())
    client_ip = request.client.host if request.client else "unknown"

    if body.hash:
        contract = _d.registry.contract_by_hash(body.contract, body.hash)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{body.contract}' has no history entry matching hash '{body.hash}'.",
            )
    elif as_of:
        contract = _d.registry.contract_as_of(body.contract, as_of)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{body.contract}' not found in history at or before '{as_of}'.",
            )
    else:
        contract = _d._get_contract_versioned_or_404(body.contract, body.version)

    if not as_of:
        if contract.status == ContractStatus.DRAFT and not allow_draft:
            if config.STRICT_DRAFT_VALIDATION:
                snapshot = getattr(contract, 'last_active_snapshot', None)
                if snapshot:
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
                    resp_data = ValidateResponse(
                        valid=snap_result["valid"],
                        event_id=event_id,
                        record_id=body.record_id,
                        errors=[FieldErrorResponse(**e) for e in _d._mask_errors(snap_result["errors"])],
                        warnings=[FieldErrorResponse(**w) for w in _d._mask_errors(snap_result["warnings"])],
                        contract=contract.name,
                        version=contract.version,
                        owner=contract.owner or "",
                        engine_version=config.ENGINE_VERSION,
                    )
                    return JSONResponse(
                        content=resp_data.model_dump(),
                        headers={"X-Contract-Status": "draft-fallback"},
                    )
            logger.warning(
                "Serving validation against DRAFT contract '%s' — promote to active for production use.",
                contract.name,
            )

        _d._check_validate_in_states(contract, body.contract, allow_draft)

    try:
        rules, _ctx_status = _d.registry.get_rules_with_context_status(contract, body.context)
    except UnknownContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _context_warning = (
        f"Context '{body.context}' is not declared on contract '{contract.name}'. "
        f"Validation proceeded with base rules (no context overrides applied). "
        f"If you intended a metadata tag (e.g. 'demo', 'ci', 'test') this is fine; "
        f"if you intended an override context, declare it on the contract."
        if _ctx_status == "undeclared" else None
    )
    result = validate_record(
        body.record,
        rules,
        contract_name=contract.name,
        context=body.context,
        sensitive_fields=getattr(contract, 'sensitive_fields', []),
    )

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "validate event_id=%s caller=%s ip=%s record_id=%s contract=%s v%s context=%s "
        "valid=%s errors=%d warnings=%d %.1fms",
        event_id, user, client_ip, body.record_id or "-",
        contract.name, contract.version, body.context or "default",
        result["valid"], len(result["errors"]), len(result["warnings"]), elapsed_ms,
    )
    if not body.dry_run:
        stats.record(
            contract=contract.name, context=body.context, valid=result["valid"],
            error_count=len(result["errors"]), warning_count=len(result["warnings"]),
            latency_ms=elapsed_ms, errors=result["errors"], mode="single",
            agent_id=body.agent_id or "",
        )
        background_tasks.add_task(_d._async_heartbeat, contract.name, contract.version)
        rule_failure_counts: dict = {}
        for e in result["errors"]:
            _rule = e.get("rule", "unknown")
            rule_failure_counts[_rule] = rule_failure_counts.get(_rule, 0) + 1
        background_tasks.add_task(
            _d._async_record_quality_stats,
            contract_name=contract.name,
            contract_version=contract.version,
            context=body.context,
            total=1,
            passed=1 if result["valid"] else 0,
            failed=0 if result["valid"] else 1,
            rule_failure_counts=rule_failure_counts,
            agent_id=body.agent_id or "",
            mode="observation_only" if getattr(body, "observe_only", False) else "enforcement",
            event_id=event_id,
            caller_principal=user or "",
        )

    if not result["valid"] and not body.dry_run:
        background_tasks.add_task(_d.webhook_manager.notify, "opendqv.validation.failed", {
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
        background_tasks.add_task(_d.webhook_manager.notify, "opendqv.validation.warning", {
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

    _entry_hash, _content_hash = _d._get_contract_hash(contract.name)

    _observe = getattr(body, "observe_only", False)
    # CRT170/J1 + CRT173/25: `valid` reflects the actual validation outcome
    # regardless of mode. `mode` and `would_have_failed` are always populated
    # so the wire shape is deterministic — clients never have to branch on null.
    _mode = "observation_only" if _observe else "enforcement"
    _would_have_failed = not result["valid"]

    return ValidateResponse(
        valid=result["valid"],
        event_id=event_id,
        record_id=body.record_id,
        errors=[FieldErrorResponse(**e) for e in _d._mask_errors(_d._add_suggested_fixes(result["errors"], rules))],
        warnings=[FieldErrorResponse(**w) for w in _d._mask_errors(_d._add_suggested_fixes(result["warnings"], rules))],
        contract=contract.name,
        version=contract.version,
        owner=contract.owner or "",
        engine_version=config.ENGINE_VERSION,
        contract_hash=_entry_hash,
        entry_hash=_entry_hash,
        content_hash=_content_hash,
        effective_rule_hash=_compute_effective_rule_hash(rules),
        owner_team=contract.owner_team,
        validated_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=round(elapsed_ms, 1),
        agent_id=body.agent_id,
        caller_principal=user or "anonymous",
        mode=_mode,
        would_have_failed=_would_have_failed,
        context_warning=_context_warning,
    )


@sub_router.post("/validate/batch", response_model=BatchValidateResponse)
@_d._validate_limit
async def validate_batch_endpoint(
    request: Request,
    body: BatchValidateRequest,
    background_tasks: BackgroundTasks,
    allow_draft: bool = Query(False, description="Allow validation against DRAFT contracts"),
    user=Depends(get_current_user),
):
    """
    Validate a batch of records against a data contract.

    Uses DuckDB for high-throughput batch processing.
    Reduces network overhead vs. calling /validate per record.
    """
    start = time.monotonic()

    if len(body.records) == 0:
        raise HTTPException(
            status_code=400,
            detail="records must not be empty",
        )

    if len(body.records) > config.MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch size {len(body.records)} exceeds the maximum of "
                f"{config.MAX_BATCH_ROWS} records. Split into smaller batches or "
                f"increase OPENDQV_MAX_BATCH_ROWS for this deployment."
            ),
        )

    if body.hash:
        contract = _d.registry.contract_by_hash(body.contract, body.hash)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{body.contract}' has no history entry matching hash '{body.hash}'.",
            )
    else:
        contract = _d._get_contract_versioned_or_404(body.contract, body.version)

    if contract.status == ContractStatus.DRAFT and not allow_draft:
        if not config.STRICT_DRAFT_VALIDATION:
            logger.warning(
                "Serving batch validation against DRAFT contract '%s' — promote to active for production use.",
                contract.name,
            )

    _d._check_validate_in_states(contract, body.contract, allow_draft)

    batch_event_id = str(uuid7())
    try:
        rules = _d.registry.get_rules_with_context(contract, body.context)
    except UnknownContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        "validate_batch event_id=%s caller=%s ip=%s contract=%s v%s context=%s "
        "total=%d passed=%d failed=%d %.1fms",
        batch_event_id, user, client_ip, contract.name, contract.version, body.context or "default",
        result["summary"]["total"], result["summary"]["passed"],
        result["summary"]["failed"], elapsed_ms,
    )
    if not body.dry_run:
        for r in result["results"]:
            stats.record(
                contract=contract.name, context=body.context, valid=r["valid"],
                error_count=len(r["errors"]), warning_count=len(r["warnings"]),
                latency_ms=elapsed_ms / max(len(result["results"]), 1),
                errors=r["errors"], mode="batch",
                agent_id=body.agent_id or "",
            )
        background_tasks.add_task(_d._async_heartbeat, contract.name, contract.version)
        background_tasks.add_task(
            _d._async_record_quality_stats,
            contract_name=contract.name,
            contract_version=contract.version,
            context=body.context,
            total=result["summary"]["total"],
            passed=result["summary"]["passed"],
            failed=result["summary"]["failed"],
            rule_failure_counts=result["summary"].get("rule_failure_counts", {}),
            agent_id=body.agent_id or "",
            mode="observation_only" if getattr(body, "observe_only", False) else "enforcement",
            event_id=batch_event_id,
            caller_principal=user or "",
        )

        if result["summary"]["failed"] > 0:
            failed_errors = []
            for r in result["results"]:
                if not r["valid"]:
                    failed_errors.extend(r["errors"])
            background_tasks.add_task(_d.webhook_manager.notify, "opendqv.batch.failed", {
                "contract": contract.name,
                "contract_version": contract.version,
                "opendqv_node_id": config.OPENDQV_NODE_ID,
                "context": body.context,
                "total": result["summary"]["total"],
                "passed": result["summary"]["passed"],
                "failed": result["summary"]["failed"],
                "error_count": len(failed_errors),
                "violations": failed_errors[:50],
            })

    _entry_hash, _content_hash = _d._get_contract_hash(contract.name)

    _observe = getattr(body, "observe_only", False)
    # CRT173/25: always populate mode and would_have_failed so callers never
    # see null in either mode. would_have_failed is summary.failed > 0 in both
    # modes — observe_only only affects whether downstream systems block.
    _mode = "observation_only" if _observe else "enforcement"
    _would_have_failed = result["summary"]["failed"] > 0

    return BatchValidateResponse(
        event_id=batch_event_id,
        summary=BatchSummary(**result["summary"]),
        results=[
            BatchResultItem(
                index=r["index"],
                event_id=str(uuid7()),
                valid=r["valid"],
                errors=[FieldErrorResponse(**e) for e in _d._mask_errors(_d._add_suggested_fixes(r["errors"], rules))],
                warnings=[FieldErrorResponse(**w) for w in _d._mask_errors(_d._add_suggested_fixes(r["warnings"], rules))],
            )
            for r in result["results"]
        ],
        contract=contract.name,
        version=contract.version,
        owner=contract.owner or "",
        engine_version=config.ENGINE_VERSION,
        contract_hash=_entry_hash,
        entry_hash=_entry_hash,
        content_hash=_content_hash,
        effective_rule_hash=_compute_effective_rule_hash(rules),
        validated_at=datetime.now(timezone.utc).isoformat(),
        latency_ms=round(elapsed_ms, 1),
        agent_id=body.agent_id,
        caller_principal=user or "anonymous",
        mode=_mode,
        would_have_failed=_would_have_failed,
    )


@sub_router.get("/trace/verify", tags=["Audit"])
async def verify_trace_log_endpoint(
    log_path: str = Query(None, description="Path to trace log file (default: opendqv_trace.jsonl)"),
    user: str = Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Verify the tamper-evident hash chain of the TRACE_LOG file. Requires auditor or admin role."""
    if role not in ("auditor", "approver", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot access the audit trail. Required: auditor, approver, or admin.")
    from opendqv.core.trace_log import verify_trace_log as _verify
    result = _verify(log_path)
    return result


@sub_router.post("/validate/batch/file", tags=["Validation"])
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
    dc = _d._get_contract_versioned_or_404(contract, version)

    try:
        rules = _d.registry.get_rules_with_context(dc, context)
    except UnknownContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    content = await file.read()
    filename = file.filename or ""
    df = _d._parse_upload(content, filename)

    records = df.to_dict(orient="records")
    result = validate_batch(records, rules)

    return {
        "filename": filename,
        "rows": len(records),
        **result,
    }
