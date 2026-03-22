# CLAUDE.md — OpenDQV AI Developer Context

This file tells Claude Code and other AI agents everything needed to work
effectively on this repository without re-exploring the codebase each session.

---

## What this project is

OpenDQV is an **open-source, contract-driven data quality validation platform**.
It validates records against YAML data contracts at the point of write — before
data enters the pipeline ("shift-left"). It is **not** a pipeline monitoring tool
(that's Monte Carlo) or a pipeline test framework (that's dbt/Soda).

**Version:** 1.3.2
**Stack:** FastAPI + Gunicorn/Uvicorn, Streamlit UI, SQLite/PostgreSQL, DuckDB (batch), MCP

---

## Repository layout

```
api/            FastAPI routes (routes.py ~2400 lines), models, GraphQL schema
core/           Engine: validator, rule_parser, contracts, code_generator, profiler,
                webhooks, federation, trace_log, node_health, isolation_log,
                quality_stats, worker_heartbeat, onboarding
core/importers/ 8 format importers: GX, dbt, Soda, CSV, ODCS, CSVW, OTel, NDC
contracts/      YAML data contracts (30 active, 22+ industry domains)
contracts/ref/  Lookup reference files used by lookup rules
docs/           76 markdown files: integration guides, security, operations
examples/       Starter contracts + sample records by domain
scripts/        Demo, wizard, perf-test, smoke tests, diagnostics
sdk/            Python SDK: sync client, async client, local validator
security/       JWT PAT auth (auth.py)
tests/          2,387+ unit/integration tests (39 test files)
ui/             Streamlit governance workbench (app.py ~2,500 lines)
```

---

## Commands you will use most

```bash
# Activate virtual environment
source .venv/bin/activate

# Run all tests (excluding E2E)
python -m pytest tests/ --ignore=tests/test_e2e.py -q --tb=short

# Run specific test file
python -m pytest tests/test_onboarding.py -q --tb=short

# Start the stack (Docker)
docker compose up -d

# Rebuild API image after code changes
docker compose build api && docker compose up -d --no-deps api

# Rebuild UI image after ui/app.py changes (dev.yml does NOT mount ui/)
docker compose -f docker-compose.yml build ui && docker compose up -d --no-deps ui

# Full smoke test suite (Linux + Mac)
bash scripts/run_smoke_tests.sh

# CLI
python -m cli list
python -m cli show customer
python -m cli validate customer '{"name":"Alice","age":30}'
```

---

## Critical conventions

### Rule model field naming
- YAML contracts use aliases: `min:`, `max:` (user-facing short names)
- Python code accesses: `rule.min_value`, `rule.max_value` (canonical field names)
- Pydantic `Field(alias="min")` + `populate_by_name=True` accepts both
- Importers MUST use `"min_value"` / `"max_value"` as dict keys (not the aliases)
- `api/routes.py` import endpoints MUST use `str(config.CONTRACTS_DIR)` — never hardcode `os.path.dirname(__file__)`

### Contract lifecycle
States: `draft` → `review` → `active` | `archived`
- `reject_contract()` transitions REVIEW → DRAFT
- ACTIVE contracts are immutable — rule mutations return 409
- Draft contracts auto-increment version counter on rule mutations and write back to YAML

### Test isolation
- `tests/conftest.py` copies `contracts/` to a temp dir at session start
- All test reads/writes go to the temp copy — never the live `contracts/` directory
- Do NOT change `OPENDQV_CONTRACTS_DIR` to point at live contracts/
- Tests use `AUTH_MODE=token` — always provide `auth_headers` / `approver_headers` fixtures

### Onboarding wizard
- `_is_inside_docker()` method (mockable) — checks `/.dockerenv`
- `_has_docker()` is separate — checks docker CLI availability
- All tests that patch `_has_docker=False` must also patch `_is_inside_docker=False`
- Wizard uses `contract_name` key (YAML `name:` field) for API calls, not `name` (filename stem)

### Docker notes
- `docker-compose.dev.yml` does NOT mount `ui/` — rebuild required after `ui/app.py` changes
- Token generation in smoke tests: `docker compose exec` (live container DB), not `run --rm`

### Windows portability (verified RT96 — Python 3.13.12, real hardware)
- **File encoding** — always pass `encoding="utf-8"` to `read_text()` and `write_text()` on any YAML or text file. Windows defaults to cp1252 which cannot decode bytes like `0x81` or encode characters like `→` (U+2192).
- **PID liveness** — never use `os.kill(pid, 0)` to check if a process is alive. On Windows, signal 0 is `CTRL_C_EVENT` — it sends Ctrl+C to the target process, causing `KeyboardInterrupt`. Use `_pid_alive()` from `core/onboarding.py` instead (uses `OpenProcess` on Windows, `os.kill` on Unix).
- **Shell tools** — `tee`, `fuser`, and other Unix utilities are not available on Windows. Do not use them in scripts intended to run cross-platform.
- **Event loop** — `tests/conftest.py` sets `WindowsSelectorEventLoopPolicy` on Windows. `ProactorEventLoop` (default on Windows 3.8+) has subprocess-cleanup behaviour that triggers spurious `KeyboardInterrupt` through pytest internals.

---

## Architecture decisions to preserve

1. **Stateless validation** — no session state on the hot path (`/validate`, `/validate/batch`)
2. **Contract-as-Code** — YAML is source of truth; mutations write back to YAML atomically
3. **Config via env vars** — all settings from `os.environ`, no runtime config files
4. **`config.CONTRACTS_DIR`** — the single source of truth for where contracts live; always use this, never `os.path.dirname(__file__)`
5. **`yaml.safe_load()` only** — never `yaml.load()` (security)
6. **Parameterised SQL** — DuckDB queries use `$param` binding; never f-string interpolation for user values
7. **`regex` library not `re`** — provides per-pattern timeout for ReDoS protection

---

## Security controls (do not remove)

- SEC-001: ReDoS timeout via `regex` library (0.5s default, `OPENDQV_REGEX_TIMEOUT`)
- SEC-002/006: Path traversal prevention (`pathlib.resolve()` + containment check)
- SEC-004: Field name SQL injection protection (parameterised queries)
- SEC-008: Webhook SSRF protection (RFC 1918 + loopback + link-local blocked)
- SEC-009: Token role whitelist — `/tokens/generate` rejects unknown roles with HTTP 422
- SEC-010: Import/webhook/reload role guards — `POST /import/*`, `POST/DELETE /webhooks` require `editor`/`admin`; `POST /contracts/reload` requires `admin`
- ACTIVE contracts are immutable — rule mutations return 409

---

## Key files to know

| File | What it does |
|------|-------------|
| `main.py` | FastAPI app startup, health endpoint, lifespan |
| `config.py` | All configuration via env vars (104 lines) |
| `core/rule_parser.py` | `Rule` Pydantic model, `ContractStatus` enum |
| `core/validator.py` | Single-record and DuckDB batch validation engine |
| `core/contracts.py` | Contract registry, YAML load/save, version management |
| `api/routes.py` | 50 REST endpoints (~2,400 lines) |
| `security/auth.py` | JWT PAT auth, RBAC (admin/approver/editor/validator/auditor/reader) |
| `core/onboarding.py` | Interactive setup wizard |
| `tests/conftest.py` | Test fixtures — sets temp contracts dir, auth tokens |
