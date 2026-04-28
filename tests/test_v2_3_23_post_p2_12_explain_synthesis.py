"""
v2.3.23 P2-12 — explain_error partial real-value synthesis.

Persona B 2026-04-28: "explain_error returns placeholder examples.
valid_examples and invalid_examples are template strings ('(value
matching the pattern)') rather than concrete data. Customer impact:
less debugging help than the tool description promises."

Sonnet's pre-impl review (a8d40b8f5784fb653) directed partial fix:
  - allowed_values rule: dispatch GAP — currently falls through to
    _generic. Add handler that surfaces real values from
    rule.allowed_values.
  - lookup rule (file-based): read first 3 non-blank lines via
    _check_lookup_path_safe. Fall back to placeholder on any read
    failure.
  - lookup rule (HTTP URL): placeholder + explanation note. No HTTP
    call at explain time.
  - regex synthesis: deferred to v2.4 (needs rstr/exrex dependency).

Eight tests per Sonnet's matrix:
  1. allowed_values rule with 5 values → valid_examples contains 3
  2. allowed_values rule with empty list → fallback string
  3. file-based lookup with real file → valid_examples are real lines
  4. file-based lookup, file does not exist → fallback placeholder
  5. file-based lookup, path traversal attempt → fallback placeholder
  6. HTTP lookup_file → placeholder with HTTP-explicit note
  7. regex still emits templated example (regression guard, v2.4 will
     replace)
  8. allowed_values dispatch hits the new handler, not _generic
"""



