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
MCP      :  Claude Desktop  →  (stdio subprocess)  →  opendqv/mcp_server.py  →  core/  →  contracts
```

The MCP server is spawned by Claude Desktop as a subprocess (stdio transport). It never listens on a port. MCP and FastAPI are peers, not a wrapper/wrappee relationship.

### Local mode (default)

No env var set. MCP reads contracts from the local filesystem and validates in-process. Works with no network dependency — the full Docker stack does not need to be running.

**When to use:** OSS users, laptop dev, demos, testing.

**Limitation:** MCP validation events are invisible to the monitoring UI. If you update contracts on a central server, the local copy may be stale.

### Remote / enterprise mode

Set `OPENDQV_MCP_API_URL` to point the MCP server at your central OpenDQV API. All tool calls are proxied to the API over HTTP:

```
Claude Desktop  →  (stdio)  →  opendqv.mcp_server (laptop)  →  (HTTP)  →  FastAPI (central server)
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
      "args": ["-m", "opendqv.mcp_server"],
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

**Note on `create_contract_draft` in remote mode:** The draft YAML is still written to the MCP server machine's local contracts directory (`OPENDQV_CONTRACTS_DIR`, default the bundled `opendqv/contracts/`), and the API is signalled to reload. For this to work, the MCP server and the API must share the same contracts directory (e.g., via a mounted volume or shared filesystem). If they do not share the directory, the draft will exist locally but will not be visible on the central server until manually synced.

## Getting started

### 1. Start the MCP server

The MCP server is self-contained — it reads contracts directly from disk and does not require the OpenDQV HTTP API or Docker stack to be running:

```bash
pip install "opendqv[mcp]"     # pulls in the mcp 2.x SDK
python -m opendqv.mcp_server
```

### 2. Connect your agent

