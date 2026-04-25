import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.core.contracts import validate_promotion_readiness
from opendqv.core.rule_parser import ContractStatus
from opendqv.security.auth import get_current_user, get_current_role

from .models import (
    ContractInfo, ContractDetail, RuleInfo,
    QualityTrendPoint, QualityTrendResponse,
    ExplainErrorResponse,
    ContractHistoryResponse, ContractDiffResponse, ContractReloadResponse,
)

logger = logging.getLogger(__name__)

sub_router = APIRouter()


@sub_router.get("/contracts", response_model=list[ContractInfo])
@_d._default_limit
async def list_contracts(
    request: Request,
    include_all: bool = Query(False, description="Include ARCHIVED contracts"),
):
    """List available data contracts. No auth required — contracts are public metadata."""
    return [ContractInfo(**c) for c in _d.registry.list_contracts(include_all=include_all)]


@sub_router.get("/contracts/{name}", response_model=ContractDetail)
@_d._default_limit
async def get_contract(
    request: Request,
    name: str,
    version: str = Query("latest"),
    hash: Optional[str] = Query(
        None,
        description=(
            "SHA-256 contract_hash returned from a prior validate response. "
            "When provided, the exact historical contract version that produced that hash "
            "is returned — required for regulator-grade point-in-time audit retrieval. "
            "Takes precedence over `version`."
        ),
    ),
):
    """Get full detail of a data contract including its rules.

    By default returns the latest version. Pass ?version=<v> for a named version,
    or ?hash=<contract_hash> to retrieve the exact historical version that produced
    a hash returned on a prior validate response.
    """
    _entry_hash = None
    _content_hash = None
    if hash:
        contract = _d.registry.contract_by_hash(name, hash)
        if not contract:
            raise HTTPException(
                status_code=404,
                detail=f"Contract '{name}' has no history entry matching hash '{hash}'",
            )
        for _snap in _d.registry.get_history(name):
            if _snap.get("entry_hash") == hash or _snap.get("content_hash") == hash:
                _entry_hash = _snap.get("entry_hash")
                _content_hash = _snap.get("content_hash")
                break
    else:
        contract = _d._get_contract_versioned_or_404(name, version)
        for _snap in reversed(_d.registry.get_history(name)):
            if _snap.get("version") == contract.version:
                _entry_hash = _snap.get("entry_hash")
                _content_hash = _snap.get("content_hash")
                break

    def _rule_values(r) -> list[str] | None:
        if r.type != "lookup" or not r.lookup_file or r.lookup_file.startswith("http"):
            return None
        try:
            fp = _d.registry.contracts_dir / r.lookup_file
            with open(fp, encoding="utf-8") as _f:
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
        contexts=sorted(contract.contexts.keys()),
        asset_id=contract.asset_id,
        owner_team=contract.owner_team,
        owner_email=contract.owner_email,
        contract_hash=_entry_hash,
        entry_hash=_entry_hash,
        content_hash=_content_hash,
    )


@sub_router.get("/contracts/{name}/explain", tags=["Contracts"])
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
    if not _d.EXPLAIN_PUBLIC and config.AUTH_MODE != "open":
        from fastapi.security.utils import get_authorization_scheme_param
        from opendqv.security import auth as _auth_mod
        import jwt as _jose_jwt
        from jwt.exceptions import InvalidTokenError as _JWTInvalidTokenError
        if not authorization:
            raise HTTPException(status_code=401, detail="No token provided. Set AUTH_MODE=open to disable auth.")
        scheme, token_val = get_authorization_scheme_param(authorization)
        if scheme.lower() != "bearer" or not token_val:
            raise HTTPException(status_code=401, detail="Invalid authorization header format")
        try:
            payload = _jose_jwt.decode(token_val, _auth_mod.SECRET_KEY, algorithms=[_auth_mod.ALGORITHM])
            if not payload.get("sub"):
                raise HTTPException(status_code=401, detail="Invalid token payload")
        except _JWTInvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

    contract = _d._get_contract_versioned_or_404(name, version)

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
            pass
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


@sub_router.get(
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
    from opendqv.core.explainer import explain_rule
    contract = _d._get_contract_versioned_or_404(name, version)

    matching = [r for r in contract.rules if r.name == rule_name and r.field == field]
    if not matching:
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
        lookup_source=info.get("lookup_source"),
        constraint=info["constraint"],
    )


