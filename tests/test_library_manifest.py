"""The committed starter-library manifest must match the bundled contracts (D5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from library_manifest import MANIFEST_PATH, build_manifest


def test_manifest_is_current():
    assert MANIFEST_PATH.exists(), "run: python scripts/library_manifest.py"
    committed = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert committed == build_manifest(), (
        "library_manifest.json is stale — the bundled contracts changed; "
        "run `python scripts/library_manifest.py` and commit the result"
    )


def test_manifest_covers_every_bundled_contract():
    manifest = build_manifest()
    files = sorted(p.name for p in (ROOT / "opendqv" / "contracts").glob("*.yaml"))
    assert [e["file"] for e in manifest["contracts"]] == files
    assert all(e["rules_sha256"] and e["rule_count"] > 0 for e in manifest["contracts"])
