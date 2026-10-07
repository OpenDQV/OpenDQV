#!/usr/bin/env python3
"""Golden hash pin for the 3.0.0 envelope change.

For every bundled contract, load it through the engine's own registry into a
fresh history DB (fixed node id + timestamp so entry_hash is deterministic)
and emit the stored content_hash and entry_hash, the effective_rule_hash of the
loaded rule set, and the library manifest's rules_sha256.

3.0.0 removed ``contexts:`` and the ``contract:`` wrapper; no hash may move.
The fixture was generated on the v2.10.5 tag and is compared, value for value,
against the working tree by tests/test_v3_golden_hashes.py.

Usage:
    python scripts/golden_hashes.py [ROOT] > tests/fixtures/golden_hashes_v2_10_5.json
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

FIXED_NODE = "golden-node"
FIXED_TS = "2026-01-01T00:00:00+00:00"


def compute(root: Path) -> dict:
    root = root.resolve()
    sys.path.insert(0, str(root))
    tmp = Path(tempfile.mkdtemp())
    try:
        contracts = tmp / "contracts"
        shutil.copytree(root / "opendqv" / "contracts", contracts)
        os.environ["OPENDQV_CONTRACTS_DIR"] = str(contracts)
        os.environ["OPENDQV_DB_PATH"] = str(tmp / "golden.db")
        os.environ["OPENDQV_NODE_ID"] = FIXED_NODE
        import opendqv.config as config
        config.OPENDQV_NODE_ID = FIXED_NODE
        import opendqv.core.contracts as cm
        assert Path(cm.__file__).resolve().is_relative_to(root), cm.__file__

        class _FrozenDatetime(cm.datetime):
            @classmethod
            def now(cls, tz=None):
                return cm.datetime.fromisoformat(FIXED_TS)

        cm.datetime = _FrozenDatetime
        registry = cm.ContractRegistry(contracts)
        assert not getattr(registry, "load_failures", []), registry.load_failures

        spec = importlib.util.spec_from_file_location("_lm", root / "scripts" / "library_manifest.py")
        lm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lm)
        lm.CONTRACTS_DIR = contracts
        manifest = {e["name"]: e["rules_sha256"] for e in lm.build_manifest()["contracts"]}

        conn = sqlite3.connect(str(tmp / "golden.db"))
        out = {}
        for name in sorted(registry._contracts):
            for version, contract in sorted(registry._contracts[name].items()):
                row = conn.execute(
                    "SELECT content_hash, entry_hash FROM contract_history "
                    "WHERE contract_name = ? AND version = ? ORDER BY id DESC LIMIT 1",
                    (name, version),
                ).fetchone()
                out[f"{name}@{version}"] = {
                    "content_hash": row[0],
                    "entry_hash": row[1],
                    "effective_rule_hash": cm._compute_effective_rule_hash(contract.rules),
                    "rules_sha256": manifest[name],
                }
        conn.close()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    print(json.dumps(compute(root), indent=2, sort_keys=True))
