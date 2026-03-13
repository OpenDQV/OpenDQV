# Data Profiler — Auto-generate Contracts from Records

The OpenDQV profiler analyses a sample of your data records and automatically generates a
contract with suggested validation rules. Use it to bootstrap a contract for a new data
source without writing YAML by hand.

---

## How it works

The profiler inspects a list of records and infers rules for each field:

| Observation | Generated rule |
|-------------|----------------|
| Field always present and non-empty | `not_empty` |
| All values match an email/URL/date pattern | `regex` with the detected pattern |
| All values are numeric within a range | `range` with min/max from the sample |
| All values are the same length | `min_length` + `max_length` |
| All values match a fixed set (low cardinality) | `not_empty` (future: `allowed_values`) |
| Field is a date string | `date_format` |

The resulting contract is in **DRAFT** status. Review the generated rules — remove any that
are too strict for your data — then activate via the normal review workflow.

---

## API usage

```bash
curl -X POST "http://localhost:8000/api/v1/profile?contract_name=my_contract" \
  -H "Authorization: Bearer $WRITER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '[
    {"id": "C001", "email": "alice@example.com", "age": 32, "signup_date": "2024-01-15"},
    {"id": "C002", "email": "bob@example.com",   "age": 28, "signup_date": "2024-02-01"},
    {"id": "C003", "email": "carol@example.com", "age": 45, "signup_date": "2024-03-10"}
  ]'
```

Query parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `contract_name` | `profiled` | Name for the generated contract |
| `save` | `false` | Write the YAML to `contracts/` and reload the registry |

**Response:**

```json
{
  "contract": {
    "name": "my_contract",
    "version": "1.0",
    "status": "draft",
    "rules": [
      {"name": "id_required",          "type": "not_empty",   "field": "id"},
      {"name": "email_required",        "type": "not_empty",   "field": "email"},
      {"name": "email_format",          "type": "regex",       "field": "email",
       "pattern": "^[^@]+@[^@]+\\.[^@]+$"},
      {"name": "age_range",             "type": "range",       "field": "age",
       "min": 18.0, "max": 120.0},
      {"name": "signup_date_format",    "type": "date_format", "field": "signup_date"}
    ]
  }
}
```

---

## Save and activate

```bash
# Profile and save in one step
curl -X POST "http://localhost:8000/api/v1/profile?contract_name=customer_v2&save=true" \
  -H "Authorization: Bearer $WRITER_TOKEN" \
  -H "Content-Type: application/json" \
  -d @sample_records.json

# Review the generated YAML
cat contracts/customer_v2.yaml

# Submit for review
curl -X POST "http://localhost:8000/api/v1/contracts/customer_v2/1.0/submit-review" \
  -H "Authorization: Bearer $EDITOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"proposed_by": "alice@example.com"}'

# Approve
curl -X POST "http://localhost:8000/api/v1/contracts/customer_v2/1.0/approve" \
  -H "Authorization: Bearer $APPROVER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "bob@example.com"}'
```

---

## Tips

- **Use a representative sample.** The profiler infers rules from the data it sees. If your
  sample is too small or unrepresentative, generated rules may be too strict (e.g. a `range`
  min/max that excludes valid edge cases) or too loose (a field that is sometimes empty will
  not get a `not_empty` rule).

- **Review before activating.** Treat the generated contract as a starting point. Common
  adjustments: loosening numeric ranges, removing overly-specific regex patterns, and adding
  cross-field rules the profiler cannot infer.

- **Combine with import formats.** If you have a CSVW or dbt schema alongside your data,
  use the [importer](importers.md) first — it produces more semantically precise rules.
  The profiler is best for ad-hoc or legacy sources with no schema definition.
