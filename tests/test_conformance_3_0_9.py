"""
3.0.9 (both engines, messages only): a list or object in compare's own field
is never a single value, whichever reading would apply. One check straight
after the counterpart-missing check (and after Core's stored-content
unknown-operator path, which does not judge) gives the typed "compares a
single value" message before every branch: declared date layout,
algorithm: semver, the boolean guard, the unreadable-number guard. On 3.0.8
those four gave the rule's own message. A collection in the counterpart keeps
the rule's own message in every branch.
"""
import json

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record

MSG = 'compare rule on field "a" compares a single value, got {} — send a string, number or boolean'


def _compare(op="gt", **extra):
    return Rule(name="c", field="a", type="compare", compare_to="b", compare_op=op,
                error_message="a must compare to b", **extra)


_LAYOUT_B = Rule(name="bd", field="b", type="date_format", format="%d/%m/%Y", error_message="b date")


def _run(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    assert single["valid"] is batch["valid"] is False
    pick = lambda r: [(e["error_code"], e["message"]) for e in r["errors"] if e["field"] == "a"]  # noqa: E731
    assert pick(single) == pick(batch)
    return [m for _, m in pick(single)]


CASES = [
    ("declared layout", lambda v: ({"a": v, "b": "10/01/2026"}, [_compare(), _LAYOUT_B])),
    ("semver", lambda v: ({"a": v, "b": "1.0.0"}, [_compare(algorithm="semver")])),
    ("boolean guard", lambda v: ({"a": v, "b": True}, [_compare()])),
    ("unreadable-number guard", lambda v: ({"a": v, "b": json.loads("1e400")}, [_compare("eq")])),
]


@pytest.mark.parametrize("label,make", CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("value,kind", [([1], "array"), ({"k": 1}, "object")])
def test_own_field_collection_gets_the_typed_message_before_every_branch(label, make, value, kind):
    record, rules = make(value)
    assert _run(record, rules) == [MSG.format(kind)], label


@pytest.mark.parametrize("record,rules", [
    ({"a": "10/01/2026", "b": [1]}, [_compare(), _LAYOUT_B]),
    ({"a": "1.0.0", "b": [1]}, [_compare(algorithm="semver")]),
    ({"a": True, "b": [1]}, [_compare()]),
    ({"a": json.loads("1e400"), "b": {"k": 1}}, [_compare("eq")]),
    ({"a": "2026-01-01", "b": [1]}, [_compare("same_date")]),
])
def test_counterpart_collection_keeps_the_rules_own_message(record, rules):
    assert _run(record, rules) == ["a must compare to b"]


def test_counterpart_missing_is_decided_before_the_collection_check():
    out = validate_record({"a": [1]}, [_compare()], "t")
    assert out["errors"][0]["message"] == "a must compare to b"
    assert out["errors"][0].get("counterpart_missing") is True


def test_stored_unknown_operator_still_does_not_judge_a_collection():
    # Core-only: an unknown compare_op is refused at submission, but stored
    # content keeps its old reading — the rule does not judge, list or not.
    assert validate_record({"a": [1], "b": 1}, [_compare("after")], "t")["valid"] is True
