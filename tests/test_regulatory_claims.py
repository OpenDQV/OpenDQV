"""Regulatory claims the starter library makes, as executable fixtures (review S6, 2.7.0).

The conformance corpus proves the two engines agree; the intent-tagged sample
records prove each record is valid or invalid as a whole. Neither says WHICH
rule carried a regulatory claim. Every row in
tests/fixtures/conformance/frozen/regulatory_claims.jsonl states one claim
(with its instrument), a record built from a known-good base plus a small
patch, the verdict the claim implies, and the error codes that must be
present when it is a rejection — so a future library edit that quietly
loses the claim fails here, by name.

Row shape: {"contract", "claim", "base": "clean" | "sample:<index>",
            "patch": {field: value | null (= delete)}, "valid": bool, "codes": [...]}

A `valid: true` row with codes lists WARNING codes that must still fire
(advisory claims); an invalid row's codes must all appear among the errors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
FIXTURE = ROOT / "tests" / "fixtures" / "conformance" / "frozen" / "regulatory_claims.jsonl"
BUNDLED = ROOT / "opendqv" / "contracts"
SAMPLES = ROOT / "examples" / "starter_contracts" / "sample_records"
_SAMPLE_ALIAS = {"banking_transaction": "banking", "nhs_dsp_patient": "healthcare_patient", "pharma_clinical_trial": "pharma"}


def _rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _contract(name: str):
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule
    from opendqv.core.validator import strict_schema_kwargs
    raw = yaml.safe_load((BUNDLED / f"{name}.yaml").read_text(encoding="utf-8"))["contract"]
    rules = [Rule(**r) for r in raw.get("rules", [])]
    dc = DataContract(name=raw["name"], rules=rules, strict_schema=bool(raw.get("strict_schema")),
                      allowed_fields=raw.get("allowed_fields") or [])
    return rules, strict_schema_kwargs(dc, rules)


def _base(row: dict) -> dict:
    if row["base"] == "clean":
        from conformance_clean_rows import CLEAN_ROWS
        return dict(CLEAN_ROWS[row["contract"]][0])
    kind, idx = row["base"].split(":")
    assert kind == "sample", row["base"]
    stem = _SAMPLE_ALIAS.get(row["contract"], row["contract"])
    records = json.loads((SAMPLES / f"{stem}.json").read_text(encoding="utf-8"))
    rec = dict(records[int(idx)])
    rec.pop("_comment", None)
    return rec


def _record(row: dict) -> dict:
    rec = _base(row)
    for k, v in row["patch"].items():
        if v is None:
            rec.pop(k, None)
        else:
            rec[k] = v
    return rec


@pytest.mark.parametrize("row", _rows(), ids=lambda r: f"{r['contract']}:{r['claim'][:48]}")
def test_claim_holds_on_both_paths(row):
    from opendqv.core.validator import validate_batch, validate_record
    rules, kw = _contract(row["contract"])
    rec = _record(row)
    single = validate_record(rec, rules, row["contract"], **kw)
    batch = validate_batch([rec], rules, row["contract"], **kw)["results"][0]
    for label, out in (("single", single), ("batch", batch)):
        assert out["valid"] is row["valid"], (
            f"{row['contract']} [{label}] — {row['claim']}: expected valid={row['valid']}, got {out['valid']}; "
            f"errors={[e['error_code'] for e in out['errors']]}")
        fired = {e["error_code"] for e in out["errors"]} | {w["error_code"] for w in out["warnings"]}
        missing = set(row["codes"]) - fired
        assert not missing, f"{row['contract']} [{label}] — {row['claim']}: expected {sorted(missing)} to fire; got {sorted(fired)}"


def test_base_records_are_valid():
    """Every base a claim is built on must itself be accepted, or the claim proves nothing."""
    from opendqv.core.validator import validate_record
    seen = set()
    for row in _rows():
        key = (row["contract"], row["base"])
        if key in seen:
            continue
        seen.add(key)
        rules, kw = _contract(row["contract"])
        out = validate_record(_base(row), rules, row["contract"], **kw)
        assert out["valid"], (key, [e["error_code"] for e in out["errors"]])
