"""CRT180 — cross-engine conformance fixtures.

tests/fixtures/conformance/<contract>.jsonl holds probe records and the
verdict OpenDQV Core (the spec) gives each one. Every engine that claims to
run the OpenDQV contract format runs the same files; a disagreement is a
spec question for docs/contract_conformance.md. Regenerate with
scripts/conformance_fixtures.py after a deliberate contract or engine change.
"""
import json
from pathlib import Path

import pytest

from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import strict_schema_kwargs, validate_batch, validate_record

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "conformance").glob("*.jsonl"))


@pytest.fixture(scope="module")
def registry():
    return ContractRegistry(ROOT / "opendqv" / "contracts")


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_core_reproduces_its_own_fixture(registry, path):
    contract = registry.get(path.stem)
    assert contract is not None, path.stem
    rules = registry.get_rules_with_context(contract, None)
    kw = strict_schema_kwargs(contract, rules)
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        case = json.loads(line)
        res = validate_record(case["record"], rules, contract_name=contract.name, **kw)
        got = {
            "valid": res["valid"],
            "error_codes": sorted(e["error_code"] for e in res["errors"]),
            "warning_codes": sorted(w["error_code"] for w in res["warnings"]),
        }
        assert got == case["expect"], f"{path.stem} probe {i}: {case['record']}"


def test_every_fixture_exercises_failures():
    # Probe records are synthesised to violate rules; hand-crafted clean rows
    # per contract are the next fixture improvement (see the conformance doc).
    for path in FIXTURES:
        verdicts = [json.loads(line)["expect"]["valid"] for line in path.read_text().splitlines()]
        assert False in verdicts, path.stem


@pytest.mark.xfail(strict=True, reason=(
    "K1 (docs/contract_conformance.md): validate_batch skips a rule entirely when "
    "no record in the batch carries the field, so a batch that omits a required field "
    "validates clean while validate_record rejects each record. Single/batch parity gap."
))
def test_k1_batch_skips_rule_when_field_absent_from_every_record():
    rules = [Rule(name="a_req", type="not_empty", field="amount", error_message="amount required")]
    single = validate_record({"other": 1}, rules)["valid"]
    batch = [r["valid"] for r in validate_batch([{"other": 1}, {"other": 2}], rules)["results"]]
    assert batch == [single, single]
