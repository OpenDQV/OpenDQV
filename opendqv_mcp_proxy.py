#!/usr/bin/env python3
"""
OpenDQV MCP proxy — standalone stdio bridge for Claude Desktop.

Zero internal imports. Forwards MCP tool calls to an OpenDQV REST API over HTTP.
Designed to run inside Claude Desktop's sandboxed Python environment.

Usage in claude_desktop_config.json:
{
  "mcpServers": {
    "OpenDQV": {
      "command": "python3",
      "args": ["/path/to/opendqv_mcp_proxy.py"],
      "env": {
        "OPENDQV_API_URL": "http://192.168.1.160:8000",
        "OPENDQV_API_TOKEN": ""
      }
    }
  }
}
"""

import json
import os
import sys

try:
    import httpx
except ImportError:
    print(
        "opendqv_mcp_proxy requires httpx. Install: pip install httpx",
        file=sys.stderr,
    )
    sys.exit(1)

API_URL = os.environ.get("OPENDQV_API_URL", "")
if not API_URL:
    print(
        "OPENDQV_API_URL environment variable is required.\n"
        "Set it to your OpenDQV server address (e.g. http://192.168.1.160:8000)",
        file=sys.stderr,
    )
    sys.exit(1)
API_TOKEN = os.environ.get("OPENDQV_API_TOKEN", "")
AGENT_IDENTITY = os.environ.get("OPENDQV_AGENT_IDENTITY", "")

_client = httpx.Client(
    base_url=API_URL,
    timeout=30.0,
    headers={"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {},
)

# ── Tool definitions ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "validate_record",
        "description": (
            "Validate a single data record against a named contract. "
            "Returns {valid: bool, errors: [...]}. "
            "Call this before writing any record to a database or external API. "
            "If valid is false, call explain_error for each error to get "
            "plain-English remediation guidance before attempting to fix the record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name (e.g. 'customer', 'media_content'). Use list_contracts to discover available names."},
                "record": {"type": "object", "description": "The data record to validate as a JSON object."},
                "context": {"type": "string", "description": "Optional per-system context override (e.g. 'billing', 'kids_app'). Omit for default rules."},
                "agent_id": {"type": "string", "description": "Your agent name or service identity."},
                "dry_run": {"type": "boolean", "description": "If true, validate without recording results in quality metrics.", "default": False},
            },
            "required": ["contract", "record"],
        },
    },
    {
        "name": "validate_batch",
        "description": (
            "Validate up to 10,000 records in a single call. "
            "Returns per-record results and aggregate statistics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name to validate all records against."},
                "records": {"type": "array", "items": {"type": "object"}, "description": "List of data records. Maximum 10,000 per call."},
                "context": {"type": "string", "description": "Optional per-system context override."},
                "agent_id": {"type": "string", "description": "Your agent name or service identity."},
                "dry_run": {"type": "boolean", "description": "If true, validate without recording results.", "default": False},
            },
            "required": ["contract", "records"],
        },
    },
    {
        "name": "list_contracts",
        "description": (
            "List all available validation contracts with their names, statuses, and rule counts. "
            "Call this first to discover which contract applies to your data."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_contract",
        "description": (
            "Get full contract details including all field rules, valid value constraints, and owner."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name."},
                "version": {"type": "string", "description": "Contract version or 'latest' (default).", "default": "latest"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "explain_error",
        "description": (
            "Get a plain-English explanation of why a field failed a validation rule, "
            "including valid and invalid examples and concrete remediation guidance."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name."},
                "field": {"type": "string", "description": "The field that failed."},
                "rule": {"type": "string", "description": "The rule name that failed."},
            },
            "required": ["contract", "field", "rule"],
        },
    },
    {
        "name": "get_quality_metrics",
        "description": (
            "Return aggregate rejection metrics for one or all contracts. "
            "Includes pass_rate, failed count, top_failing_rules."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name. Omit for all contracts."},
                "window_hours": {"type": "integer", "default": 24, "description": "Look-back window in hours."},
                "agent_id": {"type": "string", "description": "Optional: filter to a specific source/agent."},
            },
        },
    },
    {
        "name": "get_rule_velocity",
        "description": (
            "Return time-series failure counts per rule — shows whether failures are "
            "accelerating or decelerating."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name."},
                "window_hours": {"type": "integer", "default": 24, "description": "Look-back window in hours."},
                "bucket_minutes": {"type": "integer", "default": 5, "description": "Bucket width in minutes."},
            },
            "required": ["contract"],
        },
    },
    {
        "name": "get_quality_trend",
        "description": (
            "Return daily pass-rate trend for a single contract over the last N days."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name."},
                "days": {"type": "integer", "default": 7, "description": "Look-back window in calendar days (1-90)."},
                "context": {"type": "string", "description": "Optional: filter to a specific context."},
            },
            "required": ["contract"],
        },
    },
    {
        "name": "create_contract_draft",
        "description": (
            "Create a DRAFT data contract. Contract name MUST start with 'MCP_'. "
            "Created in DRAFT status — usable immediately for testing but not active "
            "until a human approves it. The AI proposes, your team disposes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name. Must start with 'MCP_'."},
                "description": {"type": "string", "description": "What data this contract validates."},
                "owner": {"type": "string", "description": "Team or person responsible."},
                "created_by": {"type": "string", "description": "Identity of the human requesting creation. Falls back to OPENDQV_AGENT_IDENTITY env var."},
                "rules": {"type": "array", "items": {"type": "object"}, "description": "List of validation rules. Each requires: name, type, field."},
            },
            "required": ["name", "description", "owner", "rules"],
        },
    },
]

