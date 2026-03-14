#!/usr/bin/env python3
"""
OpenDQV CLI — standalone command-line interface for data contract operations.

Usage:
    python -m cli <command> [options]

Commands:
    list                           List all contracts
    show <contract>                Show contract details
    validate <contract> <json>     Validate a JSON record against a contract
    export-gx <contract>           Export contract as GX expectation suite JSON
    import-gx <file>               Import GX suite JSON and save as YAML contract
    import-dbt <file>              Import dbt schema.yml and save as YAML contract(s)
    import-soda <file>             Import Soda Core checks YAML and save as YAML contract(s)
    import-csv <file>              Import CSV rules and save as YAML contract
    import-odcs <file>             Import ODCS 3.1 contract (YAML/JSON) and save as OpenDQV contract
    export-odcs <contract>         Export contract as ODCS 3.1 YAML
    export-dbt <contract>          Export contract as dbt schema.yml
    generate <contract> <target>   Generate validation code (salesforce/js/snowflake)
    onboard                        Interactive setup wizard — first validation in 90 seconds
    submit-review <contract>       Submit a DRAFT contract for review (DRAFT → REVIEW)
    approve <contract>             Approve a REVIEW contract (REVIEW → ACTIVE)
    reject <contract>              Reject a REVIEW contract back to DRAFT
    token-generate <name>          Generate a Personal Access Token for API authentication
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure the project root is on sys.path so core.* imports work
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3

from core.contracts import ContractRegistry, _compute_entry_hash
from core.validator import validate_record
from core.code_generator import generate_code
from core.importers.great_expectations import import_gx_suite, gx_suite_to_yaml, export_gx_suite
from core.importers.soda import import_soda_checks, soda_checks_to_yaml
from core.importers.csv_rules import import_csv_rules, csv_rules_to_yaml
from core.importers.odcs import import_odcs, odcs_to_yaml, contract_to_odcs_yaml
from core.importers.dbt import contract_to_dbt_yaml

CONTRACTS_DIR = PROJECT_ROOT / "contracts"

import re as _re
_CONTRACT_NAME_RE = _re.compile(r'^[A-Za-z0-9_-]{1,100}$')

def _validate_contract_name(name: str) -> None:
    """Exit with error if contract name is unsafe (path traversal, invalid chars)."""
    if not _CONTRACT_NAME_RE.match(name):
        print(
            f"Error: Invalid contract name '{name}'. "
            "Names must contain only letters, digits, hyphens, and underscores (1–100 chars).",
            file=sys.stderr,
        )
        sys.exit(1)


def get_registry() -> ContractRegistry:
    """Load the contract registry from the contracts/ directory."""
    return ContractRegistry(CONTRACTS_DIR)


def cmd_list(args):
    """List all contracts with status and rule count."""
    registry = get_registry()
    contracts = registry.list_contracts(include_all=True)
    if not contracts:
        print("No contracts found in", CONTRACTS_DIR)
        return

    # Column widths
    name_w = max(len(c["name"]) for c in contracts)
    ver_w = max(len(c["version"]) for c in contracts)
    status_w = max(len(c["status"]) for c in contracts)

    header = f"{'NAME':<{name_w}}  {'VER':<{ver_w}}  {'STATUS':<{status_w}}  RULES"
    print(header)
    print("-" * len(header))
    for c in contracts:
        print(f"{c['name']:<{name_w}}  {c['version']:<{ver_w}}  {c['status']:<{status_w}}  {c['rule_count']}")


def cmd_show(args):
    """Show contract details and list all rules."""
    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Contract: {contract.name}")
    print(f"Version:  {contract.version}")
    print(f"Status:   {contract.status.value}")
    print(f"Owner:    {contract.owner or '(none)'}")
    print(f"Desc:     {contract.description or '(none)'}")
    if contract.contexts:
        print(f"Contexts: {', '.join(contract.contexts.keys())}")
    print()

    rules = contract.rules
    if not rules:
        print("  (no rules)")
        return

    name_w = max(len(r.name) for r in rules)
    type_w = max(len(r.type) for r in rules)
    field_w = max(len(r.field) for r in rules)

    header = f"  {'RULE':<{name_w}}  {'TYPE':<{type_w}}  {'FIELD':<{field_w}}  SEVERITY"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rules:
        print(f"  {r.name:<{name_w}}  {r.type:<{type_w}}  {r.field:<{field_w}}  {r.severity.value}")


def cmd_validate(args):
    """Validate a JSON record against a contract."""
    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    try:
        record = json.loads(args.json)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    rules = registry.get_rules_with_context(contract, args.context)
    result = validate_record(record, rules)

    status = "PASS" if result["valid"] else "FAIL"
    print(f"Result: {status}")
    print(f"Errors:   {len(result['errors'])}")
    print(f"Warnings: {len(result['warnings'])}")

    if result["errors"]:
        print("\nErrors:")
        for e in result["errors"]:
            print(f"  - [{e['field']}] {e['message']}")

    if result["warnings"]:
        print("\nWarnings:")
        for w in result["warnings"]:
            print(f"  - [{w['field']}] {w['message']}")

    sys.exit(0 if result["valid"] else 1)


def cmd_export_gx(args):
    """Export contract as GX expectation suite JSON."""
    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    rules = registry.get_rules_with_context(contract, args.context)
    suite = export_gx_suite(contract.name, rules)
    suite["meta"]["contract_version"] = contract.version
    if args.context:
        suite["meta"]["context"] = args.context

    output = json.dumps(suite, indent=2)

    if args.output:
        Path(args.output).write_text(output + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)


def cmd_import_gx(args):
    """Import GX suite JSON and save as YAML contract."""
    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        suite_json = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = import_gx_suite(suite_json)
        yaml_content = gx_suite_to_yaml(suite_json)
    except Exception as e:
        print(f"Error: Failed to import GX suite: {e}", file=sys.stderr)
        sys.exit(1)
    stats = result["stats"]
    contract_name = result["contract"]["name"]
    _validate_contract_name(contract_name)

    # Write YAML to contracts dir
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONTRACTS_DIR / f"{contract_name}.yaml"
    out_path.write_text(yaml_content)

    print(f"Contract: {contract_name}")
    print(f"Saved to: {out_path}")
    print(f"Total expectations: {stats['total_expectations']}")
    print(f"Imported rules:     {stats['imported']}")
    print(f"Skipped:            {stats['skipped']}")

    if result["skipped"]:
        print("\nSkipped expectations:")
        for s in result["skipped"]:
            print(f"  - {s['expectation_type']}: {s['reason']}")


def cmd_import_dbt(args):
    """Import dbt schema.yml and save as YAML contract(s)."""
    import yaml as _yaml
    from core.importers.dbt import import_dbt_schema, dbt_schema_to_yaml

    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(path) as f:
            schema = _yaml.safe_load(f)
    except Exception as e:
        print(f"Error: Could not parse {path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = import_dbt_schema(schema)
        pairs = dbt_schema_to_yaml(schema)
    except Exception as e:
        print(f"Error: Failed to import dbt schema: {e}", file=sys.stderr)
        sys.exit(1)
    contracts_dir = CONTRACTS_DIR
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for name, yaml_content in pairs:
        _validate_contract_name(name)
        out_path = contracts_dir / f"{name}.yaml"
        out_path.write_text(yaml_content)
        print(f"Saved: {out_path}")
    for item in result["contracts"]:
        stats = item["stats"]
        print(f"\n{item['contract']['name']}: {stats['imported']} rules imported, {stats['skipped']} skipped")
    print(f"\n{len(pairs)} contract draft(s) saved. Review and activate each via the workbench or 'dqv approve'.")


def cmd_import_soda(args):
    """Import Soda Core checks YAML and save as YAML contract(s)."""
    import yaml as _yaml

    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(path) as f:
            checks_yaml = _yaml.safe_load(f)
    except Exception as e:
        print(f"Error: Could not parse {path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        result = import_soda_checks(checks_yaml)
        pairs = soda_checks_to_yaml(checks_yaml)
    except Exception as e:
        print(f"Error: Failed to import Soda checks: {e}", file=sys.stderr)
        sys.exit(1)
    contracts_dir = CONTRACTS_DIR
    contracts_dir.mkdir(parents=True, exist_ok=True)
    for name, yaml_content in pairs:
        _validate_contract_name(name)
        out_path = contracts_dir / f"{name}.yaml"
        out_path.write_text(yaml_content)
        print(f"Saved: {out_path}")
    for item in result["contracts"]:
        stats = item["stats"]
        print(f"\n{item['contract']['name']}: {stats['imported']} rules imported, {stats['skipped']} skipped")


def cmd_import_csv(args):
    """Import CSV rules and save as YAML contract."""
    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    csv_content = path.read_text()
    contract_name = args.name or path.stem
    _validate_contract_name(contract_name)

    try:
        result = import_csv_rules(csv_content, contract_name)
        yaml_content = csv_rules_to_yaml(csv_content, contract_name)
    except Exception as e:
        print(f"Error: Failed to import CSV rules: {e}", file=sys.stderr)
        sys.exit(1)
    stats = result["stats"]

    # Write YAML to contracts dir
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONTRACTS_DIR / f"{contract_name}.yaml"
    out_path.write_text(yaml_content)

    print(f"Contract: {contract_name}")
    print(f"Saved to: {out_path}")
    print(f"Total rules:    {stats['total_rules']}")
    print(f"Imported rules: {stats['imported']}")
    print(f"Skipped:        {stats['skipped']}")

    if result["skipped"]:
        print("\nSkipped rows:")
        for s in result["skipped"]:
            print(f"  - row {s.get('row', '?')}: {s['reason']}")


def cmd_import_odcs(args):
    """Import ODCS 3.1 contract (YAML or JSON) and save as OpenDQV contract YAML."""
    import yaml as _yaml

    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = path.read_text()
        contract_data = _yaml.safe_load(raw)
    except Exception as e:
        print(f"Error: Could not parse {path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        contract_name, yaml_content = odcs_to_yaml(contract_data, args.name or None)
        result = import_odcs(contract_data)
    except Exception as e:
        print(f"Error: Failed to import ODCS contract: {e}", file=sys.stderr)
        sys.exit(1)
    _validate_contract_name(contract_name)
    rule_count = result["rule_count"]
    skipped = result["skipped_checks"]

    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONTRACTS_DIR / f"{contract_name}.yaml"
    out_path.write_text(yaml_content)

    print(f"Contract: {contract_name}")
    print(f"Saved to: {out_path}")
    print(f"Rules imported: {rule_count}")
    if skipped:
        print(f"Skipped checks: {len(skipped)}")
        for s in skipped:
            print(f"  - {s}")


def cmd_export_odcs(args):
    """Export a contract as ODCS 3.1 YAML."""

    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    rules = registry.get_rules_with_context(contract, args.context)
    status_val = contract.status.value if hasattr(contract.status, "value") else str(contract.status)
    yaml_str = contract_to_odcs_yaml(
        contract_name=contract.name,
        rules=rules,
        version=contract.version,
        status=status_val,
        description=getattr(contract, "description", "") or "",
        owner=getattr(contract, "owner", "") or "",
    )

    if args.output:
        Path(args.output).write_text(yaml_str)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(yaml_str, end="")


def cmd_export_dbt(args):
    """Export a contract as dbt schema.yml."""
    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    rules = registry.get_rules_with_context(contract, getattr(args, "context", None))
    yaml_str = contract_to_dbt_yaml(
        contract_name=contract.name,
        rules=rules,
        description=getattr(contract, "description", "") or "",
    )

    if args.output:
        Path(args.output).write_text(yaml_str)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(yaml_str, end="")


def cmd_onboard(args):
    """Launch the interactive onboarding wizard."""
    from core.onboarding import OnboardingWizard
    wizard = OnboardingWizard()
    result = wizard.run()
    sys.exit(0 if result.success else 1)


def cmd_generate(args):
    """Generate validation code for a target platform."""
    registry = get_registry()
    contract = registry.get(args.contract)
    if not contract:
        print(f"Error: Contract '{args.contract}' not found.", file=sys.stderr)
        sys.exit(1)

    valid_targets = ("salesforce", "js", "snowflake")
    if args.target not in valid_targets:
        print(f"Error: Invalid target '{args.target}'. Must be one of: {', '.join(valid_targets)}", file=sys.stderr)
        sys.exit(1)

    rules = registry.get_rules_with_context(contract, args.context)
    code = generate_code(rules, args.target)
    print(code)


def cmd_contracts_import_dir(args):
    """Import all YAML contracts from a directory."""
    import yaml as _yaml
    dir_path = Path(args.directory)
    if not dir_path.exists():
        print(f"Error: Directory '{args.directory}' does not exist.", file=sys.stderr)
        sys.exit(1)

    yaml_files = sorted(dir_path.glob("*.yaml"))
    if not yaml_files:
        print(f"No YAML files found in '{args.directory}'.")
        return

    print(f"Found {len(yaml_files)} YAML file(s) in '{args.directory}':")
    loaded = 0
    failed = 0
    for f in yaml_files:
        if args.dry_run:
            print(f"  [dry-run] {f.name}")
        else:
            try:
                raw = _yaml.safe_load(f.read_text())
                if raw:
                    print(f"  \u2713 {f.name}")
                    loaded += 1
                else:
                    print(f"  ! {f.name} \u2014 empty file")
                    failed += 1
            except Exception as e:
                print(f"  \u2717 {f.name} \u2014 {e}")
                failed += 1

    if not args.dry_run:
        print(f"\nResult: {loaded} loaded, {failed} failed.")


def cmd_audit_verify(args):
    """Verify the integrity of the contract_history chain in the SQLite DB."""
    db_path = args.db
    resolved = str(Path(db_path).resolve())
    print(f"Verifying contract history: {resolved}")
    print("\u2500" * 60)

    if not Path(db_path).exists():
        print(f"Error: database not found: {resolved}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, contract_name, version, status, rules, contexts, "
            "opendqv_node_id, updated_at, prev_hash, entry_hash "
            "FROM contract_history ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Error reading contract_history: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.close()

    if not rows:
        print("No entries found in contract_history.")
        print("\u2500" * 60)
        print("All 0 entries verified. Chain integrity: PASS")
        sys.exit(0)

    # Group rows by contract_name to display them together, preserving id order
    contracts_seen = []
    contracts_map: dict = {}
    for row in rows:
        name = row["contract_name"]
        if name not in contracts_map:
            contracts_map[name] = []
            contracts_seen.append(name)
        contracts_map[name].append(row)

    _GENESIS_HASH = "0" * 64
    all_pass = True
    total_entries = 0

    for contract_name in contracts_seen:
        print(f"Contract: {contract_name}")
        prev_hash = _GENESIS_HASH
        for entry_num, row in enumerate(contracts_map[contract_name], start=1):
            total_entries += 1
            expected_hash = _compute_entry_hash(
                prev_hash=row["prev_hash"],
                contract_name=row["contract_name"],
                version=row["version"],
                status=row["status"],
                rules_json=row["rules"] or "",
                contexts_json=row["contexts"] or "",
                opendqv_node_id=row["opendqv_node_id"],
                updated_at=row["updated_at"],
            )

            hash_valid = expected_hash == row["entry_hash"]
            chain_valid = row["prev_hash"] == prev_hash

            hash_mark = "\u2713 hash valid" if hash_valid else "\u2717 hash MISMATCH"
            chain_mark = "\u2713 chain link valid" if chain_valid else "\u2717 chain link BROKEN"

            print(f"  Entry #{entry_num} (v{row['version']}, {row['status']})  {hash_mark}, {chain_mark}")

            if not hash_valid or not chain_valid:
                all_pass = False

            prev_hash = row["entry_hash"]

    print("\u2500" * 60)
    integrity = "PASS" if all_pass else "FAIL"
    print(f"All {total_entries} entries verified. Chain integrity: {integrity}")

    if not all_pass:
        sys.exit(1)


def cmd_submit_review(args):
    """Submit a DRAFT contract for review (DRAFT → REVIEW)."""
    registry = get_registry()
    proposed_by = args.proposed_by or "cli-user"
    try:
        contract = registry.submit_for_review(args.contract, args.version, proposed_by)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not contract:
        print(f"Error: contract '{args.contract}' version '{args.version}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"Contract '{args.contract}' v{args.version} submitted for review.")
    print(f"  Status   : {contract.status.value}")
    print(f"  Proposed : {proposed_by}")


def cmd_approve(args):
    """Approve a REVIEW contract (REVIEW → ACTIVE)."""
    registry = get_registry()
    approved_by = args.approved_by or "cli-user"
    try:
        contract = registry.approve_contract(args.contract, args.version, approved_by)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not contract:
        print(f"Error: contract '{args.contract}' version '{args.version}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"Contract '{args.contract}' v{args.version} approved.")
    print(f"  Status    : {contract.status.value}")
    print(f"  Approved  : {approved_by}")


def cmd_reject(args):
    """Reject a REVIEW contract back to DRAFT (REVIEW → DRAFT)."""
    registry = get_registry()
    rejected_by = args.rejected_by or "cli-user"
    reason = args.reason or ""
    try:
        contract = registry.reject_contract(args.contract, args.version, rejected_by, reason)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not contract:
        print(f"Error: contract '{args.contract}' version '{args.version}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"Contract '{args.contract}' v{args.version} rejected back to Draft.")
    print(f"  Status   : {contract.status.value}")
    print(f"  Rejected : {rejected_by}")
    if reason:
        print(f"  Reason   : {reason}")


def cmd_token_generate(args):
    """Generate a Personal Access Token (PAT) for API authentication."""
    try:
        from security.auth import create_pat
    except ImportError:
        print("Error: security.auth not available. Run from the OpenDQV project root.", file=sys.stderr)
        sys.exit(1)
    token_data = create_pat(
        username=args.name,
        expiry_days=args.expiry_days,
        role=args.role,
    )
    print(f"Token generated for '{args.name}' (role: {args.role})")
    print(f"  Expires  : {token_data['expires_at'][:10]} ({args.expiry_days} days)")
    print(f"  Token    : {token_data['token']}")
    print()
    print("Copy the token now — it is not stored in recoverable form.")


def main():
    parser = argparse.ArgumentParser(
        prog="opendqv",
        description="OpenDQV CLI — Data quality contract management and validation",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version="opendqv 1.0.0\nTrust is cheaper to build than to repair.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # list
    subparsers.add_parser("list", help="List all contracts")

    # show
    p_show = subparsers.add_parser("show", help="Show contract details")
    p_show.add_argument("contract", help="Contract name")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate a JSON record against a contract")
    p_validate.add_argument("contract", help="Contract name")
    p_validate.add_argument("json", help="JSON string of the record to validate")
    p_validate.add_argument("--context", default=None, help="Context to apply (e.g. 'salesforce', 'kids_app')")

    # export-gx
    p_export = subparsers.add_parser("export-gx", help="Export contract as GX expectation suite JSON")
    p_export.add_argument("contract", help="Contract name")
    p_export.add_argument("--context", default=None, help="Context to apply before export")
    p_export.add_argument("--output", "-o", default=None, help="Write output to file instead of stdout")

    # import-gx
    p_import = subparsers.add_parser("import-gx", help="Import GX suite JSON and save as YAML contract")
    p_import.add_argument("file", help="Path to GX suite JSON file")

    # import-dbt
    p_import_dbt = subparsers.add_parser("import-dbt", help="Import dbt schema.yml and save as YAML contract(s)")
    p_import_dbt.add_argument("file", help="Path to dbt schema.yml file")

    # import-soda
    p_import_soda = subparsers.add_parser("import-soda", help="Import Soda Core checks YAML and save as YAML contract(s)")
    p_import_soda.add_argument("file", help="Path to Soda checks YAML file")

    # import-csv
    p_import_csv = subparsers.add_parser("import-csv", help="Import CSV rules and save as YAML contract")
    p_import_csv.add_argument("file", help="Path to CSV rules file")
    p_import_csv.add_argument("--name", default=None, help="Contract name (default: CSV filename stem)")

    # import-odcs
    p_import_odcs = subparsers.add_parser("import-odcs", help="Import ODCS 3.1 contract and save as OpenDQV contract")
    p_import_odcs.add_argument("file", help="Path to ODCS 3.1 YAML or JSON file")
    p_import_odcs.add_argument("--name", default=None, help="Contract name override (default: from info.title)")

    # export-odcs
    p_export_odcs = subparsers.add_parser("export-odcs", help="Export contract as ODCS 3.1 YAML")
    p_export_odcs.add_argument("contract", help="Contract name")
    p_export_odcs.add_argument("--context", default=None, help="Context to apply before export")
    p_export_odcs.add_argument("--output", "-o", default=None, help="Write output to file instead of stdout")

    # export-dbt
    p_export_dbt = subparsers.add_parser("export-dbt", help="Export contract as dbt schema.yml")
    p_export_dbt.add_argument("contract", help="Contract name")
    p_export_dbt.add_argument("--context", default=None, help="Context to apply before export")
    p_export_dbt.add_argument("--output", "-o", default=None, help="Write to file instead of stdout")

    # generate
    p_gen = subparsers.add_parser("generate", help="Generate validation code for a target platform")
    p_gen.add_argument("contract", help="Contract name")
    p_gen.add_argument("target", help="Target platform: salesforce, js, snowflake")
    p_gen.add_argument("--context", default=None, help="Context to apply before generation")

    # onboard
    subparsers.add_parser("onboard", help="Interactive setup wizard — first validation in 90 seconds")

    # audit-verify
    p_audit = subparsers.add_parser("audit-verify", help="Verify contract history hash-chain integrity")
    p_audit.add_argument("--db", default="opendqv.db", help="Path to SQLite DB (default: opendqv.db)")

    # contracts import-dir
    p_contracts_import_dir = subparsers.add_parser(
        "contracts-import-dir", help="Import all YAML contracts from a directory"
    )
    p_contracts_import_dir.add_argument("directory", help="Path to directory containing YAML contract files")
    p_contracts_import_dir.add_argument(
        "--dry-run", action="store_true", help="List files without importing"
    )

    # submit-review
    p_submit_review = subparsers.add_parser(
        "submit-review", help="Submit a DRAFT contract for review (DRAFT → REVIEW)"
    )
    p_submit_review.add_argument("contract", help="Contract name")
    p_submit_review.add_argument("--version", required=True, help="Contract version (e.g. 1.0.0)")
    p_submit_review.add_argument("--proposed-by", default=None, help="Identity of the proposer (default: cli-user)")

    # approve
    p_approve = subparsers.add_parser(
        "approve", help="Approve a REVIEW contract (REVIEW → ACTIVE)"
    )
    p_approve.add_argument("contract", help="Contract name")
    p_approve.add_argument("--version", required=True, help="Contract version (e.g. 1.0.0)")
    p_approve.add_argument("--approved-by", default=None, help="Identity of the approver (default: cli-user)")

    # reject
    p_reject = subparsers.add_parser(
        "reject", help="Reject a REVIEW contract back to DRAFT (REVIEW → DRAFT)"
    )
    p_reject.add_argument("contract", help="Contract name")
    p_reject.add_argument("--version", required=True, help="Contract version (e.g. 1.0.0)")
    p_reject.add_argument("--rejected-by", default=None, help="Identity of the rejector (default: cli-user)")
    p_reject.add_argument("--reason", default="", help="Rejection reason")

    # token generate
    p_token_gen = subparsers.add_parser(
        "token-generate", help="Generate a Personal Access Token (PAT) for API authentication"
    )
    p_token_gen.add_argument("name", help="Token name / username (e.g. salesforce-prod)")
    p_token_gen.add_argument(
        "--role",
        default="validator",
        choices=["validator", "reader", "auditor", "editor", "approver", "admin"],
        help="Token role (default: validator)",
    )
    p_token_gen.add_argument(
        "--expiry-days",
        type=int,
        default=365,
        dest="expiry_days",
        help="Token lifetime in days (default: 365)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "list": cmd_list,
        "show": cmd_show,
        "validate": cmd_validate,
        "export-gx": cmd_export_gx,
        "import-gx": cmd_import_gx,
        "import-dbt": cmd_import_dbt,
        "import-soda": cmd_import_soda,
        "import-csv": cmd_import_csv,
        "import-odcs": cmd_import_odcs,
        "export-odcs": cmd_export_odcs,
        "export-dbt": cmd_export_dbt,
        "generate": cmd_generate,
        "audit-verify": cmd_audit_verify,
        "contracts-import-dir": cmd_contracts_import_dir,
        "onboard": cmd_onboard,
        "submit-review": cmd_submit_review,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "token-generate": cmd_token_generate,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
