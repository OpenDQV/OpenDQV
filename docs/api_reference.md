# API Reference

Full interactive docs at `/docs` (Swagger) and `/redoc` (ReDoc) when the server is running.

---

## Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/v1/validate` | Yes | Validate a single record |
| `POST` | `/api/v1/validate/batch` | Yes | Validate a batch of records (DuckDB-powered) |
| `POST` | `/api/v1/validate/batch/file` | Yes | Validate a CSV or Parquet file (multipart upload) |
| `GET` | `/api/v1/contracts` | No | List available contracts |
| `GET` | `/api/v1/contracts/{name}` | No | Get contract detail + rules |
| `POST` | `/api/v1/contracts/{name}/rules` | Yes | Add a rule to a DRAFT contract |
| `PUT` | `/api/v1/contracts/{name}/rules/{rule_name}` | Yes | Update a rule on a DRAFT contract |
| `DELETE` | `/api/v1/contracts/{name}/rules/{rule_name}` | Yes | Delete a rule from a DRAFT contract |
| `POST` | `/api/v1/contracts/{name}/status` | Yes | Change contract lifecycle status |
| `POST` | `/api/v1/contracts/{name}/{version}/submit-review` | Yes | Submit contract for approval (DRAFT → REVIEW) |
| `POST` | `/api/v1/contracts/{name}/{version}/approve` | Yes | Approve contract (REVIEW → ACTIVE); role: approver/admin |
| `POST` | `/api/v1/contracts/{name}/{version}/reject` | Yes | Reject contract back to DRAFT; role: approver/admin |
| `GET` | `/api/v1/contracts/{name}/history` | No | Append-only hash-chained audit log of all contract changes |
| `GET` | `/api/v1/contracts/{name}/explain` | No | Plain-English description of all rules |
| `GET` | `/api/v1/contracts/{name}/lint` | No | Lint a contract — validate its YAML structure |
| `GET` | `/api/v1/contracts/{name}/quality-trend` | No | Quality trend data for a contract |
| `POST` | `/api/v1/contracts/reload` | Yes (admin) | Reload contracts from disk |
| `POST` | `/api/v1/generate` | Yes | Generate platform-specific validation code |
| `GET` | `/api/v1/stats` | Yes | Validation statistics |
| `GET` | `/api/v1/analytics/quality-trend` | Yes | Global quality trend data |
| `GET` | `/api/v1/analytics/rule-velocity` | Yes | Rule failure velocity (trend) |
| `POST` | `/api/v1/tokens/generate` | Yes (admin) | Generate a Personal Access Token |
| `POST` | `/api/v1/tokens/revoke` | Yes (admin) | Revoke a PAT by token value |
| `POST` | `/api/v1/tokens/revoke/{username}` | Yes (admin) | Revoke all tokens for a system |
| `GET` | `/api/v1/tokens` | Yes (admin) | List all tokens |
| `POST` | `/api/v1/webhooks` | Yes (editor+) | Register a webhook |
| `GET` | `/api/v1/webhooks` | Yes | List webhooks |
| `DELETE` | `/api/v1/webhooks/{id}` | Yes (editor+) | Delete a webhook |
| `GET` | `/health` | No | Health check |
| `GET` | `/metrics` | No | Prometheus metrics |
| `POST` | `/api/v1/import/gx` | Yes (editor+) | Import Great Expectations suite JSON |
| `POST` | `/api/v1/import/dbt` | Yes (editor+) | Import dbt schema.yml |
| `POST` | `/api/v1/import/soda` | Yes (editor+) | Import Soda Core checks YAML |
| `POST` | `/api/v1/import/csv` | Yes (editor+) | Import CSV rule definitions |
| `POST` | `/api/v1/import/odcs` | Yes (editor+) | Import ODCS 3.1 contract |
| `POST` | `/api/v1/import/csvw` | Yes (editor+) | Import CSV on the Web metadata |
| `POST` | `/api/v1/import/otel` | Yes (editor+) | Import OpenTelemetry semantic conventions |
| `POST` | `/api/v1/import/ndc` | Yes (editor+) | Import NDC format |
| `GET` | `/api/v1/export/odcs/{contract}` | No | Export contract as ODCS 3.1 YAML |
| `GET` | `/api/v1/trace/verify` | Yes (auditor+) | Verify trace log hash-chain integrity |
| `GET` | `/api/v1/registry` | No | Schema registry — list all contracts as versioned schemas |
| `GET` | `/api/v1/registry/{name}` | No | Schema registry — get specific schema |
| `GET` | `/api/v1/federation/events` | No | SSE stream of federation sync events |
| `*` | `/graphql` | No | GraphQL endpoint (queries + mutations) |

