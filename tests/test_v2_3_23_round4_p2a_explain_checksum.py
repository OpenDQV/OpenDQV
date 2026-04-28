"""
v2.3.23 round-4 P2-A — explain_error checksum templates carry real
known-valid identifiers + curated_message surfaces the rule's
authored error_message.

Persona B 2026-04-28 outside review #4 P2:
> for reporting_firm_lei_valid it returns "(a valid lei_mod97
> identifier)" as a literal string. The contract's own error_message
> field already carries the curated example 2026-04-27T14:30:15.123456Z.

Sonnet pre-impl review (a68190bfb4ab4e4cb) verdict: option C — both
fixes, in order A then B.
  A. Hardcoded valid examples for the 4 known checksum algorithms
     (lei_mod97, isin_mod11, iban_mod97, mod10_gs1). Stable public
     identifiers; freshness is a grep fix.
  B. Surface rule.error_message verbatim as curated_message — full
     string, no extraction (extraction logic misfires on URLs and
     unrelated dates in the message).

Tests cover:
  - LEI/ISIN/IBAN/GTIN templates emit real public examples — explicit
    presence assertion (529900T8BM49AURSDO55, US0378331005, etc.).
  - Stub literal "(a valid <algo> identifier)" is GONE.
  - invalid_examples dropped.
  - curated_message carries the rule's error_message verbatim.
"""



VALID_LEI = "529900T8BM49AURSDO55"
VALID_ISIN = "US0378331005"
VALID_IBAN = "DE89370400440532013000"
VALID_GTIN = "036000291452"


# ── _checksum template emits real examples ─────────────────────────────

class TestChecksumTemplateEmitsRealExamples:

    def test_lei_mod97_template_emits_real_lei(self):
        from opendqv.core.explainer import _checksum
        from opendqv.core.validator import _validate_checksum
        result = _checksum("reporting_firm_lei", "lei_mod97")
        # Real LEI present.
        assert VALID_LEI in result["valid_examples"], (
            f"v2.3.23 round-4 P2-A: lei_mod97 template must emit a "
            f"known-valid LEI from GLEIF golden-copy. Got: "
            f"{result['valid_examples']}"
        )
        # Stub literal absent.
        for ex in result["valid_examples"]:
            assert "a valid lei_mod97 identifier" not in ex, (
                f"v2.3.22 stub literal must not appear: {ex!r}"
            )
        # Every emitted example actually validates.
        for ex in result["valid_examples"]:
            assert _validate_checksum(ex, "lei_mod97"), (
                f"emitted example {ex!r} fails the lei_mod97 check it "
                f"claims to demonstrate"
            )

    def test_isin_mod11_template_emits_real_isin(self):
        from opendqv.core.explainer import _checksum
        from opendqv.core.validator import _validate_checksum
        result = _checksum("instrument_isin", "isin_mod11")
        assert VALID_ISIN in result["valid_examples"]
        for ex in result["valid_examples"]:
            assert _validate_checksum(ex, "isin_mod11"), (
                f"emitted ISIN example {ex!r} fails isin_mod11"
            )

    def test_iban_mod97_template_emits_real_iban(self):
        from opendqv.core.explainer import _checksum
        from opendqv.core.validator import _validate_checksum
        result = _checksum("account_iban", "iban_mod97")
        assert VALID_IBAN in result["valid_examples"]
        for ex in result["valid_examples"]:
            assert _validate_checksum(ex, "iban_mod97"), (
                f"emitted IBAN example {ex!r} fails iban_mod97"
            )

    def test_gtin_mod10_template_emits_real_gtin(self):
        from opendqv.core.explainer import _checksum
        from opendqv.core.validator import _validate_checksum
        result = _checksum("product_gtin", "mod10_gs1")
        assert VALID_GTIN in result["valid_examples"]
        for ex in result["valid_examples"]:
            assert _validate_checksum(ex, "mod10_gs1"), (
                f"emitted GTIN example {ex!r} fails mod10_gs1"
            )

    def test_unknown_algorithm_falls_back_honestly(self):
        """Unknown algorithm: empty examples + honest "see the rule's
        error_message" hint in the explanation. No stub literal."""
        from opendqv.core.explainer import _checksum
        result = _checksum("test_field", "unknown_algo")
        assert result["valid_examples"] == []
        # No "(a valid unknown_algo identifier)" stub literal.
        assert "a valid unknown_algo identifier" not in result["explanation"]
        assert "no example auto-generated" in result["explanation"].lower() \
            or "see the rule" in result["explanation"].lower()

    def test_invalid_examples_dropped(self):
        """Same directive as round-3 #7: drop invalid_examples for
        checksum rules — generating a guaranteed-invalid identifier is
        fragile. The rule's error_message conveys the constraint."""
        from opendqv.core.explainer import _checksum
        for algo in ("lei_mod97", "isin_mod11", "iban_mod97", "mod10_gs1"):
            result = _checksum("test_field", algo)
            assert result["invalid_examples"] == [], (
                f"v2.3.23 round-4 P2-A: {algo} template must drop "
                f"invalid_examples. Got: {result['invalid_examples']}"
            )


