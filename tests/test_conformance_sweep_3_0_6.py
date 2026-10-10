"""
3.0.6: the managed engine's follow-up to 3.0.5 (2026-10-10). The cross-engine
rows are frozen/engine_semantics.jsonl lines 126-180 (managed rows 28, 37, 50,
then 159-210) plus line 119 (managed row 152, replaced). This file pins what
no fixture row can carry: the load refusals (§2, §3), the number grammar's
edges (§4a), the CSV readers (§4c, §4d) and the stored readings.
"""
import argparse
import logging

import pytest
import yaml

from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule, _BUILTIN_PATTERNS
from opendqv.core.submission import rule_submission_problems
from opendqv.core.validator import (
    SEMVER_PATTERN,
    _num,
    _read_layout,
    _semver_tuple,
    validate_batch,
    validate_record,
)

_BASE = {"name": "r", "field": "f", "error_message": "bad"}


def _both(record, rules):
    single = validate_record(record, rules, "t")
    batch = validate_batch([record], rules, "t")["results"][0]
    return single, batch


def _codes(out):
    return sorted(e["error_code"] for e in out["errors"])


# ── §1 compare on a boolean ─────────────────────────────────────────────

@pytest.mark.parametrize("op", ["gt", "lt", "gte", "lte", ">", "<", ">=", "<="])
@pytest.mark.parametrize("a,b", [(True, 1), (0, False), (True, "x"), (False, True), (True, True)])
def test_a_boolean_has_no_order(op, a, b):
    rule = Rule(name="c", type="compare", field="a", compare_to="b", compare_op=op, error_message="bad")
    for out in _both({"a": a, "b": b}, [rule]):
        assert _codes(out) == ["OPENDQV_COMPARE_C"]


@pytest.mark.parametrize("a,op,b,valid", [
    (True, "eq", True, True), (True, "eq", "true", True), (False, "eq", "false", True),
    (True, "neq", False, True), (True, "neq", 1, True), (True, "eq", 1, False),
    (False, "eq", 0, False), (True, "eq", "True", False), (True, "=", True, True), (True, "!=", "1", True),
])
def test_eq_and_neq_compare_a_boolean_as_its_text(a, op, b, valid):
    rule = Rule(name="c", type="compare", field="a", compare_to="b", compare_op=op, error_message="bad")
    for out in _both({"a": a, "b": b}, [rule]):
        assert out["valid"] is valid


# ── §2 a format that reads one field twice, or mixes spellings ──────────

REFUSED_FORMATS = ["%S%S", "%Y-%m-%d%d", "%Y %y", "YYYY YY", "DD/MM/YYYY DD", "%H:%M %H",
                   "%Y-MM-DD", "%d/%m/yyyy", "%YMMDD", "HH:MM %m/DD/YYYY", "%Y-%m-%d hh", "%Y-Mm-dD"]
ACCEPTED_FORMATS = ["MM/DD/YYYY HH:MM", "YYYY-MM-DD HH:MM:SS", "%Y-%m-%d added", "%Y-%m-%d (assessed)",
                    "100%% %Y", "%Y-%m-%dT%H:%M:%S.%f", "%d/%m/%Y", "%Y-%m-%d D", "%%Y %Y"]


@pytest.mark.parametrize("fmt", REFUSED_FORMATS)
def test_format_reading_a_field_twice_or_mixing_spellings_is_refused(fmt):
    problems = rule_submission_problems({**_BASE, "type": "date_format", "format": fmt})
    assert problems and ("twice" in problems[0] or "human spelling" in problems[0]), problems


@pytest.mark.parametrize("fmt", ACCEPTED_FORMATS)
def test_format_accepted(fmt):
    assert rule_submission_problems({**_BASE, "type": "date_format", "format": fmt}) == []


def test_repeated_field_message_names_both_directives():
    assert rule_submission_problems({**_BASE, "type": "date_format", "format": "%Y %y"}) == [
        "format reads the year twice (%Y and %y) — a value has one year; write each field once"]


