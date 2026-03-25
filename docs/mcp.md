# MCP Integration

OpenDQV exposes a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that allows AI agents to interact with data contracts programmatically.

## What is MCP?

MCP (Model Context Protocol) is an open standard that defines how AI agents discover and call tools exposed by external services. When an AI assistant (such as Claude, or a custom agent built on an LLM) connects to an MCP server, it receives a list of available tools with their parameter schemas. The agent can then call those tools as part of a conversation or automated workflow — without the agent needing to understand the underlying API.

Think of MCP as a structured handshake between an AI and a service: the service says "here is what I can do and what inputs I need," and the agent decides when and how to use those capabilities.

## Why does OpenDQV use MCP?

Data contracts are the source of truth for data quality expectations. AI agents working with data pipelines — whether checking data quality, generating validation code, or proposing new rules — need a reliable, structured way to read those contracts and understand validation outcomes.

OpenDQV's MCP server gives agents:

- **Contract discovery** — list all contracts and their status (DRAFT / REVIEW / ACTIVE)
- **Contract detail** — retrieve the full rule set for any contract, optionally for a specific context
- **Validation** — validate a data record against a named contract and receive a structured pass/fail result with field-level error detail
- **Write access (opt-in)** — agents can propose new contracts or add rules, subject to write guardrails (see below)

## Deployment models

OpenDQV's MCP server and FastAPI are **not** the same thing — they are two separate interfaces to the same core validation logic:

```
FastAPI  :  HTTP client  →  FastAPI (:8000)  →  core/  →  contracts DB
MCP      :  Claude Desktop  →  (stdio subprocess)  →  mcp_server.py  →  core/  →  contracts
```

The MCP server is spawned by Claude Desktop as a subprocess (stdio transport). It never listens on a port. MCP and FastAPI are peers, not a wrapper/wrappee relationship.

### Local mode (default)

No env var set. MCP reads contracts from the local filesystem and validates in-process. Works with no network dependency — the full Docker stack does not need to be running.

**When to use:** OSS users, laptop dev, demos, testing.

**Limitation:** MCP validation events are invisible to the monitoring UI. If you update contracts on a central server, the local copy may be stale.

### Remote / enterprise mode

Set `OPENDQV_MCP_API_URL` to point the MCP server at your central OpenDQV API. All tool calls are proxied to the API over HTTP:

```
Claude Desktop  →  (stdio)  →  mcp_server.py (laptop)  →  (HTTP)  →  FastAPI (central server)
                                                                             ↑
                                                                  UI monitoring sees ALL events
```

**Benefits:**
- All agent validation events flow through the central API → visible in the monitoring UI
- Agents always see the live contract version — no stale local copy
- Central audit trail covers both direct API calls and MCP-originated agent calls
- `create_contract_draft` triggers a reload on the central API

**Config:**
```json
{
  "mcpServers": {
    "OpenDQV": {
      "command": "python",
      "args": ["/path/to/OpenDQV/mcp_server.py"],
      "env": {
        "OPENDQV_AGENT_IDENTITY": "your.email@example.com",
        "OPENDQV_MCP_API_URL": "https://opendqv.internal.company.com",
        "OPENDQV_MCP_TOKEN": "your-pat-token"
      }
    }
  }
}
```

The MCP transport (stdio) does not change — only the backend. Claude Desktop config is identical; just add the two env vars. `OPENDQV_MCP_TOKEN` is a Personal Access Token issued by the central API (`POST /api/v1/tokens/generate`).

**Note on `create_contract_draft` in remote mode:** The draft YAML is still written to the MCP server machine's local `contracts/` directory, and the API is signalled to reload. For this to work, the MCP server and the API must share the same contracts directory (e.g., via a mounted volume or shared filesystem). If they do not share the directory, the draft will exist locally but will not be visible on the central server until manually synced.

## Getting started

### 1. Start the MCP server

The MCP server is self-contained — it reads contracts directly from disk and does not require the OpenDQV HTTP API or Docker stack to be running:

```bash
python mcp_server.py
```

### 2. Connect your agent

Point your MCP-compatible client at the server. For Claude Desktop, add to `~/.claude/claude_desktop_config.json`:
```json
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
```

> ⚠️ **Restart Claude Desktop after editing this file.** Changes to `claude_desktop_config.json` are only picked up at startup.

`OPENDQV_AGENT_IDENTITY` is required to use `create_contract_draft` — it is recorded in the contract audit trail as the proposing identity.

For programmatic agents using an MCP client library, use the server's stdio transport.

### 3. Available tools

Once connected, the agent will see these tools:

