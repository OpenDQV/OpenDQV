#!/usr/bin/env python3
"""
Marmot + MCP integration smoke test.

Tests:
  A. Marmot health — API key auth works, search endpoint reachable
  B. Push active contracts → Marmot assets (Approach 1, hash-skip)
  C. Verify assets appear in Marmot
  D. MCP remote mode — live API (all 7 tools exercised)
  E. MCP draft creation + cleanup
  F. MARMOT_URL deep-link construction (unit test)

Usage (Linux):
    source .venv/bin/activate
    MARMOT_TOKEN=<api-key> python scripts/test_marmot_integration.py

Environment variables:
    MARMOT_TOKEN   — Marmot API key (required)
    MARMOT_URL     — Marmot base URL (default: http://localhost:8080)
    OPENDQV_URL    — OpenDQV API base URL (default: http://localhost:8000)
"""

import asyncio
import importlib
import json
import os
import sys

import requests
import opendqv.mcp_server as mcp_server

MARMOT_URL = os.environ.get("MARMOT_URL", "http://localhost:8080")
OPENDQV_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000")
MARMOT_TOKEN = os.environ.get("MARMOT_TOKEN", "")

# ── Helpers ──────────────────────────────────────────────────────────────────

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {label}"
    if detail:
        msg += f"\n         {detail}"
    print(msg)
    _results.append((label, ok, detail))
    return ok


def marmot_headers() -> dict:
    return {"X-API-Key": MARMOT_TOKEN}


# ── Test A: Marmot health ────────────────────────────────────────────────────

