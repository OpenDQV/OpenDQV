"""
v2.3.23 round-3 #7 — explain_error sharper synthesis for regex rules.

Persona B 2026-04-28 outside review #3: explain_error returns a
literal placeholder string ("a value matching ^...") instead of an
actual generated example for regex rules.

Sonnet pre-impl review (a96411b104c1e7e18) verdict:
  - Option B: mini regex walker for character classes + quantifiers
    common in OpenDQV's bundled contracts.
  - Vendor-free (no rstr / xeger dep).
  - Drop invalid_examples for regex rules — generating a guaranteed-
    non-match is fragile / circular.
  - Self-validate the walker output via regex.fullmatch before
    returning — catches walker bugs at the caller.
  - Complexity ceiling (max_len=64) — anything bigger returns None.

Tests cover:
  - Common shapes from OpenDQV bundled contracts (LEI, ISIN, MIC,
    IBAN, ISO date, currency code).
  - Quantifier variants ({n}, {n,m}, *, +, ?).
  - Anchors (^, $).
  - Negated regex returns empty examples.
  - Patterns the walker can't parse → fallback to None (honest "no
    example", not a wrong example).
  - Self-validation: every emitted sample matches the pattern.
"""

import pytest


# ── Walker covers OpenDQV's bundled-contract regex shapes ─────────────

class TestWalkerCoversBundledShapes:
    """The patterns these contracts actually carry. Smoke test that
    the walker emits a sample for each."""

    @pytest.mark.parametrize("pattern", [
        # LEI shape (used pre-v2.3.23 across mifid contract)
        "^[A-Z0-9]{18}[0-9]{2}$",
        # ISIN shape
        "^[A-Z]{2}[A-Z0-9]{9}[0-9]$",
        # MIC shape
        "^[A-Z]{4}$",
        # ISO date
        r"^\d{4}-\d{2}-\d{2}$",
        # Currency code
        "^[A-Z]{3}$",
        # UK IBAN
        "^GB[0-9]{2}[A-Z]{4}[0-9]{14}$",
        # Variant ID with hyphen
        r"^[A-Z]{2}-\d{4}$",
        # ID prefix + numeric
        "^TXN-[0-9]{8}$",
    ])
    def test_walker_emits_valid_sample(self, pattern):
        from opendqv.core.explainer import _synthesise_regex_example
        sample = _synthesise_regex_example(pattern)
        assert sample is not None, (
            f"v2.3.23 round-3 #7: walker should emit a sample for "
            f"common bundled-contract pattern: {pattern!r}"
        )
        # Self-validate — the sample must match the pattern.
        import regex as _re
        assert _re.fullmatch(pattern, sample), (
            f"walker emitted {sample!r} which does NOT match {pattern!r}"
        )


# ── Falls back to None for patterns the walker can't parse ────────────

class TestWalkerFallsBackOnComplexPatterns:
    """Honest fallback: walker can't safely emit for alternation,
    groups, lookarounds, etc. Return None so the caller can say 'no
    example auto-generated' rather than ship a wrong one."""

    @pytest.mark.parametrize("pattern", [
        # Alternation
        "^(cat|dog)$",
        # Lookahead
        "^(?=foo)bar$",
        # Backref
        r"^(\w+)\1$",
        # General group
        "^(abc){3}$",
        # Bare dot quantifier
        "^.+$",
        # Negated character class
        "^[^A-Z]{3}$",
    ])
    def test_walker_returns_none_for_unsupported(self, pattern):
        from opendqv.core.explainer import _synthesise_regex_example
        sample = _synthesise_regex_example(pattern)
        assert sample is None, (
            f"walker should not attempt to synthesise for pattern: "
            f"{pattern!r} (returned {sample!r}); fallback honesty matters."
        )


# ── Walker self-validation: emitted sample always matches ──────────────

