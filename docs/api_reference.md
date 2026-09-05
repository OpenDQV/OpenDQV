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
| `GET` | `/api/v1/contracts/{name}/explain` | Yes (auth-gated by default) | Plain-English description of all rules |
| `GET` | `/api/v1/contracts/{name}/explain/{field}/{rule_name}` | Yes (auth-gated by default) | Explanation, valid/invalid examples and constraint for one rule |
| `GET` | `/api/v1/contracts/{name}/jsonschema` | No | Contract projected as JSON Schema |
| `GET` | `/api/v1/contracts/{name}/versions` | No | List all versions of a contract |
| `GET` | `/api/v1/contracts/{name}/at` | No | Contract as it was at a point in time / version |
| `GET` | `/api/v1/contracts/{name}/diff` | No | Diff two versions of a contract |
| `POST` | `/api/v1/contracts/{name}/version` | Yes | Create a new version (`?new_version=`); existing version → 400 |
| `GET` | `/api/v1/contracts/{name}/lint` | No | Lint a contract — validate its YAML structure |
| `GET` | `/api/v1/contracts/{name}/quality-trend` | No | Quality trend data for a contract |
| `POST` | `/api/v1/contracts/reload` | Yes (admin) | Reload contracts from disk |
| `POST` | `/api/v1/generate` | Yes | Generate platform-specific validation code |
| `GET` | `/api/v1/stats` | Yes | Validation statistics |
| `DELETE` | `/api/v1/quality/stats` | Yes (admin) | Reset quality statistics |
| `GET` | `/api/v1/agents` | Yes | Distinct agent_id values seen |
| `GET` | `/api/v1/analytics/summary` | Yes | Analytics summary |
| `GET` | `/api/v1/analytics/rule-heatmap` | Yes | Rule failure heat-map |
| `GET` | `/api/v1/rejection-summary` | Yes | Rejection summary |
| `GET` | `/api/v1/audit/events` | Yes (auditor+) | List validation audit events |
| `GET` | `/api/v1/audit/events/{event_id}` | Yes (auditor+) | Fetch one audit event |
| `GET` | `/config` | No | Effective engine configuration |
| `GET` | `/api/v1/analytics/rule-velocity` | Yes | Rule failure velocity (trend) |
| `POST` | `/api/v1/tokens/generate` | Yes (admin) | Generate a Personal Access Token |
| `POST` | `/api/v1/tokens/revoke` | Yes (admin) | Revoke a PAT by token value |
| `POST` | `/api/v1/tokens/revoke/{username}` | Yes (admin) | Revoke all tokens for a system |
| `GET` | `/api/v1/tokens` | Yes (admin) | List all tokens |
| `POST` | `/api/v1/webhooks` | Yes (editor+) | Register a webhook |
| `GET` | `/api/v1/webhooks` | Yes | List webhooks |
| `DELETE` | `/api/v1/webhooks` | Yes (editor+) | Delete a webhook (body: `{"url": ...}`) |
| `GET` | `/health` | No | Health check |
| `GET` | `/metrics` | No | Prometheus metrics |
| `POST` | `/api/v1/import/gx` | Yes (editor+) | Import Great Expectations suite JSON |
| `POST` | `/api/v1/import/dbt` | Yes (editor+) | Import dbt schema.yml |
| `POST` | `/api/v1/import/soda` | Yes (editor+) | Import Soda Core checks YAML |
| `POST` | `/api/v1/import/csv` | Yes (editor+) | Import CSV rule definitions |
| `POST` | `/api/v1/import/odcs` | Yes (editor+) | Import ODCS v3.x contract (422 if not ODCS v3) |
| `POST` | `/api/v1/import/csvw` | Yes (editor+) | Import CSV on the Web metadata |
| `POST` | `/api/v1/import/otel` | Yes (editor+) | Import OpenTelemetry semantic conventions |
| `POST` | `/api/v1/import/ndc` | Yes (editor+) | Import NDC format |
| `GET` | `/api/v1/export/odcs/{contract}` | No | Export contract as ODCS v3.1.0 YAML |
| `GET` | `/api/v1/export/gx/{contract}` | No | Export contract as a GX expectation suite |
| `POST` | `/api/v1/profile` | Yes | Profile records and propose rules |
| `POST` | `/api/v1/profile/file` | Yes | Profile an uploaded CSV/Parquet file |
| `GET` | `/api/v1/observation/summary` | Yes | Observation-mode summary |
| `GET` | `/api/v1/observation/fields` | Yes | Observation-mode per-field stats |
| `GET` | `/api/v1/observation/trend` | Yes | Observation-mode trend |
| `GET` | `/api/v1/trace/verify` | Yes (auditor+) | Verify trace log hash-chain integrity |
| `GET` | `/api/v1/registry` | No | Schema registry — list all contracts as versioned schemas |
| `GET` | `/api/v1/registry/{name}` | No | Schema registry — get specific schema |
| `GET` | `/api/v1/federation/events` | No | SSE stream of federation sync events |
| `POST` | `/api/v1/federation/register` | Yes | Register a federated node |
| `GET` | `/api/v1/federation/status` / `health` / `log` / `sync-status` | No | Federation node status, health, log, sync state |
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
  "version": "1.1",
  "owner": "Data Governance",
  "engine_version": "<engine-version>",
  "contract_hash": "…",
  "event_id": "…",
  "validated_at": "…"
}
```

(Abridged. Each error entry carries `field`, `rule`, `message`, `severity`, `error_code`, `suggested_fix`, and `counterpart_missing` — `true` when a cross-field rule failed because its counterpart was absent or blank. See [error_codes.md](error_codes.md).)

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

Export: `GET /api/v1/export/odcs/{contract}` — export a contract as ODCS v3.1.0 YAML (schema-valid; passes `datacontract lint`).

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