# ── Tool dispatch ────────────────────────────────────────────────────


def _call_tool(name: str, arguments: dict) -> str:
    """Route a tool call to the appropriate API endpoint and return JSON text."""
    try:
        if name == "validate_record":
            payload = {"contract": arguments["contract"], "record": arguments["record"]}
            for key in ("context", "agent_id", "dry_run"):
                if arguments.get(key):
                    payload[key] = arguments[key]
            resp = _client.post("/api/v1/validate?allow_draft=true", json=payload)
            resp.raise_for_status()
            return resp.text

        elif name == "validate_batch":
            payload = {"contract": arguments["contract"], "records": arguments["records"]}
            for key in ("context", "agent_id", "dry_run"):
                if arguments.get(key):
                    payload[key] = arguments[key]
            resp = _client.post("/api/v1/validate/batch?allow_draft=true", json=payload)
            resp.raise_for_status()
            return resp.text

        elif name == "list_contracts":
            resp = _client.get("/api/v1/contracts")
            resp.raise_for_status()
            return resp.text

        elif name == "get_contract":
            version = arguments.get("version", "latest")
            url = f"/api/v1/contracts/{arguments['name']}"
            if version != "latest":
                url += f"/at?version={version}"
            resp = _client.get(url)
            resp.raise_for_status()
            return resp.text

        elif name == "explain_error":
            contract = arguments["contract"]
            field = arguments["field"]
            rule = arguments["rule"]
            resp = _client.get(f"/api/v1/contracts/{contract}/explain/{field}/{rule}")
            resp.raise_for_status()
            return resp.text

        elif name == "get_quality_metrics":
            params = {}
            if arguments.get("contract"):
                params["contract"] = arguments["contract"]
            if arguments.get("window_hours"):
                params["window_hours"] = arguments["window_hours"]
            if arguments.get("agent_id"):
                params["agent_id"] = arguments["agent_id"]
            resp = _client.get("/api/v1/stats", params=params)
            resp.raise_for_status()
            return resp.text

        elif name == "get_rule_velocity":
            params = {
                "window_hours": arguments.get("window_hours", 24),
                "bucket_minutes": arguments.get("bucket_minutes", 5),
            }
            resp = _client.get(
                f"/api/v1/contracts/{arguments['contract']}/rule-velocity",
                params=params,
            )
            resp.raise_for_status()
            return resp.text

        elif name == "get_quality_trend":
            params = {"days": arguments.get("days", 7)}
            if arguments.get("context"):
                params["context"] = arguments["context"]
            resp = _client.get(
                f"/api/v1/contracts/{arguments['contract']}/quality-trend",
                params=params,
            )
            resp.raise_for_status()
            return resp.text

        elif name == "create_contract_draft":
            created_by = arguments.get("created_by") or AGENT_IDENTITY
            if not created_by:
                return json.dumps({"error": "created_by is required. Set OPENDQV_AGENT_IDENTITY or pass created_by."})
            payload = {
                "name": arguments["name"],
                "description": arguments["description"],
                "owner": arguments["owner"],
                "rules": arguments["rules"],
                "created_by": created_by,
            }
            resp = _client.post("/api/v1/contracts", json=payload)
            resp.raise_for_status()
            return resp.text

        else:
            return json.dumps({"error": f"Unknown tool: {name}"})

    except httpx.HTTPStatusError as exc:
        return json.dumps({"error": f"API returned {exc.response.status_code}: {exc.response.text}"})
    except httpx.ConnectError:
        return json.dumps({"error": f"Cannot connect to OpenDQV API at {API_URL}. Is the server running?"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── MCP JSON-RPC stdio loop ─────────────────────────────────────────


def _respond(result: dict) -> None:
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "initialize":
            _respond({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "OpenDQV", "version": "2.2.2"},
                    "capabilities": {"tools": {}},
                },
            })

        elif method == "notifications/initialized":
            pass  # no response needed

        elif method == "tools/list":
            _respond({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result_text = _call_tool(tool_name, arguments)
            _respond({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            })

        elif method == "ping":
            _respond({"jsonrpc": "2.0", "id": req_id, "result": {}})

        elif req_id is not None:
            _respond({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()