| Tool | What it does |
|------|--------------|
| `list_contracts` | List all active contracts with name, version, status, rule count |
| `get_contract` | Get full contract detail including all rules. Each rule includes constraint fields: `allowed_values`, `pattern`, `min_value`, `max_value`, `min_length`, `max_length` (null when not applicable for that rule type). |
| `validate_record` | Validate a single JSON record against a named contract. Supports `agent_id` (attribution), `dry_run` (skip metrics), `context`. Returns `latency_ms` and `suggested_fix` inline on errors. |
| `validate_batch` | Validate multiple records in one call; returns per-row results and a summary. Same `agent_id`, `dry_run`, `context` params as `validate_record`. Returns `latency_ms` on the batch envelope. |
| `explain_error` | Get a plain-English explanation of a rule failure with valid/invalid examples |

Observability tools:

| Tool | What it does |
|------|--------------|
| `get_quality_metrics` | Return rejection rates, top failing rules, and latency histogram (`avg_ms`, `p50_ms`, `p95_ms`, `p99_ms`) per contract. Accepts `window_hours` (integer) to scope stats to the last N hours — only validations within the window are counted. Each entry includes `data_confidence` (`no_data` / `low` / `medium` / `high`) and `confidence_note` (plain-English caution when fewer than 10 validations have been recorded). Includes a `catalog_hint` for chaining to Marmot or any catalog MCP server. |

Write tools:

| Tool | What it does |
|------|--------------|
| `create_contract_draft` | Propose a new DRAFT contract (requires `MCP_` prefix; review required before activation) |

## Write guardrails

Write access is disabled by default. This is intentional.

When write access is enabled, OpenDQV enforces strict guardrails to prevent agents from silently corrupting data contracts:

- **Agent-created contracts are always DRAFT.** They cannot be activated without a human review cycle (submit → approve).
- **ACTIVE contracts are immutable.** No agent can add, update, or delete rules on an ACTIVE contract. To modify an ACTIVE contract, fork it via `POST /contracts/{name}/version` — this creates a new DRAFT at the next version number.
- **All agent writes are attributed.** The `source` field is set to `"mcp"` on any contract or rule created by an agent. This attribution is permanent and auditable.

These guardrails exist because "trust is cheaper to build than to repair." A contract silently mutated by an agent is a trust failure. The design makes that impossible by construction.

## What agents can and cannot do

| Can do | Cannot do |
|--------|-----------|
| Read any contract | Activate a contract |
| Validate any record (single or batch) | Mutate an ACTIVE contract |
| Explain any validation error | Bypass the review workflow |
| Propose new DRAFT contracts (if writes enabled) | Remove or weaken inherited rules |

For more detail on the write guardrail threat model, see `docs/security/threat_model.md`.

For Claude tool use, LangChain, the error remediation loop pattern, and agent security considerations, see [`docs/llm_integration.md`](llm_integration.md).

## Vibe coding walkthrough

This section shows two end-to-end scenarios an AI agent would execute using the MCP tools. Both scenarios work with no Docker stack — the MCP server is self-contained.

### Scenario A — validate against an existing contract

Use `social_media_age_compliance` (14 rules, ACTIVE) to validate a registration record:

**Step 1 — discover contracts**
```
Tool: list_contracts()
```
```json
[
  { "name": "social_media_age_compliance", "status": "active", "rule_count": 14,
    "description": "Social media user age compliance — age gate, DOB format, and identity verification audit trail" },
  ...
]
```

**Step 2 — validate a record (minor → fail)**
```
Tool: validate_record(
  contract = "social_media_age_compliance",
  record   = { "user_id": "USR-0001", "age": 11, "dob": "2014-08-20", "verified_identity": "FALSE" }
)
```
```json
{
  "valid": false,
  "errors": [
    { "field": "age",  "rule": "age_minimum_13",  "message": "Declared age must be 13 or above for platform access" },
    { "field": "dob",  "rule": "dob_age_gate",    "message": "Date of birth indicates user is under 13. Platform access denied regardless of declared age (UK Online Safety Act minimum age)." }
  ],
  "contract": "social_media_age_compliance",
  "version": "1.0.0-draft.280"
}
```

**Step 3 — explain a failing rule**
```
Tool: explain_error(
  contract = "social_media_age_compliance",
  field    = "age",
  rule     = "age_minimum_13"
)
```
```json
{
  "contract": "social_media_age_compliance",
  "field": "age",
  "rule": "age_minimum_13",
  "rule_type": "range",
  "explanation": "age must be between 13.0 and 150.0",
  "valid_examples": [13, 25, 150],
  "invalid_examples": [12, 0, -1],
  "constraint": { "min": 13.0, "max": 150.0 }
}
```

**Step 4 — validate a passing record (adult, verified)**
```
Tool: validate_record(
  contract = "social_media_age_compliance",
  record   = {
    "user_id": "USR-0089", "age": 25, "dob": "2000-06-15",
    "verified_identity": "TRUE",
    "verification_method": "GOVERNMENT_ID",
    "verification_timestamp": "2026-03-14T09:30:00Z"
  }
)
```
```json
{ "valid": true, "errors": [], "warnings": [], "contract": "social_media_age_compliance" }
```

---

### Scenario B — create your own contract on the fly

