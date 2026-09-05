# CLAUDE.md — OpenDQV AI Developer Context

This file tells Claude Code and other AI agents everything needed to work
effectively on this repository without re-exploring the codebase each session.

---

## What this project is

OpenDQV Core is an **open-source, contract-driven data quality validation platform**.
It validates records against YAML data contracts at the point of write — before
data enters the pipeline ("shift-left"). It is **not** a pipeline monitoring tool
(that's Monte Carlo) or a pipeline test framework (that's dbt/Soda).

**Version:** 2.8.0
**Stack:** FastAPI + Gunicorn/Uvicorn, Streamlit UI, SQLite/PostgreSQL, DuckDB (batch), MCP

---

## Repository layout

Since the CRT163 namespace migration (v2.1.0) all importable code lives under
the `opendqv/` package. The top-level `api/`, `core/`, `security/`, `sdk/`
directories are gone — do not look for code there.

```
opendqv/api/            REST surface. routes.py is a 41-line assembly shim that
                        mounts 9 domain sub-routers (routes_validation.py,
                        routes_contracts.py, routes_imports.py, routes_tokens.py,
                        routes_webhooks.py, routes_analytics.py,
                        routes_audit_events.py, routes_federation.py,
                        routes_profiler.py). Shared state/helpers in deps.py.
                        graphql_schema.py = GraphQL (mounted at /graphql).
opendqv/core/           Engine: validator, rule_parser, contracts, code_generator,
                        profiler, webhooks, federation, trace_log, node_health,
                        isolation_log, quality_stats, quality_analytics,
                        worker_heartbeat, onboarding, jsonschema, linter, storage
opendqv/core/importers/ 8 format importers: GX, dbt, Soda, CSV, ODCS, CSVW, OTel, NDC
opendqv/contracts/      YAML data contracts (41 bundled, 22+ industry domains) — shipped in the wheel since v2.2.4.
                        Since v2.7.0 a MIRROR of the Cloud golden library (provenance in
                        library_manifest.json at the repo root, not in the wheel); no bundled contract carries `contexts:` —
                        the worked example is examples/contexts/customer.yaml
opendqv/contracts/ref/  Lookup reference files (.txt) used by lookup rules
opendqv/sdk/            Python SDK: sync client, async client, local validator
opendqv/security/       JWT PAT auth (auth.py)
opendqv/cli.py          CLI (23 subcommands — `opendqv --help` is authoritative). opendqv/main.py = FastAPI app.
opendqv/mcp_server.py   In-process MCP server (official mcp SDK 2.x, constructor-registered handlers, stdio)
opendqv_mcp_proxy.py    Standalone REST-bridge MCP proxy (repo root, NOT in the wheel)
docs/           Markdown docs (60+ top-level, plus rules/ and security/ subdirs): integration guides, security, operations
examples/       Starter contracts + sample records by domain
scripts/        Demo, wizard, perf-test, smoke tests, diagnostics
tests/          4,900+ unit/integration tests (150+ test files)
ui/             Streamlit governance workbench (app.py ~2,800 lines)
```

