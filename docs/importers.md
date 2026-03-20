# Import Formats

> **Format references last verified:** Great Expectations v1.x, Soda Core v3.x — 2026-03-13.
> Expectation and check names shown in the mapping tables are based on these versions.
> [GX on PyPI](https://pypi.org/project/great-expectations/) · [Soda Core on PyPI](https://pypi.org/project/soda-core/)

OpenDQV can generate contracts from existing schema definitions so you don't have to write
validation rules by hand. Each importer converts a source format into a DRAFT contract
ready for review and activation.

---

## Supported Import Formats

| Format | CLI command | API endpoint | What it imports |
|--------|-------------|--------------|-----------------|
| Great Expectations | `import-gx <file>` | `POST /api/v1/import/gx` | Expectation suites → validation rules |
| dbt schema | `import-dbt <file>` | `POST /api/v1/import/dbt` | `schema.yml` column tests → rules |
| Soda CL | `import-soda <file>` | `POST /api/v1/import/soda` | `checks.yml` → rules |
| CSV headers | `import-csv <file>` | `POST /api/v1/import/csv` | Column headers → `not_empty` rules |
| ODCS | `import-odcs <file>` | `POST /api/v1/import/odcs` | Open Data Contract Standard JSON |
| CSVW | — | `POST /api/v1/import/csvw` | W3C CSV on the Web metadata → rules |
| OTel | — | `POST /api/v1/import/otel` | OpenTelemetry semantic convention schema |
| NDC | — | `POST /api/v1/import/ndc` | FDA National Drug Code format rules (pharma) |

All importers produce contracts in **DRAFT** status with `source: "import"`. The draft must
be reviewed and activated before it can be used for production validation.

---

## Great Expectations

**Rule mapping:**

| GX expectation | OpenDQV rule type | Notes |
|----------------|-------------------|-------|
| `expect_column_values_to_not_be_null` | `not_empty` | |
| `expect_column_values_to_match_regex` | `regex` | |
| `expect_column_values_to_be_between` | `range` | |
| `expect_column_value_lengths_to_be_between` | `min_length` / `max_length` | |
| `expect_column_values_to_be_unique` | `unique` | |
| `expect_column_min_to_be_between` | `min` | |
| `expect_column_max_to_be_between` | `max` | |
| `expect_column_values_to_be_dateutil_parseable` | `date_format` | No format string |
| `expect_column_values_to_match_strftime_format` | `date_format` | Preserves format |
| `expect_column_values_to_be_in_set` | `regex` | Mapped to `^(val1\|val2)$` pattern; no native inline set support |

**`mostly` semantics:** GX's `mostly` parameter (allowing a fraction of rows to fail) has no equivalent in OpenDQV, which validates individual records. Expectations with `mostly < 1.0` are imported as `severity: warning`. When re-exported, all `warning` rules emit `mostly: 0.95` — the original `mostly` value is not preserved.

**Export format:** The exporter emits GX 1.x format (`"type"` key on expectations, `"name"` on the suite). The importer handles both GX 0.x and 1.x input.

---

## Soda Core

**What gets imported:**

| Soda check | OpenDQV rule type | Notes |
|------------|-------------------|-------|
| `missing_count(field) = 0` | `not_empty` | |
| `duplicate_count(field) = 0` | `unique` | |
| `invalid_count(field) = 0` with `valid format: email` | `regex` | Email pattern |
| `invalid_count(field) = 0` with `valid format: date/time/uuid/...` | `regex` | Format-specific pattern |
| `invalid_count(field) = 0` with `valid regex: <pattern>` | `regex` | Uses the regex directly |
| `min(field) >= N` | `min` | |
| `max(field) <= N` | `max` | |
| `min_length(field) >= N` | `min_length` | |
| `max_length(field) <= N` | `max_length` | |

**Unsupported checks (skipped with reason):**

| Soda check | Why skipped |
|------------|-------------|
| `freshness(field)` | Dataset-level time check, no record-level equivalent |
| `schema:` | Table-level schema validation |
| `failed rows:` | Multi-record SQL construct |
| `group by:` | Aggregate grouping, no record-level equivalent |
| `row_count(...)` | Dataset-level count |
| `avg_length(...)` | Dataset-level aggregate |
| `valid format: <unknown>` | Format not yet in the mapping table |

> **Aggregate vs. record semantics:** Soda checks run over a full dataset. `min(amount) >= 0` in Soda means "the dataset-level minimum is non-negative" — it passes even if one row has a negative value, as long as another row compensates. In OpenDQV, this becomes a per-record `min` rule that checks every individual record's value. This is a closer approximation for `missing_count` and `duplicate_count`, but is semantically different for `min`/`max` aggregate checks. Use Soda for dataset-level monitoring and OpenDQV for record-level write-time enforcement — they complement rather than duplicate each other.

---

## ODCS — Open Data Contract Standard (schema.quality section)

OpenDQV imports and exports the **quality and schema sections** of ODCS 3.1 contracts.
The following ODCS normative sections are **outside scope** and are silently dropped on
import unless the contract was produced by OpenDQV itself:

| ODCS normative section | Status in OpenDQV |
|------------------------|-------------------|
| `schema.quality` (not_null, unique, regex, range, min, max, min_length, max_length, date_format) | ✅ Imported and exported |
| `info` (title, version, status, description, owner) | ✅ Imported and exported |
| `sla` / `serviceLevel` | ❌ Out of scope — use your observability platform |
| `semantics` (business meaning, ontology) | ❌ Out of scope — use your data catalog |
| `infrastructure` (storage system, cloud, throughput) | ❌ Out of scope |
| `terms_of_use` (licensing, regulatory constraints) | ❌ Out of scope |
| `lineage` / upstream dependencies | ❌ Out of scope — use your lineage tool |
| Consumer ownership model | ❌ Out of scope — OpenDQV enforces at the producer boundary |

> **Vocabulary note:** In ODCS and Data Mesh terminology, OpenDQV's "source system" is
> the **data producer** and each calling service is a **data consumer**. OpenDQV enforces
> quality rules at the producer boundary — before data leaves the source.

> **Status on import:** Unlike GX and Soda importers (which always import as `draft`),
> the ODCS importer preserves the source contract's `status` field (defaulting to `active`
> if absent). This is intentional — ODCS carries explicit lifecycle status. Review the
> imported contract before activating it for production validation.

