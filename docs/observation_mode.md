# Observation Mode

> Available since v1.8.8.

## What it is

OpenDQV Core is the bouncer at the door — but observation mode makes it a bouncer who **watches and takes notes** instead of blocking. Full validation runs against every record. Every violation is logged, timestamped, and attributed. But nothing is rejected. HTTP 200 always. Records always pass through. The audit trail is tagged `mode=observation_only` and includes `would_have_failed: true` on records that would have been blocked under enforcement.

## When to use it

1. **New data source onboarding.** Connect a Salesforce org, Kafka topic, or API integration to OpenDQV Core in observe mode. Run for 1-2 weeks against real production traffic. Use the workbench dashboard to see exactly what would have been blocked — and which rules need tuning before you flip to enforcement.

2. **Design partner trial.** Give a prospect or internal team a working deployment against their own data. They see real violation rates, real field-level errors, real rejection percentages — without any risk of disrupting their pipeline.

3. **Compliance evidence gathering.** Regulators and auditors want to see *what your data quality actually looks like* before you commit to enforcement thresholds. Observation mode generates the evidence: "Here is what 14 days of production traffic looks like against this contract."

## How to enable

### API: `observe_only` in the request body

Add `"observe_only": true` to any `/validate` or `/validate/batch` request:

```bash
curl -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "contract": "customer",
    "record": {
      "name": "",
      "email": "not-an-email",
      "age": 12
    },
    "observe_only": true
  }'
```

The response will always be HTTP 200, regardless of violations.

### CLI: `--observe-only` flag

```bash
opendqv validate-file customer data.csv --observe-only
```

Exit code is always 0 in observation mode. A summary (passed / would have failed) and failure counts per rule are printed to stdout. The CLI runs locally; if the trace log is enabled, its entries are tagged `enforcement`, not `observation_only` — the `mode=observation_only` audit tag comes from the API.

### SDK: `observe_only=True` kwarg

```python
from opendqv.sdk import OpenDQVClient

client = OpenDQVClient(base_url="http://localhost:8000", token="odqv_...")

result = client.validate(
    contract="customer",
    record={"name": "", "email": "not-an-email", "age": 12},
    observe_only=True,
)

print(result["mode"])              # "observation_only"
print(result["would_have_failed"]) # True
print(result["valid"])             # False — `valid` reports the real outcome in both modes
print(result["errors"])            # [...] (violations are still listed)
```

## What the response looks like

```json
{
  "valid": false,
  "mode": "observation_only",
  "would_have_failed": true,
  "contract": "customer",
  "version": "0.1",
  "errors": [
    {
      "field": "email",
      "rule": "valid_email",
      "message": "Invalid email format",
      "severity": "error",
      "error_code": "OPENDQV_REGEX_VALID_EMAIL",
      "suggested_fix": "Check the expected format in the error message and match it exactly.",
      "counterpart_missing": null
    },
    {
      "field": "name",
      "rule": "name_required",
      "message": "Customer name is required",
      "severity": "error",
      "error_code": "OPENDQV_NOT_EMPTY_NAME_REQUIRED",
      "suggested_fix": "Provide a non-empty value.",
      "counterpart_missing": null
    }
  ],
  "warnings": [],
  "engine_version": "<engine-version>",
  "validated_at": "2026-03-27T10:00:00Z",
  "latency_ms": 2.1
}
```

Key fields:

- `valid` reports the real outcome, as in enforcement mode — `false` when an error-severity rule fails. Observation mode does not change `valid`; it changes what your caller does with it (log, don't block).
- `mode` is `"observation_only"` (vs `"enforcement"` in normal mode).
- `would_have_failed` is `true` if the record would have been rejected under enforcement (it equals `not valid`; on `/validate/batch` it is `true` when any record failed).
- `errors` still contains all violations — same structure as enforcement mode.

## The workbench dashboard

The Streamlit workbench shows observation-mode results alongside enforcement results:

- **Real-time panel** — live validation events stream in. Observation-mode events are tagged and visually distinct from enforcement events.
- **Historical analysis panel** — filter by mode to see observation-only traffic. Compare violation rates between observation and enforcement periods.
- **Enforcement readiness %** — the percentage of records that would have passed under enforcement. When this is consistently above your target threshold (e.g. 95%), you have evidence to switch.

Use this to build the business case: "Over 14 days, 97.3% of records would have passed. The remaining 2.7% fail on three rules — here they are, here is who owns them."

## Switching to enforcement

Remove `observe_only` from the request (or set it to `false`). The response is still HTTP 200 and `valid` still reports the real outcome, but `mode` becomes `enforcement` — your caller now refuses the write when `valid` is `false`. CLI exit code becomes 1 on failures.

## Exporting what would have been blocked

Use `--output-failures` with observation mode to write rejected records to a file:

```bash
opendqv validate-file customer data.csv \
  --observe-only \
  --output-failures rejected.csv
```

This produces a CSV of every record that would have been blocked under enforcement — useful for triage, root-cause analysis, and stakeholder review.
