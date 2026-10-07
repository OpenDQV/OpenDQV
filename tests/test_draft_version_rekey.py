"""A draft rule mutation bumps the contract's version (ACT-047-02 patch
counter). The registry key must move with it: before this fix
get(name, new_version) returned None and every later lifecycle call had to
use the stale key (found while testing 3.0.0; present since 2.x)."""
from pathlib import Path

import pytest

from opendqv.core.contracts import ContractRegistry

FLAT = """\
name: widget
version: "1.0"
owner_email: team@example.com
status: draft
rules:
  - name: sku_required
    type: not_empty
    field: sku
    error_message: sku is required
"""


@pytest.fixture
def reg(tmp_path: Path) -> ContractRegistry:
    (tmp_path / "widget.yaml").write_text(FLAT, encoding="utf-8")
    return ContractRegistry(tmp_path)


def _mutations(reg):
    yield lambda: reg.add_rule("widget", {"name": "qty_min", "type": "min", "field": "qty", "min": 0, "error_message": "x"})
    yield lambda: reg.update_rule("widget", "sku_required", {"name": "sku_required", "type": "not_empty", "field": "sku", "error_message": "y"})
    yield lambda: reg.delete_rule("widget", "sku_required")


@pytest.mark.parametrize("which", [0, 1, 2])
def test_registry_key_follows_the_bumped_version(reg, which):
    list(_mutations(reg))[which]()
    c = reg.get("widget")
    assert c.version == "1.0-draft.1"
    assert set(reg._contracts["widget"]) == {"1.0-draft.1"}
    assert reg.get("widget", "1.0-draft.1") is c
    assert reg.get("widget", "1.0") is None


def test_transition_with_the_reported_version_persists(reg, tmp_path):
    reg.add_rule("widget", {"name": "qty_min", "type": "min", "field": "qty", "min": 0, "error_message": "x"})
    v = reg.get("widget").version
    assert reg.submit_for_review("widget", v, "maker").status.value == "review"
    reloaded = ContractRegistry(tmp_path).get("widget", v)
    assert reloaded.status.value == "review" and reloaded.proposed_by == "maker"


def test_two_mutations_in_a_row(reg):
    reg.add_rule("widget", {"name": "a", "type": "not_empty", "field": "a", "error_message": "x"})
    reg.add_rule("widget", {"name": "b", "type": "not_empty", "field": "b", "error_message": "x"})
    assert set(reg._contracts["widget"]) == {"1.0-draft.2"}
    assert {r.name for r in reg.get("widget", "1.0-draft.2").rules} == {"sku_required", "a", "b"}
