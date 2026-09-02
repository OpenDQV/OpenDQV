#!/usr/bin/env python3
"""Generate cross-engine conformance fixtures from OpenDQV Core (the spec).

For a bundled contract, emit one JSONL line per probe record:
    {"record": {...}, "expect": {"valid": bool, "error_codes": [...], "warning_codes": [...]}}

Probe records are synthesised from the contract's own rules so that every rule
is exercised at least once (an empty record, one violating record per rule,
and one all-strings record). The expectation is Core's verdict on the
single-record path. Any other engine claiming to run the OpenDQV contract
format must reproduce these verdicts exactly; a disagreement is a spec
question for docs/contract_conformance.md, never something to paper over.

Usage:
    python scripts/conformance_fixtures.py banking_transaction hr_employee > /dev/null
    (writes tests/fixtures/conformance/<name>.jsonl)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opendqv.core.contracts import ContractRegistry
from opendqv.core.validator import strict_schema_kwargs, validate_record

_VIOLATIONS = {
    "not_empty": "", "not_empty_string": 42, "regex": "!!!", "min": -999999, "max": 999999,
    "range": -999999, "min_length": "x", "max_length": "x" * 400, "date_format": "not-a-date",
    "allowed_values": "__not_allowed__", "lookup": "__not_in_reference__",
    "checksum": "0000000000000", "compare": "1900-01-01", "date_diff": "1900-01-01",
    "age_match": -5, "ratio_check": 0, "field_sum": 0, "cross_field_range": -999999,
    "geospatial_bounds": 999, "conditional_value": "__wrong__", "required_if": "",
    "forbidden_if": "present", "unique": "dup", "conditional_lookup": "__not_in_reference__",
}


def probes(contract) -> list[dict]:
    fields = sorted({r.field for r in contract.rules if r.field})
    out = [{}]
    for r in contract.rules:
        rec = {f: "x" for f in fields}
        rec[r.field] = _VIOLATIONS.get(r.type, "__violation__")
        out.append(rec)
    out.append({f: "x" for f in fields})
    out.append({f: "x" for f in fields} | {"__undeclared__": 1})
    return out


def expectation(contract, rules, record) -> dict:
    res = validate_record(record, rules, contract_name=contract.name, **strict_schema_kwargs(contract, rules))
    return {
        "valid": res["valid"],
        "error_codes": sorted(e["error_code"] for e in res["errors"]),
        "warning_codes": sorted(w["error_code"] for w in res["warnings"]),
    }


def main(names: list[str]) -> int:
    reg = ContractRegistry(ROOT / "opendqv" / "contracts")
    outdir = ROOT / "tests" / "fixtures" / "conformance"
    outdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        contract = reg.get(name)
        if contract is None:
            print(f"unknown contract {name}", file=sys.stderr)
            return 1
        rules = reg.get_rules_with_context(contract, None)
        lines = [json.dumps({"record": rec, "expect": expectation(contract, rules, rec)}, sort_keys=True)
                 for rec in probes(contract)]
        (outdir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{name}: {len(lines)} probes -> {outdir / (name + '.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["banking_transaction", "hr_employee", "customer", "dora_ict_incident"]))
