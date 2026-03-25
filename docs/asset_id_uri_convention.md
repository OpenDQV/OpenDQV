# Asset ID URI Convention

OpenDQV contracts can carry an `asset_id` field that links them to a data catalog entry (Collibra, Atlan, DataHub, Alation, OpenMetadata, or any custom catalog).

This document specifies the URI convention and provides starter examples.

---

## Why asset IDs matter

When a contract carries an `asset_id`, consumers can:

1. **Discover quality signals automatically** — catalog tools can call `/api/v1/quality-trend/{contract}` using the `asset_id` as a lookup key.
2. **Close the lineage loop** — validation failures are traceable from the physical column back to the business glossary term.
3. **Enable self-service data products** — data mesh consumers can evaluate contract health before subscribing to a domain product.

---

## URI Format

> **Note:** The `opendqv://` URI scheme shown in this document is an optional internal convention.
> When integrating with a specific data catalog (Collibra, Purview, DataHub, OpenMetadata, Atlan),
> use that catalog's native identifier format as the `asset_id` — this enables direct asset lookup
> without translation. See the individual integration guides for catalog-specific formats.

```
opendqv://<catalog>/<domain>/<entity>[/<subentity>][?version=<semver>]
```

| Segment        | Description |
|---------------|-------------|
| `catalog`     | The catalog system: `collibra`, `datahub`, `atlan`, `alation`, `openmetadata`, `marmot`, `local` |
| `domain`      | Business domain owning the data: `finance`, `customer`, `product`, `logistics`, etc. |
| `entity`      | The dataset, table, or topic name |
| `subentity`   | Optional: sub-table, partition, or stream topic |
| `?version`    | Optional: contract version this ID was assigned at |

### Examples

```
opendqv://collibra/finance/settlements/daily_batch
opendqv://datahub/customer/crm_contacts
opendqv://atlan/product/inventory_snapshots?version=1.2
opendqv://openmetadata/logistics/shipment_events
opendqv://marmot/customer/crm_contacts
opendqv://local/marketing/email_campaigns
```

### External catalog native IDs

If your catalog uses its own UUID or URN, use the catalog's native scheme:

```
# Collibra asset UUID
urn:collibra:asset:7c3a1f5e-bb12-4a2c-b9d4-9e3f1a2c3d4e

# DataHub URN
urn:li:dataset:(urn:li:dataPlatform:bigquery,finance.settlements,PROD)

# Alation object ID
alation://schema.123/table.456
```

---

## Contract YAML — adding asset_id

```yaml
contract:
  name: settlements_daily
  version: "1.0"
  description: Daily settlement batch — T+1 reconciliation
  owner: payments-team@example.com
  asset_id: "opendqv://collibra/finance/settlements/daily_batch"
  rules:
    - name: settlement_id_not_empty
      type: not_empty
      field: settlement_id
      error_message: Settlement ID is required
    - name: amount_positive
      type: range
      field: amount
      min: 0.01
      error_message: Settlement amount must be positive
```

---

## Starter Contract Examples with Asset IDs

### Customer domain

```yaml
contract:
  name: customer_master
  version: "1.0"
  description: Customer master record — CRM and billing canonical source
  owner: customer-data@example.com
  asset_id: "opendqv://datahub/customer/crm_contacts"
  rules:
    - name: email_format
      type: regex
      field: email
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
      error_message: Invalid email format

    - name: phone_e164
      type: regex
      field: phone
      pattern: "^\\+[1-9]\\d{7,14}$"
      severity: warning
      error_message: Phone should be in E.164 format

    - name: date_of_birth_past
      type: compare
      field: date_of_birth
      compare_to: today
      compare_op: lte
      error_message: Date of birth cannot be in the future
```

### Financial transactions domain

```yaml
contract:
  name: payment_transaction
  version: "1.0"
  description: Payment transaction record — MiFIR T+1 compliant
  owner: payments-team@example.com
  asset_id: "opendqv://collibra/finance/payments/transactions"
  rules:
    - name: transaction_id_uuid
      type: regex
      field: transaction_id
      pattern: "builtin:uuid"
      error_message: Transaction ID must be a valid UUID

    - name: amount_positive
      type: range
      field: amount
      min: 0.01
      error_message: Amount must be positive

    - name: currency_iso4217
      type: allowed_values
      field: currency
      values: [GBP, EUR, USD, JPY, CHF, SEK, NOK, DKK]
      error_message: Currency must be ISO 4217

    - name: isin_checksum
      type: checksum
      field: instrument_isin
      checksum_algorithm: isin_mod11
      severity: warning
      error_message: ISIN check digit invalid
```

