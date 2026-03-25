# LLM Integration Guide

> **API last verified:** `anthropic` SDK (claude-opus-4-6), `langchain v1.2.12`, `aiokafka v0.13.0`, `mcp v1.26.0` — 2026-03-13.
> Snippets are examples; pin your own versions in `requirements.txt`.

How to call OpenDQV from AI agents, LLM coding assistants, and agentic frameworks.

---

## Quick summary

OpenDQV's REST API is LLM-native by design: structured JSON in, structured JSON out, field-level errors with `field` / `rule` / `message` / `severity` keys that any agent can parse and act on. No special SDK needed — `curl` and a JSON body is enough to get started.

For richer integration, use the [MCP server](#3-mcp-server-claude-desktop--cursor).

---

## 1. Claude Tool Use (Anthropic SDK)

Add OpenDQV as a tool in any Claude API call. Claude will call it when it determines a record needs validating before writing.

```python
import anthropic
import requests

client = anthropic.Anthropic()

# Define the tool
tools = [
    {
        "name": "validate_record",
        "description": (
            "Validate a data record against a named contract before writing it to a database or API. "
            "Returns {valid: bool, errors: [...]}. Call this before any database write. "
            "If valid is false, do not write the record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract": {
                    "type": "string",
                    "description": "Contract name (e.g. 'customer', 'banking_transaction')"
                },
                "record": {
                    "type": "object",
                    "description": "The data record to validate"
                },
            },
            "required": ["contract", "record"],
        },
    },
    {
        "name": "explain_error",
        "description": (
            "Get a plain-English explanation of why a field failed a rule, with valid/invalid examples. "
            "Call this when validate_record returns errors to understand how to fix them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string"},
                "field": {"type": "string", "description": "The field that failed (from error.field)"},
                "rule": {"type": "string", "description": "The rule that failed (from error.rule)"},
            },
            "required": ["contract", "field", "rule"],
        },
    },
]

OPENDQV_URL = "http://localhost:8000"


def handle_tool_call(tool_name: str, tool_input: dict) -> str:
    if tool_name == "validate_record":
        resp = requests.post(f"{OPENDQV_URL}/api/v1/validate", json={
            "contract": tool_input["contract"],
            "record": tool_input["record"],
        })
        return resp.text
    elif tool_name == "explain_error":
        contract = tool_input["contract"]
        field = tool_input["field"]
        rule = tool_input["rule"]
        resp = requests.get(
            f"{OPENDQV_URL}/api/v1/contracts/{contract}/explain/{field}/{rule}"
        )
        return resp.text
    return '{"error": "unknown tool"}'


# Agentic loop — Claude validates and fixes until the record is clean or escalates
messages = [
    {
        "role": "user",
        "content": (
            "Validate this customer record and fix any errors before writing it: "
            '{"email": "not-an-email", "age": -5, "name": ""}'
        ),
    }
]

while True:
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        tools=tools,
        messages=messages,
    )

    if response.stop_reason == "end_turn":
        print(response.content[0].text)
        break

    tool_calls = [b for b in response.content if b.type == "tool_use"]
    if not tool_calls:
        break

    # Append assistant message and all tool results
    messages.append({"role": "assistant", "content": response.content})
    tool_results = []
    for tc in tool_calls:
        result = handle_tool_call(tc.name, tc.input)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tc.id,
            "content": result,
        })
    messages.append({"role": "user", "content": tool_results})
```

---

## 2. LangChain / LlamaIndex Tool

Wrap OpenDQV as a LangChain `@tool` function. The docstring is what the LLM reads when deciding whether to call the tool — write it to match the agent's intent.

```python
from langchain.tools import tool
import requests

OPENDQV_URL = "http://localhost:8000"


@tool
def validate_record(contract: str, record: dict) -> dict:
    """
    Validate a data record against a named contract before writing it to a database or API.

    Use this tool whenever you are about to write structured data to a system of record.
    Returns: {valid: bool, errors: [{field, rule, message, severity}], contract, version}

    If valid is False, inspect the errors list. Each error has:
    - field: which field failed
    - rule: which rule it failed
    - message: what went wrong

    Call explain_error for each failure to get remediation guidance.

    Args:
        contract: Contract name (use list_contracts to discover available names)
        record: The data record as a dict
    """
    resp = requests.post(
        f"{OPENDQV_URL}/api/v1/validate",
        json={"contract": contract, "record": record},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


@tool
def explain_error(contract: str, field: str, rule: str) -> dict:
    """
    Get a plain-English explanation of why a validation rule failed, with examples.

    Call this after validate_record returns errors. Pass the contract name, the
    field that failed, and the rule name. Returns an explanation and valid/invalid
    examples so you can correct the record.

    Args:
        contract: Contract name (from validate_record response)
        field: The field that failed (from error.field)
        rule: The rule name that failed (from error.rule)
    """
    resp = requests.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract}/explain/{field}/{rule}",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


@tool
def list_contracts() -> list:
    """
    List all available validation contracts with their names and rule counts.
    Call this to discover which contract to use before calling validate_record.
    Only 'active' contracts can be used for validation.
    """
    resp = requests.get(f"{OPENDQV_URL}/api/v1/contracts", timeout=10)
    resp.raise_for_status()
    return resp.json()


# Use with any LangChain agent:
# agent = initialize_agent(
#     tools=[validate_record, explain_error, list_contracts],
#     llm=...,
#     agent=AgentType.OPENAI_FUNCTIONS,
# )
```

---

## 3. MCP Server (Claude Desktop / Cursor)

The MCP server gives Claude Desktop and Cursor native access to all six OpenDQV tools.

**Start the server:**

```bash
# Install the MCP extra
pip install opendqv[mcp]

# Start the MCP server
python mcp_server.py
```

**Register in Claude Desktop** (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "OpenDQV": {
      "command": "python",
      "args": ["/path/to/OpenDQV/mcp_server.py"]
    }
  }
}
```

**Register in Cursor** (Settings → MCP → Add Server):
- Name: `OpenDQV`
- Command: `python /path/to/OpenDQV/mcp_server.py`

> **Path note:** The `args` path is machine-specific. Update it when cloning on a new machine.
> In Cursor you can use `${workspaceFolder}/mcp_server.py` if your project root is the OpenDQV repo.

Once registered, Claude and Cursor can call `validate_record`, `validate_batch`, `list_contracts`, `get_contract`, `explain_error`, and `create_contract_draft` as native tools — no API key or extra configuration needed.

**Verify your connection:**

After registering, confirm the server is reachable by asking Claude Desktop to call `list_contracts`. You should receive a list of available contracts (e.g. `customer`, `banking_transaction`, etc.).

- **Success:** A list of contracts is returned — the MCP server is connected and the tools are live.
- **Failure — no tools appear:** Claude Desktop does not show any OpenDQV tools in the tool picker. Check that `mcp_server.py` is running (`python mcp_server.py` in a separate terminal), that the path in `claude_desktop_config.json` is correct, and that you restarted Claude Desktop after editing the config.
- **Failure — empty response or error:** Run `python mcp_server.py` manually and check the output for import errors. Ensure `opendqv[mcp]` is installed in the same Python environment the config points to.

**Attribution (required for write tools):**

Before using `create_contract_draft`, set `OPENDQV_AGENT_IDENTITY` to your email address or username. This value is recorded in the contract audit trail as the proposing identity and cannot be changed after creation. Write tools are blocked if neither `created_by` nor `OPENDQV_AGENT_IDENTITY` is set.

```bash
export OPENDQV_AGENT_IDENTITY="your.email@example.com"
```

In Claude Desktop, add it to the `env` block in `claude_desktop_config.json`:

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

---

## 4. Cursor Rule / Claude Code CLAUDE.md

Add this instruction to `.cursorrules` or `CLAUDE.md` to make your AI coding assistant validate data before every write:

```
Before writing any record to a database, file, or external API:
1. Call the OpenDQV validation API: POST http://localhost:8000/api/v1/validate
   Body: {"contract": "<contract_name>", "record": <data_dict>}
