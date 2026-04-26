"""
tests/test_crt173_v2311_engine_semantics.py — CRT173 v2.3.11.

Pins two engine semantic fixes from the Persona B punch list:

  1. date_format strictness: contract YAML formats are honoured strictly,
     so a YYYY-MM-DD rule rejects "26/04/2026" instead of silently
     accepting locale-ambiguous formats. Both Python and DuckDB paths
     translate human-readable patterns (YYYY-MM-DD) to strftime codes
     (%Y-%m-%d) so writers don't need to know strftime.

  2. Context-override mangled error envelopes: when a context override is
     keyed by RULE NAME (e.g. proof_of_play's `revenue_ceiling`) the
     resolved rule must keep its original field, type, and error_code —
     not mint a phantom not_empty rule whose `field` is the rule name.
     The phantom previously poisoned top_failing_fields[] with the
     rule name, breaking downstream aggregation.
"""
from pathlib import Path


from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import (
    _check_date_format,
    _human_to_strptime,
    validate_record,
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


# 2 ──────────────────────────────────────────────────────────────────
class TestContextOverrideRuleNameMatch:
    """
    proof_of_play's `billing` context overrides keys `revenue_ceiling` and
    `dwell_seconds_max` — both are RULE names, not column names. The
    resolved rules must keep their original field bindings.
    """

    def test_rule_name_match_preserves_field_and_type(self):
        reg = ContractRegistry(Path("opendqv/contracts"))
        contract = reg.get("proof_of_play")
        rules = reg.get_rules_with_context(contract, context="billing")
        rc = next(r for r in rules if r.name == "revenue_ceiling")
        assert rc.field == "revenue_gbp"
        assert rc.type == "max"
        assert rc.max_value == 500000
        assert rc.severity.value == "error"
        assert rc.cached_error_code == "OPENDQV_MAX_REVENUE_CEILING"

    def test_no_phantom_rules_minted(self):
        reg = ContractRegistry(Path("opendqv/contracts"))
        contract = reg.get("proof_of_play")
        rules = reg.get_rules_with_context(contract, context="billing")
        phantom_names = [r.name for r in rules if r.name.startswith("ctx_billing_")]
        assert phantom_names == [], (
            f"context-override should not mint phantom rules: {phantom_names}"
        )

    def test_error_envelope_field_matches_column(self):
        reg = ContractRegistry(Path("opendqv/contracts"))
        contract = reg.get("proof_of_play")
        rules = reg.get_rules_with_context(contract, context="billing")
        rec = {
            "panel_id": "LGM-UK-00001",
            "market": "UK",
            "panel_type": "DIGITAL",
            "impression_start": "2024-01-01T00:00:00Z",
            "impression_end": "2024-01-01T00:00:30Z",
            "transaction_type": "CHARGE",
            "revenue_gbp": 600000.0,
            "advertiser_id": "ADV-12345678",
            "creative_id": "CRT-1",
            "campaign_ref": "CMP-1",
            "dwell_seconds": 30,
        }
        result = validate_record(rec, rules, contract_name="proof_of_play", context="billing")
        rc_err = next(e for e in result["errors"] if e["rule"] == "revenue_ceiling")
        assert rc_err["field"] == "revenue_gbp"
        assert rc_err["error_code"] == "OPENDQV_MAX_REVENUE_CEILING"
        assert "billing reconciliation" in rc_err["message"]
        # No error has field set to a rule name
        for e in result["errors"]:
            assert e["field"] not in {r.name for r in rules}, (
                f"error field '{e['field']}' is a rule name, not a column"
            )


# 3 ──────────────────────────────────────────────────────────────────
class TestContextOverrideFieldNameMatchUnbroken:
    """Field-name match (broad) must still work for contracts like customer.yaml."""

    def test_field_name_match_modifies_all_rules_on_field(self):
        reg = ContractRegistry(Path("opendqv/contracts"))
        contract = reg.get("customer")
        rules = reg.get_rules_with_context(contract, context="kids_app")
        age_rules = [r for r in rules if r.field == "age"]
        assert len(age_rules) >= 2
        for r in age_rules:
            assert r.type == "range"
            assert r.min_value == 5
            assert r.max_value == 17
