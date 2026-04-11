# CLI Reference

> **Last reviewed:** 2026-04-11.
> Covers all 20 commands available in `cli.py`. Run `opendqv --help` or `python -m opendqv.cli --help` for the same information inline.

OpenDQV ships a standalone CLI for contract management, validation, imports, exports, lifecycle governance, and code generation. All commands operate on the local filesystem and SQLite database — no running API server is required.

---

## Installation and Invocation

**Installed via pip:**

```bash
opendqv <command> [options]
```

**Running from source (project root):**

```bash
python -m opendqv.cli <command> [options]
```

**Print version:**

```bash
opendqv --version
# opendqv 2.1.0
# Trust is cheaper to build than to repair.
```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `OPENDQV_CONTRACTS_DIR` | `./contracts` | Directory where YAML contract files are read from and written to |
| `OPENDQV_DB_PATH` | `opendqv.db` | Path to the SQLite database (contract history, audit chain) |
| `OPENDQV_AUTH_MODE` | `none` | Authentication mode for the API server (`none` or `token`); does not affect CLI directly, but `token-generate` writes to the same DB |

---

## Command Reference

| Command | Arguments | Flags | Description |
|---|---|---|---|
| `init` | — | `--dir`, `--force` | Bootstrap a `contracts/` directory with a starter contract |
| `list` | — | — | List all contracts with name, version, status, and rule count |
| `show` | `<contract>` | — | Show contract metadata and a table of all rules with type, field, and severity |
| `validate` | `<contract> <json>` | `--context` | Validate a JSON record string against a contract; exits 0 on PASS, 1 on FAIL |
| `export-gx` | `<contract>` | `--context`, `--output`/`-o` | Export contract as a Great Expectations expectation suite JSON |
| `import-gx` | `<file>` | — | Import a GX suite JSON file and save as an OpenDQV YAML contract |
| `import-dbt` | `<file>` | — | Import a dbt `schema.yml` and save as one or more YAML contracts |
| `import-soda` | `<file>` | — | Import a Soda Core checks YAML and save as one or more YAML contracts |
| `import-csv` | `<file>` | `--name` | Import a CSV rules file and save as a YAML contract |
| `import-odcs` | `<file>` | `--name` | Import an ODCS 3.1 contract (YAML or JSON) and save as an OpenDQV contract |
| `export-odcs` | `<contract>` | `--context`, `--output`/`-o` | Export contract as ODCS 3.1 YAML |
| `export-dbt` | `<contract>` | `--context`, `--output`/`-o` | Export contract as a dbt `schema.yml` |
| `generate` | `<contract> <target>` | `--context` | Generate push-down validation code for `salesforce`, `js`, or `snowflake` |
| `onboard` | — | — | Launch the interactive setup wizard; first validation in ~90 seconds |
| `submit-review` | `<contract>` | `--version` (required), `--proposed-by` | Transition a DRAFT contract to REVIEW status |
| `approve` | `<contract>` | `--version` (required), `--approved-by` | Transition a REVIEW contract to ACTIVE status |
| `reject` | `<contract>` | `--version` (required), `--rejected-by`, `--reason` | Reject a REVIEW contract back to DRAFT |
| `token-generate` | `<name>` | `--role`, `--expiry-days` | Generate a Personal Access Token (PAT) for API authentication |
| `audit-verify` | — | `--db` | Verify the SHA-256 hash-chain integrity of the `contract_history` table |
| `contracts-import-dir` | `<directory>` | `--dry-run` | Import all YAML contracts from a directory |

---

## Detailed Command Reference

### `init`

Bootstraps a `contracts/` directory with a starter contract. Designed for pip users who install OpenDQV without cloning the repo.

```bash
opendqv init
# Created contracts/customer.yaml — edit it or add more contracts.
# Validate: opendqv validate customer '{"name":"Alice","email":"alice@example.com","age":30}'
```

Override the target directory:

```bash
opendqv init --dir /path/to/my/contracts
```

Use `--force` to overwrite an existing `customer.yaml`.

---

### `list`

Lists every contract in `OPENDQV_CONTRACTS_DIR`, including draft and archived contracts.

```bash
opendqv list
```

Output columns: `NAME`, `VER`, `STATUS`, `RULES`.

---

### `show <contract>`

Displays contract metadata and a formatted rule table.

```bash
opendqv show customer
# Contract: customer
# Version:  1.0.0
# Status:   active
# Owner:    data-team
# ...
```

---

### `validate <contract> <json>`

