# Compliance Contracts Reference

OpenDQV ships 41 production-ready contracts in `opendqv/contracts/` (installed with the
wheel; a mirror of the OpenDQV Cloud golden library — see `library_manifest.json` at the repo root) covering agriculture, automotive,
banking, building safety, corporate compliance, data protection, education, energy, financial
controls, FMCG, food safety, healthcare, HR, insurance, logistics, manufacturing, media,
pharma, public safety, public sector, real estate, retail, telecoms, travel, water utility,
and more — across UK, EU, and US regulatory frameworks.

17 industry starter templates are in `examples/starter_contracts/` — they are not loaded
by default; copy the ones you need into `$OPENDQV_CONTRACTS_DIR`.

See [docs/community_use_cases.md](community_use_cases.md) for real-world examples by industry.

---

## Full Contract List

| Contract | Description | Contexts | Highlights |
|----------|-------------|----------|-----------|
| `customer` | General customer validation (email, age, name, phone, etc.) | — (worked example with `kids_app` / `financial` in `examples/contexts/`) | — |
| `salesforce_contact` | Salesforce Contact — 18 validation criteria, production-grade | — (example in `examples/contexts/`) | Sentinel date rejection |
| `salesforce_lead` | Salesforce Lead — 16 validation criteria with lead-specific checks | — (example in `examples/contexts/`) | — |
| `proof_of_play` | **Reference contract: OOH advertising impression validation** | — (`billing` / `operations` example in `examples/contexts/proof_of_play.yaml`) | Cross-field rules, conditional constraints, context-aware billing thresholds |
| `social_media_age_compliance` | UK Online Safety Act / Ofcom age assurance — 13+ age gate, DOB consistency, identity verification audit trail | — | `age_match` rule, identity verification lookup, verification timestamp |
| `ppds_menu_item` | Natasha's Law (PPDS) allergen compliance — all 14 major allergens must be explicitly declared before a QSR menu item is saved or labelled | — | 14 mandatory boolean fields, `required_if` for gluten/tree-nut type, sulphite threshold, audit trail |
| `martyns_law_venue` | Martyn's Law (Terrorism (Protection of Premises) Act 2025) — venue terrorism preparedness compliance, two-tier (standard/enhanced), mandatory SRP and SIA registration for 800+ capacity venues | — | Two-tier `required_if` enforcement, capacity minimum, enhanced-duty field gate, audit trail |
| `martyns_law_event` | Martyn's Law — qualifying events (temporary/one-off, 800+ expected attendance, s.3(1)(d); there is no 200-person tier for events, so every qualifying event carries the enhanced-level duties). Responsible-person-centric; SIA notification not registration; staff briefing not training; time-bounded with start/end dates | — | Distinct from venue contract: no `duty_tier`, `expected_attendance` min 800, `sia_notification_reference` not `sia_registration_number`; event dates required |
| `building_safety_golden_thread` | Building Safety Act 2022 — Golden Thread compliance for higher-risk buildings (18m+ / 7+ storeys). Enforces accountable person, BSR registration, safety case, and golden thread audit trail at point of write | — | Named accountable person + BSM mandatory, BSR registration gate, `required_if safety_case_documented = true` |
| `companies_house_filing` | Economic Crime and Corporate Transparency Act 2023 — identity verification for Companies House director and PSC filings. A missing verification field blocks the record before submission | — | `required_if id_verification_completed = true` gates method, date, and verifier; role and method lookups |
| `gdpr_processing_record` | UK GDPR Article 30 — Record of Processing Activities (ROPA). Enforces lawful basis declaration, consent-specific fields, legitimate interests assessment, special category data basis, and international transfer safeguard at the point of write | — | All 6 Article 6 lawful bases via lookup; consent/LIA/special-category/transfer fields via `required_if`; DPO audit trail |
| `gdpr_dsar_request` | UK GDPR Article 15 — Data Subject Access Request handling. Enforces 30-day deadline recording, identity verification gate, extension logic, and outcome tracking before a request enters the case management workflow | — | 30-day deadline field required at intake; `required_if` for verification method, extension reason, and refusal reason |
| `eu_gdpr_processing_record` | EU GDPR Article 30 — Record of Processing Activities (ROPA). EU variant with EU Standard Contractual Clauses, 27-DPA supervisory authority lookup, and EU adequacy decision list | — | EU transfer safeguards and supervisory authority lookup; otherwise identical pattern to UK GDPR |
| `eu_gdpr_dsar_request` | EU GDPR Article 15 — Data Subject Access Request handling. EU variant with €20M / 4% turnover penalty references and EU supervisory authority | — | Same enforcement pattern as UK GDPR DSAR; fines referenced in EUR |
| `dora_ict_incident` | EU DORA (Digital Operational Resilience Act) Articles 17-19 — ICT incident report for financial entities. In force 17 January 2025. Enforces incident classification, the major-incident reporting clock of CDR (EU) 2025/301 (initial notification, intermediate report, final report), and root cause documentation | — | Reporting fields `required_if` classification is `major`; `date_diff` enforces initial notification within 24 h of awareness *and* 4 h of major classification (`max: 0.16667` days), intermediate report within 72 h of the initial notification, final report within 31 days |
| `hipaa_disclosure_accounting` | US HIPAA 45 CFR 164.528 — Accounting of Disclosures. Enforces complete disclosure records before they enter covered entity systems. OCR penalties up to $2.1M/year | — | `required_if` for authorization_reference when patient_authorization; minimum_necessary_applied boolean gated on non-treatment purposes |
| `sox_control_test` | US Sarbanes-Oxley Act 2002, Sections 302/404 — Internal control test record. CEO/CFO personal certification liability. Enforces deficiency classification and remediation plan completeness before control test records are saved | — | Three-level `required_if` cascade: test_result → deficiency_classification → remediation plan + audit committee escalation |
| `mifid_transaction_report` | MiFID II / MiFIR Article 26 — Transaction reporting for investment firms and trading venues. Enforces LEI and ISIN check digits and venue MIC membership at point of write before submission to an Approved Reporting Mechanism | — | `checksum` (`lei_mod97`, `isin_luhn`) on reporting/executing entity and instrument; venue MIC `lookup` against ISO 10383; buyer/seller ID type lookup (RTS 22: `LEI NIDN CCPT CONCAT MIC INTC`); `transaction_type` `BUYI`/`SELL`; `price >= 0`, `quantity > 0` |

