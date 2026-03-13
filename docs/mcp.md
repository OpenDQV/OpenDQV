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

## Getting started

### 1. Start the MCP server

The MCP server runs as a separate process from the main API:

```bash
python mcp_server.py
```

By default it connects to the OpenDQV API at `http://localhost:8000`. Override with:
```bash
API_URL=http://your-api-host:8000 python mcp_server.py
```

### 2. Connect your agent

Point your MCP-compatible client at the server. For Claude Desktop, add to your MCP config:
```json
{
  "mcpServers": {
    "opendqv": {
      "command": "python",
      "args": ["/path/to/OpenDQV/mcp_server.py"]
    }
  }
}
```

For programmatic agents using an MCP client library, use the server's stdio transport.

### 3. Available tools

Once connected, the agent will see these tools:

| Tool | What it does |
|------|--------------|
| `list_contracts` | List all active contracts with name, version, status, rule count |
| `get_contract` | Get full contract detail including all rules |
| `validate_record` | Validate a single JSON record against a named contract |
| `validate_batch` | Validate multiple records in one call; returns per-row results and a summary |
| `explain_error` | Get a plain-English explanation of a rule failure with valid/invalid examples |

Write tools (only available when `ALLOW_MCP_WRITES=true`):

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
