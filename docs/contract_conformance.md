# Contract-format conformance across engines

> Opened by CRT180, 2026-09-02. Status: proposal for review by the Core
> maintainers; the mechanical parts ship in this change set, the decisions
> are listed at the end.

## The principle

One contract format, two engines, one verdict.

OpenDQV Core (this repository, Python, MIT) defines the contract format.
OpenDQV Cloud, the managed engine, runs the same YAML at a write-time
boundary. The public promise on both sides is that a contract written for
one runs unchanged on the other — "your YAML goes with you". That promise is
only true while the two engines agree, rule type by rule type and flag by
flag, on what a contract means. Where they disagree, the standard we both
now speak, ODCS v3.1.0, does not help: ODCS carries an OpenDQV rule
faithfully (as a `custom` entry with `engine: opendqv`), so it carries a
divergence just as faithfully. Interchange is not conformance.

Conformance therefore has to be engineered:

1. **One vocabulary.** Every rule type and every contract-level flag in the
   format is implemented by both engines with identical pass/fail semantics
   on the same record, or is explicitly refused at load time by the engine
   that lacks it. Silently ignoring a construct is never acceptable.
2. **One library.** The bundled starter contracts are one library shipped
   twice, not two libraries that happen to share names.
3. **One oracle.** Core is the spec. A fixture set of probe records with
   Core's verdicts lives in this repository; the other engine runs the same
   files in its own test suite. A disagreement is a spec question raised
   here, never something either side papers over.
4. **One interchange mapping.** Core's ODCS exporter/importer is the
   reference mapping; the other engine reproduces it byte-for-byte on shared
   contracts and proves it with a test.

## Divergence register (measured 2026-09-02, Core v2.4.0 vs the managed engine)

| Layer | Core | Managed engine | Status after CRT180 |
|---|---|---|---|
| Rule type `not_empty_string` | absent — a contract using it loaded and the rule silently passed (unknown type) | shipped | **closed** — added in 1/4 |
| Contract flag `strict_schema` + `fields` | absent — the key was dropped on parse and undeclared fields passed | shipped | **closed** — added in 2/4 |
| `contexts:` block | implemented (five bundled contracts use it) | not implemented — refused at validate time with an explicit error | **open** — decision D1 below |
| Rule key `optional` | not in the Rule model (ignored) | implemented (opts a field out of implicit-required semantics) | **open** — decision D2 |
| Envelope | bundled files use the `contract:` wrapper; loader accepts wrapped and flat | flat only; wrapper rejected | **open** — decision D3 |
| Starter library, 40 shared contracts | 23 identical; 11 rules only here; 22 only there; 24 same-name rules with different config | | **partly closed** — 3/4 applied the mechanical classes; the rest is D4 |
| Batch vs single path | `validate_batch` skips a rule when no record in the batch carries the field; `validate_record` rejects each record (`not_empty`) | single path only | **open** — known issue K1, pinned by a strict xfail in `tests/test_conformance_fixtures.py` |
| Metadata | attestation fields (`proposed_by/at`, `approved_by/at`, `owner_email`) on bundled files; versions 1.0/1.1 | none of those; version 0.1 on bundled samples by design | by design, not a conformance matter |

## What 3/4 applied and why it is safe

- **Added, verbatim, from the managed engine's library (22 rules):** check-digit
  rules (`vin_check_digit`, `instrument_id_isin_valid`, `product_code_gtin_valid`,
  `barcode_gtin_valid`, `nhs_number_valid`), DORA notification timing
  (`initial_notification_within_4h_of_classification`, `…_within_24h_of_awareness`,
  `intermediate_report_within_72h_of_initial`, plus the classification /
  intermediate timestamp rules), Martyn's Law required-field, communication-
  procedure, terrorism-protection-plan and SIA-reference rules, and
  `ni_number_not_reserved_prefix`. Additive only; every type already exists here.
- **`not_empty` → `not_empty_string` (2 rules):** `account_number_required` in
  `banking_transaction` and `financial_services_customer` — the type guard the
  other engine has enforced since mid-2026; a numeric account number is a
  type mismatch, not a valid string.
