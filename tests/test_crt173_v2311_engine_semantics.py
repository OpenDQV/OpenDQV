"""
tests/test_crt173_v2311_engine_semantics.py — CRT173 v2.3.11.

Pins an engine semantic fix from the Persona B punch list:

  1. date_format strictness: contract YAML formats are honoured strictly,
     so a YYYY-MM-DD rule rejects "26/04/2026" instead of silently
     accepting locale-ambiguous formats. Both Python and DuckDB paths
     translate human-readable patterns (YYYY-MM-DD) to strftime codes
     (%Y-%m-%d) so writers don't need to know strftime.

  (The second fix — context-override error envelopes — was retired with
  context overrides in 3.0.0.)
"""
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import (
    _check_date_format,
    _human_to_strptime,
)


# 1 ──────────────────────────────────────────────────────────────────
class TestDateFormatStrictness:

    def test_human_pattern_translated(self):
        assert _human_to_strptime("YYYY-MM-DD") == "%Y-%m-%d"
        assert _human_to_strptime("YYYY-MM-DD HH:MM:SS") == "%Y-%m-%d %H:%M:%S"
        assert _human_to_strptime("DD/MM/YYYY") == "%d/%m/%Y"

    def test_strftime_codes_pass_through(self):
        assert _human_to_strptime("%Y-%m-%d") == "%Y-%m-%d"

    def test_iso_date_accepted_against_yyyy_mm_dd(self):
        rule = Rule(
            name="valid_date",
            type="date_format",
            field="date",
            format="YYYY-MM-DD",
            error_message="must be YYYY-MM-DD",
        )
        assert _check_date_format("2024-01-15", rule) is None

    def test_locale_ambiguous_rejected_against_yyyy_mm_dd(self):
        rule = Rule(
            name="valid_date",
            type="date_format",
            field="date",
            format="YYYY-MM-DD",
            error_message="must be YYYY-MM-DD",
        )
        assert _check_date_format("26/04/2026", rule) == "must be YYYY-MM-DD"

    def test_invalid_calendar_date_rejected(self):
        rule = Rule(
            name="valid_date",
            type="date_format",
            field="date",
            format="YYYY-MM-DD",
            error_message="must be YYYY-MM-DD",
        )
        assert _check_date_format("2024-13-99", rule) == "must be YYYY-MM-DD"

    def test_default_iso_date_or_datetime_when_no_format(self):
        rule = Rule(
            name="valid_date",
            type="date_format",
            field="date",
            error_message="must be ISO 8601",
        )
        assert _check_date_format("2024-01-15", rule) is None
        assert _check_date_format("2024-01-15T10:30:00", rule) is None
        assert _check_date_format("01/15/2024", rule) == "must be ISO 8601"

