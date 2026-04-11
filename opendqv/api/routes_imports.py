import os
import yaml as _yaml

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

import opendqv.api.deps as _d
import opendqv.config as config
from opendqv.core.importers.great_expectations import import_gx_suite, gx_suite_to_yaml, export_gx_suite
from opendqv.core.importers.dbt import import_dbt_schema, dbt_schema_to_yaml
from opendqv.core.importers.soda import import_soda_checks, soda_checks_to_yaml
from opendqv.core.importers.csv_rules import import_csv_rules, csv_rules_to_yaml
from opendqv.core.importers.odcs import import_odcs, odcs_to_yaml, contract_to_odcs_yaml
from opendqv.core.importers.csvw import import_csvw, csvw_to_yaml
from opendqv.core.importers.otel import import_otel, otel_to_yaml
from opendqv.core.importers.ndc import import_ndc, ndc_to_yaml
from opendqv.core.contracts import UnknownContextError
from opendqv.security.auth import get_current_user, get_current_role

sub_router = APIRouter()


@sub_router.post("/import/gx")
@_d._default_limit
async def import_great_expectations(
    request: Request,
    response: Response,
    suite: dict = Body(..., description="Great Expectations expectation suite JSON"),
    save: bool = Query(False, description="Save as YAML contract to disk and reload registry"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import a Great Expectations expectation suite and convert to an OpenDQV contract.

    Accepts both GX 0.x and 1.x suite formats. Maps supported expectation types
    to OpenDQV rules. Unsupported expectations are skipped and reported.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    result = import_gx_suite(suite)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        contract_name = result["contract"]["name"]
        _d._validate_contract_name(contract_name)
        yaml_content = gx_suite_to_yaml(suite)
        _dd = _yaml.safe_load(yaml_content)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_content = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        _d.registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    return result


@sub_router.post("/import/dbt")
@_d._default_limit
async def import_dbt(
    request: Request,
    response: Response,
    schema: dict = Body(..., description="dbt schema.yml content as JSON"),
    save: bool = Query(False, description="Save contracts to disk and reload"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import a dbt schema.yml and convert to OpenDQV contracts.

    Parses both models and sources sections. Each model/source becomes a separate contract.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    result = import_dbt_schema(schema)
    for item in result["contracts"]:
        item["contract"]["source"] = "import"
        item["contract"]["status"] = "draft"

    if save:
        saved_files = []
        contracts_dir = str(config.CONTRACTS_DIR)
        for item in result["contracts"]:
            contract_name = item["contract"]["name"]
            _d._validate_contract_name(contract_name)
            pairs = dbt_schema_to_yaml(schema)
            for name, yaml_content in pairs:
                if name == contract_name:
                    _dd = _yaml.safe_load(yaml_content)
                    _dd["source"] = "import"
                    _dd["status"] = "draft"
                    if created_by:
                        _dd["created_by"] = created_by
                    yaml_content = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
                    file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(yaml_content)
                    saved_files.append(file_path)
                    break
        _d.registry.reload()
        result["saved_to"] = saved_files
        result["message"] = f"Saved {len(saved_files)} contract(s)"

    return result


@sub_router.post("/import/soda")
@_d._default_limit
async def import_soda(
    request: Request,
    response: Response,
    checks: dict = Body(..., description="Soda checks YAML content as JSON dict"),
    save: bool = Query(False, description="Save contracts to disk and reload"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import a Soda Core checks YAML and convert to OpenDQV contracts.

    Parses ``checks for <dataset>:`` blocks. Each dataset becomes a separate contract.
    Supports missing_count, duplicate_count, invalid_count, min, max, min_length, max_length.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    result = import_soda_checks(checks)
    for item in result.get("contracts", []):
        item["contract"]["source"] = "import"
        item["contract"]["status"] = "draft"

    if save:
        saved_files = []
        contracts_dir = str(config.CONTRACTS_DIR)
        pairs = soda_checks_to_yaml(checks)
        for name, yaml_content in pairs:
            _d._validate_contract_name(name)
            _dd = _yaml.safe_load(yaml_content)
            _dd["source"] = "import"
            _dd["status"] = "draft"
            if created_by:
                _dd["created_by"] = created_by
            yaml_content = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
            file_path = os.path.join(contracts_dir, f"{name}.yaml")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)
            saved_files.append(file_path)
        _d.registry.reload()
        result["saved_to"] = saved_files
        result["message"] = f"Saved {len(saved_files)} contract(s)"

    return result


@sub_router.post("/import/csv")
@_d._default_limit
async def import_csv(
    request: Request,
    response: Response,
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("csv_import", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import a CSV rule definition and convert to an OpenDQV contract.

    Accepts CSV as plain text body. Expected columns: field, rule_type, value, severity, error_message.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    body_bytes = await request.body()
    csv_content = body_bytes.decode("utf-8")

    _d._validate_contract_name(contract_name)
    result = import_csv_rules(csv_content, contract_name)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        yaml_content = csv_rules_to_yaml(csv_content, contract_name)
        _dd = _yaml.safe_load(yaml_content)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_content = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        _d.registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    return result


@sub_router.post("/import/odcs")
@_d._default_limit
async def import_odcs_contract(
    request: Request,
    response: Response,
    contract_data: dict = Body(..., description="ODCS 3.1 contract as JSON dict"),
    save: bool = Query(False, description="Save as YAML contract to disk and reload registry"),
    contract_name: str = Query("", description="Override contract name (default: from info.title)"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
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
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    result = import_odcs(contract_data)
    result["contract"]["source"] = "import"
    result["contract"]["status"] = "draft"

    if save:
        contract_name, yaml_content = odcs_to_yaml(contract_data, contract_name or None)
        _d._validate_contract_name(contract_name)
        _dd = _yaml.safe_load(yaml_content)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_content = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        contracts_dir = str(config.CONTRACTS_DIR)
        file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        _d.registry.reload()
        result["saved_to"] = file_path
        result["message"] = f"Contract '{contract_name}' saved and loaded"

    return result


@sub_router.post("/import/csvw")
@_d._default_limit
async def import_from_csvw(
    request: Request,
    response: Response,
    body: dict = Body(..., description="CSVW JSON-LD metadata document"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("csvw_import", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import contract rules from CSVW (CSV on the Web) W3C metadata.

    Accepts a CSVW JSON-LD metadata document and maps column definitions to
    OpenDQV validation rules. Supports required, pattern, range, length, and
    enum constraints.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    _d._validate_contract_name(contract_name)
    try:
        result = import_csvw(body)
        yaml_output = csvw_to_yaml(body, contract_name)
        _dd = _yaml.safe_load(yaml_output)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_output = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(yaml_output)
            _d.registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSVW import failed: {e}")


@sub_router.post("/import/otel")
@_d._default_limit
async def import_from_otel(
    request: Request,
    response: Response,
    body: dict = Body(..., description="OTel semantic convention schema as JSON dict"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("otel_telemetry", description="Name for the imported contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Import contract rules from OpenTelemetry semantic convention schema.

    Accepts an OTel attribute group definition and maps requirement levels,
    known enum attributes, and numeric ranges to OpenDQV validation rules.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    _d._validate_contract_name(contract_name)
    try:
        result = import_otel(body)
        yaml_output = otel_to_yaml(body, contract_name)
        _dd = _yaml.safe_load(yaml_output)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_output = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(yaml_output)
            _d.registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OTel import failed: {e}")


@sub_router.post("/import/ndc")
@_d._default_limit
async def import_from_ndc(
    request: Request,
    response: Response,
    body: dict = Body(default={}, description="NDC importer configuration"),
    save: bool = Query(False, description="Save contract to disk and reload"),
    contract_name: str = Query("pharma_dispense", description="Name for the generated contract"),
    created_by: str = Query("", description="Identity of the caller creating this contract"),
    user=Depends(get_current_user),
    role: str = Depends(get_current_role),
):
    """
    Generate NDC (National Drug Code) validation rules.

    Accepts an optional configuration dict specifying which field names to
    validate as NDC codes, desired severity, and format flags. Returns
    OpenDQV rules covering presence and format validation per FDA standard.

    Pass ?save=true to write the contract YAML to the contracts/ directory and
    reload the registry, making it immediately available for validation.
    """
    if role not in ("editor", "admin"):
        raise HTTPException(status_code=403, detail=f"Role '{role}' is not permitted. Required: editor or admin.")
    _d._validate_contract_name(contract_name)
    try:
        result = import_ndc(body)
        yaml_output = ndc_to_yaml(body, contract_name)
        _dd = _yaml.safe_load(yaml_output)
        _dd["source"] = "import"
        _dd["status"] = "draft"
        if created_by:
            _dd["created_by"] = created_by
        yaml_output = _yaml.dump(_dd, default_flow_style=False, allow_unicode=True, sort_keys=False)
        resp = {
            "rules": result["rules"],
            "metadata": result["metadata"],
            "source": "import",
            "status": "draft",
            "yaml": yaml_output,
        }
        if save:
            contracts_dir = str(config.CONTRACTS_DIR)
            file_path = os.path.join(contracts_dir, f"{contract_name}.yaml")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(yaml_output)
            _d.registry.reload()
            resp["saved_to"] = file_path
            resp["message"] = f"Contract '{contract_name}' saved and loaded"
        return resp
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"NDC import failed: {e}")


@sub_router.get("/export/gx/{contract_name}")
@_d._default_limit
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
    contract = _d._get_contract_versioned_or_404(contract_name, version)

    try:
        rules = _d.registry.get_rules_with_context(contract, context)
    except UnknownContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    suite = export_gx_suite(contract.name, rules)
    suite["meta"]["contract_version"] = contract.version
    suite["meta"]["context"] = context
    return suite


@sub_router.get("/export/odcs/{contract_name}")
@_d._default_limit
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
    contract = _d._get_contract_versioned_or_404(contract_name, version)

    try:
        rules = _d.registry.get_rules_with_context(contract, context)
    except UnknownContextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
