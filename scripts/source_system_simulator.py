#!/usr/bin/env python3
"""
OpenDQV Source System Simulator

Simulates how a real source system (Salesforce, Databricks, Kafka consumer,
Airflow task, Spark job, etc.) calls the OpenDQV validation API. Designed to:

  - Prove integrations work without a real Salesforce/Databricks account
  - Regression-test API behaviour in CI
  - Demo the pass/fail flow against any of the 16 domain contracts
  - Validate auth, network path, and response parsing end-to-end

Uses sample records from examples/starter_contracts/sample_records/.
Injects errors at a configurable rate by mutating valid record fields.

Usage:
    # Single-record validation, 20% error injection
    python scripts/source_system_simulator.py \\
      --contract banking \\
      --records 50 \\
      --error-rate 0.2 \\
      --api-url http://localhost:8000 \\
      --token $OPENDQV_TOKEN

    # Batch validation (simulates Kafka consumer / Spark foreachBatch)
    python scripts/source_system_simulator.py \\
      --contract healthcare \\
      --records 100 \\
      --error-rate 0.15 \\
      --batch-size 10 \\
      --api-url http://localhost:8000

    # Label the source system (mirrors production log output)
    python scripts/source_system_simulator.py \\
      --source-system "salesforce-prod" \\
      --contract salesforce_contact \\
      --records 20 \\
      --error-rate 0.3

    # No-auth mode (AUTH_MODE=open server)
    python scripts/source_system_simulator.py \\
      --contract retail --records 10

    # List available contracts
    python scripts/source_system_simulator.py --list-contracts

    # Sweep all 16 domain contracts
    for c in banking energy financial_services healthcare insurance logistics \\
              manufacturing pharma public_sector real_estate retail \\
              social_media_age_compliance technology telecoms travel universal; do
      python scripts/source_system_simulator.py --contract $c --records 10
    done
"""

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print(
        "ERROR: httpx is required. Install it with: pip install httpx",
        file=sys.stderr,
    )
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent.parent
SAMPLE_RECORDS_DIR = PROJECT_ROOT / "examples" / "starter_contracts" / "sample_records"

# ── Inline sample records for Salesforce contracts ────────────────────────────
# These are used when no sample_records/{contract}.json file exists.
# Mirrors the field structure of the salesforce_contact / salesforce_lead contracts.

_SF_CONTACT_SAMPLES = [
    {
        "_comment": "VALID — complete contact record",
        "FirstName": "Alice",
        "LastName": "Nguyen",
        "Email": "alice.nguyen@example.com",
        "Phone": "+447911123456",
        "Title": "Data Engineer",
        "Department": "Engineering",
        "AccountId": "001XXXXXXXXXXXX1",
        "MailingCountry": "GB",
    },
    {
        "_comment": "VALID — international contact, optional fields null",
        "FirstName": "Marco",
        "LastName": "Bellini",
        "Email": "m.bellini@acmecorp.it",
        "Phone": "+393312345678",
        "Title": "Head of Data",
        "Department": "Analytics",
        "AccountId": "001XXXXXXXXXXXX2",
        "MailingCountry": "IT",
    },
    {
        "_comment": "VALID — minimal required fields only",
        "FirstName": "Roisin",
        "LastName": "Doherty",
        "Email": "roisin.doherty@utility.ie",
        "Phone": None,
        "Title": None,
        "Department": None,
        "AccountId": None,
        "MailingCountry": "IE",
    },
    {
        "_comment": "VALID — US contact with all fields",
        "FirstName": "Sofia",
        "LastName": "Martinez",
        "Email": "sofia.martinez@enterprise.us",
        "Phone": "+12025551234",
        "Title": "VP Engineering",
        "Department": "Technology",
        "AccountId": "001XXXXXXXXXXXX3",
        "MailingCountry": "US",
    },
    {
        "_comment": "INVALID — Email field is null (email_required rule fails)",
        "FirstName": "James",
        "LastName": "Okonkwo",
        "Email": None,
        "Phone": "+447900000001",
        "Title": "Analyst",
        "Department": "Finance",
        "AccountId": None,
        "MailingCountry": "GB",
    },
    {
        "_comment": "INVALID — LastName empty, Email bad format",
        "FirstName": "Li",
        "LastName": "",
        "Email": "not-an-email",
        "Phone": "+447900000002",
        "Title": None,
        "Department": None,
        "AccountId": None,
        "MailingCountry": "GB",
    },
]

