# Code Generation (Push-Down Mode)

For systems that can't make HTTP calls at runtime, OpenDQV can generate validation logic
to embed directly in the target system. The generated code mirrors the contract rules exactly —
the same validation, no network dependency.

---

## Targets

| Target | Output | Use when |
|--------|--------|----------|
| `salesforce` | Apex class `OpenDQVValidator` | Salesforce trigger or flow |
| `js` | JavaScript function `opendqvValidate` | Node.js, browser, Snowpark JS |
| `snowflake` | Snowflake JavaScript UDF `opendqv_validate` | Snowflake SQL pipeline |
| `spark_sql` | Spark SQL `CASE WHEN` expression | PySpark / Databricks SQL |
| `bigquery` | BigQuery SQL validation query | BigQuery data pipeline |

---

## API

```bash
# Salesforce Apex
curl -s -X POST "http://localhost:8000/api/v1/generate" \
  -H "Authorization: Bearer <token>" \
  -G --data-urlencode "contract_name=salesforce_contact" \
     --data-urlencode "target=salesforce" \
     --data-urlencode "context=salesforce_prod"

# JavaScript (Node.js, browser, etc.)
curl -s -X POST "http://localhost:8000/api/v1/generate" \
  -H "Authorization: Bearer <token>" \
  -G --data-urlencode "contract_name=salesforce_contact" \
     --data-urlencode "target=js"

# Snowflake JavaScript UDF
curl -s -X POST "http://localhost:8000/api/v1/generate" \
  -H "Authorization: Bearer <token>" \
  -G --data-urlencode "contract_name=customer" \
     --data-urlencode "target=snowflake"

# Spark SQL
curl -s -X POST "http://localhost:8000/api/v1/generate" \
  -H "Authorization: Bearer <token>" \
  -G --data-urlencode "contract_name=customer" \
     --data-urlencode "target=spark_sql"

# BigQuery SQL
curl -s -X POST "http://localhost:8000/api/v1/generate" \
  -H "Authorization: Bearer <token>" \
  -G --data-urlencode "contract_name=customer" \
     --data-urlencode "target=bigquery"
```

---

## CLI

```bash
python -m opendqv.cli generate <contract> <target> [--context <context>]

# Examples
python -m opendqv.cli generate salesforce_contact salesforce --context salesforce_prod
python -m opendqv.cli generate customer js
python -m opendqv.cli generate customer snowflake
```

---

## Workbench

The **Code Export** tab in the Streamlit Workbench ([docs/ui.md](ui.md)) provides a UI for
generating and copying code. Select a contract, select a target, click Generate.

---

## Keeping generated code in sync

Generated code is a point-in-time snapshot of the contract rules. If you update a contract
(add a rule, change a threshold), regenerate and redeploy the embedded code.

For systems where regeneration is difficult, prefer the API approach — the validation stays
in sync automatically because the contract is the single source of truth.

---

## Related

- [API Reference](api_reference.md) — `/api/v1/generate` endpoint details
- [Salesforce integration](salesforce_integration.md) — full Apex deployment guide
- [Snowflake integration](snowflake_integration.md) — UDF deployment and usage
- [Spark integration](spark_integration.md) — Spark SQL and Databricks patterns
