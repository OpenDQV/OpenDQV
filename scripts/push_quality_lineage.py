#!/usr/bin/env python3
"""
push_quality_lineage.py — Push OpenDQV quality metrics to Marmot as lineage events.

For each active contract with an asset_id, posts an OpenLineage COMPLETE RunEvent
to Marmot's /api/v1/lineage endpoint. This populates lineage diagrams showing:
  - pass_rate and fail_count per contract
  - top failing rules
  - data flow: [source asset] → [opendqv:validate:<name>] → [Marmot asset]

Usage:
  MARMOT_TOKEN=<key> python scripts/push_quality_lineage.py

Environment:
  MARMOT_TOKEN  — Marmot API key (required)
  MARMOT_URL    — Marmot base URL (default: http://localhost:8080)
  OPENDQV_URL   — OpenDQV API URL (default: http://localhost:8000)
"""

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

MARMOT_URL = os.environ.get("MARMOT_URL", "http://localhost:8080")
MARMOT_TOKEN = os.environ.get("MARMOT_TOKEN", "")
OPENDQV_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000")

CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"


def load_contracts() -> list[dict]:
    """Load all active contracts that have asset_id set."""
    contracts = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        # Contracts are nested under a top-level "contract:" key
        data = raw.get("contract", raw)
        if data.get("status") == "active" and data.get("asset_id"):
            contracts.append(data)
    return contracts


def get_opendqv_stats() -> dict:
    """Fetch global quality stats from OpenDQV."""
    r = httpx.get(f"{OPENDQV_URL}/api/v1/stats", timeout=10)
    r.raise_for_status()
    return r.json()


def build_run_event(contract: dict, stats: dict) -> dict:
    """Build an OpenLineage COMPLETE RunEvent for a single contract."""
    name = contract["name"]
    asset_id = contract["asset_id"]

    # Aggregate pass/fail counts across all contexts for this contract
    total_pass = 0
    total_fail = 0
    for key, values in stats.get("by_contract", {}).items():
        if key.startswith(f"{name}:"):
            total_pass += values.get("pass", 0)
            total_fail += values.get("fail", 0)

    total = total_pass + total_fail
    pass_rate = round(total_pass / total * 100, 1) if total > 0 else None

    # Top failing rules for this contract (field:rule pairs, up to 5)
    top_failing = [
        f"{f['field']}:{f['rule']}"
        for f in stats.get("top_failing_fields", [])
        if f["contract"] == name
    ][:5]

    now = datetime.now(timezone.utc).isoformat()

    return {
        "eventType": "COMPLETE",
        "eventTime": now,
        "producer": "https://github.com/OpenDQV/OpenDQV",
        "schemaURL": "https://openlineage.io/spec/1-0-5/OpenLineage.json",
        "job": {
            "namespace": "opendqv",
            "name": f"validate:{name}",
        },
        "run": {
            "runId": str(uuid.uuid4()),
            "facets": {
                "opendqvQuality": {
                    "_producer": "https://github.com/OpenDQV/OpenDQV",
                    "_schemaURL": "https://github.com/OpenDQV/OpenDQV/schemas/quality-facet.json",
                    "contractName": name,
                    "passRate": pass_rate,
                    "totalRecords": total,
                    "passCount": total_pass,
                    "failCount": total_fail,
                    "topFailingRules": top_failing,
                    "assetId": asset_id,
                }
            },
        },
        "inputs": [
            {
                "namespace": "opendqv",
                "name": asset_id,
            }
        ],
        "outputs": [],  # handled by stitch_direct_lineage to avoid stub duplication
    }


def push_event(client: httpx.Client, event: dict) -> tuple[int, str]:
    """POST a single RunEvent to Marmot."""
    r = client.post(
        f"{MARMOT_URL}/api/v1/lineage",
        json=event,
        headers={
            "X-API-Key": MARMOT_TOKEN,
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    return r.status_code, r.text


def stitch_direct_lineage(client: httpx.Client, name: str) -> tuple[int, str]:
    """Create a direct edge from the OpenLineage job node to the existing Marmot asset.

    OpenLineage creates assets under mrn://dataset/openlineage/... but our pushed
    assets are mrn://dataset/opendqv/... — different MRNs, no automatic link.
    This call stitches them together explicitly.
    """
    job_mrn = f"mrn://job/openlineage/opendqv.validate:{name}"
    asset_mrn = f"mrn://dataset/opendqv/{name}"
    r = client.post(
        f"{MARMOT_URL}/api/v1/lineage/direct",
        json={"source": job_mrn, "target": asset_mrn, "type": "produces"},
        headers={
            "X-API-Key": MARMOT_TOKEN,
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    return r.status_code, r.text


def main() -> None:
    if not MARMOT_TOKEN:
        print("ERROR: MARMOT_TOKEN environment variable required", file=sys.stderr)
        print("  export MARMOT_TOKEN=<your-marmot-api-key>", file=sys.stderr)
        sys.exit(1)

    contracts = load_contracts()
    print(f"Contracts with asset_id: {len(contracts)}")

    stats = get_opendqv_stats()
    print(
        f"OpenDQV stats: {stats['total_validations']} validations, "
        f"pass_rate={stats['pass_rate']}%\n"
    )

    client = httpx.Client()
    ok = 0
    fail = 0

    for contract in contracts:
        name = contract["name"]
        event = build_run_event(contract, stats)
        status, body = push_event(client, event)

        if status == 200:
            qf = event["run"]["facets"]["opendqvQuality"]
            pass_rate = qf["passRate"]
            total = qf["totalRecords"]
            rate_str = (
                f"pass_rate={pass_rate}% ({total} records)"
                if total > 0
                else "no validations yet"
            )
            # Stitch job node → existing Marmot asset (MRN bridge)
            s_status, s_body = stitch_direct_lineage(client, name)
            stitch_ok = s_status in (200, 201, 409)  # 409 = already exists, fine
            stitch_str = "linked" if stitch_ok else f"stitch failed {s_status}"
            print(f"  ✅ {name:<40} {rate_str} | {stitch_str}")
            ok += 1
        else:
            print(f"  ❌ {name:<40} HTTP {status}: {body[:120]}")
            fail += 1

    print(f"\n{ok}/{len(contracts)} events pushed to Marmot")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
