# Compliance Contracts Reference

OpenDQV ships 44 production-ready contracts in `contracts/` covering agriculture, automotive,
banking, building safety, corporate compliance, data protection, education, energy, financial
controls, FMCG, food safety, healthcare, HR, insurance, logistics, manufacturing, media,
pharma, public safety, public sector, real estate, retail, telecoms, travel, water utility,
and more — across UK, EU, and US regulatory frameworks.

17 minimal starter templates are in `examples/starter_contracts/`.

See [docs/community_use_cases.md](community_use_cases.md) for real-world examples by industry.

---

## Full Contract List

| Contract | Description | Contexts | Highlights |
|----------|-------------|----------|-----------|
| `customer` | General customer validation (email, age, name, phone, etc.) | `kids_app`, `financial` | — |
| `sf_contact` | Salesforce Contact — 18 validation criteria, production-grade | `salesforce_prod`, `salesforce_sandbox`, `emea_region` | Sentinel date rejection |
| `sf_lead` | Salesforce Lead — 16 validation criteria with lead-specific checks | `web_form`, `trade_show`, `partner_referral` | — |
| `proof_of_play` | **Reference contract: OOH advertising impression validation** | `billing`, `operations` | Cross-field rules, conditional constraints, context-aware billing thresholds |
| `social_media_age_compliance` | UK Online Safety Act / Ofcom age assurance — 13+ age gate, DOB consistency, identity verification audit trail | — | `age_match` rule, identity verification lookup, verification timestamp |
| `ppds_menu_item` | Natasha's Law (PPDS) allergen compliance — all 14 major allergens must be explicitly declared before a QSR menu item is saved or labelled | — | 14 mandatory boolean fields, `required_if` for gluten/tree-nut type, sulphite threshold, audit trail |
| `martyns_law_venue` | Martyn's Law (Terrorism (Protection of Premises) Act 2025) — venue terrorism preparedness compliance, two-tier (standard/enhanced), mandatory SRP and SIA registration for 800+ capacity venues | — | Two-tier `required_if` enforcement, capacity minimum, enhanced-duty field gate, audit trail |
| `martyns_law_event` | Martyn's Law — qualifying events (temporary/one-off, 200+ expected attendance). Organiser-centric; SIA notification not registration; staff briefing not training; time-bounded with start/end dates | — | Distinct from venue contract: `sia_notification_reference` not `sia_registration_number`; event dates required |
| `pretix_event` | Martyn's Law — [Pretix](https://pretix.eu) event ticketing platform integration. Enforces expected_attendance, duty tier, evacuation/invacuation/lockdown procedures, staff briefing, and compliance audit trail at the point of write | — | Pretix-specific: `expected_attendance` field; `pre_save` signal via LocalValidator; see [docs/integrations/pretix.md](integrations/pretix.md) |
| `building_safety_golden_thread` | Building Safety Act 2022 — Golden Thread compliance for higher-risk buildings (18m+ / 7+ storeys). Enforces accountable person, BSR registration, safety case, and golden thread audit trail at point of write | — | Named accountable person + BSM mandatory, BSR registration gate, `required_if safety_case_documented = true` |
| `companies_house_filing` | Economic Crime and Corporate Transparency Act 2023 — identity verification for Companies House director and PSC filings. A missing verification field blocks the record before submission | — | `required_if id_verification_completed = true` gates method, date, and verifier; role and method lookups |
| `gdpr_processing_record` | UK GDPR Article 30 — Record of Processing Activities (ROPA). Enforces lawful basis declaration, consent-specific fields, legitimate interests assessment, special category data basis, and international transfer safeguard at the point of write | — | All 6 Article 6 lawful bases via lookup; consent/LIA/special-category/transfer fields via `required_if`; DPO audit trail |
| `gdpr_dsar_request` | UK GDPR Article 15 — Data Subject Access Request handling. Enforces 30-day deadline recording, identity verification gate, extension logic, and outcome tracking before a request enters the case management workflow | — | 30-day deadline field required at intake; `required_if` for verification method, extension reason, and refusal reason |
| `eu_gdpr_processing_record` | EU GDPR Article 30 — Record of Processing Activities (ROPA). EU variant with EU Standard Contractual Clauses, 27-DPA supervisory authority lookup, and EU adequacy decision list | — | EU transfer safeguards and supervisory authority lookup; otherwise identical pattern to UK GDPR |
| `eu_gdpr_dsar_request` | EU GDPR Article 15 — Data Subject Access Request handling. EU variant with €20M / 4% turnover penalty references and EU supervisory authority | — | Same enforcement pattern as UK GDPR DSAR; fines referenced in EUR |
| `dora_ict_incident` | EU DORA (Digital Operational Resilience Act) Articles 17-19 — ICT incident report for financial entities. In force 17 January 2025. Enforces incident classification, statutory reporting timelines (24h / 72h / 30 days), and root cause documentation | — | `date_diff` enforces 24h early warning and 72h notification windows; `required_if` for root_cause when major/significant |
| `hipaa_disclosure_accounting` | US HIPAA 45 CFR 164.528 — Accounting of Disclosures. Enforces complete disclosure records before they enter covered entity systems. OCR penalties up to $2.1M/year | — | `required_if` for authorization_reference when patient_authorization; minimum_necessary_applied boolean gated on non-treatment purposes |
| `sox_control_test` | US Sarbanes-Oxley Act 2002, Sections 302/404 — Internal control test record. CEO/CFO personal certification liability. Enforces deficiency classification and remediation plan completeness before control test records are saved | — | Three-level `required_if` cascade: test_result → deficiency_classification → remediation plan + audit committee escalation |
| `mifid_transaction_report` | MiFID II / MiFIR Article 26 — Transaction reporting for investment firms and trading venues. Enforces LEI, ISIN, and venue MIC format at point of write before submission to an Approved Reporting Mechanism | — | LEI regex (`^[A-Z0-9]{18}[0-9]{2}$`), ISIN regex, MIC regex; buyer/seller ID type lookups |

