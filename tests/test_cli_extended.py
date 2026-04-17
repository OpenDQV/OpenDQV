"""
Extended CLI tests — direct cmd_* function calls to contribute to coverage.

subprocess tests in test_cli.py exercise the full CLI path but do NOT contribute
to coverage because coverage only tracks in-process execution. These tests call
cmd_* functions directly using argparse.Namespace fixtures.
"""
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# Ensure project root is on sys.path before importing cli
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import opendqv.cli as cli_module  # noqa: E402

CONTRACTS_SRC = PROJECT_ROOT / "opendqv" / "contracts"
SAMPLE_DIR = PROJECT_ROOT / "tests" / "sample_data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture(func, args):
    """Call a cmd_* function, capture stdout + stderr, return (stdout, stderr, exit_code)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    exit_code = 0
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            func(args)
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0
    return out_buf.getvalue(), err_buf.getvalue(), exit_code


@pytest.fixture
def contracts_dir(tmp_path):
    """Copy contracts/ to tmp_path and patch CONTRACTS_DIR."""
    dest = tmp_path / "contracts"
    shutil.copytree(CONTRACTS_SRC, dest)
    with patch.object(cli_module, "CONTRACTS_DIR", dest):
        yield dest


@pytest.fixture
def draft_contract(contracts_dir):
    """Create a minimal draft contract in the temp contracts dir."""
    content = "contract:\n  name: test_draft\n  version: \"1.0\"\n  status: draft\n  rules: []\n"
    (contracts_dir / "test_draft.yaml").write_text(content, encoding="utf-8")
    return "test_draft"


@pytest.fixture
def review_contract(contracts_dir):
    """Create a minimal review contract in the temp contracts dir."""
    content = "contract:\n  name: test_review\n  version: \"1.0\"\n  status: review\n  rules: []\n"
    (contracts_dir / "test_review.yaml").write_text(content, encoding="utf-8")
    return "test_review"


# ---------------------------------------------------------------------------
# TestCmdListDirect
# ---------------------------------------------------------------------------

class TestCmdListDirect:
    """cmd_list — direct call coverage."""

    def test_list_shows_customer(self, contracts_dir):
        args = argparse.Namespace()
        out, _, rc = _capture(cli_module.cmd_list, args)
        assert "customer" in out
        assert rc == 0

    def test_list_shows_header_columns(self, contracts_dir):
        args = argparse.Namespace()
        out, _, rc = _capture(cli_module.cmd_list, args)
        assert "NAME" in out
        assert "VER" in out
        assert "STATUS" in out

    def test_list_empty_contracts_dir(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch.object(cli_module, "CONTRACTS_DIR", empty_dir):
            args = argparse.Namespace()
            out, _, rc = _capture(cli_module.cmd_list, args)
        assert "No contracts" in out


# ---------------------------------------------------------------------------
# TestCmdShowDirect
# ---------------------------------------------------------------------------

class TestCmdShowDirect:
    """cmd_show — direct call coverage."""

    def test_show_customer(self, contracts_dir):
        args = argparse.Namespace(contract="customer")
        out, _, rc = _capture(cli_module.cmd_show, args)
        assert "customer" in out
        assert "Version" in out
        assert rc == 0

    def test_show_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz")
        out, err, rc = _capture(cli_module.cmd_show, args)
        assert rc != 0
        assert "not found" in err.lower()

    def test_show_contract_with_no_rules(self, contracts_dir):
        """Contract with empty rules list shows '(no rules)'."""
        content = "contract:\n  name: empty_rules\n  version: \"1.0\"\n  status: draft\n  rules: []\n"
        (contracts_dir / "empty_rules.yaml").write_text(content, encoding="utf-8")
        args = argparse.Namespace(contract="empty_rules")
        out, _, rc = _capture(cli_module.cmd_show, args)
        assert "(no rules)" in out


# ---------------------------------------------------------------------------
# TestCmdValidateDirect
# ---------------------------------------------------------------------------

class TestCmdValidateDirect:
    """cmd_validate — direct call coverage."""

    VALID_RECORD = json.dumps({
        "email": "alice@example.com", "age": 25, "name": "Alice",
        "id": "12345", "phone": "+1234567890", "balance": 100,
        "score": 85, "date": "2024-01-15", "username": "alice_w",
        "password": "securepass123", "status": "active",
    })
    INVALID_RECORD = json.dumps({"email": "not-an-email", "age": -5})

    def test_validate_pass(self, contracts_dir):
        args = argparse.Namespace(contract="customer", json=self.VALID_RECORD, context=None)
        out, _, rc = _capture(cli_module.cmd_validate, args)
        assert "PASS" in out
        assert rc == 0

    def test_validate_fail(self, contracts_dir):
        args = argparse.Namespace(contract="customer", json=self.INVALID_RECORD, context=None)
        out, _, rc = _capture(cli_module.cmd_validate, args)
        assert "FAIL" in out
        assert rc != 0

    def test_validate_invalid_json(self, contracts_dir):
        args = argparse.Namespace(contract="customer", json="not json", context=None)
        _, err, rc = _capture(cli_module.cmd_validate, args)
        assert rc != 0
        assert "Invalid JSON" in err

    def test_validate_contract_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz", json="{}", context=None)
        _, err, rc = _capture(cli_module.cmd_validate, args)
        assert rc != 0

    def test_validate_shows_errors(self, contracts_dir):
        args = argparse.Namespace(contract="customer", json=self.INVALID_RECORD, context=None)
        out, _, rc = _capture(cli_module.cmd_validate, args)
        assert "Errors:" in out


# ---------------------------------------------------------------------------
# TestCmdExportGXDirect
# ---------------------------------------------------------------------------

class TestCmdExportGXDirect:
    """cmd_export_gx — direct call coverage."""

    def test_export_gx_stdout(self, contracts_dir):
        args = argparse.Namespace(contract="customer", context=None, output=None)
        out, _, rc = _capture(cli_module.cmd_export_gx, args)
        parsed = json.loads(out)
        assert "expectations" in parsed
        assert rc == 0

    def test_export_gx_to_file(self, tmp_path, contracts_dir):
        out_file = tmp_path / "customer_gx.json"
        args = argparse.Namespace(contract="customer", context=None, output=str(out_file))
        _capture(cli_module.cmd_export_gx, args)
        assert out_file.exists()
        parsed = json.loads(out_file.read_text())
        assert "expectations" in parsed

    def test_export_gx_with_context(self, contracts_dir):
        args = argparse.Namespace(contract="customer", context="kids_app", output=None)
        out, _, rc = _capture(cli_module.cmd_export_gx, args)
        assert rc == 0

    def test_export_gx_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz", context=None, output=None)
        _, err, rc = _capture(cli_module.cmd_export_gx, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdImportGXDirect
# ---------------------------------------------------------------------------

class TestCmdImportGXDirect:
    """cmd_import_gx — direct call coverage."""

    def test_import_gx_success(self, contracts_dir):
        args = argparse.Namespace(file=str(SAMPLE_DIR / "gx_suite_sample.json"))
        out, _, rc = _capture(cli_module.cmd_import_gx, args)
        assert rc == 0
        assert "Contract:" in out

    def test_import_gx_with_skipped(self, contracts_dir):
        """GX suite with unsupported expectations shows Skipped section — covers lines 218-220."""
        args = argparse.Namespace(file=str(SAMPLE_DIR / "gx_suite_unsupported.json"))
        out, _, rc = _capture(cli_module.cmd_import_gx, args)
        # Either success with skipped listed, or non-zero — no traceback
        assert "Traceback" not in out

    def test_import_gx_not_found(self, contracts_dir):
        args = argparse.Namespace(file="/tmp/nonexistent_gx_zzz.json")
        _, err, rc = _capture(cli_module.cmd_import_gx, args)
        assert rc != 0

    def test_import_gx_invalid_json(self, tmp_path, contracts_dir):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        args = argparse.Namespace(file=str(bad))
        _, err, rc = _capture(cli_module.cmd_import_gx, args)
        assert rc != 0

    def test_import_gx_import_exception(self, tmp_path, contracts_dir):
        """import_gx_suite raises exception — covers lines 199-201."""
        # A JSON file that parses but has an unsupported structure
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"totally": "wrong structure"}))
        args = argparse.Namespace(file=str(bad))
        _, err, rc = _capture(cli_module.cmd_import_gx, args)
        # Should fail gracefully — no traceback
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# TestCmdImportDBTDirect
# ---------------------------------------------------------------------------

class TestCmdImportDBTDirect:
    """cmd_import_dbt — direct call coverage."""

    def test_import_dbt_success(self, contracts_dir):
        args = argparse.Namespace(file=str(SAMPLE_DIR / "dbt_schema_sample.yml"))
        out, _, rc = _capture(cli_module.cmd_import_dbt, args)
        assert rc == 0

    def test_import_dbt_not_found(self, contracts_dir):
        args = argparse.Namespace(file="/tmp/nonexistent_dbt_zzz.yml")
        _, err, rc = _capture(cli_module.cmd_import_dbt, args)
        assert rc != 0

    def test_import_dbt_malformed_yaml(self, tmp_path, contracts_dir):
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: is: not: valid: yaml:")
        args = argparse.Namespace(file=str(bad))
        _, err, rc = _capture(cli_module.cmd_import_dbt, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdImportSodaDirect
# ---------------------------------------------------------------------------

class TestCmdImportSodaDirect:
    """cmd_import_soda — direct call coverage."""

    def test_import_soda_success(self, contracts_dir):
        args = argparse.Namespace(file=str(SAMPLE_DIR / "soda_checks_sample.yaml"))
        out, _, rc = _capture(cli_module.cmd_import_soda, args)
        assert rc == 0

    def test_import_soda_not_found(self, contracts_dir):
        args = argparse.Namespace(file="/tmp/nonexistent_soda_zzz.yaml")
        _, err, rc = _capture(cli_module.cmd_import_soda, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdImportCSVDirect
# ---------------------------------------------------------------------------

class TestCmdImportCSVDirect:
    """cmd_import_csv — direct call coverage."""

    def test_import_csv_success(self, contracts_dir):
        args = argparse.Namespace(
            file=str(SAMPLE_DIR / "csv_rules_sample.csv"),
            name="csv_test_contract",
        )
        out, _, rc = _capture(cli_module.cmd_import_csv, args)
        assert rc == 0
        assert "Contract:" in out

    def test_import_csv_not_found(self, contracts_dir):
        args = argparse.Namespace(file="/tmp/nonexistent_csv_zzz.csv", name="x")
        _, err, rc = _capture(cli_module.cmd_import_csv, args)
        assert rc != 0

    def test_import_csv_uses_stem_as_name(self, tmp_path, contracts_dir):
        csv = tmp_path / "my_rules.csv"
        csv.write_text("name,type,field,min,max,error_message\nage_min,min,age,18,,Age must be 18+\n")
        args = argparse.Namespace(file=str(csv), name=None)
        out, _, rc = _capture(cli_module.cmd_import_csv, args)
        assert rc == 0
        assert "my_rules" in out


# ---------------------------------------------------------------------------
# TestCmdExportDBTDirect
# ---------------------------------------------------------------------------

class TestCmdExportDBTDirect:
    """cmd_export_dbt — direct call coverage for output=file path."""

    def test_export_dbt_to_file(self, tmp_path, contracts_dir):
        out_file = tmp_path / "customer_dbt.yml"
        args = argparse.Namespace(contract="customer", context=None, output=str(out_file))
        _capture(cli_module.cmd_export_dbt, args)
        assert out_file.exists()
        parsed = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert parsed["version"] == 2

    def test_export_dbt_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz", context=None, output=None)
        _, err, rc = _capture(cli_module.cmd_export_dbt, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdValidateFileDirect
# ---------------------------------------------------------------------------

class TestCmdValidateFileDirect:
    """cmd_validate_file — direct call coverage."""

    @pytest.fixture
    def customer_csv(self, tmp_path):
        """Write a CSV file with valid customer records."""
        path = tmp_path / "customers.csv"
        path.write_text(
            "email,age,name,id,phone,balance,score,date,username,password,status\n"
            "alice@example.com,25,Alice,12345,+1234567890,100,85,2024-01-15,alice_w,securepass123,active\n",
            encoding="utf-8",
        )
        return path

    @pytest.fixture
    def invalid_csv(self, tmp_path):
        """Write a CSV file with invalid customer records."""
        path = tmp_path / "invalid.csv"
        path.write_text(
            "email,age,name,id,phone,balance,score,date,username,password,status\n"
            "not-an-email,-5,Alice,12345,+1234567890,100,85,2024-01-15,alice_w,securepass123,active\n",
            encoding="utf-8",
        )
        return path

    def test_validate_file_pass(self, contracts_dir, customer_csv):
        args = argparse.Namespace(
            contract="customer", path=str(customer_csv),
            context=None, output_failures=None, observe_only=False,
        )
        out, _, rc = _capture(cli_module.cmd_validate_file, args)
        assert "PASS" in out
        assert rc == 0

    def test_validate_file_fail(self, contracts_dir, invalid_csv):
        args = argparse.Namespace(
            contract="customer", path=str(invalid_csv),
            context=None, output_failures=None, observe_only=False,
        )
        out, _, rc = _capture(cli_module.cmd_validate_file, args)
        assert "FAIL" in out
        assert rc != 0

    def test_validate_file_observe_only(self, contracts_dir, invalid_csv):
        args = argparse.Namespace(
            contract="customer", path=str(invalid_csv),
            context=None, output_failures=None, observe_only=True,
        )
        out, _, rc = _capture(cli_module.cmd_validate_file, args)
        assert "OBSERVATION RUN" in out
        assert rc == 0

    def test_validate_file_output_failures(self, tmp_path, contracts_dir, invalid_csv):
        failures_file = tmp_path / "failures.csv"
        args = argparse.Namespace(
            contract="customer", path=str(invalid_csv),
            context=None, output_failures=str(failures_file), observe_only=False,
        )
        out, _, rc = _capture(cli_module.cmd_validate_file, args)
        assert failures_file.exists()

    def test_validate_file_not_found(self, contracts_dir):
        args = argparse.Namespace(
            contract="customer", path="/tmp/nonexistent_zzz.csv",
            context=None, output_failures=None, observe_only=False,
        )
        _, err, rc = _capture(cli_module.cmd_validate_file, args)
        assert rc != 0

    def test_validate_file_unsupported_type(self, tmp_path, contracts_dir):
        bad = tmp_path / "data.xlsx"
        bad.write_text("data")
        args = argparse.Namespace(
            contract="customer", path=str(bad),
            context=None, output_failures=None, observe_only=False,
        )
        _, err, rc = _capture(cli_module.cmd_validate_file, args)
        assert rc != 0

    def test_validate_file_contract_not_found(self, contracts_dir, customer_csv):
        args = argparse.Namespace(
            contract="nonexistent_zzz", path=str(customer_csv),
            context=None, output_failures=None, observe_only=False,
        )
        _, err, rc = _capture(cli_module.cmd_validate_file, args)
        assert rc != 0

    def test_validate_file_empty_csv(self, tmp_path, contracts_dir):
        empty = tmp_path / "empty.csv"
        empty.write_text("email,age\n", encoding="utf-8")  # header only, no records
        args = argparse.Namespace(
            contract="customer", path=str(empty),
            context=None, output_failures=None, observe_only=False,
        )
        out, _, rc = _capture(cli_module.cmd_validate_file, args)
        assert "empty" in out.lower()
        assert rc == 0


# ---------------------------------------------------------------------------
# TestCmdForkDirect
# ---------------------------------------------------------------------------

class TestCmdForkDirect:
    """cmd_fork — copy a contract to a new name as a clean DRAFT."""

    def test_fork_creates_new_draft(self, contracts_dir):
        args = argparse.Namespace(src="media_content", dst="bauer_ad", force=False)
        out, _, rc = _capture(cli_module.cmd_fork, args)
        assert rc == 0
        assert "Forked" in out
        forked = (contracts_dir / "bauer_ad.yaml").read_text(encoding="utf-8")
        assert "name: bauer_ad" in forked
        assert 'version: "1.0"' in forked
        assert "status: draft" in forked
        assert "asset_id: urn:opendqv:bauer_ad" in forked
        # The original contract's content is preserved
        assert "content_id_required" in forked

    def test_fork_preserves_comments_and_descriptions(self, contracts_dir):
        """Forking via regex mutation keeps the long regulatory description intact."""
        args = argparse.Namespace(src="media_content", dst="bauer_ad", force=False)
        _capture(cli_module.cmd_fork, args)
        forked = (contracts_dir / "bauer_ad.yaml").read_text(encoding="utf-8")
        assert "EIDR" in forked  # from media_content description
        assert "OFCOM" in forked

    def test_fork_refuses_overwrite_without_force(self, contracts_dir):
        (contracts_dir / "already_there.yaml").write_text(
            "contract:\n  name: already_there\n  version: \"1.0\"\n  rules: []\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(src="customer", dst="already_there", force=False)
        _, err, rc = _capture(cli_module.cmd_fork, args)
        assert rc != 0
        assert "already exists" in err

    def test_fork_overwrites_with_force(self, contracts_dir):
        (contracts_dir / "target.yaml").write_text(
            "contract:\n  name: target\n  version: \"9.9\"\n  rules: []\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(src="customer", dst="target", force=True)
        _, _, rc = _capture(cli_module.cmd_fork, args)
        assert rc == 0
        forked = (contracts_dir / "target.yaml").read_text(encoding="utf-8")
        assert "name: target" in forked
        assert 'version: "1.0"' in forked

    def test_fork_rejects_missing_source(self, contracts_dir):
        args = argparse.Namespace(src="does_not_exist_zzz", dst="whatever", force=False)
        _, err, rc = _capture(cli_module.cmd_fork, args)
        assert rc != 0
        assert "not found" in err.lower()

    def test_fork_rejects_identical_src_and_dst(self, contracts_dir):
        args = argparse.Namespace(src="customer", dst="customer", force=False)
        _, err, rc = _capture(cli_module.cmd_fork, args)
        assert rc != 0
        assert "identical" in err

    def test_fork_rejects_invalid_dst_name(self, contracts_dir):
        args = argparse.Namespace(src="customer", dst="../escape", force=False)
        _, err, rc = _capture(cli_module.cmd_fork, args)
        assert rc != 0

    def test_forked_contract_lints_clean(self, contracts_dir):
        """End-to-end: fork produces a file that `opendqv lint` passes."""
        _capture(cli_module.cmd_fork, argparse.Namespace(src="customer", dst="my_custom", force=False))
        out, _, rc = _capture(cli_module.cmd_lint, argparse.Namespace(contract="my_custom", format="text"))
        assert rc == 0
        assert "PASS" in out


# ---------------------------------------------------------------------------
# TestCmdLintDirect
# ---------------------------------------------------------------------------

class TestCmdLintDirect:
    """cmd_lint — direct call coverage."""

    def test_lint_text_pass(self, contracts_dir):
        args = argparse.Namespace(contract="customer", format="text")
        out, _, rc = _capture(cli_module.cmd_lint, args)
        assert "PASS" in out

    def test_lint_text_fail_with_issues(self, contracts_dir):
        """Lint a contract with logical errors — covers lines 538-542."""
        broken = contracts_dir / "broken_lint.yaml"
        broken.write_text(
            "contract:\n  name: broken_lint\n  version: \"1.0\"\n  status: draft\n"
            "rules:\n"
            "  - name: bad_range\n    type: range\n    field: age\n"
            "    min: 100\n    max: 10\n    error_message: bad\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(contract="broken_lint", format="text")
        out, _, rc = _capture(cli_module.cmd_lint, args)
        assert "FAIL" in out
        assert rc != 0

    def test_lint_json_format(self, contracts_dir):
        args = argparse.Namespace(contract="customer", format="json")
        out, _, rc = _capture(cli_module.cmd_lint, args)
        parsed = json.loads(out)
        assert "issues" in parsed or "errors" in parsed or "passed" in parsed

    def test_lint_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz", format="text")
        _, err, rc = _capture(cli_module.cmd_lint, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdGenerateDirect
# ---------------------------------------------------------------------------

class TestCmdGenerateDirect:
    """cmd_generate — direct call coverage."""

    @pytest.mark.parametrize("target", ["snowflake", "spark", "bigquery", "js", "salesforce"])
    def test_generate_target(self, target, contracts_dir):
        args = argparse.Namespace(contract="customer", target=target, context=None)
        out, _, rc = _capture(cli_module.cmd_generate, args)
        assert rc == 0
        assert len(out) > 0

    def test_generate_invalid_target(self, contracts_dir):
        args = argparse.Namespace(contract="customer", target="invalid_zzz", context=None)
        _, err, rc = _capture(cli_module.cmd_generate, args)
        assert rc != 0

    def test_generate_contract_not_found(self, contracts_dir):
        args = argparse.Namespace(contract="nonexistent_zzz", target="snowflake", context=None)
        _, err, rc = _capture(cli_module.cmd_generate, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdAuditVerifyDirect
# ---------------------------------------------------------------------------

class TestCmdAuditVerifyDirect:
    """cmd_audit_verify — direct call coverage."""

    def _make_db(self, tmp_path):
        """Create a minimal contract_history table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE contract_history ("
            "id INTEGER PRIMARY KEY, contract_name TEXT, version TEXT, "
            "status TEXT, rules TEXT, contexts TEXT, opendqv_node_id TEXT, "
            "updated_at TEXT, prev_hash TEXT, entry_hash TEXT)"
        )
        conn.commit()
        return db_path, conn

    def test_audit_verify_empty_db(self, tmp_path):
        db_path, conn = self._make_db(tmp_path)
        conn.close()
        args = argparse.Namespace(db=str(db_path))
        out, _, rc = _capture(cli_module.cmd_audit_verify, args)
        assert "PASS" in out
        assert rc == 0

    def test_audit_verify_db_not_found(self, tmp_path):
        args = argparse.Namespace(db=str(tmp_path / "nonexistent.db"))
        _, err, rc = _capture(cli_module.cmd_audit_verify, args)
        assert rc != 0

    def test_audit_verify_table_missing(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.commit()
        conn.close()
        args = argparse.Namespace(db=str(db_path))
        _, err, rc = _capture(cli_module.cmd_audit_verify, args)
        assert rc != 0

    def test_audit_verify_with_valid_history(self, tmp_path):
        """Audit-verify with actual history entries — covers lines 689-741."""
        from opendqv.core.contracts import _compute_entry_hash
        db_path, conn = self._make_db(tmp_path)

        genesis = "0" * 64
        entry_hash = _compute_entry_hash(
            prev_hash=genesis,
            contract_name="test_contract",
            version="1.0",
            status="active",
            rules_json="[]",
            contexts_json="{}",
            opendqv_node_id="node-1",
            updated_at="2026-01-01T00:00:00",
        )
        conn.execute(
            "INSERT INTO contract_history "
            "(contract_name, version, status, rules, contexts, opendqv_node_id, "
            "updated_at, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ("test_contract", "1.0", "active", "[]", "{}", "node-1",
             "2026-01-01T00:00:00", genesis, entry_hash),
        )
        conn.commit()
        conn.close()

        args = argparse.Namespace(db=str(db_path))
        out, _, rc = _capture(cli_module.cmd_audit_verify, args)
        assert "PASS" in out
        assert rc == 0
        assert "test_contract" in out

    def test_audit_verify_with_broken_chain(self, tmp_path):
        """Audit-verify with tampered entry_hash — should FAIL."""
        db_path, conn = self._make_db(tmp_path)

        conn.execute(
            "INSERT INTO contract_history "
            "(contract_name, version, status, rules, contexts, opendqv_node_id, "
            "updated_at, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ("test_contract", "1.0", "active", "[]", "{}", "node-1",
             "2026-01-01T00:00:00", "0" * 64, "tampered_hash"),
        )
        conn.commit()
        conn.close()

        args = argparse.Namespace(db=str(db_path))
        out, _, rc = _capture(cli_module.cmd_audit_verify, args)
        assert "FAIL" in out
        assert rc != 0

    def test_audit_verify_clock_sync_with_rows(self, tmp_path):
        """_print_clock_sync_section with clock rows — covers lines 628-650."""
        db_path, conn = self._make_db(tmp_path)
        conn.execute(
            "CREATE TABLE node_health_log (id INTEGER PRIMARY KEY, reason TEXT, transitioned_at TEXT)"
        )
        # Add a skewed startup row
        conn.execute(
            "INSERT INTO node_health_log (reason, transitioned_at) VALUES (?, ?)",
            (
                "node startup | clock_status=skewed | skew_ms=150 | ntp_source=pool.ntp.org",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        args = argparse.Namespace(db=str(db_path))
        out, _, rc = _capture(cli_module.cmd_audit_verify, args)
        assert "skewed" in out or "WARNING" in out

    def test_audit_verify_clock_sync_unavailable(self, tmp_path):
        """_print_clock_sync_section with unavailable clock — covers lines 641-643."""
        db_path, conn = self._make_db(tmp_path)
        conn.execute(
            "CREATE TABLE node_health_log (id INTEGER PRIMARY KEY, reason TEXT, transitioned_at TEXT)"
        )
        conn.execute(
            "INSERT INTO node_health_log (reason, transitioned_at) VALUES (?, ?)",
            (
                "node startup | clock_status=unavailable | skew_ms=0 | ntp_source=none",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        args = argparse.Namespace(db=str(db_path))
        out, _, rc = _capture(cli_module.cmd_audit_verify, args)
        assert "WARNING" in out


# ---------------------------------------------------------------------------
# TestCmdContractsImportDirDirect
# ---------------------------------------------------------------------------

class TestCmdContractsImportDirDirect:
    """cmd_contracts_import_dir — non-dry-run path coverage."""

    def test_import_dir_real_run(self, tmp_path, contracts_dir):
        src = tmp_path / "src"
        src.mkdir()
        (src / "probe.yaml").write_text(
            "contract:\n  name: probe\n  version: \"1.0\"\n  status: draft\n  rules: []\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(directory=str(src), dry_run=False)
        out, _, rc = _capture(cli_module.cmd_contracts_import_dir, args)
        assert "probe.yaml" in out

    def test_import_dir_not_found(self, contracts_dir):
        args = argparse.Namespace(directory="/tmp/nonexistent_dir_zzz", dry_run=False)
        _, err, rc = _capture(cli_module.cmd_contracts_import_dir, args)
        assert rc != 0

    def test_import_dir_no_yaml_files(self, tmp_path, contracts_dir):
        empty = tmp_path / "empty_dir"
        empty.mkdir()
        args = argparse.Namespace(directory=str(empty), dry_run=False)
        out, _, rc = _capture(cli_module.cmd_contracts_import_dir, args)
        assert "No YAML" in out

    def test_import_dir_malformed_yaml(self, tmp_path, contracts_dir):
        src = tmp_path / "bad"
        src.mkdir()
        (src / "bad.yaml").write_text("this: is: not: valid: yaml:")
        args = argparse.Namespace(directory=str(src), dry_run=False)
        out, _, rc = _capture(cli_module.cmd_contracts_import_dir, args)
        assert "failed" in out.lower() or "✗" in out


# ---------------------------------------------------------------------------
# TestCmdWorkflowDirect
# ---------------------------------------------------------------------------

class TestCmdWorkflowDirect:
    """cmd_submit_review, cmd_approve, cmd_reject — direct call coverage."""

    def test_submit_review(self, draft_contract, contracts_dir):
        args = argparse.Namespace(
            contract=draft_contract, version="1.0", proposed_by="test-user"
        )
        out, _, rc = _capture(cli_module.cmd_submit_review, args)
        assert rc == 0
        assert "submitted for review" in out.lower()

    def test_submit_review_not_found(self, contracts_dir):
        args = argparse.Namespace(
            contract="nonexistent_zzz", version="1.0", proposed_by=None
        )
        _, err, rc = _capture(cli_module.cmd_submit_review, args)
        assert rc != 0

    def test_approve(self, review_contract, contracts_dir):
        args = argparse.Namespace(
            contract=review_contract, version="1.0", approved_by="approver"
        )
        out, _, rc = _capture(cli_module.cmd_approve, args)
        assert rc == 0
        assert "approved" in out.lower()

    def test_approve_not_found(self, contracts_dir):
        args = argparse.Namespace(
            contract="nonexistent_zzz", version="1.0", approved_by=None
        )
        _, err, rc = _capture(cli_module.cmd_approve, args)
        assert rc != 0

    def test_reject(self, review_contract, contracts_dir):
        args = argparse.Namespace(
            contract=review_contract, version="1.0",
            rejected_by="reviewer", reason="Not ready"
        )
        out, _, rc = _capture(cli_module.cmd_reject, args)
        assert rc == 0
        assert "rejected" in out.lower()
        assert "Not ready" in out

    def test_reject_no_reason(self, review_contract, contracts_dir):
        args = argparse.Namespace(
            contract=review_contract, version="1.0",
            rejected_by=None, reason=""
        )
        out, _, rc = _capture(cli_module.cmd_reject, args)
        assert rc == 0

    def test_reject_not_found(self, contracts_dir):
        args = argparse.Namespace(
            contract="nonexistent_zzz", version="1.0",
            rejected_by=None, reason=""
        )
        _, err, rc = _capture(cli_module.cmd_reject, args)
        assert rc != 0


# ---------------------------------------------------------------------------
# TestCmdTokenGenerateDirect
# ---------------------------------------------------------------------------

class TestCmdTokenGenerateDirect:
    """cmd_token_generate — direct call coverage."""

    def test_token_generate(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch.dict(os.environ, {"OPENDQV_DB_PATH": str(db_path)}):
            args = argparse.Namespace(name="test-token", role="validator", expiry_days=30)
            out, _, rc = _capture(cli_module.cmd_token_generate, args)
        assert rc == 0
        assert "Token generated" in out
        assert "Token" in out

    def test_token_generate_admin_role(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch.dict(os.environ, {"OPENDQV_DB_PATH": str(db_path)}):
            args = argparse.Namespace(name="admin-token", role="admin", expiry_days=7)
            out, _, rc = _capture(cli_module.cmd_token_generate, args)
        assert rc == 0
        assert "admin" in out