_SF_LEAD_SAMPLES = [
    {
        "_comment": "VALID — full lead record",
        "FirstName": "Sophie",
        "LastName": "Chen",
        "Email": "sophie.chen@prospect.com",
        "Phone": "+447700000001",
        "Company": "Acme Analytics Ltd",
        "LeadSource": "Web",
        "Status": "Open",
        "Industry": "Technology",
        "AnnualRevenue": 5000000,
    },
    {
        "_comment": "VALID — financial services lead",
        "FirstName": "David",
        "LastName": "Park",
        "Email": "d.park@bigco.us",
        "Phone": "+12125551234",
        "Company": "BigCo Inc",
        "LeadSource": "Partner",
        "Status": "Working",
        "Industry": "Financial Services",
        "AnnualRevenue": 50000000,
    },
    {
        "_comment": "VALID — minimal fields",
        "FirstName": "Yuki",
        "LastName": "Tanaka",
        "Email": "y.tanaka@startup.jp",
        "Phone": None,
        "Company": "Startup K.K.",
        "LeadSource": "Web",
        "Status": "Open",
        "Industry": None,
        "AnnualRevenue": None,
    },
    {
        "_comment": "INVALID — Company missing (required)",
        "FirstName": "Unknown",
        "LastName": "Prospect",
        "Email": "unknown@example.com",
        "Phone": None,
        "Company": None,
        "LeadSource": "Web",
        "Status": "Open",
        "Industry": None,
        "AnnualRevenue": None,
    },
    {
        "_comment": "INVALID — Email bad format, negative AnnualRevenue",
        "FirstName": "Bad",
        "LastName": "Record",
        "Email": "not-valid",
        "Phone": "+447700000999",
        "Company": "SomeCompany",
        "LeadSource": "Web",
        "Status": "Open",
        "Industry": "Technology",
        "AnnualRevenue": -100,
    },
]

_INLINE_SAMPLES: dict[str, list[dict]] = {
    "salesforce_contact": _SF_CONTACT_SAMPLES,
    "salesforce_lead": _SF_LEAD_SAMPLES,
}


# ── Sample record loading ─────────────────────────────────────────────────────


def load_sample_records(contract: str) -> list[dict]:
    """Load sample records for a contract.

    Search order:
    1. examples/starter_contracts/sample_records/{contract}.json  (exact match)
    2. Built-in inline samples (salesforce_contact, salesforce_lead)
    3. Domain-prefix match: 'banking_transaction' → banking.json
    4. Fallback: universal.json
    """
    exact = SAMPLE_RECORDS_DIR / f"{contract}.json"
    if exact.exists():
        with open(exact) as f:
            return json.load(f)

    if contract in _INLINE_SAMPLES:
        return _INLINE_SAMPLES[contract]

    # Prefix match — try progressively shorter underscore-split prefixes
    parts = contract.split("_")
    for length in range(len(parts), 0, -1):
        candidate = "_".join(parts[:length])
        candidate_file = SAMPLE_RECORDS_DIR / f"{candidate}.json"
        if candidate_file.exists():
            print(
                f"[OpenDQV Simulator] note: no sample file for '{contract}', "
                f"using '{candidate}.json'",
                file=sys.stderr,
            )
            with open(candidate_file) as f:
                return json.load(f)

    fallback = SAMPLE_RECORDS_DIR / "universal.json"
    if fallback.exists():
        print(
            f"[OpenDQV Simulator] note: no sample file for '{contract}', "
            f"falling back to universal.json",
            file=sys.stderr,
        )
        with open(fallback) as f:
            return json.load(f)

    print(
        f"[OpenDQV Simulator] ERROR: no sample records found for '{contract}'.\n"
        f"  Add: examples/starter_contracts/sample_records/{contract}.json\n"
        f"  Or run with: --list-contracts",
        file=sys.stderr,
    )
    sys.exit(1)