Validates a single JSON record. Prints `PASS` or `FAIL`, error count, warning count, and any failing field messages. The process exits with code `0` on pass, `1` on fail — suitable for use in shell pipelines.

```bash
opendqv validate customer '{"name":"Alice","email":"alice@example.com","age":30}'
# Result: PASS
# Errors:   0
# Warnings: 0
```

With a context overlay:

```bash
opendqv validate customer '{"name":"Alice"}' --context salesforce
```

---

### `export-gx <contract>`

Exports the contract's rules as a Great Expectations expectation suite. Output goes to stdout unless `--output` is specified.

```bash
opendqv export-gx customer --output customer_suite.json
```

---

### `import-gx <file>`

Reads a GX suite JSON file and writes a new YAML contract to `OPENDQV_CONTRACTS_DIR`. Prints the number of imported and skipped expectations.

```bash
opendqv import-gx my_suite.json
```

---

### `import-dbt <file>`

Reads a dbt `schema.yml` and creates one YAML contract per model. Each contract is saved as `DRAFT` and must be reviewed before use.

```bash
opendqv import-dbt models/schema.yml
# Saved: contracts/orders.yaml
# orders: 6 rules imported, 0 skipped
# 1 contract draft(s) saved. Review and activate each via the workbench or 'dqv approve'.
```

---

### `import-soda <file>`

Reads a Soda Core checks YAML and converts each dataset block into an OpenDQV contract.

```bash
opendqv import-soda soda_checks.yml
```

---

### `import-csv <file>`

Reads a CSV file where each row defines one rule. The contract name defaults to the CSV filename stem; override with `--name`.

```bash
opendqv import-csv rules/customer_rules.csv --name customer_v2
```

---

### `import-odcs <file>`

Reads an Open Data Contract Standard (ODCS 3.1) contract — YAML or JSON — and converts it to an OpenDQV YAML contract. The contract name is taken from `info.title` unless `--name` is provided.

```bash
opendqv import-odcs my_odcs_contract.yaml --name payments
```

---

### `export-odcs <contract>`

Exports a contract as ODCS 3.1 YAML, suitable for sharing with other platforms that consume the standard.

```bash
opendqv export-odcs customer --output customer_odcs.yaml
```

---

### `export-dbt <contract>`

Exports a contract as a dbt `schema.yml` with column-level tests.

```bash
opendqv export-dbt customer --output models/customer/schema.yml
```

---

### `generate <contract> <target>`

Generates push-down validation code from the contract's rules for deployment directly into source systems. See [Code Generation Targets](#code-generation-targets) below.

```bash
opendqv generate customer salesforce > apex/OpenDQVValidator.cls
```

---

### `onboard`

Launches an interactive wizard that guides you through creating your first contract and running your first validation. Designed to get from zero to a passing validation in under 90 seconds.

```bash
opendqv onboard
```

---

### `submit-review <contract>`

Transitions a `DRAFT` contract to `REVIEW` status. Requires `--version`.

```bash
opendqv submit-review customer --version 1.0.0 --proposed-by alice
```

---

### `approve <contract>`

Transitions a `REVIEW` contract to `ACTIVE` status. Once active, a contract is immutable — rule mutations via the API return HTTP 409.

```bash
opendqv approve customer --version 1.0.0 --approved-by bob
```

---

### `reject <contract>`

Returns a `REVIEW` contract to `DRAFT` status. Provide `--reason` to log the rejection rationale.

```bash
opendqv reject customer --version 1.0.0 --rejected-by bob --reason "missing PII rules"
```

---

### `token-generate <name>`

Generates a Personal Access Token (PAT) for authenticating against the OpenDQV API. The token is displayed once — it is not stored in recoverable form.

```bash
opendqv token-generate salesforce-prod --role validator --expiry-days 90
# Token generated for 'salesforce-prod' (role: validator)
#   Expires  : 2026-06-15 (90 days)
#   Token    : eyJ...
```

Available roles: `validator`, `reader`, `auditor`, `editor`, `approver`, `admin`. Default: `validator`. Default expiry: 365 days.

---

### `audit-verify`

Reads the `contract_history` table from the SQLite database and verifies that every entry's SHA-256 hash chain is unbroken. Exits 0 on success, 1 if any entry is invalid or the chain is broken.

