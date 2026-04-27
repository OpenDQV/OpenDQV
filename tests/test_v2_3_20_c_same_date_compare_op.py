"""
v2.3.20 Cluster C (P1.2) — same_date compare_op for cross-field date equality.

Reviewer's repro: ``trade_date=2024-01-15`` with
``execution_timestamp=2026-04-25T...`` PASSED a rule called
``trade_date_matches_execution_date`` because v2.3.17 Q14 implemented
it as a regex on ``trade_date`` alone. The rule name lied.

v2.3.20 introduces ``compare_op: same_date``. The implementation
extracts the ``YYYY-MM-DD`` slice (``str(value)[:10]``) from both sides
before comparing. Works for ``YYYY-MM-DD`` and
``YYYY-MM-DDTHH:MM:SS.ffffffZ`` — both yield the same date portion.

Sonnet's highest-risk-dep flag: this rule MUST cover both matching and
mismatched cross-day pairs. Implementation wrong in either direction
breaks regulator-fidelity for every mifid transaction report.
"""


from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_record


def _mk_rule():
    return Rule(
        name="trade_date_matches_execution_date",
        field="trade_date",
        type="compare",
        compare_to="execution_timestamp",
        compare_op="same_date",
        severity="error",
        error_message="trade_date must equal the date portion of execution_timestamp",
    )


class TestSameDateCompareOp:
    def test_matching_date_portion_passes(self):
        """trade_date YYYY-MM-DD equals date portion of execution_timestamp ISO datetime → pass."""
        rules = [_mk_rule()]
        result = validate_record(
            {"trade_date": "2026-04-26", "execution_timestamp": "2026-04-26T14:30:15.123456Z"},
            rules,
        )
        assert result["valid"] is True, f"got errors: {result['errors']}"

    def test_mismatched_date_portion_fails(self):
        """The exact reviewer repro shape — trade_date 2024-01-15 with
        execution_timestamp 2026-04-25 must FAIL after v2.3.20."""
        rules = [_mk_rule()]
        result = validate_record(
            {"trade_date": "2024-01-15", "execution_timestamp": "2026-04-25T10:00:00.000000Z"},
            rules,
        )
        assert result["valid"] is False, "reviewer's exact repro must now fail"
        assert any(
            e["rule"] == "trade_date_matches_execution_date" for e in result["errors"]
        ), f"errors should name the rule: {result['errors']}"

    def test_off_by_one_day_fails(self):
        """Cross-day boundary check — 2026-04-26 vs 2026-04-27 must fail."""
        rules = [_mk_rule()]
        result = validate_record(
            {"trade_date": "2026-04-26", "execution_timestamp": "2026-04-27T00:00:01.000000Z"},
            rules,
        )
        assert result["valid"] is False
        assert any(e["rule"] == "trade_date_matches_execution_date" for e in result["errors"])

    def test_same_date_in_iso_basic_form_passes(self):
        """trade_date and execution_timestamp both ISO date (no T) → pass."""
        rules = [_mk_rule()]
        result = validate_record(
            {"trade_date": "2026-04-26", "execution_timestamp": "2026-04-26"},
            rules,
        )
        assert result["valid"] is True

    def test_field_absent_passes_silently(self):
        """If trade_date is absent, this rule does not fire (other rules
        catch the missing-field case). Same convention as the rest of
        the compare-rule family."""
        rules = [_mk_rule()]
        result = validate_record({"execution_timestamp": "2026-04-26T10:00:00.000000Z"}, rules)
        # No trade_date field → rule sees absent value → returns None
        # (other rules — like trade_date_required — catch this case).
        compare_errs = [
            e for e in result["errors"]
            if e["rule"] == "trade_date_matches_execution_date"
        ]
        assert not compare_errs, "absent-field case must not fire compare rule"

    def test_other_field_missing_returns_error(self):
        """If trade_date is present but execution_timestamp is missing,
        the comparison cannot run — emits the rule's error_message
        (consistent with existing _check_compare behaviour)."""
        rules = [_mk_rule()]
        result = validate_record({"trade_date": "2026-04-26"}, rules)
        # Bug here would be silent pass. Asserts the rule fires.
        compare_errs = [
            e for e in result["errors"]
            if e["rule"] == "trade_date_matches_execution_date"
        ]
        assert compare_errs, "missing compare_to field must surface the rule's error"

    def test_mifid_contract_full_validate_catches_reviewer_repro(self, client, auth_headers):
        """End-to-end against the bundled mifid_transaction_report
        contract: the reviewer's exact 2024 vs 2026 repro must fail."""
        body = {
            "contract": "mifid_transaction_report",
            "record": {
                "transaction_reference": "TXN-2026-DPE-RECHECK",
                "execution_timestamp": "2026-04-25T10:00:00.000000Z",
                "trade_date": "2024-01-15",  # Reviewer's exact repro
                "reporting_firm_lei": "529900T8BM49AURSDO55",
                "executing_entity_lei": "529900T8BM49AURSDO55",
                "venue_mic": "XLON",
                "instrument_isin": "GB00B03MLX29",
                "buyer_id_type": "lei",
                "buyer_id": "529900T8BM49AURSDO55",
                "seller_id_type": "lei",
                "seller_id": "529900T8BM49AURSDO55",
                "price": 100.5,
                "price_type": "monetary",
                "currency": "GBP",
                "quantity": 1000,
                "quantity_type": "units",
                "buy_sell_indicator": "BUYI",
                "investment_decision_within_firm": "529900T8BM49AURSDO55",
                "execution_within_firm": "529900T8BM49AURSDO55",
                "transaction_type": "buy",
                "reviewed_by": "ops-alice",
                "review_date": "2026-04-25",
            },
        }
        r = client.post(
            "/api/v1/validate?allow_draft=true", json=body, headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body_json = r.json()
        assert body_json["valid"] is False, (
            "v2.3.20 P1.2 reviewer repro: trade_date 2024 with "
            "execution_timestamp 2026 must FAIL. Got valid=True — the "
            "same_date compare_op fix has regressed."
        )
        assert any(
            e["rule"] == "trade_date_matches_execution_date"
            for e in body_json.get("errors", [])
        ), f"trade_date_matches_execution_date must name itself: {body_json}"
