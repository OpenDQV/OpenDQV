"""
3.0.5 §2: shapes refused when a contract is submitted (managed-engine parity,
422 contract_rule_invalid there). Each used to load and then fail every record
or never fire. Stored content still loads, with a warning, and keeps its old
reading — a refusal at load would delist a contract at boot.
"""
import logging
from pathlib import Path

import pytest
import yaml

from opendqv.core.contracts import ContractRegistry
from opendqv.core.linter import lint_contract_yaml
from opendqv.core.submission import contract_submission_problems, rule_submission_problems

_BASE = {"name": "r", "field": "f", "error_message": "bad"}

REFUSED = [
    ("unknown compare_op", {"type": "compare", "compare_to": "g", "compare_op": "after"}),
    ("== is not an alias", {"type": "compare", "compare_to": "g", "compare_op": "=="}),
    ("value and not_value", {"type": "regex", "pattern": "x", "condition": {"field": "k", "value": "a", "not_value": "b"}}),
    ("condition value null", {"type": "regex", "pattern": "x", "condition": {"field": "k", "value": None}}),
    ("condition not_value null", {"type": "regex", "pattern": "x", "condition": {"field": "k", "not_value": None}}),
    ("negate on allowed_values", {"type": "allowed_values", "allowed_values": ["a"], "negate": True}),
    ("equals in required_if", {"type": "required_if", "required_if": {"field": "k", "equals": "A"}}),
    ("equals in forbidden_if", {"type": "forbidden_if", "forbidden_if": {"field": "k", "equals": "A"}}),
    ("null trigger value", {"type": "required_if", "required_if": {"field": "k", "value": None}}),
    ("trigger without value", {"type": "required_if", "required_if": {"field": "k"}}),
    ("%b directive", {"type": "date_format", "format": "%d %b %Y"}),
    ("%z directive", {"type": "date_format", "format": "%Y-%m-%dT%H:%M:%S%z"}),
    ("%j directive", {"type": "date_format", "format": "%Y-%j"}),
    ("%p directive", {"type": "date_format", "format": "%I:%M %p"}),
    ("%c directive", {"type": "date_format", "format": "%c"}),
    ("%T directive", {"type": "date_format", "format": "%T"}),
    ("%-d directive", {"type": "date_format", "format": "%-d/%m/%Y"}),
    ("no directive", {"type": "date_format", "format": "dd/mm/yyyy"}),
    ("%f not after . or ,", {"type": "date_format", "format": "%H:%M:%S%f"}),
    ("white space around the format", {"type": "date_format", "format": " %Y-%m-%d"}),
    ("Go date layout", {"type": "date_format", "format": "2006-01-02"}),
    ("Go RFC 3339 layout", {"type": "date_format", "format": "2006-01-02T15:04:05Z07:00"}),
    ("range missing a bound", {"type": "range", "min": 0}),
    ("regex without pattern", {"type": "regex"}),
    ("empty allowed_values", {"type": "allowed_values", "allowed_values": []}),
    ("lookup without lookup_file", {"type": "lookup"}),
    ("checksum without algorithm", {"type": "checksum"}),
    ("checksum unknown algorithm", {"type": "checksum", "checksum_algorithm": "mod11"}),
    ("checksum odd case", {"type": "checksum", "checksum_algorithm": "LUHN"}),
    ("string-typed bound", {"type": "min", "min": "10"}),
]

ACCEPTED = [
    {"type": "compare", "compare_to": "g", "compare_op": op} for op in
    ("gt", "lt", "gte", "lte", "eq", "neq", "same_date", ">", "<", ">=", "<=", "=", "!=")
] + [
    {"type": "regex", "pattern": "x", "negate": True},
    {"type": "allowed_values", "allowed_values": ["a"], "negate": False},
    {"type": "regex", "pattern": "x", "condition": {"field": "k", "present": False}},
    {"type": "required_if", "required_if": {"field": "k", "value": "A"}},
    {"type": "date_format", "format": "%Y-%m-%dT%H:%M:%S.%f"},
    {"type": "date_format", "format": "%H:%M:%S,%f"},
    {"type": "date_format", "format": "YYYY-MM-DD HH:MM:SS"},
    {"type": "date_format", "format": "100%% %Y"},
    {"type": "date_format"},
    {"type": "range", "min": 0, "max": 1},
    {"type": "checksum", "checksum_algorithm": "verhoeff"},
    {"type": "min", "min": 10},
]


