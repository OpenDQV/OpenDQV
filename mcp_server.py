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

import config
from core.contracts import ContractRegistry
from core.validator import validate_record as _validate_record, validate_batch as _validate_batch
from core.explainer import explain_rule
from core.rule_parser import ContractStatus, Rule as _Rule
from monitoring import stats as _stats
from core.quality_stats import QualityStats as _QualityStats

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
server = Server("OpenDQV")


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
                "to fix the record. Do not write the record until valid is true."
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
                },
                "required": ["contract", "record"],
            },
        ),
        types.Tool(
            name="validate_batch",
            description=(
                "Validate up to 10,000 records in a single call. "
                "Returns per-record results and aggregate statistics. "
                "Use for bulk data imports or pipeline validation."
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
                },
                "required": ["contract", "records"],
            },
        ),
        types.Tool(
            name="list_contracts",
            description=(
                "List all available validation contracts with their names, statuses, and rule counts. "
                "Call this first to discover which contract applies to your data. "
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
                "or to generate type-safe data structures that match the contract."
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
                },
                "required": ["name"],
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
                "Includes pass_rate, failed count, top_failing_rules, and a catalog_hint field "
                "for chaining to Marmot or other catalog MCP servers. "
                "Call this to assess data quality health before deciding whether to route a "
                "pipeline or alert an owner."
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
                        "description": "Look-back window in hours. Used as a label only.",
                    },
                },
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
        elif name == "explain_error":
            return await _tool_explain_error(arguments)
        elif name == "create_contract_draft":
            return await _tool_create_contract_draft(arguments)
        elif name == "get_quality_metrics":
            return await _tool_get_quality_metrics(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        return [types.TextContent(type="text", text=f"Error: {exc}")]


async def _tool_validate_record(args: dict) -> list[types.TextContent]:
    contract_name = args["contract"]
    record = args["record"]
    context = args.get("context")

    if _remote_client:
        payload = {"contract": contract_name, "record": record}
        if context:
            payload["context"] = context
        if args.get("agent_id"):
            payload["agent_id"] = args["agent_id"]
        if args.get("dry_run"):
            payload["dry_run"] = True
        resp = _remote_client.post("/api/v1/validate?allow_draft=true", json=payload)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Contract '{contract_name}' not found. Use list_contracts to see available contracts."
        }))]

    rules = contract.rules
    if context and hasattr(contract, "contexts") and context in contract.contexts:
        context_rules = contract.contexts[context]
        rules = context_rules if context_rules else rules

    result = _validate_record(record, rules, contract_name)
    result["contract"] = contract_name
    result["version"] = contract.version
    if contract.status == ContractStatus.DRAFT:
        result["draft_notice"] = _DRAFT_NOTICE
    result["governance_tip"] = _pick_governance_tip(rules, result.get("errors", []))
    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


