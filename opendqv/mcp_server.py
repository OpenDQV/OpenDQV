"""
OpenDQV MCP Server

Exposes OpenDQV as a Model Context Protocol (MCP) server so that Claude Desktop,
Cursor, and any MCP-compatible agent framework can validate data records natively.

Tools (read):
  validate_record        — validate a single record against a named contract
  validate_batch         — validate up to 10,000 records in one call
  list_contracts         — discover available contracts
  get_contract           — get full contract detail including all rules
  explain_error          — get plain-English remediation guidance for a rule failure

Tools (write):
  create_contract_draft  — create a DRAFT contract for a novel domain (MCP_ prefix required)
                           DRAFT contracts are testable immediately but require human approval
                           via submit_for_review → approve_contract before becoming ACTIVE.

Usage:
  python mcp_server.py

Install MCP extra:
  pip install opendqv[mcp]      # installs the mcp package

Register in Claude Desktop (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "OpenDQV": {
        "command": "python",
        "args": ["/path/to/OpenDQV/mcp_server.py"],
        "env": {
          "OPENDQV_AGENT_IDENTITY": "your.email@example.com"
        }
      }
    }
  }

Register in Cursor (Settings → MCP → Add Server):
  Name: OpenDQV
  Command: python /path/to/OpenDQV/mcp_server.py

Note: The path to mcp_server.py must be an absolute path and is machine-specific.
Update it when cloning the repo on a new machine. In Cursor you can use the
${workspaceFolder} variable if your project root is the OpenDQV repo:
  Command: python ${workspaceFolder}/mcp_server.py

Attribution (required for write tools):
  Set the OPENDQV_AGENT_IDENTITY environment variable to your email address or
  username before using create_contract_draft. This value is recorded in the
  contract audit trail as the proposing identity and cannot be changed after
  creation. Write tools are blocked if both created_by and OPENDQV_AGENT_IDENTITY
  are unset/empty.

  export OPENDQV_AGENT_IDENTITY="your.email@example.com"

  In Claude Desktop, add the env key to the mcpServers config (see above).
  In Cursor, add it to your shell profile so it is inherited by the MCP process.

Rate limiting (ACT-045-06):
  create_contract_draft enforces a per-identity cap of 10 DRAFT creations per
  hour using an in-memory sliding-window counter: {identity: [timestamp, ...]}.
  Stale entries outside the 1-hour window are evicted on each check. Single-
  process only; for multi-process deployments, move the counter to the SQLite
  database.
"""

import json
import os
import sys
import time as _time
from pathlib import Path

# Add project root to path so core/ and config are importable
sys.path.insert(0, str(Path(__file__).parent))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
except ImportError:
    print(
        "ERROR: The 'mcp' package is required. Install it with:\n"
        "  pip install opendqv[mcp]\n"
        "or:\n"
        "  pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

import opendqv.config as config
from opendqv.core.contracts import ContractRegistry
from opendqv.core.validator import validate_record as _validate_record, validate_batch as _validate_batch
from opendqv.core.explainer import explain_rule
from opendqv.core.rule_parser import ContractStatus, Rule as _Rule
from opendqv.monitoring import stats as _stats
from opendqv.core.quality_stats import QualityStats as _QualityStats, quality_confidence as _quality_confidence
from opendqv.core.quality_analytics import QualityAnalytics as _QualityAnalytics


_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "unknown": -1}


def _severity_map(contract_name: str) -> dict:
    """Build {rule_name: worst-case-severity} from the live registry.

    v2.3.23 round-4 P1-D (Sonnet a410fe4a545b865bc): walks both base
    rules and all context overrides, returning the WORST-CASE severity
    per rule. A rule that's `warning` in default but `error` under any
    context override surfaces as `error` so an ops dashboard escalates
    correctly. Reviewer's exact case: `revenue_ceiling` is warning in
    default, error in billing context — pre-fix the dashboard showed
    warning, masking live errors. Over-classifying (showing error when
    a context demoted to warning) is the survivable false positive;
    under-classifying is the defect.

    Used to tag entries on top_failing_rules / top_failing_rules_ranked
    so consumers can rank a rule's operational priority correctly — a
    warning failing 100x must not outrank an error failing 50x in a
    dashboard. Returns empty dict when the contract is missing (e.g.
    removed) or has no rules — callers default to "unknown" in that case.
    """
    if not contract_name:
        return {}
    try:
        contract = _registry.get(contract_name)
    except Exception:
        return {}
    if not contract or not getattr(contract, "rules", None):
        return {}
    # Base severity for every rule, then promote based on context overrides.
    sev_map: dict = {
        r.name: (r.cached_severity_value or "error") for r in contract.rules
    }
    base_rule_names = set(sev_map)
    # Build field → [rules-on-that-field] map for branch-2 (field-name)
    # override resolution. core/contracts.py override resolution order:
    #   1. rule-name match → modify that rule
    #   2. field-name match → modify every rule on that field
    #   3. neither → mint a synthetic ctx_<context>_<key> rule
    # Branches 1 + 2 can mutate severity at runtime; we walk both here.
    field_to_rules: dict = {}
    for r in contract.rules:
        field_to_rules.setdefault(r.field, []).append(r.name)
    for _ctx_name, overrides in (contract.contexts or {}).items():
        for key, override in (overrides or {}).items():
            sev = override.get("severity") if isinstance(override, dict) else None
            if not sev:
                continue
            sev_rank = _SEVERITY_RANK.get(sev, -1)
            if key in base_rule_names:
                # Branch 1: rule-name match — mutate that single rule.
                if sev_rank > _SEVERITY_RANK.get(sev_map.get(key, "error"), -1):
                    sev_map[key] = sev
            elif key in field_to_rules:
                # Branch 2: field-name match — mutate every rule on that field.
                for rname in field_to_rules[key]:
                    if sev_rank > _SEVERITY_RANK.get(sev_map.get(rname, "error"), -1):
                        sev_map[rname] = sev
            # Branch 3 mints `ctx_<context>_<key>` synthetic rules. Those
            # don't appear in base contract.rules so they're absent from
            # sev_map. The normalize_trend_rule_names path strips these
            # to base rule names where possible; truly synthetic rules
            # (no base equivalent) carry the override's severity at write
            # time and surface as severity="unknown" in this map. That's
            # acceptable — synthetic rules are by definition new and the
            # registry walk cannot distinguish them from arbitrary names.
    return sev_map


def _error_envelope(
    error_code: str,
    kind: str,
    detail: str,
    status: int = 400,
    remediation: str = "",
) -> str:
    """Structured MCP error envelope (CRT173 finding 24).

    Replaces the historical "Error: {exc}" stringification and the loose
    {"error": "..."} dict shapes. The envelope is the single shape callers
    can branch on:
      error_code   — stable machine-readable identifier (UPPER_SNAKE)
      kind         — coarse category: validation | not_found | bad_request | rate_limited | internal
      status       — HTTP-equivalent status code (parity with the REST surface)
      detail       — human-readable specific message
      remediation  — actionable hint, "" when none applies
    """
    return json.dumps({
        "error": {
            "error_code": error_code,
            "kind": kind,
            "status": status,
            "detail": detail,
            "remediation": remediation,
        },
    })

# ── Governance tips ───────────────────────────────────────────────────
_GOVERNANCE_TIPS: dict[str, str] = {
    "not_empty": (
        "Empty required fields cause silent NULL propagation into analytics — "
        "catching them at ingestion is 10× cheaper than tracing them downstream."
    ),
    "regex": (
        "Format rules stop malformed data from corrupting partner APIs and export pipelines."
    ),
    "range": (
        "Out-of-range values often signal upstream bugs; catching them early avoids "
        "expensive data recalls."
    ),
    "date_format": (
        "Inconsistent date formats break time-series queries and cause invisible data loss "
        "in regulatory reports."
    ),
    "enum": (
        "Constraining categories at source keeps aggregations reliable across all consumers."
    ),
    "unique": (
        "Enforcing uniqueness here is 100× cheaper than deduplicating a data warehouse."
    ),
    "compare": (
        "Cross-field comparisons catch logical contradictions that field-level rules miss."
    ),
    "required_if": (
        "Conditional requirements encode business logic directly into the contract — "
        "preventing silent omissions that downstream consumers assume are impossible."
    ),
    "lookup": (
        "Referential integrity checks at ingestion prevent orphaned records from "
        "accumulating silently in production datasets."
    ),
    "default": (
        "Data quality rules protect every system downstream — catching errors at ingestion "
        "is always the cheapest fix."
    ),
}