**Unsupported ODCS quality types (skipped with warning):**

| ODCS type | Reason |
|-----------|--------|
| `custom` | No standard mapping |
| `values` (enumerated) | No inline value set support; convert to `regex` manually |
| `freshness` | Dataset-level time check — no record-level equivalent |

---

## CSVW — CSV on the Web

The CSVW importer reads a [W3C CSVW](https://www.w3.org/TR/tabular-data-primer/) JSON-LD
metadata document and maps column definitions to OpenDQV rules.

> **JSON-LD limitation:** This importer handles "simple CSVW" — plain JSON with `tableSchema` and `columns` keys. JSON-LD context (`@context`), `@base` declarations, and prefixed column names are not resolved. CSVW files that rely heavily on JSON-LD context may produce empty or incomplete rule sets. Use plain CSVW JSON for best results.

**What gets imported:**

| CSVW constraint | OpenDQV rule type | Notes |
|-----------------|-------------------|-------|
| `required: true` | `not_empty` | |
| `datatype: string` + `pattern` | `regex` | |
| `datatype: integer/number` + `minimum`/`maximum`/`minInclusive`/`maxInclusive` | `range` | |
| `minExclusive`/`maxExclusive` | `range` | Treated as inclusive (cannot express strict exclusion) |
| `minLength`/`maxLength` | `min_length` / `max_length` | |
| `enum` constraints | `regex` | Mapped to `^(val1\|val2)$` pattern; no native inline set support |

**API usage:**

```bash
curl -X POST "http://localhost:8000/api/v1/import/csvw?contract_name=my_dataset&save=true" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d @my_dataset_metadata.json
```

Query parameters:
- `contract_name` — name for the resulting contract (default: `csvw_import`)
- `save=true` — write the YAML to `contracts/` and reload the registry immediately
- `created_by` — identity to record in the contract audit trail

**Example CSVW input:**

```json
{
  "@context": "http://www.w3.org/ns/csvw",
  "tableSchema": {
    "columns": [
      {"name": "customer_id", "required": true, "datatype": "string"},
      {"name": "email",       "required": true, "datatype": "string",
       "pattern": "^[^@]+@[^@]+\\.[^@]+$"},
      {"name": "age",         "required": false, "datatype": "integer",
       "minimum": 0, "maximum": 120}
    ]
  }
}
```

---

## OTel — OpenTelemetry Semantic Conventions

> **Scope — read this first:** This importer converts OTel **semantic convention specification YAML** (the format used by the OpenTelemetry project to define the spec itself) into validation rules. It does **not**:
> - Validate live OTel span/trace/metric data
> - Connect to an OTel collector pipeline
> - Replace an OTel SDK or collector
>
> The practical use case is narrow: teams who maintain an OTel-compatible schema and want to bootstrap validation rules from the OTel semconv YAML files, or who want to enforce attribute constraints at the point where telemetry data is written.
>
> If your goal is to validate telemetry payloads at write time before sending to a collector, you can author an OpenDQV contract manually using standard rule types (`not_empty`, `regex`, `range`) and use it with `POST /api/v1/validate`. The OTel importer is a bootstrapping convenience, not a production OTel integration.

**What gets imported:**

| OTel requirement level | OpenDQV rule |
|------------------------|--------------|
| `required` | `not_empty` (`error` severity) |
| `recommended` | `not_empty` (`warning` severity) |
| Known enum attributes | `regex` with allowed values pattern |
| Numeric ranges (from convention docs) | `range` |

**Enum values:** The built-in enum table (`_KNOWN_ENUMS`) was current as of OTel semconv v1.25. Values for deprecated or new attributes will go stale as the spec evolves. Review generated rules before activating them.

**API usage:**

```bash
curl -X POST "http://localhost:8000/api/v1/import/otel?contract_name=otel_http_span&save=true" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d @http_span_convention.json
```

Query parameters:
- `contract_name` — name for the resulting contract (default: `otel_telemetry`)
- `save=true` — write to `contracts/` and reload
- `created_by` — identity for audit trail

**Example OTel input:**

```json
{
  "groups": [{
    "id": "trace.http",
    "attributes": [
      {"id": "http.method",      "requirement_level": "required",    "type": "string"},
      {"id": "http.status_code", "requirement_level": "required",    "type": "int"},
      {"id": "http.url",         "requirement_level": "recommended", "type": "string"},
      {"id": "http.flavor",      "requirement_level": "optional",    "type": "string",
       "examples": ["1.0", "1.1", "2", "QUIC"]}
    ]
  }]
}
```

---

## NDC — National Drug Code (Pharma)

The NDC importer generates validation rules for pharmaceutical dispensing records per the
[FDA NDC standard](https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory).
It does not require an input file — it generates a standard rule set based on configuration.

**What gets generated:**

- NDC code presence check (`not_empty`)
- NDC format validation (`regex` matching `XXXXX-XXXX-XX`, `XXXXX-XXX-XX`, and hyphen-free 11-digit variants)
- Optional: lot number, expiry date format, quantity range rules

**API usage:**

```bash
# Minimal — generate default NDC rules
curl -X POST "http://localhost:8000/api/v1/import/ndc?contract_name=pharma_dispense&save=true" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{}'

# With configuration
curl -X POST "http://localhost:8000/api/v1/import/ndc?contract_name=pharmacy_fill&save=true" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "ndc_field": "drug_code",
    "lot_field": "lot_number",
    "expiry_field": "expiry_date",
    "quantity_field": "quantity_dispensed",
    "severity": "error"
  }'
```

Query parameters:
- `contract_name` — name for the resulting contract (default: `pharma_dispense`)
- `save=true` — write to `contracts/` and reload
- `created_by` — identity for audit trail

Configuration fields (all optional):
- `ndc_field` — name of the field containing the NDC code (default: `ndc`)
- `lot_field` — name of the lot number field; if set, adds a `not_empty` rule
- `expiry_field` — name of the expiry date field; if set, adds a `date_format` rule
- `quantity_field` — name of the dispensed quantity field; if set, adds a `min` rule (`> 0`)
- `severity` — `"error"` (default) or `"warning"`

---

## Common patterns

### Preview before saving

Omit `?save=true` to preview the generated YAML without writing anything:

```bash
curl -X POST "http://localhost:8000/api/v1/import/csvw?contract_name=preview" \
  -H "Content-Type: application/json" \
  -d @metadata.json | python -c "import sys,json; print(json.load(sys.stdin)['yaml'])"
```

The response always includes a `yaml` field with the full contract text and a `rules` list
with the parsed rules. The contract is only written to disk when `?save=true` is passed.

### Activate after import

> **Prerequisite:** the contract must exist on disk first. Pass `?save=true&contract_name=my_dataset` to the import endpoint before running the steps below.

After saving, submit for review and activate:

```bash
# Submit for review (requires editor, approver, or admin role)
curl -X POST "http://localhost:8000/api/v1/contracts/my_dataset/1.0/submit-review" \
  -H "Authorization: Bearer $EDITOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"proposed_by": "alice@example.com"}'

# Approve (requires approver or admin role)
curl -X POST "http://localhost:8000/api/v1/contracts/my_dataset/1.0/approve" \
  -H "Authorization: Bearer $APPROVER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "bob@example.com"}'
```

### CLI bulk import

To import multiple files, run the appropriate `opendqv import-*` command per file. For example, to import all dbt schema files from a directory:

```bash
for f in ./schema_exports/*.yml; do opendqv import-dbt "$f"; done
```

For other formats, substitute `import-gx`, `import-soda`, `import-csv`, or `import-odcs` as appropriate. Each importer writes the resulting contract YAML to `contracts/` when called with the API `?save=true` parameter, or use the CLI without any extra flags to auto-save to the default contracts directory.

---

## Export formats

OpenDQV can also export contracts back to external schema formats:

| Target format | CLI command | Notes |
|---------------|-------------|-------|
| dbt `schema.yml` | `export-dbt <contract>` | Produces dbt v2 column tests; use `--output` to write a file |
| ODCS 3.1 | `export-odcs <contract>` | Open Data Contract Standard JSON/YAML |

See [dbt Integration](dbt_integration.md) for the full rule-to-test mapping and required dbt packages.
