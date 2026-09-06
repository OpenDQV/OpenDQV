"""2.9.0 — `forbidden_values`: the exact sibling of `allowed_values` with the
sense inverted (both engines, requested by the managed-engine maintainer
2026-09-06). A PRESENT value equal to any listed value fails; absence is not a
violation; exact rendered-text match, case-sensitive, no trimming.

D12 (numeric rendering): an integral float renders without the trailing ".0"
on both allowed_values and forbidden_values, so a JSON 99999.0 matches a
listed "99999" as it does on the managed engine.
"""
from __future__ import annotations

import json

import pytest

from opendqv.core.rule_parser import RULE_TYPES, Rule, parse_rules
from opendqv.core.validator import _RULE_HANDLERS, _render_value, validate_batch, validate_record


def _both(rules, record):
    s = validate_record(record, rules)
    b = validate_batch([record], rules)["results"][0]
    assert s["valid"] == b["valid"], f"single {s['valid']} vs batch {b['valid']} on {record}"
    assert sorted(e["error_code"] for e in s["errors"]) == sorted(e["error_code"] for e in b["errors"])
    return s


RULES = parse_rules('''
rules:
  - {name: no_placeholder, type: forbidden_values, field: email, forbidden_values: ["N/A", "test@test.com", "asdf"], error_message: placeholder email}
''')


class TestSemantics:
    def test_type_is_registered_everywhere(self):
        assert "forbidden_values" in RULE_TYPES and "forbidden_values" in _RULE_HANDLERS and len(RULE_TYPES) == 25

    def test_listed_value_fails_with_the_standard_code_shape(self):
        out = _both(RULES, {"email": "asdf"})
        assert out["valid"] is False
        assert [e["error_code"] for e in out["errors"]] == ["OPENDQV_FORBIDDEN_VALUES_NO_PLACEHOLDER"]

    def test_real_value_passes(self):
        assert _both(RULES, {"email": "jane@example.org"})["valid"] is True

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_and_blank_are_not_violations(self, value):
        rec = {} if value is None else {"email": value}
        assert _both(RULES, rec)["valid"] is True

    def test_exact_case_sensitive_untrimmed(self):
        assert _both(RULES, {"email": "n/a"})["valid"] is True
        assert _both(RULES, {"email": " N/A"})["valid"] is True
        assert _both(RULES, {"email": "N/A"})["valid"] is False

    def test_condition_scopes_it_like_any_other_rule(self):
        rules = parse_rules('''
rules:
  - {name: no_placeholder, type: forbidden_values, field: email, forbidden_values: ["N/A"], condition: {field: region, value: UK}, error_message: m}
''')
        assert _both(rules, {"email": "N/A", "region": "UK"})["valid"] is False
        assert _both(rules, {"email": "N/A", "region": "US"})["valid"] is True


class TestNumericRenderingD12:
    def test_render_value(self):
        assert _render_value(99999.0) == "99999" and _render_value(99999) == "99999"
        assert _render_value(1.5) == "1.5" and _render_value("99999.0") == "99999.0"
        assert _render_value(True) == "True"   # booleans are not floats

    @pytest.mark.parametrize("rtype,expect_valid", [("forbidden_values", False), ("allowed_values", True)])
    def test_integral_float_matches_the_listed_text_on_both_rule_types(self, rtype, expect_valid):
        rules = [Rule(name="r", type=rtype, field="amount", error_message="m", **{rtype: ["99999"]})]
        for rec in ({"amount": 99999.0}, {"amount": 99999}, {"amount": "99999"}):
            assert _both(rules, rec)["valid"] is expect_valid, rec
        assert _both(rules, {"amount": 99999.5})["valid"] is (not expect_valid)


class TestModelAndLinter:
    def test_empty_list_is_refused(self):
        with pytest.raises(ValueError, match="non-empty"):
            Rule(name="r", type="forbidden_values", field="f", forbidden_values=[], error_message="m")
        with pytest.raises(ValueError, match="non-empty"):
            Rule(name="r", type="forbidden_values", field="f", error_message="m")

    def test_allowed_values_on_a_forbidden_rule_gets_the_hint(self):
        with pytest.raises(ValueError, match="opposite meaning"):
            Rule(name="r", type="forbidden_values", field="f", allowed_values=["x"], error_message="m")

    def test_linter_codes(self):
        from opendqv.core.linter import lint_contract_yaml
        res = lint_contract_yaml('''
contract:
  name: t
  rules:
    - {name: a, type: forbidden_values, field: f, allowed_values: ["x"], error_message: m}
    - {name: b, type: forbidden_values, field: g, forbidden_values: ["x"], error_message: m}
''', "t")
        hits = [i for i in res.issues if i.code == "FORBIDDEN_VALUES_EMPTY"]
        assert [i.rule_name for i in hits] == ["a"] and "opposite meaning" in hits[0].message