- **Lookahead → `negate: true` (2 rules):** `hr_employee.ni_number_format` and
  `salesforce_lead.email_not_personal`. Same semantics, portable to RE2-class
  regex engines — which matters because the ODCS exporter already refuses to
  project lookahead natively and the ODCS reference implementation aborts on it.

## Decisions for the maintainers

**D1 — `contexts:`.** Options: (a) keep contexts as a Core extension and
state in the format that an engine which does not implement them MUST
refuse a contract that declares them at load time (today the managed engine
refuses at validate time, which is later than ideal); (b) drop contexts from
the format; (c) the managed engine implements them. Recommendation: (a)
now, (c) when a customer needs a context on that engine.

**D2 — `optional`.** The managed engine treats an error-severity single-field
rule as implying the field is present (an absent field fails) and `optional:
true` opts out. Core skips format-class rules on absent fields, so `optional`
is a no-op here and is dropped by the Rule model. Options: (a) add
`optional: bool = False` to the Rule model so the key round-trips and the
ODCS custom twin carries it; (b) adopt implicit-required semantics in Core
(behaviour change, needs its own review). Recommendation: (a) now.

**D3 — envelope.** Core accepts both shapes; the managed engine accepts only
the flat one. Options: (a) migrate the bundled files to the flat envelope in
a minor release and keep accepting the wrapper for user files; (b) the
managed engine accepts the wrapper. Recommendation: (a) — the flat form is
what the format documents.

**D4 — same-name rules with different config (judgement calls, not applied).**

| Contract | Rule | Core | Managed engine | Note |
|---|---|---|---|---|
| banking_transaction | `merchant_id_required` | `not_empty`, warning | `required_if: {field: channel, value: pos}` | conditional is the tighter reading of "card transactions" |
| banking_transaction | `reference_max_length`, `bic_format`, `sort_code_format` | — | `optional: true` | depends on D2 |
| dora_ict_incident | `final_report_timeline` | `date_diff` vs detection, max 30 | vs intermediate report, 0–31 | different reading of the reporting clock |
| dora_ict_incident | `detection_timestamp_format`, `initial_notification_timestamp_format` | `date_format` | `regex` ISO-8601 with offset | regex is stricter (requires an offset) |
| insurance_claim | `claimed_amount_min` | `min: 0` | `min: 0.01` | zero-value claims: reject or allow? |
| martyns_law_event | `expected_attendance_minimum` | `min: 200` | `min: 800` | 200 is the standard-tier threshold; 800 the enhanced tier — the managed copy looks wrong for an events contract |
| martyns_law_event | `duty_tier_valid` | `ref/martyns_law_duty_tiers.txt` | `ref/martyns_law_event_duty_tiers.txt` | reference-file naming |
| mifid_transaction_report | `quantity_min`, `price_min` | `min: 0` | `min: 1` / `min: 0.01` | zero quantity/price |
| mifid_transaction_report | `execution_timestamp_format` | exactly 6 fractional digits | 1–6 optional | RTS 25 precision reading |
| mifid_transaction_report | `buyer_id_lei_valid`, `seller_id_lei_valid` | `condition value: lei` | `value: LEI` | string comparison is case-sensitive; one of these never fires on real data |
| proof_of_play | `advertiser_id_format` | `^ADV-[0-9]{8}$` | `^ADV-[0-9]{6,10}$` | precision |
| real_estate_property, telecoms_cdr | `asking_price_min`, `duration_seconds_min` | `min: 0` | `min: 1` | zero values |

**Rules only in Core (the managed library should adopt these):**
`dora_ict_incident`: `early_warning_timestamp_required`,
`early_warning_timestamp_format`, `early_warning_timeline`,
`initial_notification_timeline`, `root_cause_required_if_significant`;
`martyns_law_event`: the four `*_required_if_enhanced` conditional variants;
`martyns_law_venue`: `sia_registration_number_required_if_enhanced`;
`nhs_dsp_patient`: `nhs_number_format`.

**D5 — library ownership.** Today the two copies are synchronised by hand and
had drifted for months. Proposal: this repository is the source of record for
the starter library; the managed engine mirrors it from a tagged release and
its build fails if the mirror differs from the tag.

## The conformance fixtures

