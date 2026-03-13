# Contract REVIEW Lifecycle

**Released:** v1.0.0

## Overview

OpenDQV contracts move through a defined lifecycle. The `REVIEW` state adds a mandatory maker-checker gate between `DRAFT` and `ACTIVE`: an editor submits a contract for review, and a separate approver (or admin) must approve it before it can be used in production validation.

This satisfies the control requirements of regulated industries: FCA SYSC, Ofwat licence conditions, NHS DSP Toolkit, and SOX-adjacent data governance frameworks all require evidence that production validation rules were authorised by an independent reviewer.

## States

```
DRAFT ──────────── submit-review ────────────► REVIEW
  ▲                                                │
  │◄────────── reject ──────────────────────────────┘
  │
  └── (revise and re-submit)

REVIEW ──────────── approve ─────────────────► ACTIVE

ACTIVE ──────────── deprecate ───────────────► DEPRECATED
```

| State | Description |
|-------|-------------|
| `DRAFT` | Being authored or revised. Blocked from production validation unless `?allow_draft=true` is explicitly passed. |
| `REVIEW` | Submitted for approval. Frozen — no edits while in review. Only approve/reject transitions are permitted. |
| `ACTIVE` | Live. Source systems can validate against it. Default state for loaded contracts. |
| `DEPRECATED` | Still functional but hidden from default contract listings. Callers should migrate to a newer version. |
| `REJECTED` | Returned to a rejected state; must be revised and re-submitted as a new DRAFT. |

## API endpoints

### Submit for review (DRAFT → REVIEW)

```
POST /api/v1/contracts/{name}/{version}/submit-review
```

**Roles:** editor, approver, or admin
**Body:**
```json
{"proposed_by": "alice@example.com"}
```

**Effect:** Transitions the contract from `DRAFT` to `REVIEW`. The contract is frozen — no further edits are permitted until the contract is approved or rejected. `proposed_by` is recorded in the `ContractHistory` audit log.

**Response:**
```json
{
  "name": "hr_employee_records",
  "version": "1.0",
  "status": "review",
  "proposed_by": "alice@example.com",
  "submitted_at": "2026-03-09T09:00:00Z"
}
```

### Approve (REVIEW → ACTIVE)

```
POST /api/v1/contracts/{name}/{version}/approve
```

**Roles:** approver or admin only (not editor)
**Body:**
```json
{"approved_by": "bob@example.com"}
```

**Effect:** Transitions the contract from `REVIEW` to `ACTIVE`. The contract is immediately available for production validation. `approved_by` is recorded in the hash-chained `ContractHistory` audit log.

**Response:**
```json
{
  "name": "hr_employee_records",
  "version": "1.0",
  "status": "active",
  "approved_by": "bob@example.com",
  "approved_at": "2026-03-09T11:00:00Z"
}
```

### Reject (REVIEW → DRAFT)

```
POST /api/v1/contracts/{name}/{version}/reject
```

**Roles:** approver or admin only
**Body:**
```json
{
  "rejected_by": "bob@example.com",
  "reason": "Needs tighter NHS format regex before approval"
}
```

**Effect:** Transitions the contract from `REVIEW` back to `DRAFT`. The rejection reason and `rejected_by` are recorded in the `ContractHistory` log. The contract author (editor) can revise and re-submit.

**Response:**
```json
{
  "name": "hr_employee_records",
  "version": "1.0",
  "status": "draft",
  "rejected_by": "bob@example.com",
  "reason": "Needs tighter NHS format regex before approval",
  "rejected_at": "2026-03-09T11:30:00Z"
}
```

### Deprecate (ACTIVE → DEPRECATED)

```
POST /api/v1/contracts/{name}/status?status=deprecated
```

**Roles:** approver or admin
**Effect:** Marks the contract as deprecated. It continues to function but is hidden from default contract listings. Existing integrations will receive a deprecation notice in the response headers.

## Roles

| Role | Can submit | Can approve | Can reject | Can deprecate |
|------|-----------|------------|-----------|--------------|
| writer / auditor | No | No | No | No |
| editor | Yes | No | No | No |
| approver | Yes | Yes | Yes | Yes |
| admin | Yes | Yes | Yes | Yes |

The separation between `editor` and `approver` roles enforces the maker-checker principle: the person who authored a contract cannot be the person who approves it for production.

## ContractHistory audit trail

Every lifecycle transition is written to an append-only, hash-chained `ContractHistory` log. Each entry includes:

- Contract name and version
- Previous and new status
- Identity of the actor (`proposed_by`, `approved_by`, or `rejected_by`)
- Timestamp
- SHA-256 hash of the previous entry (chain integrity)

```
GET /api/v1/contracts/{name}/history
```

Example response:

```json
[
  {
    "version": "1.0",
    "status_from": "draft",
    "status_to": "review",
    "actor": "alice@example.com",
    "action": "submit-review",
    "timestamp": "2026-03-09T09:00:00Z",
    "entry_hash": "a3f8d2c1...",
    "prev_hash": "0000000000..."
  },
  {
    "version": "1.0",
    "status_from": "review",
    "status_to": "active",
    "actor": "bob@example.com",
    "action": "approve",
    "approved_by": "bob@example.com",
    "timestamp": "2026-03-09T11:00:00Z",
    "entry_hash": "f7c3a9b2...",
    "prev_hash": "a3f8d2c1..."
  }
]
```

The hash chain ensures that the audit log cannot be silently altered. Any modification to a historical entry will break the chain and be detected on next verification.

## Why maker-checker matters in regulated industries

| Industry / Framework | Requirement | How REVIEW lifecycle satisfies it |
|---------------------|-------------|-----------------------------------|
| FCA SYSC 9 / MAR | Evidence that control changes were independently authorised | `approved_by` in ContractHistory with hash chain |
| Ofwat licence conditions | Change management audit trail for data quality rules | Immutable ContractHistory with actor identity |
| NHS DSP Toolkit | Controlled access to PII processing rules | Editor/approver role separation, frozen REVIEW state |
| SOX (data governance) | Independent review of financial data controls | Maker-checker gate with timestamped approver identity |
| GDPR Article 5 | Accountability for processing rules | Hash-chained log of all changes to validation contracts |

## Workflow example

A typical regulated workflow for a new field validation rule:

1. **Data engineer (editor)** creates `customer v1.1` with a new `postcode_format` rule. Sets `status: draft`.
2. **Data engineer** tests the rule in the Workbench UI (uses `?allow_draft=true`).
3. **Data engineer** calls `POST /submit-review` with `proposed_by: alice@acme.example.com`.
4. **Data governance lead (approver)** reads the `/explain` output to understand the rule in plain English.
5. **Data governance lead** approves: `POST /approve` with `approved_by: bob@acme.example.com`.
6. Contract transitions to `ACTIVE`. Source systems can validate against it. The approval is immutably recorded.

If the approver has concerns, they reject with a reason. The editor revises and re-submits.

## See also

- `sensitive_fields.md` — changes to sensitive field declarations also require a REVIEW cycle
- `explain_endpoint.md` — the /explain endpoint makes contracts human-readable for approvers
- `docs/index.md` — ContractHistory and `approved_by` field details
