"""#146 — frozen minimal_clean rows + previous-release replay (the breaking-change detector)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "conformance" / "frozen" / "minimal_clean.jsonl"
BUNDLED = ROOT / "opendqv" / "contracts"

# Lives in conformance/frozen/ so the generated-corpus glob never picks it up.
# Starters with no frozen minimal record yet. SHRINK ONLY: a name may be removed
# from this set (by adding its row), never added. New starters must ship a row.
_UNSEEDED = {
    "agriculture_batch", "automotive_vehicle", "education_student", "eu_gdpr_processing_record",
    "financial_services_customer", "financial_trade", "fmcg_product", "gdpr_processing_record",
    "hipaa_disclosure_accounting", "media_content", "ofwat_meter_reading",
}


def _rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _contract(name: str):
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule
    from opendqv.core.validator import strict_schema_kwargs
    raw = yaml.safe_load((BUNDLED / f"{name}.yaml").read_text(encoding="utf-8"))["contract"]
    rules = [Rule(**r) for r in raw.get("rules", [])]
    dc = DataContract(name=raw["name"], rules=rules, strict_schema=bool(raw.get("strict_schema")),
                      allowed_fields=raw.get("allowed_fields") or [])
    return rules, strict_schema_kwargs(dc, rules)


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["contract"])
def test_minimal_clean_row_still_validates_on_both_paths(row):
    from opendqv.core.validator import validate_batch, validate_record
    rules, kw = _contract(row["contract"])
    assert validate_record(row["record"], rules, row["contract"], **kw)["valid"], (
        f"{row['contract']}: the smallest record valid at {row['frozen_at']} is now rejected — "
        f"a breaking library/engine change; record it in the CHANGELOG or fix the regression")
    assert validate_batch([row["record"]], rules, row["contract"], **kw)["results"][0]["valid"]


def test_minimal_clean_rows_are_minimal():
    """Dropping any key makes the record invalid — otherwise it is not the minimal record."""
    from opendqv.core.validator import validate_record
    for row in _rows():
        rules, kw = _contract(row["contract"])
        for k in row["record"]:
            trial = {x: v for x, v in row["record"].items() if x != k}
            assert not validate_record(trial, rules, row["contract"], **kw)["valid"], (row["contract"], k)


def test_every_bundled_contract_is_seeded_or_explicitly_unseeded():
    seeded = {r["contract"] for r in _rows()}
    bundled = {p.stem for p in BUNDLED.glob("*.yaml")}
    assert seeded <= bundled
    assert bundled - seeded == _UNSEEDED, (
        f"unseeded set drifted: add minimal_clean rows for {sorted((bundled - seeded) - _UNSEEDED)} "
        f"or remove stale allowlist entries {sorted(_UNSEEDED - (bundled - seeded))}")
    assert not (seeded & _UNSEEDED)


def test_generator_never_touches_minimal_clean():
    src = (ROOT / "scripts" / "conformance_fixtures.py").read_text(encoding="utf-8")
    assert "minimal_clean" not in src


def _git_ok() -> bool:
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True).returncode == 0


@pytest.mark.skipif(not _git_ok(), reason="needs the git history")
def test_previous_release_replay_reports_and_gates():
    sys.path.insert(0, str(ROOT / "scripts"))
    import replay_previous_corpus as rp
    ref = rp.latest_tag()
    if ref is None:
        pytest.skip("no v* tag reachable (shallow clone) — CI's test job fetches full history; locally run `git fetch --tags`")
    report = rp.replay(ref)
    assert report["rows"] > 0, f"no fixture rows found at {ref}"
    for f in report["flips"]:
        print("FLIP", f["contract"], f.get("kind"), f.get("was_valid"), "->", f.get("now_valid"),
              f.get("old_codes"), "->", f.get("new_codes"))
    assert report["accepted_now_rejected"] == [], (
        f"{len(report['accepted_now_rejected'])} record(s) accepted at {ref} are rejected now — "
        f"a breaking change. Either fix it, or if deliberate, record it in the CHANGELOG BREAKING block, "
        f"list the contract in tests/fixtures/conformance/frozen/accepted_breaks.json with that CHANGELOG "
        f"version, and regenerate the corpus in the same PR.")
    # Every accepted break must be owned by a CHANGELOG section that exists AND
    # that names the contract — citing a real version number is not enough.
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for item in rp.load_accepted_breaks().values():
        header = f"## [{item['changelog']}]"
        assert header in changelog, (
            f"accepted break for {item['contract']} cites CHANGELOG {item['changelog']}, which has no section")
        section = changelog.split(header, 1)[1].split("\n## [", 1)[0]
        assert item["contract"] in section, (
            f"CHANGELOG {item['changelog']} does not mention {item['contract']}; a deliberate break must be documented")
