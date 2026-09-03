"""#149 — `_contract_to_yaml` (MCP drafts) must be lossless for every rule field."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from opendqv.core.contracts import ContractRegistry, DataContract
from opendqv.core.rule_parser import Rule

BUNDLED = Path(__file__).resolve().parent.parent / "opendqv" / "contracts"
_STAMPED = {"inherited", "federation_tier", "provenance", "severity_floor", "lookup_auth_header"}


def _norm(rule: Rule) -> dict:
    d = rule.model_dump(exclude_none=True, mode="json")
    return {k: v for k, v in d.items() if k not in _STAMPED and not k.startswith("cached_") and k != "compiled_pattern"}


def _reg(tmp_path) -> ContractRegistry:
    d = tmp_path / "c"
    d.mkdir()
    return ContractRegistry(d)


@pytest.mark.parametrize("path", sorted(BUNDLED.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_bundled_contract_round_trips_through_the_draft_writer(tmp_path, path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["contract"]
    rules = [Rule(**r) for r in raw.get("rules", [])]
    dc = DataContract(name=raw["name"], version=str(raw.get("version", "1.0")), rules=rules,
                      description=raw.get("description", ""), owner=raw.get("owner", ""))
    text = _reg(tmp_path)._contract_to_yaml(dc)
    back = [Rule(**r) for r in yaml.safe_load(text)["contract"]["rules"]]
    assert [_norm(r) for r in back] == [_norm(r) for r in rules]


def test_mcp_draft_keeps_every_parameter(tmp_path):
    reg = _reg(tmp_path)
    rules = [
        {"name": "cmp", "type": "compare", "field": "end", "compare_to": "start", "compare_op": "gte",
         "condition": {"field": "kind", "value": "range"}, "optional": True, "error_message": "end>=start"},
        {"name": "lk", "type": "lookup", "field": "cc", "lookup_file": "ref/iso_country.txt", "severity": "warning"},
        {"name": "chk", "type": "checksum", "field": "nhs", "checksum_algorithm": "nhs"},
        {"name": "rif", "type": "required_if", "field": "ref", "required_if": {"field": "t", "value": "x"}},
        {"name": "neg", "type": "regex", "field": "e", "pattern": "@gmail\\.com$", "negate": True},
        {"name": "rng", "type": "range", "field": "n", "min": 1, "max": 9},
    ]
    c = reg.create_draft(name="MCP_lossless", description="d", owner="o", created_by="a", rules_data=rules)
    on_disk = yaml.safe_load((reg.contracts_dir / "MCP_lossless.yaml").read_text(encoding="utf-8"))["contract"]
    by_name = {r["name"]: r for r in on_disk["rules"]}
    assert by_name["cmp"]["compare_to"] == "start" and by_name["cmp"]["condition"]["value"] == "range"
    assert by_name["cmp"]["optional"] is True
    assert by_name["lk"]["lookup_file"] == "ref/iso_country.txt"
    assert by_name["chk"]["checksum_algorithm"] == "nhs"
    assert by_name["rif"]["required_if"] == {"field": "t", "value": "x"}
    assert by_name["neg"]["negate"] is True
    assert by_name["rng"]["min"] == 1 and by_name["rng"]["max"] == 9   # YAML aliases, not min_value
    # engine-stamped keys never reach disk; caches never do either
    for r in on_disk["rules"]:
        assert not ({"provenance", "federation_tier", "severity_floor", "lookup_auth_header"} & set(r))
        assert not any(k.startswith("cached_") or k == "compiled_pattern" for k in r)
    # and it reloads to the same rules
    reg.reload()
    assert [_norm(r) for r in reg.get("MCP_lossless").rules] == [_norm(r) for r in c.rules]


def test_historical_keys_still_present(tmp_path):
    text = _reg(tmp_path)._contract_to_yaml(DataContract(name="t", rules=[Rule(name="r", type="not_empty", field="f")]))
    r = yaml.safe_load(text)["contract"]["rules"][0]
    for k in ("name", "description", "type", "field", "severity", "error_message", "negate", "all_of"):
        assert k in r
    assert list(r)[:6] == ["name", "description", "type", "field", "severity", "error_message"]
