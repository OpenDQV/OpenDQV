# Postgres Integration

> **Last reviewed:** 2026-03-17.

![OpenDQV + Postgres — validate before INSERT, route rejects to quarantine table](demo_postgres.gif)

*The validate-before-INSERT pattern: bad records never enter the target table — clean rows are written, rejected rows land in the quarantine table with full error context for review and remediation.*

> Covers psycopg2 v2.9+, Postgres 14+.
> For the Postgres storage backend (enterprise tier), see `core/storage.py`.

OpenDQV validates records before they reach Postgres. The pattern is simple: validate at the application layer before every INSERT, route clean records to the target table, and route rejected records to a quarantine table. No Postgres extensions required.

---

## `asset_id` Convention for Postgres

Use the fully-qualified Postgres object path as `asset_id`:

```yaml
contract:
  name: customer
  version: "1.0"
  asset_id: "postgres://prod-db.internal/analytics/public/customers"
  #          postgres://{host}/{database}/{schema}/{table}
```

---

## Installation

Install psycopg2 before running any of the code examples below:

```bash
# Recommended: install via the OpenDQV postgres extra
pip install 'opendqv[postgres]'

# Or install directly
pip install psycopg2-binary
```

---

## Prerequisites (Approach 1)

Before running the validate-before-INSERT pattern, ensure:

