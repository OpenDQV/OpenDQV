"""
3.0.2: a rule that reads a value as a date ignores the white space around it;
a rule that judges the text as written sees the padding. The cross-engine rows
live in frozen/engine_semantics.jsonl; this file pins the Core-only edges.
"""
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record


def _both(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    return single, batch


def _codes(out):
    return sorted(e["error_code"] for e in out["errors"])


def _same_date():
    return [Rule(name="t_plus_0", type="compare", field="trade", compare_to="exec",
                 compare_op="same_date", error_message="trade date must equal execution date")]


def test_same_date_reads_padded_dates_and_fails_a_mismatch():
    # Before 3.0.2 the [:10] slice of " 2026-01-11" was not a date, so the
    # rule skipped and a T+0 violation passed.
    for out in _both({"trade": " 2026-01-11", "exec": "2026-01-10\n"}, _same_date()):
        assert _codes(out) == ["OPENDQV_COMPARE_T_PLUS_0"]


def test_same_date_reads_padded_dates_and_passes_a_match():
    for out in _both({"trade": " 2026-01-10T09:00:00Z\n", "exec": "2026-01-10"}, _same_date()):
        assert out["valid"] is True


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