@pytest.mark.parametrize("fmt,value,ok", [
    ("%S%S", "1530", True),     # the last occurrence decides
    ("%S%S", "6030", False),    # every occurrence must be a valid value
    ("%S%S", "3060", False),
    ("%d/%m/%Y %d", "31/01/2026 15", True),
    ("%d/%m/%Y %d", "31/02/2026 15", False),   # 31 February, though the last day reads 15
])
def test_stored_repeated_directive_reading(fmt, value, ok):
    if ok:
        _read_layout(value, fmt)
    else:
        with pytest.raises(ValueError):
            _read_layout(value, fmt)
    assert _read_layout("1530", "%S%S").second == 30


# ── §3 algorithm: semver is SemVer 2.0.0 ────────────────────────────────

def test_builtin_semver_is_the_compare_grammar():
    assert _BUILTIN_PATTERNS["builtin:semver"] == "^" + SEMVER_PATTERN + "$"


_CHAIN = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
          "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0", "10.0.0"]


def test_semver_precedence_follows_the_spec_chain():
    keys = [_semver_tuple(v) for v in _CHAIN]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


@pytest.mark.parametrize("a,b", [("1.0.0-1", "1.0.0-a"), ("1.0.0-2", "1.0.0-10"), ("1.0.0-a.b", "1.0.0-a.b.0"),
                                 ("1.0.0-Z", "1.0.0-a"), ("1.0.0-0a", "1.0.0-a")])
def test_semver_identifier_order(a, b):
    assert _semver_tuple(a) < _semver_tuple(b)


@pytest.mark.parametrize("v", ["01.2.3", "1.02.3", "1.0.0-01", "1.0.0-alpha_1", "V1.2.3", "1.2", "1.2.3.4",
                               "١.٢.٣", "1.2.3-", "1.2.3+", "1.2.3-a..b", "1.2.3 4", "", 2, 1.5, True, None,
                               [], {}])
def test_not_a_version(v):
    with pytest.raises(ValueError):
        _semver_tuple(v)


@pytest.mark.parametrize("extra,ok", [
    ({"type": "compare", "compare_to": "g", "compare_op": "gt", "algorithm": "semver"}, True),
    ({"type": "compare", "compare_to": "g", "compare_op": "gt", "algorithm": "SemVer"}, False),
    ({"type": "compare", "compare_to": "g", "compare_op": "gt", "algorithm": "calver"}, False),
    ({"type": "regex", "pattern": "x", "algorithm": "semver"}, False),
    ({"type": "compare", "compare_to": "today", "compare_op": "gt", "algorithm": "semver"}, False),
    ({"type": "compare", "compare_to": "now", "compare_op": "lt", "algorithm": "semver"}, False),
    ({"type": "compare", "compare_to": "g", "compare_op": "same_date", "algorithm": "semver"}, False),
])
def test_algorithm_refusals(extra, ok):
    assert (rule_submission_problems({**_BASE, **extra}) == []) is ok