_DRAFT_NOTICE = (
    "This contract is in DRAFT. Validate freely here, but activate it before "
    "relying on results in production."
)


def _pick_governance_tip(rules: list, errors: list) -> str:
    """Return an educational governance tip based on the first failing rule type,
    falling back to the first rule in the contract, then to the 'default' key."""
    # Try to match the type of the first failing rule
    if errors:
        first_error_rule = errors[0].get("rule", "")
        for rule in rules:
            if rule.name == first_error_rule:
                tip = _GOVERNANCE_TIPS.get(rule.type)
                if tip:
                    return tip
    # Fall back to the first rule in the contract
    if rules:
        tip = _GOVERNANCE_TIPS.get(rules[0].type)
        if tip:
            return tip
    return _GOVERNANCE_TIPS["default"]


# ACT-045-06: Per-identity sliding-window rate limiter for draft creation.
# In-memory dict: {identity: [epoch_timestamp, ...]}. Single-process only.
_draft_creation_log: dict[str, list[float]] = {}
_DRAFT_RATE_LIMIT = 10        # max creations
_DRAFT_RATE_WINDOW = 3600.0   # per hour (seconds)

# ── Contract registry (local mode) ────────────────────────────────────
_registry = ContractRegistry(config.CONTRACTS_DIR)
_quality_stats = _QualityStats(config.DB_PATH)
_quality_analytics = _QualityAnalytics(config.DB_PATH)

# ── Remote mode (enterprise) ───────────────────────────────────────────
# When OPENDQV_MCP_API_URL is set, all tool calls are proxied to the central
# OpenDQV API over HTTP. Validation events become visible in the monitoring UI
# and agents always see the live contract version — not a stale local copy.
# When unset (default), local mode is used: no network dependency.
_remote_client = None  # httpx.Client | None

if config.MCP_API_URL:
    try:
        import httpx as _httpx
        _remote_headers = {}
        if config.MCP_TOKEN:
            _remote_headers["Authorization"] = f"Bearer {config.MCP_TOKEN}"
        _remote_client = _httpx.Client(
            base_url=config.MCP_API_URL.rstrip("/"),
            headers=_remote_headers,
            timeout=30.0,
        )
        print(
            f"OpenDQV MCP: remote mode active → {config.MCP_API_URL}",
            file=sys.stderr,
        )
    except ImportError:
        print(
            "WARNING: OPENDQV_MCP_API_URL is set but 'httpx' is not installed.\n"
            "Install it with: pip install httpx\n"
            "Falling back to local mode.",
            file=sys.stderr,
        )

