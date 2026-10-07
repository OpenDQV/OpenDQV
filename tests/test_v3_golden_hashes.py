"""3.0.0 removed ``contexts:`` and the ``contract:`` wrapper. No hash may move.

The fixture was generated on the v2.10.5 tag with ``scripts/golden_hashes.py``
(fixed node id + timestamp). Every bundled contract must produce the same
content_hash, entry_hash, effective_rule_hash and manifest rules_sha256 on
the working tree.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "golden_hashes_v2_10_5.json"


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
    assert not moved, moved


def test_fixture_rules_sha256_matches_library_manifest():
    before = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "library_manifest.json").read_text(encoding="utf-8"))
    pinned = {e["name"]: e["rules_sha256"] for e in manifest["contracts"]}
    assert {k.split("@")[0]: v["rules_sha256"] for k, v in before.items()} == pinned
