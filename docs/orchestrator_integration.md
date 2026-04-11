# Orchestrator Integration (Airflow, Prefect, Dagster)

> **Last reviewed:** 2026-03-13.
> Covers Apache Airflow ≥2.x, Prefect ≥2.x, Dagster ≥1.x.

OpenDQV as a pipeline gate — bad data blocked before it enters the DAG. Each of the three major Python orchestrators follows the same pattern: call `POST /api/v1/validate/batch` before the load step; fail the task if any records are rejected; let the orchestrator handle retry and alerting.

---

## The Pattern

```
Source data
     │
     ▼
┌────────────────────────────────┐
│  Pre-load gate                 │
│  POST /api/v1/validate/batch   │  ← OpenDQV
│  Raise / fail if rejected > 0  │
└────────────────────────────────┘
     │ (clean records only)
     ▼
Load to warehouse / lake
     │
     ▼
dbt / Soda / GX (post-load checks)
```

This means the warehouse table your downstream transforms read from is clean by construction — post-load tools like Soda or GX operate on already-validated data.

---

## Approach 1 — Apache Airflow

Use a Python callable task (`@task` or `PythonOperator`) that calls OpenDQV before the load step. Raise `AirflowException` on rejection to fail the task and trigger Airflow's retry/alert machinery.

```python
from airflow.decorators import dag, task
from airflow.exceptions import AirflowException
from datetime import datetime
import os
from opendqv.sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

@dag(schedule="@daily", start_date=datetime(2026, 1, 1), catchup=False)
def orders_ingestion():

    @task
    def extract_orders() -> list[dict]:
        # ... fetch from source system
        return [{"order_id": "o-001", "amount": 99.99, "status": "pending"}]

    @task
    def validate_with_opendqv(records: list[dict], **context) -> list[dict]:
        run_id = context["run_id"]
        client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
        # Pass record_id= on individual validate() calls for per-record correlation
        result = client.validate_batch(
            records,
            contract="orders",
        )
        summary = result["summary"]
        if summary["failed"] > 0:
            raise AirflowException(
                f"OpenDQV rejected {summary['failed']}/{summary['total']} records "
                f"(contract=orders, run_id={run_id})"
            )
        return records

    @task
    def load_to_warehouse(records: list[dict]):
        # ... write clean records to staging table
        pass

    raw = extract_orders()
    clean = validate_with_opendqv(raw)
    load_to_warehouse(clean)

orders_ingestion()
```

**Tips:**
- Set `retries=2` and `retry_delay=timedelta(minutes=5)` on the validate task to handle transient OpenDQV unavailability without failing the whole DAG
- Use `trace_id=context["run_id"]` to correlate OpenDQV trace log entries with Airflow task instances
- For large batches, chunk the records and call `validate/batch` in pages of 1000

---

## Approach 2 — Prefect

Use a Prefect `@task` with OpenDQV pre-check. Raise `prefect.exceptions.FailedRun` (or simply `raise`) on rejection.

```python
from prefect import flow, task, get_run_logger
from prefect.context import get_run_context
import os
from opendqv.sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

@task(retries=2, retry_delay_seconds=30)
def validate_with_opendqv(records: list[dict], contract: str) -> list[dict]:
    logger = get_run_logger()
    ctx = get_run_context()
    flow_run_id = str(ctx.flow_run.id) if ctx else "local"

    client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
    # Pass record_id= on individual validate() calls for per-record correlation
    result = client.validate_batch(
        records,
        contract=contract,
    )
    summary = result["summary"]

    if summary["failed"] > 0:
        logger.error(
            f"OpenDQV rejected {summary['failed']}/{summary['total']} records",
            extra={"contract": contract, "flow_run_id": flow_run_id},
        )
        raise ValueError(
            f"Data quality gate failed: {summary['failed']} records rejected "
            f"(contract={contract})"
        )

    logger.info(f"All {summary['total']} records passed OpenDQV validation")
    return records

@task
def load_to_warehouse(records: list[dict]):
    # ... write to destination
    pass

@flow(name="orders-ingestion")
def orders_ingestion_flow():
    records = [{"order_id": "o-001", "amount": 99.99, "status": "pending"}]
    clean = validate_with_opendqv(records, contract="orders")
    load_to_warehouse(clean)

if __name__ == "__main__":
    orders_ingestion_flow()
```

**Prefect-specific notes:**
- `retries=2` on the task handles transient OpenDQV failures; genuine data rejections are not retriable — add `retry_condition_fn` if you want to distinguish the two
- Prefect Cloud surfaces the logger output directly in the flow run UI, giving operators instant visibility into which contract failed
- Use `prefect.variables` or `prefect.blocks.system.Secret` for `OPENDQV_TOKEN`

---

## Approach 3 — Dagster

Use a Dagster `@asset_check` to call `POST /api/v1/validate/batch` and return `AssetCheckResult`. This integrates natively with Dagster's asset lineage and quality tracking UI.

