"""#145 — a cross-field rule that fails because its counterpart is absent or blank
(D10) carries `counterpart_missing: true`; a real comparison failure does not.
Same error_code, severity and message on both paths."""
from __future__ import annotations

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record

CASES = [
    # (rule, record where the counterpart is missing, record where the comparison genuinely fails)
    (Rule(name="cmp", type="compare", field="end", compare_to="start", compare_op="gte", error_message="end>=start"),
     {"end": "2026-01-02"}, {"end": "2026-01-01", "start": "2026-01-02"}),
    (Rule(name="dd", type="date_diff", field="end", date_diff_field="start", date_diff_unit="days",
          min_value=0, max_value=5, error_message="within 5 days"),
     {"end": "2026-01-02", "start": ""}, {"end": "2026-02-01", "start": "2026-01-01"}),
    (Rule(name="rc", type="ratio_check", field="num", ratio_numerator="num", ratio_denominator="den",
          min_value=0, max_value=1, error_message="ratio"),
     {"num": 1}, {"num": 5, "den": 1}),
    (Rule(name="fs", type="field_sum", field="a", sum_fields=["a", "b"], sum_equals=10, error_message="sum"),
     {"a": 4}, {"a": 4, "b": 4}),
    (Rule(name="am", type="age_match", field="age", dob_field="dob", error_message="age matches dob"),
     {"age": 30, "dob": "   "}, {"age": 99, "dob": "1990-01-01"}),
]


def _first_error(out):
    return out["errors"][0]


@pytest.mark.parametrize("rule,missing_rec,real_rec", CASES, ids=[c[0].type for c in CASES])
def test_marker_on_single_path(rule, missing_rec, real_rec):
    miss = validate_record(missing_rec, [rule], "t")
    real = validate_record(real_rec, [rule], "t")
    assert not miss["valid"] and not real["valid"]
    e_miss, e_real = _first_error(miss), _first_error(real)
    assert e_miss["counterpart_missing"] is True
    assert "counterpart_missing" not in e_real
    for k in ("error_code", "severity", "message", "rule", "field"):
        assert e_miss[k] == e_real[k], k


@pytest.mark.parametrize("rule,missing_rec,real_rec", CASES, ids=[c[0].type for c in CASES])
def test_marker_on_batch_path_matches_single(rule, missing_rec, real_rec):
    out = validate_batch([missing_rec, real_rec], [rule], "t")
    b_miss, b_real = out["results"][0]["errors"][0], out["results"][1]["errors"][0]
    assert b_miss["counterpart_missing"] is True
    assert "counterpart_missing" not in b_real
    s_miss = _first_error(validate_record(missing_rec, [rule], "t"))
    assert b_miss == s_miss   # byte-identical entry across paths


def test_shape_unchanged_for_ordinary_failures():
    r = Rule(name="ne", type="not_empty", field="f")
    for e in (validate_record({}, [r], "t")["errors"] + validate_batch([{}], [r], "t")["results"][0]["errors"]):
        assert set(e) == {"field", "rule", "message", "severity", "error_code"}


def test_warning_severity_carries_marker_too():
    r = Rule(name="cmp", type="compare", field="end", compare_to="start", compare_op="gte", severity="warning")
    out = validate_record({"end": "x"}, [r], "t")
    assert out["valid"] and out["warnings"][0]["counterpart_missing"] is True


def test_field_sum_absent_operand_is_not_zeroed_on_batch_path():
    """Residual D10 gap found while marking: batch summed the present operands and
    passed a record whose remaining fields happened to hit the target."""
    r = Rule(name="fs", type="field_sum", field="a", sum_fields=["a", "b"], sum_equals=10, error_message="sum")
    rec = {"a": 10}   # b absent — total of what is present == 10
    single = validate_record(rec, [r], "t")
    batch = validate_batch([rec], [r], "t")["results"][0]
    assert not single["valid"] and not batch["valid"]
    assert batch["errors"][0] == single["errors"][0]
    assert batch["errors"][0]["counterpart_missing"] is True