# ── MCP server setup ──────────────────────────────────────────────────
server = Server(
    "OpenDQV",
    version=config.ENGINE_VERSION,
    icons=[
        types.Icon(
            src="https://raw.githubusercontent.com/OpenDQV/OpenDQV/main/docs/assets/opendqv-favicon-128.png",
            mimeType="image/png",
            sizes=["128x128"],
        )
    ],
)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="validate_record",
            description=(
                "Validate a single data record against a named contract. "
                "Returns {valid: bool, errors: [...]}. "
                "Call this before writing any record to a database or external API. "
                "If valid is false, call explain_error for each error in the errors list to get "
                "plain-English remediation guidance and valid/invalid examples before attempting "
                "to fix the record. Do not write the record until valid is true. "
                "Note: the response field caller_principal is server-derived from the "
                "authenticated token (or 'anonymous' when AUTH_MODE=open). It is "
                "trustable; agent_id is caller-asserted and not."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name (e.g. 'customer', 'banking_transaction'). Use list_contracts to discover available names.",
                    },
                    "record": {
                        "type": "object",
                        "description": "The data record to validate as a JSON object.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional per-system context override (e.g. 'salesforce', 'kids_app'). Omit for default rules.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Your agent name or service identity — echoed in the response for attribution and session correlation.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, validate without recording results in quality metrics. Use for testing.",
                        "default": False,
                    },
                    "hash": {
                        "type": "string",
                        "description": "Optional content_hash from list_versions to pin validation to a specific historical contract version. Returns 404 if no matching history entry.",
                    },
                    "record_id": {
                        "type": "string",
                        "description": "v2.3.17 F-Q: optional caller correlation ID echoed in the response and recorded in the audit trail. Use to correlate this validate call with your upstream system's record identifier.",
                    },
                },
                "required": ["contract", "record"],
            },
        ),
        types.Tool(
            name="validate_batch",
            description=(
                "Validate up to 10,000 records in a single call. "
                "Returns per-record results and aggregate statistics. "
                "Use for bulk data imports or pipeline validation. "
                "Has a fixed setup cost (~70ms) — for batches under "
                "~70 records, individual validate_record calls are faster. "
                "Empty batches are rejected with a 400 error."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name to validate all records against.",
                    },
                    "records": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of data records to validate. Maximum 10,000 per call.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional per-system context override.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Your agent name or service identity — echoed in the response for attribution.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, validate without recording results in quality metrics. Use for testing.",
                        "default": False,
                    },
                    "hash": {
                        "type": "string",
                        "description": "Optional content_hash from list_versions to pin all records to a specific historical contract version.",
                    },
                },
                "required": ["contract", "records"],
            },
        ),
        types.Tool(
            name="list_contracts",
            description=(
                "List all available validation contracts with their names, statuses, and rule counts. "
                "Call this first to discover which contract applies to your data. "
                "Default response includes contracts in active, draft, and "
                "review status (archived ones are excluded). To include "
                "archived entries too, pass include_all=true. "
                "Only 'active' contracts can be used for validation."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_contract",
            description=(
                "Get full contract details including all field rules, valid value constraints, and owner. "
                "Use this to understand what a contract requires before validating, "
                "or to generate type-safe data structures that match the contract. "
                "Pass `hash` (the contract_hash from a prior validate response) to retrieve "
                "the exact historical version that produced that hash — for point-in-time audit retrieval. "
                "Pass `context` (e.g. 'salesforce', 'kids_app') to return the effective rule set "
                "with that context's overrides already merged in — what validate_record(context=...) "
                "would actually run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Contract name.",
                    },
                    "version": {
                        "type": "string",
                        "description": "Contract version or 'latest' (default).",
                        "default": "latest",
                    },
                    "hash": {
                        "type": "string",
                        "description": "SHA-256 contract_hash from a prior validate response. Takes precedence over `version`.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional context (e.g. 'salesforce', 'kids_app'). When set, rules are returned with that context's overrides resolved.",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="list_versions",
            description=(
                "List the version history for a contract — metadata only, no rule bodies. "
                "Returns version, status, entry_hash, content_hash, created_at, owner. "
                "Use this when you need to drive a version picker, audit a "
                "lineage, or pin a downstream call to a specific historical hash "
                "via validate_record(hash=...). Lighter than get_contract for "
                "every version when only the listing is needed. "
                "Each entry also carries `is_collision: bool` — true when "
                "this entry shares a `version` string with at least one other "
                "entry that has a different content_hash. Cite entry_hash, "
                "not version, when more than one is_collision: true entry "
                "exists for the same SemVer label (write-time uniqueness "
                "enforcement is a v2.4 architectural item)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Contract name.",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="get_contract_jsonschema",
            description=(
                "Emit a JSON Schema (draft 2020-12) document for a contract. "
                "Use this to bootstrap a producer's structural validation, generate "
                "API request/response shapes, or feed a typed code generator. "
                "Cross-field rules (compare, unique, required_if, lookup) cannot "
                "be expressed in plain JSON Schema and appear in the response under "
                "`x-opendqv-unmapped` — those rules are still enforced by the "
                "OpenDQV runtime, but JSON Schema callers must rely on validate_record "
                "for full semantic coverage."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Contract name."},
                    "context": {
                        "type": "string",
                        "description": "Optional context to apply (e.g. 'salesforce', 'kids_app').",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="compare_contracts",
            description=(
                "Compare two historical snapshots of the same contract. "
                "Workflow: call list_versions first to retrieve the "
                "available entry_hash values for the contract, then pass "
                "any two of those hashes here as hash_a and hash_b. "
                "Returns rules_added, rules_removed, rules_changed, and "
                "metadata_changed between the two snapshots. Use this to "
                "inspect what changed between two pinned hashes — for "
                "audit, change review, or drift analysis. Hash pair is "
                "more precise than version pair because a single version "
                "string may produce multiple snapshots (see is_collision "
                "on list_versions output)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Contract name."},
                    "hash_a": {"type": "string", "description": "First snapshot hash (entry_hash or content_hash)."},
                    "hash_b": {"type": "string", "description": "Second snapshot hash (entry_hash or content_hash)."},
                },
                "required": ["name", "hash_a", "hash_b"],
            },
        ),
        types.Tool(
            name="explain_error",
            description=(
                "Get a plain-English explanation of why a field failed a validation rule, "
                "including valid and invalid examples. "
                "Call this when validate_record returns errors and you need to understand how to fix the record. "
                "The explanation is designed for LLM agents and includes concrete remediation guidance."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name (from the validate_record response).",
                    },
                    "field": {
                        "type": "string",
                        "description": "The field that failed (from the error object's 'field' key).",
                    },
                    "rule": {
                        "type": "string",
                        "description": "The rule name that failed (from the error object's 'rule' key).",
                    },
                },
                "required": ["contract", "field", "rule"],
            },
        ),
        types.Tool(
            name="get_quality_metrics",
            description=(
                "Return aggregate rejection metrics for one or all contracts. "
                "Includes pass_rate_pct, failed count, top_failing_rules, and an optional "
                "catalog_hint field for chaining to a data-catalog MCP server. "
                "catalog_hint is `<prefix><contract>` where the prefix is configured via "
                "OPENDQV_CATALOG_URI_PREFIX (default `marmot:assets/`; use e.g. "
                "`datahub:dataset/`, `unitycatalog://`, or empty to omit). "
                "Each top_failing_rules entry carries `severity` (error|warning|info|unknown) "
                "so consumers can rank operational priority correctly — a warning failing "
                "100x must not outrank an error failing 50x. "
                "Counter semantics: total_validations / total_pass / total_fail are RECORD "
                "counts. total_error_violations / total_warning_violations are RULE-VIOLATION "
                "sums (a single failing record with N broken rules contributes N). The legacy "
                "keys total_errors / total_warnings are aliases for the *_violations keys and "
                "will be removed in v2.4 — prefer the *_violations names. "
                "Call this to assess data quality health before deciding whether to route a "
                "pipeline or alert an owner. "
                "Auth/trust: in AUTH_MODE=token, any authenticated caller "
                "(validator/reader minimum) can read metrics. In AUTH_MODE=open "
                "(local development only, boot warning issued), anonymous "
                "callers can read. This surface assumes a single-tenant trust "
                "boundary — operational metrics are intentionally readable by "
                "every internal user. Multi-tenant deployments requiring "
                "cross-tenant isolation must wait for v2.4 per-contract "
                "scoping. Do not expose this endpoint to untrusted networks. "
                "data_confidence band thresholds: no_data (0), low (1-9), "
                "medium (10-99), high (>=100) underlying validations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name. Omit to get metrics for all contracts.",
                    },
                    "window_hours": {
                        "type": "integer",
                        "default": 24,
                        "description": "Filters the look-back window. In-memory events used when available; SQLite used as fallback after restarts.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Optional: filter metrics to a specific source/agent (e.g. 'broadsign-prod'). Omit to see all sources combined.",
                    },
                    "include_system": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, include OpenDQV system agents (agent_ids prefixed 'OpenDQV_SA_' — smoke probes, demos, MCP self-tests) in the response. Default false hides them from tenant-facing metrics so customer-visible views stay clean of dev/test traffic.",
                    },
                },
            },
        ),
        types.Tool(
            name="list_agents",
            description=(
                "List the agents (source systems) that emitted validation traffic in the "
                "window. Returns [{agent_id, total_validations, total_pass, total_fail, "
                "pass_rate_pct, last_seen, is_system_agent}], sorted by traffic volume desc. "
                "Call this BEFORE filtering get_quality_metrics or get_quality_trend by "
                "agent_id — it is the only way to discover which agent_id values are "
                "actually present, without guessing. OpenDQV system agents (OpenDQV_SA_*) "
                "are suppressed by default; pass include_system=true to surface them. "
                "Auth/trust: same as get_quality_metrics. In AUTH_MODE=token, "
                "any authenticated caller can read; in AUTH_MODE=open, "
                "anonymous can read (dev-only, boot warning issued). Single-"
                "tenant trust boundary — multi-tenant per-contract scoping is "
                "v2.4. agent_id values are integration topology and should "
                "not be exposed to untrusted networks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_hours": {
                        "type": "integer",
                        "default": 24,
                        "description": "Look-back window in hours (1–8760). Default 24.",
                    },
                    "include_system": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, include OpenDQV system agents (OpenDQV_SA_* prefix). Default false suppresses them from customer-facing views.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_rule_velocity",
            description=(
                "Return time-series failure counts per rule for a single contract — shows whether "
                "failures are accelerating or decelerating. Use this when pass_rate_pct is degrading "
                "to diagnose whether it's a sudden spike (fix the upstream source now) or a slow "
                "drip (investigate root cause). Returns the top 5 rules by total failures, bucketed "
                "by bucket_minutes intervals. "
                "Response carries data_confidence with bands: no_data (0), "
                "low (1-9), medium (10-99), high (>=100) underlying validations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name (required).",
                    },
                    "window_hours": {
                        "type": "integer",
                        "default": 24,
                        "description": "Look-back window in hours (1–168). Default 24.",
                    },
                    "bucket_minutes": {
                        "type": "integer",
                        "default": 5,
                        "description": "Bucket width in minutes (1–60). Default 5.",
                    },
                },
                "required": ["contract"],
            },
        ),
        types.Tool(
            name="get_quality_trend",
            description=(
                "Return daily pass-rate trend for a single contract over the last N days. "
                "Use this when pass_rate_pct in get_quality_metrics is degrading — it shows whether "
                "quality is declining, recovering, or stable, and which rules are driving the change. "
                "Returns one data point per calendar day with total_records, passed, failed, pass_rate_pct, "
                "and top_failing_rules for that day. Also returns a summary.trend field: "
                "'improving', 'declining', or 'stable'. "
                "Response carries data_confidence with bands: no_data (0), "
                "low (1-9), medium (10-99), high (>=100) underlying validations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Contract name (required).",
                    },
                    "days": {
                        "type": "integer",
                        "default": 7,
                        "description": "Look-back window in calendar days (1–90). Default 7.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional: filter to a specific context (e.g. 'billing').",
                    },
                    "by": {
                        "type": "string",
                        "enum": ["date", "agent", "context", "rule"],
                        "default": "date",
                        "description": (
                            "Grouping dimension. 'date' (default) returns one bucket per "
                            "calendar day. 'agent' / 'context' / 'rule' regroup the same "
                            "underlying data by source-system, context, or failing rule "
                            "respectively — useful for diagnosing whether a degradation is "
                            "from a single feed, a single configuration, or a single rule."
                        ),
                    },
                    "include_system": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "When by=agent, include OpenDQV system agents (OpenDQV_SA_* — "
                            "smoke probes, demos, MCP self-tests). Default false honors the "
                            "suppression contract that governs list_agents and get_quality_metrics: "
                            "system traffic stays absent from customer-visible read surfaces. "
                            "Other by= dimensions are unaffected."
                        ),
                    },
                },
                "required": ["contract"],
            },
        ),
        types.Tool(
            name="create_contract_draft",
            description=(
                "Create a DRAFT data contract for a domain not yet covered by existing contracts. "
                "Use this when list_contracts returns no match for your data domain. "
                "Contract name MUST start with 'MCP_' (e.g. 'MCP_satellite_telemetry'). "
                "The contract is created in DRAFT status — you can immediately call validate_record "
                "against it for testing. It will NOT appear as active in the shared library until a "
                "human approves it. "
                "The created_by parameter is required: provide your email or username so the action "
                "is traceable. If omitted, the OPENDQV_AGENT_IDENTITY environment variable is used."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Contract name. Must start with 'MCP_' (e.g. 'MCP_satellite_telemetry').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what data this contract validates.",
                    },
                    "owner": {
                        "type": "string",
                        "description": "Team or person responsible for this contract (e.g. 'Orbital Systems Lab').",
                    },
                    "created_by": {
                        "type": "string",
                        "description": (
                            "Identity of the human on whose behalf this contract is created. "
                            "Use email address or username. Recorded in the audit trail and cannot "
                            "be changed after creation. Falls back to OPENDQV_AGENT_IDENTITY env var "
                            "if not supplied."
                        ),
                    },
                    "rules": {
                        "type": "array",
                        "description": (
                            "List of validation rules. Each rule requires: name (string), type (string), "
                            "field (string). Optional: severity ('error'|'warning', default 'error'), "
                            "error_message (string). Type-specific: min/max (float, for range/min/max rules), "
                            "pattern (string, for regex rules), min_length/max_length (int). "
                            "Common types: not_empty, regex, min, max, range, date_format, "
                            "min_length, max_length, unique, lookup."
                        ),
                        "items": {"type": "object"},
                    },
                },
                "required": ["name", "description", "owner", "rules"],
            },
        ),
        types.Tool(
            name="list_audit_events",
            description=(
                "List validation audit events with filters and cursor pagination. "
                "One row per /validate or /validate/batch call. Use to retrieve "
                "a window of historical validations for replay, dispute "
                "resolution, or regulatory evidence packs (FCA, MiFIR, EMA, "
                "Basel). "
                "Auth: in AUTH_MODE=token, requires admin or auditor role. In "
                "AUTH_MODE=open (local development only, boot warning issued), "
                "every caller is granted admin. Do not run AUTH_MODE=open in "
                "shared or regulated environments. "
                "Trust boundary: this surface assumes a single-tenant "
                "deployment. Per-contract auditor scoping for multi-tenant "
                "isolation is a v2.4 architectural item — until then, an "
                "auditor token can read every contract's events. "
                "Response carries auth_mode field for machine-readable trust "
                "evidence. v2.3.17 F-L: added to MCP for surface-parity with "
                "REST /audit/events."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {"type": "string", "description": "Filter by contract name."},
                    "contract_version": {"type": "string", "description": "Filter by contract version."},
                    "context": {"type": "string", "description": "Filter by context override."},
                    "since": {"type": "string", "description": "ISO 8601 UTC start of window. Default: 24h ago."},
                    "until": {"type": "string", "description": "ISO 8601 UTC end of window."},
                    "agent_id": {"type": "string", "description": "Filter by caller-asserted agent_id."},
                    "caller_principal": {"type": "string", "description": "Filter by trustable caller_principal."},
                    "valid": {"type": "boolean", "description": "True returns only successful events."},
                    "mode": {"type": "string", "enum": ["enforcement", "observation_only"]},
                    "cursor": {"type": "string", "description": "Opaque cursor from prior next_cursor."},
                    "limit": {"type": "integer", "default": 100, "description": "Max events per page (1-1000)."},
                },
            },
        ),
        types.Tool(
            name="get_audit_event",
            description=(
                "Retrieve a single validation audit event by event_id, "
                "when the event was persisted. "
                "event_id is the UUID v7 returned in the original validate "
                "response. Returns 404 when no audit row exists for the "
                "event_id — typically because the validate call was dry_run "
                "(every MCP-driven validate is dry_run by design per the "
                "CRT165 safety lock — see the validate response's "
                "`persisted: bool` field). If `persisted: false`, the "
                "event_id is an idempotency token only, not retrievable here. "
                "Auth: in AUTH_MODE=token, requires admin or auditor role. In "
                "AUTH_MODE=open (local development only, boot warning issued), "
                "every caller is granted admin. Do not run AUTH_MODE=open in "
                "shared or regulated environments. "
                "Trust boundary: single-tenant assumption per list_audit_events "
                "(per-contract scoping is v2.4). v2.3.17 F-L."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "UUID v7 from a validate response."},
                },
                "required": ["event_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "validate_record":
            return await _tool_validate_record(arguments)
        elif name == "validate_batch":
            return await _tool_validate_batch(arguments)
        elif name == "list_contracts":
            return await _tool_list_contracts(arguments)
        elif name == "get_contract":
            return await _tool_get_contract(arguments)
        elif name == "list_versions":
            return await _tool_list_versions(arguments)
        elif name == "compare_contracts":
            return await _tool_compare_contracts(arguments)
        elif name == "get_contract_jsonschema":
            return await _tool_get_contract_jsonschema(arguments)
        elif name == "explain_error":
            return await _tool_explain_error(arguments)
        elif name == "create_contract_draft":
            return await _tool_create_contract_draft(arguments)
        elif name == "get_quality_metrics":
            return await _tool_get_quality_metrics(arguments)
        elif name == "list_agents":
            return await _tool_list_agents(arguments)
        elif name == "get_rule_velocity":
            return await _tool_get_rule_velocity(arguments)
        elif name == "get_quality_trend":
            return await _tool_get_quality_trend(arguments)
        elif name == "list_audit_events":
            return await _tool_list_audit_events(arguments)
        elif name == "get_audit_event":
            return await _tool_get_audit_event(arguments)
        else:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="UNKNOWN_TOOL",
                kind="bad_request",
                status=404,
                detail=f"Unknown tool: {name}",
                remediation="Call list_tools to enumerate available tools.",
            ))]
    except Exception as exc:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INTERNAL_ERROR",
            kind="internal",
            status=500,
            detail=str(exc),
            remediation="Check the OpenDQV server logs; if reproducible, file an issue with the tool name and arguments.",
        ))]