def test_stored_algorithm_refusals_load_with_a_warning(tmp_path, caplog):
    doc = {"name": "legacy", "version": "0.1", "status": "active", "rules": [
        {**_BASE, "name": "d", "field": "d", "type": "date_format", "format": "%S%S"},
        {**_BASE, "name": "c", "field": "a", "type": "compare", "compare_to": "b", "compare_op": "gt",
         "algorithm": "calver"},
    ]}
    (tmp_path / "legacy.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="opendqv.core.contracts"):
        reg = ContractRegistry(tmp_path)
    assert reg.get("legacy") is not None
    warned = " ".join(r.message for r in caplog.records)
    assert "twice" in warned and "calver" in warned


# ── §4(a) one number reader ─────────────────────────────────────────────

@pytest.mark.parametrize("text,value", [
    ("12", 12.0), (" 12 ", 12.0), (" 12　", 12.0), ("-1.5e3", -1500.0), ("+7", 7.0),
    ("1_000", 1000.0), ("1_000.5_5", 1000.55), ("5.", 5.0), (".5", 0.5), ("1E2", 100.0), ("0012", 12.0),
])
def test_number_text_read(text, value):
    assert _num(text) == value


@pytest.mark.parametrize("text", [
    "NaN", "nan", "Infinity", "-inf", "INF", "0x10", "1e400", "-1e400", "1,000", "£5", "5€", "1 000",
    "１２", "١٢", "1__0", "_1", "1_", "1e", "e5", ".", "+", "", "\x1c12", "12\x1f", "1.2.3", "0b1",
])
def test_not_number_text(text):
    with pytest.raises(ValueError):
        _num(text)


@pytest.mark.parametrize("rtype,extra", [("min", {"min": 0}), ("max", {"max": 100}), ("range", {"min": 0, "max": 100})])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "0x10", "1e400", "١٢", "1,000"])
def test_bound_rules_report_a_type_mismatch(rtype, extra, value):
    rule = Rule(name="q", type=rtype, field="q", error_message="bad", **extra)
    for out in _both({"q": value}, [rule]):
        assert _codes(out) == ["OPENDQV_TYPE_MISMATCH"]


@pytest.mark.parametrize("rule_kw,record", [
    ({"type": "field_sum", "field": "t", "sum_fields": ["a", "b"], "sum_equals": 3}, {"t": "x", "a": "１", "b": "2"}),
    ({"type": "ratio_check", "field": "r", "ratio_numerator": "a", "ratio_denominator": "b", "max": 1},
     {"r": "x", "a": "١", "b": "2"}),
])
def test_other_numeric_rules_fail_under_their_own_code(rule_kw, record):
    rule = Rule(name="n", error_message="bad", **rule_kw)
    for out in _both(record, [rule]):
        assert _codes(out) == [f"OPENDQV_{rule_kw['type'].upper()}_N"]


# ── §4(b) a number is never ordered against a non-number ────────────────

@pytest.mark.parametrize("a,op,b,valid", [
    ("abc", "gt", 5, False), (5, "lt", "abc", False), ("abc", "gte", "5", False), ("NaN", "gt", 5, False),
    ("abc", "eq", 5, False), ("abc", "neq", 5, True), ("5", "eq", 5, True), (" 5 ", "gte", "4", True),
    ("b", "gt", "a", True),                 # two texts still order by text
])
def test_number_against_non_number(a, op, b, valid):
    rule = Rule(name="c", type="compare", field="a", compare_to="b", compare_op=op, error_message="bad")
    for out in _both({"a": a, "b": b}, [rule]):
        assert out["valid"] is valid


def test_a_digit_only_date_with_a_declared_layout_still_compares_as_a_date():
    rules = [Rule(name="fs", type="date_format", field="start", format="%Y%m%d", error_message="bad"),
             Rule(name="fe", type="date_format", field="end", format="%d/%m/%Y", error_message="bad"),
             Rule(name="c", type="compare", field="end", compare_to="start", compare_op="gt", error_message="bad")]
    for out in _both({"start": "20260101", "end": "02/01/2026"}, rules):
        assert out["valid"] is True
    for out in _both({"start": "20260103", "end": "02/01/2026"}, rules):
        assert _codes(out) == ["OPENDQV_COMPARE_C"]


# ── §4(c)(d) CSV cells are text; a blank cell is absent ─────────────────

_CSV = b"gtin,qty\n0012345678905,\n4006381333931,NA\n"


def test_rest_upload_reads_cells_as_text():
    from opendqv.api.deps import _parse_upload
    for name in ("g.csv", "g.txt"):
        rows = _parse_upload(_CSV, name).to_dict(orient="records")
        assert rows == [{"gtin": "0012345678905", "qty": ""}, {"gtin": "4006381333931", "qty": "NA"}]


