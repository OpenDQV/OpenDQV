"""
v2.3.25 — checksum unknown-algorithm fallback fails closed.

Pre-fix: `_validate_checksum` returned True with a warning log when
the algorithm key was unrecognised. A typo'd YAML key (e.g.
`ibn_mod97` instead of `iban_mod97`) would silently pass every record;
the warning could be missed in the engine log; downstream systems saw
records as validated when they actually weren't.

Post-fix: returns False, log the warning. The rule fails closed —
the error surfaces at the validation response, where a regulated firm
actually watches.

Found during the v2.3.25 isin_mod11→isin_luhn rename inside-check
(2026-04-29). Pilot directive: fold into v2.3.25 alongside the rename.
"""

import logging

import pytest


def _make_rule(algorithm: str):
    from opendqv.core.rule_parser import Rule, Severity
    return Rule(
        name="checksum_test",
        field="code",
        type="checksum",
        checksum_algorithm=algorithm,
        severity=Severity.ERROR,
        error_message="Failed",
    )


class TestUnknownChecksumFailsClosed:
    """v2.3.25: typo'd or invented checksum algorithm keys must fail
    records, not silently pass them."""

    @pytest.mark.parametrize("algorithm", [
        "ibn_mod97",          # typo of iban_mod97
        "lei_mod98",          # typo of lei_mod97
        "iisin_luhn",         # typo of isin_luhn
        "isin_mod11",         # legacy v2.3.23-24 key, renamed v2.3.25
        "made_up_algo",       # arbitrary
        "",                   # empty string
    ])
    def test_unknown_algorithm_returns_false(self, algorithm):
        from opendqv.core.validator import _validate_checksum
        result = _validate_checksum("anything", algorithm)
        assert result is False, (
            f"v2.3.25: _validate_checksum must return False for "
            f"unknown algorithm {algorithm!r}. Pre-fix it returned "
            f"True (passthrough). Got: {result}"
        )

    def test_unknown_algorithm_logs_warning(self, caplog):
        from opendqv.core.validator import _validate_checksum
        with caplog.at_level(logging.WARNING, logger="opendqv.core.validator"):
            _validate_checksum("anything", "made_up_algo")
        assert any("made_up_algo" in r.message for r in caplog.records), (
            f"v2.3.25: _validate_checksum must log a warning naming "
            f"the unknown algorithm. Got: "
            f"{[r.message for r in caplog.records]}"
        )

    def test_warning_lists_supported_algorithms(self, caplog):
        """Operator who sees the warning should be able to fix the
        typo from the warning text alone."""
        from opendqv.core.validator import _validate_checksum
        with caplog.at_level(logging.WARNING, logger="opendqv.core.validator"):
            _validate_checksum("x", "ibn_mod97")  # typo
        msg = " ".join(r.message for r in caplog.records)
        assert "iban_mod97" in msg, (
            f"warning should list supported algorithms so an operator "
            f"can spot the typo. Got: {msg!r}"
        )
        assert "isin_luhn" in msg, msg


class TestEngineLevelFailClosed:
    """End-to-end: a contract with a typo'd checksum_algorithm now
    rejects records under that rule. Pre-fix the rule was a no-op."""

    def test_validate_record_rejects_under_typo_algorithm(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record
        rule = Rule(
            name="iban_check_with_typo",
            field="iban",
            type="checksum",
            checksum_algorithm="ibn_mod97",  # typo — pre-fix would pass; post-fix fails
            severity=Severity.ERROR,
            error_message="Invalid IBAN",
        )
        result = validate_record({"iban": "GB82WEST12345698765432"}, [rule])
        assert result["valid"] is False, (
            f"v2.3.25: a real-world IBAN under a typo'd algorithm key "
            f"must be rejected, not silently passed. Got: {result}"
        )
