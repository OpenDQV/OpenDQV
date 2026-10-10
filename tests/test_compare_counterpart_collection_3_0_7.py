"""
3.0.7: a list or object in compare's counterpart fails with the rule's own
message. The typed collection message names rule.field, so for a collection in
compare_to it pointed at the wrong field ("x" eq [1] with the array in b said
'compare rule on field "a" ... got array'). Codes are the same either way, so
the shared fixture cannot pin this; this file does (single and batch).
"""
import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record


def _rule(op):
    return Rule(name="c", field="a", type="compare", compare_to="b", compare_op=op,
                error_message="a must match b")


def _messages(record, op):
    single = validate_record(record, [_rule(op)], "t")
    batch = validate_batch([record], [_rule(op)], "t")["results"][0]
    return [e["message"] for e in single["errors"]], [e["message"] for e in batch["errors"]]


@pytest.mark.parametrize("op,other", [("eq", [1]), ("neq", {"k": 1}), ("gt", [1]), ("lt", {"k": 1})])
def test_collection_in_the_counterpart_gets_the_rules_own_message(op, other):
    single, batch = _messages({"a": "x", "b": other}, op)
    assert single == batch == ["a must match b"]


@pytest.mark.parametrize("op", ["eq", "gt"])
def test_collection_in_the_own_field_keeps_the_typed_message(op):
    single, batch = _messages({"a": [1], "b": "x"}, op)
    assert single == batch == ['compare rule on field "a" compares a single value, got array — send a string, number or boolean']


def test_collection_on_both_sides_names_the_own_field():
    single, batch = _messages({"a": [1], "b": [2]}, "eq")
    assert single == batch and "got array" in single[0]


def test_collection_against_a_number_keeps_the_typed_message_before_the_number_guard():
    single, batch = _messages({"a": [1], "b": 5}, "gt")
    assert single == batch and "compares a single value" in single[0]


def test_boolean_guard_runs_first():
    single, batch = _messages({"a": [1], "b": True}, "gt")
    assert single == batch == ["a must match b"]
