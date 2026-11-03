"""scripts/check_scanner_ignores.py — a scanner ignore entry that no longer
matches a finding fails CI (2026-11-03: ten of eleven entries had outlived
their reason; one new pip-vendored CVE then broke the container scan)."""
import importlib.util
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_scanner_ignores", REPO / "scripts" / "check_scanner_ignores.py")
csi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csi)

GRYPE_CONFIG = """# header comment
ignore:
  # a reason
  # - vulnerability: CVE-0000-0001   (commented out, not an entry)
  - vulnerability: CVE-2026-0001
    reason: "x"
  - vulnerability: "GHSA-aaaa-bbbb-cccc"
    reason: "quoted"
"""

TRIVYIGNORE = """# comment
CVE-2025-47273

GHSA-6v7p-g79w-8964  # trailing comment
"""


def _grype_report(*ids, related=()):
    return {"matches": [
        {"vulnerability": {"id": i}, "relatedVulnerabilities": [{"id": r} for r in related]}
        for i in ids
    ]}


def _trivy_report(*ids):
    return {"Results": [
        {"Target": "Python", "Vulnerabilities": [{"VulnerabilityID": i} for i in ids]},
        {"Target": "debian", "Vulnerabilities": None},
    ]}


def test_grype_parser_reads_entries_not_comments():
    assert csi.grype_ignored_ids(GRYPE_CONFIG) == {"CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"}


def test_grype_parser_agrees_with_yaml_on_the_committed_config():
    text = (REPO / ".grype.yaml").read_text(encoding="utf-8")
    via_yaml = {e["vulnerability"] for e in (yaml.safe_load(text) or {}).get("ignore") or []}
    assert csi.grype_ignored_ids(text) == via_yaml


def test_grype_parser_agrees_with_yaml_on_a_populated_config():
    via_yaml = {e["vulnerability"] for e in yaml.safe_load(GRYPE_CONFIG)["ignore"]}
    assert csi.grype_ignored_ids(GRYPE_CONFIG) == via_yaml


def test_trivy_parser_skips_comments_and_blanks():
    assert csi.trivy_ignored_ids(TRIVYIGNORE) == {"CVE-2025-47273", "GHSA-6v7p-g79w-8964"}


def test_committed_trivyignore_holds_only_vulnerability_ids():
    ids = csi.trivy_ignored_ids((REPO / ".trivyignore").read_text(encoding="utf-8"))
    assert all(i.startswith(("CVE-", "GHSA-")) for i in ids), ids


def test_reported_ids_include_related_and_vendor_ids():
    assert csi.grype_reported_ids(_grype_report("GHSA-x", related=("CVE-1",))) == {"GHSA-x", "CVE-1"}
    trivy = {"Results": [{"Vulnerabilities": [{"VulnerabilityID": "CVE-2", "VendorIDs": ["DSA-9"]}]}]}
    assert csi.trivy_reported_ids(trivy) == {"CVE-2", "DSA-9"}


def _run(tmp_path, grype_cfg, trivy_cfg, grype_report, trivy_report):
    files = {
        "g.yaml": grype_cfg, "t.ignore": trivy_cfg,
        "g.json": json.dumps(grype_report), "t.json": json.dumps(trivy_report),
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return csi.main([
        "--grype", str(tmp_path / "g.json"), "--trivy", str(tmp_path / "t.json"),
        "--grype-config", str(tmp_path / "g.yaml"), "--trivyignore", str(tmp_path / "t.ignore"),
    ])


def test_every_entry_still_reported_passes(tmp_path, capsys):
    rc = _run(tmp_path, GRYPE_CONFIG, TRIVYIGNORE,
              _grype_report("CVE-2026-0001", related=("GHSA-aaaa-bbbb-cccc",)),
              _trivy_report("CVE-2025-47273", "GHSA-6v7p-g79w-8964"))
    assert rc == 0
    assert "STALE" not in capsys.readouterr().out


def test_an_entry_no_scanner_reports_fails_and_is_named(tmp_path, capsys):
    # The 2026-11-03 shape: pip 26.3 vendors a fixed msgpack, so the GHSA is
    # gone from the report while its ignore entry stays.
    rc = _run(tmp_path, GRYPE_CONFIG, TRIVYIGNORE,
              _grype_report("CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"),
              _trivy_report("CVE-2025-47273"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE t.ignore: GHSA-6v7p-g79w-8964" in out.replace(str(tmp_path) + "/", "")
    assert "CVE-2025-47273" not in out


def test_empty_ignore_files_pass(tmp_path):
    assert _run(tmp_path, "ignore: []\n", "# none\n", _grype_report(), _trivy_report()) == 0


def test_ci_runs_the_check_on_the_unfiltered_rescan():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/check_scanner_ignores.py" in ci
    step = ci[ci.index("Check scanner ignores are still needed"):]
    step = step[: step.index("\n  integration:")]
    assert "--ignore-unfixed" not in step and "--only-fixed" not in step and "--severity" not in step
    assert 'ignore: []' in step and "--ignorefile" in step
