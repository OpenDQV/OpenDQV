# Starter Contracts

Industry starter templates to help you get started with OpenDQV. Each file is a
complete, loadable contract in the current rule shape (`name` / `type` / `field` /
`severity` / `error_message` on every rule) with regulatory context and adaptation
notes in the comments.

These are **not** loaded by the API by default — they live here as templates. Copy
the files you need into your contracts directory (`$OPENDQV_CONTRACTS_DIR`; the
default is the bundled `opendqv/contracts/`) and the `ref/` files into its `ref/`
subdirectory, then restart or `POST /contracts/reload`.

`tests/test_starter_templates_load.py` pins that every file here loads through the
registry and passes `opendqv lint` with zero errors.

## Contracts in this directory

| File | Served as | Description | Industry |
|------|---------------|-------------|----------|
| `banking.yaml` | `banking` | Retail bank transaction — identifiers, amounts, AML screening, payment channel; PSD2 / FCA / FATF | Banking |
| `energy.yaml` | `energy` | Smart meter reading — MPRN/MPAN format, read type, consumption plausibility, settlement period | Energy |
| `financial_services.yaml` | `financial_services` | Investment trade record — ISIN, LEI, MiFID II trade reporting fields, execution venue, asset class | Financial services |
| `healthcare.yaml` | `healthcare` | Patient record — format, clinical codes (ICD-10, NHS number), physiological plausibility | Healthcare |
| `insurance.yaml` | `insurance` | Insurance claim — claim identification, financials, fraud indicators; Solvency II | Insurance |
| `logistics.yaml` | `logistics` | Shipment tracking event — carrier, routing, customs, hazmat; Incoterms 2020 | Logistics |
| `manufacturing.yaml` | `manufacturing` | Industrial sensor reading — physical plausibility, tolerance bands, quality flags, asset identification | Manufacturing |
| `pharma.yaml` | `pharma` | Clinical trial observation — CDISC SDTM-aligned, GCP-compliant, reference-range checks | Pharma |
| `public_sector.yaml` | `public_sector` | Citizen service record — identity, eligibility, case management, maker-checker approval | Public sector |
| `real_estate.yaml` | `real_estate` | Property listing and transaction — identifiers, pricing, physical attributes, EPC, tenure, SDLT | Real estate |
| `retail.yaml` | `retail` | Retail product catalogue — SKU format, GTIN check digit, pricing, inventory, reorder point | Retail |
| `social_media_age_compliance.yaml` | `social_media_age_compliance` | Age gate and identity verification audit trail for online platforms. Ofcom / ICO UK context (Online Safety Act 2023, Children's Code) | Social media / online platforms |
| `technology.yaml` | `technology` | Technology incident — ITIL 4 severity, GDPR breach notification, SOC 2 availability, SLA compliance | Technology |
| `telecoms.yaml` | `telecoms` | Call Detail Record — number formats, duration, charge, classification | Telecoms |
| `travel.yaml` | `travel` | Travel booking — PNR, IATA codes, pricing, passenger details, booking status, GDPR consent, PCI DSS | Travel |
| `universal.yaml` | `universal` | Universal starter — 16 rules exercising 9 of the 24 rule types (allowed_values, compare, max_length, min, not_empty, range, regex, required_if, unique), with comments on when to use each | General |
| `universal_benchmark.yaml` | `universal_benchmark` | 14 rules exercising 8 of the 24 rule types (compare, lookup, max_length, not_empty, range, regex, required_if, unique). The canonical performance benchmark (Core-only; not in the Cloud library) | General |

The flat-format files (everything except `universal_benchmark.yaml`) are served
under the filename stem — the `name:` inside them is not read by the loader, so
rename the file if you want a different contract name.

## Reference data

| File | Description |
|------|-------------|
| `ref/universal_status.txt` | Valid values for the `status` field: ACTIVE, INACTIVE, PENDING, SUSPENDED |
| `ref/universal_currency.txt` | A 12-code starter subset of ISO 4217 currency codes used by the benchmark contract — extend it for your markets |
| `ref/verification_methods.txt` | Approved age verification methods used by `social_media_age_compliance.yaml` |

`sample_records/` holds sample records for the **bundled library** contracts of the same
industries (`opendqv/contracts/`, exercised by the test suite) — their field names follow the
library contracts, not these templates, except `social_media_age_compliance.json` and
`universal.json` which match the templates here.

## Quick start

```bash
# Copy the benchmark contract and its reference data into your contracts directory
cp examples/starter_contracts/universal_benchmark.yaml "$OPENDQV_CONTRACTS_DIR/"
cp -r examples/starter_contracts/ref "$OPENDQV_CONTRACTS_DIR/"

# Check it before you deploy it — lint takes the contract NAME (the file is
# already in $OPENDQV_CONTRACTS_DIR); flat-format files are named by their stem
opendqv lint universal_benchmark

# The API will pick it up on next restart (or POST /contracts/reload)
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

The industry templates add `allowed_values` (inline lists), `min` / `max`,
`checksum` (GTIN), `cross_field_range` (tolerance bands and reference ranges),
`date_format` with `min_age`, and `age_match`.

Remember that a format rule never implies presence: an absent or blank field
passes `regex`, `range`, `lookup` and the rest (see
[docs/rules/core_rules.md](../../docs/rules/core_rules.md#presence-is-explicit-250)).
The templates declare `not_empty` where a field is mandatory; a format-only field
accepts blank, and `opendqv lint` reports `FORMAT_ONLY_FIELD_ACCEPTS_EMPTY` (info)
for each such field — add `not_empty` for the fields your producers must always send.
