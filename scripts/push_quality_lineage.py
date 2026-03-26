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
        if (
            data.get("status") == "active"
            and data.get("asset_id")
            and data.get("catalog_visible", True)
        ):
            contracts.append(data)
    return contracts


def get_contract_stats(name: str) -> dict:
    """Fetch persistent (SQLite-backed) quality stats for a contract via the trend endpoint.

    Uses the 30-day quality trend rather than the in-memory /stats endpoint so that
    pass rates survive API restarts. Falls back to zero-counts on error.
    """
    try:
        r = httpx.get(
            f"{OPENDQV_URL}/api/v1/contracts/{name}/quality-trend",
            params={"days": 30},
            timeout=10,
        )
        r.raise_for_status()
        trend = r.json().get("points", [])
    except Exception:
        return {"total": 0, "passed": 0, "failed": 0, "top_failing_rules": {}}

    total = sum(d["total_records"] for d in trend)
    passed = sum(d["passed"] for d in trend)
    failed = sum(d["failed"] for d in trend)
    rule_counts: dict = {}
    for d in trend:
        for rule, count in d.get("top_failing_rules", {}).items():
            rule_counts[rule] = rule_counts.get(rule, 0) + count

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "top_failing_rules": rule_counts,
    }


def build_run_event(contract: dict, contract_stats: dict) -> dict:
    """Build an OpenLineage COMPLETE RunEvent for a single contract."""
    name = contract["name"]
    asset_id = contract["asset_id"]

    total_pass = contract_stats["passed"]
    total_fail = contract_stats["failed"]
    total = total_pass + total_fail
    pass_rate = round(total_pass / total * 100, 1) if total > 0 else None

    # Top failing rules (up to 5), sorted by count descending
    top_failing = sorted(
        contract_stats.get("top_failing_rules", {}).items(),
        key=lambda x: x[1], reverse=True,
    )
    top_failing = [rule for rule, _ in top_failing[:5]]

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
                    "contractOwnerTeam": contract.get("owner_team") or None,
                }
            },
        },
        "inputs": [],   # no stubs — upstream link via asset_id in run facets only
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


def stitch_consumer_lineage(
    client: httpx.Client, name: str, consumer_mrn: str
) -> tuple[int, str]:
    """Create edge: mrn://dataset/opendqv/{name} → consumer_mrn (type: downstream).

    The target consumer_mrn must already exist in Marmot's catalog — Marmot
    returns HTTP 500 if either node is unknown. Register the consumer asset in
    Marmot before adding it to downstream_consumers in the contract YAML.
    """
    asset_mrn = f"mrn://dataset/opendqv/{name}"
    r = client.post(
        f"{MARMOT_URL}/api/v1/lineage/direct",
        json={"source": asset_mrn, "target": consumer_mrn, "type": "downstream"},
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
    print("Reading quality stats from SQLite trend (30-day window) …\n")

    client = httpx.Client()
    ok = 0
    fail = 0

    for contract in contracts:
        name = contract["name"]
        contract_stats = get_contract_stats(name)
        event = build_run_event(contract, contract_stats)
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
            # Stitch downstream consumers
            consumers = contract.get("downstream_consumers", [])
            if consumers:
                c_ok = sum(
                    1 for c_mrn in consumers
                    if stitch_consumer_lineage(client, name, c_mrn)[0] in (200, 201, 409)
                )
                print(f"      consumers: {c_ok}/{len(consumers)} linked")
            ok += 1
        else:
            print(f"  ❌ {name:<40} HTTP {status}: {body[:120]}")
            fail += 1

    print(f"\n{ok}/{len(contracts)} events pushed to Marmot")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
