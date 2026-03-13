# Starter Contracts

Example data contracts to help you get started with OpenDQV.

These are **not** loaded by the API by default — they live here as templates.
Copy the files you need into your `contracts/` directory (and the `ref/` files
into `contracts/ref/`) before deploying.

## Contracts in this directory

| File | Description | Industry |
|------|-------------|----------|
| `universal_benchmark.yaml` | 14-rule contract covering all core rule types. Used as the canonical performance benchmark. | General |
| `social_media_age_compliance.yaml` | Age-gate and identity verification audit trail for online platforms. Ofcom/ICO UK context (Online Safety Act 2023, Children's Code). | Social Media / Online Platforms |

## Reference data

| File | Description |
|------|-------------|
| `ref/universal_status.txt` | Valid values for the `status` field: ACTIVE, INACTIVE, PENDING, SUSPENDED |
| `ref/universal_currency.txt` | ISO 4217 currency codes used by the benchmark contract |
| `ref/verification_methods.txt` | Approved age verification methods used by `social_media_age_compliance.yaml` |

## Quick start

```bash
# Copy the benchmark contract and its reference data into your contracts directory
cp examples/starter_contracts/universal_benchmark.yaml contracts/
cp -r examples/starter_contracts/ref contracts/

# The API will pick them up on next restart (or SIGHUP)
```

## Rule types demonstrated

The `universal_benchmark.yaml` contract demonstrates:

- `not_empty` — required field check
- `max_length` — field length limit
- `regex` — pattern matching (email, phone, date)
- `compare` — compare_to:today for date validation
- `lookup` — reference file validation (status, currency)
- `range` — numeric bounds check
- `unique` — uniqueness within a batch
- `required_if` — conditional required field
