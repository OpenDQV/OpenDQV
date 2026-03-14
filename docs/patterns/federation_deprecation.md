# Federation Deprecation Migration Path

## Overview

In OpenDQV's federation model, an **authority node** owns a contract and propagates its rules to **community nodes** that inherit from it. When a rule must be retired — because a standard changes, a format is superseded, or a check has been folded into a better algorithm — it cannot simply be deleted. Community nodes depend on inherited rules for compliance, and regulated industries require documented, auditable migration paths.

Federation deprecation is the mechanism that lets an authority node retire a rule gracefully, giving community nodes a defined window to migrate.

---

## Why Auditable Migration Paths Matter

In financial services and healthcare, validation rules are often required by regulation. Removing a rule that enforces IBAN format, patient ID structure, or transaction amount limits without a paper trail can constitute a compliance violation. The deprecation flow creates:

- A timestamped record in the `ContractHistory` diff of when a rule was deprecated at the authority level
- A propagated notice to all community nodes via the federation sync (`event_type='push'` with `deprecated: true` in the payload)
- A configurable migration window before the rule is removed, enforced at the governance layer
- Entries in the federation log (`core/federation.py`) for every stage of the transition

---

## Migration Flow

### 1. Authority node marks the rule as deprecated

The authority node's contract author adds `deprecated: true` to the rule definition, along with a human-readable `deprecation_message` and, where applicable, a `replaces_with` pointer to the successor rule name.

The contract version is bumped. This triggers a `push` event in the federation log with the updated contract payload.

### 2. Community nodes receive the deprecation notice

On the next federation sync, all community nodes that inherited the rule receive the updated contract. The sync payload includes the `deprecated` flag on the affected rule. The community node's governance layer records the deprecation notice and starts the migration clock.

### 3. Migration window

The migration window is configurable per authority node (default: **90 days**). During this window:

- The deprecated rule **remains active** on community nodes — it still runs during validation
- The community node author is expected to add the replacement rule alongside the deprecated one
- Both rules run in parallel; divergent results are surfaced as warnings in validation output
- The severity of the deprecated rule is typically downgraded from `error` to `warning` during the window

### 4. End of migration window

At the end of the migration window the deprecated rule is eligible for removal. Community nodes must:

1. Confirm the replacement rule is in place and producing correct results
2. Submit the contract change for **REVIEW** before removing inherited rules (inherited rules cannot be removed unilaterally — removal requires an explicit governance step)
3. Record the removal in ContractHistory

The `ContractHistory` diff shows:
- When the rule was first inherited from the authority node
- When the `deprecated: true` flag arrived via federation sync
- When the rule was removed from the community contract after migration

---

## YAML Syntax: Deprecating a Rule at the Authority Node

```yaml
- name: old_iban_format_check
  type: regex
  field: iban
  pattern: "^[A-Z]{2}[0-9]{2}..."
  deprecated: true
  deprecation_message: "Replaced by iban_checksum (type: checksum, algorithm: iban_mod97)"
  replaces_with: iban_checksum
  severity: warning  # downgraded from error during deprecation window
```

Key fields:

| Field | Required | Description |
|---|---|---|
| `deprecated` | yes | Set to `true` to begin the deprecation flow |
| `deprecation_message` | yes | Human-readable explanation; appears in governance alerts and CLI output |
| `replaces_with` | recommended | Name of the successor rule; enables automated migration checks |
| `severity` | recommended | Downgrade to `warning` during the migration window so inherited validations do not hard-fail on community nodes |

---

## How Community Nodes Should Handle a Deprecation Notice

### Step 1 — Add the new rule alongside the deprecated one

Do not remove the deprecated rule immediately. Add the replacement rule to the community contract:

```yaml
# New rule (replacement)
- name: iban_checksum
  type: checksum
  field: iban
  algorithm: iban_mod97
  severity: error

# Deprecated rule (inherited, kept during migration window)
- name: old_iban_format_check
  type: regex
  field: iban
  pattern: "^[A-Z]{2}[0-9]{2}..."
  deprecated: true
  deprecation_message: "Replaced by iban_checksum (type: checksum, algorithm: iban_mod97)"
  replaces_with: iban_checksum
  severity: warning
```

### Step 2 — Run both rules in parallel during the migration window

With both rules present, every record is validated against both. Any record that passes `old_iban_format_check` but fails `iban_checksum` (or vice versa) is flagged as a migration divergence. This reveals data edge cases that the new rule handles differently from the old one — before the old rule is removed.

### Step 3 — Remove the deprecated rule after the migration window

Once the migration window closes and you are satisfied the replacement rule is correct:

1. Remove `old_iban_format_check` from the community contract
2. Submit the contract version for **REVIEW** — inherited rule removals require an explicit review step in the governance workflow, not just an author self-approval
3. The removal is recorded in ContractHistory with the reviewer's identity and timestamp

### Step 4 — Confirm in the federation log

After removal is committed, the federation log will contain:
- The original `push` event carrying `deprecated: true`
- The community node's `ack` of that push
- The `commit` event after governance review approved the removal

This chain is the auditable migration record.

---

## CLI: Check Deprecation Status Across a Federation (Future)

The following command is planned for a future release:

```
opendqv federation check-deprecated
```

Expected output:

```
Federation deprecation status — authority: nhs-national-standards
  Rule: old_iban_format_check
    Deprecated at authority: 2026-01-15
    Migration window: 90 days (expires 2026-04-15)
    Community nodes pending migration:
      hospital-london-trust    — replacement rule present: YES  removed deprecated: NO
      hospital-manchester-nhs  — replacement rule present: NO   removed deprecated: NO  [OVERDUE]
```

Until this command is implemented, query the federation log directly:

```python
from core.federation import FederationLog
log = FederationLog()
events = log.get_since(0, contract_name="nhs-iban-contract")
deprecated_pushes = [e for e in events if e["payload"].get("deprecated_rules")]
```

---

## Related

- `core/federation.py` — federation log implementation (append-only, LSN-based)
- `docs/patterns/multi_parent_federation.md` — multi-parent federation architecture
- `docs/patterns/distribution_check.md` — statistical distribution rules