---

## Validation: single record

```bash
curl -s -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"contract": "customer", "record": {"email": "alice@example.com", "name": "Alice"}}'
```

Response:

```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "contract": "customer",
  "version": "1.0",
  "owner": "Data Governance"
}
```

Both `/validate` and `/validate/batch` include an `owner` field echoing the contract's owner —
route alerts and disputes to the right team without a separate contract lookup.

---

## Validation: batch

`POST /api/v1/validate/batch` expects a JSON body with a `records` key containing a list of objects:

```bash
curl -s -X POST http://localhost:8000/api/v1/validate/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "contract": "customer",
    "records": [
      {"name": "Alice", "email": "alice@example.com", "age": 30},
      {"name": "",      "email": "not-an-email",      "age": -1}
    ]
  }'
```

The response contains per-record results and a summary including `rule_failure_counts`:

```json
{
  "summary": {
    "total": 50000,
    "passed": 48912,
    "failed": 1088,
    "error_count": 1341,
    "warning_count": 0,
    "rule_failure_counts": {
      "impression_end_after_start": 847,
      "market_allowed": 193,
      "panel_id_format": 48
    }
  },
  "results": [...]
}
```

`rule_failure_counts` is sorted descending — the rule with the highest count is the most impactful
to fix upstream. Use this for triage, not individual error inspection.

---

## Importers

Migrate existing rules from external tools into OpenDQV contracts.

| Importer | Source Format | API Endpoint | CLI Command |
|----------|--------------|--------------|-------------|
| Great Expectations | GX expectation suite JSON (v0.x or v1.x) | `POST /api/v1/import/gx` | `import-gx <file.json>` |
| dbt | `schema.yml` model tests | `POST /api/v1/import/dbt` | `import-dbt <schema.yml>` |
| Soda Core | `checks for <dataset>:` YAML | `POST /api/v1/import/soda` | `import-soda <checks.yml>` |
| CSV | Spreadsheet-style rules (field, rule_type, value, severity, error_message) | `POST /api/v1/import/csv` | `import-csv <rules.csv>` |
| ODCS | Open Data Contract Standard (JSON/YAML) | `POST /api/v1/import/odcs` | `import-odcs <file>` |
| CSVW | W3C CSV on the Web metadata | `POST /api/v1/import/csvw` | — |
| OTel | OpenTelemetry semantic convention schema | `POST /api/v1/import/otel` | — |
| NDC | FDA National Drug Code (pharma) | `POST /api/v1/import/ndc` | — |

All importers return stats (total, imported, skipped) and a list of skipped items with reasons.
Pass `?save=true` to the API to persist contracts to disk and trigger a reload.
CLI import commands always save by default.

Export: `GET /api/v1/export/odcs/{contract}` — export a contract as ODCS 3.1 YAML.

---

## Authentication

All endpoints marked "Yes" in the Auth column require:

```
Authorization: Bearer <token>
```

Tokens are Personal Access Tokens (PATs) generated via the CLI or admin API.
See [docs/administration.md](administration.md) for token management.

---

## Rate limits

| Endpoint group | Default limit |
|----------------|---------------|
| `/validate`, `/validate/batch` | 300/minute |
| Other endpoints | 120/minute |
| Token management | 10/minute |

Limits are per worker. With multiple workers (`WEB_CONCURRENCY > 1`), the effective per-IP ceiling
is `WEB_CONCURRENCY × configured value`. Use `RATE_LIMIT_BACKEND=redis` or a reverse proxy for
strict per-IP enforcement. See [docs/production_deployment.md](production_deployment.md).

---

## GraphQL

A GraphQL endpoint is available at `/graphql` (Strawberry-based). It mirrors the REST API for
contract discovery and validation. Interactive playground at `/graphql` when the server is running.

---

## Related

- [Python SDK](sdk.md) — use the SDK instead of raw curl
- [Administration](administration.md) — token management and RBAC
- [Production Deployment](production_deployment.md) — reverse proxy, TLS, rate limiting
- [Webhooks](webhooks.md) — subscribe to validation events
