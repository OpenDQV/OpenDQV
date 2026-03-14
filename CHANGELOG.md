# Changelog

All notable changes to OpenDQV are documented here.

## [1.0.0] - Unreleased

Initial public release.

### Core
- Single-record and batch validation engine (DuckDB-powered batch)
- 24 rule types: `regex`, `min`, `max`, `range`, `not_empty`, `min_length`, `max_length`, `date_format`, `unique`, `min_age`, `max_age`, `lookup`, `compare`, `required_if`, `age_match`, `checksum`, `cross_field_range`, `field_sum`, `forbidden_if`, `conditional_value`, `date_diff`, `ratio_check`, `conditional_lookup`, `geospatial_bounds`
- YAML data contracts with context-aware field overrides
- Contract lifecycle management (draft / review / active / archived) and version history

### API & Integrations
- FastAPI REST API with JWT PAT authentication (open / token modes)
- GraphQL API
- MCP server — exposes all six tools (`validate_record`, `validate_batch`, `list_contracts`, `get_contract`, `explain_error`, `create_contract_draft`) to Claude Desktop, Cursor, and any MCP-compatible agent framework
- MCP `create_contract_draft` write tool — agents can propose contracts; blocked from activation until human approves via review workflow
- Contract review workflow — `DRAFT → REVIEW → ACTIVE` lifecycle with `submit-review`, `approve`, `reject` endpoints; MCP-sourced drafts cannot bypass review
- Token role differentiation — `writer`, `editor`, `approver`, `auditor`, `admin` roles on PATs
- Importers: Great Expectations (v0.x + v1.x), dbt schema.yml, Soda Core checks, CSV rule definitions, ODCS 3.1, CSVW (W3C CSV on the Web), OTel semantic conventions, NDC (FDA National Drug Code)
- Webhook notifications for `validation.failed`, `validation.warning`, `batch.failed`
- Push-down code generation (Salesforce Apex, JavaScript, Snowflake UDF)
- Python SDK with guard decorator
- Federation — publish contracts to a parent node

### Tooling
- Streamlit workbench UI (Contracts, Validate, Profiler, Webhooks, Version History, CLI Guide, and more)
- CLI tool with `list`, `show`, `validate`, `generate`, `import-*`, `export-gx`, `export-odcs`, `export-dbt`, `audit-verify`, `contracts-import-dir` commands
- CLI review commands — `submit-review`, `approve`, `reject`, `token-generate` subcommands
- Onboarding wizard — Docker detection, rule inference, starter contract, first validation in under 90 seconds
- Rule profiler — analyse datasets to auto-generate contracts with suggested rules
- Prometheus metrics and monitoring dashboard
- `scripts/run_smoke_tests.sh` — 42-check smoke test suite (isolated unit tests, full HTTP stack, pip install CLI)
- `Dockerfile.smoketest` — clean-room Python 3.11 container for unit test isolation

### Fixes
- `cli.py` `token-generate` command — fixed `KeyError: 'pat'` (key is `'token'`)
- `pyproject.toml` — added `cli.py`, `config.py`, `main.py` to `packages`; previously missing, causing `ModuleNotFoundError` after `pip install`

### Deployment
- Docker Compose with dev, prod, and perf overlays
- Production serving via Gunicorn + UvicornWorker
- GitHub Actions CI/CD pipeline
- SQLite persistence for contract history and webhooks

### Performance
- ~199 req/s sustained (single worker), p50=4ms, p99=14ms, zero errors across 119K requests
- Benchmarked on Dell XPS 13 i5-7200U
