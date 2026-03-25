#!/usr/bin/env python3
"""
Marmot MCP stdio proxy.
Bridges Claude Desktop's stdio transport to Marmot's HTTP MCP endpoint.
"""
import os
import sys
import json
import httpx
from pathlib import Path

MARMOT_URL = os.environ.get("MARMOT_URL", "http://192.168.1.160:8080") + "/api/v1/mcp"
API_KEY = os.environ.get("MARMOT_API_KEY", "")


def _load_hidden_names() -> set[str]:
    """Return names of contracts where catalog_visible: false."""
    hidden: set[str] = set()
    contracts_dir = Path(
        os.environ.get("OPENDQV_CONTRACTS_DIR", str(Path(__file__).parent / "contracts"))
    )
    if not contracts_dir.exists():
        return hidden
    try:
        import yaml
    except ImportError:
        return hidden
    for path in contracts_dir.glob("*.yaml"):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            data = raw.get("contract", raw) if raw else {}
            if not data.get("catalog_visible", True):
                hidden.add(data.get("name", ""))
        except Exception:
            pass
    return hidden


_HIDDEN_NAMES = _load_hidden_names()


def _filter_discover_response(body: str) -> str:
    """Remove catalog_visible=false assets from discover_data result.

    Marmot's discover_data MCP tool currently returns a markdown summary string
    (not a JSON asset list), so this filter is a no-op for summary responses —
    it falls through safely when JSON parsing fails.

    Primary protection is in load_contracts() in push_quality_lineage.py:
    hidden contracts are never pushed to Marmot, so they don't appear in
    catalog queries. This filter is defence-in-depth for assets that were
    already pushed before catalog_visible was set to false, in case Marmot
    adds a structured asset-list response in a future version.
    """
    if not _HIDDEN_NAMES:
        return body
    try:
        parsed = json.loads(body)
        # MCP JSON-RPC shape: result.content[0].text contains the payload
        content_list = parsed.get("result", {}).get("content", [])
        for item in content_list:
            if item.get("type") == "text":
                try:
                    inner = json.loads(item["text"])
                    assets = inner.get("assets", [])
                    if assets:
                        inner["assets"] = [
                            a for a in assets if a.get("name") not in _HIDDEN_NAMES
                        ]
                        item["text"] = json.dumps(inner)
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
        return json.dumps(parsed)
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return body


def parse_sse(text: str) -> list[str]:
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                results.append(data)
    return results


def inject_provider_filter(request: dict) -> dict:
    """Inject providers=["opendqv"] into discover_data calls.

    Prevents OpenLineage job nodes and input stubs from appearing in
    catalog discovery — they exist for lineage only, not as catalog assets.
    Marmot v0.1.0 has no native 'hidden from catalog' flag (known gap,
    raised as contribution opportunity).
    """
    if (
        request.get("method") == "tools/call"
        and request.get("params", {}).get("name") == "discover_data"
    ):
        args = request["params"].setdefault("arguments", {})
        if not args.get("providers"):
            args["providers"] = ["opendqv"]
        if not args.get("limit"):
            args["limit"] = 100
    return request


def _is_discover_data(request: dict) -> bool:
    return (
        request.get("method") == "tools/call"
        and request.get("params", {}).get("name") == "discover_data"
    )


def main():
    client = httpx.Client(timeout=30.0)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        is_discover = _is_discover_data(request)
        request = inject_provider_filter(request)

        try:
            response = client.post(
                MARMOT_URL,
                json=request,
                headers={
                    "X-API-Key": API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            content = response.text.strip()

            if not content:
                continue  # notification acknowledged with no body — don't write empty line

            if content.startswith("event:") or content.startswith("data:"):
                for data in parse_sse(content):
                    if data:
                        if is_discover:
                            data = _filter_discover_response(data)
                        sys.stdout.write(data + "\n")
                        sys.stdout.flush()
            else:
                if is_discover:
                    content = _filter_discover_response(content)
                sys.stdout.write(content + "\n")
                sys.stdout.flush()

        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": request.get("id"),
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
