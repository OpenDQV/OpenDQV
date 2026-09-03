# Contract Versioning

How OpenDQV records, resolves, and evolves contract versions.

---

## Current Behaviour

Version is an operator-managed string stored in the contract header (e.g. `"1.0"`, `"2024-Q1"`, `"prod-v3"`). OpenDQV records the full history of every contract version in the `ContractHistory` audit log — you can retrieve any past version and see exactly which rules were active at any point in time.

The system does **not** validate that version strings follow semver, and it does **not** automatically classify changes as breaking or non-breaking. That classification is left to the operator and their governance process. The `REVIEW` lifecycle (draft → review → active) is the mechanism for ensuring that version changes are deliberately approved before they affect live validation.

---

## Creating a New Version

`POST /contracts/{name}/version?new_version=<v>` creates the
new version as a **separate contract object with its own file**, `{name}_v{version}.yaml`,
in DRAFT status with the approval trail cleared. The version it was copied from is not
modified and keeps serving validation until the new version is approved. The request is
rejected with `400` if that version — or a file for it — already exists, and the version
string must match `[A-Za-z0-9][A-Za-z0-9._-]{0,49}`. The copy is lossless: rules, `contexts:`,
`strict_schema` / `allowed_fields` and every other key carry over.

Rule mutations are only accepted while the new version is DRAFT. Once it is submitted for
review (`/contracts/{name}/{version}/submit-review`) it is immutable — the registry returns
`409` on every surface — until an approver approves it (`/approve`) or rejects it back to
DRAFT (`/reject`). Activation archives the previously ACTIVE version of the same name.

---

## In-Flight Semantics

Active version resolves at request time: when a validation request arrives, OpenDQV looks up the currently `ACTIVE` version of the named contract and applies its rules. The resolved version is recorded in the response (`"version"` field) and in the audit log entry for that request.

In-flight requests are not interrupted by a version bump. A request that has already begun evaluation completes against the version that was active when evaluation started. Requests received after the bump use the new rules immediately — there is no grace window or draining period.

This means that during a version transition, a small number of records validated in the same second as the bump may be evaluated against the old version while others are evaluated against the new one. The `contract_hash` in each validation response uniquely identifies the exact ruleset used, so point-in-time audit is always unambiguous.

---

## Version Pinning

Version pinning — the ability for a caller to request validation against a specific named version rather than the current active version — is **not currently implemented**. All validation requests resolve to the currently `ACTIVE` version.

This is a noted post-v1.0 roadmap item.

**Workaround for operators who need version isolation today:** create separate named contracts per version (e.g. `customer_v1`, `customer_v2`). Each contract is independently versioned and activated. Callers pin by name. This adds contract management overhead but provides full isolation between versions.