async def _tool_validate_record(args: dict) -> list[types.TextContent]:
    contract_name = args["contract"]
    record = args["record"]
    context = args.get("context")

    # v2.3.17 F-B (Cluster 2): reject reserved-prefix agent_id at the write
    # boundary on the MCP in-process surface. Same guard as the Pydantic
    # validator on the REST validate models (api/models.py). Without this
    # guard, the in-process MCP path would accept and persist a spoofed
    # OpenDQV_SA_* identity that the output-side suppression then hides
    # from every dashboard — completing the attack in software.
    _agent_id = args.get("agent_id")
    if _agent_id and _agent_id.startswith("OpenDQV_SA_"):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INVALID_AGENT_ID",
            kind="bad_request",
            status=422,
            detail=(
                f"agent_id '{_agent_id}' uses the reserved prefix 'OpenDQV_SA_'. "
                f"This prefix is reserved for OpenDQV-owned system traffic "
                f"(smoke probes, demos, MCP self-tests)."
            ),
            remediation=(
                "Choose an agent_id that identifies your service, AI agent, or "
                "team — e.g. 'salesforce-prod', 'claude-desktop-alice', "
                "'data-platform-team'."
            ),
        ))]

    if _remote_client:
        payload = {"contract": contract_name, "record": record}
        if context:
            payload["context"] = context
        if args.get("agent_id"):
            payload["agent_id"] = args["agent_id"]
        if args.get("dry_run"):
            payload["dry_run"] = True
        if args.get("hash"):
            payload["hash"] = args["hash"]
        resp = _remote_client.post("/api/v1/validate?allow_draft=true", json=payload)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    contract_hash = args.get("hash")
    if contract_hash:
        contract = _registry.contract_by_hash(contract_name, contract_hash)
        if not contract:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTRACT_HASH_MISMATCH",
                kind="not_found",
                status=404,
                detail=f"Contract '{contract_name}' has no history entry matching hash '{contract_hash}'.",
                remediation="Call list_versions for this contract to see available entry_hash values.",
            ))]
    else:
        contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{contract_name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]

    # v2.3.17 F-A (MCP in-process context path) fix: route through the
    # registry's get_rules_with_context_status so context overrides on a
    # historical contract apply correctly (the previous code replaced
    # `rules` with the raw `contract.contexts[context]` dict — Rule
    # objects vs raw dicts — which silently broke override application).
    # Same call as REST so both paths converge on identical rule resolution.
    rules, _ctx_status = _registry.get_rules_with_context_status(contract, context)
    _context_warning = (
        f"Context '{context}' is not declared on contract '{contract_name}'. "
        f"Validation proceeded with base rules (no context overrides applied). "
        f"If you intended a metadata tag (e.g. 'demo', 'ci', 'test') this is fine; "
        f"if you intended an override context, declare it on the contract."
        if _ctx_status == "undeclared" else None
    )

    from opendqv.core.contracts import _compute_effective_rule_hash
    result = _validate_record(record, rules, contract_name)
    result["contract"] = contract_name
    result["version"] = contract.version
    result["effective_rule_hash"] = _compute_effective_rule_hash(rules)
    if contract.status == ContractStatus.DRAFT:
        result["draft_notice"] = _DRAFT_NOTICE
    if _context_warning:
        result["context_warning"] = _context_warning
    result["governance_tip"] = _pick_governance_tip(rules, result.get("errors", []))
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


