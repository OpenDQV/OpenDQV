"""
Request/Response models for the OpenDQV API.

These are the integration contract — source systems depend on this shape.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional


# v2.3.17 F-B (Cluster 2): reserved-prefix input guard.
# Caller-asserted agent_ids that begin with this prefix are rejected at the
# write boundary — REST validate, REST validate/batch, MCP validate_record,
# MCP validate_batch — so a spoofed identity never reaches the audit store
# in the first place. The complementary output-side suppression (v2.3.15)
# alone left a write-side gap: a caller could persist OpenDQV_SA_* events
# that the dashboards then *hide by design*, making the pollution invisible.
_RESERVED_AGENT_PREFIX = "OpenDQV_SA_"


def _reject_reserved_agent_id(v: Optional[str]) -> Optional[str]:
    """Reject agent_id values that claim the reserved OpenDQV_SA_* prefix."""
    if v and v.startswith(_RESERVED_AGENT_PREFIX):
        raise ValueError(
            f"agent_id '{v}' uses the reserved prefix '{_RESERVED_AGENT_PREFIX}'. "
            f"This prefix is reserved for OpenDQV-owned system traffic "
            f"(smoke probes, demos, MCP self-tests). Choose an agent_id that "
            f"identifies your service, AI agent, or team — e.g. "
            f"'salesforce-prod', 'claude-desktop-alice', 'data-platform-team'."
        )
    return v


# ── Validation request/response ──────────────────────────────────────

class ValidateRequest(BaseModel):
    """Single-record validation request."""
    record: dict = Field(..., description="The data record to validate")
    contract: str = Field(..., description="Contract name (e.g. 'customer')")
    version: str = Field("latest", description="Contract version or 'latest'")
    hash: Optional[str] = Field(None, description="Pin validation to a specific historical contract version by SHA-256 hash (entry_hash or content_hash from a prior response, or from list_versions). Takes precedence over `version` and `as_of`. Returns 404 if the hash is not in the contract's history.")
    context: Optional[str] = Field(None, description="Context override (e.g. 'kids_app', 'salesforce')")
    record_id: Optional[str] = Field(None, description="Caller's correlation ID for tracking")
    agent_id: Optional[str] = Field(None, description="Caller-asserted identity (AI agent name, service name, or team) — NOT authenticated. Use for self-labelling and session correlation. For trustable attribution, read `caller_principal` from the response — that is server-derived from the authenticated token and cannot be spoofed. Reserved prefix: agent_ids starting with 'OpenDQV_SA_' are reserved for OpenDQV-owned system traffic (smoke probes, demos, MCP self-tests) and are REJECTED at the write boundary with HTTP 422 INVALID_AGENT_ID.")
    dry_run: bool = Field(False, description="If true, validate without recording results in quality metrics. Use for testing and demos.")
    observe_only: bool = Field(False, description="If true, run in observation-only mode: log violations but do not block. Always returns HTTP 200.")
    # v2.3.20 reverses v2.3.17 Q12 (Sonnet's option iv). The opt-in flag
    # backfired in regulated-FS context: Persona B's outside review flagged
    # `engine_version: ""` as a P2 observability gap. Their framing
    # (SoX/DORA/MiFIR audit-trail attribution) outranks the MCP reference-
    # server minimalism argument that drove the default-off choice. Flag
    # removed; engine_version is now always emitted.

    @field_validator("agent_id")
    @classmethod
    def _no_reserved_prefix(cls, v: Optional[str]) -> Optional[str]:
        return _reject_reserved_agent_id(v)

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
    error_code: str = Field("", description="Stable machine-readable error code derived from rule type AND rule name, e.g. OPENDQV_REGEX_VALID_EMAIL. Two rules of the same type now produce different codes (was: shared OPENDQV_REGEX_001 in v2.3.5 and earlier — see CHANGELOG v2.3.6 for migration). Safe to use as a routing key in dead-letter queues and alerting rules.")
    suggested_fix: Optional[str] = Field(None, description="Concise actionable fix hint — use to self-correct and resubmit without a separate explain_error call")


class ValidateResponse(BaseModel):
    """Single-record validation response."""
    valid: bool = Field(..., description="True if no blocking errors (warnings allowed)")
    event_id: str = Field(..., description="Server-generated audit primary key (UUID v7, RFC 9562). Persisted with the audit row — use to correlate this response with the SQLite audit trail and the optional TRACE_LOG entry.")
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
        description="DEPRECATED v2.3.14, removed v2.4. Alias of entry_hash. Prefer entry_hash on new integrations.",
    )
    entry_hash: Optional[str] = Field(
        None,
        description="SHA-256 over the full hash domain (content + chain + node + timestamp). Uniquely identifies the audit chain entry — use to retrieve point-in-time audit evidence.",
    )
    content_hash: Optional[str] = Field(
        None,
        description="SHA-256 over the contract content fields only (excludes prev_hash, opendqv_node_id, updated_at). Two semantically identical contracts share content_hash even if recorded at different times or nodes.",
    )
    effective_rule_hash: Optional[str] = Field(
        None,
        description=(
            "SHA-256 over the resolved Rule set actually used by the validator "
            "on this call, after context overrides are applied. The other three "
            "hashes (entry_hash, content_hash, contract_hash) describe the static "
            "contract definition and are invariant to context — two calls with "
            "different contexts produce the same triplet even though they ran "
            "different rule sets. effective_rule_hash distinguishes those calls "
            "for audit replay. v2.3.17 F-J fix; named per Q4 because the field "
            "hashes resolved-effective-rules, not the context string."
        ),
    )
    owner_team: Optional[str] = None
    validated_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of validation — use for time-series correlation with quality metrics")
    latency_ms: Optional[float] = Field(None, description="Server-side validation latency in milliseconds")
    agent_id: Optional[str] = Field(None, description="Echo of caller's agent_id — for session and caller attribution")
    caller_principal: Optional[str] = Field(None, description="Server-derived from the authenticated token (JWT sub claim, or 'anonymous' in AUTH_MODE=open). Unlike `agent_id`, this cannot be spoofed by the caller — use as the trustable attribution key for audit and per-tenant SLA accounting.")
    mode: Optional[str] = Field("enforcement", description="Validation mode: 'enforcement' (default) or 'observation_only'. Always populated since v2.3.14.")
    would_have_failed: Optional[bool] = Field(False, description="True if the record would have been rejected under enforcement (equivalent to `not valid`). Always populated since v2.3.14 — was previously null in enforcement mode.")
    context_warning: Optional[str] = Field(
        None,
        description=(
            "Populated only when the request supplied a context that is NOT declared on this contract. "
            "The engine continues to use base rules (fail-open by design — contexts double as stats-tagging "
            "metadata, e.g. 'demo', 'ci', 'test'), but this field surfaces the divergence so a caller "
            "who intended an override context can see the typo. v2.3.17 F-D fix: makes the silent "
            "fail-open visible without breaking intentional metadata-tag use."
        ),
    )


# ── Batch validation request/response ────────────────────────────────

class BatchValidateRequest(BaseModel):
    """Batch validation request — for high-throughput use cases."""
    records: list[dict] = Field(..., description="List of data records to validate")
    contract: str = Field(..., description="Contract name")
    version: str = Field("latest", description="Contract version or 'latest'")
    hash: Optional[str] = Field(None, description="Pin batch to a specific historical contract version by SHA-256 hash. Takes precedence over `version` and `as_of`. Returns 404 if the hash is not in the contract's history.")
    context: Optional[str] = Field(None, description="Context override")
    agent_id: Optional[str] = Field(None, description="Caller-asserted identity (AI agent name, service name, or team) — NOT authenticated. Use for self-labelling and session correlation. For trustable attribution, read `caller_principal` from the response — that is server-derived from the authenticated token and cannot be spoofed. Reserved prefix: agent_ids starting with 'OpenDQV_SA_' are reserved for OpenDQV-owned system traffic (smoke probes, demos, MCP self-tests) and are REJECTED at the write boundary with HTTP 422 INVALID_AGENT_ID.")
    dry_run: bool = Field(False, description="If true, validate without recording results in quality metrics. Use for testing and demos.")
    observe_only: bool = Field(False, description="If true, run in observation-only mode: log violations but do not block. Always returns HTTP 200.")
    # v2.3.20 reverses v2.3.17 Q12 — see ValidateRequest above.

    @field_validator("agent_id")
    @classmethod
    def _no_reserved_prefix(cls, v: Optional[str]) -> Optional[str]:
        return _reject_reserved_agent_id(v)

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
    event_id: str = Field(..., description="Server-generated audit primary key for this record (UUID v7, RFC 9562). Distinct from the batch-level event_id — every record is independently addressable in the audit trail.")
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
    event_id: str = Field(..., description="Server-generated audit primary key for the batch call (UUID v7, RFC 9562). Persisted with the batch's quality_stats row.")
    summary: BatchSummary = Field(..., description="Aggregate statistics across all submitted records")
    results: list[BatchResultItem] = Field(..., description="Per-record validation results in submission order")
    contract: str = Field(..., description="Contract that was evaluated")
    version: str = Field(..., description="Contract version that was evaluated")
    owner: str = Field("", description="Contract owner — for routing disputes and on-call escalation")
    engine_version: str = Field("", description="OpenDQV engine version — for regulatory audit trails")
    contract_hash: Optional[str] = Field(None, description="DEPRECATED v2.3.14, removed v2.4. Alias of entry_hash. Prefer entry_hash on new integrations — see entry_hash and content_hash for the canonical pair.")
    entry_hash: Optional[str] = Field(None, description="SHA-256 over the full hash domain — uniquely identifies the audit chain entry for this batch's contract version.")
    content_hash: Optional[str] = Field(None, description="SHA-256 over content fields only — stable across re-recordings of the same contract.")
    effective_rule_hash: Optional[str] = Field(
        None,
        description="SHA-256 over the resolved Rule set actually used by the validator on this batch, after context overrides. v2.3.17 F-J — see ValidateResponse.effective_rule_hash for full rationale.",
    )
    validated_at: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of batch validation — use for time-series correlation with quality metrics")
    latency_ms: Optional[float] = Field(None, description="Server-side batch validation latency in milliseconds (total wall-clock time for the batch)")
    agent_id: Optional[str] = Field(None, description="Echo of caller's agent_id — for session and caller attribution")
    caller_principal: Optional[str] = Field(None, description="Server-derived from the authenticated token (JWT sub claim, or 'anonymous' in AUTH_MODE=open). Unlike `agent_id`, this cannot be spoofed by the caller — use as the trustable attribution key for audit and per-tenant SLA accounting.")
    mode: Optional[str] = Field("enforcement", description="Validation mode: 'enforcement' (default) or 'observation_only'. Always populated since v2.3.14.")
    would_have_failed: Optional[bool] = Field(False, description="True if any record in the batch would have been rejected under enforcement (equivalent to summary.failed > 0). Always populated since v2.3.14 — was previously null in enforcement mode.")


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
    pattern: Optional[str] = Field(None, description="Regex pattern (regex rule)")
    min: Optional[float] = Field(None, description="Numeric lower bound (min, range rule)")
    max: Optional[float] = Field(None, description="Numeric upper bound (max, range rule)")
    min_length: Optional[int] = Field(None, description="String length lower bound (min_length rule)")
    max_length: Optional[int] = Field(None, description="String length upper bound (max_length rule)")
    format: Optional[str] = Field(None, description="Format pattern (date_format rule), e.g. YYYY-MM-DD")
    compare_to: Optional[str] = Field(None, description="Other field name or temporal sentinel (compare rule)")
    compare_op: Optional[str] = Field(None, description="Comparison operator: gt, gte, lt, lte, eq (compare rule)")
    min_age: Optional[int] = Field(None, description="Minimum age in years (age_match rule)")
    max_age: Optional[int] = Field(None, description="Maximum age in years (age_match rule)")
    allowed_values: Optional[list[str]] = Field(None, description="Inline allowed values (allowed_values / enum rule)")
    lookup_file: Optional[str] = Field(None, description="Reference file path for lookup rule")
    checksum_algorithm: Optional[str] = Field(None, description="Algorithm name for checksum rule (e.g. mod10_gs1, iban_mod97)")
    negate: Optional[bool] = Field(None, description="True when the regex must NOT match")


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
    contract_hash: Optional[str] = Field(None, description="Alias of entry_hash, retained for backward compatibility.")
    entry_hash: Optional[str] = Field(None, description="SHA-256 over the full hash domain for this contract version's history entry.")
    content_hash: Optional[str] = Field(None, description="SHA-256 over content fields only — stable across re-recordings.")


# ── Quality trend models ──────────────────────────────────────────────

class QualityTrendPoint(BaseModel):
    """Aggregated quality statistics for one bucket of the trend window.

    The bucket dimension is governed by the parent response's `by` field:
      - by="date":    bucket carries `date` (YYYY-MM-DD)
      - by=agent|context|rule: bucket carries `key` (the dimension value)
    Both date and key are optional for forward compatibility.
    """
    date: Optional[str] = Field(None, description="Calendar date (UTC), YYYY-MM-DD; populated when by=date")
    key: Optional[str] = Field(None, description="Dimension value when by=agent|context|rule")
    total_records: int = Field(0, description="Total records in this bucket")
    passed: int = 0
    failed: int = 0
    pass_rate_pct: Optional[float] = Field(
        None,
        description=(
            "Pass rate as a percentage (0.0–100.0). NULL when by=rule because "
            "pass-rate is not meaningful per-rule (a rule has violations, not "
            "'records that passed it' — passes are not tracked per-rule). "
            "v2.3.18 Q3: single canonical name; the bare `pass_rate` and "
            "`pass_rate_ratio` fields are removed in this release."
        ),
    )
    violation_count: Optional[int] = Field(
        None,
        description="Rule violation count — populated only when by=rule.",
    )
    top_failing_rules: dict = Field(
        default_factory=dict,
        description=(
            "DEPRECATED v2.3.13, removed v2.4. Top failing rules as a dict "
            "{rule_name: fail_count} — JSON dicts have no guaranteed ordering "
            "so consumers cannot read this as a ranking. Use "
            "top_failing_rules_ranked instead."
        ),
    )
    top_failing_rules_ranked: list = Field(
        default_factory=list,
        description="Top failing rules for this day, sorted desc: [{rule, count}].",
    )


class QualityTrendResponse(BaseModel):
    """Quality trend response for a contract over N days."""
    contract: str
    days: int
    context: Optional[str] = None
    by: str = Field("date", description="Grouping dimension: date | agent | context | rule")
    points: list[QualityTrendPoint]
    asset_id: Optional[str] = None  # catalog asset identifier — enables quality signal self-discovery
    # CRT170/J6: data_confidence band parity with get_quality_metrics
    data_confidence: Optional[str] = Field(
        None,
        description="Confidence band based on total validations: no_data | low | medium | high",
    )
    confidence_note: str = Field(
        "",
        description=(
            "Human-readable caveat when confidence is no_data or low. "
            "Always present as a string — empty when no caveat applies. "
            "v2.3.14 / CRT173 finding 23."
        ),
    )
    total_validations: Optional[int] = Field(
        None,
        description="Total validations underpinning this trend (sum across days)",
    )


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


class ContractVersionSummary(BaseModel):
    """Lean version metadata — exploration without payload weight."""
    version: str
    status: str
    entry_hash: Optional[str] = None
    content_hash: Optional[str] = None
    created_at: Optional[str] = None
    owner: Optional[str] = None
    owner_team: Optional[str] = None
    approved_by: Optional[str] = None
    proposed_by: Optional[str] = None


class ContractVersionsResponse(BaseModel):
    """List of versions for a contract — metadata only, no rule bodies."""
    contract: str
    versions: list[ContractVersionSummary] = Field(default_factory=list)


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
    from_hash: Optional[str] = None
    to_hash: Optional[str] = None
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
    lookup_source: Optional[str] = Field(
        None,
        description=(
            "Logical name of the reference list a `lookup` rule resolves against (e.g. "
            "`universal_currency`, `iso_country_alpha2`). Audit-friendly identifier "
            "that does not expose server filesystem layout. Present only on `lookup` rules."
        ),
    )
    constraint: dict = Field(default_factory=dict, description="The raw constraint values from the contract (e.g. {'min': 0.01})")


# ── DuckDB analytics models ───────────────────────────────────────────

class AnalyticsSummaryItem(BaseModel):
    """Pass-rate summary for one contract over the analytics window."""
    contract: str = Field(..., description="Contract name")
    total_records: int = Field(..., description="Total records validated in the window")
    passed: int = Field(..., description="Records that passed")
    failed: int = Field(..., description="Records that failed")
    pass_rate_pct: Optional[float] = Field(None, description="Pass rate as a percentage (0.0–100.0). v2.3.22 Cluster F: returns null when total_records == 0 (signal of no data, not 100% perfection).")


class AnalyticsSummaryResponse(BaseModel):
    """Cross-contract pass rate summary backed by DuckDB OLAP over SQLite quality data."""
    days: int = Field(..., description="Analytics window in days")
    contracts: list[AnalyticsSummaryItem] = Field(
        ..., description="Per-contract summary, sorted by pass_rate_pct ascending (worst first)"
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
    # CRT170/J6: data_confidence band parity with get_quality_metrics
    data_confidence: Optional[str] = Field(
        None,
        description="Confidence band based on total validations: no_data | low | medium | high",
    )
    confidence_note: str = Field(
        "",
        description=(
            "Human-readable caveat when confidence is no_data or low. "
            "Always present as a string — empty when no caveat applies. "
            "v2.3.14 / CRT173 finding 23."
        ),
    )
    total_validations: Optional[int] = Field(
        None,
        description="Total validations within the window underpinning this velocity series",
    )


# ── Observation-only analytics models ──────────────────────────────────

class ObservationSummaryResponse(BaseModel):
    """Cross-contract summary of observation-only validation runs."""
    days: int = Field(..., description="Analytics window in days")
    contract: Optional[str] = Field(None, description="Contract filter (None = all contracts)")
    total_observation_records: int = Field(..., description="Total records validated in observation mode")
    would_have_failed_count: int = Field(..., description="Records that would have been rejected under enforcement")
    would_have_passed_count: int = Field(..., description="Records that would have passed under enforcement")
    enforcement_readiness_pct: float = Field(..., description="Percentage of records that would pass enforcement (0.0–100.0)")
    by_contract: list[dict] = Field(default_factory=list, description="Per-contract breakdown")


class ObservationTrendPoint(BaseModel):
    """Daily observation-mode statistics for one contract."""
    date: str = Field(..., description="Calendar date (UTC), YYYY-MM-DD")
    total: int = Field(..., description="Total records validated in observation mode")
    would_have_failed: int = Field(..., description="Records that would have been rejected")
    would_have_passed: int = Field(..., description="Records that would have passed")


class ObservationFieldFailure(BaseModel):
    """Failure count for one rule/field in observation mode."""
    rule: str = Field(..., description="Rule name")
    field: str = Field(..., description="Field name (derived from rule)")
    count: int = Field(..., description="Total failures for this rule in the window")


# ── CRT172/K1+K2 audit event models ───────────────────────────────────


class AuditEventListItem(BaseModel):
    """One row in the cursor-paginated audit event list (CRT172 / K2)."""
    event_id: str = Field(..., description="UUID v7 returned on the original /validate response")
    contract: str = Field(..., description="Contract evaluated")
    contract_version: str = Field(..., description="Contract version evaluated")
    recorded_at: str = Field(..., description="ISO 8601 UTC timestamp of the validation call")
    total_records: int = Field(..., description="Records in this call (1 for /validate, N for /validate/batch)")
    passed: int = Field(..., description="Records that passed")
    failed: int = Field(..., description="Records that failed")
    agent_id: str = Field("", description="Caller-asserted identity — NOT authenticated. Use caller_principal for trustable attribution.")
    caller_principal: str = Field("", description="Server-derived from the authenticated token (JWT sub, or 'anonymous' in open mode). Cannot be spoofed.")
    mode: str = Field("enforcement", description="'enforcement' or 'observation_only'")


class AuditEventDetail(AuditEventListItem):
    """Single audit event with full detail (CRT172 / K1)."""
    context: Optional[str] = Field(None, description="Context override active for this call (None = default)")
    pass_rate_pct: Optional[float] = Field(None, description="passed / total_records as a percentage (0.0–100.0). v2.3.22 Cluster F: null when total_records == 0.")
    rule_failure_counts: dict = Field(
        default_factory=dict,
        description="Per-rule failure counts for this call: {rule_name: count}",
    )
    # v2.3.22 Cluster C: F-J persistence — hash triplet on the audit row.
    # Empty string is the sentinel for rows recorded before this release.
    # Callers must check truthiness rather than relying on null/None.
    effective_rule_hash: str = Field(
        "",
        description=(
            "SHA-256 over the rules actually applied to this call (after "
            "context overrides). Same value the original /validate response "
            "emitted. Empty string when the row was recorded before "
            "v2.3.22 Cluster C added persistence."
        ),
    )
    entry_hash: str = Field(
        "",
        description=(
            "SHA-256 over the contract version's full history entry. "
            "Empty when the row predates v2.3.22 Cluster C."
        ),
    )
    content_hash: str = Field(
        "",
        description=(
            "SHA-256 over content fields only — stable across re-recordings. "
            "Empty when the row predates v2.3.22 Cluster C."
        ),
    )


class AuditEventListResponse(BaseModel):
    """Cursor-paginated audit event listing (CRT172 / K2)."""
    events: list[AuditEventListItem] = Field(..., description="Audit events ordered by recorded_at DESC, id DESC")
    has_more: bool = Field(..., description="True if more events exist beyond this page — pass next_cursor to retrieve them")
    next_cursor: Optional[str] = Field(
        None,
        description="Opaque cursor token to retrieve the next page. Pass as ?cursor=<token> on the next call. Null when has_more=false.",
    )
    effective_since: str = Field(
        ...,
        description="The `since` value actually applied to this query (ISO 8601 UTC). Echoed so callers can detect silent default-window truncation when `since` is omitted.",
    )
    limit: int = Field(..., description="Max events returned per page")
    # v2.3.23 P0-1 (Sonnet's pre-impl directive a348734a7798db94b):
    # additive auth_mode field gives a consuming system machine-readable
    # evidence of the trust model in effect at retrieval time. Always
    # present; values are "token" or "open". In "open" mode, all callers
    # are admin per dev-default — consumers in regulated contexts should
    # refuse to render this response or require a re-fetch in token
    # mode before relying on the data.
    auth_mode: str = Field(
        "token",
        description=(
            "Auth mode the engine is running in: 'token' (admin/auditor "
            "role required to reach this endpoint) or 'open' (local "
            "development only, every caller granted admin per "
            "AUTH_MODE=open). Regulated deployments should refuse to "
            "trust an audit-event response with auth_mode='open'."
        ),
    )
