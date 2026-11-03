"""`unique` is the one batch rule judged in DuckDB SQL (validator.py: every
other rule runs the single-path handler per record), so these tests are the
guard on a DuckDB upgrade. Each expectation below was checked identical on
duckdb 1.5.6 and 2.0.0 (2026-11-03); a failure here after a DuckDB bump is a
verdict change, not a test to update."""
import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch


def _failing(values, **rule_kw):
    records = [{} if v is _ABSENT else {"id": v} for v in values]
    rule = Rule(name="u", type="unique", field="id", error_message="dup", **rule_kw)
    results = validate_batch(records, [rule])["results"]
    return [i for i, r in enumerate(results) if not r["valid"]]


_ABSENT = object()


def test_string_duplicates_fail_every_copy_and_nothing_else():
    assert _failing(["a", "b", "a", "c", "b", "d"]) == [0, 1, 2, 4]


def test_integer_duplicates():
    assert _failing([1, 2, 1, 3]) == [0, 2]


def test_null_and_absent_values_are_never_duplicates():
    assert _failing([None, None, _ABSENT, _ABSENT, "x"]) == []


def test_integer_and_equal_float_are_duplicates():
    assert _failing([1, 1.0, 2]) == [0, 1]


def test_integer_and_its_string_spelling_currently_collide():
    # Current behaviour on both 1.5.6 and 2.0.0: DuckDB compares the object
    # column after coercion, so 1 and "1" are one value. Pinned so a DuckDB
    # change surfaces as a failure; whether it SHOULD collide is a conformance
    # question for the managed engine, not settled here.
    assert _failing([1, "1", 2, 2]) == [0, 1, 2, 3]


def test_group_by_on_an_unsent_column_falls_back_to_the_global_sql():
    assert _failing(["1", "1", "2"], group_by=["region"]) == [0, 1]


@pytest.mark.parametrize("distinct, expected_failures", [(250, 1000), (1000, 0)])
def test_a_thousand_row_batch_is_judged_on_the_whole_column(distinct, expected_failures):
    assert len(_failing([f"k{i % distinct}" for i in range(1000)])) == expected_failures