async def _tool_validate_batch(args: dict) -> list[types.TextContent]:
    contract_name = args["contract"]
    records = args["records"]

    # v2.3.17 F-B (Cluster 2): reserved-prefix guard — see _tool_validate_record.
    _agent_id = args.get("agent_id")
    if _agent_id and _agent_id.startswith("OpenDQV_SA_"):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INVALID_AGENT_ID",
            kind="bad_request",
            status=422,
            detail=(
                f"agent_id '{_agent_id}' uses the reserved prefix 'OpenDQV_SA_'. "
                f"This prefix is reserved for OpenDQV-owned system traffic "
                f"(smoke probes, demos, MCP self-tests)."
            ),
            remediation=(
                "Choose an agent_id that identifies your service, AI agent, or "
                "team — e.g. 'salesforce-prod', 'claude-desktop-alice', "
                "'data-platform-team'."
            ),
        ))]

    if _remote_client:
        payload = {"contract": contract_name, "records": records}
        context = args.get("context")
        if context:
            payload["context"] = context
        if args.get("agent_id"):
            payload["agent_id"] = args["agent_id"]
        if args.get("dry_run"):
            payload["dry_run"] = True
        if args.get("hash"):
            payload["hash"] = args["hash"]
        resp = _remote_client.post("/api/v1/validate/batch?allow_draft=true", json=payload)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    if len(records) > 10000:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="BATCH_TOO_LARGE",
            kind="bad_request",
            status=413,
            detail=f"Maximum 10,000 records per batch call; received {len(records)}.",
            remediation="Split the batch into chunks of 10,000 or fewer records.",
        ))]

    contract_hash = args.get("hash")
    if contract_hash:
        contract = _registry.contract_by_hash(contract_name, contract_hash)
        if not contract:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTRACT_HASH_MISMATCH",
                kind="not_found",
                status=404,
                detail=f"Contract '{contract_name}' has no history entry matching hash '{contract_hash}'.",
                remediation="Call list_versions for this contract to see available entry_hash values.",
            ))]
    else:
        contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{contract_name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]

    # v2.3.17: route through get_rules_with_context_status so batch validation
    # also applies context overrides on the in-process path (was silently
    # ignored — used contract.rules unconditionally — same family as F-A on
    # validate_record). Surfaces context_warning consistently with
    # validate_record for callers who supply an undeclared context.
    context = args.get("context")
    rules, _ctx_status = _registry.get_rules_with_context_status(contract, context)
    _context_warning = (
        f"Context '{context}' is not declared on contract '{contract_name}'. "
        f"Validation proceeded with base rules (no context overrides applied). "
        f"If you intended a metadata tag (e.g. 'demo', 'ci', 'test') this is fine; "
        f"if you intended an override context, declare it on the contract."
        if _ctx_status == "undeclared" else None
    )

    from opendqv.core.contracts import _compute_effective_rule_hash
    result = _validate_batch(records, rules, contract_name)
    result["contract"] = contract_name
    result["version"] = contract.version
    result["effective_rule_hash"] = _compute_effective_rule_hash(rules)
    if contract.status == ContractStatus.DRAFT:
        result["draft_notice"] = _DRAFT_NOTICE
    if _context_warning:
        result["context_warning"] = _context_warning
    result["governance_tip"] = _pick_governance_tip(
        rules,
        result.get("results", [{}])[0].get("errors", []) if result.get("results") else [],
    )
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


async def _tool_list_contracts(args: dict) -> list[types.TextContent]:
    if _remote_client:
        resp = _remote_client.get("/api/v1/contracts")
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    contracts = _registry.list_contracts()
    summary = [
        {
            "name": c["name"],
            "version": c["version"],
            "status": c["status"],
            "rule_count": c["rule_count"],
            "description": c.get("description") or "",
            "owner": c.get("owner") or "",
        }
        for c in contracts
    ]
    return [types.TextContent(type="text", text=json.dumps(summary, default=str))]


async def _tool_get_contract(args: dict) -> list[types.TextContent]:
    name = args["name"]
    version = args.get("version", "latest")
    contract_hash = args.get("hash")
    context = args.get("context")

    if _remote_client:
        url = f"/api/v1/contracts/{name}"
        params = []
        if contract_hash:
            params.append(f"hash={contract_hash}")
        elif version and version != "latest":
            params.append(f"version={version}")
        if context:
            params.append(f"context={context}")
        if params:
            url += "?" + "&".join(params)
        resp = _remote_client.get(url)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    if contract_hash:
        contract = _registry.contract_by_hash(name, contract_hash)
        if not contract:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTRACT_HASH_MISMATCH",
                kind="not_found",
                status=404,
                detail=f"Contract '{name}' has no history entry matching hash '{contract_hash}'.",
                remediation="Call list_versions for this contract to see available entry_hash values.",
            ))]
    else:
        contract = _registry.get(name, version)
        if not contract:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTRACT_NOT_FOUND",
                kind="not_found",
                status=404,
                detail=f"Contract '{name}' not found.",
                remediation="Call list_contracts to see available contract names.",
            ))]

    if context:
        if context not in (contract.contexts or {}):
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTEXT_NOT_FOUND",
                kind="not_found",
                status=404,
                detail=f"Context '{context}' not defined for contract '{name}'.",
                remediation="Omit the context parameter, or call get_contract without it to see contexts defined on this contract.",
            ))]
        try:
            scoped_rules = _registry.get_rules_with_context(contract, context)
        except Exception as exc:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="INTERNAL_ERROR",
                kind="internal",
                status=500,
                detail=str(exc),
                remediation="Check the OpenDQV server logs; if reproducible, file an issue with the contract name and context.",
            ))]
        contract = contract.model_copy(update={"rules": scoped_rules})

    rules = [
        {
            "name": r.name,
            "type": r.type,
            "field": r.field,
            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
            "error_message": r.error_message,
            "description": r.description or "",
            "allowed_values": r.allowed_values,
            "pattern": r.pattern,
            "min_value": r.min_value,
            "max_value": r.max_value,
            "min_length": r.min_length,
            "max_length": r.max_length,
        }
        for r in contract.rules
    ]

    detail = {
        "name": contract.name,
        "version": contract.version,
        "status": contract.status.value if hasattr(contract.status, "value") else str(contract.status),
        "description": contract.description or "",
        "owner": contract.owner or "",
        "rule_count": len(contract.rules),
        "rules": rules,
    }
    return [types.TextContent(type="text", text=json.dumps(detail, default=str))]


async def _tool_list_versions(args: dict) -> list[types.TextContent]:
    name = args["name"]

    if _remote_client:
        resp = _remote_client.get(f"/api/v1/contracts/{name}/versions")
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    history = _registry.get_history(name)
    if not history and not _registry.get(name):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]
    versions = [
        {
            "version": snap.get("version", ""),
            "status": snap.get("status", ""),
            "entry_hash": snap.get("entry_hash"),
            "content_hash": snap.get("content_hash"),
            "created_at": snap.get("updated_at"),
            "owner": snap.get("owner"),
            "owner_team": snap.get("owner_team"),
            "approved_by": snap.get("approved_by"),
            "proposed_by": snap.get("proposed_by"),
        }
        for snap in history
    ]
    return [types.TextContent(type="text", text=json.dumps({
        "contract": name,
        "versions": versions,
    }, default=str))]


async def _tool_get_contract_jsonschema(args: dict) -> list[types.TextContent]:
    name = args["name"]
    context = args.get("context")

    if _remote_client:
        url = f"/api/v1/contracts/{name}/jsonschema"
        if context:
            url += f"?context={context}"
        resp = _remote_client.get(url)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    from opendqv.core.jsonschema import contract_to_jsonschema

    contract = _registry.get(name)
    if not contract:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]
    if context:
        try:
            scoped_rules = _registry.get_rules_with_context(contract, context)
        except Exception as exc:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="INTERNAL_ERROR",
                kind="internal",
                status=500,
                detail=str(exc),
                remediation="Check the OpenDQV server logs; if reproducible, file an issue with the contract name and context.",
            ))]
        contract = contract.model_copy(update={"rules": scoped_rules})
    schema = contract_to_jsonschema(contract)
    return [types.TextContent(type="text", text=json.dumps(schema, default=str))]


async def _tool_compare_contracts(args: dict) -> list[types.TextContent]:
    name = args["name"]
    hash_a = args["hash_a"]
    hash_b = args["hash_b"]

    if _remote_client:
        resp = _remote_client.get(
            f"/api/v1/contracts/{name}/diff",
            params={"hash_a": hash_a, "hash_b": hash_b},
        )
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    if not _registry.get(name):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]
    try:
        diff = _registry.diff_by_hash(name, hash_a, hash_b)
    except ValueError as exc:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INVALID_HASH",
            kind="bad_request",
            status=400,
            detail=str(exc),
            remediation="Call list_versions to see valid entry_hash values for this contract.",
        ))]
    return [types.TextContent(type="text", text=json.dumps(diff, default=str))]


