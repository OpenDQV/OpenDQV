# Changelog

All notable changes to OpenDQV are documented here.

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
