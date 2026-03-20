# Contributing to OpenDQV

Thanks for your interest in contributing to OpenDQV! This document covers everything you need to get started.

## Contributor Licence Agreement

**Before your first pull request can be merged, you must sign the [Contributor Licence Agreement](CLA.md).**

This is handled automatically — when you open your first PR, a bot will post a comment with a single-click GitHub OAuth link. It takes 30 seconds. You only sign once.

The CLA allows BGMS Consultants Ltd to offer OpenDQV under both open-source and commercial licences while you retain ownership of your contribution. See [CLA.md](CLA.md) for the full text.

Corporate contributors (contributing on behalf of an employer) should contact **opendqv@bgmsconsultants.com** before submitting.

## Development Setup

### Prerequisites

- Python 3.11+
- Docker and Docker Compose (for containerized testing)
- Node.js (only for load tests)

### Local Setup

```bash
# Clone the repo
git clone https://github.com/OpenDQV/OpenDQV.git
cd OpenDQV

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install all dependencies (runtime + dev)
pip install -r requirements-dev.txt

# Start the API
uvicorn main:app --reload

# Run tests
pytest tests/ -v
```

### Docker Setup

```bash
cp .env.example .env
docker compose up --build
docker compose exec api python -m pytest tests/ -v
```

## Running Tests

```bash
# Full suite (1,000+ tests)
pytest tests/ -v

# Specific test file
pytest tests/test_core.py -v

# Specific test class
pytest tests/test_api.py::TestValidateSingle -v

# With coverage (if pytest-cov installed)
pytest tests/ --cov=core --cov=api --cov=sdk -v
```

### Test Structure

| File | What it tests |
|------|---------------|
| `test_core.py` | Rule parser, single-record validator, batch validator (all rule types) |
| `test_contracts.py` | Contract registry, context overrides, reload |
| `test_api.py` | REST endpoints, auth, context override via API |
| `test_graphql.py` | GraphQL queries and mutations |
| `test_lifecycle.py` | Draft blocking, status changes, deprecated filtering |
| `test_sdk.py` | SDK client, guard decorator, record extraction |
| `test_clock_sync.py` | NTP clock sync module — skew thresholds, graceful network failure, timestamp format |
| `test_e2e.py` | Playwright UI tests for the Workbench (requires `pytest-playwright` + `playwright install chromium` + running stack) |
| `conftest.py` | Shared fixtures (test client, auth token, test DB) |

Tests use a temporary SQLite database and run with `AUTH_MODE=token` to exercise the full auth path.

## Smoke Tests

Pre-release gate — runs the full three-part suite locally with one command:

```bash
bash scripts/run_smoke_tests.sh
```

- **Part 1:** 1,000+ unit tests in a clean Python 3.11 container (`Dockerfile.smoketest`)
- **Part 2:** 42 HTTP checks via Docker Compose — auth modes, write guardrails, CLI, batch upload, webhook SSRF, rate limiting, federation SSE, UI
- **Part 3:** `pip install .` in a clean container, verifies the `opendqv` CLI entry point

All 43 checks must pass before the `ALL SMOKE TESTS PASSED` line appears.

**Important:** the smoke test starts its own Docker Compose stack on ports 8000 and 8501. Ensure no other stack is running on those ports before running — the script will fail immediately with a clear message if they are occupied (`docker compose down` to clear).

**Disk space:** the smoketest image requires ~4-5GB of free space on the Docker filesystem (Python 3.11 base + Playwright driver + all dependencies). Minimum 6GB free recommended. If you hit a space error, run `docker system prune -af` to remove unused images and build cache, then retry.

## Making Changes

### Before You Start

