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

## ODCS — Open Data Contract Standard (v3.1.0)

OpenDQV exports contracts as [ODCS v3.1.0](https://bitol-io.github.io/open-data-contract-standard/latest/)
and imports ODCS v3.0.x / v3.1.0 documents. Every export validates against the official
ODCS JSON schema and passes `datacontract lint`
([datacontract-cli](https://github.com/datacontract/datacontract-cli)); this is enforced by
the test suite on all bundled contracts.

> **Before v2.4.0** the ODCS exporter emitted an `info:` block and `mustBeSatisfied`
> quality checks. That shape was not ODCS 3.x and failed `datacontract lint` on the first
> check. Re-export any contract exported with an earlier version.

### Export mapping

| OpenDQV | ODCS v3.1.0 |
|---------|-------------|
| `name` | `id`, `name`, `schema[0].name` |
| `version` | `version` |
| `status` draft / review / active / archived | `status` draft / proposed / active / deprecated (original kept in `customProperties[opendqv.status]`) |
| `description` | `description.purpose` |
| `owner`, `owner_email` | `team.name`, `team.members[].username` (role `owner`) |
| every rule | one `quality` entry: `type: custom`, `engine: opendqv`, `implementation: <rule>`, plus `name`, `severity`, `dimension`, `description` (= `error_message`) |
| `not_empty` (error) | `required: true` |
| `unique` (error) | `unique: true` |
| `regex` / `min_length` / `max_length` (error, string field) | `logicalTypeOptions.pattern` / `minLength` / `maxLength` |
| `min` / `max` / `range` (error, numeric field) | `logicalTypeOptions.minimum` / `maximum`; `logicalType: number` |
| `date_format` (error) | `logicalType: date`, `logicalTypeOptions.format` as a JDK pattern (`yyyy-MM-dd`) |
| `lookup` with `allowed_values` (error) | `quality: {type: library, metric: invalidValues, arguments.validValues, mustBe: 0}` |

Two deliberate choices:

- **Warning-severity rules are never projected onto native ODCS fields.** A native
  constraint (`required`, `logicalTypeOptions`) is a hard constraint to every other ODCS
  consumer; a soft OpenDQV warning must not become one downstream. Warnings travel only in
  the `custom` entry, with `severity: warning`.
- **A field has one `logicalType`** (numeric wins over date wins over string). Only rules
  compatible with it are projected natively — the schema forbids e.g. `pattern` on a
  number. Nothing is lost: the `custom` entry always carries the full rule.

### Import mapping

| ODCS v3.x | OpenDQV |
|-----------|---------|
| `name` (else `id`) | contract name (lower-cased, non `[a-z0-9_]` → `_`) |
| `version`, `status` | `version`; status mapped back (proposed → review, deprecated/retired → archived). The REST import always lands as `draft`. |
| `description.purpose` (else `usage`) | `description` |
| `team.name` / `team.members[0].username` (or the v3.0 member list) | `owner` / `owner_email` |
| `quality: {type: custom, engine: opendqv, implementation}` | the rule itself — authoritative; native projections on that property are ignored |
| `required: true` / `unique: true` | `not_empty` / `unique` (error) |
| `logicalTypeOptions.pattern` / `minLength` / `maxLength` | `regex` / `min_length` / `max_length` |
| `logicalTypeOptions.minimum` + `maximum` (number, integer) | `range`; one of them → `min` / `max`. `exclusiveMinimum` / `exclusiveMaximum` are treated as inclusive and reported in `skipped_checks` |
| `logicalTypeOptions.format` (date) | `date_format` (JDK pattern → strftime; unsupported letters are reported) |
| `library` `nullValues` / `duplicateValues` `mustBe: 0` | `not_empty` / `unique` (severity from the entry) |
| `library` `invalidValues` + `arguments.validValues` `mustBe: 0` | `lookup` with `allowed_values` |

Everything else — `text` and `sql` checks, `custom` checks for other engines, object-level
checks, `rowCount` / `missingValues`, percentage thresholds, `multipleOf`, date bounds — is
dataset-level or not expressible at the record boundary and is listed in `skipped_checks`.
Nothing is dropped silently. Multiple schema objects are flattened into one record contract;
a field defined twice is reported and the first definition kept.

**Import safety.** A `custom/opendqv` implementation is allow-listed against the `Rule`
model. It may not set `inherited`, `federation_tier`, `provenance`, `severity_floor` or
`lookup_auth_header` — those are engine-stamped authority and credential fields — and an
invalid rule fails the whole import with HTTP 422 rather than loading partially.

Other ODCS sections (`servers`, `slaProperties`, `roles`, `support`, `price`, `tags`,
`domain`, `tenant`) are outside OpenDQV's scope. They are returned as `_odcs_metadata` in
the API response for the caller's use and are not persisted.

> **Vocabulary note:** In ODCS and Data Mesh terminology, OpenDQV's "source system" is
> the **data producer** and each calling service is a **data consumer**. OpenDQV enforces
> quality rules at the producer boundary — before data leaves the source.

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
| ODCS v3.1.0 | `export-odcs <contract>` | Open Data Contract Standard YAML (schema-valid) |

See [dbt Integration](dbt_integration.md) for the full rule-to-test mapping and required dbt packages.
