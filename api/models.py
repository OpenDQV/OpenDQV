"""
Request/Response models for the OpenDQV API.

These are the integration contract — source systems depend on this shape.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ── Validation request/response ──────────────────────────────────────

class ValidateRequest(BaseModel):
    """Single-record validation request."""
    record: dict = Field(..., description="The data record to validate")
    contract: str = Field(..., description="Contract name (e.g. 'customer')")
    version: str = Field("latest", description="Contract version or 'latest'")
    context: Optional[str] = Field(None, description="Context override (e.g. 'kids_app', 'salesforce')")
    record_id: Optional[str] = Field(None, description="Caller's correlation ID for tracking")

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "record": {"email": "alice@example.com", "age": 25, "name": "Alice"},
                "contract": "customer",
                "version": "latest",
                "record_id": "sf-lead-12345",
            }]
        }
    }


class FieldErrorResponse(BaseModel):
    """A single field-level validation failure."""
    field: str = Field(..., description="The field that failed validation")
    rule: str = Field(..., description="The rule name that failed, as defined in the contract YAML")
    message: str = Field(..., description="Human-readable description of the failure")
    severity: str = Field(..., description="Blocking level: 'error' prevents the record from being accepted; 'warning' is informational only")


class ValidateResponse(BaseModel):
    """Single-record validation response."""
    valid: bool = Field(..., description="True if no blocking errors (warnings allowed)")
    record_id: Optional[str] = Field(None, description="Echo of caller's correlation ID")
    errors: list[FieldErrorResponse] = Field(default_factory=list, description="Blocking validation failures")
    warnings: list[FieldErrorResponse] = Field(default_factory=list, description="Non-blocking quality warnings")
    contract: str = Field(..., description="Contract that was evaluated")
    version: str = Field(..., description="Contract version that was evaluated")
    owner: str = Field("", description="Contract owner — for routing disputes and on-call escalation")
    engine_version: str = Field(
        "",
        description="OpenDQV engine version — required for EMA clinical trial submissions, MiFIR regulatory reporting, and Basel III audit trails",
    )
    contract_hash: Optional[str] = Field(
        None,
        description="SHA-256 hash of the contract ruleset at validation time — enables point-in-time audit evidence",
    )
    owner_team: Optional[str] = None


# ── Batch validation request/response ────────────────────────────────

class BatchValidateRequest(BaseModel):
    """Batch validation request — for high-throughput use cases."""
    records: list[dict] = Field(..., description="List of data records to validate")
    contract: str = Field(..., description="Contract name")
    version: str = Field("latest", description="Contract version or 'latest'")
    context: Optional[str] = Field(None, description="Context override")

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "records": [
                    {"email": "alice@example.com", "age": 25, "name": "Alice"},
                    {"email": "bad-email", "age": 15, "name": ""},
                ],
                "contract": "customer",
            }]
        }
    }


class BatchResultItem(BaseModel):
    """Validation result for a single record within a batch."""
    index: int = Field(..., description="Zero-based position of this record in the submitted list")
    valid: bool = Field(..., description="True if no blocking errors for this record")
    errors: list[FieldErrorResponse] = Field(default_factory=list, description="Blocking validation failures for this record")
    warnings: list[FieldErrorResponse] = Field(default_factory=list, description="Non-blocking quality warnings for this record")


class BatchSummary(BaseModel):
    """Summary statistics for a batch validation."""
    total: int = Field(..., description="Total number of records submitted")
    passed: int = Field(..., description="Number of records with no blocking errors")
    failed: int = Field(..., description="Number of records with at least one blocking error")
    error_count: int = Field(..., description="Total number of blocking errors across all records")
    warning_count: int = Field(..., description="Total number of warnings across all records")
    rule_failure_counts: dict = Field(
        default_factory=dict,
        description="Per-rule failure counts across all records: {rule_name: count}. "
                    "Use for triage — the rules with the highest counts are the most impactful to fix.",
    )


class BatchValidateResponse(BaseModel):
    """Batch validation response."""
    summary: BatchSummary = Field(..., description="Aggregate statistics across all submitted records")
    results: list[BatchResultItem] = Field(..., description="Per-record validation results in submission order")
    contract: str = Field(..., description="Contract that was evaluated")
    version: str = Field(..., description="Contract version that was evaluated")
    owner: str = Field("", description="Contract owner — for routing disputes and on-call escalation")
    engine_version: str = Field("", description="OpenDQV engine version — for regulatory audit trails")
    contract_hash: Optional[str] = Field(None, description="SHA-256 hash of the contract ruleset at validation time")


# ── Contract models ──────────────────────────────────────────────────

class ContractInfo(BaseModel):
    """Summary info about a data contract."""
    name: str = Field(..., description="Unique contract identifier — use this as the 'contract' parameter in /validate calls")
    version: str = Field(..., description="Current version string (e.g. '1.0', '2.3')")
    description: str = Field("", description="Human-readable description of what this contract validates")
    owner: str = Field("", description="Team or individual responsible for this contract")
    status: str = Field("active", description="Lifecycle status: 'active' (use for validation), 'draft' (being authored), 'deprecated' (migrate away)")
    rule_count: int = Field(0, description="Number of validation rules in this contract")
    asset_id: Optional[str] = Field(None, description="Data catalog asset ID — links this contract to its catalog entry")


class RuleInfo(BaseModel):
    """Rule details within a contract."""
    name: str = Field(..., description="Unique rule identifier within the contract")
    type: str = Field(..., description="Rule type: not_empty, regex, min, max, range, min_length, max_length, date_format, email, enum, lookup, etc.")
    field: str = Field(..., description="The field this rule applies to")
    severity: str = Field(..., description="'error' blocks the record; 'warning' allows but flags it")
    error_message: str = Field(..., description="Message returned when this rule fails")
    values: Optional[list[str]] = Field(None, description="Valid values for lookup rules — first entry used to pre-populate workbench sample records")


class ContractDetail(BaseModel):
    """Full detail of a data contract including its rules."""
    name: str = Field(..., description="Unique contract identifier")
    version: str = Field(..., description="Contract version string")
    description: str = Field("", description="Human-readable description of what this contract validates")
    owner: str = Field("", description="Team or individual responsible for this contract")
    status: str = Field("active", description="Lifecycle status: 'active', 'draft', or 'deprecated'")
    rules: list[RuleInfo] = Field(default_factory=list, description="All validation rules in this contract")
    contexts: list[str] = Field(default_factory=list, description="Named per-system rule overrides available (e.g. 'salesforce', 'kids_app')")
    asset_id: Optional[str] = Field(None, description="Data catalog asset ID")
    owner_team: Optional[str] = Field(None, description="Owning team name")
    owner_email: Optional[str] = Field(None, description="Owning team contact email")


# ── Quality trend models ──────────────────────────────────────────────

class QualityTrendPoint(BaseModel):
    """Aggregated quality statistics for one calendar day."""
    date: str = Field(..., description="Calendar date (UTC), YYYY-MM-DD")
    total_records: int = Field(..., description="Total records validated on this day")
    passed: int
    failed: int
    pass_rate: float = Field(..., description="Fraction of records that passed (0.0–1.0)")
    top_failing_rules: dict = Field(
        default_factory=dict,
        description="Top failing rules for this day: {rule_name: fail_count}",
    )


class QualityTrendResponse(BaseModel):
    """Quality trend response for a contract over N days."""
    contract: str
    days: int
    context: Optional[str] = None
    points: list[QualityTrendPoint]
    asset_id: Optional[str] = None  # catalog asset identifier — enables quality signal self-discovery


# ── Explain error response ────────────────────────────────────────────

class ExplainErrorResponse(BaseModel):
    """
    Plain-English explanation of a validation rule failure — designed for LLM agents
    that need to understand what went wrong and how to fix it without reading YAML.
    """
    contract: str = Field(..., description="Contract the rule belongs to")
    field: str = Field(..., description="The field that failed")
    rule: str = Field(..., description="The rule name that failed")
    rule_type: str = Field(..., description="The rule type (min, max, regex, enum, etc.)")
    explanation: str = Field(..., description="Plain-English explanation of the constraint and how to fix it")
    valid_examples: list = Field(default_factory=list, description="Example values that would pass this rule")
    invalid_examples: list = Field(default_factory=list, description="Example values that would fail this rule")
    constraint: dict = Field(default_factory=dict, description="The raw constraint values from the contract (e.g. {'min': 0.01})")
