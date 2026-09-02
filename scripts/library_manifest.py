#!/usr/bin/env python3
"""Starter-library manifest (docs/contract_conformance.md, D5).

Emits ``library_manifest.json``: for every bundled contract, its name, version,
rule count and a SHA-256 over the canonical JSON of its rules (sorted keys,
``None``/empty values dropped). A downstream engine that mirrors this library
pins the manifest it was synced from and diffs against a newer one to see
exactly which contracts changed — without diffing YAML by eye.

``tests/test_library_manifest.py`` regenerates this in CI and fails when the
committed file is stale, so every library change is a deliberate, visible
commit to the manifest.

Usage:  python scripts/library_manifest.py            # write library_manifest.json
        python scripts/library_manifest.py --check    # exit 1 if stale
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "opendqv" / "contracts"
MANIFEST_PATH = ROOT / "library_manifest.json"

_DROP = (None, "", [], {}, False)


def _canonical_rule(rule: dict) -> dict:
    return {k: v for k, v in sorted(rule.items()) if v not in _DROP}


def _rules_digest(rules: list[dict]) -> str:
    payload = json.dumps([_canonical_rule(r) for r in rules], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest() -> dict:
    entries = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        body = doc.get("contract", doc)
        rules = body.get("rules") or []
        entries.append({
            "file": path.name,
            "name": body.get("name"),
            "version": str(body.get("version", "")),
            "strict_schema": bool(body.get("strict_schema", False)),
            "rule_count": len(rules),
            "rules_sha256": _rules_digest(rules),
        })
    whole = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"manifest_version": 1, "contracts": entries, "library_sha256": whole}


def main(argv: list[str]) -> int:
    manifest = build_manifest()
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if "--check" in argv:
        current = MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else ""
        if current != rendered:
            sys.stderr.write("library_manifest.json is stale — run: python scripts/library_manifest.py\n")
            return 1
        print("library_manifest.json up to date")
        return 0
    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {MANIFEST_PATH.relative_to(ROOT)} ({len(manifest['contracts'])} contracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