class TestAllowedValuesSynthesis:
    def test_allowed_values_rule_inlines_three_values(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule

        rule = Rule(
            name="status_check", field="status", type="allowed_values",
            allowed_values=["active", "inactive", "pending", "archived", "draft"],
            severity=Severity.ERROR,
            error_message="Status must be one of allowed values",
        )
        result = explain_rule(rule)
        assert result["rule_type"] == "allowed_values", result
        valid = result["valid_examples"]
        # Top 3 of 5 values inline.
        assert len(valid) == 3, valid
        for v in valid:
            assert v in ["active", "inactive", "pending", "archived", "draft"], v

    def test_allowed_values_empty_list_falls_back(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule

        rule = Rule(
            name="empty", field="x", type="allowed_values",
            allowed_values=[],
            severity=Severity.ERROR,
            error_message="empty",
        )
        result = explain_rule(rule)
        # Either non-empty placeholder or empty list — both are
        # acceptable. What's NOT acceptable is a crash.
        assert "valid_examples" in result
        assert isinstance(result["valid_examples"], list)

    def test_allowed_values_dispatch_does_not_fall_to_generic(self):
        """Pre-fix: allowed_values fell through to _generic with empty
        examples. Post-fix: explicit dispatch returns rule_type
        'allowed_values'."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule

        rule = Rule(
            name="t", field="x", type="allowed_values",
            allowed_values=["A", "B"],
            severity=Severity.ERROR, error_message="t",
        )
        result = explain_rule(rule)
        assert result["rule_type"] == "allowed_values", (
            f"v2.3.23 P2-12: dispatch must hit allowed_values handler, "
            f"not fall to _generic. Got rule_type={result.get('rule_type')!r}"
        )


class TestLookupFileSynthesis:
    def test_file_based_lookup_inlines_real_lines(self, tmp_path, monkeypatch):
        """Lookup with file path containing real data: explain_error
        surfaces first 3 non-blank lines as valid_examples."""
        # Write fixture inside CONTRACTS_DIR so _check_lookup_path_safe
        # accepts it.
        import opendqv.config as cfg
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "ref").mkdir()
        (contracts_dir / "ref" / "test_lookup.txt").write_text(
            "ALPHA\nBRAVO\nCHARLIE\nDELTA\nECHO\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cfg, "CONTRACTS_DIR", contracts_dir)

        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="code", type="lookup",
            lookup_file="ref/test_lookup.txt",
            severity=Severity.ERROR, error_message="t",
        )
        result = explain_rule(rule)
        valid = result["valid_examples"]
        assert "ALPHA" in valid or "BRAVO" in valid or "CHARLIE" in valid, (
            f"v2.3.23 P2-12: file-based lookup must surface real "
            f"values from the file. Got: {valid}"
        )
        # Must NOT be the legacy placeholder.
        assert "(a value present in the reference list)" not in valid

    def test_lookup_file_does_not_exist_falls_back(self, tmp_path, monkeypatch):
        import opendqv.config as cfg
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "ref").mkdir()
        monkeypatch.setattr(cfg, "CONTRACTS_DIR", contracts_dir)

        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="code", type="lookup",
            lookup_file="ref/no_such_file.txt",
            severity=Severity.ERROR, error_message="t",
        )
        # Must not crash, must return placeholder.
        result = explain_rule(rule)
        assert "valid_examples" in result
        # Fallback shape: at least a placeholder string is present.
        valid = result["valid_examples"]
        assert isinstance(valid, list)
        assert len(valid) >= 1

    def test_lookup_path_traversal_falls_back(self, tmp_path, monkeypatch):
        """SEC-002: lookup_file with path traversal must not be read.
        Fall back to placeholder. _check_lookup_path_safe raises
        ValueError; explainer catches and falls back."""
        import opendqv.config as cfg
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        monkeypatch.setattr(cfg, "CONTRACTS_DIR", contracts_dir)

        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="code", type="lookup",
            lookup_file="../../etc/passwd",
            severity=Severity.ERROR, error_message="t",
        )
        # Must not crash, must not leak file contents.
        result = explain_rule(rule)
        assert "valid_examples" in result
        valid = result["valid_examples"]
        # No /etc/passwd entries should appear.
        for v in valid:
            assert not (isinstance(v, str) and "root:" in v), (
                f"Path traversal leaked file contents! Got: {valid}"
            )

    def test_http_lookup_emits_placeholder_with_note(self):
        """HTTP/HTTPS lookup_file: don't make a network call. Emit
        placeholder. The single example must mention HTTP so the
        consumer knows why values aren't inlined."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="code", type="lookup",
            lookup_file="https://example.com/codes.txt",
            severity=Severity.ERROR, error_message="t",
        )
        result = explain_rule(rule)
        valid = result["valid_examples"]
        # At least one example string mentions HTTP / external.
        joined = " ".join(str(v) for v in valid)
        assert "HTTP" in joined or "http" in joined or "external" in joined.lower(), (
            f"HTTP lookup must emit placeholder with HTTP-explicit "
            f"note. Got: {valid}"
        )


class TestRegexStillPlaceholder:
    """v2.3.23 round-3 #7: regex synthesis upgraded from a literal
    templated placeholder to a mini regex walker that emits a real
    sample for character-class + quantifier patterns. Falls back to
    empty examples (with an honest "no example auto-generated"
    explanation) for patterns the walker can't safely parse —
    alternation, groups, lookarounds. The email regex below uses
    `\\w` and `\\.` which the walker can't handle (escaped literal +
    set-membership), so it falls back to empty — that IS the new
    contract."""

    def test_regex_complex_pattern_falls_back_honestly(self):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.explainer import explain_rule
        rule = Rule(
            name="t", field="email", type="regex",
            pattern=r"^[\w.+-]+@[\w.-]+\.\w+$",
            severity=Severity.ERROR, error_message="t",
        )
        result = explain_rule(rule)
        # Constraint payload still carries the pattern (Cluster D + P1-5).
        assert result["constraint"].get("pattern") == r"^[\w.+-]+@[\w.-]+\.\w+$"
        # invalid_examples dropped per Sonnet's directive — generating
        # a guaranteed-non-match is fragile/circular.
        assert result["invalid_examples"] == [], (
            "v2.3.23 round-3 #7: regex explain must drop invalid_examples"
        )
        # The walker should NOT emit the v2.3.22 literal placeholder.
        for ex in result["valid_examples"]:
            assert not ex.startswith("a value matching"), (
                f"v2.3.23 round-3 #7: regex explain must not emit the "
                f"v2.3.22 literal-placeholder string. Got: {ex!r}"
            )
