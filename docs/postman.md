# OpenDQV Postman Collection

Explore the full OpenDQV API in Postman — no curl commands, no manual header wiring.

The collection covers all 50 endpoints across 10 folders: health, contracts, validation, batch, lifecycle, code generation, tokens, import/export, monitoring, and GraphQL.

---

## 1. Import

1. Open Postman (desktop app or web at app.getpostman.com)
2. Click **File → Import** (or drag files onto the Postman window)
3. Import **both** files from the `postman/` directory:
   - `OpenDQV.postman_collection.json`
   - `OpenDQV.postman_environment.json`

You should now see **OpenDQV API** in your Collections sidebar and **OpenDQV Local** in the Environments dropdown.

---

## 2. Environment setup

1. Select **OpenDQV Local** from the environment dropdown (top-right corner in Postman)
2. Click the eye icon to view/edit environment variables:
   - **`base_url`** — set to `http://localhost:8000` (default) or `http://localhost:8080` for the [demo Docker Compose](demo.md)
   - **`auth_token`** — leave blank if `AUTH_MODE=open` (see note below); paste a PAT if `AUTH_MODE=token`
   - **`contract_name`** — default is `customer`; change to any active contract name

---

## 3. First five requests (new developer quickstart)

Run these in order for the fastest path to understanding what OpenDQV does:

| # | Request | Folder | What you learn |
|---|---------|--------|----------------|
| 1 | **Health check** | 1. Health & Status | Confirms the API is up and shows auth_mode |
| 2 | **List all contracts** | 2. Contracts | See all loaded contracts, their status, and version |
| 3 | **Explain contract (human-readable)** | 2. Contracts | Plain-English description of every rule — no YAML required |
| 4 | **Valid customer record** | 3. Validate — Single Record | `{"valid": true, "errors": []}` — the happy path |
| 5 | **Invalid customer record — the aha moment** | 3. Validate — Single Record | Multiple field failures returned simultaneously, with actionable error messages |

The invalid record request is the **aha moment**: you see exactly what OpenDQV catches at write time that a post-load check would only surface hours later.

---

## 4. AUTH_MODE=open (dev/demo)

By default OpenDQV runs with `AUTH_MODE=open` — all callers get admin access without a token.

- Leave `auth_token` blank in the environment
- All requests will succeed without an `Authorization` header
- Never use `AUTH_MODE=open` with sensitive data or on an internet-facing node

When you're ready to switch to production auth:

```bash
# Generate a PAT (run in your terminal, or use the Tokens folder in Postman)
curl -X POST http://localhost:8000/api/v1/tokens/generate \
  -H "Content-Type: application/json" \
  -d '{"role": "admin", "label": "postman"}'
```

Copy the returned token into `auth_token` in the Postman environment, then update your `.env` to set `AUTH_MODE=token`.

---

## 5. GraphQL playground

In addition to the Postman GraphQL folder, you can use the interactive browser playground:

```
http://localhost:8000/graphql
```

The playground includes a schema explorer, autocomplete, and query history. Useful for building dashboards and custom reporting queries against the validation history.

---

## 6. Streamlit governance workbench

The Postman collection covers the REST API. For a point-and-click UI that shows contracts, validation trends, lifecycle management, and the audit trail:

```
http://localhost:8501
```

The Monitoring tab visualises the same data the `/api/v1/stats` endpoint returns.

---

## 7. Switching to the demo environment

If you're using the [demo Docker Compose](demo.md) (pre-seeded data, ports 8080/8502):

1. In the **OpenDQV Local** environment, change `base_url` to `http://localhost:8080`
2. Leave `auth_token` blank (demo runs `AUTH_MODE=open`)
3. Run **Validation statistics** (folder 9) — you should see ~690 total validations pre-seeded

---

## Folder reference

| # | Folder | Key endpoints |
|---|--------|--------------|
| 1 | Health & Status | `GET /health`, `GET /metrics` |
| 2 | Contracts | List, detail, explain, reload, history, quality-trend, diff |
| 3 | Validate — Single Record | `POST /api/v1/validate` (5 example records) |
| 4 | Validate — Batch | `POST /api/v1/validate/batch`, file upload |
| 5 | Contract Lifecycle | submit-review, approve, reject |
| 6 | Code Generation | Apex, JavaScript, Snowflake UDF |
| 7 | Tokens | Generate, list, revoke |
| 8 | Import / Export | GX, dbt, Soda, CSV, ODCS, CSVW, NDC |
| 9 | Monitoring & Registry | stats, registry list, registry item |
| 10 | GraphQL | List contracts, validation history |