async def _tool_explain_error(args: dict) -> list[types.TextContent]:
    contract_name = args["contract"]
    field = args["field"]
    rule_name = args["rule"]

    if _remote_client:
        # Fetch contract from API to get rule definitions, then explain locally.
        # The API's /explain endpoint is contract-level only; per-rule explanation
        # is pure deterministic logic so we compute it here.
        resp = _remote_client.get(f"/api/v1/contracts/{contract_name}")
        if resp.status_code == 404:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="CONTRACT_NOT_FOUND",
                kind="not_found",
                status=404,
                detail=f"Contract '{contract_name}' not found on remote API.",
                remediation="Call list_contracts to see available contract names on this server.",
            ))]
        resp.raise_for_status()
        detail = resp.json()
        rules_raw = detail.get("rules", [])
        matching = [r for r in rules_raw if r["name"] == rule_name and r["field"] == field]
        if not matching:
            matching = [r for r in rules_raw if r["name"] == rule_name]
        if not matching:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="RULE_NOT_FOUND",
                kind="not_found",
                status=404,
                detail=f"Rule '{rule_name}' not found in contract '{contract_name}'.",
                remediation="Call get_contract for this contract to see its rules.",
            ))]
        r = matching[0]
        # v2.3.22 Cluster D / O-19: reconstruct Rule with the FULL
        # constraint set returned by GET /contracts/{name} (RuleInfo
        # at api/models.py). Prior versions passed only name/type/
        # field/error_message — every constraint surfaced as None,
        # so explain_rule emitted "matching None" / "(reference list)"
        # placeholders for regex and lookup rules. Reviewer (Persona B
        # round-1 O-19) flagged this as templated content. Rule has
        # populate_by_name=True with `min` / `max` aliases for
        # min_value / max_value — the REST RuleInfo uses the alias
        # form, which Pydantic accepts directly via the alias.
        rule_obj = _Rule(
            name=r["name"],
            type=r["type"],
            field=r["field"],
            error_message=r.get("error_message", ""),
            pattern=r.get("pattern"),
            min=r.get("min"),
            max=r.get("max"),
            min_length=r.get("min_length"),
            max_length=r.get("max_length"),
            min_age=r.get("min_age"),
            max_age=r.get("max_age"),
            format=r.get("format"),
            compare_to=r.get("compare_to"),
            compare_op=r.get("compare_op"),
            allowed_values=r.get("allowed_values"),
            lookup_file=r.get("lookup_file"),
            checksum_algorithm=r.get("checksum_algorithm"),
        )
        info = explain_rule(rule_obj)
        payload = {
            "contract": contract_name,
            "field": rule_obj.field,
            "rule": rule_obj.name,
            "rule_type": info["rule_type"],
            "explanation": info["explanation"],
            "valid_examples": info["valid_examples"],
            "invalid_examples": info["invalid_examples"],
            "constraint": info["constraint"],
        }
        if "lookup_source" in info:
            payload["lookup_source"] = info["lookup_source"]
        return [types.TextContent(type="text", text=json.dumps(payload))]

    contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="CONTRACT_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Contract '{contract_name}' not found.",
            remediation="Call list_contracts to see available contract names.",
        ))]

    matching = [r for r in contract.rules if r.name == rule_name and r.field == field]
    if not matching:
        matching = [r for r in contract.rules if r.name == rule_name]
    if not matching:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="RULE_NOT_FOUND",
            kind="not_found",
            status=404,
            detail=f"Rule '{rule_name}' not found on field '{field}' in contract '{contract_name}'.",
            remediation="Call get_contract for this contract to see its rules and their fields.",
        ))]

    rule = matching[0]
    info = explain_rule(rule)

    response = {
        "contract": contract_name,
        "field": rule.field,
        "rule": rule.name,
        "rule_type": info["rule_type"],
        "explanation": info["explanation"],
        "valid_examples": info["valid_examples"],
        "invalid_examples": info["invalid_examples"],
        "constraint": info["constraint"],
    }
    if "lookup_source" in info:
        response["lookup_source"] = info["lookup_source"]
    return [types.TextContent(type="text", text=json.dumps(response, default=str))]


async def _tool_create_contract_draft(args: dict) -> list[types.TextContent]:
    name = args.get("name", "").strip()
    description = args.get("description", "").strip()
    owner = args.get("owner", "").strip()
    created_by = args.get("created_by", "").strip()
    rules_data = args.get("rules", [])

    # Attribution: require created_by or fall back to OPENDQV_AGENT_IDENTITY env var
    if not created_by:
        created_by = os.environ.get("OPENDQV_AGENT_IDENTITY", "").strip()
    if not created_by:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="MISSING_CREATED_BY",
            kind="bad_request",
            status=400,
            detail="created_by is required and was not provided.",
            remediation=(
                "Pass created_by as a parameter, or set the OPENDQV_AGENT_IDENTITY "
                "environment variable to your email or username. This value is "
                "recorded in the contract audit trail."
            ),
        ))]

    # ACT-045-06: Rate limit — 10 draft creations per identity per hour.
    _now = _time.monotonic()
    _window = _draft_creation_log.setdefault(created_by, [])
    _draft_creation_log[created_by] = [t for t in _window if _now - t < _DRAFT_RATE_WINDOW]
    if len(_draft_creation_log[created_by]) >= _DRAFT_RATE_LIMIT:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="DRAFT_RATE_LIMITED",
            kind="rate_limited",
            status=429,
            detail=(
                f"Rate limit reached: '{created_by}' has created {_DRAFT_RATE_LIMIT} "
                f"draft contracts in the last hour."
            ),
            remediation="Wait before creating more drafts, or contact an admin to raise the limit.",
        ))]

    # MCP_ prefix guard (also enforced inside ContractRegistry.create_draft)
    if not name.startswith("MCP_"):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INVALID_CONTRACT_NAME",
            kind="validation",
            status=422,
            detail=f"Agent-created contracts must be named with the 'MCP_' prefix. Got: '{name}'",
            remediation="Rename the contract with an 'MCP_' prefix (e.g. MCP_satellite_telemetry) and retry.",
        ))]

    try:
        contract = _registry.create_draft(
            name=name,
            description=description,
            owner=owner,
            created_by=created_by,
            rules_data=rules_data,
        )
    except ValueError as exc:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="DRAFT_VALIDATION_ERROR",
            kind="validation",
            status=422,
            detail=str(exc),
            remediation="Inspect the error detail and adjust the contract name, owner, or rules accordingly.",
        ))]

    _draft_creation_log[created_by].append(_time.monotonic())

    # Remote mode: trigger a contract reload on the central API so the new YAML
    # becomes visible there. Requires MCP server and API to share the same
    # contracts directory (e.g. a mounted volume or shared NFS path).
    remote_reload_note = ""
    if _remote_client:
        try:
            reload_resp = _remote_client.post("/api/v1/contracts/reload")
            reload_resp.raise_for_status()
            remote_reload_note = (
                " The central API has been notified and reloaded the contract. "
                "Ensure the MCP server and API share the same contracts directory."
            )
        except Exception as reload_err:
            remote_reload_note = (
                f" Note: could not notify the remote API to reload ({reload_err}). "
                "The draft exists locally. Manually POST to /api/v1/contracts/reload "
                "on the API server, or ensure both share the same contracts directory."
            )

    return [types.TextContent(type="text", text=json.dumps({
        "created": True,
        "name": contract.name,
        "version": contract.version,
        "status": contract.status.value,
        "source": contract.source,
        "proposed_by": contract.proposed_by,
        "rule_count": len(contract.rules),
        "message": (
            f"Draft contract '{contract.name}' created with {len(contract.rules)} rule(s). "
            "You can now call validate_record against it (draft status allows testing). "
            "To submit for human review: POST /api/v1/contracts/{name}/submit-for-review. "
            "The contract will only become ACTIVE — and visible in the shared library — "
            f"after a human approves it.{remote_reload_note}"
        ),
    }, default=str))]


