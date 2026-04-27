# Changelog

All notable changes to OpenDQV are documented here.

## [2.3.19] - 2026-04-27

Single-concern release closing a three-release regression: the MCP
proxy hardcoded `serverInfo.version: "2.3.16"` and reported the wrong
version on every client's `initialize` call through v2.3.16, v2.3.17,
and v2.3.18. Found during a Pilot-requested inside-view probe pass
that ran AFTER v2.3.18 tagged but BEFORE the outside reviewer was
sent the lean prompt. Closes the surface-honesty regression and the
process gap that let it ship through three releases.

### Fixed

- **`opendqv_mcp_proxy.py` — proxy now resolves engine version
  dynamically.** The previous hardcode at line 599 made the proxy
  report `2.3.16` on every `initialize` handshake regardless of the
  running engine. The new `_resolve_engine_version()` queries the
  engine's `/openapi.json info.version` (the canonical version surface
  per v2.3.17 Q11) at module-import time. When the engine is
  unreachable, falls back to the `"unknown"` sentinel rather than
  reporting a confidently-wrong stale value.
  Startup-race caveat documented inline: `_ENGINE_VERSION` resolves
  ONCE at import; a long-lived proxy launched before the engine binds
  its port will report `"unknown"` for its lifetime. v2.4 proxy
  unification (CRT-N) is the structural fix; in the interim, restart
  the proxy when restarting the engine.

- **`mifid_transaction_report.venue_mic_format` error message now
  states SHAPE-only (I-3, same N-5 LEI treatment).** Previous text
  claimed "valid 4-character Market Identifier Code per ISO 10383"
  which implied list lookup the rule does not perform. New text:
  `"venue_mic must match ISO 10383 MIC shape — 4 uppercase letters.
  Note: this rule enforces the SHAPE only; full ISO 10383 list lookup
  against iso20022.org is a v2.4 capability"`. Closes a regulator-
  fidelity description over-claim that v2.3.17 Cluster 7 fixed for
  LEI rules but missed for MIC.

### Added