def list_available_contracts() -> None:
    print("Sample record files (examples/starter_contracts/sample_records/):")
    for f in sorted(SAMPLE_RECORDS_DIR.glob("*.json")):
        try:
            with open(f) as fh:
                records = json.load(fh)
            valid = sum(1 for r in records if "VALID" in r.get("_comment", "").upper()
                        and "INVALID" not in r.get("_comment", "").upper())
            invalid = sum(1 for r in records if "INVALID" in r.get("_comment", "").upper())
            print(f"  {f.stem:<40} ({len(records)} records: {valid} valid, {invalid} invalid)")
        except Exception:
            print(f"  {f.stem}")
    print()
    print("Built-in inline samples (no file needed):")
    for name, samples in sorted(_INLINE_SAMPLES.items()):
        valid = sum(1 for r in samples if "VALID" in r.get("_comment", "").upper()
                    and "INVALID" not in r.get("_comment", "").upper())
        invalid = sum(1 for r in samples if "INVALID" in r.get("_comment", "").upper())
        print(f"  {name:<40} ({len(samples)} records: {valid} valid, {invalid} invalid)")


# ── Record cleaning and error injection ───────────────────────────────────────


def clean_record(record: dict) -> dict:
    """Remove metadata fields (_comment, _sim_id) from a sample record."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def is_invalid_sample(record: dict) -> bool:
    """Return True if the sample record's _comment marks it as INVALID."""
    comment = record.get("_comment", "")
    return "INVALID" in comment.upper()


def inject_error(record: dict, rng: random.Random) -> dict:
    """Corrupt a random non-null field to trigger a validation failure.

    Applies domain-aware mutations (email format, phone format, date format,
    lookup values) so errors are realistic rather than arbitrary noise.
    """
    mutated = dict(record)
    candidates = [k for k, v in mutated.items() if v is not None]
    if not candidates:
        return mutated

    field = rng.choice(candidates)
    val = mutated[field]
    fname = field.lower()

    if isinstance(val, str):
        if "email" in fname:
            mutated[field] = "not-an-email-address"
        elif "phone" in fname:
            mutated[field] = "000"
        elif "date" in fname or "time" in fname or "datetime" in fname:
            mutated[field] = "31/13/9999"
        elif "status" in fname or "type" in fname or "source" in fname:
            mutated[field] = "INVALID_VALUE_XYZ"
        elif "country" in fname:
            mutated[field] = "ZZZ"
        elif "currency" in fname:
            mutated[field] = "XXX"
        elif "account" in fname or "id" in fname:
            mutated[field] = "BAD-ID-FORMAT"
        else:
            mutated[field] = ""  # triggers not_empty rules
    elif isinstance(val, (int, float)):
        mutated[field] = -999999  # likely out of range
    elif isinstance(val, list):
        mutated[field] = ["INVALID_VALUE_XYZ"]

    return mutated


def build_test_population(
    samples: list[dict],
    n: int,
    error_rate: float,
    rng: random.Random,
) -> list[tuple[dict, bool]]:
    """Build n (record, expected_invalid) pairs for the test run.

    Draws from the sample pool, using existing INVALID records where available
    and injecting errors into VALID records when more failures are needed.
    """
    valid_pool = [clean_record(r) for r in samples if not is_invalid_sample(r)]
    invalid_pool = [clean_record(r) for r in samples if is_invalid_sample(r)]

    n_invalid = round(n * error_rate)
    n_valid = n - n_invalid

    result: list[tuple[dict, bool]] = []

    # Fill valid slots
    if valid_pool:
        for i in range(n_valid):
            result.append((valid_pool[i % len(valid_pool)], False))
    elif invalid_pool:
        for i in range(n_valid):
            result.append((invalid_pool[i % len(invalid_pool)], True))

    # Fill invalid slots — prefer existing INVALID samples, inject errors when needed
    for i in range(n_invalid):
        if invalid_pool:
            base = invalid_pool[i % len(invalid_pool)]
            result.append((base, True))
        elif valid_pool:
            base = valid_pool[i % len(valid_pool)]
            corrupted = inject_error(base, rng)
            result.append((corrupted, True))
        else:
            result.append(({}, True))

    rng.shuffle(result)
    return result


# ── Formatting helpers ────────────────────────────────────────────────────────


def fmt_errors(errors: list[dict]) -> str:
    msgs = [e.get("message", e.get("error", str(e))) for e in errors]
    return json.dumps(msgs)


def fmt_warnings(warnings: list[dict]) -> str:
    msgs = [w.get("message", w.get("warning", str(w))) for w in warnings]
    return ", ".join(msgs)


