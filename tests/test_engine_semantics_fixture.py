"""Engine-semantics fixture: self-contained rows (inline rules + record +
verdict + codes) pinning engine decisions that no bundled contract exercises,
so every engine that replays the corpus is held to the same semantics.

Row shape: {"claim", "rules": [rule dicts], "record", "valid", "codes"}.
Kept in frozen/ so the corpus generator's glob never sees it, and edited by
hand only — a row changes when a decision changes, with a CHANGELOG entry.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "conformance" / "frozen" / "engine_semantics.jsonl"


def _rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["claim"][:60])
def test_semantics_row_holds_on_both_paths(row):
    rules = [Rule(**r) for r in row["rules"]]
    single = validate_record(row["record"], rules, "engine_semantics")
    batch = validate_batch([row["record"]], rules, "engine_semantics")["results"][0]
    for label, out in (("single", single), ("batch", batch)):
        assert out["valid"] is row["valid"], f"[{label}] {row['claim']}: expected valid={row['valid']}, got {out['valid']}"
        fired = sorted(e["error_code"] for e in out["errors"])
        assert fired == sorted(row["codes"]), f"[{label}] {row['claim']}: expected {sorted(row['codes'])}, got {fired}"


def test_rows_are_well_formed():
    for row in _rows():
        assert set(row) == {"claim", "rules", "record", "valid", "codes"}, row["claim"]
