#!/usr/bin/env python3
"""Fail when a container-scanner ignore entry no longer matches anything.

Every entry in `.grype.yaml` and `.trivyignore` was added for one finding and
carries a "re-evaluate when ..." note. Nothing used to check those notes, so
entries outlived their reason: pip 26.3 vendored fixed msgpack and urllib3,
and python-jose/ecdsa left the dependency tree, but four `.trivyignore`
entries stayed until a new CVE broke the scan (2026-11-03).

CI scans the image a second time with NO ignores and no severity or
fixed-only filter, then runs this script on the JSON reports. Any ignored ID
that neither scanner reports any more is stale and fails the job — delete it
(and, for pip-vendored packages, raise the Dockerfile `pip>=` floor).

Usage:
    check_scanner_ignores.py --grype grype.json --trivy trivy.json \
        [--grype-config .grype.yaml] [--trivyignore .trivyignore]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Stdlib only: CI runs this on the runner's system Python, before any install.
_GRYPE_VULN_RE = re.compile(r"""^\s*-?\s*vulnerability:\s*["']?([^"'\s#]+)""", re.MULTILINE)


def grype_ignored_ids(config_text: str) -> set[str]:
    return set(_GRYPE_VULN_RE.findall(config_text))


def trivy_ignored_ids(ignore_text: str) -> set[str]:
    ids = set()
    for line in ignore_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.add(line.split()[0])
    return ids


def grype_reported_ids(report: dict) -> set[str]:
    ids = set()
    for match in report.get("matches") or []:
        vuln = match.get("vulnerability") or {}
        if vuln.get("id"):
            ids.add(vuln["id"])
        for related in match.get("relatedVulnerabilities") or []:
            if related.get("id"):
                ids.add(related["id"])
    return ids


def trivy_reported_ids(report: dict) -> set[str]:
    ids = set()
    for result in report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            if vuln.get("VulnerabilityID"):
                ids.add(vuln["VulnerabilityID"])
            ids.update(vuln.get("VendorIDs") or [])
    return ids


def stale_entries(ignored: set[str], reported: set[str]) -> list[str]:
    return sorted(ignored - reported)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--grype", required=True, help="grype -o json report, scanned without ignores")
    parser.add_argument("--trivy", required=True, help="trivy --format json report, scanned without ignores")
    parser.add_argument("--grype-config", default=".grype.yaml")
    parser.add_argument("--trivyignore", default=".trivyignore")
    args = parser.parse_args(argv)

    grype_report = json.loads(Path(args.grype).read_text(encoding="utf-8"))
    trivy_report = json.loads(Path(args.trivy).read_text(encoding="utf-8"))
    grype_stale = stale_entries(
        grype_ignored_ids(Path(args.grype_config).read_text(encoding="utf-8")),
        grype_reported_ids(grype_report),
    )
    trivy_stale = stale_entries(
        trivy_ignored_ids(Path(args.trivyignore).read_text(encoding="utf-8")),
        trivy_reported_ids(trivy_report),
    )

    for name, stale in ((args.grype_config, grype_stale), (args.trivyignore, trivy_stale)):
        for vuln_id in stale:
            print(f"STALE {name}: {vuln_id} is no longer reported for this image — delete the entry")
    if grype_stale or trivy_stale:
        return 1
    print("Every scanner ignore entry still matches a finding in this image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