2. If the response has "valid": false, do NOT write the record.
3. For each error in the errors list, call:
   GET http://localhost:8000/api/v1/contracts/<contract>/explain/<field>/<rule>
   to understand how to fix it.
4. Fix the record and re-validate before writing.

Available contracts: GET http://localhost:8000/api/v1/contracts
```

---

## 5. Error Remediation Loop

The full agentic loop: validate → explain → fix (Claude API call) → re-validate → write.
This is the pattern that prevents bad data from reaching your database without human intervention.

```python
import json
import requests

OPENDQV_URL = "http://localhost:8000"
MAX_RETRIES = 2


def fix_with_claude(record: dict, hints: list[dict]) -> dict:
    """
    Call Claude to correct a record that failed validation.

    hints — list of {field, message, explanation, valid_examples} dicts,
    one per failing rule, sourced directly from OpenDQV's explain_error endpoint.
    Claude receives the exact constraint details and concrete valid examples,
    making self-correction reliable rather than guesswork.
    """
    import anthropic
    claude = anthropic.Anthropic()

    hints_text = "\n".join(
        f"- field '{h['field']}': {h['explanation']} "
        f"(valid examples: {h['valid_examples']})"
        for h in hints
    )
    prompt = (
        f"The following JSON record failed data validation:\n"
        f"```json\n{json.dumps(record, indent=2)}\n```\n\n"
        f"Validation errors and how to fix them:\n{hints_text}\n\n"
        "Return ONLY the corrected JSON record with no explanation or markdown. "
        "Do not add, remove, or rename fields — only correct the values that failed."
    )
    response = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip accidental markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def validate_and_fix(contract: str, record: dict) -> dict:
    """
    Validate → explain → fix → re-validate loop.

    Returns:
        {"status": "ok",       "record": {...}, "attempts": int}  — clean record
        {"status": "escalate", "record": {...}, "errors": [...]}  — still failing; hand to human
    """
    for attempt in range(MAX_RETRIES + 1):
        result = requests.post(
            f"{OPENDQV_URL}/api/v1/validate",
            json={"contract": contract, "record": record},
        ).json()

        if result["valid"]:
            return {"status": "ok", "record": record, "attempts": attempt + 1}

        if attempt == MAX_RETRIES:
            return {
                "status": "escalate",
                "record": record,
                "errors": result["errors"],
                "message": f"Still failing after {MAX_RETRIES + 1} attempts — escalate to human",
            }

        # Fetch plain-English remediation hints for every failing rule
        hints = []
        for error in result["errors"]:
            explanation = requests.get(
                f"{OPENDQV_URL}/api/v1/contracts/{contract}"
                f"/explain/{error['field']}/{error['rule']}"
            ).json()
            hints.append({
                "field": error["field"],
                "message": error["message"],
                "explanation": explanation.get("explanation", ""),
                "valid_examples": explanation.get("valid_examples", []),
            })

        # Claude fixes the record using the constraint-aware hints
        record = fix_with_claude(record, hints)

    return {"status": "escalate", "record": record, "errors": result["errors"]}