class TestWalkerSelfValidates:
    """Per Sonnet's gap point: the walker must validate its own output
    via regex.fullmatch before returning. Test by feeding patterns that
    exercise the walker's logic and asserting every non-None output
    actually matches."""

    @pytest.mark.parametrize("pattern", [
        "^[A-Z]{1}$",
        "^[A-Z]{20}$",
        r"^\d{1}$",
        r"^\d{15}$",
        "^[A-Z0-9]{1,10}$",  # ranged quantifier — walker uses lo
        "^A$",  # literal
        "^[A-Z]?[0-9]?$",  # optional quantifier
    ])
    def test_emitted_sample_matches_pattern(self, pattern):
        from opendqv.core.explainer import _synthesise_regex_example
        sample = _synthesise_regex_example(pattern)
        if sample is not None:
            import regex as _re
            assert _re.fullmatch(pattern, sample), (
                f"v2.3.23 round-3 #7 (Sonnet's self-validation gap): "
                f"walker emitted {sample!r} that does NOT fullmatch "
                f"{pattern!r}. The self-check must catch this before "
                f"returning."
            )


# ── explain_rule integration: regex rules carry generated example ─────

class TestExplainRuleRegexIntegration:
    """End-to-end: feed _regex with a real LEI shape pattern, assert
    the response now carries an actual valid example (not the literal
    placeholder string the v2.3.22 implementation returned)."""

    def test_regex_rule_no_longer_returns_literal_placeholder(self):
        from opendqv.core.explainer import _regex
        result = _regex("reporting_firm_lei", "^[A-Z0-9]{18}[0-9]{2}$", negate=False)
        examples = result["valid_examples"]
        assert examples, (
            f"v2.3.23 round-3 #7: regex explain must carry a concrete "
            f"example, not an empty list. Got: {result}"
        )
        # Specifically assert it's NOT the v2.3.22 literal placeholder.
        for ex in examples:
            assert not ex.startswith("a value matching"), (
                f"v2.3.23 round-3 #7: regex explain must not emit the "
                f"v2.3.22 literal-placeholder string ('a value "
                f"matching <pattern>'). Got: {ex!r}"
            )
        # The example must actually match the pattern.
        import regex as _re
        for ex in examples:
            assert _re.fullmatch("^[A-Z0-9]{18}[0-9]{2}$", ex), (
                f"example {ex!r} does not match the LEI pattern"
            )

    def test_regex_rule_drops_invalid_examples(self):
        """Sonnet's directive: drop invalid_examples for regex rules
        — generating a guaranteed-non-match is fragile/circular and the
        rule's error_message conveys the constraint already."""
        from opendqv.core.explainer import _regex
        result = _regex("test_field", "^[A-Z]{3}$", negate=False)
        assert result["invalid_examples"] == [], (
            f"v2.3.23 round-3 #7: regex explain must drop "
            f"invalid_examples (was: list of stub strings). Got: "
            f"{result['invalid_examples']}"
        )

    def test_regex_rule_falls_back_honestly_on_complex(self):
        """When the walker can't synthesise, the explanation must say
        so explicitly — not ship a wrong example."""
        from opendqv.core.explainer import _regex
        result = _regex("test_field", "^(cat|dog)$", negate=False)
        assert result["valid_examples"] == [], (
            f"v2.3.23 round-3 #7: walker should fall back to empty "
            f"examples for alternation patterns. Got: {result}"
        )
        # The explanation must signal the absence honestly.
        explanation = result["explanation"].lower()
        assert "no example auto-generated" in explanation or \
               "test your data" in explanation, (
            f"v2.3.23 round-3 #7: when walker returns None, the "
            f"explanation must say 'no example auto-generated' "
            f"explicitly. Got: {result['explanation']!r}"
        )

    def test_negated_regex_emits_empty_examples(self):
        from opendqv.core.explainer import _regex
        result = _regex("forbidden_field", "^bad", negate=True)
        # Negated rule semantics: walker output would be a value that
        # MATCHES the pattern — exactly the wrong example. Drop both.
        assert result["valid_examples"] == []
        assert result["invalid_examples"] == []
        assert result["constraint"]["negate"] is True
