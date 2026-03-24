#!/usr/bin/env python3
"""
Marmot MCP stdio proxy.
Bridges Claude Desktop's stdio transport to Marmot's HTTP MCP endpoint.
"""
import sys
import json
import httpx

MARMOT_URL = "http://192.168.1.160:8080/api/v1/mcp"
API_KEY = "lozlsLIzgGyydZcDe9dp_d5M15kHhYhMnFx_KzQ8iBE="


def parse_sse(text: str) -> list[str]:
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                results.append(data)
    return results


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

            if content.startswith("event:") or content.startswith("data:"):
                for data in parse_sse(content):
                    sys.stdout.write(data + "\n")
                    sys.stdout.flush()
            else:
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
