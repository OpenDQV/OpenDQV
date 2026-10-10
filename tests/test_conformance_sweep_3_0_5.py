"""
3.0.5: the conformance sweep (managed engine, 2026-10-10). The cross-engine
rows are frozen/engine_semantics.jsonl (managed rows 100-158); this file pins
what those rows cannot: anything that depends on today's date, the stored
(lenient) readings, and the batch path's per-record behaviour.
"""
from datetime import datetime, timedelta, timezone

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import (
    _is_field_absent,
    _num,
    _read_layout,
    _semver_tuple,
    validate_batch,
    validate_record,
)


def _both(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    return single, batch


def _codes(out):
    return sorted(e["error_code"] for e in out["errors"])


# ── compare_to: today / now ─────────────────────────────────────────────

def _today_rule(op, to="today"):
    return [Rule(name="t", type="compare", field="ts", compare_to=to, compare_op=op, error_message="bad")]


def test_today_compares_calendar_dates_so_a_timestamp_stamped_today_is_lte_today():
    # Core 3.0.4 compared the instant to midnight, so `lte today` failed every
    # timestamp after 00:00.
    stamp = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    for out in _both({"ts": stamp}, _today_rule("lte")):
        assert out["valid"] is True
    for out in _both({"ts": stamp}, _today_rule("gt")):
        assert out["valid"] is False   # not "after today"


def test_today_yesterday_and_tomorrow():
    today = datetime.now(timezone.utc).date()
    for out in _both({"ts": (today - timedelta(days=1)).isoformat()}, _today_rule("lt")):
        assert out["valid"] is True
    for out in _both({"ts": (today + timedelta(days=1)).isoformat()}, _today_rule("lte")):
        assert out["valid"] is False


def test_now_is_the_instant():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for out in _both({"ts": past}, _today_rule("lt", "now")):
        assert out["valid"] is True
    for out in _both({"ts": past}, _today_rule("gte", "now")):
        assert out["valid"] is False


def test_today_on_a_value_that_is_not_a_date_fails():
    for out in _both({"ts": "soon"}, _today_rule("lte")):
        assert _codes(out) == ["OPENDQV_COMPARE_T"]


# ── white space, absence, numbers ───────────────────────────────────────

@pytest.mark.parametrize("value", ["\u001c", "\u001d", "\u001e", "\u001f"])
def test_information_separators_are_not_white_space(value):
    assert not _is_field_absent(value)


@pytest.mark.parametrize("value", ["", " ", "\t\n", " ", "　", " ", [], {}, None])
def test_absent_values(value):
    assert _is_field_absent(value)


@pytest.mark.parametrize("value", [True, False, [1], {"a": 1}, float("nan"), float("inf"), 10 ** 400, "abc"])
def test_num_refuses(value):
    with pytest.raises(ValueError):
        _num(value)


@pytest.mark.parametrize("rule", [
    Rule(name="r", type="max", field="x", max=10, error_message="bad"),
    Rule(name="r", type="range", field="x", min=0, max=10, error_message="bad"),
])
def test_boolean_is_a_type_mismatch_on_both_paths(rule):
    for out in _both({"x": True}, [rule]):
        assert _codes(out) == ["OPENDQV_TYPE_MISMATCH"]


def test_boolean_operands_fail_the_cross_field_numeric_rules():
    cases = [
        (Rule(name="r", type="ratio_check", field="l", ratio_numerator="a", ratio_denominator="b",
              max=2, error_message="bad"), {"a": True, "b": 1}),
        (Rule(name="r", type="cross_field_range", field="v", cross_min_field="lo", error_message="bad"),
         {"v": 5, "lo": True}),
        (Rule(name="r", type="geospatial_bounds", field="lat", error_message="bad"), {"lat": True}),
        (Rule(name="r", type="age_match", field="age", dob_field="dob", age_tolerance=200,
              error_message="bad"), {"age": True, "dob": "2000-01-01"}),
    ]
    for rule, rec in cases:
        for out in _both(rec, [rule]):
            assert out["valid"] is False, rule.type


def test_a_nan_operand_no_longer_passes_field_sum():
    rule = Rule(name="s", type="field_sum", field="t", sum_fields=["a", "b"], sum_equals=2, error_message="bad")
    for out in _both({"a": float("nan"), "b": 1}, [rule]):
        assert out["valid"] is False


def test_huge_integer_message_names_the_range():
    out = validate_record({"x": 10 ** 400}, [Rule(name="m", type="min", field="x", min=0, error_message="x")], "t")
    assert "floating-point range" in out["errors"][0]["message"]


def test_integer_column_with_a_gap_keeps_its_digits_in_batch():
    # B2: pandas made [12, None] a float column, so 12 read "12.0".
    rule = Rule(name="r", type="regex", field="q", pattern="^12$", error_message="bad")
    res = validate_batch([{"q": 12}, {"q": None}], [rule], "t")["results"]
    assert res[0]["valid"] is True


# ── text rendering, lists and objects ───────────────────────────────────

def test_regex_reads_a_boolean_as_its_json_spelling():
    rule = Rule(name="r", type="regex", field="f", pattern="^true$", error_message="bad")
    for out in _both({"f": True}, [rule]):
        assert out["valid"] is True


@pytest.mark.parametrize("rule", [
    Rule(name="r", type="forbidden_values", field="f", forbidden_values=["x"], error_message="bad"),
    Rule(name="r", type="conditional_value", field="f", must_equal="x", error_message="bad"),
])
def test_a_list_is_not_text(rule):
    for out in _both({"f": ["x"]}, [rule]):
        assert out["errors"][0]["message"] == (
            f'{rule.type} rule on field "f" compares a single value, got array — send a string, number or boolean')


def test_condition_matches_text_as_written_and_never_a_missing_field():
    rule = Rule(name="r", type="regex", field="a", pattern="^x$", error_message="bad",
                condition={"field": "k", "value": "12"})
    for rec, applies in (({"k": 12, "a": "y"}, True), ({"k": 12.0, "a": "y"}, True),
                         ({"k": " 12", "a": "y"}, False), ({"a": "y"}, False), ({"k": None, "a": "y"}, False)):
        for out in _both(rec, [rule]):
            assert out["valid"] is (not applies), rec


def test_required_if_trigger_uses_the_same_rendering():
    rule = Rule(name="r", type="required_if", field="f", required_if={"field": "k", "value": "true"},
                error_message="bad")
    for out in _both({"k": True}, [rule]):
        assert out["valid"] is False


# ── dates ───────────────────────────────────────────────────────────────

def test_repeated_directive_reads_each_occurrence():
    # strptime raised a regex group-redefinition error here.
    assert _read_layout("0505", "%S%S").second == 5
    rule = Rule(name="d", type="date_format", field="d", format="%S%S", error_message="bad")
    for out in _both({"d": "0505"}, [rule]):
        assert out["valid"] is True
    for out in _both({"d": "055"}, [rule]):
        assert out["valid"] is False


@pytest.mark.parametrize("value,valid", [
    ("2026-01-10T08:00:00.5", True), ("2026-01-10T08:00:00.123456", True),
    ("2026-01-10T08:00:00.1234567", False), ("2026-01-10T08:00:00", False),
])
def test_fraction_is_one_to_six_digits(value, valid):
    rule = Rule(name="d", type="date_format", field="d", format="%Y-%m-%dT%H:%M:%S.%f", error_message="bad")
    for out in _both({"d": value}, [rule]):
        assert out["valid"] is valid


def test_calendar_is_checked_after_the_shape():
    rule = Rule(name="d", type="date_format", field="d", format="DD/MM/YYYY", error_message="bad")
    for out in _both({"d": "31/02/2026"}, [rule]):
        assert out["valid"] is False


@pytest.mark.parametrize("fmt,value,valid", [
    ("2006-01-02", "2026-01-10", True),
    ("2006-01-02", "2026-1-10", False),
    ("2006-01-02T15:04:05Z07:00", "2026-01-10T08:00:00Z", True),
    ("2006-01-02T15:04:05Z07:00", "2026-01-10T08:00:00+01:00", True),
    ("2006-01-02T15:04:05Z07:00", "2026-01-10T08:00:00", False),       # the zone is required
    ("2006-01-02T15:04:05Z07:00", "2026-01-10T08:00:00.5Z", False),    # no fraction
])
def test_stored_go_layouts_read_as_aliases(fmt, value, valid):
    rule = Rule(name="d", type="date_format", field="d", format=fmt, error_message="bad")
    for out in _both({"d": value}, [rule]):
        assert out["valid"] is valid


# ── semver ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [("1.2.3", "v1.2.3"), ("1.2.3+b5", "1.2.3"), ("1.2.3-rc.1+b5", "1.2.3-rc.1")])
def test_semver_key_ignores_the_v_and_build_metadata(a, b):
    # 3.0.6: the key is SemVer 2.0.0 precedence (3.0.5 read the triple only,
    # ignoring the pre-release; see test_conformance_sweep_3_0_6)
    assert _semver_tuple(a) == _semver_tuple(b)


def test_semver_value_that_is_not_a_version_fails_on_both_paths():
    rule = Rule(name="v", type="compare", field="a", compare_to="b", compare_op="gte", algorithm="semver",
                error_message="bad")
    for out in _both({"a": "latest", "b": "1.0.0"}, [rule]):
        assert out["valid"] is False


# ── batch: a checker that raises fails that record only ─────────────────

def test_a_raising_checker_fails_closed_per_record_in_batch(monkeypatch):
    from opendqv.core import validator
    real = validator._RULE_HANDLERS["regex"]

    def flaky(value, rule, record=None):
        if value == "boom":
            raise RuntimeError("checker bug")
        return real(value, rule, record)

    monkeypatch.setitem(validator._RULE_HANDLERS, "regex", flaky)
    rule = Rule(name="r", type="regex", field="f", pattern="^a$", error_message="bad", severity="warning")
    res = validate_batch([{"f": "a"}, {"f": "boom"}], [rule], "t")["results"]
    assert res[0]["valid"] is True
    assert res[1]["valid"] is False
    assert res[1]["errors"][0]["error_code"] == "OPENDQV_RULE_ERROR"
    single = validate_record({"f": "boom"}, [rule], "t")
    assert single["errors"][0]["error_code"] == "OPENDQV_RULE_ERROR"
    assert single["errors"][0]["message"] == res[1]["errors"][0]["message"]


@pytest.mark.parametrize("a,b", [(True, 0), (1, True), (True, True), ("true", False)])
def test_compare_with_a_boolean_operand_fails(a, b):
    # Sweep §4: a JSON boolean is not a number on compare either. 3.0.6: and
    # it has no order — gte fails; eq/neq compare its text (3.0.6 tests).
    rule = Rule(name="c", type="compare", field="a", compare_to="b", compare_op="gte", error_message="bad")
    for out in _both({"a": a, "b": b}, [rule]):
        assert _codes(out) == ["OPENDQV_COMPARE_C"]


# ── blind-review findings (3.0.5 pre-merge) ─────────────────────────────

def test_a_failed_lookup_load_is_tried_once_per_batch(monkeypatch):
    from opendqv.core import validator
    calls = []

    def failing(file_path, lookup_field):
        calls.append(file_path)
        raise FileNotFoundError(file_path)

    monkeypatch.setattr(validator, "_load_lookup_set", failing)
    rule = Rule(name="lk", type="lookup", field="f", lookup_file="ref/x.csv", error_message="bad")
    res = validate_batch([{"f": "a"}] * 500, [rule], "t")
    assert res["summary"]["failed"] == 500   # fails closed on every record
    assert len(calls) == 1                   # but loads once
    validate_batch([{"f": "a"}] * 3, [rule], "t")
    assert len(calls) == 2                   # the memo lives for one batch only


@pytest.mark.parametrize("a,b", [(float("nan"), 5), (5, 10 ** 400), ("nan", "5"), ("5", "1e400")])
def test_compare_never_reads_an_unreadable_number_as_text(a, b):
    rule = Rule(name="c", type="compare", field="a", compare_to="b", compare_op="gt", error_message="bad")
    for out in _both({"a": a, "b": b}, [rule]):
        assert out["valid"] is False


def test_submission_check_survives_a_non_string_compare_op():
    from opendqv.core.submission import rule_submission_problems
    assert rule_submission_problems({"name": "r", "type": "compare", "field": "f", "compare_to": "g",
                                     "compare_op": ["gt"]})


def test_age_add_on_on_nan_agrees_on_both_paths():
    rule = Rule(name="r", type="regex", field="f", pattern=".*", min_age=18, error_message="bad")
    single, batch = _both({"f": float("nan")}, [rule])
    assert single["valid"] is batch["valid"] is False


def test_stored_unknown_compare_op_keeps_the_counterpart_check():
    rule = Rule(name="c", type="compare", field="f", compare_to="g", compare_op="after", error_message="bad")
    for out in _both({"f": 5}, [rule]):
        assert out["valid"] is False          # counterpart missing (D10), as on 3.0.4's single path
    for out in _both({"f": 5, "g": 1}, [rule]):
        assert out["valid"] is True           # the unknown operator does not judge
