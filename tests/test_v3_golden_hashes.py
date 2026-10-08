"""3.0.0 removed ``contexts:`` and the ``contract:`` wrapper. No hash may move.

The fixture was generated on the v2.10.5 tag with ``scripts/golden_hashes.py``
(fixed node id + timestamp). Every bundled contract must produce the same
content_hash, entry_hash, effective_rule_hash and manifest rules_sha256 on
the working tree — except a contract whose content was changed on purpose
by a later library release, listed in CHANGED_SINCE_2_10_5 with its reason.
Each listed contract must move (and match library_manifest.json); nothing
else may.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "golden_hashes_v2_10_5.json"

CHANGED_SINCE_2_10_5 = {
    "gdpr_processing_record@0.1": "3.0.3: consent_timestamp error_message (ISO 8601 date or datetime)",
    "eu_gdpr_processing_record@0.1": "3.0.3: consent_timestamp error_message (ISO 8601 date or datetime)",
    "technology_event@0.1": "3.0.3: event_timestamp error_message (ISO 8601 date or datetime)",
}


def _manifest_rules_sha256() -> dict:
    manifest = json.loads((ROOT / "library_manifest.json").read_text(encoding="utf-8"))
    return {e["name"]: e["rules_sha256"] for e in manifest["contracts"]}


def test_hashes_unchanged_since_2_10_5():
    # Subprocess: the generator loads the registry against its own temp
    # contracts dir and DB, which must not touch this session's config.
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "golden_hashes.py"), str(ROOT)],
        capture_output=True, text=True, check=True, cwd=str(ROOT.parent),
    ).stdout
    now = json.loads(out)
    before = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sorted(now) == sorted(before)
    moved = {k: (before[k], now[k]) for k in before if before[k] != now[k]}
    assert set(moved) == set(CHANGED_SINCE_2_10_5), moved
    pinned = _manifest_rules_sha256()
    for k in CHANGED_SINCE_2_10_5:
        assert now[k]["rules_sha256"] == pinned[k.split("@")[0]], k


def test_fixture_rules_sha256_matches_library_manifest():
    before = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pinned = _manifest_rules_sha256()
    unchanged = {k: v for k, v in before.items() if k not in CHANGED_SINCE_2_10_5}
    assert {k.split("@")[0]: v["rules_sha256"] for k, v in unchanged.items()} == {
        n: h for n, h in pinned.items() if f"{n}@0.1" not in CHANGED_SINCE_2_10_5}