async def _tool_validate_batch(args: dict) -> list[types.TextContent]:
    contract_name = args["contract"]
    records = args["records"]

    if _remote_client:
        payload = {"contract": contract_name, "records": records}
        context = args.get("context")
        if context:
            payload["context"] = context
        if args.get("agent_id"):
            payload["agent_id"] = args["agent_id"]
        if args.get("dry_run"):
            payload["dry_run"] = True
        resp = _remote_client.post("/api/v1/validate/batch?allow_draft=true", json=payload)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    if len(records) > 10000:
        return [types.TextContent(type="text", text=json.dumps({
            "error": "Maximum 10,000 records per batch call."
        }))]

    contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Contract '{contract_name}' not found."
        }))]

    result = _validate_batch(records, contract.rules, contract_name)
    result["contract"] = contract_name
    result["version"] = contract.version
    if contract.status == ContractStatus.DRAFT:
        result["draft_notice"] = _DRAFT_NOTICE
    result["governance_tip"] = _pick_governance_tip(
        contract.rules,
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

    if _remote_client:
        url = f"/api/v1/contracts/{name}"
        if version and version != "latest":
            url += f"?version={version}"
        resp = _remote_client.get(url)
        resp.raise_for_status()
        return [types.TextContent(type="text", text=resp.text)]

    contract = _registry.get(name, version)
    if not contract:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Contract '{name}' not found."
        }))]

    rules = [
        {
            "name": r.name,
            "type": r.type,
            "field": r.field,
            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
            "error_message": r.error_message,
            "description": r.description or "",
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
            return [types.TextContent(type="text", text=json.dumps({
                "error": f"Contract '{contract_name}' not found on remote API."
            }))]
        resp.raise_for_status()
        detail = resp.json()
        rules_raw = detail.get("rules", [])
        matching = [r for r in rules_raw if r["name"] == rule_name and r["field"] == field]
        if not matching:
            matching = [r for r in rules_raw if r["name"] == rule_name]
        if not matching:
            return [types.TextContent(type="text", text=json.dumps({
                "error": f"Rule '{rule_name}' not found in contract '{contract_name}'."
            }))]
        r = matching[0]
        # Reconstruct a minimal Rule object — constraint fields will be None
        # but explain_rule handles None gracefully via per-type fallbacks.
        rule_obj = _Rule(
            name=r["name"],
            type=r["type"],
            field=r["field"],
            error_message=r.get("error_message", ""),
        )
        info = explain_rule(rule_obj)
        return [types.TextContent(type="text", text=json.dumps({
            "contract": contract_name,
            "field": rule_obj.field,
            "rule": rule_obj.name,
            "rule_type": info["rule_type"],
            "explanation": info["explanation"],
            "valid_examples": info["valid_examples"],
            "invalid_examples": info["invalid_examples"],
            "constraint": info["constraint"],
        }))]

    contract = _registry.get(contract_name)
    if not contract:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Contract '{contract_name}' not found."
        }))]

    matching = [r for r in contract.rules if r.name == rule_name and r.field == field]
    if not matching:
        matching = [r for r in contract.rules if r.name == rule_name]
    if not matching:
        return [types.TextContent(type="text", text=json.dumps({
            "error": f"Rule '{rule_name}' not found on field '{field}' in contract '{contract_name}'."
        }))]

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
        return [types.TextContent(type="text", text=json.dumps({
            "error": (
                "created_by is required. Provide it as a parameter, or set the "
                "OPENDQV_AGENT_IDENTITY environment variable to your email or username. "
                "This value is recorded in the contract audit trail."
            )
        }))]

    # ACT-045-06: Rate limit — 10 draft creations per identity per hour.
    _now = _time.monotonic()
    _window = _draft_creation_log.setdefault(created_by, [])
    _draft_creation_log[created_by] = [t for t in _window if _now - t < _DRAFT_RATE_WINDOW]
    if len(_draft_creation_log[created_by]) >= _DRAFT_RATE_LIMIT:
        return [types.TextContent(type="text", text=json.dumps({
            "error": (
                f"Rate limit reached: '{created_by}' has created {_DRAFT_RATE_LIMIT} draft "
                f"contracts in the last hour. Wait before creating more, or contact an admin."
            )
        }))]

    # MCP_ prefix guard (also enforced inside ContractRegistry.create_draft)
    if not name.startswith("MCP_"):
        return [types.TextContent(type="text", text=json.dumps({
            "error": (
                f"Agent-created contracts must be named with the 'MCP_' prefix "
                f"(e.g. MCP_satellite_telemetry). Got: '{name}'"
            )
        }))]

    try:
        contract = _registry.create_draft(
            name=name,
            description=description,
            owner=owner,
            created_by=created_by,
            rules_data=rules_data,
        )
    except ValueError as exc:
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]

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
    governance_tip = (
        "Pass this contract's asset_id to your catalog MCP server to retrieve "
        "lineage and ownership context."
    )

    if _remote_client:
        resp = _remote_client.get("/api/v1/stats")
        resp.raise_for_status()
        summary = resp.json()
    else:
        summary = _stats.get_summary()

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
        pass_rate = round(total_pass / total_val, 4) if total_val > 0 else 1.0
        top_rules = [
            {"rule": f["rule"], "field": f["field"], "failures": f["count"]}
            for f in top_fields if f["contract"] == cname
        ][:5]
        if not _remote_client:
            try:
                trend = _quality_stats.get_trend(cname, days=1)
                if trend and trend[0].get("top_failing_rules"):
                    for rule, count in trend[0]["top_failing_rules"].items():
                        existing = next((tr for tr in top_rules if tr["rule"] == rule), None)
                        if existing:
                            existing["failures"] = max(existing["failures"], count)
                        else:
                            top_rules.append({"rule": rule, "field": "", "failures": count})
                    top_rules.sort(key=lambda x: x["failures"], reverse=True)
                    top_rules = top_rules[:5]
            except Exception:
                pass
        entry = {
            "contract": cname,
            "window_hours": window_hours,
            "total_validations": total_val,
            "pass_rate": pass_rate,
            "failed": total_fail,
            "top_failing_rules": top_rules,
            "latency": summary.get("latency", {}),
            "catalog_hint": f"marmot:assets/{cname}",
            "governance_tip": governance_tip if total_val > 0 else "No validation data recorded yet for this contract.",
        }
        if total_val > 0 or contract_name:
            result.append(entry)

    if contract_name and not result:
        result.append({
            "contract": contract_name,
            "window_hours": window_hours,
            "total_validations": 0,
            "pass_rate": 1.0,
            "failed": 0,
            "top_failing_rules": [],
            "catalog_hint": f"marmot:assets/{contract_name}",
            "governance_tip": "No validation data recorded yet for this contract.",
        })

    output = result[0] if (contract_name and result) else result
    return [types.TextContent(type="text", text=json.dumps(output, default=str))]


# ── Entry point ───────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
