"""Guard: every template in examples/starter_contracts/ loads through the registry and
lints with zero errors.

The templates were written in a pre-1.0 dialect (`- field:` / `rule:` / `lookup_values:`)
and drifted unnoticed for months because nothing exercised them (2.7.0 docs audit). This
pins the migrated shape so the examples a newcomer copies are the examples the engine
runs.
"""
import shutil
from pathlib import Path

import pytest

from opendqv.core.contracts import ContractRegistry
from opendqv.core.linter import lint_contract_file

# CI runs from /home/runner — never hardcode the checkout path.
STARTER_DIR = Path(__file__).resolve().parent.parent / "examples" / "starter_contracts"
TEMPLATES = sorted(STARTER_DIR.glob("*.yaml"))


def test_directory_has_templates():
    assert len(TEMPLATES) >= 17, [p.name for p in TEMPLATES]


@pytest.fixture(scope="module")
def loaded_registry(tmp_path_factory):
    """Copy every template + ref/ into a temp contracts dir and load it once."""
    d = tmp_path_factory.mktemp("starter_contracts")
    for p in TEMPLATES:
        shutil.copy(p, d / p.name)
    shutil.copytree(STARTER_DIR / "ref", d / "ref")
    reg = ContractRegistry(d)
    return {c["name"] for c in reg.list_contracts(include_all=True)}


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_loads_through_registry(template, loaded_registry):
    # Flat-format templates are named from the filename stem; the `contract:` format
    # from its own name: field. Either way the stem must resolve to a loaded contract.
    stem = template.stem
    assert stem in loaded_registry, f"{template.name} did not load (loaded: {sorted(loaded_registry)})"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_lints_with_zero_errors(template):
    result = lint_contract_file(str(template))
    errors = [i for i in result.issues if i.severity == "error"]
    assert not errors, [(i.code, i.rule_name, i.message) for i in errors]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_uses_current_rule_shape(template):
    """No `rule:` keys, no unknown-type warnings — every rule carries name/type/field/error_message."""
    import yaml

    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    rules = raw["contract"]["rules"] if "contract" in raw else raw["rules"]
    for r in rules:
        assert "rule" not in r, f"{template.name}: legacy `rule:` key in {r}"
        for key in ("name", "type", "field", "severity", "error_message"):
            assert key in r, f"{template.name}: rule {r.get('name')} missing `{key}`"
    names = [r["name"] for r in rules]
    assert len(names) == len(set(names)), f"{template.name}: duplicate rule names"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_has_no_bare_yaml_booleans(template):
    """PyYAML is YAML 1.1: bare YES/NO/TRUE/FALSE/ON/OFF become Python booleans, which
    stringify to "True"/"False" and never match the "YES"/"NO"/"TRUE" a record sends.
    Norway's ISO code is NO. Every such literal in a value position must be quoted."""
    import yaml

    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    rules = raw["contract"]["rules"] if "contract" in raw else raw["rules"]
    for r in rules:
        for v in r.get("allowed_values") or []:
            assert not isinstance(v, bool), f"{template.name}: {r['name']} has bare boolean {v!r} in allowed_values"
        for block in ("required_if", "condition"):
            for k in ("value", "not_value"):
                v = (r.get(block) or {}).get(k)
                assert not isinstance(v, bool), f"{template.name}: {r['name']} {block}.{k} is bare boolean {v!r}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_required_if_block_only_on_required_if_rules(template):
    """/code-review of #162: a `required_if:` block on any other rule type is
    inert (only the required_if handler reads it) — gating is `condition:`."""
    import yaml
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    rules = (raw.get("contract") or raw).get("rules") or []
    stray = [r["name"] for r in rules if isinstance(r, dict) and "required_if" in r and r.get("type") != "required_if"]
    assert stray == [], f"{template.name}: required_if block on non-required_if rule(s) {stray} — use condition:"
