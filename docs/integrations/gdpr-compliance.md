# GDPR / UK GDPR Data Protection Compliance

> **Last reviewed:** 2026-03-21.

The UK General Data Protection Regulation (UK GDPR) and Data Protection Act 2018 impose
two categories of point-of-write data obligations that OpenDQV enforces directly:

1. **Records of Processing Activities (ROPA)** — Article 30 requires every data controller
   to maintain a written record of each processing activity, including its lawful basis,
   data categories, recipients, and retention period. A ROPA entry without a lawful basis
   or without retention period is a compliance failure before any data is processed.

2. **Data Subject Access Requests (DSARs)** — Article 15 and the DPA 2018 require
   controllers to respond within 30 calendar days. A request that enters a workflow without
   a recorded receipt date, requester identity, or response deadline cannot be managed
   to the statutory deadline.

OpenDQV enforces both at the point of write — before the record enters your case management
system, CRM, or ROPA register — making omission structurally impossible rather than relying
on human review.

---

## Who is in scope

**UK GDPR applies to every organisation that:**

- Processes personal data of individuals in the UK, regardless of where the organisation
  is based
- Is established in the UK and processes personal data anywhere

There is no size threshold. A sole trader processing customer email addresses is in scope.
A multinational processing employee HR data is in scope.

**ICO enforcement:** The Information Commissioner's Office can issue fines of up to
£17.5 million or 4% of global annual turnover (whichever is higher) for serious breaches.

---

## Contract 1: `gdpr_processing_record`

**Enforces:** UK GDPR Article 30 — Record of Processing Activities (ROPA)

### What it validates

Every ROPA entry must declare:

| Field | Rule | Why |
|-------|------|-----|
| `record_id` | not_empty | Unique identifier for the processing activity |
| `controller_name` | not_empty | Identity of the data controller |
| `processing_purpose` | not_empty | Article 30(1)(b) — purpose must be documented |
| `lawful_basis` | not_empty + lookup | Article 6(1) — one of six bases must be declared |
| `data_categories` | not_empty | Article 30(1)(c) — categories of personal data |
| `data_subjects` | not_empty | Article 30(1)(c) — categories of data subjects |
| `recipients` | not_empty | Article 30(1)(d) — who data is shared with |
| `retention_period` | not_empty | Article 30(1)(f) — how long data is kept |
| `special_category_data` | not_empty + lookup | Boolean — does this involve Article 9 data? |
| `international_transfer` | not_empty + lookup | Boolean — is data transferred outside the UK? |

### The six lawful bases (Article 6)

| Value | Basis |
|-------|-------|
| `consent` | The data subject has given clear consent |
| `contract` | Processing is necessary for a contract with the data subject |
| `legal_obligation` | Processing is necessary to comply with the law |
| `vital_interests` | Processing is necessary to protect someone's life |
| `public_task` | Processing is necessary for a public interest task |
| `legitimate_interests` | Processing is necessary for legitimate interests |

### Consent-specific fields

When `lawful_basis = consent`, three additional fields are required:

```yaml
consent_mechanism:  # How consent was obtained (web form, verbal, etc.)
consent_timestamp:  # ISO 8601 datetime when consent was given
withdrawal_mechanism:  # How the data subject can withdraw consent
```

### Legitimate interests fields

When `lawful_basis = legitimate_interests`, a Legitimate Interests Assessment (LIA) is
required before processing begins:

```yaml
lia_completed:  # Boolean — has the LIA been completed?
lia_date:       # Date the LIA was completed (YYYY-MM-DD)
```

### Special category data (Article 9)

When `special_category_data = true`, the Article 9(2) basis must be declared:

```yaml
special_category_basis:  # One of 10 Article 9(2) conditions
special_category_types:  # Description of what special category data is processed
```

### International transfers

When `international_transfer = true`, the transfer safeguard must be declared:

```yaml
transfer_safeguard:  # adequacy_decision, standard_contractual_clauses, binding_corporate_rules, etc.
```

---

## Contract 2: `gdpr_dsar_request`

**Enforces:** UK GDPR Article 15 — Data Subject Access Request handling

### The 30-day rule

A controller must respond to a DSAR within **30 calendar days** of receipt. This can be
extended by up to two further months for complex or numerous requests, but the extension
must be communicated to the data subject within the original 30 days.

OpenDQV enforces that the response deadline is recorded at the point the request is
logged — before it enters your case management workflow.

### What it validates

| Field | Rule | Why |
|-------|------|-----|
| `request_id` | not_empty + unique | Unique identifier for audit trail |
| `requester_name` | not_empty | Identity of the data subject |
| `requester_email` | not_empty + regex | Contact for the response |
| `receipt_date` | not_empty + date_format | Starts the 30-day clock |
| `response_due_date` | not_empty + date_format | Must be recorded at intake |
| `request_channel` | not_empty + lookup | How the request was received |
| `request_type` | not_empty + lookup | What right is being exercised |
| `id_verification_completed` | not_empty + lookup | Boolean — identity must be verified |
| `status` | not_empty + lookup | open / in_progress / closed / withdrawn |

### Identity verification gate

When `id_verification_completed = true`, the method and date must be recorded:

```yaml
id_verification_date:    # Date identity was verified (YYYY-MM-DD)
id_verification_method:  # How identity was verified
```

### Extension handling

When `extension_applied = true`, the reason and new deadline must be recorded:

```yaml
extension_reason:    # Why the extension was necessary
extended_due_date:   # New response deadline (YYYY-MM-DD)
```

### Outcome tracking

When `status = closed`, an outcome must be recorded:

```yaml
outcome:  # fulfilled / refused / partially_fulfilled / withdrawn
```

When `outcome = refused`, the refusal reason must be recorded:

```yaml
refusal_reason:  # Article 12(5) permits refusal for manifestly unfounded/excessive requests
```

---

## Example: validating a ROPA entry

```bash
python -m opendqv.cli validate gdpr_processing_record '{
  "record_id": "ROPA-2026-001",
  "controller_name": "Acme Ltd",
  "processing_purpose": "Customer order fulfilment",
  "lawful_basis": "contract",
  "data_categories": "name, email, delivery_address",
  "data_subjects": "customers",
  "recipients": "courier_partners",
  "retention_period": "7 years post-transaction",
  "special_category_data": "false",
  "international_transfer": "false",
  "dpo_reviewed_by": "Jane Smith",
  "dpo_review_date": "2026-03-21"
}'
```

```bash
# A record missing lawful_basis is rejected before it enters the ROPA register:
python -m opendqv.cli validate gdpr_processing_record '{
  "record_id": "ROPA-2026-002",
  "controller_name": "Acme Ltd",
  "processing_purpose": "Marketing emails"
}'
# Result: FAIL — lawful_basis is required (Article 6(1) requires a documented lawful basis)
```

---

## Example: logging a DSAR

```bash
python -m opendqv.cli validate gdpr_dsar_request '{
  "request_id": "DSAR-2026-001",
  "requester_name": "John Doe",
  "requester_email": "john.doe@example.com",
  "receipt_date": "2026-03-21",
  "response_due_date": "2026-04-20",
  "request_channel": "email",
  "request_type": "subject_access",
  "id_verification_completed": "false",
  "status": "open",
  "assigned_to": "privacy@acme.com",
  "reviewed_by": "DPO",
  "review_date": "2026-03-21"
}'
```

---

## ICO guidance references

- [UK GDPR Article 30 — Records of processing activities](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/)
- [Right of access (Article 15)](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/right-of-access/)
- [Responding to a subject access request](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/right-of-access/what-should-we-consider-when-responding-to-a-request/)