# ── End-to-end example ────────────────────────────────────────────────────────
#
# Scenario: an upstream system sends amount as a currency string "£250.00".
# Without validation it silently writes a string into a numeric column.
# With this loop: caught on attempt 1, Claude strips "£" and casts to float,
# re-validates on attempt 2, writes clean.

if __name__ == "__main__":
    bad_record = {
        "transaction_id": "TXN-2026-0311-001",
        "account_number": "40158793",
        "transaction_date": "2026-03-11",
        "amount": "£250.00",          # ← upstream bug: string instead of float
        "currency": "GBP",
        "transaction_type": "transfer",
        "channel": "mobile",
        "merchant_id": "MCHT-00124",
        "merchant_category_code": "5411",
    }

    outcome = validate_and_fix("banking_transaction", bad_record)

    if outcome["status"] == "ok":
        print(f"✓ Clean after {outcome['attempts']} attempt(s) — writing to DB")
        print(json.dumps(outcome["record"], indent=2))
        # db.insert(outcome["record"])
    else:
        print("✗ Could not auto-fix — routing to human review queue")
        print(json.dumps(outcome["errors"], indent=2))
```

**What this loop does:**

1. `validate_record` — sends the record to OpenDQV; gets structured field-level errors
2. `explain_error` — fetches constraint-aware remediation hints per failing rule (the `valid_examples` list tells Claude exactly what a passing value looks like)
3. `fix_with_claude` — Claude receives the record + hints and returns a corrected JSON object; the prompt is deliberately constrained ("return ONLY the corrected JSON") to avoid hallucinated fields
4. Re-validate — the corrected record goes back through step 1; if still failing, repeat up to `MAX_RETRIES`
5. Escalate — after `MAX_RETRIES` the record is handed to a human review queue, not silently dropped or written corrupt

**Why `explain_error` makes the difference:**
Without it, the prompt to Claude is "here's a record, it failed validation, fix it." With it, Claude receives `"valid examples: [0.01, 0.02, 0.1]"` and `"common cause: failed type coercion from a string (e.g. '£0.01' instead of 0.01)"` — the model has the exact constraint and a likely cause. Fix rate on attempt 1 is dramatically higher.

---

## 6. Interpreting validation responses

Every validation response has the same shape:

```json
{
  "valid": false,
  "errors": [
    {
      "field": "amount",
      "rule": "amount_min",
      "message": "amount must be > 0",
      "severity": "error"
    }
  ],
  "warnings": [],
  "contract": "banking_transaction",
  "version": "1.0",
  "contract_hash": "sha256:abc123...",
  "engine_version": "1.0.0"
}
```

- `valid: false` means at least one `error`-severity rule failed. Do not write the record.
- `valid: true` with non-empty `warnings` means the record passed but has quality concerns. Write it, but review the warnings.
- `contract_hash` is the SHA-256 hash of the exact ruleset used. Store this alongside the record for point-in-time audit evidence.
- To understand any error: `GET /api/v1/contracts/{contract}/explain/{field}/{rule}`

**MCP tool responses include two additional fields:**

When calling `validate_record` or `validate_batch` through the MCP server (Claude Desktop, Cursor, or any MCP-compatible agent), the response includes two extra keys not present in the REST API:

```json
{
  "valid": true,
  "errors": [],
  "contract": "banking_transaction",
  "version": "1.0",
  "governance_tip": "Empty required fields cause silent NULL propagation into analytics — catching them at ingestion is 10× cheaper than tracing them downstream.",
  "draft_notice": "This contract is in DRAFT. Validate freely here, but activate it before relying on results in production."
}
```

- `governance_tip` — always present. A one-sentence explanation of *why* the most relevant rule type matters, aimed at developers who may not have a data governance background. The tip is chosen based on the first failing rule (or the first rule in the contract when all pass), so it surfaces the most actionable context for the current validation.
- `draft_notice` — present only when the contract is in DRAFT status. Signals to the agent that the contract has not yet been reviewed and activated, and that validation results should not be relied on in production. When this key is absent, the contract is ACTIVE.

Agents that read these fields can surface them directly to the user, helping vibe coders understand not just *whether* their data is valid but *why* the rules exist.

**Batch validation response shape:**

When calling `validate_batch` (`POST /api/v1/validate/batch`), the response uses a different shape from single-record validation:

```json
{
  "summary": {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "error_count": 1,
    "warning_count": 0
  },
  "results": [
    {"index": 0, "valid": true,  "errors": [], "warnings": []},
    {"index": 1, "valid": false, "errors": [{"field": "email", "rule": "email_format", "message": "invalid email", "severity": "error"}], "warnings": []},
    {"index": 2, "valid": true,  "errors": [], "warnings": []}
  ]
}
```

The top-level keys are `summary` and `results`. Each item in `results` has `index`, `valid`, `errors`, and `warnings`. There is no `per_row_errors` or `row_errors` key.

---

## 7. Using OpenDQV without MCP (pure HTTP)

The MCP server is optional. The core OpenDQV REST API has **no non-standard dependencies** — call it with `curl`, `httpx`, `requests`, or any HTTP client. No `mcp` package required.

This is the recommended integration path for:
- Environments with locked-down package mirrors or strict package governance
- Server-side code that calls OpenDQV directly (not via an LLM agent)
- Situations where the MCP package cannot be installed

```python
import httpx

