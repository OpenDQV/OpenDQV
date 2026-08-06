import os
import yaml as _yaml

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.core.profiler import profile_records
from opendqv.core.rule_parser import ContractStatus
from opendqv.security.auth import get_current_user, get_current_role

sub_router = APIRouter()


def _assert_may_save_profile(role: str, contract_name: str) -> None:
    """Guard the profiler's `save=true` contract-write path (CRT177 Tier 2).

    Two holes closed here:

    1. **No role guard.** Every sibling write path (`/import/*`) requires
       editor/admin (SEC-010); the profiler save did not, so ANY authenticated
       principal — including `reader`/`validator`/`auditor` — could write a
       contract.
    2. **Destructive overwrite by name.** The write was a bare
       ``open(f"{contract_name}.yaml", "w")`` with no existence check, and
       `_validate_contract_name` is charset-only. `?contract_name=customer`
       therefore REPLACED the live ACTIVE `customer` contract with
       profiler-generated rules and reloaded it — bypassing ACTIVE
       immutability and the approval workflow in a single unprivileged request.

    Profiling into a *new* name, or refreshing one's own DRAFT, stays allowed.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' is not permitted to save contracts. Required: editor or admin.",
        )
    existing = _d.registry.get(contract_name)
    if existing is not None and existing.status != ContractStatus.DRAFT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Contract '{contract_name}' already exists with status "
                f"'{existing.status.value}' and will not be overwritten by the profiler. "
                f"Profile into a new name, or create a draft version first."
            ),
        )


@sub_router.post("/profile")
@_d._default_limit
async def profile_data(
    request: Request,
    records: list[dict] = Body(...),
    contract_name: str = Query("profiled", description="Name for the generated contract"),
    save: bool = Query(False, description="Save as YAML contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """Analyze records and auto-generate an OpenDQV contract with suggested rules."""
    _d._validate_contract_name(contract_name)
    if save:
        _assert_may_save_profile(role, contract_name)
    result = profile_records(records, contract_name=contract_name)

    if save:
        contract_data = {"contract": result["contract"]}
        yaml_content = _yaml.dump(contract_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        _d.registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    return result


@sub_router.post("/profile/file", tags=["Profiler"])
@_d._default_limit
async def profile_file(
    request: Request,
    file: UploadFile = File(...),
    contract_name: str = Query("profiled", description="Name for the generated contract"),
    save: bool = Query(False, description="Save as YAML contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Profile records from an uploaded CSV or Parquet file.

    Returns a field-level statistical profile and suggested contract rules.
    DuckDB-powered: includes mean, stddev, and percentiles for numeric fields.
    Max file size: configured via OPENDQV_MAX_UPLOAD_MB (default 10MB).
    """
    _d._validate_contract_name(contract_name)
    if save:
        _assert_may_save_profile(role, contract_name)
    content = await file.read()
    filename = file.filename or ""
    df = _d._parse_upload(content, filename)

    records = df.to_dict(orient="records")
    result = profile_records(records, contract_name=contract_name)

    if save:
        contract_data = {"contract": result["contract"]}
        yaml_content = _yaml.dump(contract_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        _d.registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    result["filename"] = filename
    result["rows"] = len(records)
    return result