**Two MCP entry points** — `opendqv/mcp_server.py` (in-process) and
`opendqv_mcp_proxy.py` (stdio REST bridge). They have independent schemas and
drift; update both on every MCP-touching change. `tests/test_v2_3_17_cluster4_proxy_parity.py`
guards tool + schema parity.

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
python -m opendqv.cli list
python -m opendqv.cli show customer
python -m opendqv.cli validate customer '{"name":"Alice","age":30}'
```

---

## Critical conventions

### Rule model field naming
- YAML contracts use aliases: `min:`, `max:` (user-facing short names)
- Python code accesses: `rule.min_value`, `rule.max_value` (canonical field names)
- Pydantic `Field(alias="min")` + `populate_by_name=True` accepts both
- Importers MUST use `"min_value"` / `"max_value"` as dict keys (not the aliases)
- Import endpoints (`opendqv/api/routes_imports.py`) MUST use `str(config.CONTRACTS_DIR)` — never hardcode `os.path.dirname(__file__)`

### Rule conditions
- `RULE_TYPES` in `core/rule_parser.py` is the closed set of rule types; `Rule()` refuses anything else at construction, `_RULE_HANDLERS` must equal it (asserted at import), the linter reads it. `min_age`/`max_age` are keys on `date_format`, not types (v2.8.0).
- `condition:` vocabulary is closed: `field`, `value`, `not_value`, `present` (bool). Unknown keys are a load error (v2.6.0).
- D10: a cross-field rule fails when its counterpart is absent/blank on both paths; the error entry carries `counterpart_missing: true`. Use `condition: {field: <counterpart>, present: true}` for "compare only when both present".
- `tests/fixtures/conformance/frozen/minimal_clean.jsonl` is frozen by hand, never regenerated; `scripts/replay_previous_corpus.py` fails the suite when a record the previous release accepted is now rejected.

### Contract lifecycle
States: `draft` → `review` → `active` | `archived`
- `reject_contract()` transitions REVIEW → DRAFT
- ACTIVE and REVIEW contracts are immutable — rule mutations return 409 (REVIEW: reject back to DRAFT to edit)
- `POST /contracts/{name}/version` creates a NEW object + `{name}_v{version}.yaml` via `ContractRegistry.create_version` — the base object is never mutated
- The file index is `_contract_paths[name][version]` — two-level, mirroring `_contracts`
- Draft contracts auto-increment version counter on rule mutations and write back to YAML

### Test isolation
- `tests/conftest.py` copies `opendqv/contracts/` to a temp dir at session start
- All test reads/writes go to the temp copy — never the live bundled directory
- Do NOT change `OPENDQV_CONTRACTS_DIR` to point at live `opendqv/contracts/`
- Tests use `AUTH_MODE=token` — always provide `auth_headers` / `approver_headers` fixtures

### Onboarding wizard
- `_is_inside_docker()` method (mockable) — checks `/.dockerenv`
- `_has_docker()` is separate — checks docker CLI availability
- All tests that patch `_has_docker=False` must also patch `_is_inside_docker=False`
- Wizard uses `contract_name` key (YAML `name:` field) for API calls, not `name` (filename stem)

### Docker notes
- `docker-compose.dev.yml` does NOT mount `ui/` — rebuild required after `ui/app.py` changes
- Token generation in smoke tests: `docker compose exec` (live container DB), not `run --rm`

### Windows portability (verified on Python 3.13.12, real hardware)
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
- SEC-010: Import/webhook/reload/token role guards — `POST /import/*`, `POST/DELETE /webhooks` require `editor`/`admin`; `POST /contracts/reload` requires `admin`; `GET/POST /tokens/*` require `admin`
- ACTIVE and REVIEW contracts are immutable — rule mutations return 409 (registry-level gate, v2.5.1)
- SEC-004 covers every field reference a rule carries (trigger fields, compare_to, cross-field names), not just `field`; `date_format.format` is a bound SQL parameter (v2.4.1)
- `CONTRACT_NAME_RE` in `core/contracts.py` is the single contract-name charset; every core write path taking a caller-supplied name checks it (v2.4.1)

---

## Key files to know

| File | What it does |
|------|-------------|
| `opendqv/main.py` | FastAPI app startup, health endpoint, lifespan, GraphQL mount |
| `opendqv/config.py` | All configuration via env vars |
| `opendqv/core/rule_parser.py` | `Rule` Pydantic model, `ContractStatus` enum |
| `opendqv/core/validator.py` | Single-record and DuckDB batch validation engine |
| `opendqv/core/contracts.py` | Contract registry, YAML load/save, version management |
| `opendqv/api/routes.py` | 41-line shim mounting 9 domain sub-routers (~67 REST endpoints total) |
| `opendqv/api/deps.py` | Shared router state, limiter, auth/validation helpers |
| `opendqv/security/auth.py` | JWT PAT auth, RBAC (admin/approver/editor/validator/auditor/reader) |
| `opendqv/core/onboarding.py` | Interactive setup wizard |
| `tests/conftest.py` | Test fixtures — sets temp contracts dir, auth tokens |
