"""
3.0.3: one date reader. With no layout declared, every rule that reads a date
reads exactly the ISO surface — YYYY-MM-DD, optionally Thh:mm:ss, a fraction,
and Z or ±hh:mm. The cross-engine rows live in frozen/engine_semantics.jsonl;
this file pins the readers those rows do not reach (compare, date_diff, the
age rules), the bounds, and the surface choices not yet confirmed on the
managed engine (fraction length, comma separator).
"""
import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import _read_iso, validate_batch, validate_record


def _both(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    return single, batch


def _codes(out):
    return sorted(e["error_code"] for e in out["errors"])


@pytest.mark.parametrize("value", [
    "2026-01-10", "2026-01-10T08:00:00", "2026-01-10T08:00:00Z", "2026-01-10T08:00:00+01:00",
    "2026-01-10T08:00:00-05:30", "2026-01-10T08:00:00.5", "2026-01-10T08:00:00.123456Z",
    " 2026-01-10\n",
])
def test_reader_accepts_the_iso_surface(value):
    _read_iso(value)


@pytest.mark.parametrize("value", [
    "2026-1-10", "2026-01-10 08:00:00", "20260110", "2026-01-10T08:00Z", "2026-01-10T08:00:00+0100",
    "2026-W02", "2026-01-10t08:00:00z", "2026-01-10Z", "2026-02-30", "2026-01-10T24:00:00",
    "2026-01-10T08:00:60", "2026-01-10T08:00:00+24:00", "2026-01-10T08:00:00.", "2026-01-10 T08:00:00Z",
    "２０２６-01-10",
])
def test_reader_refuses_everything_else(value):
    with pytest.raises(ValueError):
        _read_iso(value)


# Surface choices to confirm on the managed engine: any fraction length
# (fromisoformat and Go's RFC 3339 parser both truncate past nanoseconds),
# and the dot only — DuckDB refuses a comma, and the note's examples use a dot.
def test_reader_accepts_a_long_fraction():
    assert _read_iso("2026-01-10T08:00:00.123456789Z").microsecond == 123456


def test_reader_refuses_a_comma_fraction():
    with pytest.raises(ValueError):
        _read_iso("2026-01-10T08:00:00,5Z")


def _cmp(op="gt"):
    return [Rule(name="end_after_start", type="compare", field="end", compare_to="start",
                 compare_op=op, error_message="bad")]


def test_compare_reads_offsets_as_instants_on_both_paths():
    # 08:30+01:00 is 07:30Z — before 08:00Z as an instant, after it as text.
    for out in _both({"end": "2026-01-10T08:30:00+01:00", "start": "2026-01-10T08:00:00Z"}, _cmp()):
        assert _codes(out) == ["OPENDQV_COMPARE_END_AFTER_START"]


def test_compare_on_space_separated_values_compares_text_on_both_paths():
    # No longer read as dates: "2026-01-10 08:00:00" vs "2026-01-10T07:00:00"
    # compares as text, where " " sorts before "T".
    for out in _both({"end": "2026-01-10 08:00:00", "start": "2026-01-10T07:00:00"}, _cmp()):
        assert _codes(out) == ["OPENDQV_COMPARE_END_AFTER_START"]


def test_date_diff_refuses_a_basic_format_operand_on_both_paths():
    rules = [Rule(name="within", type="date_diff", field="reported", date_diff_field="detected",
                  date_diff_unit="days", min_value=0, max_value=1, error_message="bad")]
    for out in _both({"reported": "2026-01-10", "detected": "20260110"}, rules):
        assert _codes(out) == ["OPENDQV_DATE_DIFF_WITHIN"]


def test_age_match_reads_a_z_date_of_birth_on_both_paths():
    rules = [Rule(name="age_ok", type="age_match", field="age", dob_field="dob",
                  age_tolerance=200, error_message="bad")]
    for out in _both({"age": 36, "dob": "1990-01-10T00:00:00Z"}, rules):
        assert out["valid"] is True


def test_max_age_judges_a_z_date_of_birth_on_both_paths():
    # Before 3.0.3 the single path could not read it and skipped the add-on.
    rules = [Rule(name="dob", type="date_format", field="dob", max_age=5, error_message="too old")]
    for out in _both({"dob": "1990-01-10T00:00:00Z"}, rules):
        assert _codes(out) == ["OPENDQV_DATE_FORMAT_DOB"]


def test_min_age_skips_a_space_separated_date_of_birth_on_both_paths():
    # Unreadable as a date: date_format is the catcher, the add-on skips (2.8.0).
    rules = [Rule(name="dob", type="not_empty", field="dob", min_age=18, error_message="bad")]
    for out in _both({"dob": "2024-01-10 00:00:00"}, rules):
        assert out["valid"] is True


def test_batch_date_format_matches_single_on_a_mixed_batch():
    rules = [Rule(name="when", type="date_format", field="when", error_message="bad")]
    values = ["2026-01-10T08:00:00Z", "2026-1-10", "", None, " 2026-01-10 ", "2026-01-10 08:00:00"]
    records = [{"when": v} for v in values]
    batch = validate_batch(records, rules, "t")["results"]
    for rec, b in zip(records, batch):
        assert b["valid"] is validate_record(rec, rules, "t")["valid"], rec
