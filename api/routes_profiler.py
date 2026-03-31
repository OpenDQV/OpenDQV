import os
import yaml as _yaml

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile

import api.deps as _d
import config
from core.profiler import profile_records
from security.auth import get_current_user

sub_router = APIRouter()


@sub_router.post("/profile")
@_d._default_limit
async def profile_data(
    request: Request,
    records: list[dict] = Body(...),
    contract_name: str = Query("profiled", description="Name for the generated contract"),
    save: bool = Query(False, description="Save as YAML contract"),
    user=Depends(get_current_user),
):
    """Analyze records and auto-generate an OpenDQV contract with suggested rules."""
    _d._validate_contract_name(contract_name)
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
):
    """
    Profile records from an uploaded CSV or Parquet file.

    Returns a field-level statistical profile and suggested contract rules.
    DuckDB-powered: includes mean, stddev, and percentiles for numeric fields.
    Max file size: configured via OPENDQV_MAX_UPLOAD_MB (default 10MB).
    """
    _d._validate_contract_name(contract_name)
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
