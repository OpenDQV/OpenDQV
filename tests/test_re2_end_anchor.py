"""`$` is the end of the value (2.10.4).

A regex rule is an unanchored search in both engines, but Python's `$` (no
MULTILINE) also matches just before a final "\\n", while the RE2 reference
reading is the end of the value only. Core rewrites `$` to `\\Z` when it
COMPILES a pattern; the authored string is never touched.
"""
from __future__ import annotations

import json

import pytest

import opendqv.core.rule_parser as rp
from opendqv.core.rule_parser import Rule, compile_rule_pattern, parse_rules, re2_end_anchor
from opendqv.core.validator import validate_batch, validate_record


class TestTransform:
    @pytest.mark.parametrize("pattern,expected", [
        (r"^[0-9]{13}$", r"^[0-9]{13}\Z"),
        (r"[^]$]x$", r"[^]$]x\Z"),
        ("^a\\\\$", "^a\\\\\\Z"),
        (r"(?i)^AB$", r"(?i)^AB\Z"),
        (r"(?i-m)a$", r"(?i-m)a\Z"),
        (r"^(?:a$|b$)", r"^(?:a\Z|b\Z)"),
        (r"a$|b", r"a\Z|b"),
        (r"^$", r"^\Z"),
        (r"$", r"\Z"),
    ])
    def test_rewritten(self, pattern, expected):
        assert re2_end_anchor(pattern) == expected

    @pytest.mark.parametrize("pattern", [
        r"\$", r"[$]", r"[]$]", r"[[:digit:]$]+",
        r"(?m)^a$", r"(?im)^a$", r"(?m-i)^a$", r"(?m:a$)b$",
        r"^[0-9]+", "", r"plain",
    ])
    def test_unchanged(self, pattern):
        assert re2_end_anchor(pattern) == pattern

    def test_class_then_anchor_outside_it(self):
        assert re2_end_anchor(r"[a$]$") == r"[a$]\Z"
        assert re2_end_anchor(r"[\]$]$") == r"[\]$]\Z"

    def test_alternate_end_token_for_java(self):
        assert re2_end_anchor(r"^x$", end=r"\z") == r"^x\z"

    def test_compile_uses_the_rewritten_form(self):
        assert compile_rule_pattern(r"^[0-9]{13}$").pattern == r"^[0-9]{13}\Z"


RULE = parse_rules('rules:\n  - {name: ean, type: regex, field: code, pattern: "^[0-9]{13}$", error_message: 13 digits}\n')
NEGATED = parse_rules('rules:\n  - {name: not_ean, type: regex, field: code, pattern: "^[0-9]{13}$", negate: true, error_message: must not be 13 digits}\n')


def _both(rules, value) -> bool:
    single = validate_record({"code": value}, rules)["valid"]
    batch = validate_batch([{"code": value}], rules)["results"][0]["valid"]
    assert single == batch, f"single {single} vs batch {batch} on {value!r}"
    return single


class TestVerdicts:
    def test_plain_value_valid(self):
        assert _both(RULE, "1234567890123") is True

    @pytest.mark.parametrize("value", ["1234567890123\n", "1234567890123\r\n", "1234567890123\n\n"])
    def test_trailing_line_ending_invalid(self, value):
        assert _both(RULE, value) is False

    def test_negate_flips(self):
        assert _both(NEGATED, "1234567890123") is False
        assert _both(NEGATED, "1234567890123\n") is True

    def test_builtin_alias_is_expanded_on_the_batch_fallback_too(self):
        alias = next(iter(rp._BUILTIN_PATTERNS))
        r = Rule.model_construct(**{**Rule(name="a", type="regex", field="code", pattern=alias,
                                            error_message="m").model_dump(), "compiled_pattern": None})
        # with the compiled pattern stripped, both paths must fall back through
        # the same alias expansion and the same compile
        out_s = validate_record({"code": "definitely-not-matching-anything-"}, [r])
        out_b = validate_batch([{"code": "definitely-not-matching-anything-"}], [r])["results"][0]
        assert out_s["valid"] == out_b["valid"]


class TestReFallback:
    def test_without_the_regex_module(self, monkeypatch):
        monkeypatch.setattr(rp, "_HAS_REGEX_LIB", False)
        compiled = compile_rule_pattern(r"^[0-9]{13}$")
        import re as stdlib_re
        assert isinstance(compiled, stdlib_re.Pattern)
        assert compiled.pattern == r"^[0-9]{13}\Z"
        assert compiled.search("1234567890123") and not compiled.search("1234567890123\n")


class TestAuthoredStringIsUntouched:
    def test_rule_pattern_and_dump(self):
        r = RULE[0]
        assert r.pattern == r"^[0-9]{13}$"
        assert r.model_dump(by_alias=True)["pattern"] == r"^[0-9]{13}$"
        assert r.compiled_pattern.pattern == r"^[0-9]{13}\Z"

    def test_json_schema_keeps_the_authored_pattern(self):
        from opendqv.core.contracts import DataContract
        from opendqv.core.jsonschema import contract_to_jsonschema
        c = DataContract(name="t", rules=RULE)
        assert contract_to_jsonschema(c)["properties"]["code"]["pattern"] == r"^[0-9]{13}$"

    def test_dbt_export_keeps_the_authored_pattern(self):
        from opendqv.core.importers.dbt import export_dbt_schema
        out = json.dumps(export_dbt_schema("t", RULE))
        assert "[0-9]{13}$" in out and r"\Z" not in out

    def test_explainer_and_mcp_shaped_readers_see_the_authored_pattern(self):
        from opendqv.core.explainer import explain_rule
        assert r"\Z" not in json.dumps(explain_rule(RULE[0]))

    def test_manifest_digests_are_unchanged(self):
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run([sys.executable, str(root / "scripts" / "library_manifest.py"), "--check"],
                              capture_output=True, text=True, cwd=root)
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestCodeGeneration:
    def test_spark_emits_java_absolute_end(self):
        from opendqv.core.code_generator import generate_code
        out = generate_code(RULE, target="spark")
        assert r"^[0-9]{13}\z" in out and r"\Z" not in out and "[0-9]{13}$" not in out

    @pytest.mark.parametrize("target", ["js", "snowflake", "salesforce"])
    def test_other_targets_keep_the_authored_dollar(self, target):
        from opendqv.core.code_generator import generate_code
        out = generate_code(RULE, target=target)
        assert "[0-9]{13}$" in out and r"\z" not in out and r"\Z" not in out