For the remaining 25+ contracts (agriculture, automotive, energy, HR, insurance, logistics,
manufacturing, media, pharma, real estate, telecoms, travel, water utility, and more) see
the [`contracts/`](../contracts/) directory directly.

---

## Regulatory Context

### UK Online Safety Act (Ofcom enforcement from January 2026)

The `social_media_age_compliance` contract demonstrates age assurance patterns required by the
UK Online Safety Act 2023: 13-year age gate, age/DOB consistency check (`age_match` rule),
identity verification method tracking, and verification timestamp audit trail.

### Natasha's Law (in force 1 October 2021)

The `ppds_menu_item` contract enforces explicit allergen declaration for Pre-Packed for Direct
Sale (PPDS) food at the point of write. All 14 major allergens are mandatory fields — omission
is structurally impossible and triggers a 422 before the record enters the system. The
`allereasy_dish` contract extends this for [AllerEasy](https://www.allereasy.co.uk/)
(open-source Django allergen management), adding a timestamped review audit trail enforced in
`Dish.clean()` via the `LocalValidator` SDK. See
[docs/integrations/natasha-law-compliance.md](integrations/natasha-law-compliance.md) and
[docs/integrations/allereasy.md](integrations/allereasy.md).

### Martyn's Law (Royal Assent 3 April 2025)

The `martyns_law_venue` and `martyns_law_event` contracts enforce terrorism preparedness
compliance for venues and events with a capacity of 200 or more. Enhanced-duty premises (800+)
must declare a named Senior Responsible Person, SIA registration/notification reference, and
Terrorism Protection Plan — omission triggers a 422 before the record enters the system. The
`pretix_event` contract extends this for [Pretix](https://pretix.eu) (open-source event
ticketing), adding a compliance audit trail enforced via a `pre_save` signal and the
`LocalValidator` SDK. Named after Martyn Hett (1987–2017), killed in the Manchester Arena
attack. See [docs/integrations/martyns-law-compliance.md](integrations/martyns-law-compliance.md)
and [docs/integrations/pretix.md](integrations/pretix.md).

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
record enters a case management system. The `date_diff` rule enforces DORA's statutory
reporting windows: 24-hour early warning and 72-hour initial notification from the moment of
becoming aware.

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
trading venues. LEI, ISIN, and venue MIC codes are format-validated at point of write before
submission to an Approved Reporting Mechanism. Applies across EU and UK markets.

---

## Reference Contract: `proof_of_play`

The `proof_of_play` contract is the recommended starting point for learning cross-field rules
and conditional constraints. It demonstrates:

- `compare` rule: `impression_end` must be strictly after `impression_start` (catches phantom billing from inverted timestamps)
- `required_if` rule: `refresh_rate_hz` required only when `panel_type == DIGITAL`
- `condition` block: revenue floor applied only to `CHARGE` records, not `CREDIT` notes
- Two contexts: `billing` (all warnings become errors) and `operations` (relaxed thresholds for dashboards)