@sub_router.get("/contracts/{name}/lint", tags=["Contracts"])
async def lint_contract(
    name: str,
    user: str = Depends(get_current_user),
):
    """
    Lint a contract's YAML for logical errors before deployment.

    Returns a structured list of issues (errors and warnings). Responds with
    HTTP 200 and `"passed": true` when no errors are found; HTTP 422 when
    errors are present so CI pipelines can gate on status code.
    """
    from opendqv.core.linter import lint_contract_file
    from pathlib import Path as _Path

    contract_path = _Path(config.CONTRACTS_DIR) / f"{name}.yaml"
    if not contract_path.exists():
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    result = lint_contract_file(str(contract_path))
    payload = result.to_dict()

    if not result.passed:
        raise HTTPException(status_code=422, detail=payload)

    return payload


@sub_router.get("/contracts/{name}/quality-trend", response_model=QualityTrendResponse)
@_d._default_limit
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
    c = _d._get_contract_or_404(name)

    points = _d._quality_stats.get_trend(name, days=days, context=context)
    return QualityTrendResponse(
        contract=name,
        days=days,
        context=context,
        points=[QualityTrendPoint(**p) for p in points],
        asset_id=c.asset_id,
    )


@sub_router.delete("/quality/stats")
@_d._default_limit
async def delete_quality_stats_by_context(
    request: Request,
    context: str = Query(..., description="Context tag to delete (e.g. 'demo')"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Delete all quality statistics records for a given context tag.

    Intended for demo teardown — removes records written with context='demo'
    after a prospect session ends. Requires admin role.
    """
    if role not in ("admin",):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted. Required: admin.",
        )
    deleted = _d._quality_stats.delete_by_context(context)
    return {"deleted": deleted, "context": context}


@sub_router.get("/contracts/{name}/at")
@_d._default_limit
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
    history = _d.registry.get_history(name)
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


@sub_router.post("/contracts/reload", response_model=ContractReloadResponse)
@_d.limiter.limit("5/minute")
async def reload_contracts(request: Request, user=Depends(get_current_user), role: str = Depends(get_current_role)):
    """Reload contracts from disk. Useful after editing YAML files."""
    if role not in ("admin",):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: admin.")
    _d.registry.reload()
    return {"status": "reloaded", "contracts": _d.registry.list_contracts(include_all=True)}


@sub_router.post("/contracts/{name}/status")
@_d.limiter.limit("10/minute")
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

    contract = _d._get_contract_versioned_or_404(name, version)

    if new_status == ContractStatus.ACTIVE and role not in ("admin", "approver"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Promoting a contract to 'active' requires the 'approver' or 'admin' role. "
                f"Current role: '{role}'. Request approval from a contract approver."
            ),
        )

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

    if new_status == ContractStatus.ACTIVE:
        issues = validate_promotion_readiness(contract)
        if issues:
            raise HTTPException(
                status_code=422,
                detail=f"Contract '{name}' is not ready for activation: " + "; ".join(issues),
            )

    try:
        contract = _d.registry.set_status(name, version, new_status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    logger.info(
        "contract_status_change: name=%s version=%s status=%s caller=%s role=%s",
        name, version, new_status.value, user, role,
    )
    return {
        "name": contract.name,
        "version": contract.version,
        "status": contract.status.value,
        "message": f"Contract '{name}' is now {contract.status.value}",
        "approved_by": user,
    }


@sub_router.post("/contracts/{name}/{version}/submit-review", tags=["Contracts"])
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
        contract = _d.registry.submit_for_review(name, version, proposed_by)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    await _d.webhook_manager.notify("opendqv.contract.submitted", {
        "contract": name, "version": version, "proposed_by": proposed_by,
    })
    return {"status": "submitted", "contract": name, "version": version, "proposed_by": proposed_by}


@sub_router.post("/contracts/{name}/{version}/approve", tags=["Contracts"])
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
        contract = _d.registry.approve_contract(name, version, approved_by)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    await _d.webhook_manager.notify("opendqv.contract.approved", {
        "contract": name, "version": version, "approved_by": approved_by,
    })
    return {"status": "approved", "contract": name, "version": version, "approved_by": approved_by}


@sub_router.post("/contracts/{name}/{version}/reject", tags=["Contracts"])
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
        contract = _d.registry.reject_contract(name, version, rejected_by, reason)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' v{version} not found")
    await _d.webhook_manager.notify("opendqv.contract.rejected", {
        "contract": name, "version": version, "rejected_by": rejected_by, "reason": reason,
    })
    return {"status": "rejected", "contract": name, "version": version, "rejected_by": rejected_by, "reason": reason}


@sub_router.get("/contracts/{name}/history", response_model=ContractHistoryResponse)
@_d._default_limit
async def get_contract_history(
    request: Request,
    name: str,
    user=Depends(get_current_user),
):
    """Get version history for a contract."""
    history = _d.registry.get_history(name)
    if not history and not _d.registry.get(name):
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return {"contract": name, "history": history}


@sub_router.get("/contracts/{name}/diff", response_model=ContractDiffResponse)
@_d._default_limit
async def diff_contract_versions(
    request: Request,
    name: str,
    version_a: str = Query(..., description="First version to compare"),
    version_b: str = Query(..., description="Second version to compare"),
    user=Depends(get_current_user),
):
    """Compare two versions of a contract."""
    if not _d.registry.get(name):
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    try:
        diff = _d.registry.diff_versions(name, version_a, version_b)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"One or both versions not found for contract '{name}'")
    return diff


@sub_router.post("/contracts/{name}/version")
@_d.limiter.limit("10/minute")
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
    if role not in ("admin", "approver"):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Creating a new contract version requires the 'approver' or 'admin' role. "
                f"Current role: '{role}'. Request a version bump from a contract approver."
            ),
        )

    contract = _d._get_contract_or_404(name)

    old_version = contract.version

    if old_version == new_version:
        raise HTTPException(status_code=400, detail=f"New version must differ from current version '{old_version}'.")

    _d.registry.history.record_version(contract)

    contract.version = new_version
    contract.status = ContractStatus.DRAFT

    if name in _d.registry._contracts:
        _d.registry._contracts[name][new_version] = contract

    _d.registry.history.record_version(contract)

    logger.info(
        "version_bump caller=%s contract=%s old_version=%s new_version=%s status=draft",
        user, name, old_version, new_version,
    )

    try:
        diff = _d.registry.diff_versions(name, old_version, new_version)
    except ValueError:
        diff = None

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


@sub_router.post("/contracts/{name}/rules", tags=["Contracts"])
@_d._default_limit
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
    contract = _d._get_contract_or_404(name)

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    _d._assert_contract_mutable(contract, name, user, "add_rule")
    try:
        contract = _d.registry.add_rule(name, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "added", "contract": name, "rule": body.get("name"), "rule_count": len(contract.rules), "version": contract.version}


@sub_router.put("/contracts/{name}/rules/{rule_name}", tags=["Contracts"])
@_d._default_limit
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
    contract = _d._get_contract_or_404(name)

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    _d._assert_contract_mutable(contract, name, user, "update_rule")
    try:
        contract, breaking = _d.registry.update_rule(name, rule_name, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    resp: dict = {"status": "updated", "contract": name, "rule": rule_name, "version": contract.version}
    if breaking:
        resp["breaking_change_warning"] = (
            "Modifying an existing rule may cause previously passing validations to fail. "
            "Consider bumping the contract version when promoting this draft to ACTIVE."
        )
    return resp


@sub_router.delete("/contracts/{name}/rules/{rule_name}", tags=["Contracts"])
@_d._default_limit
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
    contract = _d._get_contract_or_404(name)

    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot modify contract rules. Required: editor or admin.")

    _d._assert_contract_mutable(contract, name, user, "delete_rule")
    try:
        contract = _d.registry.delete_rule(name, rule_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "deleted", "contract": name, "rule": rule_name, "rule_count": len(contract.rules), "version": contract.version}


@sub_router.get("/registry", tags=["Schema Registry"])
@_d._default_limit
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
    contracts = _d.registry.list_contracts(include_all=False)
    result = []
    for c in contracts:
        contract = _d.registry.get(c["name"])
        history = _d.registry.get_history(c["name"])
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


@sub_router.get("/registry/{name}", tags=["Schema Registry"])
@_d._default_limit
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
    contract = _d._get_contract_or_404(name)
    history = _d.registry.get_history(name)
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


@sub_router.post("/generate")
@_d._default_limit
async def generate_code_endpoint(
    request: Request,
    contract_name: str = Query(..., description="Contract to generate code for"),
    target: str = Query(..., description="Target platform: snowflake, salesforce, js, spark, bigquery"),
    version: str = Query("latest"),
    context: str = Query(None, description="Optional context to apply (e.g. 'salesforce', 'kids_app')"),
    user=Depends(get_current_user),
):
    """Generate validation code for a target platform from a contract's rules."""
    from opendqv.core.code_generator import generate_code
    contract = _d._get_contract_versioned_or_404(contract_name, version)

    rules = _d.registry.get_rules_with_context(contract, context)
    code = generate_code(rules, target, contract_name=contract.name, contract_version=contract.version)
    return {"contract": contract.name, "target": target, "context": context, "code": code}