```python
from dagster import (
    asset,
    asset_check,
    AssetCheckResult,
    AssetCheckSeverity,
    define_asset_job,
)
import os
from opendqv.sdk import OpenDQVClient

OPENDQV_URL = os.getenv("OPENDQV_URL", "http://opendqv:8000")
OPENDQV_TOKEN = os.getenv("OPENDQV_TOKEN")

@asset
def raw_orders() -> list[dict]:
    # ... fetch from source system
    return [{"order_id": "o-001", "amount": 99.99, "status": "pending"}]

@asset_check(asset=raw_orders, blocking=True)
def opendqv_orders_check(raw_orders: list[dict]) -> AssetCheckResult:
    client = OpenDQVClient(OPENDQV_URL, token=OPENDQV_TOKEN)
    result = client.validate_batch(raw_orders, contract="orders")
    summary = result["summary"]

    passed = summary["failed"] == 0
    return AssetCheckResult(
        passed=passed,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "total_records": summary["total"],
            "failed_records": summary["failed"],
            "passed_records": summary["passed"],
        },
        description=(
            f"All {summary['total']} records passed"
            if passed
            else f"{summary['failed']}/{summary['total']} records rejected by OpenDQV"
        ),
    )

@asset(deps=[raw_orders], check_specs=[opendqv_orders_check])
def clean_orders(raw_orders: list[dict]) -> list[dict]:
    # This asset only materialises after opendqv_orders_check passes (blocking=True)
    return raw_orders

orders_job = define_asset_job("orders_ingestion_job", selection=[raw_orders, clean_orders])
```

**Dagster-specific notes:**
- `blocking=True` on `@asset_check` prevents downstream assets from materialising if the check fails — this is the key guard that stops bad data propagating
- Check results appear in the Dagster UI under the asset's **Checks** tab with the metadata fields surfaced
- For large assets, use `context.log.info` in the check body to emit progress — Dagster streams logs in real time

---

## Approach 4 — Webhook Back-Channel: Run ID Correlation

Pass the orchestrator's run ID as `trace_id` to OpenDQV so that trace log entries can be correlated back to the specific DAG/flow/job run that triggered them.

| Orchestrator | Run ID source | How to pass |
|---|---|---|
| Airflow | `context["run_id"]` | `trace_id=run_id` in `validate_batch` |
| Prefect | `get_run_context().flow_run.id` | `trace_id=str(flow_run_id)` |
| Dagster | `context.run_id` | `trace_id=context.run_id` |

OpenDQV echoes `trace_id` in every validation response and logs it at `DEBUG` level. Configure your log aggregator to index on `trace_id` for cross-system joins.

---

## Approach 5 — Filter by `asset_id` (Relevant Contracts Only)

For DAGs that process multiple datasets, filter contracts by `asset_id` prefix to only validate contracts relevant to this run:

```python
import requests

def get_contracts_for_dag(asset_id_prefix: str) -> list[dict]:
    """Return contracts whose asset_id starts with the given prefix."""
    all_contracts = requests.get(
        "http://opendqv:8000/api/v1/registry",
        headers={"Authorization": "Bearer <token>"},
    ).json()
    return [
        c for c in all_contracts
        if (c.get("asset_id") or "").startswith(asset_id_prefix)
    ]

# Example: only contracts for the 'orders' domain
contracts = get_contracts_for_dag("urn:opendqv:acme:orders")
```

---

## Approach 6 — Federation-Aware

In a multi-region deployment, route validation requests to the OpenDQV instance closest to the data source:

```python
INSTANCES = {
    "eu-west": "http://opendqv-eu.internal:8000",
    "us-east": "http://opendqv-us.internal:8000",
}

def get_client_for_region(region: str) -> OpenDQVClient:
    base_url = INSTANCES.get(region, INSTANCES["us-east"])
    return OpenDQVClient(base_url, token=os.getenv("OPENDQV_TOKEN"))
```

Pass `region` as a DAG/flow parameter, or infer from the source system's location.

---

## Limitations

| Limitation | Detail |
|---|---|
| Large batch performance | `validate/batch` is synchronous; for batches > 10,000 records consider chunking into pages of 1,000 and running in parallel using the async SDK (`AsyncOpenDQVClient`) |
| Transient failures | OpenDQV unavailability should be retried; data rejections should not — add retry condition logic to distinguish the two |
| Unique rule in batch mode | The `unique` rule type requires the full batch to be present; streaming record-by-record validation cannot enforce uniqueness |

---

## Recommended Path

| Phase | Action |
|---|---|
| **Now** | Add an OpenDQV validation task/check before every warehouse load task |
| **Now** | Pass the orchestrator run ID as `trace_id` for end-to-end correlation |
| **Now** | Use `asset_id` filtering to only check contracts relevant to each DAG |
| **Planned — based on community demand** | Switch to `AsyncOpenDQVClient` for high-throughput pipelines |
| **Planned — based on community demand** | Add webhook back-channel to push OpenDQV rejections to your alerting stack |

---

## Roadmap

See [`roadmap.md`](roadmap.md) for planned orchestrator features including Airflow provider package, Prefect block, and Dagster integration library.

---

## See Also

- [`webhooks.md`](webhooks.md) — webhook configuration and HMAC signing
- [`ecosystem_reference_stack.md`](ecosystem_reference_stack.md) — layered architecture overview
- [`gx_integration.md`](gx_integration.md) — Great Expectations integration (post-load checks)
- [`soda_integration.md`](soda_integration.md) — Soda Core integration (post-load scans)
- [`dbt_integration.md`](dbt_integration.md) — dbt integration