# ── curated_message surfaces rule.error_message ────────────────────────

class TestCuratedMessageSurfaced:
    """Sonnet directive (B): explain_rule emits curated_message
    verbatim when the rule has an authored error_message. Consumer
    chooses whichever fits the UX."""

    def test_lei_rule_carries_curated_message(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="reporting_firm_lei_valid",
            field="reporting_firm_lei",
            type="checksum",
            checksum_algorithm="lei_mod97",
            severity=Severity.ERROR,
            error_message=(
                "reporting_firm_lei must be a valid 20-character LEI per ISO 17442 — "
                "shape AND mod-97-10 check digit."
            ),
        )
        result = explain_rule(rule)
        assert "curated_message" in result, (
            f"v2.3.23 round-4 P2-A: explain_rule must surface "
            f"curated_message when rule.error_message is authored. "
            f"Got: {sorted(result.keys())}"
        )
        assert "ISO 17442" in result["curated_message"]
        # Verbatim — no extraction, no truncation.
        assert result["curated_message"] == rule.error_message

    def test_no_curated_message_when_error_message_empty(self):
        """If a rule has no authored error_message, curated_message is
        absent from the response (don't emit empty string)."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="x", type="not_empty",
            severity=Severity.ERROR, error_message="",
        )
        result = explain_rule(rule)
        # Either absent or empty — both are acceptable; the goal is
        # consumers can detect "no curated guidance" simply.
        if "curated_message" in result:
            assert result["curated_message"] == ""

    def test_regex_rule_also_carries_curated_message(self):
        """The fallback covers every rule type, not just checksum."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="execution_timestamp_format",
            field="execution_timestamp",
            type="regex",
            pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
            severity=Severity.ERROR,
            error_message=(
                "execution_timestamp must match RTS 25 µs UTC: "
                "2026-04-27T14:30:15.123456Z"
            ),
        )
        result = explain_rule(rule)
        assert result.get("curated_message", "").startswith(
            "execution_timestamp must match RTS 25 µs UTC"
        ), (
            f"explain_rule must surface the curated example "
            f"'2026-04-27T14:30:15.123456Z' from the rule's "
            f"error_message. Got: {result.get('curated_message')!r}"
        )


# ── End-to-end: live mifid contract LEI rule explain ───────────────────

class TestLiveContractLeiExplain:
    """Reviewer's exact case: explain on a real LEI rule from the
    bundled mifid_transaction_report contract. Pre-fix it returned
    "(a valid lei_mod97 identifier)" as a literal. Post-fix it must
    return the real example AND the curated error_message."""

    def test_mifid_lei_explain_carries_real_example_and_curated(self):
        from opendqv.api import deps as _d
        from opendqv.core.explainer import explain_rule
        contract = _d.registry.get("mifid_transaction_report")
        assert contract is not None
        # Find the reporting_firm_lei_valid rule.
        rule = next(
            (r for r in contract.rules if r.name == "reporting_firm_lei_valid"),
            None,
        )
        assert rule is not None, "reporting_firm_lei_valid not in contract"
        result = explain_rule(rule)
        # No v2.3.22 stub literal in the explanation OR examples.
        assert "a valid lei_mod97 identifier" not in result["explanation"]
        for ex in result["valid_examples"]:
            assert "a valid lei_mod97 identifier" not in ex
        # Real LEI present.
        assert VALID_LEI in result["valid_examples"]
        # Curated message present.
        assert "curated_message" in result
        assert "ISO 17442" in result["curated_message"]