OPENDQV_URL = "http://localhost:8000"

def validate(contract: str, record: dict) -> dict:
    resp = httpx.post(
        f"{OPENDQV_URL}/api/v1/validate",
        json={"contract": contract, "record": record},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def explain(contract: str, field: str, rule: str) -> dict:
    resp = httpx.get(
        f"{OPENDQV_URL}/api/v1/contracts/{contract}/explain/{field}/{rule}",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# Example: validate before writing
result = validate("banking_transaction", {"amount": 250.00, "currency": "GBP", ...})
if not result["valid"]:
    for error in result["errors"]:
        hint = explain(result["contract"], error["field"], error["rule"])
        print(hint["explanation"])
```

The `mcp` package is only needed if you run `mcp_server.py`. You do not need it to call the REST API.

---

## 8. Novel Domain Bootstrap (No Matching Contract)

When `list_contracts` returns no match for your data domain, use `create_contract_draft` to
bootstrap a new contract without leaving the agent context. The full workflow:

```python
import anthropic
import requests

client = anthropic.Anthropic()
OPENDQV_URL = "http://localhost:8000"

# Step 1: Discover contracts — find no match for satellite telemetry
contracts = requests.get(f"{OPENDQV_URL}/api/v1/contracts").json()
# → 29 contracts, none for satellite telemetry

# Step 2: Create a draft contract via the MCP tool (recommended for agents)
# NOTE: There is no generic POST /api/v1/contracts REST endpoint.
# Contracts can be created via:
#   1. The MCP tool `create_contract_draft` (for AI agents — recommended)
#   2. Import endpoints: POST /api/v1/import/csv, /import/gx, /import/odcs, etc.
#   3. Manual YAML authoring + POST /api/v1/contracts/reload
# The REST example below uses the MCP tool path via the client.
#
# Via MCP tool (shown in the "Via MCP tools" block further below):
#   create_contract_draft(name="MCP_satellite_telemetry", ...)
# → {"created": True, "name": "MCP_satellite_telemetry", "status": "draft", ...}

# Step 3: Validate your record against the draft immediately
telemetry_record = {
    "sensor_id": "SAT-7712-A",
    "timestamp": "2026-03-10T14:22:00Z",
    "altitude_km": 547.3,
    "velocity_ms": 7640.2,
    "signal_quality": 0.94,
    "status": "nominal",
}
result = requests.post(f"{OPENDQV_URL}/api/v1/validate?allow_draft=true", json={
    "contract": "MCP_satellite_telemetry",
    "record": telemetry_record,
}).json()
# → {"valid": true, "errors": [], ...}

# Step 4: Submit for human review when ready
review_resp = requests.post(
    f"{OPENDQV_URL}/api/v1/contracts/MCP_satellite_telemetry/1.0/submit-review",
    json={"proposed_by": "engineer@acme.example.com"},
).json()
# → contract transitions to REVIEW status

# Step 5: Human approves via workbench UI or REST
# POST /api/v1/contracts/MCP_satellite_telemetry/approve
# {"approved_by": "governance@acme.example.com"}
# → contract becomes ACTIVE, visible in shared library

# Step 6: Validate against the now-active contract (no allow_draft needed)
result = requests.post(f"{OPENDQV_URL}/api/v1/validate", json={
    "contract": "MCP_satellite_telemetry",
    "record": telemetry_record,
}).json()
# → {"valid": true, ...}
```

**Via MCP tools** (Claude Desktop / Cursor — no code required):

```
1. list_contracts()                    → no match for satellite telemetry
2. create_contract_draft(
     name="MCP_satellite_telemetry",
     description="...",
     owner="ACME Corp",
     created_by="engineer@acme.example.com",
     rules=[...]
   )                                   → draft created
3. validate_record("MCP_satellite_telemetry", record)  → test immediately
4. Human submits for review via workbench or REST API
5. Human approves → contract becomes ACTIVE
6. validate_record("MCP_satellite_telemetry", record)  → production-ready
```

**Key points:**
- `MCP_` prefix is required and enforced — you cannot name a contract without it via this tool
- Draft contracts are testable (use `?allow_draft=true` with the REST API; MCP tools test against drafts automatically)
- **When validating via MCP against a DRAFT contract, the response includes a `draft_notice` field** reminding the agent that the contract is not yet approved. This notice is absent from ACTIVE contract responses, so agents can use its presence as a reliable DRAFT signal without inspecting the `status` field themselves.
- Draft contracts do NOT appear as `active` in `list_contracts` — they cannot be used for production validation until approved
- The `source: "mcp"` field on the contract is immutable and set by the server — you cannot fake a `manual` source through the MCP tool
- **Rate limit:** `create_contract_draft` is capped at **10 drafts per hour per identity** (sliding window). Exceeding the limit returns an error message rather than creating the contract. The counter resets automatically as the window slides. In multi-process deployments the counter is per-process.
- **Field-completeness gate:** Before a draft can be promoted to ACTIVE, it must have a non-empty `description`, a non-empty `owner`, and at least one rule. Attempting activation without these returns `422`. This applies to all contracts regardless of source.
- **MCP review prerequisite:** Contracts with `source: "mcp"` cannot be directly activated — they must first be submitted for review via `POST /api/v1/contracts/{name}/{version}/submit-review`. Bypassing this step returns `403`. This is enforced even if the caller has the `approver` role.

---

## 9. Security Considerations

### Prompt injection risk: LOW (current read-only surface)

The current MCP tool surface is read-only except for `create_contract_draft`. The read-only
tools present a low-severity prompt injection path worth understanding.

**The attack path:**

`explain_error` takes three parameters — `contract`, `field`, and `rule` — derived from
validation results. Those results were computed from user-supplied data. An attacker who
controls the records being validated controls the field names and values that appear in
validation errors, which in turn influence what `explain_error` is called with.

**Why severity is LOW:**

`explain_error` generates responses from deterministic templates keyed on rule type (e.g.
`min`, `not_null`, `regex`). The templates do not execute arbitrary logic and do not echo
back user-supplied field values in a form that could carry executable instructions. The
response structure — `explanation`, `valid_examples`, `invalid_examples`, `constraint` — is
narrow and predictable. An attacker cannot inject a prompt into an `explain_error` response
by crafting a record payload.

**What to watch for as the surface expands:**

- If `contract` or rule `description` fields ever render user-supplied text verbatim in MCP
  responses, re-evaluate this rating.
- `create_contract_draft` accepts a `description` field. If this description is later served
  back via `get_contract` and an agent reads it as instructions, a malicious description could
  redirect the agent. Mitigation: treat contract descriptions as data, not instructions. When
  displaying contract descriptions to agents, wrap them: "The contract description is: [...]"
  rather than inserting them directly into the system prompt.
- Multi-agent pipelines where one agent creates a contract and a second agent reads it are
  higher risk than single-agent workflows. The creating agent could be compromised to write
  a malicious description that the reading agent executes.

**Current mitigations in place:**

- MCP_ prefix enforcement limits contract creation to a clearly labelled namespace
- The `source: "mcp"` field allows downstream systems to filter agent-created contracts
- Draft-only creation means agent-created contracts cannot enter the production validation
  path without a human reviewing the contract content (including its description)

---

## See Also

- [`mcp.md`](mcp.md) — MCP server setup, write guardrails, available tools
- [`importers.md`](importers.md) — Import contracts from GX, dbt, Soda, CSV, ODCS, OTel, NDC
- [`catalog_integration.md`](catalog_integration.md) — Catalog sync patterns for agents
- [`webhooks.md`](webhooks.md) — Event-driven integration