No matching contract for your domain? Create one as a DRAFT and validate against it immediately — no deployment step required.

**Step 1 — create the draft**
```
Tool: create_contract_draft(
  name        = "MCP_my_app_users",
  description = "Basic user registration validation for MyApp",
  owner       = "Platform Engineering",
  created_by  = "engineer@example.com",
  rules       = [
    { "name": "email_required", "type": "not_empty", "field": "email" },
    { "name": "email_format",   "type": "regex",     "field": "email",
      "pattern": "^[^@]+@[^@]+\\.[^@]+$",
      "error_message": "Must be a valid email address" },
    { "name": "username_min_length", "type": "min_length", "field": "username",
      "min_length": 3, "error_message": "Username must be at least 3 characters" }
  ]
)
```
```json
{
  "created": true,
  "name": "MCP_my_app_users",
  "version": "1.0.0",
  "status": "draft",
  "source": "mcp",
  "proposed_by": "engineer@example.com",
  "rule_count": 3,
  "message": "Draft contract 'MCP_my_app_users' created with 3 rule(s). You can now call validate_record against it (draft status allows testing)."
}
```

**Step 2 — validate immediately (DRAFT is testable)**
```
Tool: validate_record(
  contract = "MCP_my_app_users",
  record   = { "email": "not-an-email", "username": "x" }
)
```
```json
{
  "valid": false,
  "errors": [
    { "field": "email",    "rule": "email_format",        "message": "Must be a valid email address" },
    { "field": "username", "rule": "username_min_length",  "message": "Username must be at least 3 characters" }
  ],
  "draft_notice": "This contract is in DRAFT. Validate freely here, but activate it before relying on results in production.",
  "contract": "MCP_my_app_users"
}
```

**Step 3 — human approves → ACTIVE**

A human submits the contract for review via the API and approves it. Once ACTIVE, the contract appears in `list_contracts()` for all agents — no code change required on the agent side.

> The contract name (`MCP_my_app_users`) stays the same across DRAFT → REVIEW → ACTIVE, so agent code written against the DRAFT works unchanged in production.

---

## MCP composition — chaining OpenDQV with a catalog

Because both OpenDQV and catalog tools like [Marmot](https://github.com/marmotdata/marmot) expose MCP servers, an agent can compose them natively. The `get_quality_metrics` tool includes a `catalog_hint` field that tells agents which asset to look up next.

### How it works

```
Agent
  ├─ OpenDQV:get_quality_metrics("customer")
  │     → { pass_rate: 0.94, failed: 85, catalog_hint: "Marmot:assets/customer" }
  └─ Marmot:get_asset("customer")          ← agent uses catalog_hint
        → { owner: "...", lineage: [...], downstream_assets: [...] }
```

No integration code required. Both servers are already running. The agent reads the `catalog_hint` and calls the appropriate catalog tool.

### Example agent prompt

```
You have access to two MCP servers: OpenDQV and Marmot.

1. Call OpenDQV:get_quality_metrics for the "customer" contract.
2. Read the catalog_hint field from the result.
3. If pass_rate is below 0.95, call Marmot:get_asset using the asset name
   from catalog_hint to find the asset owner.
4. Summarise: which rules are failing, who owns the asset, and what
   downstream assets are at risk.
```

### What each server contributes

| Layer | Tool | Contributes |
|-------|------|-------------|
| Layer 1 — write-time | `OpenDQV:get_quality_metrics` | Pass rate, failing rules, rejection counts |
| Layer 2 — catalog | `Marmot:get_asset` (or DataHub / Atlan equivalent) | Owner, lineage, downstream assets |

OpenDQV stays pure enforcement. The catalog stays pure governance. The agent composes them at runtime.

### Marmot proxy (`Marmot_proxy.py`)

For Claude Desktop setups where Marmot's MCP endpoint is on a remote machine, `Marmot_proxy.py` acts as a stdio-to-HTTP bridge. It also applies two filters automatically:

1. **Provider filter** — injects `providers=["OpenDQV"]` into every `discover_data` call, so catalog discovery returns only OpenDQV assets (not OpenLineage job nodes or other providers).
2. **Visibility filter** — contracts with `catalog_visible: false` in their YAML are excluded from `discover_data` responses. `Marmot_proxy.py` loads hidden contract names from the local `contracts/` directory at startup (configurable via `OPENDQV_CONTRACTS_DIR`).

Claude Desktop config using the proxy:

```json
{
  "mcpServers": {
    "Marmot": {
      "command": "python3",
      "args": ["/path/to/OpenDQV/Marmot_proxy.py"],
      "env": {
        "MARMOT_URL": "http://<linux-ip>:8080",
        "MARMOT_API_KEY": "<your-Marmot-api-key>",
        "OPENDQV_CONTRACTS_DIR": "/path/to/OpenDQV/contracts"
      }
    }
  }
}
```

For a full walkthrough including webhook quality tagging and the lineage push script, see [`docs/marmot_integration.md`](marmot_integration.md).
