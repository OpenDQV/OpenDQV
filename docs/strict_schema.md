# Strict schema — reject undeclared fields

> Added in CRT180 (contract-format conformance).

By default an OpenDQV contract is **permissive**: a record may carry fields the
contract says nothing about, and they pass through unexamined. That is the
right default for evolving producers. It is the wrong default for a regulated
boundary where an unexpected field is itself a finding — a PII column that
should not be in the feed, a typo'd key that silently bypasses a rule, a
schema drift nobody announced.

`strict_schema: true` flips the contract to **reject records that carry any
field the contract does not declare** — the JSON-Schema
`additionalProperties: false` idea, enforced at the write boundary.

```yaml
contract:
  name: payments_feed
  version: "1.0"
  strict_schema: true
  allowed_fields: [trace_id]  # allowed without a rule of their own
  rules:
    - name: amount_required
      type: not_empty
      field: amount
    - name: amount_positive
      type: min
      field: amount
      min: 0
    - name: settled_after_booked
      type: compare
      field: settled_at
      compare_to: booked_at   # booked_at is declared by this reference
      compare_op: gte
```

## What counts as declared

A field is declared when:

- any rule targets it (`field:`), or
- any rule references it across fields — `compare_to`, `date_diff_field`,
  `cross_min_field` / `cross_max_field`, `ratio_numerator` /
  `ratio_denominator`, `geo_lon_field`, `dob_field`, every entry of
  `sum_fields` and `group_by`, and the `field` key of `required_if`,
  `forbidden_if` and `condition` — or
- it is listed in the contract's `allowed_fields:` allow-list (`fields:` is accepted as a deprecated alias and the linter warns).

The calendar sentinels `today` and `now` are not field names.

## What a violation looks like

One error per record, before any rule runs, naming every unknown field so the
producer sees all of them at once:

```json
{
  "field": "",
  "rule": "additional_properties",
  "message": "record contains 2 unknown field(s) not declared in the contract: 'card_pan', 'notes'",
  "severity": "error",
  "error_code": "OPENDQV_ADDITIONAL_PROPERTIES"
}
```

Single-record and batch validation behave identically. Contexts are applied
first; the declared set is computed from the resolved rules.

## Where it travels

- **Audit chain.** `strict_schema` and `allowed_fields` are content: a contract that
  turns strict on gets a new `content_hash` / `entry_hash`, and a snapshot
  retrieved by hash restores both. Contracts that never set them keep their
  existing hashes byte-for-byte — the two values only join the canonical
  payload when set.
- **JSON Schema export** emits `additionalProperties: false` for a strict
  contract and a bare property entry for every `allowed_fields:` name.
- **ODCS export** carries both as custom properties (`opendqv.strict_schema`,
  `opendqv.allowed_fields`); ODCS import restores them, and still reads the
  older `opendqv.fields` key for back-compatibility. The standard has no native
  construct for "reject undeclared fields".
- **Linter** rejects a non-boolean `strict_schema` or a non-string-list
  `allowed_fields`, warns when it is set without `strict_schema`, and warns on the deprecated `fields:` spelling.

## Why this exists

The same contract YAML has to mean the same thing on every engine that runs
it. The managed OpenDQV Cloud engine has enforced `strict_schema` since
mid-2026; until CRT180 a strict contract loaded into OpenDQV Core silently
lost the flag (unknown keys are ignored on parse) and accepted the records
Cloud rejects. See `docs/contract_conformance.md`.
