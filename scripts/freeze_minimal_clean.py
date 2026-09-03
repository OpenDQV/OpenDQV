#!/usr/bin/env python3
"""Re-freeze tests/fixtures/conformance/frozen/minimal_clean.jsonl (#146).

For every bundled contract that has a seed record — the hand-written clean row
in scripts/conformance_clean_rows.py, else the existing frozen row, else the
first record in examples/starter_contracts/sample_records/ that the contract
accepts — this shrinks the record greedily (drop a key, keep the drop if the
record is still valid) until every remaining key is load-bearing, which is
exactly what tests/test_backlog_146_minimal_clean_and_replay.py demands.

Contracts with no valid seed are left out (they must appear in the test's
_UNSEEDED allowlist). Run after any deliberate library change:

    python scripts/freeze_minimal_clean.py            # rewrite the file
    python scripts/freeze_minimal_clean.py --check    # exit 1 if it would change
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
FROZEN = ROOT / "tests" / "fixtures" / "conformance" / "frozen" / "minimal_clean.jsonl"
BUNDLED = ROOT / "opendqv" / "contracts"
SAMPLES = ROOT / "examples" / "starter_contracts" / "sample_records"

# sample_records file stem → contract name where they differ
_SAMPLE_ALIAS = {
    "banking": "banking_transaction", "energy": "ofgem_meter_reading", "healthcare_patient": "nhs_dsp_patient",
    "insurance": "insurance_claim", "logistics": "logistics_shipment", "manufacturing": "manufacturing_iot",
    "pharma": "pharma_clinical_trial", "public_sector": "public_sector_service", "real_estate": "real_estate_property",
    "retail": "retail_product", "technology": "technology_event", "telecoms": "telecoms_cdr",
    "travel": "travel_booking", "universal": "customer",
}


def _contract(name: str):
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule
    from opendqv.core.validator import strict_schema_kwargs
    raw = yaml.safe_load((BUNDLED / f"{name}.yaml").read_text(encoding="utf-8"))["contract"]
    rules = [Rule(**r) for r in raw.get("rules", [])]
    dc = DataContract(name=raw["name"], rules=rules, strict_schema=bool(raw.get("strict_schema")),
                      allowed_fields=raw.get("allowed_fields") or [])
    return rules, strict_schema_kwargs(dc, rules)


def _valid(rec: dict, name: str, cache: dict) -> bool:
    from opendqv.core.validator import validate_batch, validate_record
    if name not in cache:
        cache[name] = _contract(name)
    rules, kw = cache[name]
    return bool(validate_record(rec, rules, name, **kw)["valid"]) and bool(
        validate_batch([rec], rules, name, **kw)["results"][0]["valid"])


def _sample_records(name: str) -> list[dict]:
    out = []
    for p in sorted(SAMPLES.glob("*.json")):
        stem = _SAMPLE_ALIAS.get(p.stem, p.stem)
        if stem != name:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        recs = data if isinstance(data, list) else data.get("records") or data.get("valid") or []
        # Sample files annotate each record with a `_comment`; it is not data
        # (and is an unknown field under strict_schema).
        out.extend({k: v for k, v in r.items() if k != "_comment"} for r in recs if isinstance(r, dict))
    return out


def _seeds(name: str, frozen: dict[str, dict]) -> list[dict]:
    from conformance_clean_rows import CLEAN_ROWS
    seeds = list(CLEAN_ROWS.get(name, []))
    if name in frozen:
        seeds.append(frozen[name]["record"])
    seeds.extend(_sample_records(name))
    return seeds


def _shrink(rec: dict, name: str, cache: dict) -> dict:
    cur = dict(rec)
    changed = True
    while changed:
        changed = False
        for k in sorted(cur):
            trial = {x: v for x, v in cur.items() if x != k}
            if _valid(trial, name, cache):
                cur = trial
                changed = True
    return cur


def build(version_label: str) -> list[dict]:
    frozen = {}
    if FROZEN.exists():
        for line in FROZEN.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                frozen[row["contract"]] = row
    cache: dict = {}
    rows = []
    for p in sorted(BUNDLED.glob("*.yaml")):
        name = p.stem
        seed = next((s for s in _seeds(name, frozen) if _valid(s, name, cache)), None)
        if seed is None:
            continue
        minimal = _shrink(seed, name, cache)
        prev = frozen.get(name)
        label = prev["frozen_at"] if prev and prev["record"] == minimal else version_label
        rows.append({"contract": name, "frozen_at": label, "record": dict(sorted(minimal.items()))})
    return rows


def main(argv: list[str]) -> int:
    label = next((a for a in argv if not a.startswith("--")), "working-tree")
    rows = build(label)
    rendered = "".join(json.dumps(r, sort_keys=False) + "\n" for r in rows)
    if "--check" in argv:
        if FROZEN.read_text(encoding="utf-8") != rendered:
            print("minimal_clean.jsonl would change — run scripts/freeze_minimal_clean.py <version>")
            return 1
        print("minimal_clean.jsonl up to date")
        return 0
    FROZEN.write_text(rendered, encoding="utf-8")
    print(f"froze {len(rows)} minimal-clean rows into {FROZEN.relative_to(ROOT)} (label {label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