For the remaining 25+ contracts (agriculture, automotive, energy, HR, insurance, logistics,
manufacturing, media, pharma, real estate, telecoms, travel, water utility, and more) see
the [`opendqv/contracts/`](../opendqv/contracts/) directory directly.

---

## Regulatory Context

### UK Online Safety Act (Ofcom enforcement from January 2026)

The `social_media_age_compliance` contract demonstrates age assurance patterns required by the
UK Online Safety Act 2023: 13-year age gate, age/DOB consistency check (`age_match` rule),
identity verification method tracking, and verification timestamp audit trail.

### Natasha's Law (in force 1 October 2021)

The `ppds_menu_item` contract enforces explicit allergen declaration for Pre-Packed for Direct
Sale (PPDS) food at the point of write. All 14 major allergens are mandatory fields — omission
is structurally impossible and triggers a 422 before the record enters the system. See
[docs/integrations/natasha-law-compliance.md](integrations/natasha-law-compliance.md).

### Martyn's Law (Royal Assent 3 April 2025)

The `martyns_law_venue` and `martyns_law_event` contracts enforce terrorism preparedness
compliance. Venues qualify at a capacity of 200 or more (two tiers: standard 200–799, enhanced
800+, declared in `duty_tier`); enhanced-duty premises must declare a named designated senior
individual, SIA notification reference, and public protection measures. Qualifying *events* are
those where 800 or more individuals may be present (s.3(1)(d)) — there is no 200-person tier for
events, so the event contract has no `duty_tier` and every qualifying event carries the
enhanced-level duties. Omission triggers a 422 before the record enters the system. Named
after Martyn Hett (1987–2017), killed in the Manchester Arena attack. See
[docs/integrations/martyns-law-compliance.md](integrations/martyns-law-compliance.md).

### Building Safety Act 2022 — Golden Thread

