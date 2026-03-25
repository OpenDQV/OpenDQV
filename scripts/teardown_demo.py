#!/usr/bin/env python3
"""
Demo teardown — deletes all quality_stats records tagged context='demo' and
re-runs push_quality_lineage.py so Marmot reflects the clean state.

Run this at home after a customer demo:
    python scripts/teardown_demo.py

Environment variables (same as the demo scripts):
    OPENDQV_URL    — default: http://localhost:8000
    OPENDQV_TOKEN  — admin token (required: DELETE /quality/stats requires admin role)

Optional flags:
    --dry-run      Print what would be deleted without making any changes
    --skip-marmot  Delete quality_stats rows but skip the Marmot lineage sync
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000").rstrip("/")
TOKEN    = os.environ.get("OPENDQV_TOKEN", "")
_SCRIPT_DIR = Path(__file__).parent


def _delete_demo_stats(dry_run: bool) -> int:
    """Delete quality_stats rows tagged context='demo'. Returns count deleted."""
    url = f"{BASE_URL}/api/v1/quality/stats?context=demo"
    if dry_run:
        print(f"[dry-run] Would DELETE {url}")
        return 0

    req = urllib.request.Request(url, method="DELETE")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode())
            return body.get("deleted", 0)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"ERROR: DELETE /quality/stats returned HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"ERROR: Could not reach {BASE_URL} — {exc.reason}", file=sys.stderr)
        sys.exit(1)


def _sync_marmot(dry_run: bool) -> None:
    """Re-run push_quality_lineage.py so Marmot reflects the now-clean quality_stats."""
    script = _SCRIPT_DIR / "push_quality_lineage.py"
    if not script.exists():
        print("WARNING: push_quality_lineage.py not found — skipping Marmot sync.", file=sys.stderr)
        return

    if dry_run:
        print(f"[dry-run] Would run: python {script}")
        return

    print("Syncing Marmot lineage...")
    env = {**os.environ, "OPENDQV_URL": BASE_URL}
    if TOKEN:
        env["OPENDQV_TOKEN"] = TOKEN

    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: push_quality_lineage.py exited {result.returncode}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tear down demo data: delete context='demo' quality stats and sync Marmot"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without making any changes",
    )
    parser.add_argument(
        "--skip-marmot",
        action="store_true",
        help="Delete quality_stats rows but skip the Marmot lineage sync",
    )
    args = parser.parse_args()

    if not TOKEN and not args.dry_run:
        print("WARNING: OPENDQV_TOKEN not set — request may fail if auth is required.",
              file=sys.stderr)

    print(f"Deleting demo quality_stats from {BASE_URL}...")
    deleted = _delete_demo_stats(args.dry_run)
    if not args.dry_run:
        print(f"  Deleted {deleted} row(s) with context='demo'.")

    if not args.skip_marmot:
        _sync_marmot(args.dry_run)
        if not args.dry_run:
            print("  Marmot lineage sync complete.")

    print("Teardown done.")


if __name__ == "__main__":
    main()