async def _tool_get_quality_metrics(args: dict) -> list[types.TextContent]:
    contract_name = args.get("contract", "")
    window_hours = args.get("window_hours", 24)
    agent_id_filter = args.get("agent_id", "")
    include_system = bool(args.get("include_system", False))
    governance_tip = (
        "Pass this contract's asset_id to your catalog MCP server to retrieve "
        "lineage and ownership context."
    )

    if _remote_client:
        # v2.3.22 Cluster A (Persona B 2026-04-27 P1.5 regression): the
        # proxy path was dropping `contract`, `agent_id`, AND `window_hours=0`
        # on the outbound REST call. So server-side `_scope_summary_to_contract`
        # never ran and `by_agent` came back unscoped — exactly the
        # cross-contract per-agent leak the reviewer hit on round 2 (and
        # round 1). The v2.3.17 Cluster 5 fix scoped the helper but the
        # proxy never sent the param to trigger it; the v2.3.20 Cluster A
        # guard at line 1515 only protected the in-process branch. This
        # corrects the upstream call so all three params reach the server.
        # No proxy-path test in the suite caught this — the v2.3.22 paired
        # test (tests/test_v2_3_22_a_proxy_path_params.py) closes that gap.
        params = {}
        if window_hours is not None:
            params["window_hours"] = window_hours
        if include_system:
            params["include_system"] = "true"
        if contract_name:
            params["contract"] = contract_name
        if agent_id_filter:
            params["agent_id"] = agent_id_filter
        resp = _remote_client.get("/api/v1/stats", params=params)
        resp.raise_for_status()
        summary = resp.json()
    elif agent_id_filter:
        # Explicit agent filter — caller is asking for that exact agent_id, so
        # suppression is irrelevant (they get what they asked for).
        summary = _stats.get_windowed_summary_for_agent(window_hours or 24, agent_id_filter)
    else:
        summary = (
            _stats.get_windowed_summary(window_hours, include_system=include_system)
            if window_hours
            else _stats.get_summary(include_system=include_system)
        )
        # SQLite fallback: if in-memory events are empty (e.g. after a restart), use persisted data
        if summary.get("total_validations", 0) == 0 and not _remote_client:
            try:
                fallback_contract = contract_name or ""
                if fallback_contract:
                    fb = _quality_stats.get_windowed_totals(fallback_contract, window_hours or 24)
                    if fb.get("total", 0) > 0:
                        summary = dict(summary)
                        summary["by_contract"] = {
                            f"{fallback_contract}:default": {
                                "pass": fb["passed"],
                                "fail": fb["failed"],
                            }
                        }
                        summary["total_validations"] = fb["total"]
                        summary["total_pass"] = fb["passed"]
                        summary["total_fail"] = fb["failed"]
            except Exception:
                pass

    by_contract = summary.get("by_contract", {})
    if contract_name:
        by_contract = {k: v for k, v in by_contract.items() if k.startswith(f"{contract_name}:")}
    top_fields = summary.get("top_failing_fields", [])

    contracts_to_process = (
        {contract_name} if contract_name
        else {k.split(":")[0] for k in by_contract.keys()}
    )

    result = []
    for cname in contracts_to_process:
        contract_keys = [k for k in by_contract.keys() if k.startswith(f"{cname}:")]
        total_pass = sum(by_contract[k]["pass"] for k in contract_keys)
        total_fail = sum(by_contract[k]["fail"] for k in contract_keys)
        total_val = total_pass + total_fail
        # v2.3.18 Q3: pass_rate_pct (percent 0–100, 1dp). Single canonical
        # field name across every surface; no companion `pass_rate` field.
        # v2.3.22 Cluster F: empty-state returns null (was 100.0).
        pass_rate_pct = round(total_pass / total_val * 100, 1) if total_val > 0 else None
        # v2.3.23 round-3 review (Sonnet abab68d76a01115ec): every entry
        # in top_failing_rules carries a `severity` field so consumers can
        # rank by error vs warning correctly. Read from the live registry
        # (authoritative source); legacy data with deleted rules surfaces
        # severity="unknown" — explicit honesty over silent omission.
        sev_map = _severity_map(cname)
        # v2.3.23 round-3 review (Sonnet a154314ae2e179025): strip the
        # synthesised `ctx_<context>_` prefix from rule names so override
        # rules collapse to their base name on the read surface. Build
        # the normalizer once per contract; coalesce duplicate names.
        try:
            _contract_obj = _registry.get(cname)
        except Exception:
            _contract_obj = None
        from opendqv.monitoring import _build_rule_normalizer
        _normalize_rule = _build_rule_normalizer(_contract_obj)
        from collections import defaultdict as _dd
        _coalesced: dict = _dd(lambda: {"rule": "", "field": "", "failures": 0, "severity": "unknown"})
        for f in top_fields:
            if f["contract"] != cname:
                continue
            rn = _normalize_rule(f["rule"])
            bucket = _coalesced[rn]
            bucket["rule"] = rn
            # field: keep first non-? value; severity: keep first non-unknown.
            if not bucket["field"] or bucket["field"] == "?":
                bucket["field"] = f.get("field", "") or ""
            bucket["failures"] += int(f.get("count", 0))
            if bucket["severity"] in (None, "unknown"):
                bucket["severity"] = sev_map.get(rn, "unknown")
        top_rules = sorted(
            _coalesced.values(),
            key=lambda x: x["failures"], reverse=True,
        )[:5]
        # v2.3.23 round-4 P1-C (Sonnet afa6d1f8581846bfe): the prior
        # `_quality_stats.get_trend(cname, days=1)` augmentation block
        # is removed. It was a hydration-era crutch that mixed lifetime
        # `top_failing_fields` with windowed days=1 trend counts via
        # max() — explaining the metrics-vs-trend reconciliation gap
        # the reviewer flagged (revenue_ceiling 450 vs 199). Now that
        # `get_windowed_summary` builds `top_failing_fields` windowed,
        # the augmentation has nothing to add and was actively wrong:
        # max() of a lifetime count and a windowed count returned the
        # lifetime count.
        confidence, confidence_note = _quality_confidence(total_val)
        entry = {
            "contract": cname,
            "window_hours": window_hours,
            "total_validations": total_val,
            "pass_rate_pct": pass_rate_pct,
            "passed": total_pass,
            "failed": total_fail,
            "data_confidence": confidence,
            "confidence_note": confidence_note,
            "top_failing_rules": top_rules,
            "latency": (
                _stats.get_contract_latency(cname, window_hours or 24)
                if not _remote_client else summary.get("latency", {})
            ),
            "governance_tip": governance_tip if total_val > 0 else "No validation data recorded yet for this contract.",
        }
        if config.CATALOG_URI_PREFIX:
            entry["catalog_hint"] = f"{config.CATALOG_URI_PREFIX}{cname}"
        # Include per-agent breakdown when >1 distinct agent seen in the window
        if not _remote_client:
            try:
                agent_breakdown = _quality_stats.get_agent_breakdown(
                    cname, window_hours or 24, include_system=include_system,
                )
                if len(agent_breakdown) > 1:
                    entry["by_agent"] = {
                        a["agent_id"]: {
                            "total": a["total"],
                            "passed": a["passed"],
                            "failed": a["failed"],
                            "pass_rate_pct": a["pass_rate_pct"],
                        }
                        for a in agent_breakdown
                    }
                # v2.3.20 P1.5 + v2.3.23 outside-review P0 (Sonnet
                # a74a3758ab3476042): when contract is filtered, scoped
                # get_agent_breakdown above is correct; if it returned
                # ≤1 agent, omit by_agent entirely. Previous fallback
                # (`elif summary.get("by_agent") and not contract_name:
                # entry["by_agent"] = summary["by_agent"]`) inlined the
                # unscoped GLOBAL rollup into every per-contract entry
                # in the all-contracts loop. Reviewer caught it: same
                # broadsign/salesforce per-agent totals labelled as the
                # breakdown for banking_transaction, customer,
                # media_content, AND proof_of_play. Removed. Consumers
                # who want a global agent rollup call list_agents.
            except Exception:
                pass
        if total_val > 0 or contract_name:
            result.append(entry)

    if contract_name and not result:
        empty = {
            "contract": contract_name,
            "window_hours": window_hours,
            "total_validations": 0,
            # v2.3.22 Cluster F: explicit no-data fallback returns null,
            # not 100.0. Empty dashboard = "no data" signal, not "perfect."
            "pass_rate_pct": None,
            "failed": 0,
            "data_confidence": "no_data",
            "confidence_note": "No validation data recorded yet for this contract.",
            "top_failing_rules": [],
            "governance_tip": "No validation data recorded yet for this contract.",
        }
        if config.CATALOG_URI_PREFIX:
            empty["catalog_hint"] = f"{config.CATALOG_URI_PREFIX}{contract_name}"
        result.append(empty)

    output = result[0] if (contract_name and result) else result
    return [types.TextContent(type="text", text=json.dumps(output, default=str))]


