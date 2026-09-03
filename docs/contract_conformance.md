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
| Contract flag `strict_schema` + `allowed_fields` | absent — the key was dropped on parse and undeclared fields passed | shipped | **closed** — added in 2/4 |
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
| martyns_law_event | `expected_attendance_minimum` | `min: 200` | `min: 800` | **Correction (review round 1):** the managed copy is right and this row was wrong. Terrorism (Protection of Premises) Act 2025 (c. 10) s.3(1)(d): a qualifying *event* requires "800 or more individuals"; the 200 threshold is s.2(2)(c) for *premises*. Core's 200 should become 800 — filed as an issue rather than changed here |
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
{"kind": "probe",
 "record": {...},
 "expect": {"valid": false,
            "errors":   [{"code": "OPENDQV_NOT_EMPTY_AMOUNT_REQUIRED", "severity": "error", "message": "amount is required"}],
            "warnings": []}}
```

Three kinds of line. `probe` records are synthesised by
`scripts/conformance_fixtures.py` from the contract's own rules (an empty
record, one violating record per rule, an all-strings record, and one with
an undeclared field). `clean` and `warning_only` records are hand-written in
`scripts/conformance_clean_rows.py` so the corpus proves *acceptance* as
well as rejection — every fixture carries at least one clean row, and a
warning-only row wherever the contract has a warning-severity rule.

The expectation is Core's verdict — code, severity and message — computed
on **both** validate paths. The generator refuses to emit a corpus in which
`validate_record` and `validate_batch` disagree, in which a clean row is not
clean, or in which a rule type in the validator's handler table has no
violation probe. `tests/test_conformance_fixtures.py` proves Core reproduces
its own fixtures on every run: each line on the single path, each line as a
one-record batch, and the whole corpus as a single batch (where only
`unique`, whose scope is the batch by definition, may differ). Two of the
five fixture contracts (`banking_transaction`, `dora_ict_incident`) are
`strict_schema: true`, so the undeclared-field probe is a rejection there.

The other engine runs the same files from a pinned tag of this repository;
a disagreement is filed here as a spec question. `library_manifest.json`
(`scripts/library_manifest.py`, CI-checked) names the library version that
engine mirrored: one SHA-256 per contract's rules and one for the whole
library, so "which contracts changed since the tag we synced" is a diff of
two small JSON files.

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

(First run, on the original code-only corpus. The second run, on the
rebuilt corpus after review round 1, follows this section.)

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

## Second cross-engine run (2026-09-02, after review round 1)

Same method, rebuilt corpus: 120 rows across the five fixtures, each
expectation now `{code, severity, message}` per finding, with the
hand-written clean and warning-only rows. The managed engine's probe
compares the verdict, the sorted code+severity set, and — separately — the
messages.

| Fixture | Rows | Identical (verdict + code + severity) | … incl. messages | Verdict | Clean | Warning-only |
|---|---|---|---|---|---|---|
| banking_transaction (strict) | 18 | 17 | 16 | 18/18 | 1/1 | 1/1 |
| hr_employee | 22 | 20 | 20 | 22/22 | 1/1 | 1/1 |
| customer | 17 | 16 | 16 | 17/17 | 1/1 | 1/1 |
| dora_ict_incident (strict) | 41 | 36 | 36 | 41/41 | 1/1 | — |
| nhs_dsp_patient | 22 | 20 | 20 | 22/22 | 1/1 | 1/1 |
| **total** | **120** | **109** | **108** | **120/120** | **5/5** | **4/4** |

**Every row gets the same verdict on both engines, every clean row is clean
on both, every warning-only row is warning-only on both, and both strict
contracts reject the undeclared-field probe identically.** The 11 rows that
differ in codes are exactly the two classes already on the register:

- **Class A — implicit-required, 4 rows** (the `{}` probe in banking, hr,
  customer, nhs): the managed engine adds `OPENDQV_REQUIRED_FIELD_MISSING`
  per absent error-severity format field. → D2.
- **Class B — empty string, 7 rows** (hr 1, dora 5, nhs 1): the managed
  engine fires the format rule on `""` as well as `not_empty`. → D6, now
  normative here; the managed engine adopts Core's reading.

Messages: 108 of the 109 code-identical rows also match message-for-message
after two wording alignments made in this round — engine-generated messages
now name the **JSON type** (`got string`, not `got str`) and quote field
names the same way on both sides. The one remaining difference is a
behaviour question, not wording:

- **D9 — `min_length` / `max_length` on a non-string (new).** Given
  `account_number: 42`, Core coerces to `"42"` and reports the contract's
  own message ("must be at least 6 characters"); the managed engine refuses
  to coerce and reports a typed message under the same code ("length rule
  … expects a JSON string, got number"). Same code, same severity, same
  verdict, different reason. Recommendation: the managed engine's reading —
  the same no-coercion rule `not_empty_string` already follows — so a
  length rule never measures the decimal rendering of a number. **Applied
  (round 2, S5):** `min_length`/`max_length` on a non-string now report a
  typed message under the rule's own code on both paths; the corpus row is
  identical.

Two things the run taught about the *harness*, not the engines: the
managed engine resolves relative `lookup_file:` paths against its
configured lookup directory (the first run of the rebuilt corpus reported
three lookup failures until the directory was pointed at `ref/`), and the
`lookup`-backed clean rows are the only rows whose verdict depends on files
outside the contract. Both are now noted in the probe procedure.

## Third cross-engine run (2026-09-02, after both engines adopted D6 and D7)

The managed engine adopted D6 (blank is absent, one catcher) and D7
(start-anchored patterns), and refuses `contexts:` at parse (D1). Running
the corpus against it then exposed **Core's own D6 gaps**: `allowed_values`,
`lookup` and `date_diff` still fired on a blank string on the single path,
and their batch twins did too. Fixed here (`_is_field_absent` on the three
handlers; `_batch_absent` on the batch loops; `forbidden_if`'s batch SQL
trims before comparing). The generator's single/batch parity gate caught
the batch half before the corpus could be regenerated — the mechanism
working on its own oracle.

| Fixture | Rows | Identical (verdict + code + severity) | … incl. messages | Verdict |
|---|---|---|---|---|
| banking_transaction (strict) | 18 | 17 | 16 | 18/18 |
| hr_employee | 22 | 21 | 21 | 22/22 |
| customer | 17 | 16 | 16 | 17/17 |
| dora_ict_incident (strict) | 41 | 41 | 41 | 41/41 |
| nhs_dsp_patient | 22 | 21 | 21 | 22/22 |
| **total** | **120** | **116** | **115** | **120/120** |

The residue is now exactly the two open decisions: **Class A** (4 rows, the
`{}` probe on the four contracts with format-only fields → D2, implicit-
required) and **D9** (1 message, length rules on a number). Class B is
closed on both engines. One more harness note: the managed engine's probe
drops the `contexts:` block from Core's `customer` contract before parsing
(it would be refused otherwise) — the same one-transformation rule as the
envelope wrapper.

## Explicit presence in the library (CRT181) and the fourth run

D2 / Class A was the last verdict-level divergence: a field with an
error-severity format rule and no presence rule is "required" to an engine
with implicit-required and "optional when present" here. Rather than add a
contract-level switch both engines would have to interpret identically
forever, the ambiguity was removed from the data. Both engines already agree
on any field where the YAML says which it is — `not_empty` is "required" on
both, `optional: true` on a format rule is "optional" on both — so every such
field in the bundled library now says which: 109 `<field>_required` rules
across 27 contracts, one `optional: true` (`customer.loyalty_tier`, "when
provided"). One deliberate exception: `customer` is the hello-world contract
on both sides, and a minimal record (email, age, name) must stay valid — so
`email` and `age` are required and `phone`, `score`, `date`, `username`,
`password` carry `optional: true` ("optional when present") rather than a
presence rule. `tests/test_library_presence_explicit.py` keeps it that way: the
advisory `FORMAT_ONLY_FIELD_ACCEPTS_EMPTY` lint is an error for the library.
The managed engine mirrored the same pass on its starters.

**Fourth cross-engine run (2026-09-02, both libraries explicit):**

| Fixture | Rows | Identical (verdict + code + severity) | … incl. messages | Verdict |
|---|---|---|---|---|
| banking_transaction (strict) | 24 | 24 | 23 | 24/24 |
| hr_employee | 26 | 26 | 26 | 26/26 |
| customer | 19 | 19 | 19 | 19/19 |
| dora_ict_incident (strict) | 41 | 41 | 41 | 41/41 |
| nhs_dsp_patient | 27 | 27 | 27 | 27/27 |
| **total** | **137** | **137** | **136** | **137/137** |

Class A is closed. The only difference left on the whole corpus is **D9**
(one message: length rule on a number). For contracts *outside* the bundled
library the question D2 asks is still open — the lint names the ambiguous
field, and the managed engine is moving its implicit-required check from
the validator to the authoring boundary so a customer's YAML means the same
thing on both engines.

## Review round 2 (2026-09-03) — what changed

- **B1 — the six strict flips are OUT of this change.** `strict_schema: true`
  on `banking_transaction`, `dora_ict_incident`, `financial_services_customer`,
  `financial_trade`, `mifid_transaction_report`, `sox_control_test` rejected
  records that validated the day before (the live-stack integration test
  proved it). The feature ships with every bundled starter **off**; the flips
  land in their own release with a breaking-change entry and a migration
  note ("add extra columns to `allowed_fields:` or fork the starter"). Until
  then the managed library's copies of those six are strict, and the
  undeclared-field probe differs on two fixture rows — recorded, not hidden.
- **B2 — the absence guard is structural.** `_check_rule` (single) and
  `_batch_check_rule` (batch, a post-filter on the rule's field) skip every
  absent/blank value for every rule type outside `_PRESENCE_RULE_TYPES` ∪
  {`conditional_value`, `unique`}; the set is shared with the linter. The
  per-type sweep in the corpus (`blank_probe` rows: absent, `""`, spaces,
  tab/newline, wrong type — S1) found two more batch-half gaps on the way:
  SQL `TRIM` strips spaces only (tab/newline-only passed `not_empty` in
  batch), and `required_if`'s batch compare was `= ''` not blank-aware.
- **B4 / B5 — K1 finished.** The batch path materialises
  `declared_field_set(rules)` (cross-field counterparts included) and a
  present target with an absent counterpart fails `compare` as the single
  path does; `unique` on a synthesised column reports nothing (a column
  nobody sent cannot carry duplicates), and a synthesised `group_by` column
  is not a valid group key (existing global-unique fallback).
- **B6** manifest keeps zero boundaries (`0 == False` trap), hashes
  `strict_schema` + `allowed_fields` + `contexts`. **B7** stray files removed.
- **D10 (new, from the S1 blank sweep) — a cross-field rule whose
  counterpart is absent or blank FAILS, for every cross-field type, on both
  paths and both engines.** The evidence was messier than either engine's
  docs: Core's single path failed `compare`/`date_diff` on `None` (four
  deliberate tests) but silently zeroed an absent counterpart in
  `ratio_check`/`field_sum` and skipped it in `age_match`; its batch path
  skipped `compare` (the K1 shape) and had no `age_match` branch at all; the
  managed engine skipped `compare` and `age_match` (its F18 counterpart
  half, 2026-04-29) but failed `date_diff`/`ratio`/`range`/`sum`, and failed
  a *blank* `compare` counterpart only by accident of string comparison.
  Ruled FAIL on the maintainer's B4 and Core's deliberate tests; D6 governs
  a rule's OWN field, not the counterpart a comparison needs. Pinned per
  type on both sides and by the corpus's `counterpart_probe` rows. (A first
  cut of this round went the other way; corrected the same morning.)
- **Batch fallback (pattern-closer).** Any rule type without a native batch
  branch is now evaluated per record with the single-path handler, so
  single/batch parity holds by construction for every present and future
  type — `age_match` had no branch and silently passed in batch.
- **S1** negative control: a deliberately wrong expectation fails the suite.
- **S3 — strict export with contexts is looser than the engine.** The union
  export accepts a field only a context declares while a no-context engine
  call rejects it. Latent (no strict starter has contexts); noted here with
  the "cross-field refs inside overrides not walked" caveat.
- **S4 / S5 / S2 / S7** as above (D7, D9, CLI label, dead branch).

## Known issues

**K1 — batch skipped absent fields (FIXED, review B6).** `validate_batch`
used to skip a rule when no record in the batch carried the field, so a batch
of records that all omit a required field validated clean while
`validate_record` rejected each of them. The batch path now materialises
every rule field the frame lacks as an all-null column before evaluation,
so absence is judged by the same presence-class rules on both paths. Pinned
by `test_k1_batch_matches_single_when_field_absent_from_every_record` and by
the corpus itself (every line is checked as a one-record batch).

## Review round 1 (2026-09-02) — what the maintainer review changed

Blockers, all fixed on this branch:

- **B1 — dead negated pattern.** Core matches `regex` from the start of the
  value (`re.match`), so `salesforce_lead.email_not_personal`'s
  `@(gmail|…)\.com$` could never match and the warning never fired. Now
  `.*@(gmail|…)\.com$`; regression test pins gmail → warns, corporate → clean,
  `x@gmail.com.evil` → clean. See **D7** below — this is a spec question the
  run had not yet reached.
- **B2 — MCP and GraphQL bypassed `strict_schema`.** Both now splat
  `strict_schema_kwargs(...)`. A source-level test walks every
  `validate_record` / `validate_batch` call in `opendqv/` and fails if one
  omits it, so no surface can regress silently.
- **B3 — draft fallback read live flags.** The REST STRICT_DRAFT path now
  reads `last_active_strict_schema` / `last_active_fields`, snapshotted at
  ACTIVE→DRAFT alongside the rule snapshot.
- **B4 — Postgres history backend.** DDL, migrations, dedupe, hash inputs,
  INSERT, `get_as_of`, `get_history` mirror SQLite; test asserts the content
  hash of a strict contract is identical on both backends.
- **B5 — batch message parity.** The batch path emits the same typed
  `not_empty_string` message as the single path (per-row message override).
- **B6 — K1.** Fixed as above.

Shoulds: `fields:` → `allowed_fields:` everywhere (the old key is accepted
as a deprecated alias with a `FIELDS_KEY_DEPRECATED` lint warning; allow-list
names are validated against the same unsafe-character rule as rule fields);
`OPENDQV_ADDITIONAL_PROPERTIES` names at most ten unknown fields in the
message ("… and N more") while `unknown_fields` on the entry carries them
all; `strict_schema: true` set on the six regulated bundled contracts the
managed library already treats as strict (`banking_transaction`,
`dora_ict_incident`, `financial_services_customer`, `financial_trade`,
`mifid_transaction_report`, `sox_control_test`); corpus rebuilt in the
richer shape with clean and warning-only rows, both paths, and a
handler-table coverage check (above); `not_empty_string` documented as a
closed set of one — the format takes no other typed-presence rule without a
decision here.

Decisions this round settled or opened:

- **D2 — implicit-required: CLOSED, and not by "reserved".** The format's
  reading is normative: **a format rule never implies presence; presence is
  declared** (`not_empty`, `not_empty_string`, `required_if`), and a field
  meant to be optional-when-present says `optional: true` on its format
  rule. The bundled library carries no undecided field (CRT181, folded in
  here; `tests/test_library_presence_explicit.py`). The round-2 S6 proposal
  — implicit-required behind a flag, default flipped on a release later —
  was considered and declined by the project owner (2026-09-03): it would
  parameterise in two engines what the YAML can simply state, and the
  managed engine is moving its own implicit-required check from the
  validator to the authoring boundary so a customer's YAML means the same
  thing on both sides. `optional` stays accepted and round-tripped; on Core
  it is documentation of intent, not a switch.
- **D6 — an empty string is absent. Normative.** For every rule that is
  not presence-class (`not_empty`, `not_empty_string`, `required_if`), a
  value of `None` or a whitespace-only string is treated as *absent* and the
  rule does not fire. Presence is judged only by presence-class rules. A
  field that carries error-severity format rules and no presence rule
  therefore accepts `""`; the linter says so with the advisory
  `FORMAT_ONLY_FIELD_ACCEPTS_EMPTY` (severity `info`, never a failure —
  optional-when-present is a legitimate design). The managed engine adopts
  this reading; the Class B rows in the run above flip to identical.
- **D7 — regex semantics: UNANCHORED SEARCH is normative** (review round 2,
  S4, ruled 2026-09-03). ODCS `pattern`, JSON Schema `pattern`, RE2 and
  every code-generation target read a pattern as "matches anywhere"; Core's
  `re.match` silently anchored the start and made every portable export
  looser than the engine. Core now searches (`_safe_match`); `^`/`$` in the
  pattern pin the ends; the linter names patterns that do not start with `^`
  (`REGEX_NOT_START_ANCHORED`, info). Every bundled pattern is `^`-anchored
  or `.*`-led, so nothing bundled moves. The managed engine reverts to
  search as well.
- **D8 — `date_format` vs. ISO regex in `dora_ict_incident` (new, D4
  list).** The contract's `date_format` fields accept `YYYY-MM-DD` or
  `YYYY-MM-DDTHH:MM:SS` with no zone designator, while its two regex-only
  timestamp fields *require* one. Each field is self-consistent; the
  contract is not. A judgement call for the library owners, listed with the
  other D4 rows, not applied.

## ODCS is the interchange, not the conformance

Both engines export and import ODCS v3.1.0. Core's mapping
(`opendqv/core/importers/odcs.py`, documented in `docs/importers.md`) is the
reference; the managed engine reproduces it and proves byte-equivalence on
the shared library. What ODCS cannot express natively (conditions, negated
regex, cross-field arithmetic, checksums, file-backed lookups, strict schema)
travels as the `custom/opendqv` entry — which is exactly why the two engines
must agree on that vocabulary first.
