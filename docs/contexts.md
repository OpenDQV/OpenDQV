# Contexts

> Last reviewed: 2026-03-17

Contexts are named sets of rule overrides that apply on top of a base contract. A single YAML contract can serve multiple source systems, tenants, or regulatory regimes — each with their own rule adjustments — without duplicating the contract.

---

## What contexts are

Every data contract defines a base set of rules for a business entity (e.g. `customer`). Contexts let you declare named variants that override specific field rules for that same entity.

Common uses:

- **Multi-tenant SaaS** — one contract, per-tenant rule variants
- **Regional compliance** — same entity, different age or consent rules for EU vs US vs under-13 applications
- **Source system quirks** — a Salesforce feed that can legitimately have a null phone field, while the base contract requires it

When a validation request specifies a context, the engine applies the base contract rules first, then replaces rules for any fields named in the context. Fields not mentioned in the context keep their base contract rules unchanged.

---

## Merge semantics

A context override **replaces all rules for the named field**. It does not merge with or modify individual base rules — it replaces the entire field ruleset for that field with what is defined in the context block.

Fields not mentioned in the context are untouched and run with their base contract rules.

**Example — base contract has two rules for `age`:**

```yaml
# Base contract rules for "age"
- name: age_minimum
  type: min
  field: age
  min: 0
  severity: error

- name: age_reasonable
  type: max
  field: age
  max: 150
  severity: warning
```

**After applying the `kids_app` context:**

```yaml
# Effective rules for "age" under kids_app context
- type: range
  field: age
  min: 5
  max: 17
  severity: error
  error_message: Age must be 5-17 for kids app
```

Both base `age` rules (`age_minimum` and `age_reasonable`) are replaced by the single `range` rule from the context. All other fields (`email`, `name`, `id`, etc.) run their base contract rules unchanged.

---

## YAML syntax

Contexts are defined in a `contexts:` block at the end of the contract, after the `rules:` list. Each key under `contexts:` is a context name; each key under that is a field name whose rules are overridden.

The following is the exact `contexts:` block from `contracts/customer.yaml`:

```yaml
  contexts:
    kids_app:
      age:
        type: range
        min: 5
        max: 17
        severity: error
        error_message: Age must be 5-17 for kids app
    financial:
      age:
        type: min
        min: 18
        severity: error
        error_message: Must be 18+ for financial products
      balance:
        type: min
        min: 0
        severity: error
        error_message: Balance must be non-negative for financial accounts
```

**Reading this:**

- `kids_app` overrides the `age` field with a `range` rule requiring age 5–17.
- `financial` overrides `age` with a `min` rule requiring 18+, and also overrides `balance` to make the warning-severity base rule into a hard `error`.
- All other fields in the `customer` contract (`email`, `name`, `id`, `phone`, etc.) run their base rules under both contexts.

---

## How to pass context

### REST API

Include `"context"` in the validation request body:

```bash
curl -s -X POST http://localhost:8000/api/v1/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "contract": "customer",
    "context": "kids_app",
    "record": {"name": "Alex", "age": 10, "email": "alex@example.com"}
  }'
```

### Sync SDK

```python
from sdk.client import OpenDQVClient

client = OpenDQVClient(base_url="http://localhost:8000", token="...")
result = client.validate(record, contract="customer", context="kids_app")
```

### Async SDK

```python
from sdk.async_client import AsyncOpenDQVClient

async with AsyncOpenDQVClient(base_url="http://localhost:8000", token="...") as client:
    result = await client.validate(record, contract="customer", context="kids_app")
```

### CLI

```bash
python -m cli validate customer '{"age": 10}' --context kids_app
```

### Code generation

Pass `--context` to scope the generated code to context-specific field rules:

```bash
python -m cli generate customer salesforce --context financial
```

---

## Multi-tenant SaaS pattern

A single `customer` contract with per-tenant contexts avoids contract proliferation. The tenant's context is looked up from a mapping and passed to the validator at request time.

**Contract with three contexts:**