# ── Validation runners ────────────────────────────────────────────────────────


def run_single(
    population: list[tuple[dict, bool]],
    contract: str,
    api_url: str,
    token: Optional[str],
    agent_id: str = "",
) -> list[dict]:
    """Validate records one at a time via POST /api/v1/validate."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results = []
    with httpx.Client(base_url=api_url, headers=headers, timeout=30.0) as client:
        for idx, (record, _) in enumerate(population, 1):
            record_id = f"sim-{idx:03d}"
            body = {"record": record, "contract": contract, "record_id": record_id}
            if agent_id:
                body["agent_id"] = agent_id

            t0 = time.perf_counter()
            try:
                resp = client.post("/api/v1/validate", json=body)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                print(
                    f"[{idx:>3}] ERROR  record_id={record_id} "
                    f"http={exc.response.status_code} body={exc.response.text[:120]}"
                )
                results.append(
                    {"valid": None, "record_id": record_id, "latency_ms": latency_ms, "errors": []}
                )
                continue
            except httpx.RequestError as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                print(
                    f"[{idx:>3}] ERROR  record_id={record_id} "
                    f"connection error: {exc}"
                )
                results.append(
                    {"valid": None, "record_id": record_id, "latency_ms": latency_ms, "errors": []}
                )
                continue

            valid = data.get("valid", False)
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])

            if valid:
                warn_str = f" warnings=[{fmt_warnings(warnings)}]" if warnings else ""
                print(f"[{idx:>3}] PASS  record_id={record_id} latency={latency_ms}ms{warn_str}")
            else:
                print(f"[{idx:>3}] FAIL  record_id={record_id} errors={fmt_errors(errors)}")

            results.append(
                {
                    "valid": valid,
                    "record_id": record_id,
                    "latency_ms": latency_ms,
                    "errors": errors,
                    "warnings": warnings,
                }
            )

    return results


def run_batch(
    population: list[tuple[dict, bool]],
    contract: str,
    batch_size: int,
    api_url: str,
    token: Optional[str],
    agent_id: str = "",
) -> list[dict]:
    """Validate records in batches via POST /api/v1/validate/batch.

    Simulates: Kafka consumer (batch commit), Spark foreachBatch, Airflow bulk task.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results = []
    all_records = [r for r, _ in population]
    n = len(all_records)

    with httpx.Client(base_url=api_url, headers=headers, timeout=60.0) as client:
        offset = 0
        batch_num = 0
        while offset < n:
            batch_records = all_records[offset : offset + batch_size]
            batch_num += 1
            sim_ids = [f"sim-{i:03d}" for i in range(offset + 1, offset + len(batch_records) + 1)]

            body = {"records": batch_records, "contract": contract}
            if agent_id:
                body["agent_id"] = agent_id

            t0 = time.perf_counter()
            try:
                resp = client.post("/api/v1/validate/batch", json=body)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                print(
                    f"[batch-{batch_num:03d}] ERROR  records={offset+1}-{offset+len(batch_records)} "
                    f"http={exc.response.status_code}"
                )
                for sim_id in sim_ids:
                    results.append(
                        {"valid": None, "record_id": sim_id, "latency_ms": latency_ms, "errors": []}
                    )
                offset += batch_size
                continue
            except httpx.RequestError as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                print(f"[batch-{batch_num:03d}] ERROR  connection error: {exc}")
                for sim_id in sim_ids:
                    results.append(
                        {"valid": None, "record_id": sim_id, "latency_ms": latency_ms, "errors": []}
                    )
                offset += batch_size
                continue

            per_latency = latency_ms // max(len(batch_records), 1)
            batch_results = data.get("results", [])

            for i, (br, sim_id) in enumerate(zip(batch_results, sim_ids)):
                valid = br.get("valid", False)
                errors = br.get("errors", [])
                warnings = br.get("warnings", [])

                if valid:
                    warn_str = f" warnings=[{fmt_warnings(warnings)}]" if warnings else ""
                    print(
                        f"[batch-{batch_num:03d}:{i+1:>3}] PASS  "
                        f"record_id={sim_id} latency={per_latency}ms{warn_str}"
                    )
                else:
                    print(
                        f"[batch-{batch_num:03d}:{i+1:>3}] FAIL  "
                        f"record_id={sim_id} errors={fmt_errors(errors)}"
                    )

                results.append(
                    {
                        "valid": valid,
                        "record_id": sim_id,
                        "latency_ms": per_latency,
                        "errors": errors,
                        "warnings": warnings,
                    }
                )

            offset += batch_size

    return results


