"""
#147 — `_BATCH_BRANCH_TYPES` must equal the set of rule types that actually
have a native branch in `_batch_check_rule_inner`; every handler type outside
it reaches the per-record fallback. Pinned at source level so a type that
gains a branch without leaving the set (double evaluation) or loses one
without joining it (silent fallback) fails here.

#148 — `Rule.optional` is engine-inert: it changes no verdict on either path.
The day it becomes engine-visible, this test must be updated deliberately.
"""
from __future__ import annotations

import inspect
import re

import pytest

from opendqv.core import validator
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import _BATCH_BRANCH_TYPES, _RULE_HANDLERS, validate_batch, validate_record

_BRANCH_RE = re.compile(r"""rule\.type\s*(?:==\s*["']([a-z_]+)["']|in\s*\(([^)]*)\))""")


def _native_branch_types() -> set[str]:
    src = inspect.getsource(validator._batch_check_rule_inner)
    found: set[str] = set()
    for m in _BRANCH_RE.finditer(src):
        if m.group(1):
            found.add(m.group(1))
        else:
            found.update(re.findall(r"""["']([a-z_]+)["']""", m.group(2)))
    return found


class TestBatchBranchTypes:
    def test_constant_equals_native_branches(self):
        native = _native_branch_types()
        assert native, "could not find any rule.type branch in _batch_check_rule_inner"
        assert set(_BATCH_BRANCH_TYPES) == native, (
            f"listed-but-no-branch: {sorted(set(_BATCH_BRANCH_TYPES) - native)}; "
            f"branch-but-not-listed (would double-evaluate): {sorted(native - set(_BATCH_BRANCH_TYPES))}"
        )

    def test_every_handler_type_is_either_native_or_fallback(self):
        handlers = set(_RULE_HANDLERS)
        assert set(_BATCH_BRANCH_TYPES) <= handlers
        fallback = handlers - set(_BATCH_BRANCH_TYPES)
        assert fallback == {"age_match", "conditional_lookup"}, sorted(fallback)

    @pytest.mark.parametrize("rtype", sorted(set(_RULE_HANDLERS) - set(_BATCH_BRANCH_TYPES)))
    def test_fallback_type_agrees_with_single_path(self, rtype):
        """A fallback type is evaluated per record with the single-path handler."""
        rule = {"age_match": Rule(name="am", type="age_match", field="age", dob_field="dob"),
                "conditional_lookup": Rule(name="cl", type="conditional_lookup", field="k",
                                           condition={"field": "t", "value": "x"}, allowed_values=["a"])}[rtype]
        recs = [{"age": 30, "dob": "1990-01-01", "k": "zzz", "t": "x"},
                {"age": 99, "dob": "1990-01-01", "k": "a", "t": "x"}]
        single = [validate_record(r, [rule], "t")["valid"] for r in recs]
        batch = [x["valid"] for x in validate_batch(recs, [rule], "t")["results"]]
        assert single == batch


_TYPE_FIXTURES: list[tuple[dict, list[dict]]] = [
    ({"type": "not_empty", "field": "f"}, [{"f": "x"}, {"f": ""}, {}]),
    ({"type": "regex", "field": "f", "pattern": "^a+$"}, [{"f": "aaa"}, {"f": "b"}, {"f": ""}, {}]),
    ({"type": "min", "field": "n", "min_value": 1}, [{"n": 5}, {"n": 0}, {}]),
    ({"type": "range", "field": "n", "min_value": 1, "max_value": 3}, [{"n": 2}, {"n": 9}, {"n": None}]),
    ({"type": "allowed_values", "field": "f", "allowed_values": ["a"]}, [{"f": "a"}, {"f": "z"}, {"f": ""}]),
    ({"type": "date_format", "field": "d", "format": "%Y-%m-%d"}, [{"d": "2026-01-01"}, {"d": "nope"}, {}]),
    ({"type": "compare", "field": "a", "compare_to": "b", "compare_op": "lt"}, [{"a": 1, "b": 2}, {"a": 3, "b": 2}, {"a": 1}]),
    ({"type": "required_if", "field": "f", "required_if": {"field": "t", "value": "x"}}, [{"t": "x", "f": "v"}, {"t": "x"}, {"t": "y"}]),
]


class TestOptionalIsInert:
    @pytest.mark.parametrize("spec,records", _TYPE_FIXTURES, ids=[s["type"] for s, _ in _TYPE_FIXTURES])
    def test_optional_changes_no_verdict_on_either_path(self, spec, records):
        plain = Rule(name="r", **spec)
        opt = Rule(name="r", optional=True, **spec)
        assert opt.optional is True and plain.optional is False
        for rec in records:
            assert validate_record(rec, [plain], "t")["valid"] == validate_record(rec, [opt], "t")["valid"], rec
        b1 = [x["valid"] for x in validate_batch(records, [plain], "t")["results"]]
        b2 = [x["valid"] for x in validate_batch(records, [opt], "t")["results"]]
        assert b1 == b2

    def test_optional_round_trips_through_model_dump(self):
        r = Rule(name="r", type="not_empty", field="f", optional=True)
        assert r.model_dump(exclude_none=True)["optional"] is True