```yaml
contract:
  name: customer
  version: "1.0"
  rules:
    - name: valid_email
      type: regex
      field: email
      severity: error
    - name: age_minimum
      type: min
      field: age
      min: 0
      severity: error
    # ... other base rules ...
  contexts:
    eu_gdpr:
      age:
        type: min
        min: 16
        severity: error
        error_message: EU GDPR requires age 16+ for consent
    us_standard:
      age:
        type: min
        min: 13
        severity: error
        error_message: US COPPA requires age 13+ without parental consent
    kids_app:
      age:
        type: range
        min: 5
        max: 17
        severity: error
        error_message: Age must be 5-17 for kids app
```

**FastAPI routing by tenant:**

```python
from fastapi import FastAPI
from sdk.async_client import AsyncOpenDQVClient

app = FastAPI()
client = AsyncOpenDQVClient(base_url="http://localhost:8000", token="...")

tenant_context_map = {
    "eu-tenant-1": "eu_gdpr",
    "eu-tenant-2": "eu_gdpr",
    "us-tenant-1": "us_standard",
    "kids-app":    "kids_app",
}

@app.post("/ingest/{tenant_id}")
async def ingest(tenant_id: str, data: dict):
    context = tenant_context_map.get(tenant_id, "default")
    result = await client.validate(data, contract="customer", context=context)
    if not result.valid:
        return {"status": "rejected", "errors": result.errors}
    return {"status": "accepted"}
```

Tenants not in the map fall back to `"default"`, which — since no context named `default` exists in the contract — runs the base contract rules. This is a safe fallback.

---

## Regional compliance pattern

The same `customer` contract can enforce different age and consent thresholds by region without duplicating contract definitions:

| Context | Age rule | Use case |
|---|---|---|
| `eu_gdpr` | min 16 | EU digital services requiring GDPR consent |
| `us_standard` | min 13 | US services under COPPA |
| `kids_app` | range 5–17 | Children's app with upper age cap |
| `financial` | min 18 | Financial products requiring adult status |
| *(no context)* | min 0, max 150 | Base contract — non-negative, plausible range only |

A record with `age: 15` would pass the base contract and `kids_app`, but fail `financial` and `eu_gdpr`.

---

## When to use contexts vs. a new contract version

| Scenario | Recommendation |
|---|---|
| Different rules for the **same data entity** across source systems or tenants | Use a context |
| Relaxing or tightening a specific field rule for a known population segment | Use a context |
| The contract itself is **evolving** — rule additions, breaking changes, renamed fields | Use a new contract version |
| A **fundamentally different entity** (e.g. `order` vs `customer`) | Use a new contract |
| Governance requires a separate audit trail for a distinct business unit | Use a new contract |

Contexts are horizontal slices across a stable entity definition. Contract versions are the vertical history of how that entity definition evolved over time. A new contract is the right choice when the entity itself is distinct enough that sharing a rule set would be misleading.

---

## Naming conventions

Use `snake_case` for context names. Recommended examples:

```
kids_app
financial
eu_gdpr
us_standard
salesforce_prod
salesforce_sandbox
internal_review
```

Avoid spaces, hyphens, and special characters. Context names are stored as-is in YAML keys and passed directly in API requests and CLI flags.

---

## Context names are case-sensitive

Context names are freeform strings matched exactly. Passing an unknown context name raises an error — the API returns HTTP 422, and `LocalValidator` raises `UnknownContextError`.

```bash
# This runs the "financial" context rules
python -m cli validate customer '{"age": 25}' --context financial

# This raises an error — "Financial" is not a defined context
python -m cli validate customer '{"age": 25}' --context Financial
# → Error: Unknown context 'Financial' for contract 'customer'. Available contexts: ['financial', 'kids_app']
```

```python
from core.contracts import UnknownContextError

try:
    result = validator.validate(record, contract="customer", context="financia")
except UnknownContextError as e:
    print(e)  # Unknown context 'financia' for contract 'customer'. Available contexts: ['financial', 'kids_app']
```

The error message includes the list of available contexts, so misconfiguration is immediately obvious.

---

## See also

- [docs/naming_conventions.md](naming_conventions.md) — naming conventions for contracts, rules, and fields
- [contracts/customer.yaml](../contracts/customer.yaml) — the reference contract with live `contexts:` examples