```bash
opendqv audit-verify --db opendqv.db
# Verifying contract history: /home/user/opendqv.db
# ────────────────────────────────────────────────────────────
# Contract: customer
#   Entry #1 (v1.0.0, draft)   ✓ hash valid, ✓ chain link valid
#   Entry #2 (v1.0.0, review)  ✓ hash valid, ✓ chain link valid
#   Entry #3 (v1.0.0, active)  ✓ hash valid, ✓ chain link valid
# ────────────────────────────────────────────────────────────
# All 3 entries verified. Chain integrity: PASS
```

---

### `contracts-import-dir <directory>`

Validates and loads all `*.yaml` files from a directory. Use `--dry-run` to list files without loading them.

```bash
opendqv contracts-import-dir ./new_contracts --dry-run
opendqv contracts-import-dir ./new_contracts
```

---

## Code Generation Targets

The `generate` command emits a self-contained validation function that can be deployed directly into the source system. Generated code is a point-in-time snapshot of the contract rules at generation time; re-run the command after updating the contract.

| Target | Output | Use case |
|---|---|---|
| `salesforce` | Salesforce Apex class `OpenDQVValidator` | Deploy as a trigger on any Salesforce object |
| `js` | Plain JavaScript function `opendqvValidate(data)` | Browser form validation, Node.js pre-insert hooks |
| `snowflake` | Snowflake JS UDF `opendqv_validate(data ARRAY)` | Push validation into Snowflake at ingest time |

**Salesforce example:**

```bash
opendqv generate customer salesforce > force-app/main/default/classes/OpenDQVValidator.cls
```

**JavaScript example:**

```bash
opendqv generate customer js > src/validation/opendqvValidate.js
```

**Snowflake example:**

```bash
opendqv generate customer snowflake | snowsql -c my_connection -f -
```

The generated header embeds a sync reminder and the contract version so it is always clear when the snapshot was taken:

```
// Generated by OpenDQV — push-down validation snapshot
// Contract: customer v1.0.0 | Generated: 2026-03-17T10:00:00Z
// SYNC REMINDER: This class is a snapshot of the contract rules at generation
// time. Re-run if the contract has been updated:
//   opendqv generate customer salesforce
// For live governance (always in sync), see the HTTP callout integration.
```

For live, always-in-sync governance use the API integration rather than generated code. Generated code is appropriate for low-latency edge deployments or environments without outbound network access to the OpenDQV API.

---

## Worked Workflow: CSV to Active Contract

This example walks through a complete lifecycle: import rules from a spreadsheet, validate a record, promote the contract to active.

**Step 1 — Prepare a CSV rules file.**

Create `customer_rules.csv`:

```
name,type,field,min,max,pattern,severity,error_message
name_required,not_empty,name,,,,,Name is required
email_format,regex,email,,,^[^@\s]+@[^@\s]+\.[^@\s]+$,error,Invalid email address
age_range,range,age,18,120,,error,Age must be between 18 and 120
```

**Step 2 — Import the CSV.**

```bash
opendqv import-csv customer_rules.csv --name customer
# Contract: customer
# Saved to: contracts/customer.yaml
# Total rules:    3
# Imported rules: 3
# Skipped:        0
```

**Step 3 — Verify the contract was created.**

```bash
opendqv show customer
# Contract: customer
# Version:  1.0.0
# Status:   draft
# ...
#   RULE            TYPE       FIELD   SEVERITY
#   name_required   not_empty  name    error
#   email_format    regex      email   error
#   age_range       range      age     error
```

**Step 4 — Test with a valid record.**

```bash
opendqv validate customer '{"name":"Alice","email":"alice@example.com","age":30}'
# Result: PASS
# Errors:   0
# Warnings: 0
```

**Step 5 — Test with an invalid record.**

```bash
opendqv validate customer '{"name":"","email":"not-an-email","age":15}'
# Result: FAIL
# Errors:   3
# Warnings: 0
#
# Errors:
#   - [name] Name is required
#   - [email] Invalid email address
#   - [age] Age must be between 18 and 120
echo $?
# 1
```

**Step 6 — Submit for review.**

```bash
opendqv submit-review customer --version 1.0.0 --proposed-by alice
# Contract 'customer' v1.0.0 submitted for review.
#   Status   : review
#   Proposed : alice
```

**Step 7 — Approve the contract.**

```bash
opendqv approve customer --version 1.0.0 --approved-by bob
# Contract 'customer' v1.0.0 approved.
#   Status    : active
#   Approved  : bob
```

The contract is now `ACTIVE` and ready for production use. Rule mutations on an active contract are rejected until a new version is created and promoted through the same lifecycle.