Point your MCP-compatible client at the server. For Claude Desktop, add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "OpenDQV": {
      "command": "python",
      "args": ["-m", "opendqv.mcp_server"],
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
| `list_contracts` | List all contracts with name, version, status, rule count. Default includes active, draft and review; `include_all=true` adds archived. Only `active` contracts can be used for production validation. |
| `get_contract` | Get full contract detail including all rules. Each rule includes constraint fields: `allowed_values`, `pattern`, `min_value`, `max_value`, `min_length`, `max_length` (null when not applicable for that rule type). Accepts `version`, `context` and `hash`. |
| `list_versions` | Version history for a contract — metadata only, no rule bodies. Returns `version`, `status`, `entry_hash`, `content_hash`, `created_at`, `owner`. Use it to drive a version picker, audit a lineage, or pin a `content_hash` for `validate_record`. |
| `get_contract_jsonschema` | Emit a JSON Schema (draft 2020-12) document for a contract, for producer-side structural validation or typed code generation. Cross-field rules are not expressible in JSON Schema and are listed separately. Accepts `context` and `strict`. |
| `compare_contracts` | Diff two historical snapshots of the same contract. Call `list_versions` first, then pass any two `entry_hash` values as `hash_a` / `hash_b`. |
| `validate_record` | Validate a single JSON record against a named contract. Supports `agent_id` (attribution), `dry_run` (skip metrics), `context`, `record_id` (caller correlation ID echoed back and recorded in the audit trail) and `hash` (a `content_hash` from `list_versions` — pins validation to that historical version; 404 if no match). Errors carry `field`, `rule`, `message`, `severity`, `error_code`, and `counterpart_missing: true` when a cross-field rule failed because its counterpart field was absent or blank. The envelope carries `effective_rule_hash` and `governance_tip`. |
| `validate_batch` | Validate up to 10,000 records in one call; returns per-row results and a summary. Same `agent_id`, `dry_run`, `context`, `hash` params as `validate_record`. Fixed setup cost (~70ms) — for batches under ~70 records, individual `validate_record` calls are cheaper. |
| `explain_error` | Plain-English explanation of a rule failure: `rule_type`, `explanation`, `valid_examples`, `invalid_examples`, `constraint`, and `curated_message` (the contract author's `error_message`, when set). |

Observability tools:

| Tool | What it does |
|------|--------------|
| `get_quality_metrics` | Return rejection rates, top failing rules, and per-contract latency histogram (`avg_ms`, `p50_ms`, `p95_ms`, `p99_ms`) per contract. Accepts `window_hours` to scope stats, optional `agent_id` to filter metrics to a single data source (e.g. `"broadsign-prod"`), and `include_system`. Each entry includes `data_confidence` (`no_data` / `low` / `medium` / `high`), `confidence_note`, and a `catalog_hint` for chaining to Marmot or any catalog MCP server. |
| `get_quality_trend` | Return daily pass-rate trend for a single contract over the last N days. Shows whether quality is improving, declining, or stable, and which rules are driving the change. Each data point includes `total_records`, `passed`, `failed`, `pass_rate`, and `top_failing_rules`. Accepts `contract` (required), `days` (default 7, max 90), optional `context`, `by` and `include_system`. |
| `get_rule_velocity` | Return a time-series breakdown of rule failure counts bucketed by time interval. Useful for spotting which rules are spiking. Accepts `contract`, `window_hours`, and `bucket_minutes`. Returns a `series` dict keyed by rule name, each with a list of `{bucket, failures}` points. |
| `list_agents` | List the agents (source systems) that emitted validation traffic in the window: `agent_id`, totals, `pass_rate_pct`, `last_seen`, `is_system_agent`, sorted by volume. Accepts `window_hours`, `include_system`. |
| `list_audit_events` | List validation audit events (one row per `/validate` or `/validate/batch` call) with filters (`contract`, `contract_version`, `agent_id`, `caller_principal`, `context`, `mode`, `valid`, `since`, `until`) and cursor pagination (`cursor`, `limit`). For replay, dispute resolution, or regulatory evidence. |
| `get_audit_event` | Retrieve a single persisted audit event by `event_id` (the UUID v7 returned in the original validate response). 404 when no audit row exists — typically a `dry_run` call. |

Write tools:

| Tool | What it does |
|------|--------------|
| `create_contract_draft` | *(in-process server only — the standalone REST-bridge proxy does not expose it; a good first issue)* Propose a new DRAFT contract. The name must match the contract-name charset (letters, digits, hyphens, underscores; 1–100 chars) **and** start with `MCP_`; `created_by` (or `OPENDQV_AGENT_IDENTITY`) is required; review required before activation. |

## Write guardrails

Write access is disabled by default. This is intentional.

When write access is enabled, OpenDQV enforces strict guardrails to prevent agents from silently corrupting data contracts:

- **Agent-created contracts are always DRAFT.** They cannot be activated without a human review cycle (submit → approve).
- **ACTIVE and REVIEW contracts are immutable.** No agent can add, update, or delete rules on an ACTIVE or REVIEW contract — rule mutations return 409 (a REVIEW contract must be rejected back to DRAFT to edit). To modify an ACTIVE contract, create a new version with `POST /contracts/{name}/version` — this writes a new DRAFT as `{name}_v{version}.yaml` and rejects an existing version with 400.
- **All agent writes are attributed.** The `source` field is set to `"mcp"` on any contract or rule created by an agent. This attribution is permanent and auditable.

These guardrails exist because "trust is easier to build than to repair." A contract silently mutated by an agent is a trust failure. The design makes that impossible by construction.

## What agents can and cannot do

| Can do | Cannot do |
|--------|-----------|
| Read any contract | Activate a contract |
| Validate any record (single or batch) | Mutate an ACTIVE or REVIEW contract |
| Explain any validation error | Bypass the review workflow |
| Query quality metrics, trends, and rule velocity | Remove or weaken inherited rules |
| Propose new DRAFT contracts (if writes enabled) | |

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
  { "name": "social_media_age_compliance", "version": "1.0", "status": "active", "rule_count": 14,
    "description": "..." },
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
    { "field": "age", "rule": "age_minimum_13", "message": "Declared age must be 13 or above for platform access",
      "severity": "error", "error_code": "OPENDQV_RANGE_AGE_MINIMUM_13" },
    { "field": "age", "rule": "age_dob_consistent", "message": "Declared age is inconsistent with date of birth — possible data integrity issue or falsified age declaration",
      "severity": "error", "error_code": "OPENDQV_AGE_MATCH_AGE_DOB_CONSISTENT" },
    { "field": "dob", "rule": "dob_age_gate", "message": "Date of birth indicates user is under 13. Platform access denied regardless of declared age (UK Online Safety Act minimum age).",
      "severity": "error", "error_code": "OPENDQV_DATE_FORMAT_DOB_AGE_GATE" }
  ],
  "warnings": [
    { "field": "verified_identity", "rule": "verified_identity_advisory", "message": "Identity is self-declared (verified_identity=FALSE). Apply enhanced content restrictions per Ofcom age assurance guidance.",
      "severity": "warning", "error_code": "OPENDQV_LOOKUP_VERIFIED_IDENTITY_ADVISORY" }
  ],
  "contract": "social_media_age_compliance",
  "version": "1.0",
  "effective_rule_hash": "9ef29a7c…",
  "governance_tip": "Out-of-range values often signal upstream bugs; catching them early avoids expensive data recalls."
}
```

`age_dob_consistent` fires because 11 does not match a 2014 date of birth — a cross-field rule. Cross-field rules also fail when the counterpart field is absent or blank; those error entries carry `counterpart_missing: true`.

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
  "explanation": "The 'age' field must be a number between 13.0 and 150.0 (inclusive). Values below 13.0 or above 150.0 will fail. Check the value is numeric and within the allowed range.",
  "valid_examples": [13.0, 81.5, 150.0],
  "invalid_examples": [12.0, 151.0, null],
  "constraint": { "min": 13.0, "max": 150.0 },
  "curated_message": "Declared age must be 13 or above for platform access"
}
```