`tests/fixtures/conformance/<contract>.jsonl` — one JSON object per line:

```json
{"record": {...}, "expect": {"valid": false, "error_codes": ["OPENDQV_NOT_EMPTY_AMOUNT_REQUIRED"], "warning_codes": []}}
```

Probe records are synthesised by `scripts/conformance_fixtures.py` from the
contract's own rules (an empty record, one violating record per rule, an
all-strings record, and one with an undeclared field), and the expectation
is Core's single-record verdict. `tests/test_conformance_fixtures.py` proves
Core reproduces its own fixtures on every run. The other engine runs the
same files from a pinned tag of this repository; a disagreement is filed
here as a spec question. Hand-crafted clean rows per contract are the next
improvement — a synthesised record is rarely clean.

Error codes are deterministic and engine-independent: `OPENDQV_<RULE_TYPE>_<RULE_NAME>`
upper-cased, plus `OPENDQV_TYPE_MISMATCH`, `OPENDQV_REQUIRED_FIELD_MISSING`
(managed engine, implicit-required) and `OPENDQV_ADDITIONAL_PROPERTIES`
(`strict_schema`).

## First cross-engine run (2026-09-02, the managed engine on these fixtures)

The five fixture files were run through the managed engine against the
same synced contracts (wrapper removed — the only transformation).

| Fixture | Probes | Identical | Differ |
|---|---|---|---|
| banking_transaction | 16 | 15 | 1 |
| hr_employee | 20 | 18 | 2 |
| customer | 15 | 14 | 1 |
| dora_ict_incident | 40 | 35 | 5 |
| nhs_dsp_patient | 20 | 18 | 2 |
| **total** | **111** | **100** | **11** |

Every one of the 111 probes gets the **same `valid` verdict** on both
engines. The 11 differences are in which error codes accompany an invalid
record, and they fall into exactly two classes — both are specification
questions, not bugs on either side:

- **Class A — implicit-required (5 probes, the empty record `{}`).** The
  managed engine adds `OPENDQV_REQUIRED_FIELD_MISSING` for every
  error-severity single-field format rule whose field is absent (its
  implicit-required semantics, the thing `optional: true` opts out of). Core
  reports only the `not_empty` codes because format-class rules skip absent
  fields. → **Decision D2** above; whichever way it goes, the fixtures
  encode it.
- **Class B — empty string on format-class rules (6 probes: one field set
  to `""`).** Core treats `""` as absent for format-class rules (`regex`,
  `date_format`, …), so only the `not_empty` rule fires — CRT170/J3, "the
  presence-class rules are the single catcher for absence". The managed
  engine fires the format rule too (`OPENDQV_REGEX_EMAIL_FORMAT`,
  `OPENDQV_DATE_FORMAT_DETECTION_TIMESTAMP_FORMAT`, …) alongside `not_empty`.
  → **Decision D6: is an empty string absent?** Recommendation: Core's
  reading — one error per missing value, from the rule whose job it is —
  and the managed engine adopts it.

A third class existed for one run and is gone: the first cut of
`not_empty_string` in this change set reported a non-string value under
`OPENDQV_TYPE_MISMATCH`, while the managed engine (which shipped the type
first) reports it under the rule's own code. The fixtures caught it within
minutes; Core now matches. That is the mechanism working.

## Known issues

**K1 — batch skips absent fields.** `validate_batch` logs "Skipping rule —
field not in data" and skips the rule when no record in the batch carries the
field; `validate_record` on each of those records rejects it (`not_empty`).
A batch of records that all omit a required field validates clean. Pinned by
`test_k1_batch_skips_rule_when_field_absent_from_every_record` (strict xfail)
so the fix flips it to a pass deliberately.

## ODCS is the interchange, not the conformance

Both engines export and import ODCS v3.1.0. Core's mapping
(`opendqv/core/importers/odcs.py`, documented in `docs/importers.md`) is the
reference; the managed engine reproduces it and proves byte-equivalence on
the shared library. What ODCS cannot express natively (conditions, negated
regex, cross-field arithmetic, checksums, file-backed lookups, strict schema)
travels as the `custom/opendqv` entry — which is exactly why the two engines
must agree on that vocabulary first.