class TestProjections:
    RULE = Rule(name="no_placeholder", type="forbidden_values", field="email",
                forbidden_values=["N/A", "test@test.com"], error_message="placeholder email")

    def test_explainer_offers_forbidden_values_as_invalid_examples_only(self):
        from opendqv.core.explainer import explain_rule
        out = explain_rule(self.RULE)
        assert out["rule_type"] == "forbidden_values"
        assert set(out["invalid_examples"]) <= {"N/A", "test@test.com"}
        assert not set(out["valid_examples"]) & {"N/A", "test@test.com"}
        assert out["constraint"] == {"forbidden_values": ["N/A", "test@test.com"]}

    def test_sibling_forbidden_set_never_seeds_another_rule_s_valid_examples(self):
        from opendqv.core.explainer import explain_rule
        other = Rule(name="fmt", type="regex", field="email", pattern="^[^@]+@[^@]+$", error_message="m")
        out = explain_rule(other)
        assert not set(out.get("valid_examples") or []) & {"N/A", "test@test.com"}

    def test_json_schema_projects_not_enum_type_neutral(self):
        from opendqv.core.contracts import DataContract
        from opendqv.core.jsonschema import contract_to_jsonschema
        c = DataContract(name="t", rules=[self.RULE, Rule(name="mx", type="max", field="email", max=5, error_message="m")])
        prop = contract_to_jsonschema(c)["properties"]["email"]
        assert prop["not"] == {"enum": ["N/A", "test@test.com"]}
        assert "enum" not in prop

    def test_odcs_export_carries_it_verbatim_in_the_custom_twin_never_as_validValues(self):
        from opendqv.core.importers.odcs import export_odcs, import_odcs
        doc = export_odcs("t", [self.RULE], version="1.0", status="active")
        prop = doc["schema"][0]["properties"][0]
        assert "validValues" not in prop and "validValues" not in json.dumps(doc)
        twins = [q for q in prop.get("quality", []) if q.get("type") == "custom" and q.get("engine") == "opendqv"]
        assert twins, prop
        impl = twins[0]["implementation"]
        assert impl["type"] == "forbidden_values" and impl["forbidden_values"] == ["N/A", "test@test.com"]
        assert twins[0].get("dimension") == "conformity"
        back = import_odcs(doc)["contract"]["rules"]
        fv = [r for r in back if r["type"] == "forbidden_values"]
        assert fv and fv[0]["forbidden_values"] == ["N/A", "test@test.com"]

    def test_code_generator_sql_uses_in(self):
        import inspect
        from opendqv.core import code_generator as cg
        from opendqv.core.code_generator import generate_code
        assert "requires API validation" in generate_code([self.RULE], target="snowflake")
        src = inspect.getsource(cg._spark_case_when)
        assert 'rtype == "forbidden_values"' in src and "AS STRING) IN (" in src

    def test_mcp_get_contract_exposes_the_constraint(self):
        import inspect
        import opendqv.mcp_server as m
        src = inspect.getsource(m)
        assert '"forbidden_values": r.forbidden_values' in src and 'forbidden_values=r.get("forbidden_values")' in src


def test_linter_warns_when_negate_or_all_of_is_put_on_a_set_rule():
    # Sonnet red-team: negate is regex-only and all_of lookup-only; on a set rule
    # they were silently ignored — exactly where an author reaches for negate.
    from opendqv.core.linter import lint_contract_yaml
    res = lint_contract_yaml('''
contract:
  name: t
  rules:
    - {name: a, type: forbidden_values, field: f, forbidden_values: ["x"], negate: true, error_message: m}
    - {name: b, type: allowed_values, field: g, allowed_values: ["x"], all_of: true, error_message: m}
    - {name: c, type: forbidden_values, field: h, forbidden_values: ["x"], error_message: m}
''', "t")
    hits = [(i.rule_name, i.severity) for i in res.issues if i.code == "SET_RULE_KEY_IGNORED"]
    assert hits == [("a", "warning"), ("b", "warning")]
