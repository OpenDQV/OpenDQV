"""2.8.0 — cross-field date rules honour the field's declared ``date_format`` layout.

Reported by the managed-engine maintainer (2026-09-05): every cross-field date
rule parsed with a hard-coded ISO assumption, so a contract declaring
``DD/MM/YYYY`` was internally inconsistent — its format rule accepted a value
its compare / date_diff / min_age / age_match rules could not read. Worse,
``compare`` fell back to string order, so an inverted UK pair passed silently.

Every case here runs on BOTH paths (validate_record and a batch of the same
records) and asserts the exact set of rules that fire.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from opendqv.core.rule_parser import Rule, parse_rules
from opendqv.core.validator import (
    _layout_conflicts_warned,
    _parse_date,
    resolve_date_layouts,
    validate_batch,
    validate_record,
)

UK = '''
rules:
  - {name: start_fmt, type: date_format, field: start, format: "%d/%m/%Y", error_message: start format}
  - {name: end_fmt, type: date_format, field: end, format: "%d/%m/%Y", error_message: end format}
  - {name: end_after_start, type: compare, field: end, compare_to: start, compare_op: gte, error_message: end before start}
  - {name: span, type: date_diff, field: end, date_diff_field: start, date_diff_unit: days, min_value: 0, error_message: negative span}
  - {name: dob_fmt, type: date_format, field: dob, format: "%d/%m/%Y", error_message: dob format}
  - {name: adult, type: date_format, field: dob, format: "%d/%m/%Y", min_age: 18, error_message: under 18}
  - {name: age_ok, type: age_match, field: age, dob_field: dob, error_message: age mismatch}
'''

COMPACT = UK.replace('"%d/%m/%Y"', '"%Y%m%d"')


def _fired(rules, record) -> tuple[list[str], list[str]]:
    single = sorted(e["rule"] for e in validate_record(record, rules)["errors"])
    batch = sorted(e["rule"] for e in validate_batch([record], rules)["results"][0]["errors"])
    return single, batch


def _both(rules, record, expected: list[str]):
    single, batch = _fired(rules, record)
    assert single == expected, f"single path fired {single}, expected {expected}"
    assert batch == expected, f"batch path fired {batch}, expected {expected}"


class TestUKLayout:
    rules = parse_rules(UK)

    def test_inverted_pair_fails_compare_and_date_diff_and_nothing_else(self):
        # 2.7.0: compare PASSED ("3" > "0" as strings) and span/adult/age_ok were false failures.
        _both(self.rules, {"start": "01/01/2027", "end": "31/12/2026", "dob": "01/01/1990", "age": 36},
              ["end_after_start", "span"])

    def test_valid_pair_and_adult_pass_everything(self):
        # 2.7.0: compare FAILED ("0" < "2" as strings) on a correct pair.
        _both(self.rules, {"start": "20/01/2027", "end": "05/02/2027", "dob": "01/01/1990", "age": 36}, [])

    def test_six_year_old_fails_min_age_only(self):
        dob = (date.today() - timedelta(days=6 * 365 + 40)).strftime("%d/%m/%Y")
        _both(self.rules, {"start": "20/01/2027", "end": "05/02/2027", "dob": dob, "age": 6}, ["adult"])

    def test_age_mismatch_still_caught_with_declared_layout(self):
        _both(self.rules, {"start": "20/01/2027", "end": "05/02/2027", "dob": "01/01/1990", "age": 20}, ["age_ok"])

    def test_value_the_layout_cannot_read_fails_the_reading_rules_too(self):
        # The format rule names the shape; the cross-field rules must not
        # fall through to a string/number comparison on an unreadable operand.
        single, batch = _fired(self.rules, {"start": "2027-01-20", "end": "05/02/2027", "dob": "01/01/1990", "age": 36})
        assert single == batch
        assert {"start_fmt", "end_after_start", "span"} <= set(single)


class TestCompactLayout:
    rules = parse_rules(COMPACT)

    def test_adult_passes_min_age_and_age_match(self):
        # 2.7.0: min_age and age_match were false failures on every compact record.
        _both(self.rules, {"start": "20270120", "end": "20270205", "dob": "19900101", "age": 36}, [])

    def test_inverted_pair_fails_by_date_not_by_accident_of_number_order(self):
        _both(self.rules, {"start": "20270101", "end": "20261231", "dob": "19900101", "age": 36},
              ["end_after_start", "span"])


class TestMixedOperands:
    """One declared operand against one undeclared (ISO) operand — per-operand layouts."""
    rules = parse_rules('''
rules:
  - {name: start_fmt, type: date_format, field: start, format: "DD/MM/YYYY", error_message: start format}
  - {name: end_after_start, type: compare, field: end, compare_to: start, compare_op: gt, error_message: end before start}
  - {name: span, type: date_diff, field: end, date_diff_field: start, date_diff_unit: days, min_value: 1, error_message: span}
''')

    def test_uk_start_against_iso_end(self):
        _both(self.rules, {"start": "20/01/2027", "end": "2027-02-05"}, [])
        _both(self.rules, {"start": "20/01/2027", "end": "2027-01-05"}, ["end_after_start", "span"])

    def test_iso_datetime_on_the_undeclared_side(self):
        _both(self.rules, {"start": "20/01/2027", "end": "2027-01-21T12:00:00Z"}, [])  # 1.5 days later
        _both(self.rules, {"start": "20/01/2027", "end": "2027-01-20T12:00:00Z"}, ["span"])  # gt holds, span < 1 day
        _both(self.rules, {"start": "20/01/2027", "end": "2027-01-19T23:59:59Z"}, ["end_after_start", "span"])


class TestSentinelsAndSameDate:
    def test_compare_to_today_with_uk_layout(self):
        rules = parse_rules('''
rules:
  - {name: fmt, type: date_format, field: d, format: "%d/%m/%Y", error_message: fmt}
  - {name: not_future, type: compare, field: d, compare_to: today, compare_op: lte, error_message: future}
''')
        _both(rules, {"d": "01/01/2000"}, [])
        _both(rules, {"d": (date.today() + timedelta(days=400)).strftime("%d/%m/%Y")}, ["not_future"])

    def test_same_date_compares_parsed_dates_under_a_layout(self):
        rules = parse_rules('''
rules:
  - {name: fmt, type: date_format, field: trade_date, format: "%d/%m/%Y", error_message: fmt}
  - {name: t0, type: compare, field: trade_date, compare_to: executed_at, compare_op: same_date, error_message: t0}
''')
        _both(rules, {"trade_date": "15/01/2027", "executed_at": "2027-01-15T09:30:00Z"}, [])
        _both(rules, {"trade_date": "15/01/2027", "executed_at": "2027-01-16T09:30:00Z"}, ["t0"])


class TestResolution:
    def test_layouts_come_from_the_first_declared_format_per_field(self):
        rules = parse_rules('''
rules:
  - {name: a, type: date_format, field: d, format: "DD/MM/YYYY", error_message: a}
  - {name: b, type: date_format, field: e, error_message: b}
  - {name: c, type: regex, field: f, pattern: "^x$", error_message: c}
''')
        assert resolve_date_layouts(rules) == {"d": "%d/%m/%Y"}   # e: no format → ISO path (absent)

    def test_conflicting_layouts_warn_once_and_first_wins(self, caplog):
        _layout_conflicts_warned.clear()
        rules = parse_rules('''
rules:
  - {name: a, type: date_format, field: d, format: "DD/MM/YYYY", error_message: a}
  - {name: b, type: date_format, field: d, format: "YYYY-MM-DD", error_message: b}
  - {name: c, type: compare, field: d, compare_to: today, compare_op: lte, error_message: c}
''')
        with caplog.at_level(logging.WARNING, logger="opendqv.core.validator"):
            validate_record({"d": "01/01/2000"}, rules)
            validate_record({"d": "01/01/2000"}, rules)
        warnings = [r for r in caplog.records if "two date_format layouts" in r.getMessage()]
        assert len(warnings) == 1
        assert rules[2].cached_date_layout == "%d/%m/%Y"

    def test_stamps_are_excluded_from_serialisation(self):
        rules = parse_rules(UK)
        validate_record({"start": "01/01/2027", "end": "31/12/2026", "dob": "01/01/1990", "age": 36}, rules)
        for r in rules:
            dumped = r.model_dump(by_alias=True, exclude_none=True, mode="json")
            assert "cached_date_layout" not in dumped and "cached_other_date_layout" not in dumped

    def test_rebuilt_rule_list_is_restamped(self):
        rules = parse_rules(UK)
        validate_record({"start": "01/01/2027", "end": "31/12/2026"}, rules)
        assert rules[2].cached_date_layout == "%d/%m/%Y"
        # a draft edit replaces the rule objects: drop the format rules → ISO path again
        rebuilt = [Rule(**r.model_dump(by_alias=True, exclude_none=True)) for r in rules if r.type != "date_format"]
        out = validate_record({"start": "2027-01-01", "end": "2026-12-31"}, rebuilt)
        assert [e["rule"] for e in out["errors"]] == ["end_after_start", "span"]
        assert rebuilt[0].cached_date_layout is None

    def test_parse_date_with_layout_is_utc_midnight(self):
        dt = _parse_date("11/12/2026", "%d/%m/%Y")
        assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 12, 11, 0)
        assert dt.tzinfo is not None
        with pytest.raises(ValueError):
            _parse_date("2026-12-11", "%d/%m/%Y")

    def test_min_age_on_a_non_date_format_rule_uses_the_field_layout(self):
        rules = parse_rules('''
rules:
  - {name: fmt, type: date_format, field: dob, format: "%d/%m/%Y", error_message: fmt}
  - {name: present_adult, type: not_empty, field: dob, min_age: 18, error_message: under 18}
''')
        _both(rules, {"dob": "01/01/1990"}, [])
        _both(rules, {"dob": (date.today() - timedelta(days=3000)).strftime("%d/%m/%Y")}, ["present_adult"])


def test_no_bundled_contract_declares_a_non_iso_layout_on_a_cross_field_date_field():
    """The maintainer's check: today the two nearest templates are ISO —
    social_media_age_compliance (date) and telecoms_cdr, whose call_start /
    call_end declare the ISO datetime layout ``%Y-%m-%dT%H:%M:%S`` under a
    compare. If a golden template ever declares a non-ISO layout on a field a
    cross-field rule reads, this names it so the corpus gets a row for it."""
    from pathlib import Path
    from opendqv.core.contracts import ContractRegistry
    from opendqv.core.validator import _CROSS_FIELD_DATE_TYPES, _counterpart_field

    iso = {"%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"}
    reg = ContractRegistry(Path(__file__).resolve().parents[1] / "opendqv" / "contracts")
    found = []
    for entry in reg.list_contracts(include_all=True):
        c = reg.get(entry["name"])
        rules = reg.get_rules_with_context(c, None)
        layouts = {f: fmt for f, fmt in resolve_date_layouts(rules).items() if fmt not in iso}
        for r in rules:
            reads = {r.field} | ({_counterpart_field(r)} if r.type in _CROSS_FIELD_DATE_TYPES else set())
            reads.discard(None)
            if (r.type in _CROSS_FIELD_DATE_TYPES or r.cached_has_age_constraint) and reads & set(layouts):
                found.append((c.name, r.name, {f: layouts[f] for f in reads & set(layouts)}))
    assert found == [], found


def test_linter_reports_a_layout_conflict_as_a_warning():
    from opendqv.core.linter import lint_contract_yaml
    res = lint_contract_yaml('''
contract:
  name: t
  rules:
    - {name: a, type: date_format, field: d, format: "DD/MM/YYYY", error_message: a}
    - {name: b, type: date_format, field: d, format: "YYYY-MM-DD", error_message: b}
    - {name: c, type: date_format, field: d, format: "DD/MM/YYYY", error_message: c}
''', "t")
    hits = [i for i in res.issues if i.code == "DATE_LAYOUT_CONFLICT"]
    assert [(i.severity, i.rule_name) for i in hits] == [("warning", "b")]


def test_context_rebuilt_rules_with_reused_addresses_are_stamped_every_call():
    """Sonnet red-team: a context merge mints fresh Rule objects each request and
    CPython reuses their addresses, so identity-keyed memoisation served stale
    stamps. Two contracts with different layouts, rebuilt alternately."""
    uk = parse_rules(UK)
    iso_rules = parse_rules(UK.replace('"%d/%m/%Y"', '"YYYY-MM-DD"'))
    for _ in range(20):
        a = [Rule(**r.model_dump(by_alias=True, exclude_none=True)) for r in uk]
        assert validate_record({"start": "01/01/2027", "end": "31/12/2026"}, a)["valid"] is False
        del a
        b = [Rule(**r.model_dump(by_alias=True, exclude_none=True)) for r in iso_rules]
        assert validate_record({"start": "2027-01-01", "end": "2026-12-31"}, b)["valid"] is False
        assert validate_record({"start": "2026-12-31", "end": "2027-01-01"}, b)["valid"] is True
        del b


def test_dropping_the_format_rule_from_a_live_list_clears_the_stamp():
    rules = parse_rules(UK)
    validate_record({"start": "01/01/2027", "end": "31/12/2026"}, rules)
    compare_rule = rules[2]
    assert compare_rule.cached_date_layout == "%d/%m/%Y"
    rules[:] = [r for r in rules if r.type != "date_format"]   # in-place draft edit keeps the compare object
    out = validate_record({"start": "2027-01-01", "end": "2026-12-31"}, rules)
    assert compare_rule.cached_date_layout is None
    assert [e["rule"] for e in out["errors"]] == ["end_after_start", "span"]


def test_python_only_strptime_directive_keeps_batch_and_single_in_parity():
    # DuckDB rejects %e; the batch date_format and age add-on must fall back to
    # the single-path check. Python accepts %e only from 3.13 (CI runs 3.11).
    try:
        _parse_date(" 1/01/1990", "%e/%m/%Y")
    except ValueError:
        pytest.skip("this Python's strptime has no %e directive")
    rules = parse_rules('''
rules:
  - {name: dob_fmt, type: date_format, field: dob, format: "%e/%m/%Y", min_age: 18, error_message: under 18}
''')
    _both(rules, {"dob": " 1/01/1990"}, [])
    _both(rules, {"dob": " 1/01/" + str(date.today().year - 5)}, ["dob_fmt"])