@pytest.mark.parametrize("label,extra", REFUSED, ids=[r[0] for r in REFUSED])
def test_refused_shape(label, extra):
    assert rule_submission_problems({**_BASE, **extra}), label


@pytest.mark.parametrize("extra", ACCEPTED, ids=lambda e: f"{e['type']}:{e.get('compare_op', e.get('format', ''))}")
def test_accepted_shape(extra):
    assert rule_submission_problems({**_BASE, **extra}) == []


def test_the_bundled_library_and_examples_have_none():
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / "opendqv" / "contracts").glob("*.yaml")) + sorted((root / "examples").rglob("*.y*ml"))
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            assert contract_submission_problems(doc) == [], f.name


def test_mcp_create_draft_refuses(tmp_path):
    reg = ContractRegistry(tmp_path)
    with pytest.raises(ValueError, match="contract_rule_invalid"):
        reg.create_draft("MCP_x", "d", "o", "agent", [{**_BASE, "type": "range", "min": 0}])
    assert not (tmp_path / "MCP_x.yaml").exists()


def test_rule_add_and_update_refuse(tmp_path):
    reg = ContractRegistry(tmp_path)
    reg.create_draft("MCP_y", "d", "o", "agent", [{**_BASE, "type": "regex", "pattern": "x"}])
    with pytest.raises(ValueError, match="negate: true is for regex only"):
        reg.add_rule("MCP_y", {**_BASE, "name": "r2", "type": "allowed_values", "allowed_values": ["a"], "negate": True})
    with pytest.raises(ValueError, match="compare_op"):
        reg.update_rule("MCP_y", "r", {**_BASE, "type": "compare", "compare_to": "g", "compare_op": "after"})


def test_linter_reports_an_error():
    res = lint_contract_yaml(yaml.safe_dump({
        "name": "t", "version": "0.1", "rules": [{**_BASE, "type": "date_format", "format": "%d %b %Y"}]}), "t")
    assert [i for i in res.issues if i.code == "CONTRACT_RULE_INVALID" and i.severity == "error"]


def test_stored_content_still_loads_with_a_warning_and_its_old_reading(tmp_path, caplog):
    doc = {"name": "legacy", "version": "0.1", "status": "active", "rules": [
        {**_BASE, "name": "d", "field": "d", "type": "date_format", "format": "2006-01-02"},
        {**_BASE, "name": "c", "field": "a", "type": "compare", "compare_to": "b", "compare_op": "after"},
    ]}
    (tmp_path / "legacy.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="opendqv.core.contracts"):
        reg = ContractRegistry(tmp_path)
    contract = reg.get("legacy")
    assert contract is not None
    assert any("refused when a contract is submitted" in r.message for r in caplog.records)
    from opendqv.core.validator import validate_record
    # the Go layout reads as its alias; the unknown compare_op does not judge
    assert validate_record({"d": "2026-01-10", "a": 1, "b": 2}, contract.rules, "legacy")["valid"] is True
    assert validate_record({"d": "10/01/2026"}, contract.rules, "legacy")["valid"] is False


def test_fork_refuses(tmp_path, monkeypatch, capsys):
    import argparse
    import opendqv.cli as cli
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({"name": "bad", "version": "0.1", "rules": [
        {**_BASE, "type": "range", "min": 0}]}), encoding="utf-8")
    monkeypatch.setattr(cli, "CONTRACTS_DIR", tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_fork(argparse.Namespace(src="bad", dst="good", force=False))
    assert exc.value.code == 1
    assert "range needs both min and max" in capsys.readouterr().err
    assert not (tmp_path / "good.yaml").exists()