1. Check existing [issues](https://github.com/OpenDQV/OpenDQV/issues) for related work
2. For large changes, open an issue first to discuss the approach
3. Create a feature branch from `main`

### Code Guidelines

- **Python style:** Follow PEP 8. Use type hints for function signatures.
- **Keep it simple:** Only add what's needed. Don't over-engineer.
- **Write tests:** New features need tests. Bug fixes need a regression test.
- **Contracts are YAML:** Rule definitions live in `contracts/*.yaml`, not in Python code.

### Project Conventions

- **Rule types** are strings (`regex`, `min`, `max`, `range`, `not_empty`, etc.) -- not classes. This keeps YAML simple.
- **Severity** is either `error` (blocks) or `warning` (flags but allows).
- **Contexts** are per-field overrides in contracts, merged at validation time by `ContractRegistry.get_rules_with_context()`.
- **Single-record validation** is pure Python (no DuckDB). **Batch validation** uses DuckDB for performance.
- **Code generation** outputs platform-specific code (Apex/JS/Snowflake) from the same rules.

### Conditional Constraints (`condition` block)

Any rule type can be made conditional using a `condition` block. The rule is evaluated
only when the condition is met; otherwise it is silently skipped.

```yaml
# Apply only when condition field equals a specific value
- name: eu_gdpr_consent
  type: not_empty
  field: gdpr_consent
  condition:
    field: region
    value: EU
  error_message: "gdpr_consent required for EU records"

# Skip rule when condition field equals a specific value (negative condition)
- name: revenue_floor_for_charges
  type: min
  field: revenue_gbp
  min: 0
  condition:
    field: transaction_type
    not_value: CREDIT
  error_message: "revenue_gbp must be >= 0 for charge records (credits are exempt)"
```

A condition dict has exactly one of:
- `value: X` — apply rule only when `field == X`
- `not_value: X` — apply rule only when `field != X`

Works in both single-record and batch (DuckDB) modes.

### Cross-Field Rules

OpenDQV supports rules that compare two fields within the same record.

**`compare` — compare this field to another field**

```yaml
- name: impression_end_after_start
  type: compare
  field: impression_end
  compare_to: impression_start
  compare_op: gt            # gt | lt | gte | lte | eq | neq
  error_message: "impression_end must be later than impression_start"
  severity: error
```

Works with numbers, ISO 8601 date/datetime strings, and plain strings.
Both `field` and `compare_to` must be present in the record; missing either fails validation.

**`required_if` — conditionally require a field**

```yaml
- name: refresh_rate_required_for_digital
  type: required_if
  field: refresh_rate_hz
  required_if:
    field: panel_type
    value: DIGITAL
  error_message: "refresh_rate_hz is required when panel_type is DIGITAL"
  severity: error
```

If `panel_type == DIGITAL`, then `refresh_rate_hz` must be present and non-empty.
If `panel_type` is anything else, the rule is skipped.

### File-Based Lookup Rules

Validate a field value against a reference file (e.g. a list of valid panel IDs,
advertiser IDs, or market codes that change too frequently to hardcode in YAML).

**One value per line:**

```yaml
- name: panel_id_valid
  type: lookup
  field: panel_id
  lookup_file: /app/data/active_panels.txt
  error_message: "panel_id not found in active panel registry"
```

**CSV with a named column:**

```yaml
- name: panel_id_valid
  type: lookup
  field: panel_id
  lookup_file: /app/data/panels.csv
  lookup_field: panel_id       # column name in the CSV header
  error_message: "panel_id not in registry"
```

Notes:
- Files are loaded once and cached in-process. Call `_load_lookup_set.cache_clear()` to invalidate.
- For production use, mount the reference file as a Docker volume (e.g. `-v ./data:/app/data`).
- REST-based lookups with configurable TTL are planned for a future release.

### `allowed_values` — static values only

The `regex` rule type with a pipe-delimited alternation pattern is how OpenDQV
enforces a fixed allowed-values list:

```yaml
- name: market_allowed
  type: regex
  field: market
  pattern: "^(UK|DE|FR|ES)$"
  error_message: "market must be UK, DE, FR or ES"
```

> **Important:** There is no `allowed_values` rule type with dynamic/database-backed
> lookups. For a static list, use `regex` with an alternation pattern as shown above.
> For a dynamic list that changes at runtime, use `lookup` with a reference file.

### Adding a New Rule Type

1. Add the type check in `core/validator.py` -- both `_check_rule()` (single) and `_batch_check_rule()` (batch)
2. Add code generation in `core/code_generator.py` -- `_generate_salesforce()`, `_js_rule_check()`, and `_generate_snowflake()`
3. Add tests in `tests/test_core.py`
4. Update the Rule Types table in `README.md`

### Adding a New Contract

1. Create a YAML file in `contracts/`
2. Follow the `contract:` format (see `sf_contact.yaml` for a complete example)
3. Restart the API or call `POST /api/v1/contracts/reload`
4. Add sample data in `tests/sample_data/` if useful for testing

## Pull Request Process

1. **Create a feature branch** from `main`
2. **Make your changes** with tests
3. **Run the full test suite:** `pytest tests/ -v` -- all 1,000+ tests must pass
4. **Keep PRs focused** on a single change
5. **Write a clear description** of what changed and why

### PR Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] New features have tests
- [ ] No secrets or credentials committed
- [ ] README updated if adding user-facing features
- [ ] Contracts are valid YAML (API loads without errors)

## Reporting Issues

When filing an issue, include:

- Steps to reproduce the problem
- The full error message and traceback
- Your environment (Python version, OS, Docker version if applicable)
- The contract YAML and request body, if relevant (sanitise any real data)

## Architecture Notes

For contributors working on the core:

- **`main.py`** -- FastAPI app initialization, wires together router + GraphQL + metrics
- **`api/routes.py`** -- All REST endpoints. Uses `registry` (set at startup) for contract access.
- **`core/validator.py`** -- Two paths: `validate_record()` (pure Python, fast) and `validate_batch()` (DuckDB, high throughput). Both return the same result structure.
- **`core/contracts.py`** -- `ContractRegistry` loads YAML, caches in memory, handles context merging. Supports three YAML formats (contract, legacy, onboarding).
- **`security/auth.py`** -- JWT tokens stored in SQLite. `get_current_user()` is a FastAPI dependency that checks auth mode.
- **`monitoring.py`** -- Prometheus metrics via middleware + `ValidationStats` for the dashboard.

## Community Response

OpenDQV launched as a solo-maintained project. Community response is currently handled
by the maintainer. If you are a community member willing to help with issue triage,
please open a Discussion on GitHub — contributors who help with issue response are
recognised in the project.

**For maintainers:** GitHub Issues are the primary support channel. Aim for a first
response within 7 days. If you do not know the answer, say so — a "I'm looking into
this" response is better than silence.

**Issue triage labels:** `bug`, `question`, `enhancement`, `good first issue`,
`needs-investigation`. Apply on first read.

## High-Sensitivity Files

Changes to the following files require extra care and a higher bar for review:

| File | Why |
|------|-----|
| `core/validator.py` | The validation engine. Any change to rule evaluation logic must include a test for the specific case being changed — both a passing and a failing case. Pay particular attention to datetime comparison, checksum algorithms, and batch vs. single-record parity. |
| `security/auth.py` | Authentication and RBAC. Changes must not weaken token validation, role enforcement, or the revocation mechanism. |
| `api/routes.py` | All 50 endpoints. ACTIVE contract immutability guards (HTTP 409) must not be relaxed. |

If you are unsure whether your change affects these files, open a Discussion before submitting a PR.

## Licence

By contributing, you agree to the terms of the [Contributor Licence Agreement](CLA.md). BGMS Consultants Ltd may offer your contributions under open-source and/or commercial licence terms. You retain ownership of your contributions.
