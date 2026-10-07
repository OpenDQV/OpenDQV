# Contexts — removed in 3.0.0

> Last reviewed: 2026-10-07

OpenDQV 2.x let a contract declare a `contexts:` block: named sets of rule
overrides (for example `kids_app`, `salesforce_prod`) that a validate call
selected with `context=`. **3.0.0 removes the feature.** In the same release
the `contract:` wrapper around a contract document is removed: a contract is a
flat YAML document.

---

## Why

- **One contract per context gives each variant its own version history and
  audit lineage.** A context override changed which rules ran without changing
  the contract's version, and a context name that the contract did not declare
  quietly fell back to the base rules. A separate contract
  (`salesforce_lead_web_form`) has its own versions, its own approval
  workflow, its own hashes and its own row in every audit event.
- **The managed engine refuses the block too.** Core now refuses it at the
  same point with the same error code, so a contract means the same thing on
  both engines (see [`contract_conformance.md`](contract_conformance.md)).

---

## What is refused

A contract that declares a `contexts:` block (any value, even `{}`) is refused
at load. The file shows up in `load_failures`, `POST /import/*` returns 422,
`opendqv fork` refuses it and `opendqv lint` reports code
`CONTRACT_CONTEXTS_UNSUPPORTED` with this message:

```
contract_contexts_unsupported: This contract declares a 'contexts' block, which OpenDQV Core does not support. Remove the 'contexts' block, or publish one contract per context (e.g. 'salesforce_lead_web_form') so each has its own version history and audit lineage.
```

A bare `contexts:` with no value (YAML null) is tolerated.

A contract wrapped in a top-level `contract:` key is refused the same way
(lint code `CONTRACT_ENVELOPE_UNSUPPORTED`):

```
contract_envelope_unsupported: contract YAML uses the legacy top-level 'contract:' wrapper — remove the wrapper so name/version/rules are top-level fields.
```

Removed surfaces: `?context=` on `GET /api/v1/contracts/{name}`,
`GET /api/v1/contracts/{name}/jsonschema`, `POST /api/v1/generate`,
`GET /api/v1/export/gx/{name}`, `GET /api/v1/export/odcs/{name}` and
`POST /api/v1/validate/batch/file`; the `contexts` field on the contract
response; `context_warning` on validate responses; the GraphQL
`contract { contexts }` field and the `context` argument on the `validate` /
`validateBatch` mutations; the `context` argument on the MCP `get_contract`
and `get_contract_jsonschema` tools; the CLI `--context` flag on `validate`,
`validate-file`, `export-gx`, `export-odcs`, `export-dbt` and `generate`; and
`LocalValidator(...).validate(context=)`.

Hashes (`content_hash`, `entry_hash`, `contract_hash`, `effective_rule_hash`,
the manifest's `rules_sha256`) are unchanged for contracts that never declared
contexts.

---

## `context` on validate is still accepted — as a tag

`context` on `POST /api/v1/validate` and `POST /api/v1/validate/batch` (and on
the MCP `validate_record` / `validate_batch` tools and the SDK client's
`validate(context=...)`) stays. It is a caller-supplied tag recorded with
quality stats, the audit event and metrics. **It never changes which rules
run.**

```bash
curl -s -X POST http://localhost:8000/api/v1/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"contract": "customer", "context": "demo", "record": {"name": "Alice", "age": 30, "email": "alice@example.com"}}'
# → validates against customer's own rules; the quality_stats row and audit event carry context="demo"
```

Tagged traffic can be filtered or grouped
(`GET /api/v1/contracts/{name}/quality-trend?context=demo`, or `by=context`)
and removed after a demo:

```bash
# Delete all quality_stats rows with context="demo" (admin role required)
curl -s -X DELETE "http://localhost:8000/api/v1/quality/stats?context=demo" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# → {"deleted": 42, "context": "demo"}
```

---

## Migrating a contract that declared contexts

For each context, copy the contract as `<contract>_<context>`, set `name:` to
the new name, and fold that context's overrides into its rules. Then remove
the `contexts:` block from the original. Callers that sent
`"context": "web_form"` now send `"contract": "salesforce_lead_web_form"`
(they can keep sending `context` as a tag if they want it in stats).

In 2.x an override keyed by a rule name changed that one rule; an override
keyed by a field name changed every rule on that field; any other key added a
new rule. Fold each override into the rule (or rules) it was written for.

**Before (2.x) — `salesforce_lead.yaml`:**

```yaml
name: salesforce_lead
version: "1.0"
owner: Sales Operations
status: active
rules:
  - name: email_required
    field: Email
    type: not_empty
    severity: error
    error_message: Email is required for lead routing and dedup.
  - name: email_not_personal
    field: Email
    type: regex
    pattern: '@(gmail|yahoo|hotmail|outlook|aol|icloud)\.com$'
    negate: true
    severity: warning
    error_message: Personal email detected — business email preferred for B2B leads.
  - name: lead_source_required
    field: LeadSource
    type: not_empty
    severity: error
    error_message: LeadSource is required for attribution tracking.
contexts:
  web_form:
    email_not_personal:
      severity: error
      error_message: Web form leads must use a business email address.
```

**After (3.0.0) — two flat files.** `salesforce_lead.yaml` is the same file
without the `contexts:` block. `salesforce_lead_web_form.yaml`:

```yaml
name: salesforce_lead_web_form
version: "1.0"
owner: Sales Operations
status: active
rules:
  - name: email_required
    field: Email
    type: not_empty
    severity: error
    error_message: Email is required for lead routing and dedup.
  - name: email_not_personal
    field: Email
    type: regex
    pattern: '@(gmail|yahoo|hotmail|outlook|aol|icloud)\.com$'
    negate: true
    severity: error                     # folded from contexts.web_form
    error_message: Web form leads must use a business email address.
  - name: lead_source_required
    field: LeadSource
    type: not_empty
    severity: error
    error_message: LeadSource is required for attribution tracking.
```

## Removing the `contract:` wrapper

Delete the `contract:` line and dedent everything under it by one level.

**Before (2.x):**

```yaml
contract:
  name: order
  version: "1.0"
  owner: Data Governance
  status: active
  rules:
    - name: amount_positive
      type: min
      field: amount
      min: 0.01
      severity: error
```

**After (3.0.0):**

```yaml
name: order
version: "1.0"
owner: Data Governance
status: active
rules:
  - name: amount_positive
    type: min
    field: amount
    min: 0.01
    severity: error
```

Run `opendqv lint <contract>` on each migrated contract (the CLI looks it up
in your contracts directory by name) before reloading; a file that still
carries either shape is reported with the codes above.

---

## See also

- [`contract_versioning.md`](contract_versioning.md) — versions, `opendqv fork`, and pinning by hash
- [`naming_conventions.md`](naming_conventions.md) — naming conventions for contracts, rules, and fields
- [`contract_conformance.md`](contract_conformance.md) — the Core ↔ managed-engine decisions record
