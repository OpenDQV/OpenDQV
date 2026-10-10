"""
3.0.8 (managed-engine parity, messages only — verdicts and codes already agree):

1. compare's today/now and same_date branches check for a list or object in
   the rule's own field before reading a date, so [1] gt today and
   [1] same_date "2026-01-01" get the typed "compares a single value" message,
   as every other compare operator does (3.0.7).
2. A JSON number the engine cannot read (1e400, which the JSON parser makes
   infinity; an integer beyond float64) is "a number beyond the readable
   range" under the type-mismatch message — not "infinity" (Core) and not
   "expected numeric value, got number" (managed). NaN keeps its own name.
"""
import json

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record


def _messages(record, rule):
    single = validate_record(record, [rule], "t")
    batch = validate_batch([record], [rule], "t")["results"][0]
    assert single["valid"] is batch["valid"] is False
    assert [e["error_code"] for e in single["errors"]] == [e["error_code"] for e in batch["errors"]]
    return [e["message"] for e in single["errors"]], [e["message"] for e in batch["errors"]]


def _compare(op, to="b"):
    return Rule(name="c", field="a", type="compare", compare_to=to, compare_op=op,
                error_message="a must compare to b")


@pytest.mark.parametrize("op,to,other", [
    ("gt", "today", None), ("lt", "now", None), ("same_date", "today", None),
    ("same_date", "b", "2026-01-01"),
])
@pytest.mark.parametrize("value,kind", [([1], "array"), ({"k": 1}, "object")])
def test_collection_in_the_own_field_gets_the_typed_message_on_the_date_branches(op, to, other, value, kind):
    record = {"a": value} if other is None else {"a": value, "b": other}
    single, batch = _messages(record, _compare(op, to))
    assert single == batch == [
        f'compare rule on field "a" compares a single value, got {kind} — send a string, number or boolean']


def test_same_date_collection_in_the_counterpart_keeps_the_rules_own_message():
    single, batch = _messages({"a": "2026-01-01", "b": [1]}, _compare("same_date"))
    assert single == batch == ["a must compare to b"]


def test_unreadable_date_text_on_the_date_branches_keeps_the_rules_own_message():
    for rule, record in ((_compare("gt", "today"), {"a": "x"}), (_compare("same_date"), {"a": "x", "b": "2026-01-01"})):
        single, batch = _messages(record, rule)
        assert single == batch == ["a must compare to b"]


BEYOND = "expected a number it can read, got a number beyond the readable range"


@pytest.mark.parametrize("raw", ['{"a": 1e400}', '{"a": -1e400}', '{"a": ' + "9" * 400 + "}"])
@pytest.mark.parametrize("rtype,extra", [("min", {"min": 0}), ("max", {"max": 0}), ("range", {"min": 0, "max": 1})])
def test_unreadable_json_number_message(raw, rtype, extra):
    rule = Rule(name="r", field="a", type=rtype, error_message="bad", **extra)
    single, batch = _messages(json.loads(raw), rule)
    assert single == batch == [f"{rtype} rule on field 'a' {BEYOND}"]


def test_nan_keeps_its_own_name():
    rule = Rule(name="r", field="a", type="min", min=0, error_message="bad")
    single, batch = _messages({"a": float("nan")}, rule)
    assert single == batch == ["min rule on field 'a' expected a number it can read, got NaN"]


def test_other_type_mismatches_keep_their_message():
    rule = Rule(name="r", field="a", type="min", min=0, error_message="bad")
    single, _ = _messages({"a": "abc"}, rule)
    assert single == ["min rule on field 'a' expected numeric value, got string"]
