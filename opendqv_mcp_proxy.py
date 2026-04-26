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
            "plain-English remediation guidance before attempting to fix the record. "
            "Pass `hash` (a content_hash from list_versions) to pin validation "
            "to a specific historical contract version — for reproducible audit. "
            "Safety: MCP validation always runs in dry-run mode — it never records "
            "results in production quality metrics. To run real validation that feeds "
            "monitoring dashboards, use the REST API, Python SDK, or CLI directly "
            "from a source system."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name (e.g. 'customer', 'media_content'). Use list_contracts to discover available names."},
                "record": {"type": "object", "description": "The data record to validate as a JSON object."},
                "context": {"type": "string", "description": "Optional per-system context override (e.g. 'billing', 'kids_app'). Omit for default rules."},
                "agent_id": {"type": "string", "description": "Your agent name or service identity."},
                "hash": {"type": "string", "description": "Optional content_hash from list_versions to pin validation to a historical contract version. Returns 404 if no matching history entry."},
            },
            "required": ["contract", "record"],
        },
    },
    {
        "name": "validate_batch",
        "description": (
            "Validate up to 10,000 records in a single call. "
            "Returns per-record results and aggregate statistics. "
            "Pass `hash` to pin the entire batch to a historical contract version. "
            "Safety: MCP validation always runs in dry-run mode — it never records "
            "results in production quality metrics. Use the REST API or SDK for real "
            "validation that feeds monitoring."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "contract": {"type": "string", "description": "Contract name to validate all records against."},
                "records": {"type": "array", "items": {"type": "object"}, "description": "List of data records. Maximum 10,000 per call."},
                "context": {"type": "string", "description": "Optional per-system context override."},
                "agent_id": {"type": "string", "description": "Your agent name or service identity."},
                "hash": {"type": "string", "description": "Optional content_hash from list_versions to pin all records to a historical contract version."},
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
            "Get full contract details including all field rules, valid value constraints, and owner. "
            "Pass `hash` (the contract_hash from a prior validate response) to retrieve the exact "
            "historical version that produced that hash — for point-in-time audit retrieval. "
            "Pass `context` (e.g. 'salesforce', 'kids_app') to return the effective rule set "
            "with that context's overrides resolved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name."},
                "version": {"type": "string", "description": "Contract version or 'latest' (default).", "default": "latest"},
                "hash": {"type": "string", "description": "Contract hash (from a prior validate response). Takes precedence over version."},
                "context": {"type": "string", "description": "Optional context to apply (e.g. 'salesforce', 'kids_app'). Returns the effective rule set with overrides resolved."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_versions",
        "description": (
            "List the version history for a contract — metadata only, no rule bodies. "
            "Returns version, status, entry_hash, content_hash, created_at, owner. "
            "Use this to drive a version picker, audit a lineage, or pin a "
            "downstream call to a specific historical hash via "
            "validate_record(hash=...). Lighter than get_contract for every version."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_contract_jsonschema",
        "description": (
            "Emit a JSON Schema (draft 2020-12) document for a contract. Use to "
            "bootstrap structural validation in a producer. Cross-field rules "
            "appear under `x-opendqv-unmapped` — OpenDQV still enforces them at "
            "validate time, but plain JSON Schema cannot express them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name."},
                "context": {"type": "string", "description": "Optional context override."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "compare_contracts",
        "description": (
            "Compare two historical snapshots of the same contract identified by "
            "entry_hash or content_hash (from list_versions). Returns rules_added, "
            "rules_removed, rules_changed, metadata_changed. Use for audit, change "
            "review, or drift analysis between two pinned hashes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Contract name."},
                "hash_a": {"type": "string", "description": "First snapshot hash."},
                "hash_b": {"type": "string", "description": "Second snapshot hash."},
            },
            "required": ["name", "hash_a", "hash_b"],
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
    # create_contract_draft — removed from proxy (no REST endpoint yet).
    # Good first issue for contributors: wrap _registry.create_draft() as POST /api/v1/contracts.
]

# ── Tool dispatch ────────────────────────────────────────────────────


def _call_tool(name: str, arguments: dict) -> str:
    """Route a tool call to the appropriate API endpoint and return JSON text."""
    try:
        if name == "validate_record":
            payload = {"contract": arguments["contract"], "record": arguments["record"]}
            for key in ("context", "agent_id", "hash"):
                if arguments.get(key):
                    payload[key] = arguments[key]
            # Safety: MCP validation is always dry-run. AI agents never write to
            # production quality metrics — that's reserved for source systems using
            # the REST API or SDK directly. This is a structural guarantee, not a
            # parameter: removing the knob is the safest default.
            payload["dry_run"] = True
            resp = _client.post("/api/v1/validate?allow_draft=true", json=payload)
            resp.raise_for_status()
            return resp.text

        elif name == "validate_batch":
            payload = {"contract": arguments["contract"], "records": arguments["records"]}
            for key in ("context", "agent_id", "hash"):
                if arguments.get(key):
                    payload[key] = arguments[key]
            # Safety: always dry-run — see validate_record handler.
            payload["dry_run"] = True
            resp = _client.post("/api/v1/validate/batch?allow_draft=true", json=payload)
            resp.raise_for_status()
            return resp.text

        elif name == "list_contracts":
            resp = _client.get("/api/v1/contracts")
            resp.raise_for_status()
            return resp.text

        elif name == "get_contract":
            version = arguments.get("version", "latest")
            contract_hash = arguments.get("hash")
            context_arg = arguments.get("context")
            url = f"/api/v1/contracts/{arguments['name']}"
            params = []
            if contract_hash:
                params.append(f"hash={contract_hash}")
            elif version != "latest":
                params.append(f"version={version}")
            if context_arg:
                params.append(f"context={context_arg}")
            if params:
                url += "?" + "&".join(params)
            resp = _client.get(url)
            resp.raise_for_status()
            return resp.text

        elif name == "list_versions":
            resp = _client.get(f"/api/v1/contracts/{arguments['name']}/versions")
            resp.raise_for_status()
            return resp.text

        elif name == "compare_contracts":
            resp = _client.get(
                f"/api/v1/contracts/{arguments['name']}/diff",
                params={"hash_a": arguments["hash_a"], "hash_b": arguments["hash_b"]},
            )
            resp.raise_for_status()
            return resp.text

        elif name == "get_contract_jsonschema":
            params = {}
            if arguments.get("context"):
                params["context"] = arguments["context"]
            resp = _client.get(
                f"/api/v1/contracts/{arguments['name']}/jsonschema",
                params=params,
            )
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
                "contract": arguments["contract"],
                "window_hours": arguments.get("window_hours", 24),
                "bucket_minutes": arguments.get("bucket_minutes", 5),
            }
            resp = _client.get("/api/v1/analytics/rule-velocity", params=params)
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
                    "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                    "serverInfo": {
                        "name": "OpenDQV",
                        "version": "2.3.12",
                        "icons": [
                            {
                                "src": "https://raw.githubusercontent.com/OpenDQV/OpenDQV/main/docs/assets/opendqv-favicon-128.png",
                                "mimeType": "image/png",
                                "sizes": ["128x128"],
                            },
                            {
                                "src": "data:image/png;base64,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",
                                "mimeType": "image/png",
                                "sizes": ["48x48"],
                            },
                        ],
                    },
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
