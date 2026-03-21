# Changelog

All notable changes to OpenDQV are documented here.

## [1.0.2] - 2026-03-21

### Security

- **Replace `python-jose` with `PyJWT`** — `python-jose` pulled `ecdsa` as a
  transitive dependency (CVE-2024-23342, Minerva timing attack on P-256). OpenDQV
  uses `HS256` exclusively; `ecdsa` was never exercised. Migrated to `PyJWT>=2.10.0`
  which has zero extra dependencies. `ecdsa`, `pyasn1`, and `rsa` are removed from
  the dependency tree. API surface unchanged — `jwt.encode`/`jwt.decode` signatures
  are identical.
- **Starlette `FileResponse` DoS alerts dismissed** (CVE-2025-62727, CVE-2025-54121)
  — OpenDQV uses neither `FileResponse` nor `StaticFiles`. Both vulnerable code paths
  are unreachable. Alerts dismissed with documented rationale.

### Documentation

- `README.md` — added *"The shift-left distinction that actually matters"* section
  to `## Why OpenDQV?`: direct comparison table contrasting industry "shift-left" tools
  with true pre-write validation.
- `README.md` — added three-layer governance architecture table to `## What OpenDQV
  is NOT`: write-time enforcement (OpenDQV) / catalog+stewardship (Atlan, Collibra,
  Purview) / pipeline testing+observability (GX, Soda, Monte Carlo). Answers the
  most common evaluator question — how does this fit with tools we already have?

---

## [1.0.1] - 2026-03-21

### Fixes

- **`date_format` validator** — `rule.format` (strftime syntax) is now used as the
  primary format when specified. Previously the field was accepted by the Rule model
  but silently ignored; only four hardcoded formats were tried. Custom formats such as
  `'%Y-%m-%d %H:%M:%S'` (space-separated datetime, common in SQL Server exports) now
  validate correctly.
- **`/explain` endpoint** — respects `AUTH_MODE=open`. The auth check order was
  inverted: an absent token raised 401 before the auth-mode check was reached, making
  the endpoint unreachable without a token even in open mode.
- **`/validate/batch/file`** — unknown `context` values now return `422` instead of
  an unhandled exception. The try/except for `UnknownContextError` was present on
  `/validate` and `/validate/batch` but missing on the file upload endpoint.
- **`contracts/customer.yaml`** — `valid_email` regex rule now includes the email
  pattern. The rule existed but had no `pattern` field, making it a no-op that
  accepted any value including invalid emails.
- **`install.sh`** — added `PYTHON` environment variable override. Users with Python
  3.11 installed under a non-default command (e.g. `python3.11` via Homebrew on macOS)
  can now run `PYTHON=python3.11 bash install.sh` instead of failing silently.

### Documentation

- `docs/rules/explain_endpoint.md` — corrected auth behaviour: documents
  `AUTH_MODE=open` vs `AUTH_MODE=token` behaviour and `OPENDQV_EXPLAIN_PUBLIC` flag.
- `README.md` — `date_format` rule entry clarified: `format` is optional, uses Python
  strftime syntax, tried before fallback list; all four fallback formats listed.
- `docs/troubleshooting.md` — added `PYTHON=python3.11 bash install.sh` override
  under Python version troubleshooting.
- `docs/quickstart.md` — same PYTHON override tip added to Python install section.
- `docs/runbook.md` — corrected token generation curl command to current API path.

---

## [1.0.0] - 2026-03-20

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
- Token role differentiation — `validator`, `editor`, `approver`, `auditor`, `admin` roles on PATs
- Importers: Great Expectations (v0.x + v1.x), dbt schema.yml, Soda Core checks, CSV rule definitions, ODCS 3.1, CSVW (W3C CSV on the Web), OTel semantic conventions, NDC (FDA National Drug Code)
- Webhook notifications for `opendqv.validation.failed`, `opendqv.validation.warning`, `opendqv.batch.failed`
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
- `scripts/run_smoke_tests.sh` — 43-check smoke test suite (isolated unit tests, full HTTP stack, pip install CLI) with pre-flight port check
- `Dockerfile.smoketest` — clean-room Python 3.11 container for unit test isolation

### Security
- Role validation whitelist at token generation — `/tokens/generate` now rejects unknown roles (e.g. `superadmin`) with HTTP 422. Only the six defined roles (`validator`, `reader`, `auditor`, `editor`, `approver`, `admin`) are accepted.
- RBAC enforcement on import, webhook, and reload endpoints — `POST /import/*` and `POST/DELETE /webhooks` now require `editor` or `admin`; `POST /contracts/reload` requires `admin`. Previously any authenticated user could trigger these operations.
- RBAC documentation corrected — all roles can validate (no role check on `/validate`); `reader` and `validator` are semantically distinct but functionally equivalent; `auditor` additionally has access to `GET /trace/verify`.

### Audit
- NTP clock synchronisation check at startup — OpenDQV queries `pool.ntp.org` at startup and records the result (`clock_status`, `skew_ms`, `ntp_source`) in the node health log. Gives auditors evidence that timestamps were accurate when the chain was written. Graceful failure if network unavailable.
- `opendqv audit-verify` upgraded — now outputs a **Clock Synchronization** section after chain integrity. Shows clock status for every startup event; warns if skew > 5 seconds or NTP was unavailable.
- `core/clock_sync.py` — new module. Pure socket NTP query, 2-second timeout, no external dependencies. RFC 3161 trusted timestamp anchoring is the documented commercial upgrade path for regulated environments.

### Fixes
- `compare_to: now` timezone handling — sentinel now uses `datetime.now(timezone.utc)` (was naive). Timezone-aware input values (e.g. `+01:00`, `Z`) are normalised to UTC before comparison. Previously raised `TypeError` when comparing aware and naive datetimes.
- `compare_to: today`, `min_age`, `max_age` — all `datetime.today()` calls replaced with `datetime.now(timezone.utc)`. Sentinel now resolves to the current UTC date consistently regardless of server timezone setting.
- `cli.py` `token-generate` command — fixed `KeyError: 'pat'` (key is `'token'`)
- `pyproject.toml` — added `cli.py`, `config.py`, `main.py` to `packages`; previously missing, causing `ModuleNotFoundError` after `pip install`

### Deployment
- Docker Compose with dev, prod, and perf overlays
- Production serving via Gunicorn + UvicornWorker
- GitHub Actions CI/CD pipeline
- SQLite persistence for contract history and webhooks

### Performance
- ~208 req/s sustained (4 Gunicorn workers, 5-minute stabilised), p50=19ms, p99=205ms, zero errors across 218K requests
- Benchmarked on Dell XPS 13 i5-7200U (Linux, native Docker)
- macOS (i7-1068NG7, Docker Desktop): 257.3 req/s sustained over 10 minutes, zero errors across 233K requests
- ARM64 (Raspberry Pi 400): 79.1 req/s sustained over 10 minutes, zero errors across 72K requests
- Windows 10 (i7, Docker Desktop): 185.1 req/s, zero errors