1. **`OPENDQV_CONTRACTS_DIR`** is set to the directory containing your YAML contracts (e.g. `export OPENDQV_CONTRACTS_DIR=./contracts`). `LocalValidator` loads contracts from this path.
2. **Quarantine schema and table exist** — the quarantine INSERT at the bottom of the `load_records` function requires the `quarantine.rejected_records` table. Run the DDL in the [Quarantine Table DDL](#quarantine-table-ddl) section below before calling `load_records` for the first time.

---

## Approach 1 — Application Layer: Validate Before INSERT

Validate records with `LocalValidator` (no API server) before writing to Postgres. This is the recommended pattern for v1: no network dependency, no latency, runs wherever your Python application runs.

```python
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from sdk.local import LocalValidator

DB_URL = os.getenv("OPENDQV_DB_URL", "postgresql://user:pass@localhost:5432/analytics")

validator = LocalValidator()  # loads contracts from $OPENDQV_CONTRACTS_DIR

conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
cur = conn.cursor()


def load_records(records: list[dict], contract: str, table: str):
    result = validator.validate_batch(records, contract=contract)
    summary = result["summary"]

    clean = [records[r["index"]] for r in result["results"] if r["valid"]]
    rejected = [
        {"record": records[r["index"]], "errors": r["errors"]}
        for r in result["results"] if not r["valid"]
    ]

    if clean:
        # NOTE: `table` and column names are interpolated directly into the SQL
        # string — they must come from a trusted whitelist, never from user input.
        # Values are parameterised (%s) and safe from injection.
        columns = ", ".join(clean[0].keys())
        placeholders = ", ".join([f"%({k})s" for k in clean[0].keys()])
        cur.executemany(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            clean,
        )
        conn.commit()

    if rejected:
        cur.executemany(
            "INSERT INTO quarantine.rejected_records (source_table, record_json, errors_json) "
            "VALUES (%s, %s::jsonb, %s::jsonb)",
            [(table, json.dumps(r["record"]), json.dumps(r["errors"])) for r in rejected],
        )
        conn.commit()

    print(
        f"Loaded {summary['passed']}/{summary['total']} records to {table}; "
        f"{summary['failed']} quarantined"
    )
```

---

## Approach 2 — API-Mode: Validate Before INSERT (with running OpenDQV API)

When you have an OpenDQV API server running, use `OpenDQVClient` instead of `LocalValidator`. The pattern is identical — swap the client.

```python
from sdk import OpenDQVClient

client = OpenDQVClient(
    os.getenv("OPENDQV_URL", "http://opendqv:8000"),
    token=os.getenv("OPENDQV_TOKEN"),
)

result = client.validate_batch(records, contract="customer")
```

---

## Approach 3 — FastAPI Decorator: Guard Before Write

Use the `guard` decorator to pre-validate on your API boundary.

```python
import os
from fastapi import FastAPI
from sdk import OpenDQVClient

app = FastAPI()
client = OpenDQVClient(os.getenv("OPENDQV_URL"), token=os.getenv("OPENDQV_TOKEN"))


@app.post("/customers")
@client.guard(contract="customer")
async def create_customer(data: dict):
    # Only runs if data passes validation — no manual validate() call needed
    cur.execute(
        "INSERT INTO customers (name, email, age) VALUES (%(name)s, %(email)s, %(age)s)",
        data,
    )
    conn.commit()
    return {"status": "created"}
```

---

## Local Development with Docker

The `docker-compose.dev.yml` overlay includes a Postgres service for local development and testing:

```bash
# Start the full stack (API + Postgres)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Start only the Postgres service (for app-layer development without the API)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d

# Connection string for local dev
# postgresql://opendqv:opendqv@localhost:5432/opendqv
```

---

## Running the Integration Tests

```bash
# Start Postgres (if not already running)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres -d

# Wait for Postgres to be ready (usually a few seconds)
until docker compose exec postgres pg_isready -U opendqv -q 2>/dev/null; do
    echo "Waiting for Postgres..."; sleep 1
done
echo "Postgres ready"

# Run Postgres integration tests
cd ~/OpenDQV && source .venv/bin/activate
pytest tests/test_postgres_integration.py -v

# Tests skip automatically if Postgres is unavailable — main CI is never broken
```

Override the connection URL with `OPENDQV_TEST_POSTGRES_URL` if needed:

```bash
OPENDQV_TEST_POSTGRES_URL="postgresql://myuser:mypass@myhost:5432/mydb" \
  pytest tests/test_postgres_integration.py -v
```

---

## Quarantine Table DDL

Run this DDL once before using the validate-before-INSERT pattern. Column names are suggestions — adapt them to your schema.

```sql
-- Create the quarantine schema if it doesn't exist
CREATE SCHEMA IF NOT EXISTS quarantine;

CREATE TABLE quarantine.rejected_records (
    id           BIGSERIAL PRIMARY KEY,
    source_table VARCHAR(255) NOT NULL,   -- name of the target table the record was destined for
    record_json  JSONB        NOT NULL,   -- the rejected record
    errors_json  JSONB        NOT NULL,   -- validation errors from OpenDQV
    rejected_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Index for querying by source table and time
CREATE INDEX ON quarantine.rejected_records (source_table, rejected_at DESC);
```

---

## Approach 4 — Native DB Trigger: Enforce at the Database Layer

This is the governance-first approach. A `BEFORE INSERT OR UPDATE` trigger calls the
OpenDQV API directly from within Postgres. Bad records are blocked at the database
layer — regardless of which application, ETL tool, or migration script is doing the
writing. The governance team owns the YAML contract; the trigger is a one-time setup.

> **Why this matters for Databricks and Snowflake migrations:**
> Both platforms have made significant investments in Postgres compatibility (Databricks
> via Neon, Snowflake via Postgres-compatible features). A trigger-enforced OpenDQV
> contract moves with the data — the validation contract is not tied to the application
> layer or any specific ETL process. This is the pattern that unblocks platform
> migrations: replace the stored-procedure DQ logic with an OpenDQV contract, attach a
> trigger, and the enforcement survives the migration intact.

### Prerequisites

Install the `http` extension (available on standard Postgres, Supabase, and most
managed providers):

```sql
CREATE EXTENSION IF NOT EXISTS http;
```

### The trigger function

```sql
CREATE OR REPLACE FUNCTION opendqv_validate()
RETURNS TRIGGER AS $$
DECLARE
  _response http_response;
  _result   JSONB;
  _contract TEXT := TG_ARGV[0];   -- passed as trigger argument
  _url      TEXT := TG_ARGV[1];   -- OpenDQV API URL, e.g. 'http://opendqv:8000'
BEGIN
  SELECT * INTO _response
  FROM http_post(
    _url || '/api/v1/validate',
    json_build_object('contract', _contract, 'record', row_to_json(NEW))::text,
    'application/json'
  );

  IF _response.status != 200 THEN
    RAISE EXCEPTION 'OpenDQV API unreachable (HTTP %): check your OpenDQV server', _response.status;
  END IF;

  _result := _response.content::JSONB;

  IF NOT (_result->>'valid')::boolean THEN
    RAISE EXCEPTION 'Validation failed: %', _result->'errors';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

### Attach to a table

```sql
-- Validates every INSERT and UPDATE against the 'customer' contract
CREATE TRIGGER customer_opendqv_validate
  BEFORE INSERT OR UPDATE ON customers
  FOR EACH ROW
  EXECUTE FUNCTION opendqv_validate('customer', 'http://opendqv:8000');
```

### Test it

```sql
-- This should pass
INSERT INTO customers (name, email, age) VALUES ('Alice', 'alice@example.com', 30);

-- This should be blocked with a validation error
INSERT INTO customers (name, email, age) VALUES ('', 'not-an-email', -1);
-- ERROR:  Validation failed: [{"field": "name", "message": "name is required"}, ...]
```

### What this unlocks

- **Any writer is validated** — psycopg2, JDBC, dbt, pgloader, a migration script,
  a manual `psql` session. It does not matter. The trigger fires before every write.
- **Contract changes take effect immediately** — update the YAML, reload the registry
  (`POST /api/v1/contracts/reload`), and the next INSERT is validated against the new
  rules. No trigger redeployment required.
- **The governance team governs** — they own the YAML. The database enforces it.
  They are not writing triggers or stored procedures.

### Async variant (Supabase / pg_net)

For non-blocking validation (fire-and-forget logging rather than write blocking),
use `pg_net` instead of `http`. Note: this does not block the INSERT — use it for
audit logging only, not enforcement.

```sql
SELECT net.http_post(
  url := 'http://opendqv:8000/api/v1/validate',
  body := json_build_object('contract', 'customer', 'record', row_to_json(NEW))::jsonb
);
```

---

## Limitations

| Limitation | Detail |
|---|---|
| Postgres backend (`OPENDQV_DB_BACKEND=postgres`) | The contract history and federation log Postgres backend is part of the enterprise tier. The application-layer validate-before-INSERT pattern (this document) works with SQLite or any database. |
| `unique` rule in batch | The `unique` rule checks within the batch only, not against existing Postgres rows. Use a `UNIQUE` constraint on the column for database-level deduplication. |
| `plpython3u` UDF | Not recommended for v1 — application-layer validation is simpler, more portable, and doesn't require the `plpython3u` extension. |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Quick start** | Approach 1 — `validate_batch` before every `executemany` / bulk INSERT |
| **Quick start** | Set `asset_id` to `postgres://{host}/{db}/{schema}/{table}` for catalog linkage |
| **Governance-first** | Approach 4 — native DB trigger calling the OpenDQV API (enforcement at DB layer, any writer validated) |
| **Platform migration** | Approach 4 trigger pattern unblocks Databricks/Snowflake migrations by removing the stored-procedure DQ dependency |
| **Community** | Postgres enterprise storage backend (contract history + federation log in Postgres) — community PR welcome |

---

## See Also

- [`pandas_integration.md`](pandas_integration.md) — validate DataFrames before writing to Postgres via pandas `to_sql()`
- [`dbt_integration.md`](dbt_integration.md) — validate seed files and staging models
- [`kafka_integration.md`](kafka_integration.md) — validate before writing Kafka-consumed records to Postgres
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
