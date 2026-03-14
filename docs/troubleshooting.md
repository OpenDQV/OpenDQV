# Troubleshooting

Common errors and how to resolve them.

---

## 1. `docker compose up` exits immediately / services restart

**Symptom:** One or more services (usually `api`) start and then exit with code 0 or code 1 within seconds.

**Most likely cause:** Missing `command:` block in `docker-compose.dev.yml`. Docker Compose clears the `CMD` from the Dockerfile when an `entrypoint:` override is present. Without an explicit `command:`, the container has nothing to run.

**Fix:** Ensure `docker-compose.dev.yml` has an explicit `command:` under the `api:` service:
```yaml
services:
  api:
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Also check:** Stale Docker image. If you changed `ui/app.py` and the workbench is still showing old content, the image needs a rebuild:
```bash
docker compose -f docker-compose.yml build ui
docker compose -f docker-compose.yml up -d --no-deps ui
```

---

## 2. `Error: Contract 'x' not found`

**Symptom:** CLI or API returns a "not found" error for a contract that exists on disk.

**Cause:** The contract registry has not been reloaded since the file was added.

**Fix (API):** POST to the reload endpoint:
```bash
curl -X POST http://localhost:8000/api/v1/contracts/reload
```

**Fix (CLI):** The CLI reloads on every call — if the error persists, check that the YAML file is in the `contracts/` directory and is parseable:
```bash
python -c "import yaml; yaml.safe_load(open('contracts/mycontract.yaml'))"
```

---

## 3. Import command fails with `Error: Failed to import ... :`

**Symptom:** `opendqv import-gx`, `import-dbt`, `import-soda`, `import-csv`, or `import-odcs` exits with a readable error message.

**Common causes:**
- The input file has a field that the importer expected but did not find (e.g., missing `suite_name` in a GX suite, missing `models:` in a dbt schema)
- The input file contains a rule type the importer does not recognise
- The YAML/JSON is structurally valid but semantically incomplete

**Fix:** Inspect the error message — it will name the specific field or issue. For GX suites: GX 0.x suites use `expectation_suite_name`; GX 1.x suites use `name`. Both are accepted on import. The export endpoint emits GX 1.x format (`name`). For dbt schemas, ensure at least one model has `columns:` defined. For ODCS, ensure `apiVersion` and `kind` are present.

---

## 4. Validation returns unexpected `FAIL` for a field that looks correct

**Symptom:** A record fails validation on a field where the value appears to meet the rule.

**Diagnosis steps:**
1. Check which rule is firing: `opendqv validate <contract> '<json>' --context <ctx>` and read the error detail.
2. Check if a context override is changing the rule: some contracts define stricter rules for specific contexts (e.g., `salesforce`, `kids_app`).
3. Check for whitespace: `not_empty` passes but `regex` rules often fail on leading/trailing spaces.

---

## 5. `409 Conflict: Contract is ACTIVE — create a new version to modify rules`

**Symptom:** An API call to add, update, or delete a rule returns 409.

**Cause:** ACTIVE contracts are immutable by design. This is an intentional write guardrail.

**Fix:** To modify rules on an ACTIVE contract, fork it first:
```bash
curl -X POST http://localhost:8000/api/v1/contracts/<name>/version \
  -H "Content-Type: application/json" \
  -d '{"bump": "minor", "created_by": "your-name"}'
```
This creates a new DRAFT at the next version number. Edit the DRAFT, then submit for review and approve.

---

## 6. MCP server not responding to agent calls

**Symptom:** An AI agent using the MCP integration reports the tool is unavailable or returns an error.

**Check 1:** Is the MCP server running?
```bash
python mcp_server.py
```
It must be running as a separate process from the main API.

**Check 2:** Is the API reachable from the MCP server? The MCP server calls the OpenDQV REST API internally. Ensure `API_URL` is correctly set in the environment (default: `http://localhost:8000`).

**Check 3:** For write operations, confirm the MCP server is configured with `ALLOW_MCP_WRITES=true` and that you have reviewed the write guardrail documentation before enabling writes.

---

## 7. `UI_ACCESS_TOKEN is not set, but the API is running in token mode`

**Symptom:** The Streamlit workbench shows an "Access Restricted" error instead of the normal UI.

**Cause:** The API is configured with `AUTH_MODE=token` (maker-checker enforcement) but the workbench has no `UI_ACCESS_TOKEN` set. This configuration is blocked intentionally — an unsecured workbench in front of a secured API is a misconfiguration.

**Fix:** Set `UI_ACCESS_TOKEN` in your `.env` file and restart the UI service:
```bash
echo 'UI_ACCESS_TOKEN=your-secret-token' >> .env
docker compose up -d --no-deps ui
```

---

## 8. `audit-verify` reports `chain link BROKEN`

**Symptom:** `opendqv audit-verify --db opendqv.db` shows one or more entries with `chain link BROKEN`.

**Cause:** A row in `contract_history` has a `prev_hash` that does not match the `entry_hash` of the preceding row. This indicates either database corruption or manual row manipulation.

**Action:** Do not attempt to repair the chain manually. Preserve the database as evidence and restore from a known-good backup. If no backup exists, contact your security team — a broken audit chain is a security event.

---

## 9. Tests fail with `OPENDQV_DB_PATH` errors

**Symptom:** Running `pytest` produces errors related to SQLite database access.

**Fix:** These variables are set automatically by `conftest.py` — no manual export needed. Just run:
```bash
python -m pytest tests/ --ignore=tests/test_e2e.py -q
```
E2e tests require a running stack and Playwright — see `CONTRIBUTING.md` for setup.

---

## 10. `import-odcs` produces a contract with no rules

**Symptom:** After running `opendqv import-odcs <file>`, the generated YAML contract has an empty rules list.

**Cause:** The ODCS file's `schema[*].properties[*].quality` array is empty or absent. The importer maps ODCS quality checks to OpenDQV rules — if there are no quality checks, there are no rules.

**Fix:** Add quality checks to the ODCS schema properties, or use the wizard (`opendqv onboard`) to build the contract from scratch with guided rule assignment. The imported DRAFT will be saved to `contracts/` — you can also add rules manually via the workbench or API after import.
