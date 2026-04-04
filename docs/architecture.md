# Architecture

## Project structure

```
OpenDQV/
├── api/
│   ├── routes.py              # FastAPI app assembly and middleware (~39 lines)
│   ├── routes_validation.py   # Validation endpoints — /validate, /validate/batch (~378 lines)
│   ├── routes_contracts.py    # Contract management — CRUD, lifecycle, audit (~840 lines)
│   ├── routes_analytics.py    # Analytics endpoints — trends, velocity (~238 lines)
│   ├── models.py              # Pydantic request/response models (~359 lines)
│   ├── graphql_schema.py      # Strawberry GraphQL schema (~237 lines)
│   └── deps.py                # FastAPI dependency injection
│
├── core/
│   ├── validator.py           # Validation engine — single-record + DuckDB batch (~1,400 lines)
│   ├── rule_parser.py         # Rule Pydantic model, YAML parsing, compiled patterns (~304 lines)
│   ├── contracts.py           # Contract registry, YAML load/save, versioning (~1,053 lines)
│   ├── code_generator.py      # Push-down code generation (Apex/JS/Snowflake/SQL) (~465 lines)
│   ├── onboarding.py          # Interactive setup wizard (~1,254 lines)
│   ├── webhooks.py            # Lifecycle webhook dispatch (~330 lines)
│   ├── federation.py          # Multi-node contract federation (~236 lines)
│   ├── trace_log.py           # Per-record validation trace log
│   ├── node_health.py         # Node health state machine
│   ├── isolation_log.py       # Federation isolation audit log
│   ├── quality_stats.py       # Validation quality statistics
│   ├── worker_heartbeat.py    # Gunicorn worker liveness tracking
│   ├── profiler.py            # Field-level data profiling
│   └── importers/             # 8 format importers (GX, dbt, Soda, CSV, ODCS, CSVW, OTel, NDC)
│
├── security/
│   └── auth.py                # JWT PAT authentication, RBAC — 6 roles (~224 lines)
│
├── sdk/
│   ├── client.py              # Synchronous Python SDK (httpx-based) (~547 lines)
│   ├── async_client.py        # Asynchronous Python SDK
│   └── local_validator.py     # Zero-network in-process validation
│
├── ui/
│   └── app.py                 # Streamlit governance workbench, 12 sections (~2,826 lines)
│
├── contracts/                 # YAML data contracts (45 active, 22+ industry domains)
│   └── ref/                   # Lookup reference files used by lookup rules
│
├── examples/
│   ├── starter_contracts/     # 17 minimal starter templates
│   └── sample_records/        # Sample records by domain
│
├── tests/                     # pytest suite (3,398+ tests, 72 test files)
│   └── conftest.py            # Fixtures — temp contracts dir, auth tokens, test isolation
│
├── docs/                      # 79 markdown integration and operations guides
│
├── scripts/
│   ├── demo_*.py              # Domain-specific demo seeders (OOH, PPDS, Salesforce, etc.)
│   ├── run_smoke_tests.sh     # Full pre-release smoke test suite (43 checks)
│   ├── perf-test.sh           # Load testing with Apache Bench
│   ├── record_demo_readme.sh  # Terminal sequence for recording the README demo GIF
│   └── diagnostics/           # Debug and diagnostic utilities
│
├── postman/                   # Postman collection + environment (all 50 endpoints)
│
├── monitoring.py              # Prometheus metrics + in-memory validation stats (~355 lines)
├── mcp_server.py              # MCP server (Claude Desktop / Cursor integration) (~1,059 lines)
├── config.py                  # All configuration via environment variables (~185 lines)
├── main.py                    # FastAPI app entry point (~212 lines)
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

YAML files in `contracts/` are the single source of truth. The API writes back to YAML
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
FastAPI (routes_validation.py)
    │
    ├─ Auth check (security/auth.py)
    ├─ Rate limit check (slowapi)
    │
    ▼
Validator (core/validator.py)
    │
    ├─ Load contract from registry (core/contracts.py)
    ├─ Apply each rule in sequence
    │   ├─ regex: compiled_pattern.match() with ReDoS timeout
    │   ├─ lookup: TTL-cached HTTP or file lookup
    │   ├─ compare: cross-field evaluation
    │   └─ ... (15 rule types)
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
| SEC-001 | `core/validator.py` | ReDoS timeout via `regex` library (0.5s default) |
| SEC-002 | `core/contracts.py` | Path traversal prevention (`pathlib.resolve()` + containment) |
| SEC-004 | `core/validator.py` | Field name SQL injection protection (parameterised DuckDB) |
| SEC-008 | `core/webhooks.py` | Webhook SSRF protection (RFC 1918 + loopback blocked) |
| SEC-009 | `security/auth.py` | Token role whitelist — unknown roles rejected with 422 |
| SEC-010 | `api/routes_contracts.py` | Role guards on import, webhook, reload, and token endpoints |
| — | `core/contracts.py` | ACTIVE contracts are immutable — rule mutations return 409 |

---

## Related

- [Quickstart](quickstart.md) — first validation in 15 minutes
- [API Reference](api_reference.md) — all 50 endpoints
- [Production Deployment](production_deployment.md) — Docker Compose, TLS, scaling
- [Security](../SECURITY.md) — threat model, deployment checklist