- **Three new recurrence tests** (Queen's Standard pairing):
  - `tests/test_v2_3_17_cluster6_surface_hygiene.py::TestVersionSourceConsistency::test_proxy_initialize_reports_unknown_when_engine_unreachable`
    — subprocess-spawned proxy with unreachable engine asserts
    `serverInfo.version == "unknown"`. Guards against regression to
    a SemVer-shaped hardcode.
  - `tests/test_v2_3_17_cluster6_surface_hygiene.py::TestVersionSourceConsistency::test_proxy_initialize_reports_real_engine_version_when_connected`
    — positive-path test: spawns a real uvicorn on a random free
    port, runs the proxy against it, asserts proxy's reported version
    equals `importlib.metadata.version("opendqv")`. Closes the F-S
    invariant ring for the proxy surface.
  - `tests/test_v2_3_17_cluster4_proxy_parity.py::TestProxyInprocessParity::test_initialize_serverinfo_version_does_not_drift`
    — unit-time belt-and-suspenders. Asserts the proxy module exposes
    `_ENGINE_VERSION` and refuses to return a SemVer-shaped string
    when the engine is unreachable.

- **`tests/test_v2_3_17_cluster7_contract_content.py::TestMicDescriptionHonesty`** — recurrence test for I-3.

### Process

- **Inside-view probe pass is now a release-blocking gate**, alongside
  the existing Q10 cold-client smoke. Documented in
  `feedback_inside_before_outside_eval.md` Amendment 2026-04-27 and
  `feedback_cold_client_mcp_smoke.md` v2.3.19 amendment. The Q10
  cold-client smoke checklist now requires `initialize.serverInfo`
  agreement as a fourth check (was: tools/list + routing + response
  shape). Both gates bind; either failing blocks the tag.

- **Tagged probe agent_id convention** for inside-view scripts:
  `agent_id="bt7274-inside-probe-<timestamp>"` so audit-store cleanup
  of probe writes is selectable rather than wholesale or none.

### Why this regression escaped three releases

- v2.3.16's proxy hardcode happened to match the v2.3.16 engine — the
  drift was invisible until the next release.
- v2.3.17's Q10 cold-client smoke release-blocking gate
  (`tests/test_v2_3_17_cluster4_proxy_parity.py`) snapshotted
  `tools/list` and error envelope shape, NOT `initialize.serverInfo`.
- v2.3.18's pass_rate_pct rename did not touch the proxy's version
  surface and so didn't trip any test.
- The v2.3.17 plan's Q15 inside-first protocol was scoped to external
  eval rounds, not release tags. The protocol gap is closed in this
  release.

The Pilot's call-out caught the process gap inside the same session
that shipped v2.3.17 + v2.3.18, before the outside reviewer was sent
the lean prompt. Inside-first sequencing did its job — when actually
applied.

## [2.3.18] - 2026-04-27

Focused single-concern release: the Q3 `pass_rate_pct` rename. Closes
the v2.3.17 deferred item — wire field unification across every
surface that exposes a pass-rate value, plus the storage column
migration. Single canonical name, single canonical scale (percent
0–100, 1dp).

### Changed

- **`pass_rate_pct` is now the single canonical wire field on every
  surface that exposes a pass-rate value.** REST `/api/v1/stats`,
  `/api/v1/agents`, `/api/v1/contracts/{name}/quality-trend`,
  `/api/v1/rejection-summary`, `/api/v1/analytics/summary`,
  `/api/v1/audit/events/{event_id}` (and the in-memory equivalents on
  the MCP in-process server: `get_quality_metrics`, `list_agents`,
  `get_quality_trend`). Single name, single scale; no surface returns
  the bare `pass_rate` or the redundant `pass_rate_ratio`.
- **DuckDB `quality_stats.pass_rate` column renamed to
  `pass_rate_pct`** with a one-time migration that multiplies existing
  ratio values × 100. Idempotent — re-running the migration on an
  already-migrated DB is a no-op (SQLite `OperationalError` on
  `RENAME COLUMN` is caught and skipped). After migration, no bare
  `pass_rate` column remains anywhere in the stored schema.
- **Audit-event payload (`get_audit_event(event_id)` and
  `AuditEventDetail`)** emits `pass_rate_pct`. Same source of truth as
  the wire surfaces; auditors who replay an event get the same field
  name they saw at write time.

### Removed

- **`pass_rate_ratio` companion field** removed from every surface that
  emitted it (was added in v2.3.14 as a transitional dual-shape
  alongside `pass_rate`). Single canonical name closes the dual-shape
  debt.
- **Bare `pass_rate` field** removed from every wire response and
  every model field. Was previously emitted as percent on some surfaces
  (`monitoring.py`) and as ratio on others (`mcp_server.py`,
  `routes_analytics.py`, `quality_stats.py`) — exactly the
  inconsistency Persona B's Q3 finding named.

### Recurrence test

- **`tests/test_v2_3_18_q3_pass_rate_pct_rename.py`** (8 tests):
  - DuckDB column is `pass_rate_pct` on a fresh DB.
  - Legacy DB with `pass_rate` column is migrated: column renamed AND
    existing ratio values multiplied × 100 (proves migration ran).
  - `get_summary`, `get_windowed_summary`, `list_agents` all return
    `pass_rate_pct` and absent of `pass_rate` / `pass_rate_ratio`.
  - REST `/api/v1/stats` and `/api/v1/agents` enforce the same.
  - Audit-event payload from `get_event(event_id)` returns
    `pass_rate_pct`.
  PR/release blocks if any wire surface re-introduces the legacy names.

### Queen's Standard four-answer block

1. **Easy:** ship `pass_rate_pct` only on new surfaces, leave existing
   `pass_rate` / `pass_rate_ratio` everywhere for back-compat. Two
   names, two scales, the dual-shape debt persists indefinitely.
2. **Hard:** rename storage column, rename wire field on every existing
   surface, drop `pass_rate_ratio`, ship the recurrence test that
   blocks future re-introduction.
3. **Take:** hard. There are no real users — back-compat for an
   internal-only field is theatre.
4. **Recurrence:** 8-test contract assertion in same release. Blocks
   regression at PR time.

## [2.3.17] - 2026-04-27

The Persona B re-evaluation round produced a 19-finding outside report
plus inherited Persona A items. Rather than chase each finding locally
(the v2.3.10–v2.3.16 sprint pattern that produced the 28-item punch
list this release was triggered by), v2.3.17 ships seven structural
clusters paired with their recurrence tests in the same release —
Queen's Standard / Protocol 32 mechanic 1.

Sonnet pre-implementation review is now the default working pattern.
On Cluster 1, Cluster 3, and Cluster 4, Sonnet's read of the actual
code reframed the cluster substantially before any production code was
written — reducing the four originally-claimed F-A/F-D/F-F bugs to one
real bug + one transparency fix; redirecting F-C from a wrong-shape
"history rewrite" to the correct at-row-insert demotion; and locking
in the parity test with an explicit `KNOWN_ASYMMETRIES` allowlist that
v2.4 must drain to empty.

### Added

- **`effective_rule_hash` on every validate response (REST + MCP, F-J
  / Q4).** A 4th hash field alongside `entry_hash`, `content_hash`,
  `contract_hash`. The existing triplet is invariant to context;
  `effective_rule_hash` hashes the resolved Rule set actually used by
  the validator after override application. Two validate calls with
  the same record but different contexts that produce different rule
  sets now produce different `effective_rule_hash` values, closing
  the audit-replay gap Persona B named in F-J.

- **`context_warning` field on validate response (F-D, Pilot decision
  c).** When a context is supplied but not declared on the contract,
  the engine continues to use base rules (fail-open by design —
  contexts double as stats-tagging metadata for "demo", "ci", "test"),
  but the response now carries a `context_warning` string identifying
  the divergence. Authors who typo a real context name (`prodd` for
  `prod`) can now see the typo without breaking metadata-tag use.

- **`include_metadata: bool = false` request flag on validate (Q12).**
  When true, `engine_version` is included on the wire response. Default-
  off matches MCP reference-server minimalism. Durable per-call engine
  version is preserved on the audit-event payload retrievable via
  `get_audit_event(event_id)`.

- **`record_id` on MCP `validate_record` schema (F-Q).** Both proxy
  and in-process MCP surfaces declare `record_id` in
  `inputSchema.properties`. Caller code using `record_id` against REST
  works identically via MCP.

- **`list_audit_events` and `get_audit_event` MCP tools on both
  surfaces (F-L).** Closes the v2.3.x gap where REST had K1+K2 audit-
  event surfaces but MCP had none.

- **Three temporal rules on `mifid_transaction_report` exemplar
  (Q14, hard path, severity:error):**
  `trade_date_matches_execution_date` (regex on shape),
  `trade_date_not_in_future` (`compare_to: today, lte`),
  `execution_timestamp_not_in_future` (`compare_to: now, lte`).
  Closes Persona B's N-4 — `trade_date: 2030-01-01` previously
  passed; the third rule catches the self-consistent future-trade
  case (2030 trade + 2030 timestamp). Sonnet/Grok right-of-reply
  round confirmed T+0 only is the regulatory norm per RTS 22 Annex
  Table 2 + ESMA Q&A TR 9.1/TR 10.2 + FCA Market Watch 57+62.

- **44 new recurrence tests across 7 cluster test files.**

### Changed

- **`mifid_transaction_report.execution_timestamp_format` is now RTS
  25 microsecond UTC (Q13, option a).** Format regex:
  `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$`. Closes Persona B's
  N-1 four-way contradiction — rule-required claim, format rule,
  `explain_error` text, and JSON Schema export now align.

- **`/api/v1/stats?contract=X` actually scopes the response (F-H /
  N-7).** The proxy passed `contract` as a query param but
  `/api/v1/stats` did not declare it; FastAPI silently dropped the
  parameter and returned the unfiltered summary. The endpoint now
  accepts and applies the filter, scoping `by_contract`,
  `top_failing_fields`, `top_failing_fields_by_agent`, `recent_history`,
  `dimensions.by_severity`, and totals.

- **`top_failing_fields_by_agent` no longer leaks `"?"` as a field
  name (F-K / N-8).** The SQLite hydration path emits `"?"` because
  the aggregate persists rule_failure_counts but not field names.
  Aggregator output now transforms `"?"` to `field: null` plus
  `field_provenance: "unavailable"`.

- **`get_quality_trend(by=rule)` reports honest `data_confidence`
  (N-2).** Previously returned `data_confidence: "no_data"` and
  `pass_rate: 1.0` even when violations were present. Route now
  fetches `by=date` aggregation to compute `total_validations` when
  `by=rule` was asked; `pass_rate` is `null` per-rule.

- **`approve_contract` invariant: at most one ACTIVE history row per
  (contract, version) (F-C).** Before INSERTing a new ACTIVE row,
  the history backend demotes prior ACTIVE rows for the same
  (contract_name, version) to ARCHIVED. History remains append-only
  for chain integrity; the `status` column is correctly updated when
  truth changes. Canonical terminal status: `archived`.

- **MCP-only `validate_record` historical-hash + context path applies
  Rule objects, not raw dicts (F-A on MCP in-process).** Both MCP
  `_tool_validate_record` and `_tool_validate_batch` route through
  the new `Registry.get_rules_with_context_status` method so REST
  and MCP converge on identical rule resolution.

- **Proxy returns the same structured error envelope as the in-process
  server (F-I, V-1).** The proxy's loose `{"error": "..."}` dict shape
  is replaced with `{error: {error_code, kind, status, detail,
  remediation}}`. Subsumes V-1: tools that use `arguments[required_key]`
  without `.get()` now produce `INVALID_REQUEST` envelopes instead of
  leaking `KeyError` strings.

- **Three-way version-source consistency invariant (F-S).**
  `pyproject.toml == importlib.metadata.version("opendqv") ==
  /openapi.json info.version`. Was four-way; `/config` no longer
  exposes the field per Q11.

- **`reporting_firm_lei_format` and `executing_entity_lei_format`
  error messages state SHAPE-only (N-5).** No longer claim ISO 17442
  check-digit verification (which the rule does not perform). Full
  mod-97 verification deferred to v2.4 capability.

### Removed

- **`engine_version` dropped from `GET /config` (Q11, Sonnet option
  iv).** `/openapi.json info.version` is REQUIRED by the OpenAPI 3.x
  spec — that's the canonical surface. `/config` remains the operator-
  diagnostic endpoint for non-version fields. Closes a duplicate-
  exposure CRT170-J violation.

### Security / Hardening

- **Reserved `OpenDQV_SA_*` agent_id prefix is now rejected at the
  write boundary on all four validate surfaces (F-B / Cluster 2).**
  REST `POST /api/v1/validate` and `POST /api/v1/validate/batch` use
  Pydantic field validators; MCP `validate_record` and `validate_batch`
  on the in-process server use explicit guards returning
  `INVALID_AGENT_ID` envelopes. Combined with the v2.3.15 output-side
  suppression, a spoofed `OpenDQV_SA_*` identity can no longer be
  accepted, persisted, then hidden by design.

- **Cold-client MCP smoke is now a release-blocking gate (Q10 /
  Protocol 32 mechanic 3).** Both proxy and in-process probes against
  the actual built artifact, before `git tag` or `gh release create`.

### Deferred to v2.3.18 / v2.4

- **`pass_rate_pct` rename (Q3) — v2.3.18.** Wire rename + DuckDB
  column migration deserves its own focused release rather than landing
  alongside seven other clusters.
- **F-M (`create_contract_draft` on proxy)** — requires a new REST
  endpoint that does not exist today. Tracked in `KNOWN_ASYMMETRIES`.
- **F-E (`get_contract_jsonschema` 4-min hang in Claude Desktop
  transport)** — engine returns in 0.4s; wrapper-transport problem,
  not engine. Investigation task only.
- **N-3 negative price_min vs price_type yield/spread/basis_points**
  — separate domain contract (not mifid).
- **N-9 JSON Schema export improvements** (lookup→enum,
  not_empty→minLength:1).
- **Bundle-as-draft architectural change** — v2.4 CRT.
- **CRT-N proxy unification** — v2.4; parity test is the load-bearing
  protection until then.
- **ISO 17442 mod-97 check-digit capability** for LEI/ISIN — v2.4.

## [2.3.16] - 2026-04-26

### Changed

- **MCP proxy (`opendqv_mcp_proxy.py`) catches up with v2.3.15.** The
  in-process MCP server (`opendqv/mcp_server.py`) shipped the
  `include_system` parameter on `get_quality_metrics` and `list_agents`
  in v2.3.15; the proxy did not. v2.3.16 brings the proxy's tool
  inputSchemas and dispatcher in line — the suppression now works
  identically whether a client connects in-process or through the proxy.

  *Why this matters.* The dual-path discipline
  (`feedback_release_mcp_dual_path.md`) exists precisely so
  client-visible tool surfaces don't drift between the two entry
  points. v2.3.15 missed the proxy. v2.3.16 closes that gap before
  the next external eval.

## [2.3.15] - 2026-04-26

### Added

- **Reserved `OpenDQV_SA_*` agent_id prefix for OpenDQV-owned system
  traffic.** Smoke probes, demos, MCP self-tests and perf harnesses
  now sit under a clearly-namespaced prefix
  (`OpenDQV_SA_[Category]_[Scope]`). Customer-facing metrics endpoints
  suppress these by default — `/api/v1/stats`, `/api/v1/agents`, MCP
  `get_quality_metrics`, MCP `list_agents`. Pass `include_system=true`
  to surface them; each agent row carries an `is_system_agent` flag
  for diagnostic views. An explicit `agent_id=` filter bypasses
  suppression so callers always get exactly what they ask for.
  CRT173 / Persona B findings 26, 27, 28.

  *Why this matters.* The Persona B reviewer reading "production-shaped"
  metrics saw `impostor`, `cursor-walk`, `smoke-v239` mixed into the
  agent listing and read it as a tenant-isolation smell. The leak was
  cosmetic (dev/test agent_ids in a single-tenant dev DB), but the
  signal was real: customer-visible surfaces should not advertise
  internal traffic. Suppression is at the read surface only — no
  write-path rejection, since the prefix is self-evidently ours.

## [2.3.14] - 2026-04-26

### Added

- **`pass_rate_ratio` (0–1, 4dp) on every quality surface** alongside the
  legacy `pass_rate` (percent, 1dp). Two integrations had already pushed
  ratios through fields named `pass_rate` after assuming the unit;
  naming the unit at the field name removes the ambiguity. Emitted on
  `get_summary`, windowed and per-agent summaries, the agents listing,
  and per-contract quality metrics. The legacy `pass_rate` continues
  unchanged. CRT173 / Persona B finding 20.

- **`mode` and `would_have_failed` always populated on validate
  responses.** Previously `null` in enforcement mode (the default),
  forcing every caller to defensively branch on null. Now `mode`
  always returns `"enforcement"` or `"observation_only"` and
  `would_have_failed` always returns `not valid` (single) or
  `summary.failed > 0` (batch). Wire shape is deterministic.
  CRT173 / Persona B finding 25.

### Changed

- **MCP errors return a structured envelope.** Every tool that
  previously emitted `{"error": "<string>"}` or `"Error: {exc}"` now
  returns `{"error": {error_code, kind, status, detail, remediation}}`.
  Stable, machine-readable error codes (e.g. `CONTRACT_NOT_FOUND`,
  `BATCH_TOO_LARGE`, `MISSING_CREATED_BY`, `DRAFT_RATE_LIMITED`,
  `INTERNAL_ERROR`) replace ad-hoc string matching, and every error
  carries an actionable `remediation` hint. Single source for all 22
  error sites in `mcp_server.py`. CRT173 / Persona B finding 24.

- **`confidence_note` always populated as a string.** `medium` and
  `high` bands now return `""` instead of `None`. The wire shape was
  previously three-state: null, absent, or a string — two states too
  many. `quality_confidence()` now returns `tuple[str, str]`. CRT173
  / Persona B finding 23.

### Documentation

- **`contract_hash` field marked deprecated** in API model
  descriptions. Preserved on the wire as an alias of `entry_hash` for
  v2.3.x; removal targeted for v2.4. New integrations should read
  `entry_hash` and `content_hash`. CRT173 / Persona B finding 21.

- **Window field semantics** documented inline in `monitoring.py`:
  `window_hours` is the canonical caller-requested window;
  `effective_window_seconds` reports actual data coverage from the
  oldest event to now; `requested_window_hours` is deprecated in
  favour of `window_hours`. CRT173 / Persona B finding 22.

## [2.3.13] - 2026-04-26

### Added

- **`total_error_violations` / `total_warning_violations`** in the
  metrics surface (`get_summary`, `get_quality_metrics`). Names what
  the math is — *rule-violation sums*, not record counts. The legacy
  keys `total_errors` / `total_warnings` continue to ship as aliases
  and will be removed in v2.4. Counter semantics are now documented
  on every MCP tool description: `total_validations` / `total_pass` /
  `total_fail` are RECORD counts; `total_error_violations` /
  `total_warning_violations` are VIOLATION sums (a single failing
  record with N broken rules contributes N). CRT173 / Persona B
  finding 14.

- **`p99_9_ms` and `max_ms` on every latency stats surface** — both
  the global `_latency_stats` and per-contract `get_contract_latency`.
  p99 alone hides outlier behaviour at high traffic; the long tail
  is what wakes ops at 3am. CRT173 / Persona B finding 18.

- **`top_failing_rules_ranked` array of `{rule, count}`** on every
  trend surface, alongside the legacy `top_failing_rules` dict. JSON
  dicts have no inherent ordering and cross-contract rule-name
  collisions silently overwrite — the ranked array is the canonical
  shape from v2.4. CRT173 / Persona B finding 19.

- **`GET /api/v1/agents`** — list the source-systems that emitted
  validation traffic in the window. Returns
  `[{agent_id, total_validations, total_pass, total_fail, pass_rate,
  last_seen}]`, sorted by traffic volume desc. Closes the gap where
  operators had to filter by `agent_id` without first being able to
  enumerate the values. New MCP tool `list_agents` mirrors it on both
  the in-process server and the proxy. CRT173 / Persona B finding 16.

- **Multi-dimensional quality trend.** `GET /contracts/{name}/quality-trend?by=date|agent|context|rule`
  regroups the same underlying batch validation history along whichever
  dimension the caller wants to diagnose. `by=date` (default) preserves
  the exact legacy shape; `by=agent`, `by=context`, `by=rule` return
  buckets keyed by `key` (rule mode adds a `violation_count` field and
  is sorted desc). Mirrored on the MCP tool `get_quality_trend`. CRT173
  / Persona B finding 17.

### Changed

- **Warning aggregation regression-pinned.** 26 bundled contracts have
  `severity: warning` rules; the path that increments
  `total_warning_violations` and `dimensions.by_severity.warning` is
  now under explicit invariant test (no behavioural change — just a
  regression net so a future refactor cannot silently zero out the
  warning bucket). CRT173 / Persona B finding 15.

## [2.3.12] - 2026-04-26

### Added

- **Uniform constraint exposure on `GET /api/v1/contracts/{name}`.**
  `RuleInfo` now surfaces `pattern`, `min`, `max`, `min_length`,
  `max_length`, `format`, `compare_to`, `compare_op`, `min_age`,
  `max_age`, `allowed_values`, `lookup_file`, `checksum_algorithm`,
  and `negate` on every rule. Previously each rule type bled different
  constraint fields onto the response — agents had to know the rule
  type before they could read its bound. Now the contract surface is
  uniform: the value is `null` when not applicable, populated when it
  is. CRT173 / Persona B finding 8.

- **`GET /api/v1/contracts/{name}/versions`** — lean version listing
  (metadata only, no rule bodies). Returns `version`, `status`,
  `entry_hash`, `content_hash`, `created_at`, `owner`, `owner_team`,
  `approved_by`, `proposed_by` for each historical snapshot. Use to
  drive a version picker or pin a downstream call to a specific hash
  via `validate_record(hash=...)` without paying the cost of streaming
  every rule body. New MCP tool `list_versions` mirrors it. CRT173 /
  Persona B finding 9.

- **`validate_record(hash=...)` and `validate_batch(hash=...)` accept a
  `content_hash` (or `entry_hash`) to pin validation to a specific
  historical contract version.** Returns 404 when the hash does not
  match a known history entry — silent fallback to the latest version
  is a regulator-grade hazard for point-in-time replay. Wired through
  REST, the bundled MCP server, and the proxy. CRT173 / Persona B
  finding 10.

- **`GET /api/v1/contracts/{name}/diff` accepts `hash_a` + `hash_b`.**
  Compares two historical snapshots identified by entry_hash or
  content_hash and returns `rules_added`, `rules_removed`,
  `rules_changed`, `metadata_changed`, plus `from_hash` / `to_hash`
  on the response so the payload is self-documenting. The existing
  version-pair mode is preserved for backward compatibility. New MCP
  tool `compare_contracts` exposes the hash-pair surface. CRT173 /
  Persona B finding 11.

- **`GET /api/v1/contracts/{name}/jsonschema`** — emits a JSON Schema
  draft 2020-12 document derived from the contract's rules. Maps
  `not_empty` → `required[]`, `regex` → `pattern`, `min`/`max` →
  `minimum`/`maximum`, `min_length`/`max_length` →
  `minLength`/`maxLength`, `allowed_values` → `enum`, `date_format` →
  `format: date` or `date-time`. Cross-field and stateful rules
  (`unique`, `compare`, `required_if`, `lookup`) cannot be expressed in
  plain JSON Schema and are surfaced under `x-opendqv-unmapped` —
  consumers see exactly what was lost in translation. Also accepts
  `?context=X` to emit the schema for a context's effective rule set.
  New MCP tool `get_contract_jsonschema`. CRT173 / Persona B finding 12.

- **`GET /api/v1/contracts/{name}` accepts `?context=X`.** Returns the
  effective rule set with the named context's overrides resolved —
  what `validate_record(context=X)` would actually run. Composes with
  `?hash=` and `?version=`. The MCP `get_contract` tool gains a
  matching `context` parameter. CRT173 / Persona B finding 13.

## [2.3.11] - 2026-04-26

### Fixed

- **`date_format` rule honours the contract's declared format strictly.**
  Previously a rule like `format: YYYY-MM-DD` would silently accept
  `"26/04/2026"` because the implementation fell through to a permissive
  default. The validator now translates human-readable patterns
  (`YYYY-MM-DD`, `DD/MM/YYYY`, `YYYY-MM-DD HH:MM:SS`) to strftime codes,
  so the rule enforces what its `error_message` claims. When no `format`
  is declared the default is ISO 8601 (date or datetime) — locale-ambiguous
  formats are never silently accepted. Both the Python single-record path
  and the DuckDB batch path (now using `TRY_STRPTIME` instead of
  `TRY_CAST AS DATE`) share the translator. CRT173 / Persona B finding 6.

- **Context overrides keyed by rule name no longer mint phantom rules.**
  Previously, a context override keyed by a rule name (e.g.
  `proof_of_play.yaml`'s `revenue_ceiling: { severity: error, ... }`)
  would not match any rule's `field` and would fall through to a
  fallback that synthesised a `not_empty` rule whose `field` was set
  to the rule name. The synthetic rule fired against a column that
  did not exist, producing error envelopes with `field` set to the
  rule name (e.g. `"revenue_ceiling"` instead of `"revenue_gbp"`),
  the wrong `error_code` prefix (`OPENDQV_NOT_EMPTY_*`), the wrong
  `suggested_fix` template, and poisoning `top_failing_fields[]`
  aggregation. Resolution order is now: rule-name match (specific,
  modifies one rule) → field-name match (broad, modifies all rules
  on that field, e.g. `customer.kids_app.age` → both `age_minimum`
  and `age_reasonable`) → mint a synthetic rule for genuinely new
  constraints. Both syntaxes are supported; the resolved error
  envelope always carries the original rule's `field`, `type`, and
  `error_code`. CRT173 / Persona B finding 7.

## [2.3.10] - bundled into 2.3.11 (no separate tag)

### Fixed

- **`/validate/batch` with empty records now returns HTTP 400.**
  Previously a request with `records: []` returned a silent
  `{summary: {total: 0, passed: 0, failed: 0}, results: []}` —
  CI assertions like "passed > 0 and failed == 0" passed on
  empty batches, masking upstream filter bugs. Matches Cloud's
  behaviour. Detail: `"records must not be empty"`.

- **`compare` rule's `suggested_fix` now branches on cross-time
  vs cross-field sub-cases.** Previously a rule like
  `created_date_not_future` (which compares a field to `now`)
  returned the cross-field template ("satisfies the cross-field
  comparison in the error message"), which was nonsensical for
  temporal comparisons. The template now consults the rule's
  `compare_to`: temporal sentinels (`today`, `now`) get a
  date/time-aware hint; named fields get a hint that names the
  other field; absent `compare_to` falls back to a generic line.

- **`uptime_seconds` is now returned as an integer.** Previously
  rendered with microsecond precision (`39710.428693`) which is
  meaningless on a process-uptime field.

### Changed

- **`validate_batch` MCP tool description** now documents the
  ~70ms fixed setup cost and the empty-batch rejection. For
  batches under ~70 records, individual `validate_record` calls
  are faster.

- **`validate_record` MCP tool description** now clarifies the
  trust distinction between `caller_principal` (server-derived
  from JWT `sub`, trustable) and `agent_id` (caller-asserted,
  not trustable).

Found via CRT173 / Persona B cross-engine evaluation. All five
items are 🟡 polish severity — no client migration required.

## [2.3.9] - 2026-04-26

### Added

- **`GET /audit/events` and `GET /audit/events/{event_id}` — auditor-readable
  surface over the `quality_stats` audit table.** Previously, every
  `/validate` and `/validate/batch` call wrote a row to `quality_stats`
  recording the contract, version, agent_id, server-derived
  `caller_principal`, mode, and pass/fail counts — but the only way to
  read those rows back was to open the SQLite file directly. There was
  no API surface for an auditor to ask "what did caller X submit
  against contract Y last week, and was any of it rejected?"
  - `GET /audit/events/{event_id}` (K1) returns one row by the
    `event_id` UUID emitted on the original validation response,
    including JSON-decoded `rule_failure_counts`. 404 if not found.
  - `GET /audit/events` (K2) lists rows in `recorded_at DESC, id DESC`
    order with cursor pagination. Filters: `contract`, `contract_version`,
    `context`, `since` (default: 24 hours ago), `until`, `agent_id`,
    `caller_principal`, `valid` (true ⇒ `failed=0 AND total_records>0`,
    false ⇒ `failed>0`), `mode`, `cursor`, `limit` (1–1000, default
    100). The list response echoes `effective_since` so a client can
    detect that the default 24h window was applied. `has_more` is
    computed via a `limit+1` lookahead so the boundary case where the
    last page is exactly `limit` rows is correct.
  - Per-record validation errors are not stored on the audit row —
    they live in the optional TRACE_LOG (`AUDIT_MODE=signed`).

  Both endpoints are auth-gated to **admin or auditor**, matching
  `/trace/verify` and `/config`.

  **v2.4 caveat — per-contract auditor scoping.** Today the auditor
  role is global. An auditor can pass any `contract` filter and read
  `caller_principal` values across all contracts. For multi-tenant SaaS
  deployments, per-contract auditor scoping must be added in
  `security/auth.py` before this endpoint can be safely exposed across
  tenants. Until then, this surface assumes a single-tenant trust
  boundary. The caveat is documented inline in
  `opendqv/api/routes_audit_events.py`.

  Found via CRT172 / K1 + K2 audit. Working principle (extends
  CRT170/J1, /J3, /J4, /J6, /J2): a response field's value must reflect
  what its name claims. Each filter is mapped 1:1 to a column on
  `quality_stats`; cursor pagination uses the `(recorded_at, id)`
  integer-PK tiebreaker so ordering is total even when rows share a
  millisecond.

  No client migration required — additive endpoints.

## [2.3.8] - 2026-04-26

### Added

- **`GET /config` — consolidated tenant configuration snapshot,
  auth-gated to admin or auditor.** Previously, an auditor wanting to
  confirm "what `AUTH_MODE`, `AUDIT_MODE`, federation state, and rate
  limits is this node actually running?" had to read three surfaces
  (`/`, `/health` extended, the `opendqv.config` module) and would
  still miss federation, MCP, and policy values. The new endpoint
  returns one structured response with named sections:
  - `auth` — mode, token expiry, secret-key insecurity flag
  - `audit` — mode (basic/signed), proxy header trust
  - `storage` — DB backend, contracts dir, presence of DB URL
  - `limits` — batch, isolation, SSE caps
  - `rate_limits` — default/validate/tokens, active flag
  - `federation` — federated mode, presence of upstream/join token
  - `mcp` — remote mode, presence of API URL/token
  - `policy` — strict-draft-validation, contract edit mode

  **Secrets are deliberately omitted.** `SECRET_KEY`, `DB_URL`,
  `JOIN_TOKEN`, and `MCP_TOKEN` values are never returned — only a
  boolean indicator of presence (e.g. `auth.secret_key_insecure`,
  `mcp.token_set`). Test guards in
  `tests/test_crt172_k5_config_endpoint.py::TestConfigSecretsNeverLeak`
  pin this contract.

  Found via CRT172 / K5 audit. Working principle (extends CRT170/J1,
  /J3, /J4, /J6, /J2): a response field's value must reflect what its
  name claims. Each section block is named for the regulatory concern
  it represents.

  No client migration required — additive endpoint.

## [2.3.7] - 2026-04-26

### Added

- **`caller_principal` is now returned on every validation response and
  recorded on every audit row — server-derived from the authenticated
  token, cannot be spoofed.** Previously, `agent_id` was the only
  caller-attribution field on a validation response. It is
  caller-asserted: any client could send `{"agent_id": "anyone"}` in
  the request body and have that string echoed in the response and
  written to the SQLite audit row. There was no field on the response
  whose value was provably the authenticated identity, so a downstream
  system could not use the response alone to attribute a validation
  back to a specific token.

  `caller_principal` closes that gap. It is derived server-side from
  the JWT `sub` claim (or `"anonymous"` in `AUTH_MODE=open`) and
  appears on:
  - `POST /api/v1/validate` → `ValidateResponse.caller_principal`
  - `POST /api/v1/validate/batch` → `BatchValidateResponse.caller_principal`
  - the `caller_principal` column on `quality_stats` (idempotent
    `ALTER TABLE` migration on first boot of v2.3.7)

  `agent_id` is preserved unchanged for self-labelling and session
  correlation. The two fields now have orthogonal semantics:

  | Field | Source | Trust | Use |
  |---|---|---|---|
  | `agent_id` | request body | caller-asserted | session/agent label |
  | `caller_principal` | JWT `sub` | server-verified | audit attribution |

  Found via CRT170 / J2 audit. Working principle (extends CRT170/J1,
  /J3, /J4, /J6): a response field's value must reflect what its name
  claims. `caller_principal` claims to be the authenticated identity
  and provably is.

  No client migration required — the new field is additive. Old call
  sites that don't pass `caller_principal` continue to work; the
  column defaults to empty string.

## [2.3.6] - 2026-04-26

### Changed (client-visible breaking change)

- **`error_code` is now rule-instance-shaped, not rule-type-shaped.**
  Previously, every rule of the same type collapsed to the same code:
  `valid_email`, `valid_phone`, and `username_format` (all `regex`) all
  returned `OPENDQV_REGEX_001`, so consumers using `error_code` as a
  routing key in dead-letter queues could not distinguish "bad email"
  from "bad phone number" — the response field's name (`error_code`)
  did not match its meaning. Codes now encode the actual rule:

  ```
  before: OPENDQV_<TYPE>_001
  after:  OPENDQV_<TYPE>_<RULE_NAME>
  ```

  | Rule | Before | After |
  |---|---|---|
  | `valid_email` (regex) | `OPENDQV_REGEX_001` | `OPENDQV_REGEX_VALID_EMAIL` |
  | `valid_phone` (regex) | `OPENDQV_REGEX_001` | `OPENDQV_REGEX_VALID_PHONE` |
  | `name_required` (not_empty) | `OPENDQV_NOT_EMPTY_001` | `OPENDQV_NOT_EMPTY_NAME_REQUIRED` |

  Single source of truth: `Rule.cached_error_code` (`rule_parser.py`).
  Both the single-record and batch (DuckDB) paths now read from the same
  cached value, so they cannot diverge.

  **Migration.** Clients matching exactly on the old code must update;
  clients matching on the type prefix continue to work:

  ```python
  # before: dead-letter on regex failure
  if err["error_code"] == "OPENDQV_REGEX_001":         # breaks
      ...
  # after — by rule type (prefix match, recommended for category routing)
  if err["error_code"].startswith("OPENDQV_REGEX_"):   # works
      ...
  # after — by specific rule (exact match, recommended for fine-grained routing)
  if err["error_code"] == "OPENDQV_REGEX_VALID_EMAIL": # works
      ...
  ```

  Found via CRT170 / J4 audit; covered by
  `tests/test_crt170_j4_error_code_instance.py` (15 tests) plus an
  inverted assertion in `tests/test_smoke.py::TestErrorCodesSmoke`.
  Documentation updated in `docs/error_codes.md`. Working principle
  (extends CRT170/J1, /J3, /J6): a response field's value must reflect
  what its name claims. `error_code` claims to identify the rule that
  failed; it now does.

## [2.3.5] - 2026-04-26

### Added

- **`data_confidence` band now appears on every quality analytics
  response.** Previously, the confidence band (`no_data` / `low` /
  `medium` / `high`) and accompanying `confidence_note` shipped only on
  `get_quality_metrics` (MCP) and the `/stats`-derived REST surface.
  `get_quality_trend` and `get_rule_velocity` returned numbers without
  any signal of how much underlying data they were derived from, so a
  client could not tell a 100% pass-rate built on 3 validations apart
  from one built on 30,000. Both tools, plus their REST counterparts
  `GET /api/v1/contracts/{name}/quality-trend` and
  `GET /api/v1/analytics/rule-velocity`, now return:
  - `data_confidence` — `no_data`, `low` (<10), `medium` (<100), `high`
  - `confidence_note` — caveat string when confidence is `no_data` /
    `low`, otherwise `null`
  - `total_validations` — the underlying count the band was computed
    from (sum across days for trend; window-scoped count for velocity)
  Single source of truth lives in
  `opendqv.core.quality_stats.quality_confidence(total)`; both MCP
  tools and both REST endpoints call it, so thresholds and copy stay
  in sync. Found via CRT170 / J6 audit. Working principle (extends
  CRT170/J1, /J3): a response surface that reports a metric must also
  report enough context for the consumer to judge that metric's
  reliability.

  No client migration required — the new fields are additive on the
  response.

## [2.3.4] - 2026-04-25

### Changed (client-visible behaviour)

- **`POST /api/v1/validate` with `observe_only=true` now returns the real
  `valid` value.** Previously, observation mode hardcoded `valid: true`
  in the response body even when the record failed every rule, forcing
  callers to read `would_have_failed` to recover the truth. The response
  field's name (`valid`) now matches its meaning. Observation mode's
  blocking semantic is unchanged: HTTP is always 200, downstream systems
  do not block, violations are still reported in `errors[]`. The
  redundant `would_have_failed` field is retained for backward
  compatibility — in observation mode it is the negation of `valid`.
  Found via CRT170 / J1 audit; covered by
  `tests/test_observe_only.py::TestJ1ValidCoherenceAcceptance` plus
  realigned assertions across `test_observe_only.py` and `test_smoke.py`.
  Working principle (extends CRT170/J3): a response field's value must
  reflect what its name claims. Observation mode is a *blocking* policy,
  not a truth policy.

  Migration: clients that read `valid` to mean "was this record valid in
  reality" already worked correctly under the prior shape *only* in
  enforcement mode and were silently broken in observation mode. They
  now work correctly in both. Clients that read `valid` to mean "did the
  request return 200" should switch to checking `r.status_code` directly.

## [2.3.3] - 2026-04-25

### Fixed

- **Format-class rules no longer double-fire on absent fields.** When a
  contract declared both `not_empty` and a format-class rule (e.g.
  `date_format`, `regex`, `min`, `max`, `range`, `min_length`,
  `max_length`, `compare`, `checksum`, `cross_field_range`,
  `conditional_lookup`, `geospatial_bounds`, `age_match`, `age`) on the
  same field, an empty record reported **two errors for the same fact**
  — one from `not_empty`, one from the format-class rule firing on the
  None / empty-string value. The format-class rules now skip absent
  values (None, missing key, whitespace-only string) and `not_empty` is
  the single catcher for absence. Both the single-record handlers and
  the batch DuckDB SQL queries are covered. The relational-class rules
  (`compare`, `cross_field_range`, etc.) skip when the **target** field
  is absent but still fail when a present target references an **absent
  counterpart** (a real cross-field error). Found via CRT170 / J3 audit;
  covered by `tests/test_rule_coverage.py::TestAbsentFieldSkipping` and
  realigned assertions across `test_rule_coverage.py`, `test_core.py`,
  `test_geospatial.py`, `test_p1_features.py`. Working principle: a
  presence-class rule is the single catcher for absence; format-class
  rules characterise the shape of a value and have nothing to say about
  an absent one.

## [2.3.2] - 2026-04-25

### Fixed

- **`GET /contracts/{name}?hash=<historical_hash>` now echoes the requested
  hash on the response.** Previously the endpoint correctly returned the
  historical contract body but reported the LATEST `entry_hash` /
  `content_hash` / `contract_hash` for that contract version, not the
  hashes of the snapshot whose body was returned. Cause: the response-
  shape code resolved the body by hash but then walked history matching
  by `version` only; when two history entries shared a version (e.g. a
  symmetric in-place description edit) it picked the most recent entry's
  hashes. The lookup now matches the snapshot whose `entry_hash` or
  `content_hash` equals the requested value, so the body and the hash
  fields always agree. Found by an external reviewer running a CRT169
  symmetric-edit round-trip; covered by
  `tests/test_versioning.py::TestHistoricalHashEcho`.

### Changed

- **`contexts` array in `GET /contracts/{name}` is now alphabetically
  sorted.** Previously the response used insertion order, which was
  deterministic within a single boot but could change across reloads
  if the YAML key order changed. Alphabetical ordering is stable across
  reloads and across nodes, which makes it safer for diffing audit
  responses. Clients that depended on insertion order for UI display
  should re-sort client-side if a different order is needed.

## [2.3.1] - 2026-04-25

### Fixed

- **`opendqv_mcp_proxy.py` advertised version string.** The proxy's
  `initialize` response reported `serverInfo.version: 2.2.5` even after
  the v2.2.6/v2.2.8/v2.3.0 releases — a hardcoded string that was
  missed during those bumps. Now reports `2.3.1`. No tool behaviour
  changed; this only affects the version string Claude Desktop and other
  MCP clients see at handshake time. Spotted by a stdio-level smoke
  test of the proxy after v2.3.0 shipped.

## [2.3.0] - 2026-04-25

### Changed — hash domain expansion (CRT169) — BREAKING for hash values

- **Hash domain now covers every semantically meaningful contract field.**
  `entry_hash` is computed over: `name`, `version`, `status`, `owner`,
  `owner_email`, `owner_team`, `asset_id`, `description`,
  `downstream_consumers`, `rules`, `contexts`, plus the chain fields
  (`prev_hash`, `opendqv_node_id`, `updated_at`). Pre-v2.3.0 chain entries
  hashed only `version`/`status`/`description`/`owner`/`rules`/`contexts`,
  so edits to `owner_email`, `owner_team`, `asset_id`, or
  `downstream_consumers` could update a contract without producing a new
  chain entry — the audit trail silently pointed at a stale snapshot.
- **New `content_hash` companion to `entry_hash`.** `content_hash` covers
  the content fields only (excludes `prev_hash`, `opendqv_node_id`,
  `updated_at`), so two byte-identical contracts share `content_hash`
  even when recorded at different times or on different nodes. Both
  hashes are returned on `validate`, `validate/batch`, and
  `GET /contracts/{name}` responses; `?hash=<value>` accepts either.
- **Canonical JSON serialisation for hash inputs.** `sort_keys=True`,
  `separators=(",", ":")`, `ensure_ascii=False` — guarantees byte-stable
  hashing across Python versions and dict insertion orders.
- **Scrub-and-restart migration.** On first boot under v2.3.0, the
  engine deletes all chain entries with `domain_version < 2` and
  re-records the current state of every contract under the v2 hash
  domain. Idempotent across boots; no manual intervention required.
- **CI guard against silent hash-domain drift.** A new test in
  `tests/test_versioning.py::TestHashDomainCompleteness` introspects
  `DataContract.model_fields` and fails CI if a new field is added
  without an explicit content/exclusion classification — preventing
  a future CRT169 from happening twice.
- **Response shape additions.** `entry_hash` and `content_hash` join
  the existing `contract_hash` (which is now an alias of `entry_hash`,
  retained for backward compatibility) on validate, batch validate, and
  `GET /contracts/{name}` responses.

This release is a deliberate breaking change to chain hash values,
not to the public API surface. Hashes captured under v2.2.x will not
match any chain entry after the upgrade — clients pinning to a prior
hash should re-fetch from `validate` or `GET /contracts/{name}` and
store the new value. OpenDQV is pre-launch; no external customers are
known to depend on pre-v2.3.0 hashes.

## [2.2.8] - 2026-04-25

### Fixed — MCP proxy dual-path consistency (CRT168 PR-A follow-up)

- **`opendqv_mcp_proxy.py` now exposes the `hash` parameter on
  `get_contract`.** v2.2.6 added `?hash=<contract_hash>` to the engine
  and to `opendqv/mcp_server.py`, but the standalone stdio bridge that
  ships for Claude Desktop integrations carries its own hand-maintained
  inputSchema and URL routing, and it was missed. Buyers connecting via
  the proxy saw only `name` and `version` on the tool schema, with no
  way to pin a contract by hash.
- **URL routing in the proxy was broken for `version` too.** The
  previous handler routed `version` through the `/at?version=` timestamp
  endpoint, which returned 422 without a timestamp. The proxy now hits
  `/api/v1/contracts/{name}` directly with `?hash=` taking precedence
  over `?version=`, matching the engine API.

No engine logic changed in this release; the version bump exists so
that buyers and reviewers can identify which `opendqv_mcp_proxy.py`
revision is on disk by checking `engine_version` on a validate
response.

## [2.2.7] - 2026-04-25

### Added — audit credibility part 2 (CRT168 PR-B + PR-C)

- **`lookup_source` field on explainer + `explain_error` API.** A logical,
  user-facing name for the reference list backing a `lookup` rule
  (e.g. `universal_currency`), separate from the engine-internal
  `lookup_file` constraint. Stops the server's `ref/<filename>.txt`
  filesystem path leaking into the human explanation that ships in
  audit/regulator-facing copy.
- **`OWNER_EMAIL_MISSING` linter warning.** Every contract should declare
  a contact in `contract.owner_email`; without one the audit trail is
  anonymous when a regulator follows up. Companion `OWNER_EMAIL_INVALID`
  warning catches obvious typos / placeholders.
- **`UNIQUE_RULE_MISSING_SCOPE_NOTE` linter warning.** `unique` rules
  must qualify scope in their `error_message` (batch / file / dataset /
  etc.). The engine de-duplicates within the input batch only — bare
  "must be unique" overstates coverage to a reviewer.
- All 41 bundled contracts now ship with
  `owner_email: opendqv@bgmsconsultants.com` and explicit batch-scope
  wording on every `unique` rule. Regulatory contracts (DORA, HIPAA,
  MiFID, SOX, ICH-GCP) carry an additional note that cross-batch
  uniqueness against the master register is the upstream system's
  responsibility.

### Fixed

- `lint_contract_yaml` previously read `rules` only at the YAML top
  level, which silently no-op'd rule-level checks on bundled contracts
  that nest under `contract.rules`. The linter now accepts both
  structures.

## [2.2.6] - 2026-04-25

### Added — audit credibility (CRT168, external-eval driven)

- **Server-generated `event_id` on every validate response.** Every
  `POST /api/v1/validate` and `POST /api/v1/validate/batch` response now
  carries a UUID v7 (RFC 9562) `event_id` — the audit primary key for that
  call. Batch responses additionally carry a per-record `event_id` on each
  `BatchResultItem`, so every record is independently addressable in the
  audit trail. Persisted on the corresponding row in `quality_stats` (new
  `event_id TEXT` column with index, idempotent migration).
- **`GET /api/v1/contracts/{name}?hash=<contract_hash>`** — retrieve the
  exact historical contract version that produced a given `contract_hash`
  on a prior validate response. Hash lookup takes precedence over
  `?version=`. Required for regulator-grade point-in-time audit retrieval
  (EMA, MiFIR, Basel III workflows). MCP `get_contract` tool exposes the
  same `hash` parameter.
- **`opendqv.core._uuid7.uuid7()`** — internal RFC 9562 §5.7 UUID v7 shim.
  Stays in place permanently; not contingent on the Python 3.14 stdlib
  addition.

### Why

External regulator-facing review (Data Governance Lead persona, MCP eval,
2026-04-25) flagged that responses returned `record_id: null` and that
`get_contract` could not retrieve a contract by its hash — both blockers
for credible point-in-time audit retrieval. CRT168 scoped the fix.

## [2.2.5] - 2026-04-18

### Added

- **`opendqv fork <src> <dst>`** — copy a contract to a new name as a clean DRAFT.
  Rewrites `name:`, `version: "1.0"`, `status: draft`, and `asset_id:` in place
  while preserving all comments, descriptions, and rules from the source. One
  command replaces the `cp + edit name: field + reset version` workflow — and
  removes the footgun where a forgotten `name:` edit caused `opendqv validate`
  to return "not found" despite `opendqv lint` passing.
- **Linter rule `FILENAME_NAME_MISMATCH`** — `opendqv lint` now errors when the
  filename stem differs from the YAML's internal `name:` field. Catches the
  same footgun for users who copy files manually instead of using `fork`.

## [2.2.4] - 2026-04-17

### Changed

- **Contracts now ship in the wheel.** The 43 bundled YAML contracts plus their
  reference lookup files have moved from `./contracts/` at repo root to
  `opendqv/contracts/` inside the package. Pip-install users get the full library
  as the default `CONTRACTS_DIR` — `opendqv list` works out of the box with zero
  configuration. Dev and Docker workflows unchanged in behaviour: `OPENDQV_CONTRACTS_DIR`
  still overrides, and the Docker compose mount points at the new path.
- **`opendqv init --all`** — new flag copies every bundled contract (43+ regulated
  domains) plus the reference lookup files into the target directory for a writable
  working copy. Default `opendqv init` still writes the single starter contract.
- Tagline unified across every user-facing surface: *"Trust is easier to build than
  to repair."* CLI `--version`, onboarding wizard, Streamlit sidebar, CLI docs, MCP
  docs — all aligned with README and Protocol 29.

## [2.2.3] - 2026-04-16

### Fixed

- **4 broken `max_length` rules** in banking_transaction (reference, 18 chars),
  fmcg_product (brand, 70), retail_product (product_name, 100), and media_content
  (title, 255). All used `max:` instead of `max_length:` in YAML — the Pydantic
  alias mapped `max` to `max_value`, leaving `max_length=None`. Rules silently
  never fired. Found via MCP-driven sample record audit.
- **proof_of_play sample records** — `SGMEDIA` exceeded `{2,6}` panel_id prefix
  limit, advertiser_id values padded to 8 digits after regex tightening (f959870).
- **16 sample record files** aligned with v1.1 contracts. 11 full rewrites
  (field name changes from v1.0→v1.1), 5 minor fixes (data/comment corrections).
  142/142 sample records now validate correctly against their contracts.
- **3 broken `min_length` rules** — same alias trap as `max_length`. banking_transaction
  (account_number, 6 chars) and logistics_shipment (origin/destination_country, 2 chars)
  used `min:` instead of `min_length:`.
- MCP proxy version hardcoded — updated to 2.2.3.
- CVE-2026-40347: bumped python-multipart 0.0.22 → 0.0.26.

### Added

- **Contract linter warnings** `MAX_LENGTH_ALIAS_CONFUSION` and
  `MIN_LENGTH_ALIAS_CONFUSION` — `opendqv lint` now catches `max:` on `max_length`
  rules and `min:` on `min_length` rules at author time.
- **`docs/rules/core_rules.md`** — YAML syntax reference for all 13 core rule types
  with correct field keys and a Common Pitfalls section.

## [2.2.2] - 2026-04-12

### Fixed

- MCP server version was hardcoded as `"1.8.4"` — now reads from `config.ENGINE_VERSION` dynamically

## [2.2.1] - 2026-04-12 — PICK Sprint: Engine Quality

Systematic code quality sprint using PICK methodology (ease × benefit quadrants).
32 findings audited, 15 shipped (12 Implement + 3 Challenge), zero regressions.

### Security

- **Removed `yaml.full_load()` fallback** in `core/contracts.py`. The fallback could
  deserialize arbitrary Python objects (RCE vector). Zero contracts used Python tags.
  Now raises `RuntimeError` with a clear remediation message. `yaml.safe_load()` only.

### Performance

- **O(n²) → O(n) grouped uniqueness** in `validate_batch()`. Replaced nested loop
  with single-pass `defaultdict` grouping. Benchmarks: 48× faster at 100 records,
  954× faster at 2,000 records. 10K records: ~55 min → 0.8s.
- **Hot-path micro-optimisations** — `_COMPARE_OPS` dict, `_UNSAFE_FIELD_CHARS` regex,
  and `_parse_date()` hoisted to module level. `fields_validated` moved above batch loop.

### Changed

- **Dispatch table for `_check_rule()`** — 417-line if/elif chain (23 branches)
  extracted into 23 handler functions + `_RULE_HANDLERS` dispatch dict. Adding a
  new rule type now requires one function + one dict entry. Hardcoded known-types
  tuple removed.
- `ValidationError` exported from `opendqv.sdk` — users of `guard()` decorator can
  now catch errors without importing from private submodules.
- PEP 561 `py.typed` marker added at package root (was only in subpackages).
- PyPI classifiers: `Framework :: FastAPI`, `Typing :: Typed`. Changelog URL added.
- `codecov-action` pinned to SHA in CI.
- 8 `write_text()` calls in `cli.py` fixed with `encoding="utf-8"` (Windows compat).
- 3 redundant local `datetime` imports removed from `contracts.py`.

### Fixed

- **62 broken import paths** across 27 docs files — all `from sdk import` →
  `from opendqv.sdk import`, all `python -m cli` → `python -m opendqv.cli`.
  Every pip user following any code example would have hit `ModuleNotFoundError`.
- `test_cli.py` bare import fixed (`from cli import` → `from opendqv.cli import`).
- `docs/observation_mode.md` incorrect SDK import path.
- `docs/quickstart.md` stale download link (v1.0.0 → v2.1.0).
- `CONTRIBUTING.md` stale paths, test counts, missing ruff section.
- Removed stale `e2e_audit_test.yaml` contract (zero references, test fixture).
- Fixed `social_media_age_compliance.yaml` version (draft counter artefact → "1.0").

## [2.1.0] - 2026-04-11 — Namespace Restructure

All code moved under `opendqv/` namespace for proper `pip install opendqv` support.
`opendqv init` CLI command added. `regex` library promoted to hard dependency.

## [2.0.0] - 2026-04-07 — First Beta Release

OpenDQV Core graduates from Alpha to **Beta**. No breaking changes from 1.9.8 —
this release is a status milestone, not an API break. Existing 1.9.x deployments
upgrade in place.

### What Beta means

- **Public API surface is stable.** REST endpoints, contract YAML schema, MCP
  tool names, and Python SDK signatures will not change without a deprecation
  cycle (one minor release of warnings before removal).
- **Security fixes are backported** to the latest 2.x line.
- **Coverage 93%, 3,398 tests** across the engine, importers, SDK, and routes.
- **Hot-path performance verified** on EC2 c6i.large at 482 req/s sustained
  (CRT161 benchmark, hot-path caches landed in 1.9.8).
- **All RT148 critical and high findings closed** (token priv-esc, contract
  state machine, routes.py split, SSRF DNS rebinding, require_role dead code).

### What Beta does NOT mean

- No production SLA. This is still a community OSS project with one maintainer.
- No formal third-party penetration test (disclosed in SECURITY.md).
- The hot-path is the bouncer; surrounding tooling (MCP, federation, importers,
  GraphQL, DuckDB analytics) is supported but earns its stability claims one
  release at a time.

### Changed

- Trove classifier: `Development Status :: 3 - Alpha` → `4 - Beta`
- README badge and positioning copy updated to reflect Beta status
- SECURITY.md and SUPPORT.md updated with Beta support commitments

## [1.9.8] - 2026-04-03

### Performance

- **4× regex throughput improvement** — `_safe_match()` in `core/validator.py` now calls
  `compiled_pattern.match(str_val, timeout=...)` directly on the pre-compiled `regex.Pattern`
  object instead of re-compiling the pattern string on every call. Eliminates redundant
  `regex._compile()` calls on the hot path. Valid-record mean latency: 0.161 ms → 0.040 ms.
  Invalid-record mean latency: 0.234 ms → 0.052 ms. (CRT156/A2/A5)

- **Rule parser compiles with `regex` library** — `core/rule_parser.py` now uses
  `_regex_lib.compile()` when the `regex` library is available, ensuring all
  `Rule.compiled_pattern` fields are `regex.Pattern` objects. Prerequisite for the
  `_safe_match` fix. (CRT156/A5)

### Bug Fixes

- **Latent ReDoS timeout bug** — `except _regex_lib.TimeoutError:` would raise
  `AttributeError` if a regex timeout actually fired, masking the security control.
  Fixed to `except TimeoutError:` (builtin — what `regex.match()` actually raises). (CRT156/A5)

### Reliability

- **Webhook dispatch moved to background tasks** — `api/routes_validation.py` now calls
  `background_tasks.add_task(webhook_manager.notify, ...)` instead of awaiting
  `webhook_manager.notify()` directly. Prevents webhook delivery (5s timeout) from
  blocking the HTTP response on validation failures. Aligns with the documented semantics
  of `notify()` ("Fire-and-forget — never raises"). (CRT156/A5)

- **`list_hooks()` in-memory cache** — `core/webhooks.py` `WebhookManager` now caches the
  webhook list in memory; cache is invalidated on `register()` and `unregister()`.
  Previously every failed validation triggered a synchronous SQLite read. (CRT156/A5)

### Documentation

- **`docs/benchmark_throughput.md`** — expanded platform comparison table with EC2 c5.large
  valid/invalid/mixed breakdown; sizing rule of thumb; key insight on 18% invalid-record
  slowdown; benchmark methodology notes (date, contract, auth mode). (CRT156/A3)

- **README Performance section** — replaced developer-laptop headline with EC2 mixed-workload
  figure (~341 req/s, c5.large, 2 workers) as the production capacity planning baseline.
  Six-platform table. Sizing rule. (CRT156/A4)

---

## [1.9.7] - 2026-04-02

### Quality

- **Coverage: 90.87% → 93.0%** — targeted sprint covering JSON decode exception handlers, auth function edge paths, Spark code generator fallback, batch validation edge cases, and file-based storage paths. Threshold raised from 90% to 93% (`fail_under = 93`).
- **3398 tests** (up from 3314 / +84 tests).

#### New / extended test classes
- `TestQualityAnalyticsInvalidJson` — covers `except (json.JSONDecodeError, TypeError): continue` branches in `rule_heatmap`, `rule_failure_velocity`, and `observation_fields` (lines 109-110, 174-175, 197-198, 368-369).
- `TestQualityStatsFileBased` — covers `conn.close()` in non-memory `get_windowed_totals` finally blocks (lines 235, 251).
- `TestAuthDirectFunctions` — covers open-mode invalid Bearer fallback (lines 170-171), non-Bearer 401 (line 178), and `get_current_role` validator fallback paths (lines 215, 221-222).
- `TestBatchValidationEdgeCases` — covers `compare_to="now"` sentinel, date-parse string fallback, null batch lookup, missing lookup file swallowed, and non-numeric cross_field_range.
- `TestValidatorEdgeCases` — covers checksum NHS/CPF/VIN/LEI edge cases, geospatial lon bounds, age_match edge cases, and unknown rule type path.
- `TestCodeGeneratorEdgeCases` — covers `_js_rule_check` default `age_checked=set()` init (line 212) and Spark `else` todo_note (line 340).
- `TestTokensExtended` — covers `POST /tokens/revoke/{username}` (line 94) and IS_OPEN_MODE role downgrade (line 43).
- `TestTraceLogMissedLines` extensions — rotation with 4+ segments, stat OSError early return, rename OSError logging, unlink OSError swallowed.

---

## [1.9.6] - 2026-04-02

### Quality

- **Coverage: 89.76% → 90.87%** — 3 CRT152 open items resolved. Threshold raised from 89% to 90% (`fail_under = 90`).
- **3314 tests** (up from 3251 / +63 tests).

#### Dead code removed
- `api/routes_contracts.py`: removed unreachable `except UnknownContextError` block in `generate_code_endpoint`. `get_rules_with_context()` never raises this exception — it falls back to base rules for unknown contexts. This was confirmed dead code and has been deleted.

#### New test file
- `tests/test_main.py` (14 tests): lifespan startup and shutdown, heartbeat flush on shutdown, heartbeat flush exception swallowed, `_maker_checker_enforced()` in open/token mode, root endpoint auth_mode field, health endpoint detail mode (HEALTH_DETAIL=true/false).

#### Extended test files
- `tests/test_onboarding.py` (+49 tests): `_read_workbench_lock` dead-pid and exception paths, `_write_workbench_lock` / `_write_api_lock`, `_load_first_lookup_value` (file found, comments filtered, OSError, field-name fallbacks), `_build_valid_from_regex` missing branches (loose phone pattern, postcode/phone/email keyword inference), `build_sample_records` for country/colour/color/_status fields, `build_sample_records_from_rules` for max/min_length/lookup/age_match rules, `_demo_governance` (happy path, 401/403 skip, already-active skip, exception swallowed), `_reload` (success, exception swallowed), `_start_docker` (missing .env.example, FileNotFoundError on Popen), `_list_templates` edge cases (missing dir, excluded template, null YAML, bad YAML), wizard `run()` inside-Docker path, session file write exception swallowed.

#### Per-file gains
| File | Before | After |
|------|--------|-------|
| `core/onboarding.py` | 80.8% | **91.9%** |
| `main.py` | 74.0% | **83.3%** |
| `api/routes_contracts.py` | 72.7% | **83.2%** (dead code removed) |

---

## [1.9.5] - 2026-04-02

### Quality

- **Coverage sprint: 80.4% → 89.8%** — aimed for 100%, landed at 89.8%. Threshold raised from 80% to 89% (`fail_under = 89`).

- **3251 tests** (up from 2933 / +318 tests). New test files and extensions:
  - `tests/test_cli_extended.py` (new, 69 tests): in-process `cmd_*` function calls covering all CLI commands — validate, lint, generate, audit-verify, workflow, token-generate, import-dir
  - `tests/test_explainer.py` (new, 63 tests): all 20+ rule type handlers in `explain_rule()` + `quick_fix()`. `core/explainer.py` → **100%**
  - `tests/test_linter_extended.py` (new → extended, 31 tests): invalid YAML structures, required_if, allowed_values, date_diff, age bounds, non-numeric bound edge cases
  - `tests/test_storage_extended.py` (new, 15 tests): PostgreSQL backend via psycopg2 mocking — `record_version`, `get_as_of`, `get_history`, `diff`. `core/storage.py` → **99.3%**
  - `tests/test_rule_coverage.py` extended (60 new batch tests): DuckDB `validate_batch()` paths for all 17 rule types — min/max/range, not_empty, lengths, date_format, unique (global + group_by), compare (numeric/date/sentinel), required_if, allowed_values, checksum, cross_field_range, field_sum, forbidden_if, conditional_value, date_diff, ratio_check, geospatial_bounds, min_age
  - `tests/test_mcp_server.py` extended: `call_tool` dispatcher for all 9 tools, `_tool_get_quality_metrics`, `_tool_get_quality_trend`, `_tool_get_rule_velocity`, governance tip edge cases
  - `tests/test_trace_log.py` extended: JSON parse error, prev_hash mismatch, HMAC mismatch, no-key-but-HMAC-in-log, pre-HMAC backward compat, rotation with existing segments, write failure
  - `tests/test_federation_api.py` extended: sync-status with unreachable peer, peer divergence detection, SSE stream connected event, 429 connection limit
  - `tests/test_contracts_extended.py` (new, 28 tests): explain_contract auth paths, rule description branches (all rule types), history/timestamp endpoints, diff errors, workflow role errors, rule mutation role errors, schema registry endpoints, generate endpoint
  - `tests/test_worker_heartbeat.py` extended: file-based DB (covers non-shared conn.close() paths), zero-pending flush skip, exception handling

- **`marmot_proxy.py` excluded from coverage** — infrastructure bridge to external Marmot service; not application logic. Removes 97 statements from measurement denominator for more meaningful coverage reporting.

## [1.9.4] - 2026-04-02

### Quality

- **Coverage threshold raised to 80%** — up from 77%. Measured baseline 80.4%. Threshold enforced via `[tool.coverage.report] fail_under = 80`.

- **Coverage improvements** — 101 new tests across 5 new/extended test files:
  - `tests/test_rule_coverage.py` (47 tests): `field_sum`, `forbidden_if`, `conditional_value`, `date_diff`, checksum algorithms (`mod10_gs1`, `iban_mod97`, `isin_mod11`, `isrc_luhn`, `lei_mod97`, `nhs_mod11`, `cpf_mod11`, `vin_mod11`), `compare` edge cases, profiler file upload
  - `tests/test_import_api_save.py` (23 tests): API-level import tests with `save=True` for dbt, soda, csv, CSVW, OTel, NDC, ODCS importers
  - Extended `tests/test_quality_analytics.py`: `rejection-summary`, `rule-velocity`, `stats?window_hours`, `observation/summary/trend/fields` endpoints
  - Extended `tests/test_profiler.py`: profiler API endpoint with `save=True`
  - Extended `tests/test_worker_heartbeat.py`: `flush()` method
  - Extended `tests/test_trace_log.py`: log rotation paths
  - Extended `tests/test_security.py`: `revoke_by_username`, `get_current_role` edge cases

- **README: positioning paragraph added** — defines enforcement telemetry vs. post-landing observability. Replaces imprecise "bouncer at the door, nothing else" with accurate framing that acknowledges the analytics layer.

- **README: API Stability section added** — documents what is stable within v1.x (REST API, YAML contract format, SDK public methods, MCP tools). Sets expectations for Beta transition.

- **README: Alpha notice updated** — version pinning recommendation added. External user condition resolved: Alpha's purpose was to prove OpenDQV is useful to its maintainer. That is done. The remaining Beta condition is the backwards compatibility commitment, now documented.

## [1.9.3] - 2026-03-31

### Quality

- **CRT150: `py.typed` markers added (PEP 561)** — `sdk/`, `core/`, `api/`, `security/` now
  ship `py.typed` files, enabling proper IDE autocomplete and type checking for downstream users.
  (`pyproject.toml` updated to include markers in package distributions.)

- **CRT150: Coverage threshold enforced (77%)** — `[tool.coverage.report] fail_under = 77`
  added to `pyproject.toml`. Measured baseline is 77.5%; threshold prevents silent regression.

- **CRT150: SDK unit tests added (`tests/test_sdk.py`)** — 70 new tests covering
  `OpenDQVClient`, `AsyncOpenDQVClient`, `LocalValidator`, `@guard()` decorator, contract
  caching, and `_extract_record`. SDK line coverage: 95.3%. (`tests/test_sdk.py`)

- **CRT150: `validate_config()` added to `config.py`** — validates all environment variables
  at startup: `AUTH_MODE`, `DB_BACKEND`, `DB_URL` (when postgres), integer bounds
  (`TOKEN_EXPIRY_DAYS`, `MAX_BATCH_ROWS`, `MAX_SSE_CONNECTIONS`, `MAX_ISOLATION_HOURS`), and
  rate-limit format strings. Raises `ValueError` with a clear message at startup instead of
  crashing mid-request. Called from `main.py` lifespan. (`config.py`, `main.py`)

- **CRT150: Config validation tests added (`tests/test_config_validation.py`)** — tests each
  bad env var value produces a clear `ValueError`, and all valid values produce no error.

- **CRT150: SDK README added (`sdk/README.md`)** — covers sync client, async client,
  `LocalValidator`, `@guard()`, auth, observation-only mode, and contract caching with
  copy-paste examples.

## [1.9.2] - 2026-03-31

### Security

- **N3 (RT149): `GET /tokens` restricted to `admin` role** — token metadata (usernames, expiry,
  roles) was visible to any authenticated user. Now requires `admin` role in `AUTH_MODE=token`.
  (`api/routes_tokens.py`)

- **N1 (RT149): SECURITY.md updated to reflect M1 and L2 fixes** — DNS rebinding section updated
  to document dispatch-time re-resolution; token revocation section updated from "no ownership
  check" to reflect admin-only requirement. (`SECURITY.md`)

### Bug Fixes

- **N4 (RT149): `encoding="utf-8"` added to 12 `open()` calls in refactored router files** —
  H1 refactor introduced `open()` calls without explicit encoding in `routes_imports.py` (8),
  `routes_profiler.py` (2), and `routes_contracts.py` (1). Windows cp1252 default could fail
  on non-ASCII content (CLAUDE.md convention). (`api/routes_imports.py`, `api/routes_profiler.py`,
  `api/routes_contracts.py`)

- **N8 (RT149): Webhook DNS re-resolution moved to thread pool** — `socket.getaddrinfo()` in
  `_send()` is a blocking syscall; wrapped in `asyncio.to_thread()` so the event loop is not
  stalled under high webhook volume. (`core/webhooks.py`)

- **N5 (RT149): `revoke_system_tokens` open-mode guard harmonised** — endpoint previously checked
  role unconditionally; now uses the same `if not config.IS_OPEN_MODE and role != "admin"` pattern
  as sibling token endpoints. (`api/routes_tokens.py`)

### Code Quality

- **N6 (RT149): `_ensure_db()` thread-safety documented** — added comment explaining why the
  benign race on `_db_initialized` is safe (SQLite serialises DDL; `CREATE TABLE IF NOT EXISTS`
  is idempotent). (`security/auth.py`)

- **N7 (RT149): `__all__` added to `api/routes.py` shim** — enumerates intentionally re-exported
  names, making the delegation boundary explicit. (`api/routes.py`)

- **N9 (RT149): Misleading test docstring corrected** — `TestSubmitReviewRoles` now correctly
  states "editor, admin only" to match the maker-checker separation. (`tests/test_rbac.py`)

## [1.9.1] - 2026-03-31

### Refactoring

- **H1 (RT148): `api/routes.py` split into 8 domain modules** — 2,764-line monolith replaced with
  `api/deps.py` (shared state/helpers) + 8 domain sub-routers. No URL paths or API behaviour
  changed. All 2790 tests pass. Contributor onboarding significantly improved.

### Security

- **M1 (RT148): Webhook SSRF — IP re-validated at send time** — `_check_resolved_ips()` now
  called in `_send()` before dispatch, mitigating DNS rebinding attacks where a hostname
  resolves to a public IP at registration but is changed to an internal IP before dispatch.
  (`core/webhooks.py`)

- **H2 (RT148): `require_role()` dead code removed** — unused dependency factory in
  `security/auth.py` deleted. All 20+ routes already use inline role checks; the factory
  created a false sense of centralised enforcement. (`security/auth.py`)

- **L1 (RT148): `auth.py` — `init_db()` no longer fires at module import** — replaced with lazy
  `_ensure_db()` guard called by each DB-touching function. Side-effect-free import; DB
  initialised on first use or application lifespan startup. (`security/auth.py`, `main.py`)

- **L2 (RT148): Token revocation restricted to `admin` role** — `POST /tokens/revoke` previously
  allowed any authenticated user to revoke any token, enabling DoS against integrations.
  Now requires `admin` role in `AUTH_MODE=token`. (`api/routes_tokens.py`)

### Tests

- `tests/test_rbac.py` — new `TestTokenRevokeRoles`: admin allowed, all 5 non-admin roles
  forbidden for `POST /tokens/revoke`.
- **2790 tests passing, 21 skipped** (+6 from v1.9.0)

## [1.9.0] - 2026-03-30

### Security

- **C2 fix (RT148): Contract state machine now enforces valid transitions** — `set_status()`
  previously accepted any transition including `archived → active`, allowing an approver to
  bypass the maker-checker review workflow entirely. A transition map is now enforced:
  archived contracts must re-enter via `draft`; `archived → active` returns HTTP 409.
  (`core/contracts.py`)

  Valid transitions:
  - `draft → active | archived`
  - `review → active | draft | archived`
  - `active → archived | draft`
  - `archived → draft` only (must re-enter lifecycle; cannot jump to active or review)

### Tests

- `tests/test_lifecycle.py` — new `TestStatusTransitionValidation` class: 5 tests covering
  `archived → active` blocked (API + registry), `active → archived` allowed, `active → draft`
  allowed, `archived → draft` allowed. Cleanup of `TestArchivedFilter` fixed to use
  valid `archived → draft → active` restore path.
- **2784 tests passing, 21 skipped** (+5 from v1.8.9)

## [1.8.9] - 2026-03-29

### Security

- **C1 fix (RT148): Token generation now requires `admin` role** — `POST /tokens/generate`
  previously had no role guard in `AUTH_MODE=token`, allowing any authenticated user to mint
  tokens for any role including `admin`. Now restricted to `admin` callers only. Open mode
  behaviour unchanged (elevated roles continue to be capped to `validator`).
  (`api/routes.py`, `security/auth.py`)

- **M2 fix (RT148):** `config.py` — `read_text(encoding="utf-8")` on `pyproject.toml` read.
  Was missing `encoding=` in violation of CLAUDE.md Windows portability rule. (`config.py:17`)

### Tests

- `tests/test_rbac.py` — new `TestTokenGenerateRoles` class: 1 admin-allowed test + 5 parametrised
  forbidden-for-non-admin tests. `test_rbac.py` now covers all role-gated endpoints including
  token generation.
- `tests/test_api.py` — `TestTokens` and `TestTokenRoles` updated to use `admin_headers` for
  token generation calls (required by C1 fix).
- **2779 tests passing, 21 skipped** (+6 from v1.8.8)

## [1.8.8] - 2026-03-28

### Features

- **Observation mode analytics** — three new API endpoints to analyse observation-only runs
  separately from enforcement runs:
  - `GET /api/v1/observation/summary?days=7&contract=X` — total observation records,
    would_have_failed_count, would_have_passed_count, enforcement_readiness_pct, by_contract breakdown
  - `GET /api/v1/observation/trend?contract=X&days=7` — daily time-series of observation
    violations (same shape as `/quality/trend`, mode-filtered)
  - `GET /api/v1/observation/fields?contract=X&days=7` — top failing rules/fields under
    observation mode, sorted by frequency
  - DuckDB-backed via three new `QualityAnalytics` methods: `observation_summary()`,
    `observation_trend()`, `observation_fields()`

- **Observation mode persistence** — `quality_stats` SQLite table now stores a `mode` column
  (`'enforcement'` or `'observation_only'`). Idempotent schema migration runs on startup
  (existing deployments upgrade automatically). Observation runs no longer pollute enforcement
  analytics.

- **Workbench Observation dashboard** — new "Observation" section in the Streamlit governance
  workbench. Two panels:
  - Real-time: KPI metrics (Observation Records, Would Fail, Would Pass, Enforcement Readiness %),
    filtered event table from in-memory stats
  - Historical: contract + day selector, daily trend line chart, top failing fields table,
    enforcement readiness score — all backed by the new DuckDB analytics endpoints

- **Observation mode documentation** — `docs/observation_mode.md` (new): practical guide
  covering the prospect workflow (observe → analyse → enforce), API/CLI/SDK usage examples,
  workbench dashboard interpretation, and `--output-failures` export.

### Tests

- 2773 passed, 21 skipped (was 2745 in v1.8.7; +28 new tests):
  - `TestObserveOnlyPersistence` (3 tests) — verify `mode` column written correctly to SQLite
    for both observation and enforcement calls
  - `TestObservationAnalyticsEndpoints` (6 tests) — all three new endpoints: shape, auth boundary,
    required params
  - `TestObservationModeSmoke` (3 tests) — 16th smoke coverage area; happy path + endpoint
    reachability

## [1.8.7] - 2026-03-27

### Features

- **Contract linter** (`opendqv lint <contract>` + `GET /api/v1/contracts/{name}/lint`) — static
  analysis of contract YAML before deployment. Works at raw-dict level (pre-Pydantic) to catch
  logical errors silently swallowed at runtime. 20+ check codes including `DUPLICATE_RULE_NAME`,
  `RANGE_MIN_GT_MAX`, `REGEX_INVALID_PATTERN`, `COMPARE_INVALID_OP`, `CHECKSUM_UNKNOWN_ALGORITHM`,
  `ALLOWED_VALUES_EMPTY`, and full coverage for cross_field_range, field_sum, geospatial_bounds,
  ratio_check, required_if, forbidden_if, conditional_value. HTTP 422 on errors (CI-gatable on
  status code). JSON output mode for pipeline integration. SDK `lint()` on both sync and async
  clients.

- **Spark SQL code generator** (`target="spark"`) — generates `WITH _dqv_checks AS (...)` CTE
  pattern with `FILTER()` + `SIZE()` for `_dqv_errors`/`_dqv_valid` output columns. Supports
  `not_empty`, `regex`, `min`, `max`, `range`, `min_length`, `max_length`, `date_format`,
  `allowed_values`, `unique`. Python strftime → Spark date format conversion built-in.
  SQL single-quote escaping on all error messages.

- **BigQuery JS UDF code generator** (`target="bigquery"`) — generates
  `CREATE OR REPLACE FUNCTION ... RETURNS STRUCT<valid BOOL, errors ARRAY<STRING>> LANGUAGE js`
  UDF. Accepts `row_json STRING` via `TO_JSON_STRING(t)`. Reuses `_js_rule_check()` from
  Snowflake/JS generators — no logic duplication. Supported targets now: `snowflake`, `salesforce`,
  `js`, `spark`, `bigquery`.

- **Async Python SDK parity** — `AsyncOpenDQVClient` was missing `contracts()` (gap vs sync
  client). Both clients now have `lint()`. 13 new async tests covering validate, validate_batch,
  contracts, contract detail, lint, context manager, and guard decorator.

### Performance

- **Async fire-and-forget storage on validation hot path** — SQLite writes (`quality_stats`,
  `heartbeat`) are now decoupled from the HTTP response on `/validate` and `/validate/batch`.
  `asyncio.to_thread()` wraps sync writes; `asyncio.create_task()` schedules them as background
  tasks. Response returns before disk write completes. Measured improvement: 208 req/s → 237 req/s
  sustained (+14%), p99 205ms → 163ms (−20%) on Dell XPS 13, 4 workers, zero errors across
  222,529 requests (1+5+10 minute runs, 2026-03-27).

  MacBook reference (updated macOS + Docker Desktop, 4 workers, 2026-03-27):

  | Run    | Total reqs | Throughput  | p95     | p99     | Errors |
  |--------|------------|-------------|---------|---------|--------|
  | 1-min  | 13,506     | 224.9 req/s | 127.1ms | 174.8ms | 0      |
  | 5-min  | 67,468     | 224.8 req/s | 123.7ms | 171.2ms | 0      |
  | 10-min | 133,926    | 223.2 req/s | 124.8ms | 174.0ms | 0      |

  Throughput ~5.5% lower than XPS 13/Ubuntu baseline (Docker-on-Mac virtualisation overhead).
  Zero errors, no degradation under sustained load.

### Tests

- **Smoke test suite** (`tests/test_smoke.py`, 39 tests) — wide-coverage, shallow-depth tests
  serving as both a first-gate CI check and living documentation of the API contract. Covers
  health, contracts, single validation, error codes, batch, contract linter, code generation
  (all 5 targets), quality trend, explain, stats, auth boundary, validate-file CLI, lint CLI,
  and LocalValidator SDK. Runs in ~8 seconds.

- **Test suite total:** 2745 passed, 6 skipped, 0 failed (77 tests added this sprint).

### Security

- **`cryptography` bumped to `>=46.0.6`** — fixes GHSA-m959-cc7f-wv43 (incomplete DNS name
  constraint enforcement on peer names; low severity).
- **`requests` bumped to `>=2.33.0`** — fixes GHSA-gc5v-m9x4-r6x2 (medium severity).
  `pyproject.toml` updated to `^2.33` accordingly.
- **`starlette` bumped to `>=1.0.0`** — covers GHSA-2c2j-9gv5-cj73 (fix: 0.47.2) and
  GHSA-7f5h-v6xp-fcq8 (fix: 0.49.1); also tracks new stable release.
- **Dockerfile: `pip>=26.0` added to pre-install step** — fixes CVE-2026-1703 in pip itself
  before requirements are installed.
- **`pygments` (transitive via `rich`)** — CVE-2026-4539 (GHSA-5239-wwwm-4pmq); no upstream
  fix available as of 2026-03-27. Acknowledged; will resolve when pygments ships a patch.

---

### Observation-Only Mode

- **`--observe-only` flag on `validate-file` CLI** — run full validation without blocking. Output labelled `OBSERVATION RUN`, exits 0 regardless of violations. `--output-failures` still works to export what would have been rejected.
- **`observe_only: bool` on POST /validate and POST /validate-batch** — when `true`, returns HTTP 200 with `"mode": "observation_only"` and `"would_have_failed": true/false`. Errors and warnings are still fully populated.
- **SDK: `observe_only=True`** on both `OpenDQVClient.validate()` / `validate_batch()` and `AsyncOpenDQVClient` equivalents.
- **Audit trail `mode` field** — `write_trace_entry()` accepts `mode` parameter. Observation-only runs are recorded with `mode=observation_only`; all existing enforcement runs default to `mode=enforcement`.
- **Streamlit workbench** — observation-only checkbox in validation workbench. Results display `OBSERVATION RUN` banner with "would have failed/passed" language.
- **13 new tests** (`tests/test_observe_only.py`) covering CLI exit code, output labelling, API response shape, batch mode, trace log mode field, and regression for enforcement mode.

---

## [1.8.6] - 2026-03-27

### Features

- **Typed error codes** — every validation failure now carries a stable `error_code` field
  (`OPENDQV_{RULE_TYPE}_001`, e.g. `OPENDQV_REGEX_001`). Derived deterministically from rule
  type. Present in single-record, batch, and GraphQL responses. Safe to use as a routing key
  in Kafka DLQs, PagerDuty rules, and ServiceNow auto-tickets. Full catalogue: `docs/error_codes.md`.

- **`opendqv validate-file` CLI** — validate a CSV, TSV, or Parquet file against a contract
  without starting the API server. `opendqv validate-file <contract> <path>`. Outputs pass/fail
  summary, failed-record count, per-rule failure breakdown. Optional `--output-failures <file>`
  flag writes failed records to CSV. Exits 1 on failures, 0 on clean data.

- **Benchmark suite — five standard workloads** — `tests/test_benchmark.py` extended with
  W1 (single-record, 1K sequential), W2 (batch 1K mixed), W3 (batch 10K mixed),
  W4 (batch 1K regex-heavy), W5 (batch 1K numeric-range). Workloads W2–W5 use the DuckDB
  batch path with inline Rule objects — no file I/O, reproducible in CI.
  `docs/benchmark_throughput.md` updated with five-workload table and comparative methodology
  for community-contributed tool comparisons.

### Changes

- **`CONTRIBUTING.md`** — added "Adding a New Importer" section with step-by-step pattern
  (reference: `core/importers/csv_rules.py`); CHANGELOG entry added to PR checklist;
  good-first-issue qualification criteria; Code of Conduct pointer.

- **`opendqv --version`** — was hardcoded `1.0.0`; now reads from `importlib.metadata`
  (matches `pyproject.toml` version).

- **pre-commit lint gate** — `.pre-commit-config.yaml` added. Mirrors CI ruff check exactly:
  `--select E,W,F --ignore E501,E402,E701 --fix`. Auto-fixes safe issues (unused imports)
  before commit.

### Bug Fixes

- **`ENGINE_VERSION` mismatch** (v1.8.5) — was hardcoded `"1.0.0"` since initial release.
  Every audit trail entry since v1.1.0 was stamped with the wrong version. Now derived from
  `pyproject.toml` at source installs and `importlib.metadata` at pip installs. CI assertion
  added. `tests/test_p1_features.py` updated to validate against `pyproject.toml`.

## [1.8.2] - 2026-03-25

### Features

- **Top-10 customer demo scripts** — nine new `scripts/customer_<contract>_demo.py` scripts
  covering the most commercially valuable contracts: `hr_employee`, `gdpr_dsar_request`,
  `healthcare_patient`, `mifid_transaction_report`, `dora_ict_incident`, `sox_control_test`,
  `companies_house_filing`, `martyns_law_venue`, `building_safety_golden_thread`.
  Each script ships a generic default menu (no trademarks or prospect names) with two passing
  records and four instructive failure modes. Customer-specific menus load from gitignored
  `scripts/<contract>_demo_customers.local.json` files.

- **Demo context persistence** — demo scripts use `context="demo"` (not `dry_run: true`) so
  records land in `quality_stats` during the demo. The analytics dashboard updates in real time
  as the prospect watches each record validate.

- **`scripts/_demo_utils.py`** — shared utilities extracted from `customer_ppds_demo.py`:
  `_validate`, `_first_error`, `_load_menu`, `run_demo`. All demo scripts import from here.

- **`scripts/teardown_demo.py`** — post-demo cleanup: deletes all `context='demo'`
  quality_stats rows via the new admin endpoint, then re-runs `push_quality_lineage.py` to
  sync Marmot back to the clean state. Run at home after returning from a customer visit.

- **`DELETE /api/v1/quality/stats?context=`** — new admin endpoint. Deletes all quality_stats
  rows with the given context tag. Returns `{"deleted": N, "context": "..."}`.

- **`quality_stats.delete_by_context(context)`** — new method on `QualityStats` backing the
  above endpoint.

### Changes

- **`.gitignore`** — single `scripts/ppds_demo_customers.local.json` entry replaced with
  glob `scripts/*_demo_customers.local.json` to cover all 10 demo scripts.

- **`scripts/customer_ppds_demo.py`** — refactored to import from `_demo_utils`; duplicate
  utility code removed; switched from `dry_run: true` to `context="demo"`.

## [1.8.1] - 2026-03-25

### Features

- **Customer PPDS demo script** (`scripts/customer_ppds_demo.py`) — parameterised by
  `--customer` (or `OPENDQV_CUSTOMER` env var). Seeds branded menu items through the
  `ppds_menu_item` contract with a deliberate mix of passes and four instructive failure
  modes: missing reviewer, sulphites without ppm, blank allergen field, gluten without
  cereal type. Customer menus loaded from `scripts/ppds_demo_customers.local.json`
  (gitignored); generic `_default` fallback ships in the repo.
  Prints a narration-ready summary for prospect demos.

### Changes

- **`allereasy_dish` hidden from Marmot catalog** — `catalog_visible: false` added to
  `contracts/allereasy_dish.yaml`. Contract remains active and functional; removed from
  `discover_data` MCP tool and catalog endpoint to avoid surfacing a placeholder
  integration during customer demos. Reversible by removing the flag.

## [1.8.0] - 2026-03-25

### Features

- **DuckDB OLAP analytics layer** — new `core/quality_analytics.py` completes the
  OLTP/OLAP split introduced in v1.7.1. DuckDB attaches the SQLite `quality_stats`
  table directly via its SQLite extension — zero data duplication from the OLTP write
  path. Two new REST endpoints:
  - `GET /api/v1/analytics/summary?days=N` — cross-contract pass rates sorted
    worst-first, backed by a DuckDB aggregation query over SQLite.
  - `GET /api/v1/analytics/rule-heatmap?days=N` — top-50 failing rules across all
    contracts ranked by failure count, for systemic data quality diagnosis.
- **Schema-driven demo seeder** (`scripts/seed_broad_demo.py` rewrite) — all 32
  contract generators replaced with a contract-aware engine that reads YAML rules
  directly. Generates valid records by priority: `allowed_values`/`lookup` → `regex`/
  `date_format` → numeric `min`/`max`/`range` → `not_empty` fallback. Handles `compare`,
  `required_if`, and `date_diff` rules. Previously produced ~100% failure rates due to
  field name and allowed_values mismatches; now generates realistic 80–88% pass rates.

### Tests

- 34 new tests in `tests/test_quality_analytics.py`: DuckDB OLAP unit tests (window
  filtering, pass-rate ranking, rule heatmap aggregation, 50-entry cap), API endpoint
  shape and auth tests, seeder helper unit tests (make_valid_record, make_invalid_record).
- **Total: 2,505 passing, 21 skipped.**

## [1.7.1] - 2026-03-25

### Bug fixes

- **Single-record validations now persist to SQLite** — `/validate` (single-record) previously
  wrote only to in-memory `ValidationStats`, so all per-contract pass rates were lost on API
  restart. Now also calls `_quality_stats.record_batch(total=1, ...)` into the `quality_stats`
  SQLite table — same path batch validation already used.
- **`push_quality_lineage.py` reads from SQLite, not in-memory stats** — replaced the call to
  `GET /api/v1/stats` (in-memory, resets on restart) with per-contract calls to
  `GET /api/v1/contracts/{name}/quality-trend?days=30` (SQLite-backed). Pass rates pushed to
  Marmot are now restart-safe.

## [1.7.0] - 2026-03-25

### Features

- **`get_contract` exposes constraint fields** — `allowed_values`, `pattern`, `min_value`,
  `max_value`, `min_length`, `max_length` now included in every rule dict returned by the
  `get_contract` MCP tool. Previously these were all omitted, forcing agents to trigger
  validation failures just to discover valid values.
- **`window_hours` is now a real filter in `get_quality_metrics`** — pass `window_hours=1`
  to receive stats computed only from validations in the last hour. Previously the parameter
  was echoed but ignored; all-time totals were always returned. Uses a new timestamped
  `_events` deque (`maxlen=10,000`) in `ValidationStats`. The REST `GET /api/v1/stats`
  endpoint now also accepts an optional `window_hours` query parameter for the same effect.
- **Sample size confidence indicator** — `data_confidence` (`no_data` / `low` / `medium` /
  `high`) and `confidence_note` (plain-English caution when n < 10) added to every entry in
  `get_quality_metrics` output. Prevents agents from treating 1-record pass rates as signal.

### Bug fixes / investigations

- **`window_hours` label-only (Issue 2)** — confirmed as a real gap; implemented above.
- **`dry_run` latency claim (Issue 4)** — verified NOT a bug via live test. The guard in
  `api/routes.py` (`if not body.dry_run: stats.record(...)`) is working correctly. Regression
  test added to confirm the guard remains in place.

## [1.6.0] - 2026-03-25

### Features

- **`downstream_consumers` on contracts** — optional list of Marmot MRNs for downstream
  consumers of a validated asset (e.g. Tableau dashboards, dbt models). When present,
  `push_quality_lineage.py` stitches direct lineage edges from the validated asset to each
  consumer, completing the full lineage graph: source → OpenDQV job → validated asset →
  downstream consumers.
- **`catalog_visible` on contracts** — boolean flag (default `true`). Set to `false` to
  exclude a contract from Marmot catalog discovery and `push_quality_lineage.py` pushes.
  The Marmot proxy also filters these from `discover_data` responses at runtime.
- **`owner_team` synced to Marmot** — `contractOwnerTeam` now included in the
  `opendqvQuality` OpenLineage facet pushed via `push_quality_lineage.py`.
- **Visual diff UI in Audit Trail workbench** — "Diff Versions" section upgraded from
  free-text inputs to selectbox dropdowns populated from loaded history. Diffs render
  colour-coded: added rules in green, removed rules in red, changed rules with per-field
  before/after values. Diff state persists across Streamlit rerenders; clears on contract
  switch.

## [1.5.5] - 2026-03-24

### Features

- **`latency_ms` on validate responses** — server-side validation latency in milliseconds
  on every `/validate` and `/validate/batch` response. Enables latency monitoring and SLA tracking.
- **`suggested_fix` on validation errors** — each `FieldErrorResponse` now includes a concise
  one-liner fix hint. Allows AI agents and downstream consumers to self-correct without a
  separate `/explain_error` call.
- **`dry_run` flag on `/validate` and `/validate/batch`** — pass `dry_run: true` to validate
  without recording results in quality metrics, stats, or triggering webhooks. Use for testing
  and demo calls.
- **Latency histogram in `/stats`** — `latency` field with `avg_ms`, `p50_ms`, `p95_ms`,
  `p99_ms`, and `sample_size` computed from the last 1,000 validations.

Remaining items from Mac Claude's feature wishlist after live MCP demo. All four items
identified during real integration use.

## [1.5.4] - 2026-03-24

### Features

- **`validated_at` on validate responses** — ISO 8601 UTC timestamp on every
  `/validate` and `/validate/batch` response. Enables time-series correlation
  with quality metrics windows.
- **`agent_id` on validate requests/responses** — optional caller identity field
  on `/validate` and `/validate/batch`. Pass your agent name, service name, or
  team; it echoes back in the response for session and caller attribution.
- **`failure_rate_pct` in `/rejection-summary`** — each rule in `top_failing_rules`
  now includes the percentage of total records failing that rule, not just raw count.
  Gives meaningful signal when batch sizes vary.

Feature requests from Mac Claude after live MCP demo session. All three items
identified during real integration use — not hypothetical.

## [1.5.3] - 2026-03-24

### Fixed

- **Batch validator `all_of` array handling** — lookup rules with `all_of: true` now
  correctly validate list values in batch mode. Previously `str(["wheat"])` became
  `"['wheat']"` which never matched the lookup set, causing a 97% failure rate on
  `ppds_menu_item` batches. Single-record validator was unaffected. Fix mirrors the
  existing `all_of` list iteration from the single-record path to the batch path.
  Defect found by Mac Claude during live MCP demo session; confirmed by Grok.
- **Marmot `discover_data` pagination** — `marmot_proxy.py` now injects `limit=100`
  alongside `providers=["opendqv"]`, preventing Marmot from returning a summary
  breakdown instead of the full asset list when >20 results match.
- **MCP server name** — `Server("opendqv")` corrected to `Server("OpenDQV")`.

## [1.5.2] - 2026-03-24

### Features

- **Marmot lineage diagrams** — `scripts/push_quality_lineage.py` pushes OpenLineage
  COMPLETE RunEvents to Marmot for all active contracts, populating interactive lineage
  diagrams showing `[source asset] → [validate:<contract>] → [Marmot asset]`. Includes
  pass_rate, fail_count, and top failing rules per contract in the run facets. Direct
  lineage stitching via `POST /api/v1/lineage/direct` bridges OpenLineage MRNs to
  existing OpenDQV Marmot assets.
- **`asset_id` on all 44 contracts** — every active contract now carries
  `asset_id: urn:opendqv:<name>` for upstream lineage anchoring. Previously only 3
  contracts had this field.
- **`marmot_proxy.py`** — stdio-to-HTTP bridge enabling Claude Desktop (Mac) to connect
  to Marmot's MCP server. Claude Desktop only supports stdio MCP configs; the proxy
  bridges to Marmot's HTTP endpoint. API key and URL configurable via env vars.

## [1.5.1] - 2026-03-24

### Maintenance

- **DRY refactoring** — 18 copy-paste violations eliminated across `api/routes.py`,
  `config.py`, `security/auth.py`, `main.py`, and `cli.py`: 5 route helper functions
  (`_get_contract_or_404`, `_get_contract_versioned_or_404`, `_get_contract_hash`,
  `_check_validate_in_states`, `_assert_contract_mutable`), `VALID_ROLES` centralised
  in `auth.py`, `_ensure_utc()` helper, `IS_OPEN_MODE` and `_RATE_LIMIT_OFF_VALUES`
  constants in `config.py`
- **Version single source of truth** — `pyproject.toml` is the only place to set the
  version; `main.py` reads it via `importlib.metadata` at startup
- **`X-Auth-Mode` header** — moved from 4 individual write endpoints to an ASGI
  middleware so every response carries it (better for monitoring systems)
- **Dependency bumps** (Dependabot) — pytest-playwright 0.4.4→0.7.2,
  pytest-cov 4.1.0→7.1.0, ruff 0.4.10→0.15.7, duckdb 1.5.0→1.5.1,
  strawberry-graphql 0.311.1→0.312.0, github/codeql-action 3.34.0→4.34.1,
  peter-evans/create-issue-from-file 5→6
- **Bug fix** — `NameError` in `get_quality_trend` after route helper refactor; lint
  fixes (F541 f-strings, E402 local import in `cli.py`)

## [1.5.0] - 2026-03-24

### Workbench UX Overhaul

- **Grouped sidebar navigation** — replaced flat radio with sectioned button nav:
  CORE (Contracts, Validate, Monitoring, Audit Trail), INTEGRATIONS (Catalogs & AI,
  Integration Guide, Code Export, Webhooks, Federation), CONTRACT TOOLS (Import Rules,
  Profiler, CLI Guide)
- **Validate** — "Validate Record" and "Validate Batch" merged into a single section
  with a mode toggle; sample JSON generation is now explicit opt-in (button), not
  auto-reset on contract change
- **Audit Trail** — "Version History" renamed to "Audit Trail"
- **Catalogs & AI** — "Catalog & Agents" renamed; Marmot onboarding message rewritten
  with context (what Marmot is, what you gain, concrete next step); `urn:opendqv:*`
  asset IDs show "OpenDQV Internal ID" instead of "Catalog: Custom"; asset_id
  optionality explained in caption
- **Profiler** — "Rule Profiler" renamed to "Profiler" (sidebar) / "Data Profiler" (header)
- **Monitoring** — bar chart fixed (red Fail / blue Pass, wide-format DataFrame);
  Refresh button moved to bottom of section; charts rendered before tables
- **Acronym display** — `_display_name()` helper with `_ACRONYMS` dict corrects
  `.title()` mangling: Qsr→QSR, Gdpr→GDPR, Fmcg→FMCG, Hipaa→HIPAA, Dora→DORA,
  Mifid→MiFID, Sox→SOX, Eu→EU, and more
- **Domain labels** — `_contract_domain()` now applies `_ACRONYMS`; new entries:
  `martyns→"Martyn's Law"`, `allereasy→"AllerEasy"`, `ppds→"Natasha's Law / PPDS"`

### Contract Rename — qsr_menu_item → ppds_menu_item

- **`ppds_menu_item`** — contract renamed from `qsr_menu_item`. PPDS (Pre-Packed for
  Direct Sale) is the correct legal scope: Natasha's Law applies to any food business
  making food on the same premises it is sold — not only QSRs. Cafés, bakeries, delis,
  school canteens, and hospital food services all fall in scope. Zero breaking change
  (no external users at time of rename)
- Contract description updated to reflect full PPDS scope beyond QSR
- `contracts/ppds_menu_item.yaml`, `examples/ppds/ppds_menu_item.yaml`,
  `core/onboarding.py`, `README.md`, `docs/integrations/natasha-law-compliance.md`,
  `docs/integrations/allereasy.md` all updated; `examples/qsr/` folder renamed to
  `examples/ppds/`

### Demo Data

- **`scripts/seed_demo_data.py`** — fixed three bugs causing 0% validation pass rate:
  `score` field used range 300–850 (contract enforces 0–100); `loyalty_tier` included
  "platinum" (not in `ref/loyalty_tiers.txt`); `sf_contact` records missing `Birthdate`
  (a severity:error rule). Seeded data now produces a realistic pass/fail split

### Tests

- **`tests/test_e2e.py`** — Playwright navigation updated for new sidebar button UI:
  `_navigate_to_version_history` now clicks `button[Audit Trail]`; heading assertion
  updated to "Contract Audit Trail"

---

## [1.4.0] - 2026-03-23

### Martyn's Law — Pretix event ticketing integration

- **`pretix_event` contract** — 26-rule Martyn's Law compliance contract for [Pretix](https://pretix.eu) (open-source AGPL v3 event ticketing platform). Enforces expected attendance (min: 200), duty tier declaration (standard/enhanced), evacuation/invacuation/lockdown procedures, pre-event staff briefing, enhanced-duty fields (Senior Responsible Person, SIA notification reference, Terrorism Protection Plan), and a compliance audit trail — all at the point of write via a Django `pre_save` signal and OpenDQV's `LocalValidator` SDK
- **`docs/integrations/pretix.md`** — full integration guide: the structural gap (Pretix has no capacity field or emergency procedure model), 14 new nullable fields, `pre_save` signal implementation, zero breaking changes, opt-in via `ENABLE_OPENDQV_VALIDATION=true`
- **`docs/integrations/martyns-law-compliance.md`** — OSS project integrations table added; Pretix cross-reference
- **`examples/pretix/`** — starter contract + valid standard event, valid enhanced event, invalid missing procedures samples

---

## [1.3.3] - 2026-03-23

### Natasha's Law — ppds_menu_item contract fixes

Two compliance gaps in the `ppds_menu_item` contract (Natasha's Law / PPDS enforcement) closed:

- **`sulphites_ppm` now required when sulphites declared** — added `required_if: {field: contains_sulphites, value: "true"}` rule. Previously, if `contains_sulphites = "true"` but `sulphites_ppm` was absent, the `min: 10` threshold check silently never fired — a record could pass validation with sulphites declared but no concentration recorded
- **`ppds_review_date` now enforces ISO 8601 format** — added `format: "%Y-%m-%d"` to the `date_format` rule. Previously any parseable date (including `"03/21/2026"`) was accepted; FSA audit requirements demand unambiguous ISO 8601 dates
- Contract version bumped: `1.0` → `1.1`

---

## [1.3.2] - 2026-03-22

### Windows Compatibility (Python 3.13.12, verified on real hardware)

- **Windows test runner** — `scripts/windows_test.bat`: 3-run benchmark, pre-flight disk space + Python 3.11+ checks, UTF-8 mode, summary block with per-run timing, full cleanup. Verified: 2387 passed, 6 skipped, ~4:48 per run on Python 3.13.12
- **UTF-8 encoding** — explicit `encoding="utf-8"` on all `read_text()` / `write_text()` calls touching YAML files across `core/contracts.py`, `core/onboarding.py`, `cli.py`, and test files. Windows defaults to cp1252 which cannot decode bytes outside ASCII range
- **PID liveness check** — replaced `os.kill(pid, 0)` with `_pid_alive()` helper in `core/onboarding.py` and `_pid_exists()` in `core/worker_heartbeat.py`. On Windows, signal 0 is `CTRL_C_EVENT` — calling `os.kill(os.getpid(), 0)` in tests sent Ctrl+C to the pytest process, causing consistent `KeyboardInterrupt` at test ~1902
- **Session file path** — replaced hardcoded `/tmp/.opendqv_session` with `tempfile.gettempdir()` in `core/onboarding.py`, `ui/app.py`, and test. `/tmp/` does not exist on Windows
- **Null byte path check** — `_check_lookup_path_safe()` now explicitly rejects null bytes. Linux pathlib raises `ValueError` automatically; Windows Python 3.13 does not
- **Windows event loop** — `tests/conftest.py` sets `WindowsSelectorEventLoopPolicy` on Windows. `ProactorEventLoop` (default on Windows 3.8+) triggers spurious `KeyboardInterrupt` through pytest internals
- **`asyncio_mode = "auto"`** — added to `pyproject.toml` pytest config for pytest-asyncio 0.23+ compatibility
- **CLAUDE.md** — Windows portability rules section added for AI teammates

### Documentation & Repository

- **README roadmap** — removed "REST-based lookup rules" from future work (fully implemented since v1.x with `cache_ttl`, thread-safe HTTP cache, auth header support)
- **README Quick Start** — replaced `https://api.yourcompany.com/` placeholder URLs with working local `ref/order_statuses.txt` and `ref/carriers.txt` lookups; added note that HTTP endpoints are also supported
- **README Project Structure** — updated to reflect actual layout: 42 contracts, 2387+ tests, `postman/`, all core modules, demo compose
- **`contracts/ref/order_statuses.txt`** + **`contracts/ref/carriers.txt`** — new lookup ref files for the Quick Start walkthrough

---

## [1.3.1] - 2026-03-22

### Developer Experience

- **Postman collection** — `postman/OpenDQV.postman_collection.json` + `postman/OpenDQV.postman_environment.json`: 10 folders, all 50 endpoints, collection-level pre-request script for auto-auth, 3 environment variables (`base_url`, `auth_token`, `contract_name`)
- **Demo Docker environment** — `docker compose -f docker-compose.demo.yml up -d` gives a pre-seeded environment on ports 8080/8502 in under 2 minutes; ~740 validation events across 7 contracts plus a full draft→review→active lifecycle demo
- **Demo seeder** — `scripts/seed_demo_data.py`: idempotent, deterministic (seed 42), realistic UK data with deliberate failure injection
- **`DEMO_MODE` env var** — startup banner when running the demo compose; no effect in standard deployments
- **`docs/postman.md`** — import guide, 5-request quickstart, folder reference
- **`docs/demo.md`** — launch guide, 5-step exploration path, reset and production migration instructions
- **README Quick Start** — demo compose and Postman rows added at the top of the Quick Start section

## [1.3.0] - 2026-03-22

### Contracts

Seventeen contracts upgraded from thin/weak presence checklists to production-grade
with deep domain-specific validation and regulatory commentary. AI team contract audit
(Opus) identified 14 of 40 domain contracts still as presence checklists post-v1.2.3.
This release clears the backlog completely — the contract portfolio is now 0 thin/weak.

**Bottom 5 upgraded (Weak/Thin → Solid):**

- **`automotive_vehicle`** — ISO 3779 VIN regex (17-char, excludes I/O/Q), DVLA
  fuel_type / transmission / body_type allowed_values, UK registration format,
  500k km anomaly warning
- **`pharma_clinical_trial`** — ClinicalTrials.gov NCT regex, ICH-GCP trial_phase
  values, CTCAE adverse_event_severity, informed_consent, dose_unit, subject_id
  uniqueness
- **`financial_trade`** — ISIN regex (ISO 6166), LEI regex (ISO 17442), trade_side
  values, MiFID II instrument_type taxonomy, CSDR settlement_status, settlement_date
  ≥ trade_date compare
- **`fmcg_product`** — GTIN-8/12/13/14 barcode regex, GS1 GPC category taxonomy,
  ISO 4217 currency, pack_unit standardisation, ISO 3166-1 country_of_origin
  (UK Food Information Regulations 2014)
- **`water_utility_reading`** — MOSL meter_status and read_type values (estimated
  reads invalid for billing disputes), Ofwat PR24 1,000 m3 anomaly warning,
  current ≥ previous monotonic compare

**Honourable mentions upgraded (Weak/Thin → Solid):**

- **`media_content`** — EIDR content_type taxonomy, BBFC/PEGI age rating, ISO 639-1
  language (AVMS Directive), ISO 3166-1 rights_territory, 24h duration anomaly
- **`technology_event`** — Segment Spec event_type taxonomy, platform values, UUID
  v4 event_id format, semver sdk_version validation
- **`agriculture_batch`** — AHDB crop_type taxonomy, certification scheme values
  (Red Tractor / LEAF Marque / Rainforest Alliance / Fairtrade / GlobalG.A.P.),
  ISO 3166-1 country_of_origin, yield anomaly warning
- **`retail_product`** — GTIN barcode regex, GS1 UK department taxonomy, ISO 4217
  currency, product_status lifecycle values, POS name max_length
- **`real_estate_property`** — RICS property_type taxonomy, EPC A-G values with
  MEES Regulations 2018 context, council_tax_band A-I, tenure values, listing_status

**Solid → Production-grade upgrades:**

- **`healthcare_patient`** — NHS 16+1 ethnicity, sex values (NHS Data Dictionary),
  HES admission_type, discharge_date ≥ admission_date compare, discharge_reason,
  blood_type annotated as NHS Never Event
- **`hr_employee`** — HMRC NI number regex (prefix exclusion rules), NMW/NLW salary
  warning, ISO 4217 salary_currency, employment_status lifecycle, right_to_work_status
  (Immigration Act 2014, £60k penalty context)
- **`insurance_claim`** — claim_date ≥ incident_date compare (Insurance Act 2015),
  ISO 4217 currency, IFB/IFED fraud_indicator taxonomy, excess_amount, extended
  claim_type including cyber, extended status values
- **`logistics_shipment`** — ICC Incoterms 2020 (DAT correctly replaced by DPU),
  HS tariff code regex (UK CDS), shipment_mode values, ISO 4217 currency,
  estimated_delivery ≥ dispatch compare, weight anomaly, extended status values
- **`manufacturing_iot`** — ISA-88/ISA-95 device_type taxonomy, ISA-95 status
  values, IEC 62682 alert_level, humidity 0-100% range, vibration ISO 10816 anomaly,
  OEE 0-100% bounds, pressure anomaly, extended unit_of_measure
- **`energy_meter_reading`** — MPAN 13-digit regex (ECOES/DCC), MPRN 6-10 digit
  regex (Xoserve), Ofgem BSCP read_type values, kWh anomaly, current ≥ previous
  compare, Ofgem BSC supply_type, extended meter_type (solar_export, ev_charger)
- **`telecoms_cdr`** — ITU-T E.164 MSISDN regex, GSMA IMEI 15-digit format, PLMN
  MCC-MNC format, call_end ≥ call_start compare, rating_status lifecycle,
  roaming_country ISO 3166-1, extended call_type (premium_rate, emergency)

### Fixes

- Remove extraneous `f` prefix from two string literals in `core/onboarding.py`
  (ruff F541)
- Rename ambiguous variable `l` → `ln` in `tests/test_core.py` (ruff E741)

### Tests

2,383 passing (was 2,261 in v1.2.3) — 122 new tests from upgraded contracts.

---

## [1.2.3] - 2026-03-22

### Features

- **`allowed_values` rule type** — validate that a field value is one of an inline
  list without needing a separate lookup file. Supports single-record and DuckDB
  batch validation.

  ```yaml
  - name: status_valid
    field: status
    type: allowed_values
    allowed_values: [active, inactive, pending]
    severity: error
    error_message: "status must be one of: active, inactive, pending"
  ```

- **Lifecycle webhooks** — three new webhook events fire on contract lifecycle
  transitions: `opendqv.contract.submitted` (DRAFT → REVIEW),
  `opendqv.contract.approved` (REVIEW → ACTIVE), `opendqv.contract.rejected`
  (REVIEW → DRAFT). Subscribers can notify approvers automatically.

### Contracts

Eight "adequate starter" contracts upgraded with domain-specific validation:

- **`telecoms_cdr`** — fixed `call_start`/`call_end` from date to datetime format
  (CDRs record to the second); added `call_type` allowed_values
- **`healthcare_patient`** — added ICD-10 `diagnosis_code` regex
- **`banking_transaction`** — added `transaction_type` allowed_values,
  `account_number` min_length
- **`hr_employee`** — added `contract_type` allowed_values
- **`insurance_claim`** — added `claim_type` and `claim_status` allowed_values
- **`logistics_shipment`** — added ISO 3166-1 alpha-2 country code regex,
  `shipment_status` allowed_values
- **`manufacturing_iot`** — fixed `timestamp` from date to datetime format;
  added `unit_of_measure` allowed_values
- **`energy_meter_reading`** — added `meter_type` and `reading_unit` allowed_values

Suite: 2,261 passing, 24 skipped.

---

## [1.2.2] - 2026-03-22

### Fixes

- **Code generator — silent gap eliminated:** Rule types not implemented by a
  generator target previously emitted nothing (silent drop). Now emit an explicit
  `// NOTE: requires API validation` comment for known API-only types
  (`required_if`, `lookup`, `compare`, `date_diff`, `checksum`, etc.) and a
  `// TODO` comment for any unknown future types. Users deploying generated code
  can now see exactly which rules are enforced and which require the live API.
- **Salesforce generator — `max` rule missing:** The `max` rule type was implemented
  in Snowflake and JS targets but silently dropped in Salesforce Apex. Fixed.
- **Code generator docstring:** Removed false claim "Covers all rule types."

### Tests

- **58 new parametric tests:** `TestCodeGeneratorRuleCoverage` — every rule type
  across all three targets (snowflake, salesforce, js) must produce at least one
  line of output. Silent drops will now fail CI immediately.

Suite: 2,242 passing, 24 skipped (+61 from generator coverage tests).

---

## [1.2.1] - 2026-03-22

### UI

- **Governance Audit Trail** — "Version History" tab renamed "Contract Audit &
  Lifecycle". Now shows hash chain integrity banner (✅ intact / ❌ broken),
  timeline view with proposed-by / approved-by / rejected-by / rejection-reason
  per entry, and raw history table in collapsible expander. All governance fields
  were already stored in the DB; this release surfaces them.

### Documentation

- **`docs/faq.md`** — new FAQ covering: LLM/Claude scripts vs OpenDQV, GE/Soda/dbt
  comparison, outsourced stored procedures, Databricks/Snowflake migrations, catalog
  complementarity, production readiness, and quickstart. Linked from nav bar.
- **README** — compute cost reality section, governance moat in first 200 words,
  shift-left solution framing, FAQ nav link.

### Fixes

- `core/contracts.py` + `core/storage.py`: `get_history()` now returns
  `proposed_by`, `proposed_at`, `rejected_by`, `rejected_at`, `rejection_reason`
- `api/models.py`: `ContractHistoryEntry` exposes all governance fields
- `tests/test_e2e.py`: `TestContractAuditLifecycle` — 7 Playwright E2E tests
- Lint: split import (E401) and unused hashlib import (F401) fixed

Suite: 2,181 passing, 24 skipped.

---

## [1.2.0] - 2026-03-21

### Contracts

- **`dora_ict_incident`** — EU DORA (Digital Operational Resilience Act), Articles 17-19.
  ICT incident reporting for EU financial entities (in force 17 January 2025). Enforces
  incident classification, 24h early warning and 72h notification windows via `date_diff`
  rule, root cause documentation for major/significant incidents, and remediation tracking.
  30 rules. 3 new reference files.

- **`hipaa_disclosure_accounting`** — US HIPAA 45 CFR 164.528. Accounting of disclosures
  for covered entities and business associates. Enforces recipient type, disclosure purpose,
  authorization reference (required for patient_authorization purpose), and minimum necessary
  determination (required for all non-treatment disclosures). 27 rules. 3 new reference files.

- **`sox_control_test`** — US Sarbanes-Oxley Act 2002, Sections 302/404. Internal control
  test record for US public companies. Three-level `required_if` cascade: ineffective test
  → deficiency classification → remediation plan + audit committee escalation for material
  weaknesses. 32 rules. 5 new reference files.

- **`eu_gdpr_processing_record`** — EU GDPR Article 30 ROPA. EU variant of the UK GDPR
  contract with EU Standard Contractual Clauses, 27-DPA supervisory authority lookup, and
  EU adequacy decision list. 31 rules.

- **`eu_gdpr_dsar_request`** — EU GDPR Article 15 DSAR. EU variant with EUR penalty
  references and EU supervisory authority. 31 rules.

- **`mifid_transaction_report`** — MiFID II / MiFIR Article 26. Transaction reporting for
  investment firms. LEI regex, ISIN regex, venue MIC regex enforced at point of write.
  30 rules. 3 new reference files.

Suite: 2,181 passing, 24 skipped (+239 from contract linter coverage of 6 new contracts).

---

## [1.1.0] - 2026-03-21

### Contracts

- **`gdpr_processing_record`** — UK GDPR Article 30 Record of Processing Activities
  (ROPA). Enforces lawful basis declaration (all 6 Article 6 bases), consent-specific
  fields (mechanism, timestamp, withdrawal) via `required_if`, Legitimate Interests
  Assessment gating, special category data basis (Article 9), international transfer
  safeguard, and DPO audit trail. 29 rules. 7 new reference files.

- **`gdpr_dsar_request`** — UK GDPR Article 15 Data Subject Access Request handling.
  Enforces 30-day response deadline recording at intake, identity verification gate,
  extension logic (`required_if extension_applied=true`), outcome and refusal tracking.
  31 rules.

- **Removed `books.yaml`** — accidental wizard output committed during testing.
  Contained a `merchant_category_code` artifact from an unrelated domain.

- **Removed `customer_onboarding.yaml`** — redundant with `customer` contract. Was
  in a legacy schema format incompatible with the standard contract loader.

### Security

- **Dependency floors tightened:** `PyJWT>=2.12.0` (CVE-2026-32597 — JWT algorithm
  confusion), `urllib3>=2.6.3` (4 CVEs), `cryptography>=44.0.1` (CVE-2024-12797).

### CI

- **Coverage reporting:** `pytest-cov` with `--cov-branch` now runs on every push.
  Coverage report uploaded to Codecov. Branch coverage: 74% across `core/`, `api/`,
  `security/`, `sdk/`.

- **Release automation:** `release.yml` workflow — trigger via GitHub UI
  (Actions → Create Release → Run workflow). Selects patch/minor/major bump,
  extracts CHANGELOG notes, creates tag and GitHub release. PyPI publish and
  Docker rebuild fire automatically from the release and tag events.

### Documentation

- New integration guide: `docs/integrations/gdpr-compliance.md`
- README: three-layer governance table moved to top; nav links repositioned above
  demo GIF; install script leads Option 2; ethos line labelled; GDPR callout block
  added.

### Badges

- Added: Ruff, OpenSSF Best Practices (100% passing), Coverage (74% branch).
- PyPI badge cache-busted (`?style=flat`).

Suite: 1,942 passing, 25 skipped.

---

## [1.0.7] - 2026-03-21

### Fixes

- **PyPI publish workflow** — all releases since v1.0.1 failed to publish to PyPI
  with `400 Bad Request` because `pyproject.toml` was never bumped from `1.0.0`.
  Fixed by: (a) adding a `poetry version ${GITHUB_REF_NAME#v}` step that derives
  the package version from the git tag automatically on every future release, and
  (b) adding `permissions: read-all` at the workflow top level in
  `docker-publish.yml` (Scorecard `TokenPermissions` check), and (c) pinning
  `poetry==2.3.2` in `publish.yml` (Scorecard `PinnedDependencies` check).
  The v1.0.7 release is the first to publish correctly since v1.0.0.

Suite: 1,876 passing, 25 skipped (no code changes).

---

## [1.0.6] - 2026-03-21

### Contracts

- **`martyns_law_event`** — Martyn's Law (Terrorism (Protection of Premises) Act
  2025) qualifying events contract. Distinct from `martyns_law_venue`: the
  responsible party is the event organiser, the SIA obligation is notification
  (not registration), staff obligation is a pre-event briefing (not ongoing
  training), and records are time-bounded with `event_start_date` /
  `event_end_date`. 33 rules. New reference file: `martyns_law_event_types.txt`.

### Fixes

- **Stale pip output file** — `=2.10.0` in repo root (stale artifact from v1.0.2
  PyJWT install, shell redirect mishap) removed.

Suite: 1,876 passing, 25 skipped (+43 from contract linter coverage of new contract).

---

## [1.0.5] - 2026-03-21

### Contracts

- **`building_safety_golden_thread`** — Building Safety Act 2022 Golden Thread
  compliance contract for higher-risk buildings (18m+ / 7+ storeys). Enforces
  accountable person appointment, BSR registration, safety case documentation,
  and golden thread audit trail at the point of write. 26 rules. New reference
  files: `building_safety_primary_uses.txt`.

- **`companies_house_filing`** — Economic Crime and Corporate Transparency Act
  2023 compliance contract for Companies House director and PSC identity
  verification. Enforces mandatory ID verification before a filing record is
  saved. 23 rules. New reference files: `companies_house_roles.txt`,
  `companies_house_id_verification_methods.txt`.

Suite: 1,780 passing, 25 skipped (no new tests — contract-only additions).

---

## [1.0.4] - 2026-03-21

### Contracts

- **`ppds_menu_item`** — Natasha's Law (Food Information (Amendment) (England)
  Regulations 2019) allergen compliance contract for Pre-Packed for Direct Sale
  (PPDS) food. All 14 major allergens are mandatory fields — omission triggers a
  422 before the record enters the system. 49 rules. New reference files:
  `allergen_boolean.txt`, `allergen_gluten_cereals.txt`,
  `allergen_tree_nut_types.txt`, `qsr_item_categories.txt`.

- **`martyns_law_venue`** — Terrorism (Protection of Premises) Act 2025
  compliance contract for venues and events. Two-tier enforcement: standard duty
  (200–799 capacity) and enhanced duty (800+, requires named SRP, SIA
  registration, Terrorism Protection Plan). 29 rules. New reference files:
  `martyns_law_duty_tiers.txt`, `martyns_law_venue_types.txt`.

Suite: 1,780 passing, 25 skipped (no new tests — contract-only additions).

---

## [1.0.3] - 2026-03-21

### Fixes

- **Three additional unprotected context endpoints** — `POST /generate`,
  `GET /export/gx/{name}`, and `GET /export/odcs/{name}` were missing the
  `UnknownContextError` try/except guard that existed on the three validate
  endpoints. An unknown `context` parameter on any of these would have
  produced an unhandled exception. Now returns 422 consistently.
- **Regex rule with no `pattern` now fails records** — previously a `regex`
  rule with no `pattern` field silently passed every value (no-op). Now
  returns `error_message` so the misconfiguration is visible immediately
  rather than silently corrupting data quality guarantees. This is the
  production-side fix that complements the contract linter (see below).
- **Misconfiguration warnings at contract load time** — `rule_parser.py`
  now logs a warning when rules are loaded with missing required fields:
  `regex` without `pattern`, `lookup` without `lookup_file`, `checksum`
  without `checksum_algorithm`, `date_diff` without `date_diff_field`.
  Makes broken contracts visible at startup rather than silently at
  validation time.

### Tests

454 net new tests. Suite: 1,679 passing, 25 skipped.

- **Contract linter** (`tests/test_contract_linter.py`) — 8 semantic
  completeness checks parametrised across all 29 standard contracts.
  Verifies regex rules have patterns, lookup rules have files, checksum
  rules have algorithms, compare rules have operands, date_format explicit
  formats are valid strftime, all rule types are known. A regression of the
  a regression of the `customer.yaml` no-op email rule would now be caught.
- **Endpoint consistency** (`tests/test_endpoint_consistency.py`) — unknown
  context → 422 parametrised across all 6 context-accepting endpoints.
  Adding a new endpoint without updating the list breaks the suite visibly.
- **Rule model field audit** (`tests/test_rule_model_fields.py`) — explicit
  behaviour tests for `format` (date_format), `cache_ttl`, and
  `lookup_auth_header`. Locks in that each field has the effect the model
  promises.
- **Auth mode matrix** (`tests/test_auth_modes.py`) — tests `/explain` in
  `AUTH_MODE=open` without a token (regression lock), open-mode
  bypass on key endpoints, token-mode enforcement. Tests the bypass, not
  just the block.
- **DB isolation** (`tests/conftest.py`) — test DB now uses a fresh temp
  directory per session. Eliminates false positives from stale history
  snapshots between runs.
- **Smoke test Part 4** (`scripts/run_smoke_tests.sh`) — verifies
  `PYTHON=python3.11 bash install.sh` works on a machine where `python3`
  is not in PATH (regression lock).

---

## [1.0.2] - 2026-03-21

### Security

- **Replace `python-jose` with `PyJWT`** — `python-jose` pulled `ecdsa` as a
  transitive dependency (CVE-2024-23342, Minerva timing attack on P-256). OpenDQV
  uses `HS256` exclusively; `ecdsa` was never exercised. Migrated to `PyJWT>=2.10.0`
  which has zero extra dependencies. `ecdsa`, `pyasn1`, and `rsa` are removed from
  the dependency tree. API surface unchanged — `jwt.encode`/`jwt.decode` signatures
  are identical.
- **Starlette `FileResponse` DoS alerts dismissed** (CVE-2025-62727, CVE-2025-54121)
  — OpenDQV uses neither `FileResponse` nor `StaticFiles`. Both vulnerable code paths
  are unreachable. Alerts dismissed with documented rationale.

### Documentation

- `README.md` — added *"The shift-left distinction that actually matters"* section
  to `## Why OpenDQV?`: direct comparison table contrasting industry "shift-left" tools
  with true pre-write validation.
- `README.md` — added three-layer governance architecture table to `## What OpenDQV
  is NOT`: write-time enforcement (OpenDQV) / catalog+stewardship (Atlan, Collibra,
  Purview) / pipeline testing+observability (GX, Soda, Monte Carlo). Answers the
  most common evaluator question — how does this fit with tools we already have?

---

## [1.0.1] - 2026-03-21

### Fixes

- **`date_format` validator** — `rule.format` (strftime syntax) is now used as the
  primary format when specified. Previously the field was accepted by the Rule model
  but silently ignored; only four hardcoded formats were tried. Custom formats such as
  `'%Y-%m-%d %H:%M:%S'` (space-separated datetime, common in SQL Server exports) now
  validate correctly.
- **`/explain` endpoint** — respects `AUTH_MODE=open`. The auth check order was
  inverted: an absent token raised 401 before the auth-mode check was reached, making
  the endpoint unreachable without a token even in open mode.
- **`/validate/batch/file`** — unknown `context` values now return `422` instead of
  an unhandled exception. The try/except for `UnknownContextError` was present on
  `/validate` and `/validate/batch` but missing on the file upload endpoint.
- **`contracts/customer.yaml`** — `valid_email` regex rule now includes the email
  pattern. The rule existed but had no `pattern` field, making it a no-op that
  accepted any value including invalid emails.
- **`install.sh`** — added `PYTHON` environment variable override. Users with Python
  3.11 installed under a non-default command (e.g. `python3.11` via Homebrew on macOS)
  can now run `PYTHON=python3.11 bash install.sh` instead of failing silently.

### Documentation

- `docs/rules/explain_endpoint.md` — corrected auth behaviour: documents
  `AUTH_MODE=open` vs `AUTH_MODE=token` behaviour and `OPENDQV_EXPLAIN_PUBLIC` flag.
- `README.md` — `date_format` rule entry clarified: `format` is optional, uses Python
  strftime syntax, tried before fallback list; all four fallback formats listed.
- `docs/troubleshooting.md` — added `PYTHON=python3.11 bash install.sh` override
  under Python version troubleshooting.
- `docs/quickstart.md` — same PYTHON override tip added to Python install section.
- `docs/runbook.md` — corrected token generation curl command to current API path.

---

## [1.0.0] - 2026-03-20

Initial public release.

### Core
- Single-record and batch validation engine (DuckDB-powered batch)
- 24 rule types: `regex`, `min`, `max`, `range`, `not_empty`, `min_length`, `max_length`, `date_format`, `unique`, `min_age`, `max_age`, `lookup`, `compare`, `required_if`, `age_match`, `checksum`, `cross_field_range`, `field_sum`, `forbidden_if`, `conditional_value`, `date_diff`, `ratio_check`, `conditional_lookup`, `geospatial_bounds`
- YAML data contracts with context-aware field overrides
- Contract lifecycle management (draft / review / active / archived) and version history

### API & Integrations
- FastAPI REST API with JWT PAT authentication (open / token modes)
- GraphQL API
- MCP server — exposes all six tools (`validate_record`, `validate_batch`, `list_contracts`, `get_contract`, `explain_error`, `create_contract_draft`) to Claude Desktop, Cursor, and any MCP-compatible agent framework
- MCP `create_contract_draft` write tool — agents can propose contracts; blocked from activation until human approves via review workflow
- Contract review workflow — `DRAFT → REVIEW → ACTIVE` lifecycle with `submit-review`, `approve`, `reject` endpoints; MCP-sourced drafts cannot bypass review
- Token role differentiation — `validator`, `editor`, `approver`, `auditor`, `admin` roles on PATs
- Importers: Great Expectations (v0.x + v1.x), dbt schema.yml, Soda Core checks, CSV rule definitions, ODCS 3.1, CSVW (W3C CSV on the Web), OTel semantic conventions, NDC (FDA National Drug Code)
- Webhook notifications for `opendqv.validation.failed`, `opendqv.validation.warning`, `opendqv.batch.failed`
- Push-down code generation (Salesforce Apex, JavaScript, Snowflake UDF)
- Python SDK with guard decorator
- Federation — publish contracts to a parent node

### Tooling
- Streamlit workbench UI (Contracts, Validate, Profiler, Webhooks, Version History, CLI Guide, and more)
- CLI tool with `list`, `show`, `validate`, `generate`, `import-*`, `export-gx`, `export-odcs`, `export-dbt`, `audit-verify`, `contracts-import-dir` commands
- CLI review commands — `submit-review`, `approve`, `reject`, `token-generate` subcommands
- Onboarding wizard — Docker detection, rule inference, starter contract, first validation in under 90 seconds
- Rule profiler — analyse datasets to auto-generate contracts with suggested rules
- Prometheus metrics and monitoring dashboard
- `scripts/run_smoke_tests.sh` — 43-check smoke test suite (isolated unit tests, full HTTP stack, pip install CLI) with pre-flight port check
- `Dockerfile.smoketest` — clean-room Python 3.11 container for unit test isolation

### Security
- Role validation whitelist at token generation — `/tokens/generate` now rejects unknown roles (e.g. `superadmin`) with HTTP 422. Only the six defined roles (`validator`, `reader`, `auditor`, `editor`, `approver`, `admin`) are accepted.
- RBAC enforcement on import, webhook, and reload endpoints — `POST /import/*` and `POST/DELETE /webhooks` now require `editor` or `admin`; `POST /contracts/reload` requires `admin`. Previously any authenticated user could trigger these operations.
- RBAC documentation corrected — all roles can validate (no role check on `/validate`); `reader` and `validator` are semantically distinct but functionally equivalent; `auditor` additionally has access to `GET /trace/verify`.

### Audit
- NTP clock synchronisation check at startup — OpenDQV queries `pool.ntp.org` at startup and records the result (`clock_status`, `skew_ms`, `ntp_source`) in the node health log. Gives auditors evidence that timestamps were accurate when the chain was written. Graceful failure if network unavailable.
- `opendqv audit-verify` upgraded — now outputs a **Clock Synchronization** section after chain integrity. Shows clock status for every startup event; warns if skew > 5 seconds or NTP was unavailable.
- `core/clock_sync.py` — new module. Pure socket NTP query, 2-second timeout, no external dependencies. RFC 3161 trusted timestamp anchoring is the documented commercial upgrade path for regulated environments.

### Fixes
- `compare_to: now` timezone handling — sentinel now uses `datetime.now(timezone.utc)` (was naive). Timezone-aware input values (e.g. `+01:00`, `Z`) are normalised to UTC before comparison. Previously raised `TypeError` when comparing aware and naive datetimes.
- `compare_to: today`, `min_age`, `max_age` — all `datetime.today()` calls replaced with `datetime.now(timezone.utc)`. Sentinel now resolves to the current UTC date consistently regardless of server timezone setting.
- `cli.py` `token-generate` command — fixed `KeyError: 'pat'` (key is `'token'`)
- `pyproject.toml` — added `cli.py`, `config.py`, `main.py` to `packages`; previously missing, causing `ModuleNotFoundError` after `pip install`

### Deployment
- Docker Compose with dev, prod, and perf overlays
- Production serving via Gunicorn + UvicornWorker
- GitHub Actions CI/CD pipeline
- SQLite persistence for contract history and webhooks

### Performance
- ~208 req/s sustained (4 Gunicorn workers, 5-minute stabilised), p50=19ms, p99=205ms, zero errors across 218K requests
- Benchmarked on Dell XPS 13 i5-7200U (Linux, native Docker)
- macOS (i7-1068NG7, Docker Desktop): 257.3 req/s sustained over 10 minutes, zero errors across 233K requests
- ARM64 (Raspberry Pi 400): 79.1 req/s sustained over 10 minutes, zero errors across 72K requests
- Windows 10 (i7, Docker Desktop): 185.1 req/s, zero errors
