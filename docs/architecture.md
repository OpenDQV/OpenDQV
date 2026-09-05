# Architecture

## Project structure

```
OpenDQV/
├── opendqv/                   # The installable package — all importable code lives here
│   ├── api/
│   │   ├── routes.py              # Assembly shim — mounts the 9 domain sub-routers below
│   │   ├── routes_validation.py   # /validate, /validate/batch
│   │   ├── routes_contracts.py    # Contract CRUD, lifecycle, versioning, audit
│   │   ├── routes_imports.py      # /import/* (8 format importers)
│   │   ├── routes_tokens.py       # /tokens/* (PAT issue / revoke)
│   │   ├── routes_webhooks.py     # /webhooks
│   │   ├── routes_analytics.py    # Trends, velocity, quality metrics
│   │   ├── routes_audit_events.py # Audit event read surface
│   │   ├── routes_federation.py   # Multi-node federation
│   │   ├── routes_profiler.py     # /profile, /profile/file
│   │   ├── models.py              # Pydantic request/response models
│   │   ├── graphql_schema.py      # Strawberry GraphQL schema (mounted at /graphql)
│   │   └── deps.py                # Shared router state, limiter, auth/validation helpers
│   │
│   ├── core/
│   │   ├── validator.py           # Validation engine — single-record + DuckDB batch
│   │   ├── rule_parser.py         # Rule Pydantic model, YAML parsing, compiled patterns
│   │   ├── contracts.py           # Contract registry, YAML load/save, versioning, history
│   │   ├── code_generator.py      # Push-down code generation (Apex/JS/Snowflake/SQL)
│   │   ├── onboarding.py          # Interactive setup wizard
│   │   ├── webhooks.py            # Lifecycle webhook dispatch
│   │   ├── federation.py          # Multi-node contract federation
│   │   ├── trace_log.py           # Per-record validation trace log (hash chain + HMAC)
│   │   ├── node_health.py         # Node health state machine
│   │   ├── isolation_log.py       # Federation isolation audit log
│   │   ├── quality_stats.py       # Validation quality statistics
│   │   ├── quality_analytics.py   # DuckDB OLAP analytics layer
│   │   ├── worker_heartbeat.py    # Gunicorn worker liveness tracking
│   │   ├── profiler.py            # Field-level data profiling
│   │   ├── linter.py              # Contract linter
│   │   ├── jsonschema.py          # Contract → JSON Schema projection
│   │   ├── storage.py             # History backends (SQLite / PostgreSQL)
│   │   └── importers/             # 8 format importers (GX, dbt, Soda, CSV, ODCS, CSVW, OTel, NDC)
│   │
│   ├── contracts/                 # Bundled YAML contracts — a mirror of the OpenDQV Cloud
│   │   │                          #   golden library; provenance in repo-root library_manifest.json (not in the wheel)
│   │   └── ref/                   # Lookup reference files used by lookup rules
│   │
│   ├── security/
│   │   └── auth.py                # JWT PAT authentication, RBAC — 6 roles
│   │
│   ├── sdk/
│   │   ├── client.py              # Synchronous + asynchronous Python SDK (httpx-based)
│   │   └── local.py               # Zero-network in-process validation (LocalValidator)
│   │
│   ├── cli.py                     # CLI (23 subcommands)
│   ├── config.py                  # All configuration via environment variables
│   ├── main.py                    # FastAPI app entry point, lifespan, /health, GraphQL mount
│   ├── monitoring.py              # Prometheus metrics + in-memory validation stats
│   └── mcp_server.py              # In-process MCP server (official mcp SDK, stdio)
│
├── opendqv_mcp_proxy.py       # Standalone REST-bridge MCP proxy (repo root, not in the wheel)
│
├── ui/
│   └── app.py                 # Streamlit governance workbench
│
├── examples/
│   ├── starter_contracts/     # Minimal starter templates
│   ├── contexts/              # Worked `contexts:` example (customer.yaml)
│   └── <domain>/              # Sample records + starter contract by domain
│
├── tests/                     # pytest suite (see CLAUDE.md for the current count)
│   └── conftest.py            # Fixtures — temp contracts dir, auth tokens, test isolation
│
├── docs/                      # Markdown integration and operations guides
│
├── scripts/
│   ├── demo_*.py              # Domain-specific demo seeders (OOH, PPDS, Salesforce, etc.)
│   ├── run_smoke_tests.sh     # Full pre-release smoke test suite
│   ├── perf-test.sh           # Load testing with Apache Bench
│   └── diagnostics/           # Debug and diagnostic utilities
│
├── postman/                   # Postman collection + environment
│
├── docker-compose.yml         # Production stack (API + UI + PostgreSQL)
├── docker-compose.dev.yml     # Development stack (hot-reload API)
└── docker-compose.demo.yml    # Demo stack (ports 8080/8502, pre-seeded data)
```

