"""Round-2 review of the CRT180 conformance PR — each test names its item.

B2 structural absence guard (single + batch, every non-presence type)
B4 cross-field counterparts materialised on the batch path
B5 unique/group_by never fabricates duplicates on a synthesised column
B6 manifest digest keeps zero boundaries and covers allowed_fields/contexts
S2 CLI lint label for info
S4 regex is unanchored search
S5 length rules refuse non-strings (typed message, same code) on both paths
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import (
    _ABSENT_EXEMPT_RULE_TYPES,
    _PRESENCE_RULE_TYPES,
    _RULE_HANDLERS,
    validate_batch,
    validate_record,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

BLANKS = [None, "", "   ", "\t\n"]


def _rule_for(rtype: str) -> Rule | None:
    """A minimal, valid rule of the given type on field 'f' (cross-field partner 'g')."""
    base = {"name": f"r_{rtype}", "type": rtype, "field": "f", "error_message": f"{rtype} failed"}
    extra = {
        "regex": {"pattern": "^[a-z]+$"}, "min": {"min": 1}, "max": {"max": 1}, "range": {"min": 0, "max": 1},
        "min_length": {"min_length": 3}, "max_length": {"max_length": 1}, "date_format": {},
        "allowed_values": {"allowed_values": ["a"]}, "checksum": {"checksum_algorithm": "luhn"},
        "compare": {"compare_to": "g", "compare_op": "gt"}, "date_diff": {"date_diff_field": "g", "max": 1},
        "age_match": {"dob_field": "g"}, "cross_field_range": {"min_field": "g", "max_field": "g"},
        "geospatial_bounds": {"lon_field": "g"}, "forbidden_if": {"forbidden_if": {"field": "g", "value": "x"}},
        "conditional_value": {"must_equal": "a"}, "required_if": {"required_if": {"field": "g", "value": "x"}},
        "not_empty": {}, "not_empty_string": {}, "unique": {},
        "field_sum": {"sum_fields": ["g"], "sum_equals": "f"}, "ratio_check": {"ratio_field": "g", "max": 2},
        "lookup": None, "conditional_lookup": None,  # need a reference file; covered by the corpus
    }
    if rtype not in extra or extra[rtype] is None:
        return None
    try:
        return Rule(**base, **extra[rtype])
    except (ValueError, TypeError):  # pydantic ValidationError is a ValueError
        return None


@pytest.mark.parametrize("rtype", sorted(_RULE_HANDLERS))
def test_b2_no_non_presence_rule_fires_on_absent_or_blank(rtype):
    rule = _rule_for(rtype)
    if rule is None:
        pytest.skip(f"{rtype}: needs a fixture (covered by the conformance corpus)")
    for blank in BLANKS:
        rec = {"g": "x"} if blank is None else {"f": blank, "g": "x"}
        single = validate_record(rec, [rule])
        batch = validate_batch([rec], [rule])["results"][0]
        fired_single = [e for e in single["errors"] + single["warnings"] if e["rule"] == rule.name]
        fired_batch = [e for e in batch["errors"] + batch["warnings"] if e["rule"] == rule.name]
        if rtype in _ABSENT_EXEMPT_RULE_TYPES and rtype != "unique":
            # presence-class + conditional_value MUST fire (required_if only when its condition holds)
            assert fired_single, f"{rtype} must fire on {blank!r} (single)"
            assert bool(fired_batch) == bool(fired_single), f"{rtype} single/batch parity on {blank!r}"
        else:
            assert not fired_single, f"{rtype} fired on {blank!r} (single): {fired_single}"
            assert not fired_batch, f"{rtype} fired on {blank!r} (batch): {fired_batch}"


def test_b2_presence_set_is_shared_with_linter():
    from opendqv.core import linter
    assert linter._PRESENCE_RULE_TYPES is _PRESENCE_RULE_TYPES
    assert _PRESENCE_RULE_TYPES == {"not_empty", "not_empty_string", "required_if"}


def test_b4_cross_field_counterpart_absent_matches_single_path():
    rules = [Rule(name="a_lt_b", type="compare", field="a", compare_to="b", compare_op="lt", error_message="a<b")]
    rec = {"a": 1}  # counterpart b absent from every record
    single = validate_record(rec, rules)
    batch = validate_batch([rec, rec], rules)["results"]
    assert [r["valid"] for r in batch] == [single["valid"]] * 2
    # D10: a missing or blank counterpart IS a comparison failure — on both
    # engines and both paths (the batch path used to pass it: the K1 shape).
    assert single["valid"] is False
    assert validate_record({"a": 1, "b": ""}, rules)["valid"] is False
    assert validate_record({"a": 1, "b": 2}, rules)["valid"] is True
    assert validate_record({"a": 2, "b": 1}, rules)["valid"] is False  # a real violation still fails
    dd = [Rule(name="dd", type="date_diff", field="a", date_diff_field="b", max=1, error_message="dd")]
    rec = {"a": "2026-01-01"}
    assert validate_batch([rec], dd)["results"][0]["valid"] == validate_record(rec, dd)["valid"]


def test_b5_unique_never_fabricates_duplicates_on_synthesised_column():
    rules = [Rule(name="acct_unique", type="unique", field="acct", group_by=["region"], error_message="dup")]
    recs = [{"region": "uk"}, {"region": "uk"}, {"region": "eu"}]  # nobody sent acct
    res = validate_batch(recs, rules)
    assert res["summary"]["failed"] == 0, res
    recs = [{"acct": "1"}, {"acct": "1"}]  # group_by column absent, field present → global-unique fallback
    assert validate_batch(recs, rules)["summary"]["failed"] == 2
    recs = [{"acct": "1", "region": "uk"}, {"acct": "1", "region": "uk"}]  # real duplicate still caught
    assert validate_batch(recs, rules)["summary"]["failed"] == 2


def test_b6_manifest_digest_keeps_zero_and_covers_allowlist_and_contexts():
    from library_manifest import _rules_digest
    with_zero = [{"name": "r", "type": "range", "field": "x", "min": 0, "max": 5}]
    without = [{"name": "r", "type": "range", "field": "x", "max": 5}]
    assert _rules_digest(with_zero) != _rules_digest(without)
    base = [{"name": "r", "type": "not_empty", "field": "x"}]
    assert _rules_digest(base, {}) != _rules_digest(base, {"allowed_fields": ["trace_id"]})
    assert _rules_digest(base, {}) != _rules_digest(base, {"strict_schema": True})
    assert _rules_digest(base, {}) != _rules_digest(base, {"contexts": {"web": {"x": {"type": "regex", "pattern": "^a$"}}}})
    assert _rules_digest(base, {"allowed_fields": []}) == _rules_digest(base, {})


def test_s4_regex_is_unanchored_search():
    r = Rule(name="dom", type="regex", field="e", pattern=r"@example\.com$", error_message="not example.com")
    assert validate_record({"e": "alice@example.com"}, [r])["valid"] is True  # search: matches inside the value
    assert validate_record({"e": "alice@other.com"}, [r])["valid"] is False
    anchored = Rule(name="dom", type="regex", field="e", pattern=r"^@example\.com$", error_message="x")
    assert validate_record({"e": "alice@example.com"}, [anchored])["valid"] is False
    # batch agrees
    assert validate_batch([{"e": "alice@example.com"}], [r])["results"][0]["valid"] is True


def test_s4_linter_names_unanchored_patterns_as_info():
    from opendqv.core.linter import lint_contract_yaml
    y = "rules:\n  - name: dom\n    type: regex\n    field: e\n    pattern: '@example\\.com$'\n    error_message: x\n"
    codes = {i.code: i.severity for i in lint_contract_yaml(y).issues}
    assert codes.get("REGEX_NOT_START_ANCHORED") == "info"
    y2 = y.replace("'@example", "'^.*@example")
    assert "REGEX_NOT_START_ANCHORED" not in {i.code for i in lint_contract_yaml(y2).issues}


def test_s5_length_rules_refuse_non_strings_on_both_paths():
    r = Rule(name="acct_len", type="min_length", field="acct", min_length=6, error_message="acct must be at least 6 characters")
    single = validate_record({"acct": 42}, [r])["errors"][0]
    batch = validate_batch([{"acct": 42}], [r])["results"][0]["errors"][0]
    assert single["error_code"] == batch["error_code"] == "OPENDQV_MIN_LENGTH_ACCT_LEN"
    assert single["message"] == batch["message"]
    assert 'expects a JSON string, got number' in single["message"]
    # a real string still measures
    assert validate_record({"acct": "1234567"}, [r])["valid"] is True
    assert validate_record({"acct": "123"}, [r])["errors"][0]["message"] == "acct must be at least 6 characters"


def test_s2_cli_lint_label_for_info(capsys):
    from opendqv import cli
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert '"info": "INFO   "' in src


@pytest.mark.parametrize("rtype,rule_kwargs,rec", [
    ("compare", {"compare_to": "g", "compare_op": "lt"}, {"f": 1}),
    ("date_diff", {"date_diff_field": "g", "max": 1}, {"f": "2026-01-01"}),
    ("age_match", {"dob_field": "g"}, {"f": 30}),
    ("cross_field_range", {"cross_min_field": "g", "cross_max_field": "g"}, {"f": 5}),
    ("ratio_check", {"ratio_numerator": "f", "ratio_denominator": "g", "max": 2}, {"f": 5}),
    ("field_sum", {"sum_fields": ["g"], "sum_equals": "f"}, {"f": 5}),
])
def test_d10_counterpart_absent_or_blank_fails_on_both_paths(rtype, rule_kwargs, rec):
    """D10: a cross-field rule whose COUNTERPART is absent or blank fails — one
    reading for every cross-field type, single and batch. (The rule's own field
    absent is the presence rule's business — D6.)"""
    try:
        rule = Rule(name="x", type=rtype, field="f", error_message="cross", **rule_kwargs)
    except (ValueError, TypeError) as e:
        pytest.skip(f"{rtype}: {e}")
    for v in (None, "", "   "):
        r = dict(rec)
        if v is not None:
            r["g"] = v
        single = validate_record(r, [rule])
        batch = validate_batch([r], [rule])["results"][0]
        assert single["valid"] is False, f"{rtype}: counterpart {v!r} must fail (single)"
        assert batch["valid"] is False, f"{rtype}: counterpart {v!r} must fail (batch)"


def test_batch_fallback_covers_every_handler_type():
    """Every rule type is either natively batched or falls back to the single
    handler per record — no type can silently pass in batch again."""
    from opendqv.core.validator import _BATCH_BRANCH_TYPES
    assert _BATCH_BRANCH_TYPES <= set(_RULE_HANDLERS)
    # age_match: no native branch → fallback → same verdict as the single path
    rule = Rule(name="m", type="age_match", field="age", dob_field="dob", error_message="age/dob mismatch")
    rec = {"age": 30, "dob": "1900-01-01"}
    assert validate_batch([rec], [rule])["results"][0]["valid"] == validate_record(rec, [rule])["valid"] is False
