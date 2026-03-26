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
    agent_id: Optional[str] = Field(None, description="Caller identity — AI agent name, service name, or team. Echoed in response for session correlation.")
    dry_run: bool = Field(False, description="If true, validate without recording results in quality metrics. Use for testing and demos.")

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
    suggested_fix: Optional[str] = Field(None, description="Concise actionable fix hint — use to self-correct and resubmit without a separate explain_error call")


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
    validated_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of validation — use for time-series correlation with quality metrics")
    latency_ms: Optional[float] = Field(None, description="Server-side validation latency in milliseconds")
    agent_id: Optional[str] = Field(None, description="Echo of caller's agent_id — for session and caller attribution")


# ── Batch validation request/response ────────────────────────────────

class BatchValidateRequest(BaseModel):
    """Batch validation request — for high-throughput use cases."""
    records: list[dict] = Field(..., description="List of data records to validate")
    contract: str = Field(..., description="Contract name")
    version: str = Field("latest", description="Contract version or 'latest'")
    context: Optional[str] = Field(None, description="Context override")
    agent_id: Optional[str] = Field(None, description="Caller identity — AI agent name, service name, or team. Echoed in response for session correlation.")
    dry_run: bool = Field(False, description="If true, validate without recording results in quality metrics. Use for testing and demos.")

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
    validated_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of batch validation — use for time-series correlation with quality metrics")
    latency_ms: Optional[float] = Field(None, description="Server-side batch validation latency in milliseconds (total wall-clock time for the batch)")
    agent_id: Optional[str] = Field(None, description="Echo of caller's agent_id — for session and caller attribution")


# ── Contract models ──────────────────────────────────────────────────

class ContractInfo(BaseModel):
    """Summary info about a data contract."""
    name: str = Field(..., description="Unique contract identifier — use this as the 'contract' parameter in /validate calls")
    version: str = Field(..., description="Current version string (e.g. '1.0', '2.3')")
    description: str = Field("", description="Human-readable description of what this contract validates")
    owner: str = Field("", description="Team or individual responsible for this contract")
    status: str = Field("active", description="Lifecycle status: 'active' (use for validation), 'draft' (being authored), 'archived' (migrate away)")
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
    status: str = Field("active", description="Lifecycle status: 'active', 'draft', or 'archived'")
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


# ── Contract history / diff / reload ─────────────────────────────────

class ContractHistoryEntry(BaseModel):
    """One snapshot in a contract's audit history."""
    version: str
    status: str
    description: Optional[str] = None
    owner: Optional[str] = None
    opendqv_node_id: Optional[str] = None
    updated_at: str
    prev_hash: Optional[str] = None
    entry_hash: Optional[str] = None
    approved_by: Optional[str] = None
    proposed_by: Optional[str] = None
    proposed_at: Optional[str] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    rules: list = Field(default_factory=list)
    contexts: dict = Field(default_factory=dict)


class ContractHistoryResponse(BaseModel):
    """Version history for a contract."""
    contract: str
    history: list[ContractHistoryEntry]


class DiffRuleSummary(BaseModel):
    """A rule that was added or removed between two versions."""
    name: str
    type: str
    field: str


class DiffRuleChanged(BaseModel):
    """A rule whose definition changed between two versions."""
    name: str
    field: str
    changes: dict = Field(
        ...,
        description="Per-field changes: {field_name: {old: ..., new: ...}}",
    )


class DiffChanges(BaseModel):
    rules_added: list[DiffRuleSummary] = Field(default_factory=list)
    rules_removed: list[DiffRuleSummary] = Field(default_factory=list)
    rules_changed: list[DiffRuleChanged] = Field(default_factory=list)
    metadata_changed: dict = Field(
        default_factory=dict,
        description="Contract-level field changes (status, description, owner): {field: {old, new}}",
    )


class ContractDiffResponse(BaseModel):
    """Comparison of two contract versions."""
    contract: str
    from_version: str
    to_version: str
    changes: DiffChanges


class ContractReloadResponse(BaseModel):
    """Result of reloading contracts from disk."""
    status: str = Field(..., description="Always 'reloaded'")
    contracts: list[ContractInfo]


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


# ── DuckDB analytics models ───────────────────────────────────────────

class AnalyticsSummaryItem(BaseModel):
    """Pass-rate summary for one contract over the analytics window."""
    contract: str = Field(..., description="Contract name")
    total_records: int = Field(..., description="Total records validated in the window")
    passed: int = Field(..., description="Records that passed")
    failed: int = Field(..., description="Records that failed")
    pass_rate: float = Field(..., description="Pass rate as a fraction (0.0–1.0)")
    pass_rate_pct: float = Field(..., description="Pass rate as a percentage (0.0–100.0)")


class AnalyticsSummaryResponse(BaseModel):
    """Cross-contract pass rate summary backed by DuckDB OLAP over SQLite quality data."""
    days: int = Field(..., description="Analytics window in days")
    contracts: list[AnalyticsSummaryItem] = Field(
        ..., description="Per-contract summary, sorted by pass_rate ascending (worst first)"
    )
    total_contracts: int = Field(..., description="Number of contracts with data in the window")


class RuleHeatmapItem(BaseModel):
    """Failure count for one (contract, rule) pair over the analytics window."""
    contract: str = Field(..., description="Contract name")
    rule: str = Field(..., description="Rule name")
    failure_count: int = Field(..., description="Total failures for this rule in the window")


class RuleHeatmapResponse(BaseModel):
    """Top failing rules across all contracts, backed by DuckDB OLAP over SQLite quality data."""
    days: int = Field(..., description="Analytics window in days")
    rules: list[RuleHeatmapItem] = Field(
        ..., description="Rules ranked by failure_count descending (up to 50)"
    )
    total_rules: int = Field(..., description="Number of distinct (contract, rule) pairs returned")


class RuleVelocityBucket(BaseModel):
    """One time bucket in a rule velocity series."""
    bucket: str = Field(..., description="Bucket start time in ISO 8601 format (UTC)")
    failures: int = Field(..., description="Total failures for this rule in this bucket")


class RuleVelocityResponse(BaseModel):
    """Time-series failure counts per rule — shows acceleration vs slow drip."""
    contract: str = Field(..., description="Contract name")
    window_hours: int = Field(..., description="Look-back window in hours")
    bucket_minutes: int = Field(..., description="Bucket width in minutes")
    series: dict[str, list[RuleVelocityBucket]] = Field(
        ..., description="Per-rule time-series (top 5 rules by total failures)"
    )
