#!/usr/bin/env python3
"""Generate cross-engine conformance fixtures from OpenDQV Core (the spec).

For a bundled contract, emit one JSONL line per record:

    {"kind": "clean" | "warning_only" | "probe",
     "record": {...},
     "expect": {"valid": bool,
                "errors":   [{"code": ..., "severity": ..., "message": ...}, ...],
                "warnings": [{"code": ..., "severity": ..., "message": ...}, ...]}}

``probe`` records are synthesised from the contract's own rules so that every
rule type is exercised at least once (an empty record, one violating record
per rule, an all-strings record, and an undeclared-field record). ``clean``
and ``warning_only`` records are hand-written in
``scripts/conformance_clean_rows.py`` so the corpus also proves *acceptance*,
not only rejection.

The expectation is Core's verdict. It is computed on BOTH validate paths
(``validate_record`` and ``validate_batch``) and the generator refuses to emit
a corpus in which the two disagree, or in which a clean row is not clean.
Any other engine claiming to run the OpenDQV contract format must reproduce
these verdicts exactly; a disagreement is a spec question for
docs/contract_conformance.md, never something to paper over.

Usage:
    python scripts/conformance_fixtures.py                      # the five bundled fixture contracts
    python scripts/conformance_fixtures.py banking_transaction  # one
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from conformance_clean_rows import CLEAN_ROWS, WARNING_ONLY_ROWS

from opendqv.core.contracts import ContractRegistry
from opendqv.core.validator import (
    _RULE_HANDLERS,
    strict_schema_kwargs,
    validate_batch,
    validate_record,
)

DEFAULT_CONTRACTS = ["banking_transaction", "hr_employee", "customer", "dora_ict_incident", "nhs_dsp_patient"]

# One value that violates each rule type. Keyed off the validator's handler
# table: a new rule type without an entry here fails generation (and the CI
# test) instead of silently going unprobed.
_VIOLATIONS: dict[str, object] = {
    "not_empty": "", "not_empty_string": 42, "regex": "!!!", "min": -999999, "max": 999999,
    "range": -999999, "min_length": "x", "max_length": "x" * 400, "date_format": "not-a-date",
    "allowed_values": "__not_allowed__", "lookup": "__not_in_reference__",
    "checksum": "0000000000000", "compare": "1900-01-01", "date_diff": "1900-01-01",
    "age_match": -5, "ratio_check": 0, "field_sum": 0, "cross_field_range": -999999,
    "geospatial_bounds": 999, "conditional_value": "__wrong__", "required_if": "",
    "forbidden_if": "present", "unique": "dup", "conditional_lookup": "__not_in_reference__",
}


def unprobed_rule_types() -> set[str]:
    return set(_RULE_HANDLERS) - set(_VIOLATIONS)


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


def _entries(items: list[dict]) -> list[dict]:
    return sorted(
        ({"code": e["error_code"], "severity": e["severity"], "message": e["message"]} for e in items),
        key=lambda d: (d["code"], d["message"]),
    )


def _shape(res: dict) -> dict:
    return {"valid": res["valid"], "errors": _entries(res["errors"]), "warnings": _entries(res["warnings"])}


def expectation(contract, rules, record: dict) -> dict:
    kw = strict_schema_kwargs(contract, rules)
    single = _shape(validate_record(record, rules, contract_name=contract.name, **kw))
    batch = _shape(validate_batch([record], rules, contract_name=contract.name, **kw)["results"][0])
    if single != batch:
        raise SystemExit(
            f"{contract.name}: validate_record and validate_batch disagree on {record!r}\n"
            f"  single: {single}\n  batch:  {batch}\n"
            "Fix the engine (single/batch parity is a conformance invariant, see K1) before regenerating."
        )
    return single


def build(contract, rules) -> list[dict]:
    lines: list[dict] = []
    for rec in CLEAN_ROWS.get(contract.name, []):
        exp = expectation(contract, rules, rec)
        if not (exp["valid"] and not exp["errors"] and not exp["warnings"]):
            raise SystemExit(f"{contract.name}: hand-written clean row is not clean under Core: {exp}")
        lines.append({"kind": "clean", "record": rec, "expect": exp})
    for rec in WARNING_ONLY_ROWS.get(contract.name, []):
        exp = expectation(contract, rules, rec)
        if not (exp["valid"] and not exp["errors"] and exp["warnings"]):
            raise SystemExit(f"{contract.name}: hand-written warning-only row is not warning-only under Core: {exp}")
        lines.append({"kind": "warning_only", "record": rec, "expect": exp})
    for rec in probes(contract):
        lines.append({"kind": "probe", "record": rec, "expect": expectation(contract, rules, rec)})
    return lines


def main(names: list[str]) -> int:
    missing = unprobed_rule_types()
    if missing:
        print(f"rule types without a violation probe: {sorted(missing)} — add them to _VIOLATIONS", file=sys.stderr)
        return 1
    reg = ContractRegistry(ROOT / "opendqv" / "contracts")
    outdir = ROOT / "tests" / "fixtures" / "conformance"
    outdir.mkdir(parents=True, exist_ok=True)
    for name in names:
        contract = reg.get(name)
        if contract is None:
            print(f"unknown contract {name}", file=sys.stderr)
            return 1
        rules = reg.get_rules_with_context(contract, None)
        lines = build(contract, rules)
        (outdir / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8"
        )
        kinds = {k: sum(1 for line in lines if line["kind"] == k) for k in ("clean", "warning_only", "probe")}
        print(f"{name}: {len(lines)} records {kinds} -> {outdir / (name + '.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or DEFAULT_CONTRACTS))