# ── Summary ───────────────────────────────────────────────────────────────────


def print_summary(
    results: list[dict],
    source_system: str,
    contract: str,
    error_rate: float,
) -> None:
    passed = sum(1 for r in results if r["valid"] is True)
    errored = sum(1 for r in results if r["valid"] is None)
    total = len(results)

    latencies = [r["latency_ms"] for r in results if r["valid"] is not None]
    avg_latency = int(sum(latencies) / len(latencies)) if latencies else 0
    min_latency = min(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0

    # Top failures by error message
    all_errors: list[str] = []
    for r in results:
        for e in r.get("errors", []):
            msg = e.get("message", e.get("error", ""))
            if msg:
                all_errors.append(msg)
    top_failures = Counter(all_errors).most_common(3)

    pct = int(passed / total * 100) if total else 0
    print()
    print("─" * 72)
    print(
        f"Summary: {passed}/{total} passed ({pct}%) | "
        f"avg_latency={avg_latency}ms | min={min_latency}ms | max={max_latency}ms"
    )
    if top_failures:
        top_str = " | ".join(f'"{msg[:50]}" ({count}x)' for msg, count in top_failures)
        print(f"Top failures: {top_str}")
    if errored:
        print(
            f"API errors:   {errored} record(s) — check OpenDQV is running and reachable"
        )
    print("─" * 72)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OpenDQV Source System Simulator — "
            "test integrations without a real Salesforce/Databricks account"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--contract",
        required=False,
        help="Contract name to validate against (e.g. banking, salesforce_contact, healthcare)",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=20,
        metavar="N",
        help="Number of records to simulate (default: 20)",
    )
    parser.add_argument(
        "--error-rate",
        type=float,
        default=0.0,
        metavar="RATE",
        help="Fraction of records that should fail validation, 0.0–1.0 (default: 0.0)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Records per API call: 1=single /validate, >1=batch /validate/batch "
            "(default: 1)"
        ),
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        metavar="URL",
        help="OpenDQV API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENDQV_TOKEN", ""),
        metavar="TOKEN",
        help="Bearer token (or set OPENDQV_TOKEN env var; omit for AUTH_MODE=open)",
    )
    parser.add_argument(
        "--source-system",
        default="simulator",
        metavar="LABEL",
        help="Label for this simulated source system — appears in output (default: simulator)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible runs (default: random)",
    )
    parser.add_argument(
        "--list-contracts",
        action="store_true",
        help="List available sample record files and exit",
    )

    args = parser.parse_args()

    if args.list_contracts:
        list_available_contracts()
        return

    if not args.contract:
        parser.error("--contract is required (or use --list-contracts to see options)")

    if not 0.0 <= args.error_rate <= 1.0:
        print(
            f"ERROR: --error-rate must be between 0.0 and 1.0, got {args.error_rate}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.batch_size < 1:
        print("ERROR: --batch-size must be >= 1", file=sys.stderr)
        sys.exit(1)

    rng = random.Random(args.seed)

    # ── Load records ─────────────────────────────────────────────────────────
    samples = load_sample_records(args.contract)
    population = build_test_population(samples, args.records, args.error_rate, rng)

    # ── Header ───────────────────────────────────────────────────────────────
    mode = f"batch/{args.batch_size}" if args.batch_size > 1 else "single"
    print(
        f"[OpenDQV Simulator] source={args.source_system} "
        f"contract={args.contract} "
        f"records={args.records} "
        f"error_rate={int(args.error_rate * 100)}% "
        f"mode={mode} "
        f"api_url={args.api_url}"
    )
    print()

    # ── Validate ─────────────────────────────────────────────────────────────
    if args.batch_size == 1:
        results = run_single(
            population, args.contract, args.api_url, args.token or None,
            agent_id=args.source_system,
        )
    else:
        results = run_batch(
            population, args.contract, args.batch_size, args.api_url, args.token or None,
            agent_id=args.source_system,
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    print_summary(results, args.source_system, args.contract, args.error_rate)


if __name__ == "__main__":
    main()
