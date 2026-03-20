# Multi-Parent Federation

**Status: P3 — Architectural direction. Not yet implemented in OpenDQV.**

This document describes the planned multi-parent federation model, the conflict resolution approach, and the current workaround for organisations that need to comply with rules from more than one authority node.

---

## What It Means

In OpenDQV's current single-parent model, a community node inherits rules from exactly one authority node. The authority owns the contract, propagates changes via federation sync, and is the single source of governance truth for the community node.

Multi-parent federation allows a community node to inherit rules from **two or more authority nodes simultaneously**. This reflects real-world compliance structures where a single organisation is subject to multiple regulatory regimes.

### Example: A hospital subject to two authorities

A hospital trust may be required to comply with:

- **NHS National Data Standards** (the national health authority) — mandating fields like NHS number format, SNOMED CT coding, and PDS matching rules
- **Regional Health Authority** — adding region-specific rules for referral pathway codes and local patient identifiers

Under multi-parent federation, the hospital's community node would declare both as parent authorities and inherit rules from each. The resulting effective contract is the **union** of both parent contracts, with conflict resolution applied where rules overlap.

---

## Conflict Resolution: Stricter Wins

When two parent authorities define rules for the same field, the conflict resolution model is:

**The stricter constraint wins.**

More precisely:

1. **Invariants from both parents must hold.** A record that fails a rule inherited from either parent is invalid. There is no mechanism to override a parent's invariant at the community level — that would defeat the purpose of federated governance.

2. **Where rules overlap on the same field with different thresholds**, the stricter threshold applies. For example:
   - Parent A requires `age` to be between 0 and 120
   - Parent B requires `age` to be between 0 and 110
   - The effective rule requires `age` to be between 0 and 110

3. **Where rules are complementary** (different fields or non-overlapping conditions), both rules apply independently.

4. **Where rules are logically contradictory** (e.g., Parent A requires field X to be present, Parent B requires field X to be absent), the merge is **rejected with a conflict error**. The community node operator must resolve the conflict manually by filing a governance exception with one or both authority nodes.

This model means multi-parent federation is **additive and strictness-monotone**: adding a parent can only make the effective contract stricter, never looser.

---

## Planned Implementation Approach

The planned implementation involves three components:

### 1. Contract merge engine

A merge step that takes N parent contract versions and produces a single merged effective contract. The merge engine:

- Builds a map of rules keyed by `(field, rule_type)`
- For overlapping rules, applies the stricter-wins resolution
- Detects logical contradictions and surfaces them as merge errors before the contract is activated
- Records the provenance of each merged rule (which parent authority it came from) so auditors can trace any rule back to its source

### 2. Multi-cursor federation log

The existing `FederationLog` in `core/federation.py` uses a single LSN cursor per `(contract_name, source_node)` pair. Multi-parent federation requires the community node to maintain an independent replication cursor for each parent authority, and to re-run the merge step whenever any parent's contract changes.

### 3. Conflict governance workflow

When a merge conflict is detected, the system raises a governance alert rather than silently applying one parent's rule over the other. The alert records:

- Which two rules are in conflict
- Which parent authorities own each rule
- The timestamp and contract versions at which the conflict was first detected

Resolution requires an explicit governance action — either a contract amendment by one of the authorities, or a documented exception approved by both.

---

## Current Limitation

OpenDQV currently supports **single-parent federation only**. A community node can declare at most one `upstream` authority in its contract configuration. Attempting to declare multiple upstreams is not validated today and will produce undefined behaviour.

Multi-parent federation is on the roadmap but has no committed delivery date. It is tracked as a P3 architectural direction item.

---

## Current Workaround

Organisations that need to comply with rules from multiple authority nodes can use the following workaround today:

1. **Identify all rules from each authority contract** that apply to your organisation.

2. **Copy those rules manually** into a single community contract that you maintain directly (not as an inherited contract).

3. **Document the provenance of each copied rule** in the contract's `metadata` block:

   ```yaml
   rules:
     - name: nhs_number_format
       type: regex
       field: nhs_number
       pattern: "^[0-9]{10}$"
       metadata:
         source_authority: nhs-national-standards
         source_contract_version: "2.4.1"
         copied_at: "2026-03-09"

     - name: regional_referral_code
       type: enum
       field: referral_pathway_code
       values: [RTT, 18W, CEPN]
       metadata:
         source_authority: southwest-regional-health
         source_contract_version: "1.1.0"
         copied_at: "2026-03-09"
   ```

4. **Monitor both authority contracts for changes** manually. When either authority releases a new contract version, compare the diff against your community contract and apply relevant updates.

5. **Track this as technical debt** — when multi-parent federation ships, migrating from a manually-merged contract to a proper multi-parent configuration should be straightforward because the provenance metadata is already recorded.

### Limitations of the workaround

- No automatic propagation of upstream changes — you must poll both authority contracts manually
- No automatic conflict detection — you must review both contracts for contradictions before merging
- The federation log will not record the dual-authority lineage of individual rules

These limitations are the reason multi-parent federation is on the roadmap.

---

## Related

- `core/federation.py` — federation log implementation (append-only, LSN-based)
- `docs/patterns/federation_deprecation.md` — how authority nodes deprecate inherited rules