def test_a_leading_zero_gtin_passes_its_check_digit_and_a_blank_cell_is_absent():
    from opendqv.api.deps import _parse_upload
    rules = [Rule(name="g", type="checksum", field="gtin", checksum_algorithm="mod10_gs1", error_message="bad"),
             Rule(name="q", type="min", field="qty", min=0, error_message="bad")]
    rows = _parse_upload(_CSV, "g.csv").to_dict(orient="records")
    res = validate_batch(rows[:1], rules, "t")["results"][0]
    assert res["valid"] is True, res


def test_validate_file_reads_cells_as_text(tmp_path, monkeypatch):
    import opendqv.cli as cli
    (tmp_path / "g.csv").write_bytes(_CSV)
    doc = {"name": "g", "version": "0.1", "rules": [
        {**_BASE, "name": "g", "field": "gtin", "type": "checksum", "checksum_algorithm": "mod10_gs1"}]}
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "g.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    reg = ContractRegistry(tmp_path / "contracts")
    monkeypatch.setattr(cli, "get_registry", lambda: reg)
    seen = []
    real = cli.validate_batch

    def spy(records, *a, **k):
        seen.extend(records)
        return real(records, *a, **k)

    monkeypatch.setattr(cli, "validate_batch", spy)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_validate_file(argparse.Namespace(contract="g", path=str(tmp_path / "g.csv"),
                                                 output_failures=None, observe_only=False))
    assert exc.value.code in (0, None)
    assert seen == [{"gtin": "0012345678905", "qty": ""}, {"gtin": "4006381333931", "qty": "NA"}]


# ── blind-review findings (3.0.6 pre-merge) ─────────────────────────────

@pytest.mark.parametrize("fmt,value,year", [("%Y %y", "2024 25", 2025), ("%y %Y", "25 2024", 2024),
                                            ("YYYY YY", "2024 25", 2025)])
def test_stored_year_read_twice_the_last_occurrence_decides(fmt, value, year):
    from opendqv.core.validator import _human_to_strptime
    assert _read_layout(value, _human_to_strptime(fmt)).year == year


def test_stored_year_read_twice_every_occurrence_must_be_valid():
    # last decides 2025, which has no 29 February
    rule = Rule(name="d", type="date_format", field="d", format="%d/%m/%Y %y", error_message="bad")
    for out in _both({"d": "29/02/2024 25"}, [rule]):
        assert out["valid"] is False
    with pytest.raises(ValueError):
        _read_layout("0000 99", "%Y %y")


def test_semver_parts_of_any_length():
    big = "9" * 5000
    assert _semver_tuple(big + ".0.0") == _semver_tuple(big + ".0.0")
    assert _semver_tuple(big + ".0.0") > _semver_tuple("9" * 4999 + ".0.0")
    assert _semver_tuple("1.0.0-" + big) > _semver_tuple("1.0.0-1")
    assert _semver_tuple("10.0.0") > _semver_tuple("9.0.0")


def test_human_minutes_twice_says_why():
    [msg] = rule_submission_problems({**_BASE, "type": "date_format", "format": "HH:MM DD/MM/YYYY"})
    assert "MM after HH reads the minutes" in msg


def test_profiler_top_values_skip_blank_cells():
    from opendqv.api.deps import _parse_upload
    from opendqv.core.profiler import profile_records
    lines = [b"id,colour"] + [f"{i},{c}".encode() for i, c in enumerate(["red"] * 4 + ["blue"] * 3 + [""] * 3)]
    rows = _parse_upload(b"\n".join(lines) + b"\n", "c.csv").to_dict(orient="records")
    prof = profile_records(rows, contract_name="c")["profile"]["fields"]["colour"]
    assert prof["null_count"] == 3
    assert prof["top_values"] == {"red": 4, "blue": 3}