The `building_safety_golden_thread` contract enforces the Act's own obligation — "accurate and
up-to-date information throughout the building lifecycle" — for higher-risk buildings (18m+ or
7+ storeys). Accountable person, BSR registration number, and safety case documentation are
mandatory fields; omission triggers a 422 before the record enters the system. See
[docs/integrations/building-safety-golden-thread.md](integrations/building-safety-golden-thread.md).

### Economic Crime and Corporate Transparency Act 2023

The `companies_house_filing` contract enforces identity verification for Companies House
director and PSC filings. A record with `id_verification_completed` undeclared, or with
verification details missing, is rejected before it enters the filing system. See
[docs/integrations/companies-house-filing.md](integrations/companies-house-filing.md).

### UK GDPR / Data Protection Act 2018

Two contracts enforce the UK's most universally applicable data regulation.
`gdpr_processing_record` enforces Article 30 Records of Processing Activities — lawful basis,
data categories, consent fields, and retention period are mandatory before a ROPA entry is
saved. `gdpr_dsar_request` enforces Article 15 Subject Access Request handling — receipt date,
response deadline (30 days), and identity verification must be recorded before a request enters
any workflow. See [docs/integrations/gdpr-compliance.md](integrations/gdpr-compliance.md).

### EU GDPR (Regulation (EU) 2016/679)

`eu_gdpr_processing_record` and `eu_gdpr_dsar_request` mirror the UK GDPR contracts with
EU-specific transfer safeguards (Standard Contractual Clauses), all 27 national supervisory
authority codes, and EUR penalty references. Applies to any organisation processing personal
data of EU residents.

### EU DORA — Digital Operational Resilience Act (in force 17 January 2025)

`dora_ict_incident` enforces ICT incident reporting completeness for EU financial entities.
Incident classification, affected services, and root cause are mandatory before an incident
record enters a case management system. For incidents classified `major`, the reporting
fields become `required_if` and `date_diff` rules enforce the clock set by CDR (EU) 2025/301:
initial notification within 24 hours of becoming aware and within 4 hours of classifying the
incident as major (`max: 0.16667` days — the fractional `date_diff` added in 2.7.0),
intermediate report within 72 hours of the initial notification, and final report within 31
days of the intermediate report.

### US HIPAA — 45 CFR 164.528

`hipaa_disclosure_accounting` enforces complete accounting of PHI disclosures before records
enter covered entity systems. Authorization reference is required when purpose is
patient_authorization; minimum necessary determination is required for all non-treatment
disclosures under 45 CFR 164.502(b). OCR civil penalties up to $2.1M per violation category
per year.

### US Sarbanes-Oxley Act 2002 — Sections 302/404

`sox_control_test` enforces SOX internal control test record completeness. A three-level
`required_if` cascade ensures that ineffective test results require deficiency classification,
and material weaknesses require remediation plans and audit committee escalation — before the
record enters the GRC system. Applies to all US public companies (~4,200 NYSE/NASDAQ listed
companies).

### MiFID II / MiFIR Article 26

`mifid_transaction_report` enforces transaction reporting completeness for investment firms and
trading venues. LEI and ISIN are check-digit validated (`checksum`: `lei_mod97`, `isin_luhn`),
venue MIC is checked against the ISO 10383 list, buyer/seller identifier types against the
RTS 22 code set (`LEI NIDN CCPT CONCAT MIC INTC`), `transaction_type` must be `BUYI` or `SELL`,
and `price >= 0` / `quantity > 0` — all at point of write before submission to an Approved
Reporting Mechanism. Applies across EU and UK markets.

---

## Reference Contract: `proof_of_play`

The `proof_of_play` contract is the recommended starting point for learning cross-field rules
and conditional constraints. It demonstrates:

- `compare` rule: `impression_end` must be strictly after `impression_start` (catches phantom billing from inverted timestamps)
- `required_if` rule: `refresh_rate_hz` required only when `panel_type == DIGITAL`
- `condition` block: revenue floor applied only to `CHARGE` records, not `CREDIT` notes
- Contexts: the bundled contract carries none (no bundled contract does — 2.7.0 library mirror); the `billing` (all warnings become errors) and `operations` (relaxed thresholds for dashboards) worked example lives in `examples/contexts/proof_of_play.yaml`