### Healthcare / clinical domain

```yaml
contract:
  name: patient_encounter
  version: "1.0"
  description: Patient encounter record — EMA clinical trial compliant
  owner: clinical-data@example.com
  asset_id: "opendqv://openmetadata/clinical/patient_encounters"
  sensitive_fields: [patient_id, date_of_birth, diagnosis_code]
  rules:
    - name: nhs_number_checksum
      type: checksum
      field: nhs_number
      checksum_algorithm: nhs_mod11
      error_message: Invalid NHS number (mod-11 check digit failure)

    - name: encounter_date_not_future
      type: compare
      field: encounter_date
      compare_to: today
      compare_op: lte
      error_message: Encounter date cannot be in the future

    - name: diagnosis_code_not_empty
      type: not_empty
      field: diagnosis_code
      error_message: Diagnosis code is required
```

### IoT / telemetry domain

```yaml
contract:
  name: smart_meter_reading
  version: "1.0"
  description: Half-hourly smart meter settlement data — DUOS compliant
  owner: grid-data@example.com
  asset_id: "opendqv://local/energy/smart_meter_readings"
  rules:
    - name: mpan_format
      type: regex
      field: mpan
      pattern: "^\\d{13}$"
      error_message: MPAN must be 13 digits

    - name: reading_positive
      type: range
      field: kwh_reading
      min: 0
      error_message: kWh reading cannot be negative

    - name: settlement_period_range
      type: range
      field: settlement_period
      min: 1
      max: 50
      error_message: Settlement period must be 1–50

    - name: unique_per_meter
      type: unique
      field: settlement_period
      group_by: [mpan]
      error_message: Duplicate settlement period for this MPAN
```

---

## Downstream Consumer Lineage (`downstream_consumers`)

For Marmot integrations, contracts can declare the downstream consumers of their validated asset. This completes the full lineage graph in Marmot — from source through the OpenDQV validation job to the consuming dashboards, models, or services.

```yaml
contract:
  name: customer_master
  asset_id: "mrn://dataset/opendqv/customer_master"
  downstream_consumers:
    - "mrn://dataset/tableau/sales_dashboard"
    - "mrn://dataset/dbt/customer_mart"
    - "mrn://dataset/looker/revenue_explore"
```

Marmot MRN format for common consumer types:

| Consumer type | MRN format |
|---|---|
| Tableau workbook / view | `mrn://dataset/tableau/{workbook_or_view_name}` |
| dbt model | `mrn://dataset/dbt/{model_name}` |
| Looker Explore | `mrn://dataset/looker/{explore_name}` |
| Another OpenDQV asset | `mrn://dataset/opendqv/{contract_name}` |
| Generic dataset | `mrn://dataset/{provider}/{name}` |

`scripts/push_quality_lineage.py` reads `downstream_consumers` and stitches direct `downstream` edges in Marmot automatically. The target MRN must already exist in Marmot's catalog before running the script — register the consumer asset in Marmot first.

---

## Catalog Visibility Control (`catalog_visible`)

Set `catalog_visible: false` on any contract that should be excluded from Marmot catalog discovery:

```yaml
contract:
  name: internal_test_contract
  asset_id: "mrn://dataset/opendqv/internal_test_contract"
  catalog_visible: false   # exclude from Marmot push and discover_data
```

Effects:
- `push_quality_lineage.py` skips the contract — it is never pushed to Marmot.
- `marmot_proxy.py` filters it from `discover_data` MCP responses at runtime.

Omitting the field (or setting `catalog_visible: true`) keeps the default visible behaviour. Setting `catalog_visible: false` does **not** remove an asset that was already pushed to Marmot — delete it via the Marmot UI or REST API if needed.

---

## Catalog Integration Pattern

Once your contracts carry `asset_id` fields, catalog tools can query OpenDQV quality signals:

```python
# Example: Collibra custom attribute population
import httpx

def sync_quality_score_to_collibra(asset_id: str, contract: str, token: str):
    r = httpx.get(
        f"https://opendqv.internal:8000/api/v1/quality-trend/{contract}?days=7",
        headers={"Authorization": f"Bearer {token}"},
    )
    trend = r.json()
    avg_pass_rate = sum(p["pass_rate"] for p in trend["points"]) / len(trend["points"])

    # Update Collibra attribute
    collibra_client.update_asset_attribute(
        asset_id=asset_id.replace("opendqv://collibra/", ""),
        attribute="data_quality_score",
        value=round(avg_pass_rate * 100, 1),
    )
```
