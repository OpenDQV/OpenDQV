"""
v2.3.23 outside-review fix #3 — type confusion in min/max/range rules.

Persona B 2026-04-28:
> "Type confusion masquerades as a min-rule violation. Sending
>  price: 'not a number' (string) fired price_min 'must be >= 0' —
>  coerced and reported as below minimum, not as a type error.
>  Customer impact: producer fixes the wrong thing — checks numeric
>  values that already pass, instead of fixing the type contract."

Sonnet's pre-impl review (aec401d0381905d97):
  - Apply to _check_min, _check_max, _check_range (single + batch)
  - Defer _check_date_diff, _check_ratio_check, _check_geospatial_bounds,
    _check_age_match (different complexity)
  - Length rules (_check_min_length, _check_max_length) untouched —
    they str() everything; cannot see type mismatch
  - New error_code: OPENDQV_TYPE_MISMATCH
  - Generated message: field name + rule type + Python type of value
    (NOT the value — PII risk)
  - Test: parametrize across rule types × non-numeric values + a
    regression guard that legitimate boundary violations still emit
    the rule's own error_code
"""

import pytest


# ── Single-record path ────────────────────────────────────────────────

class TestSingleRecordTypeMismatch:
    @pytest.mark.parametrize("rule_type,kwargs,non_numeric", [
        ("min",   {"min_value": 0.0},                     "not a number"),
        ("max",   {"max_value": 100.0},                   "not a number"),
        ("range", {"min_value": 0.0, "max_value": 100.0}, "not a number"),
        ("min",   {"min_value": 0.0},                     {"nested": "obj"}),
        ("range", {"min_value": 0.0, "max_value": 100.0}, [1, 2, 3]),
    ])
    def test_non_numeric_value_emits_type_mismatch_code(self, rule_type, kwargs, non_numeric):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record

        rules = [
            Rule(
                name=f"price_{rule_type}", field="price", type=rule_type,
                severity=Severity.ERROR,
                error_message=f"price violates {rule_type}",
                **kwargs,
            ),
        ]
        result = validate_record({"price": non_numeric}, rules)
        assert not result["valid"], result
        # Must surface a type-mismatch error code, not the rule's own code.
        codes = [e.get("error_code") for e in result["errors"]]
        assert "OPENDQV_TYPE_MISMATCH" in codes, (
            f"v2.3.23 outside-review #3: non-numeric value on {rule_type} "
            f"rule must emit error_code OPENDQV_TYPE_MISMATCH, not the "
            f"rule's own code. Producer fixes the wrong thing if we "
            f"label this as a value violation. Got codes: {codes}"
        )
        # Message must mention type, not the value (PII risk).
        type_mismatch_err = next(e for e in result["errors"] if e["error_code"] == "OPENDQV_TYPE_MISMATCH")
        msg = type_mismatch_err["message"]
        # Field name and type name must be in message; value must NOT.
        assert "price" in msg, msg
        assert type(non_numeric).__name__ in msg, msg
        # Must not echo the offending value.
        if isinstance(non_numeric, str):
            assert non_numeric not in msg, (
                f"Type-mismatch message must not echo the offending "
                f"value (PII risk). Got: {msg}"
            )

    def test_legitimate_min_violation_still_emits_rule_error_code(self):
        """Regression guard per Sonnet: numeric value below threshold
        must still emit the rule's own error_code, not the new
        OPENDQV_TYPE_MISMATCH code."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record

        rules = [
            Rule(
                name="price_min", field="price", type="min",
                min_value=0.0,
                severity=Severity.ERROR,
                error_message="price must be >= 0",
            ),
        ]
        result = validate_record({"price": -5.0}, rules)
        assert not result["valid"]
        codes = [e.get("error_code") for e in result["errors"]]
        # Must be the rule's own code, NOT type-mismatch.
        assert "OPENDQV_TYPE_MISMATCH" not in codes
        assert any(c.startswith("OPENDQV_MIN_") for c in codes), codes

    def test_legitimate_max_violation_still_emits_rule_error_code(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record

        rules = [
            Rule(
                name="price_max", field="price", type="max",
                max_value=100.0,
                severity=Severity.ERROR,
                error_message="price must be <= 100",
            ),
        ]
        result = validate_record({"price": 200.0}, rules)
        assert not result["valid"]
        codes = [e.get("error_code") for e in result["errors"]]
        assert "OPENDQV_TYPE_MISMATCH" not in codes
        assert any(c.startswith("OPENDQV_MAX_") for c in codes), codes

    def test_int_value_on_min_rule_is_not_type_mismatch(self):
        """Integer values must not trip the type guard — they're
        legitimately numeric. Only strings, dicts, lists are type
        mismatches."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record

        rules = [
            Rule(
                name="age_min", field="age", type="min", min_value=18.0,
                severity=Severity.ERROR, error_message="age must be >= 18",
            ),
        ]
        result = validate_record({"age": 25}, rules)
        assert result["valid"]


# ── Batch path ────────────────────────────────────────────────────────

class TestBatchPathTypeMismatch:
    """Sonnet's pre-impl review: dual-path discipline. Batch path uses
    DuckDB CAST which raises on string-typed columns. The caller
    catches and currently reports every row as failing the rule's own
    code. Must surface OPENDQV_TYPE_MISMATCH on rows whose value is
    non-numeric, while still applying the numeric rule to rows whose
    value IS numeric."""

    def test_batch_with_string_value_emits_type_mismatch(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(
                name="price_min", field="price", type="min", min_value=0.0,
                severity=Severity.ERROR,
                error_message="price must be >= 0",
            ),
        ]
        records = [
            {"price": 50.0},                # numeric, passes
            {"price": "not a number"},      # type mismatch
            {"price": -10.0},               # numeric, fails the rule
        ]
        result = validate_batch(records, rules)
        # Three records: index 0 valid, index 1 type mismatch, index 2 rule fail.
        results = result["results"]
        assert results[0]["valid"], results[0]
        assert not results[1]["valid"], results[1]
        type_mismatch_found = any(
            e.get("error_code") == "OPENDQV_TYPE_MISMATCH"
            for e in results[1]["errors"]
        )
        assert type_mismatch_found, (
            f"v2.3.23 batch type mismatch: index 1 must emit "
            f"OPENDQV_TYPE_MISMATCH for string value. Got: {results[1]}"
        )
        assert not results[2]["valid"], results[2]
        # Index 2 is a legitimate rule violation — must NOT emit
        # type-mismatch code.
        index_2_codes = [e.get("error_code") for e in results[2]["errors"]]
        assert "OPENDQV_TYPE_MISMATCH" not in index_2_codes
