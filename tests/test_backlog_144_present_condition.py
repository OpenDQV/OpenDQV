"""#144 — `condition: {field: X, present: true|false}` on both paths (D6 absence reading)."""
from __future__ import annotations

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record

CMP = Rule(name="cmp", type="compare", field="end", compare_to="start", compare_op="gte",
           condition={"field": "start", "present": True}, error_message="end>=start")

RECS = [
    ({"end": "2026-01-02", "start": "2026-01-01"}, True),    # both present, holds
    ({"end": "2026-01-01", "start": "2026-01-02"}, False),   # both present, fails
    ({"end": "2026-01-02"}, True),                           # counterpart absent → rule not applied
    ({"end": "2026-01-02", "start": ""}, True),              # blank counterpart → absent → not applied
    ({"end": "2026-01-02", "start": "   "}, True),           # whitespace → absent → not applied
]


@pytest.mark.parametrize("rec,expected", RECS, ids=[str(i) for i in range(len(RECS))])
def test_compare_only_when_counterpart_present_single(rec, expected):
    assert validate_record(rec, [CMP], "t")["valid"] is expected


def test_batch_matches_single_for_present_true():
    recs = [r for r, _ in RECS]
    single = [validate_record(r, [CMP], "t")["valid"] for r in recs]
    batch = [x["valid"] for x in validate_batch(recs, [CMP], "t")["results"]]
    assert batch == single == [e for _, e in RECS]


def test_present_false_applies_only_when_absent():
    r = Rule(name="fallback", type="not_empty", field="legacy_id",
             condition={"field": "new_id", "present": False}, error_message="legacy_id required when new_id absent")
    recs = [{"new_id": "N1"}, {"legacy_id": "L1"}, {}, {"new_id": "", "legacy_id": ""}]
    expected = [True, True, False, False]
    assert [validate_record(x, [r], "t")["valid"] for x in recs] == expected
    assert [x["valid"] for x in validate_batch(recs, [r], "t")["results"]] == expected


def test_present_conjoins_with_value():
    r = Rule(name="x", type="min", field="amount", min_value=1,
             condition={"field": "kind", "present": True, "value": "charge"})
    assert validate_record({"amount": 0, "kind": "charge"}, [r], "t")["valid"] is False
    assert validate_record({"amount": 0, "kind": "credit"}, [r], "t")["valid"] is True
    assert validate_record({"amount": 0}, [r], "t")["valid"] is True


def test_batch_present_when_column_absent_from_every_record():
    recs = [{"end": "2026-01-02"}, {"end": "2026-01-03"}]
    assert all(x["valid"] for x in validate_batch(recs, [CMP], "t")["results"])


@pytest.mark.parametrize("bad", [
    {"field": "a", "presnt": True},
    {"field": "a", "present": "yes"},
    {"value": "x"},
])
def test_condition_vocabulary_is_closed(bad):
    with pytest.raises(ValueError):
        Rule(name="r", type="not_empty", field="f", condition=bad)