async def _tool_get_quality_trend(args: dict) -> list[types.TextContent]:
    contract_name = args.get("contract", "")
    if not contract_name:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="MISSING_CONTRACT",
            kind="bad_request",
            status=400,
            detail="contract is required and was not provided.",
            remediation="Pass the contract name as the 'contract' argument.",
        ))]
    days = max(1, min(90, int(args.get("days", 7))))
    context = args.get("context") or None
    by = args.get("by", "date")
    include_system = bool(args.get("include_system", False))
    if by not in ("date", "agent", "context", "rule"):
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INVALID_PARAMETER",
            kind="bad_request",
            status=400,
            detail=f"invalid by={by!r}; expected one of: date, agent, context, rule.",
            remediation="Pass by='date' (default) or one of agent | context | rule.",
        ))]

    if _remote_client:
        params: dict = {"days": days, "by": by}
        if context:
            params["context"] = context
        if include_system:
            params["include_system"] = "true"
        resp = _remote_client.get(f"/api/v1/contracts/{contract_name}/quality-trend", params=params)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=json.dumps(resp.json(), default=str))]

    points = _quality_stats.get_trend(
        contract_name, days=days, context=context, by=by, include_system=include_system,
    )
    # v2.3.23 round-3 review (Sonnet a154314ae2e179025): strip the
    # synthesised `ctx_<context>_` prefix from rule names at the emit
    # boundary so consumers see the base rule name. Mirrors the REST
    # path's normalize-then-severity sequence so dual surfaces emit
    # byte-identical names.
    try:
        contract_obj = _registry.get(contract_name)
    except Exception:
        contract_obj = None
    if contract_obj is not None:
        from opendqv.monitoring import normalize_trend_rule_names
        points = normalize_trend_rule_names(points, contract_obj, by)
    # v2.3.23 round-3 review: tag each ranked rule with severity so a
    # consumer can read "warning failing 100x" vs "error failing 50x"
    # without re-fetching the contract. Mirror the get_quality_metrics
    # treatment. by=date emits per-day points each with their own
    # top_failing_rules_ranked; by=rule rows are themselves rule entries.
    sev_map = _severity_map(contract_name)
    if sev_map and points:
        for p in points:
            ranked = p.get("top_failing_rules_ranked")
            if ranked:
                for entry in ranked:
                    if "severity" not in entry:
                        entry["severity"] = sev_map.get(entry.get("rule", ""), "unknown")
            if by == "rule" and "key" in p and "severity" not in p:
                # by=rule rows have shape {key: rule_name, violation_count: N}
                p["severity"] = sev_map.get(p["key"], "unknown")
    # v2.3.22 N-2 (P0): by=rule rows carry violation_count not total_records
    # (a rule has violations, not records — see quality_stats.py:300-323).
    # Summing total_records from those rows yields 0, which routes through
    # _quality_confidence(0) to data_confidence: "no_data" — the SRE blind
    # spot the round-2 reviewer hit. Mirror the REST path's v2.3.17 fix
    # (routes_contracts.py:395-397) and re-query by=date for the underlying
    # record volume when by=rule.
    if by == "rule":
        date_points = _quality_stats.get_trend(contract_name, days=days, context=context, by="date")
        total_validations = sum(int(p.get("total_records", 0) or 0) for p in date_points)
    else:
        total_validations = sum(int(p.get("total_records", 0) or 0) for p in points)
    confidence, confidence_note = _quality_confidence(total_validations)
    result = {
        "contract": contract_name,
        "days": days,
        "context": context,
        "by": by,
        "points": points,
        "data_confidence": confidence,
        "confidence_note": confidence_note,
        "total_validations": total_validations,
    }
    if by == "date":
        # v2.3.18 Q3: pass_rate_pct on every per-bucket point.
        result["summary"] = {
            "total_days_with_data": len(points),
            "latest_pass_rate_pct": points[-1]["pass_rate_pct"] if points else None,
            "earliest_pass_rate_pct": points[0]["pass_rate_pct"] if points else None,
            "trend": (
                "improving" if len(points) >= 2 and points[-1]["pass_rate_pct"] > points[0]["pass_rate_pct"]
                else "declining" if len(points) >= 2 and points[-1]["pass_rate_pct"] < points[0]["pass_rate_pct"]
                else "stable"
            ),
        }
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


async def _tool_list_agents(args: dict) -> list[types.TextContent]:
    window_hours = max(1, min(8760, int(args.get("window_hours", 24))))
    include_system = bool(args.get("include_system", False))
    if _remote_client:
        params = {"window_hours": window_hours}
        if include_system:
            params["include_system"] = "true"
        resp = _remote_client.get("/api/v1/agents", params=params)
        return [types.TextContent(type="text", text=resp.text)]
    out = {
        "window_hours": window_hours,
        "agents": _stats.list_agents(window_hours, include_system=include_system),
        "include_system": include_system,
    }
    return [types.TextContent(type="text", text=json.dumps(out, default=str))]


async def _tool_get_rule_velocity(args: dict) -> list[types.TextContent]:
    contract_name = args.get("contract", "")
    if not contract_name:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="MISSING_CONTRACT",
            kind="bad_request",
            status=400,
            detail="contract is required and was not provided.",
            remediation="Pass the contract name as the 'contract' argument.",
        ))]
    window_hours = max(1, min(168, int(args.get("window_hours", 24))))
    bucket_minutes = max(1, min(60, int(args.get("bucket_minutes", 5))))

    if _remote_client:
        resp = _remote_client.get(
            "/api/v1/analytics/rule-velocity",
            params={"contract": contract_name, "window_hours": window_hours, "bucket_minutes": bucket_minutes},
        )
        resp.raise_for_status()
        return [types.TextContent(type="text", text=json.dumps(resp.json(), default=str))]

    try:
        data = _quality_analytics.rule_failure_velocity(
            contract_name=contract_name,
            window_hours=window_hours,
            bucket_minutes=bucket_minutes,
        )
    except Exception as exc:
        return [types.TextContent(type="text", text=_error_envelope(
            error_code="INTERNAL_ERROR",
            kind="internal",
            status=500,
            detail=str(exc),
            remediation="Check the OpenDQV server logs; if reproducible, file an issue with the contract name and parameters.",
        ))]

    # CRT170/J6: total validations underpinning this window → confidence band.
    try:
        totals = _quality_stats.get_windowed_totals(contract_name, window_hours)
        total_validations = int(totals.get("total", 0))
    except Exception:
        total_validations = 0
    confidence, confidence_note = _quality_confidence(total_validations)
    if isinstance(data, dict):
        data["data_confidence"] = confidence
        data["confidence_note"] = confidence_note
        data["total_validations"] = total_validations

    return [types.TextContent(type="text", text=json.dumps(data, default=str))]


async def _tool_list_audit_events(args: dict) -> list[types.TextContent]:
    """v2.3.17 F-L: MCP wrapper over GET /api/v1/audit/events.

    Mirrors the proxy's list_audit_events. When configured with a remote
    REST client (the in-process server's standard configuration), forwards
    to the REST surface. The REST endpoint enforces the auth gate
    (admin/auditor); this wrapper trusts the engine's check.
    """
    if _remote_client:
        params = {}
        for key in ("contract", "contract_version", "context", "since", "until",
                    "agent_id", "caller_principal", "mode", "cursor"):
            if args.get(key):
                params[key] = args[key]
        if "valid" in args:
            params["valid"] = "true" if args["valid"] else "false"
        if "limit" in args:
            params["limit"] = args["limit"]
        resp = _remote_client.get("/api/v1/audit/events", params=params)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=_error_envelope(
        error_code="REMOTE_CLIENT_REQUIRED",
        kind="bad_request",
        status=400,
        detail="list_audit_events requires the in-process MCP server to be configured with a remote REST client.",
        remediation="Set OPENDQV_API_URL and OPENDQV_API_TOKEN environment variables.",
    ))]


async def _tool_get_audit_event(args: dict) -> list[types.TextContent]:
    """v2.3.17 F-L: MCP wrapper over GET /api/v1/audit/events/{event_id}."""
    if _remote_client:
        try:
            event_id = args["event_id"]
        except KeyError:
            return [types.TextContent(type="text", text=_error_envelope(
                error_code="INVALID_REQUEST",
                kind="bad_request",
                status=400,
                detail="Missing required argument: event_id",
                remediation="Provide event_id (UUID v7) from a prior validate response.",
            ))]
        resp = _remote_client.get(f"/api/v1/audit/events/{event_id}")
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]
    return [types.TextContent(type="text", text=_error_envelope(
        error_code="REMOTE_CLIENT_REQUIRED",
        kind="bad_request",
        status=400,
        detail="get_audit_event requires the in-process MCP server to be configured with a remote REST client.",
        remediation="Set OPENDQV_API_URL and OPENDQV_API_TOKEN environment variables.",
    ))]


# ── Entry point ───────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