---

## Architecture principles

**1. Stateless validation hot path**

`POST /validate` and `POST /validate/batch` carry no session state. Each request loads the
contract from the in-memory registry and returns a result. This means any number of instances
can run behind a load balancer with no coordination.

**2. Contract-as-Code**

YAML files in `config.CONTRACTS_DIR` (the bundled `opendqv/contracts/` by default) are the single source of truth. The API writes back to YAML
atomically on every mutation. The in-memory registry is rebuilt from disk on reload.

**3. Config via environment variables**

All settings come from environment variables via `config.py`. No runtime config files,
no config DB. `.env` is the deployment artifact.

**4. DuckDB for batch**

Single-record validation runs the Python rule engine. Batch validation (> ~100 records)
uses DuckDB — contracts are compiled to SQL and executed as a single query. This gives
batch validation 10-100× better throughput than iterating the Python engine.

**5. Layer 1 only**

OpenDQV validates well-formed structured records at the write boundary. It does not:
- Sit in pipelines as an observer
- Monitor data at rest
- Store records or payloads
- Infer schema from data

---

## Data flow

```
Source system
    │
    │  POST /validate
    ▼
FastAPI (opendqv/api/routes_validation.py)
    │
    ├─ Auth check (opendqv/security/auth.py)
    ├─ Rate limit check (slowapi)
    │
    ▼
Validator (opendqv/core/validator.py)
    │
    ├─ Load contract from registry (opendqv/core/contracts.py)
    ├─ Apply each rule in sequence
    │   ├─ regex: unanchored pattern.search() with ReDoS timeout (anchor with ^…$)
    │   ├─ lookup: TTL-cached HTTP or file lookup
    │   ├─ compare: cross-field evaluation
    │   └─ ... (every rule type in the dispatch table — see rules.md)
    │
    ├─ Collect errors + warnings
    ├─ Write analytics event (fire-and-forget, SQLite async)
    │
    ▼
Response: {valid, errors, warnings, contract, version, owner}
    │
    ├─ If validation.failed → webhook notify (background task)
    └─ Prometheus metric increment
```

---

## Security controls

| Control | Where | Description |
|---------|-------|-------------|
| SEC-001 | `core/validator.py` | ReDoS timeout via `regex` library (0.5s default, `OPENDQV_REGEX_TIMEOUT`) |
| SEC-002 | `core/contracts.py` | Path traversal prevention (`pathlib.resolve()` + containment) |
| SEC-004 | `core/validator.py`, `core/rule_parser.py` | SQL injection protection — every field reference a rule carries (`field`, trigger fields, `compare_to`, cross-field names) is parameterised; `date_format.format` is a bound SQL parameter |
| SEC-006 | `core/importers/` | Path traversal prevention on importer `lookup_file` paths |
| SEC-008 | `core/webhooks.py` | Webhook SSRF protection (RFC 1918 + loopback + link-local blocked) |
| SEC-009 | `security/auth.py` | Token role whitelist — unknown roles rejected with 422 |
| SEC-010 | `api/deps.py` + every sub-router | Role guards — `POST /import/*`, `POST/DELETE /webhooks` require `editor`/`admin`; `POST /contracts/reload` and `/tokens/*` require `admin` |
| SEC-011 | `core/validator.py`, `config.py` | `lookup_auth_header` secret substitution — `OPENDQV_LOOKUP_` prefix allowlist, disabled under `AUTH_MODE=open`, egress host allowlist (`OPENDQV_LOOKUP_EGRESS_ALLOWLIST`) |
| — | `core/contracts.py` | `CONTRACT_NAME_RE` — the single contract-name charset; every core write path taking a caller-supplied name checks it |
| — | `core/contracts.py` | ACTIVE and REVIEW contracts are immutable — rule mutations return 409 (registry-level gate); reject REVIEW → DRAFT to edit |

---

## Related

- [Quickstart](quickstart.md) — first validation in 15 minutes
- [API Reference](api_reference.md) — all REST endpoints
- [Production Deployment](production_deployment.md) — Docker Compose, TLS, scaling
- [Security](../SECURITY.md) — threat model, deployment checklist
