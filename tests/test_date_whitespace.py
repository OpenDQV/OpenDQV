"""
3.0.2: a rule that reads a value as a date ignores the white space around it;
a rule that judges the text as written sees the padding. The cross-engine rows
live in frozen/engine_semantics.jsonl (same_date included, confirmed on the
managed engine); this file pins the Core-only edges.
"""
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record


def _both(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    return single, batch


def _codes(out):
    return sorted(e["error_code"] for e in out["errors"])


def test_date_format_with_declared_layout_ignores_padding():
    rules = [Rule(name="d", type="date_format", field="d", format="DD/MM/YYYY", error_message="bad")]
    for out in _both({"d": " 10/01/2026\t"}, rules):
        assert out["valid"] is True


def test_regex_still_refuses_a_trailing_newline():
    rules = [Rule(name="iso", type="regex", field="d", pattern=r"^\d{4}-\d{2}-\d{2}$", error_message="bad")]
    for out in _both({"d": "2026-01-10\n"}, rules):
        assert _codes(out) == ["OPENDQV_REGEX_ISO"]


def test_compare_lt_on_non_dates_still_compares_text_as_written():
    rules = [Rule(name="ordered", type="compare", field="a", compare_to="b", compare_op="lt", error_message="bad")]
    # " b" < "a" as text (space sorts first); trimming would make it "b" < "a" → fail.
    for out in _both({"a": " b", "b": "a"}, rules):
        assert out["valid"] is True