**Step 4 — validate a passing record (adult, verified)**
```
Tool: validate_record(
  contract = "social_media_age_compliance",
  record   = {
    "user_id": "USR-0089", "age": 26, "dob": "2000-06-15",
    "verified_identity": "TRUE",
    "verification_method": "GOVERNMENT_ID",
    "verification_timestamp": "2026-03-14T09:30:00Z"
  }
)
```
```json
{ "valid": true, "errors": [], "warnings": [], "contract": "social_media_age_compliance", "version": "1.0",
  "effective_rule_hash": "9ef29a7c…", "governance_tip": "..." }
```

> `age` must agree with `dob` as of today — a record born 2000-06-15 is 26 in 2026. Pass an age that is consistent with the date of birth or `age_dob_consistent` fails.

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
  "version": "1.0",
  "status": "draft",
  "source": "mcp",
  "proposed_by": "engineer@example.com",
  "rule_count": 3,
  "message": "Draft contract 'MCP_my_app_users' created with 3 rule(s). You can now call validate_record against it (draft status allows testing). ..."
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
    { "field": "email",    "rule": "email_format",       "message": "Must be a valid email address",
      "severity": "error", "error_code": "OPENDQV_REGEX_EMAIL_FORMAT" },
    { "field": "username", "rule": "username_min_length", "message": "Username must be at least 3 characters",
      "severity": "error", "error_code": "OPENDQV_MIN_LENGTH_USERNAME_MIN_LENGTH" }
  ],
  "warnings": [],
  "contract": "MCP_my_app_users",
  "version": "1.0",
  "effective_rule_hash": "7d425a72…",
  "draft_notice": "This contract is in DRAFT. Validate freely here, but activate it before relying on results in production.",
  "governance_tip": "Format rules stop malformed data from corrupting partner APIs and export pipelines."
}
```

**Step 3 — human approves → ACTIVE**

A human submits the contract for review (`POST /api/v1/contracts/{name}/{version}/submit-review`) and approves it (`POST /api/v1/contracts/{name}/{version}/approve`). While it sits in REVIEW it is immutable — reject it back to DRAFT to edit. Once ACTIVE, the contract appears in `list_contracts()` for all agents — no code change required on the agent side.

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

### Marmot proxy (`marmot_proxy.py`)

For Claude Desktop setups where Marmot's MCP endpoint is on a remote machine, `marmot_proxy.py` (repo root) acts as a stdio-to-HTTP bridge. It also applies two filters automatically:

1. **Provider filter** — injects `providers=["OpenDQV"]` into every `discover_data` call, so catalog discovery returns only OpenDQV assets (not OpenLineage job nodes or other providers).
2. **Visibility filter** — contracts with `catalog_visible: false` in their YAML are excluded from `discover_data` responses. `marmot_proxy.py` loads hidden contract names from the local contracts directory at startup (configurable via `OPENDQV_CONTRACTS_DIR`).

Claude Desktop config using the proxy:

```json
{
  "mcpServers": {
    "Marmot": {
      "command": "python3",
      "args": ["/path/to/OpenDQV/marmot_proxy.py"],
      "env": {
        "MARMOT_URL": "http://<linux-ip>:8080",
        "MARMOT_API_KEY": "<your-Marmot-api-key>",
        "OPENDQV_CONTRACTS_DIR": "/path/to/OpenDQV/opendqv/contracts"
      }
    }
  }
}
```

For a full walkthrough including webhook quality tagging and the lineage push script, see [`docs/marmot_integration.md`](marmot_integration.md).
