#!/usr/bin/env python3
"""Replay a previous release's conformance corpus against the CURRENT engine and
report every verdict flip (#146).

The corpus proves the two engines agree; it cannot detect that a library or
engine change altered user-facing verdicts, because both sides are swept in
lockstep. This script is the breaking-change detector: it reads the fixture
files as they were at a git ref (default: the latest v* tag), validates every
record with the engine and bundled contracts on the working tree, and diffs the
verdict (and error/warning code sets) against what the old corpus expected.

Usage:
    python scripts/replay_previous_corpus.py            # vs latest tag
    python scripts/replay_previous_corpus.py v2.5.1     # vs a specific ref
    python scripts/replay_previous_corpus.py --json     # machine-readable

Exit status: 0 when no CLEAN row (a record the previous release accepted) is
now rejected; 1 otherwise. Other flips are reported but do not fail — they may
be deliberate (and must then appear in the CHANGELOG).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = "tests/fixtures/conformance"
CONTRACTS = ROOT / "opendqv" / "contracts"


def latest_tag() -> str | None:
    """Latest v* tag, or None when the clone carries no tags (shallow CI checkout)."""
    out = subprocess.run(["git", "tag", "--sort=-v:refname"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    tags = [t for t in out if t.startswith("v")]
    return tags[0] if tags else None


def files_at(ref: str) -> list[str]:
    """Corpus files at ``ref``: the generated per-contract files plus the frozen
    minimal_clean rows (kept in frozen/ so the generator's glob never sees them)."""
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, f"{FIXTURE_DIR}/"], cwd=ROOT, capture_output=True, text=True)
    return [p for p in out.stdout.split() if p.endswith(".jsonl")]


def read_at(ref: str, path: str) -> list[dict]:
    out = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _contract(name: str):
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule
    from opendqv.core.validator import strict_schema_kwargs
    raw = yaml.safe_load((CONTRACTS / f"{name}.yaml").read_text(encoding="utf-8"))["contract"]
    rules = [Rule(**r) for r in raw.get("rules", [])]
    dc = DataContract(name=raw["name"], rules=rules, strict_schema=bool(raw.get("strict_schema")),
                      allowed_fields=raw.get("allowed_fields") or [])
    return rules, strict_schema_kwargs(dc, rules)


def replay(ref: str) -> dict:
    from opendqv.core.validator import validate_record
    flips: list[dict] = []
    rows = 0
    cache: dict = {}
    for path in files_at(ref):
        stem = Path(path).stem
        for line in read_at(ref, path):
            if "record" not in line or "rules" in line:
                # not a contract-bound record row: the claims fixture (base +
                # patch) and the self-contained engine_semantics rows (inline
                # rules) each have their own test and are not replay baselines
                continue
            name = line.get("contract") or stem
            if not (CONTRACTS / f"{name}.yaml").exists():
                flips.append({"contract": name, "kind": line.get("kind", "?"), "change": "contract removed"})
                continue
            if name not in cache:
                cache[name] = _contract(name)
            rules, kw = cache[name]
            rec = line["record"]
            if "expect" in line:
                old_valid = line["expect"]["valid"]
                old_codes = sorted({e.get("code") or e.get("error_code") for e in line["expect"].get("errors", [])})
            else:                       # minimal_clean rows: always expected valid
                old_valid, old_codes = True, []
            out = validate_record(rec, rules, name, **kw)
            new_codes = sorted({e["error_code"] for e in out["errors"]})
            rows += 1
            if out["valid"] != old_valid or (not out["valid"] and new_codes != old_codes):
                flips.append({"contract": name, "kind": line.get("kind", "minimal_clean"),
                              "was_valid": old_valid, "now_valid": out["valid"],
                              "old_codes": old_codes, "new_codes": new_codes, "record": rec})
    # Deliberate breaks: a flip is accepted only when its contract is listed in
    # frozen/accepted_breaks.json AND every error code the record now trips is
    # one the entry names (`new_codes_allowed`) — a regression in the same
    # contract that trips any other rule still gates. Accepted flips are
    # reported, not swallowed. The file is meant to be emptied at the next
    # release (the replay baseline moves forward), never to accumulate.
    accepted = load_accepted_breaks()
    for f in flips:
        entry = accepted.get(f["contract"])
        if entry and set(f.get("new_codes") or []) <= set(entry["new_codes_allowed"]):
            f["accepted_break"] = entry
    accepted_now_rejected = [f for f in flips if f.get("was_valid") and not f.get("now_valid") and "accepted_break" not in f]
    return {"ref": ref, "rows": rows, "flips": flips, "accepted_now_rejected": accepted_now_rejected}


ACCEPTED_BREAKS = ROOT / "tests" / "fixtures" / "conformance" / "frozen" / "accepted_breaks.json"


def load_accepted_breaks() -> dict[str, dict]:
    """{contract: {"changelog": "x.y.z", "reason": "...", "new_codes_allowed": [...]}}
    — deliberate verdict changes the release notes own, scoped to the exact
    error codes the change introduces. Absent file = nothing accepted."""
    if not ACCEPTED_BREAKS.exists():
        return {}
    data = json.loads(ACCEPTED_BREAKS.read_text(encoding="utf-8"))
    out = {}
    for item in data:
        if not item.get("contract") or not item.get("changelog") or not item.get("reason") or not item.get("new_codes_allowed"):
            raise SystemExit("accepted_breaks.json: every entry needs contract, changelog, reason and a non-empty new_codes_allowed list")
        out[item["contract"]] = item
    return out


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]
    ref = args[0] if args else latest_tag()
    if ref is None:
        raise SystemExit("no v* tag found — run `git fetch --tags` or pass a ref")
    report = replay(ref)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"replayed {report['rows']} rows from {ref} against the working tree")
        for f in report["flips"]:
            tag = f"  (accepted break, CHANGELOG {f['accepted_break']['changelog']})" if f.get("accepted_break") else ""
            print(f"  FLIP {f['contract']} [{f['kind']}]: valid {f.get('was_valid')} -> {f.get('now_valid')} "
                  f"codes {f.get('old_codes')} -> {f.get('new_codes')}{tag}")
        n = len(report["accepted_now_rejected"])
        print(f"{len(report['flips'])} flip(s); {n} record(s) the previous release accepted are now rejected")
    return 1 if report["accepted_now_rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
