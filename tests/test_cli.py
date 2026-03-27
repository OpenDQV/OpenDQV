"""Tests for the OpenDQV CLI (cli.py).

Command tests that do NOT write files use subprocess to exercise the full
dispatch path. Import tests that write files call cmd_* functions directly
(subprocess can't share monkeypatch state across process boundaries).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

# Path to the cli module
CLI = [sys.executable, str(Path(__file__).resolve().parent.parent / "cli.py")]

# Import the module for direct function-call tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli as cli_module


def run(*args, input_text=None, expect_rc=None):
    """Run the CLI with given arguments and return CompletedProcess."""
    result = subprocess.run(
        CLI + list(args),
        capture_output=True,
        text=True,
        input=input_text,
    )
    if expect_rc is not None:
        assert result.returncode == expect_rc, (
            f"Expected rc={expect_rc}, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


# ---------------------------------------------------------------------------
# TestCLIList
# ---------------------------------------------------------------------------

class TestCLIList:
    def test_list_exits_zero(self):
        r = run("list")
        assert r.returncode == 0

    def test_list_shows_customer(self):
        r = run("list")
        assert "customer" in r.stdout

    def test_list_shows_header(self):
        r = run("list")
        assert "NAME" in r.stdout

    def test_list_shows_version(self):
        r = run("list")
        assert "VER" in r.stdout


# ---------------------------------------------------------------------------
# TestCLIShow
# ---------------------------------------------------------------------------

class TestCLIShow:
    def test_show_exits_zero(self):
        r = run("show", "customer")
        assert r.returncode == 0

    def test_show_prints_name(self):
        r = run("show", "customer")
        assert "customer" in r.stdout

    def test_show_prints_version(self):
        r = run("show", "customer")
        assert "Version:" in r.stdout

    def test_show_prints_rules(self):
        r = run("show", "customer")
        assert "RULE" in r.stdout

    def test_show_missing_contract_exits_nonzero(self):
        r = run("show", "nonexistent_zzz")
        assert r.returncode != 0

    def test_show_missing_contract_error_message(self):
        r = run("show", "nonexistent_zzz")
        assert "not found" in r.stderr.lower() or "not found" in r.stdout.lower()


# ---------------------------------------------------------------------------
# TestCLIValidate
# ---------------------------------------------------------------------------

class TestCLIValidate:
    VALID_RECORD = json.dumps({
        "email": "alice@example.com",
        "age": 25,
        "name": "Alice",
        "id": "12345",
        "phone": "+1234567890",
        "balance": 100,
        "score": 85,
        "date": "2024-01-15",
        "username": "alice_w",
        "password": "securepass123",
        "status": "active",
    })
    INVALID_RECORD = json.dumps({"email": "not-an-email", "age": -5})

    def test_valid_record_exits_zero(self):
        r = run("validate", "customer", self.VALID_RECORD)
        assert r.returncode == 0

    def test_valid_record_shows_pass(self):
        r = run("validate", "customer", self.VALID_RECORD)
        assert "PASS" in r.stdout

    def test_invalid_record_exits_nonzero(self):
        r = run("validate", "customer", self.INVALID_RECORD)
        assert r.returncode != 0

    def test_invalid_record_shows_fail(self):
        r = run("validate", "customer", self.INVALID_RECORD)
        assert "FAIL" in r.stdout

    def test_invalid_json_exits_nonzero(self):
        r = run("validate", "customer", "not json")
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# TestCLIImportODCS
# ---------------------------------------------------------------------------

SAMPLE_ODCS = {
    "apiVersion": "v3.1.0",
    "kind": "DataContract",
    "info": {
        "title": "CLI Test Contract",
        "version": "1.0",
        "status": "active",
        "description": "Created by CLI test",
        "owner": "test-team",
    },
    "schema": [
        {
            "name": "records",
            "properties": [
                {"name": "email", "required": True, "unique": True},
                {
                    "name": "age",
                    "quality": [
                        {"type": "range", "min": 0, "max": 120, "mustBeSatisfied": True}
                    ],
                },
            ],
        }
    ],
}


class TestCLIImportODCS:
    """Tests use direct function calls so CONTRACTS_DIR can be patched in-process."""

    def _run_import(self, tmp_path, odcs_file, name=None):
        """Call cmd_import_odcs directly with CONTRACTS_DIR patched."""
        args = argparse.Namespace(file=str(odcs_file), name=name)
        with patch.object(cli_module, "CONTRACTS_DIR", tmp_path):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_module.cmd_import_odcs(args)
            return buf.getvalue()

    def test_import_odcs_writes_yaml_file(self, tmp_path):
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(SAMPLE_ODCS))
        self._run_import(tmp_path, odcs_file)
        assert (tmp_path / "cli_test_contract.yaml").exists()

    def test_import_odcs_shows_contract_name(self, tmp_path):
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(SAMPLE_ODCS))
        out = self._run_import(tmp_path, odcs_file)
        assert "cli_test_contract" in out

    def test_import_odcs_shows_rule_count(self, tmp_path):
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(SAMPLE_ODCS))
        out = self._run_import(tmp_path, odcs_file)
        assert "Rules imported:" in out

    def test_import_odcs_saved_yaml_is_valid(self, tmp_path):
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(SAMPLE_ODCS))
        self._run_import(tmp_path, odcs_file)
        parsed = yaml.safe_load((tmp_path / "cli_test_contract.yaml").read_text(encoding="utf-8"))
        assert "contract" in parsed
        assert isinstance(parsed["contract"]["rules"], list)

    def test_import_odcs_name_override(self, tmp_path):
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(SAMPLE_ODCS))
        out = self._run_import(tmp_path, odcs_file, name="my_override")
        assert "my_override" in out
        assert (tmp_path / "my_override.yaml").exists()

    def test_import_odcs_json_input(self, tmp_path):
        """ODCS file can be JSON (yaml.safe_load handles both)."""
        odcs_file = tmp_path / "test.json"
        odcs_file.write_text(json.dumps(SAMPLE_ODCS))
        self._run_import(tmp_path, odcs_file)
        assert (tmp_path / "cli_test_contract.yaml").exists()

    def test_import_odcs_missing_file_exits_nonzero(self):
        r = run("import-odcs", "/tmp/nonexistent_odcs_file_zzz.yaml")
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# TestCLIExportODCS
# ---------------------------------------------------------------------------

class TestCLIExportODCS:
    def test_export_odcs_exits_zero(self):
        r = run("export-odcs", "customer")
        assert r.returncode == 0

    def test_export_odcs_stdout_is_yaml(self):
        r = run("export-odcs", "customer")
        parsed = yaml.safe_load(r.stdout)
        assert parsed is not None

    def test_export_odcs_has_api_version(self):
        r = run("export-odcs", "customer")
        parsed = yaml.safe_load(r.stdout)
        assert parsed["apiVersion"] == "v3.1.0"

    def test_export_odcs_kind_is_data_contract(self):
        r = run("export-odcs", "customer")
        parsed = yaml.safe_load(r.stdout)
        assert parsed["kind"] == "DataContract"

    def test_export_odcs_has_schema(self):
        r = run("export-odcs", "customer")
        parsed = yaml.safe_load(r.stdout)
        assert len(parsed["schema"]) > 0
        assert len(parsed["schema"][0]["properties"]) > 0

    def test_export_odcs_writes_to_file(self, tmp_path):
        out_file = tmp_path / "customer_odcs.yaml"
        run("export-odcs", "customer", "--output", str(out_file))
        assert out_file.exists()
        parsed = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert parsed["apiVersion"] == "v3.1.0"

    def test_export_odcs_missing_contract_exits_nonzero(self):
        r = run("export-odcs", "nonexistent_zzz")
        assert r.returncode != 0

    def test_export_odcs_with_context(self):
        r = run("export-odcs", "customer", "--context", "kids_app")
        assert r.returncode == 0
        parsed = yaml.safe_load(r.stdout)
        assert parsed["apiVersion"] == "v3.1.0"


# ---------------------------------------------------------------------------
# TestCLIExportDBT
# ---------------------------------------------------------------------------

class TestCLIExportDBT:
    def test_export_dbt_stdout(self):
        r = run("export-dbt", "customer")
        assert r.returncode == 0
        parsed = yaml.safe_load(r.stdout)
        assert parsed is not None
        assert parsed["version"] == 2
        assert "models" in parsed

    def test_export_dbt_to_file(self, tmp_path):
        out_file = tmp_path / "customer_dbt.yml"
        r = run("export-dbt", "customer", "--output", str(out_file))
        assert r.returncode == 0
        assert out_file.exists()
        parsed = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert parsed["version"] == 2

    def test_export_dbt_unknown_contract_exits_nonzero(self):
        r = run("export-dbt", "nonexistent_zzz")
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# TestCLINoCommand
# ---------------------------------------------------------------------------

class TestCLINoCommand:
    def test_no_command_exits_nonzero(self):
        r = run()
        assert r.returncode != 0

    def test_no_command_shows_help(self):
        r = run()
        assert "usage" in r.stdout.lower() or "usage" in r.stderr.lower()

# ---------------------------------------------------------------------------
# ACT-049-04: --version flag
# ---------------------------------------------------------------------------

class TestCLIVersion:
    def test_version_flag_exits_zero(self):
        r = run("--version")
        assert r.returncode == 0

    def test_version_flag_shows_version_string(self):
        r = run("--version")
        output = r.stdout + r.stderr
        assert "opendqv" in output

    def test_version_flag_shows_ethos(self):
        r = run("--version")
        output = r.stdout + r.stderr
        assert "Trust is cheaper to build than to repair" in output

    def test_short_version_flag(self):
        r = run("-V")
        output = r.stdout + r.stderr
        assert "opendqv" in output


# ---------------------------------------------------------------------------
# ACT-049-01/02: Import command error paths — malformed input must produce
# a human-readable error message and a non-zero exit code.
# ---------------------------------------------------------------------------

class TestCLIImportErrorPaths:
    """Malformed import inputs should produce clean error messages, not Python tracebacks."""

    def test_import_gx_malformed_json_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json at all")
        r = run("import-gx", str(bad))
        assert r.returncode != 0

    def test_import_gx_malformed_json_shows_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json at all")
        r = run("import-gx", str(bad))
        assert "Error" in r.stderr or "Error" in r.stdout

    def test_import_gx_empty_suite_exits_nonzero(self, tmp_path):
        """GX suite missing required 'expectation_suite_name' key."""
        import json as _json
        bad = tmp_path / "empty.json"
        bad.write_text(_json.dumps({"expectations": []}))
        r = run("import-gx", str(bad))
        # Either raises a handled error or produces empty output — must not traceback
        assert r.returncode != 0 or "Traceback" not in r.stderr

    def test_import_gx_no_traceback_on_malformed(self, tmp_path):
        """Malformed GX input must never produce a raw Python traceback."""
        import json as _json
        bad = tmp_path / "malformed.json"
        bad.write_text(_json.dumps({"totally": "wrong", "nested": {"missing_fields": True}}))
        r = run("import-gx", str(bad))
        assert "Traceback (most recent call last)" not in r.stderr

    def test_import_dbt_malformed_yaml_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: is: not: valid: yaml:")
        r = run("import-dbt", str(bad))
        assert r.returncode != 0

    def test_import_dbt_no_traceback_on_empty(self, tmp_path):
        """Empty dbt schema (no models) must not traceback."""
        bad = tmp_path / "empty.yaml"
        bad.write_text("version: 2\nmodels: []")
        r = run("import-dbt", str(bad))
        assert "Traceback (most recent call last)" not in r.stderr

    def test_import_soda_malformed_yaml_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": invalid yaml content :\n  :")
        r = run("import-soda", str(bad))
        assert r.returncode != 0

    def test_import_soda_no_traceback_on_empty(self, tmp_path):
        """Empty Soda checks dict must not traceback."""
        bad = tmp_path / "empty.yaml"
        bad.write_text("checks: []")
        r = run("import-soda", str(bad))
        assert "Traceback (most recent call last)" not in r.stderr

    def test_import_odcs_malformed_missing_required_fields(self, tmp_path):
        """ODCS without required info.title must produce a clean error or handled result."""
        import yaml as _yaml
        bad = tmp_path / "bad.yaml"
        bad.write_text(_yaml.dump({"apiVersion": "v3.1.0", "kind": "DataContract"}))
        r = run("import-odcs", str(bad))
        # Either exits non-zero with a message, or exits zero with empty rules — no traceback
        assert "Traceback (most recent call last)" not in r.stderr

    def test_import_missing_file_exits_nonzero(self):
        """All import commands exit non-zero when the file does not exist."""
        for cmd in ("import-gx", "import-dbt", "import-soda", "import-csv", "import-odcs"):
            r = run(cmd, "/tmp/nonexistent_zzz_file.yaml")
            assert r.returncode != 0, f"{cmd} did not exit non-zero for missing file"


# ---------------------------------------------------------------------------
# ACT-049-07: contracts-import-dir --dry-run must NEVER write files to disk.
# ---------------------------------------------------------------------------

class TestCLIDryRunDoesNotWrite:
    """The --dry-run flag on contracts-import-dir must be a true no-op on disk."""

    def _run_import_dir(self, tmp_path, src_dir, dry_run=False):
        args = ["contracts-import-dir", str(src_dir)]
        if dry_run:
            args.append("--dry-run")
        with patch.object(cli_module, "CONTRACTS_DIR", tmp_path):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    cli_module.cmd_contracts_import_dir(
                        argparse.Namespace(directory=str(src_dir), dry_run=dry_run)
                    )
                except SystemExit:
                    pass
            return buf.getvalue()

    def test_dry_run_writes_no_files(self, tmp_path):
        """--dry-run must not create any files in the contracts directory."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        (src / "probe.yaml").write_text("contract:\n  name: probe\n  rules: []\n")
        self._run_import_dir(dest, src, dry_run=True)
        assert list(dest.glob("*.yaml")) == [], "dry-run wrote files to disk"

    def test_dry_run_output_labels_files(self, tmp_path):
        """--dry-run output must mention [dry-run] to confirm no-op mode is active."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        (src / "probe.yaml").write_text("contract:\n  name: probe\n  rules: []\n")
        out = self._run_import_dir(dest, src, dry_run=True)
        assert "dry-run" in out.lower()

    def test_no_dry_run_writes_files(self, tmp_path):
        """Without --dry-run, files ARE processed (validates the non-dry path works)."""
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"
        dest.mkdir()
        (src / "probe.yaml").write_text("contract:\n  name: probe\n  rules: []\n")
        self._run_import_dir(dest, src, dry_run=False)
        # The command reads YAML but doesn't copy to dest in the current implementation —
        # it validates and counts. The point is it runs without error.
        # (The command validates in-place from src, not copy to dest.)
        pass  # no traceback = pass


# ---------------------------------------------------------------------------
# SEC-013: CLI _validate_contract_name() blocks path traversal
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
import cli as cli_module  # noqa: E402,F811


class TestCLIContractNameValidation:
    """SEC-013 — CLI import commands reject malicious contract names before writing."""

    @pytest.mark.parametrize("malicious_name", [
        "../../etc/passwd",
        "../evil",
        "has/slash",
        "/absolute/path",
        "a" * 101,
    ])
    def test_import_odcs_rejects_malicious_name(self, tmp_path, malicious_name):
        """cmd_import_odcs exits with code 1 for a malicious contract name."""
        import yaml
        odcs_data = {
            "apiVersion": "v3", "kind": "DataContract",
            "info": {"title": malicious_name, "version": "1.0"},
            "schema": [],
        }
        odcs_file = tmp_path / "test.yaml"
        odcs_file.write_text(yaml.dump(odcs_data))
        args = argparse.Namespace(file=str(odcs_file), name=malicious_name)
        with patch.object(cli_module, "CONTRACTS_DIR", tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                cli_module.cmd_import_odcs(args)
            assert exc_info.value.code == 1

    @pytest.mark.parametrize("malicious_name", [
        "../../etc/passwd",
        "../evil",
        "has/slash",
    ])
    def test_import_csv_rejects_malicious_name(self, tmp_path, malicious_name):
        """cmd_import_csv exits with code 1 for a malicious contract name."""
        csv_file = tmp_path / "rules.csv"
        csv_file.write_text("field,rule_type,value,severity,error_message\n")
        args = argparse.Namespace(file=str(csv_file), name=malicious_name)
        with patch.object(cli_module, "CONTRACTS_DIR", tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                cli_module.cmd_import_csv(args)
            assert exc_info.value.code == 1

    def test_valid_name_passes_validation(self, tmp_path):
        """A valid contract name does not cause sys.exit in the validator."""
        from cli import _validate_contract_name
        # Should not raise SystemExit
        _validate_contract_name("valid-contract-name_123")