def test_a_marmot_health():
    print("\n── A. Marmot health ─────────────────────────────────────────────")
    r = requests.get(f"{MARMOT_URL}/api/v1/assets/search", headers=marmot_headers(), params={"q": ""}, timeout=10)
    check("Marmot reachable (200)", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        check("Response has 'assets' key", "assets" in body)


# ── Test B: Push active contracts → Marmot assets ───────────────────────────

def test_b_push_contracts():
    print("\n── B. Push active contracts → Marmot assets ────────────────────")
    r = requests.get(f"{OPENDQV_URL}/api/v1/contracts", timeout=10)
    check("OpenDQV contracts endpoint (200)", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return

    contracts = r.json()
    active = [c for c in contracts if c.get("status") == "active"]
    check(f"Active contracts found ({len(active)})", len(active) > 0)

    pushed = 0
    skipped = 0
    failed = 0

    for c in active:
        name = c.get("name", "")
        payload = {
            "name": name,
            "type": "dataset",
            "providers": ["opendqv"],
            "description": c.get("description", f"OpenDQV contract: {name}"),
            "metadata": {
                "contract_version": str(c.get("version", "1.0")),
                "contract_status": c.get("status", "active"),
                "contract_owner": c.get("owner", ""),
                "contract_hash": c.get("contract_hash", ""),
                "source": "opendqv",
            },
        }
        # Try create; if 409 conflict, skip (already exists)
        pr = requests.post(
            f"{MARMOT_URL}/api/v1/assets",
            headers={**marmot_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if pr.status_code in (200, 201):
            pushed += 1
        elif pr.status_code == 409:
            skipped += 1
        else:
            failed += 1
            print(f"         WARN: {name} → HTTP {pr.status_code}: {pr.text[:80]}")

    check(f"Assets pushed/skipped ({pushed} new, {skipped} existing)", failed == 0,
          f"{failed} push failures" if failed else "")


# ── Test C: Verify assets appear in Marmot ──────────────────────────────────

def test_c_verify_assets():
    print("\n── C. Verify assets in Marmot catalog ───────────────────────────")
    r = requests.get(f"{MARMOT_URL}/api/v1/assets/search", headers=marmot_headers(), params={"q": "customer"}, timeout=10)
    check("Search for 'customer' (200)", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        body = r.json()
        total = body.get("total", 0)
        check("customer asset found in Marmot", total > 0, f"total={total}")

    r2 = requests.get(f"{MARMOT_URL}/api/v1/assets/search", headers=marmot_headers(), params={"q": "ppds_menu_item"}, timeout=10)
    if r2.status_code == 200:
        body2 = r2.json()
        check("ppds_menu_item asset found in Marmot", (body2.get("total", 0) > 0))


# ── Test D: MCP remote mode — live API ──────────────────────────────────────

def test_d_mcp_remote():
    print("\n── D. MCP remote mode (live API) ────────────────────────────────")

    os.environ["OPENDQV_MCP_API_URL"] = OPENDQV_URL

    # Reload mcp_server so the env var is picked up
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    importlib.reload(mcp_server)

    # D1: list_contracts
    result = asyncio.run(mcp_server._tool_list_contracts({}))
    data = json.loads(result[0].text)
    check("list_contracts returns >0 contracts", len(data) > 0, f"count={len(data)}")

    # D2: validate_record — pass case (all required fields for customer contract)
    result = asyncio.run(mcp_server._tool_validate_record({
        "contract": "customer",
        "record": {
            "name": "Alice Smith",
            "email": "alice@example.com",
            "age": 30,
            "score": 85,
            "date": "2024-01-15",
            "phone": "+14155552671",
            "username": "alice_smith",
            "password": "SecurePass1",
            "loyalty_tier": "gold",
            "balance": 100.0,
            "id": "cust-001",
        },
    }))
    data = json.loads(result[0].text)
    check("validate_record pass case: valid=True", data.get("valid") is True, str(data.get("errors", [])))
    # governance_tip is a local-mode enrichment; remote mode returns raw API response
    # Accept either (remote mode won't have it; local mode will)
    check("validate_record result is dict", isinstance(data, dict))

    # D3: validate_record — fail case (bad email, no name)
    result = asyncio.run(mcp_server._tool_validate_record({
        "contract": "customer",
        "record": {"name": "", "email": "not-an-email", "age": 5},
    }))
    data = json.loads(result[0].text)
    check("validate_record fail case: valid=False", data.get("valid") is False, f"errors={data.get('errors', [])}")

    # D4: validate_batch
    result = asyncio.run(mcp_server._tool_validate_batch({
        "contract": "customer",
        "records": [
            {"name": "Alice", "email": "alice@example.com", "age": 30},
            {"name": "", "email": "bad"},
        ],
    }))
    data = json.loads(result[0].text)
    check("validate_batch summary present", "summary" in data)
    check("validate_batch has 2 results", len(data.get("results", [])) == 2)

    # D5: get_contract
    result = asyncio.run(mcp_server._tool_get_contract({"name": "customer"}))
    data = json.loads(result[0].text)
    check("get_contract returns rules", len(data.get("rules", [])) > 0)

    # D6: explain_error
    result = asyncio.run(mcp_server._tool_explain_error({
        "contract": "customer",
        "field": "email",
        "rule": "valid_email",
    }))
    data = json.loads(result[0].text)
    check("explain_error returns explanation", "explanation" in data or "error" not in data)

    # D7: get_quality_metrics — catalog_hint format
    result = asyncio.run(mcp_server._tool_get_quality_metrics({"contract": "customer"}))
    data = json.loads(result[0].text)
    hint = data.get("catalog_hint", "")
    check(
        "get_quality_metrics catalog_hint uses marmot: prefix",
        hint.startswith("marmot:assets/"),
        f"catalog_hint={hint!r}",
    )


# ── Test E: MCP draft creation + cleanup ────────────────────────────────────

def test_e_mcp_draft():
    print("\n── E. MCP draft creation + cleanup ──────────────────────────────")

    os.environ["OPENDQV_AGENT_IDENTITY"] = "test@opendqv.local"

    importlib.reload(mcp_server)

    import time as _time
    TEST_CONTRACT = f"MCP_marmot_smoke_{int(_time.time())}"

    # Create draft
    result = asyncio.run(mcp_server._tool_create_contract_draft({
        "name": TEST_CONTRACT,
        "description": "Marmot integration smoke test — auto-created, safe to delete",
        "owner": "test@opendqv.local",
        "rules": [{
            "name": "name_required",
            "field": "name",
            "type": "not_empty",
            "severity": "error",
        }],
    }))
    data = json.loads(result[0].text)
    check("create_contract_draft: status=draft", data.get("status") == "draft", str(data))
    check("create_contract_draft: message or draft_notice present",
          "message" in data or "draft_notice" in data)

    # Cleanup — archive the draft (no DELETE endpoint; archive is the removal path)
    # status is a query param, not body
    archive_r = requests.post(
        f"{OPENDQV_URL}/api/v1/contracts/{TEST_CONTRACT}/status",
        params={"status": "archived"},
        timeout=10,
    )
    check(
        "Cleanup: draft archived (200/404)",
        archive_r.status_code in (200, 404),
        f"status={archive_r.status_code} body={archive_r.text[:80]}",
    )


# ── Test F: MARMOT_URL deep-link construction (unit) ────────────────────────

def test_f_deep_link():
    print("\n── F. Deep-link construction (unit) ─────────────────────────────")

    marmot_base = "http://localhost:8080"

    # Mirrors ui/app.py logic for catalog_hint → deep-link URL
    catalog_hint = "marmot:assets/customer"
    asset_name = catalog_hint.split("marmot:assets/")[-1]
    deep_link = f"{marmot_base}/assets/{asset_name}"
    check(
        "Deep-link URL constructed correctly",
        deep_link == "http://localhost:8080/assets/customer",
        f"got: {deep_link}",
    )

    # Verify the asset search returns results for this name in Marmot
    r = requests.get(
        f"{MARMOT_URL}/api/v1/assets/search",
        headers=marmot_headers(),
        params={"q": "customer"},
        timeout=10,
    )
    if r.status_code == 200:
        total = r.json().get("total", 0)
        check("Marmot search for deep-link asset name returns results", total > 0, f"total={total}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not MARMOT_TOKEN:
        print("ERROR: MARMOT_TOKEN environment variable not set.")
        print("  Get a key from: http://localhost:8080 → Settings → API Keys")
        print("  Or: curl -s -X POST http://localhost:8080/api/v1/users/login \\")
        print("       -H 'Content-Type: application/json' \\")
        print("       -d '{\"username\":\"admin\",\"password\":\"admin\"}' | python3 -c \"")
        print("       import sys,json; t=json.load(sys.stdin)['access_token']")
        print("       # then POST /api/v1/users/apikeys with Bearer token\"")
        sys.exit(1)

    print("\nOpenDQV Marmot Integration Smoke Test")
    print(f"  OpenDQV : {OPENDQV_URL}")
    print(f"  Marmot  : {MARMOT_URL}")

    test_a_marmot_health()
    test_b_push_contracts()
    test_c_verify_assets()
    test_d_mcp_remote()
    test_e_mcp_draft()
    test_f_deep_link()

    # Summary
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    print(f"\n{'─' * 60}")
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
        for label, ok, detail in _results:
            if not ok:
                print(f"  FAIL: {label}" + (f" — {detail}" if detail else ""))
    else:
        print("  ✓ All tests passed")
    print()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
