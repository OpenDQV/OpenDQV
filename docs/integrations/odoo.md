# Odoo Integration — GDPR Article 30 ROPA Enforcement

> **Odoo:** [odoo.com](https://www.odoo.com) — open-source (LGPL-3.0 Community Edition),
> Python/OWL-based ERP platform. The Community Edition is used by hundreds of thousands
> of SMBs worldwide for CRM, accounting, HR, and inventory.

---

## Why a standalone module, not an upstream PR

Odoo operates a dual-licence model (LGPL Community Edition + proprietary Enterprise
Edition). Their Contributor License Agreement grants Odoo S.A. an irrevocable,
sublicensable right to incorporate contributions into proprietary products — the same
pattern that led us to close the Pretix PR. The Odoo CLA is based on Apache ICLA v2.0
but without the non-profit constraints that make the Apache version safe to sign.

The standalone module approach keeps OpenDQV's IP under MIT licence. Odoo users
install it from the App Store or directly from the OpenDQV repository — no CLA
required for third-party Odoo apps.

---

## The gap this module closes

Odoo has no built-in mechanism to enforce Article 30 completeness at the point of
write. A ROPA entry can be saved with:

- No lawful basis declared
- No retention period recorded
- Consent basis selected but no consent mechanism, timestamp, or withdrawal route recorded
- Legitimate interests claimed but no LIA completed
- Special category data processed with no Article 9(2) basis
- International transfer flagged but no safeguard mechanism recorded

That is the structural gap UK GDPR Article 30 and ICO audit expectations require
controllers to close. OpenDQV closes it before the record reaches the database.

---

## What the module adds

A dedicated `gdpr.processing.record` model with all Article 30(1) fields, validated
at the point of write by OpenDQV's `gdpr_processing_record` contract.

### Fields

| Section | Fields |
|---------|--------|
| Identity | `name` (Record ID), `controller_name` |
| Processing | `processing_purpose`, `lawful_basis`, `data_categories`, `data_subjects`, `recipients`, `retention_period` |
| Consent | `consent_mechanism`, `consent_timestamp`, `withdrawal_mechanism` |
| LIA | `lia_completed`, `lia_date` |
| Special category | `special_category_data`, `special_category_basis`, `special_category_types` |
| International transfers | `international_transfer`, `transfer_safeguard` |
| DPO audit trail | `dpo_reviewed_by`, `dpo_review_date` |

### Validation enforcement

When `ENABLE_OPENDQV_VALIDATION=true`, saving any `gdpr.processing.record` calls
OpenDQV's `LocalValidator` against the `gdpr_processing_record` contract. Any missing
or invalid field raises an Odoo `ValidationError` before the record is committed.

### Fully opt-in

- Off by default — `ENABLE_OPENDQV_VALIDATION` not set
- Silent if `opendqv` is not installed
- Zero breaking changes to existing Odoo data

---

## Setup

### 1. Get the module

```bash
# From the OpenDQV repo
cp -r odoo_modules/odoo_opendqv_gdpr /path/to/odoo/addons/
```

### 2. Install opendqv

```bash
pip install opendqv>=1.4.0
```

### 3. Install in Odoo

**Settings → Apps → Search "OpenDQV GDPR"** → Install

### 4. Enable validation

```bash
export ENABLE_OPENDQV_VALIDATION=true
```

### 5. Point to the contract

```bash
export OPENDQV_CONTRACTS_DIR=/path/to/opendqv/contracts
```

Or copy `gdpr_processing_record.yaml` to `./contracts/` in your Odoo working directory.

---

## How it works

The `gdpr_processing_record` contract (`contracts/gdpr_processing_record.yaml`)
defines what Article 30 completeness means:

```yaml
rules:
  - name: lawful_basis_required
    field: lawful_basis
    type: not_empty
    error_message: "lawful_basis is required — Article 6(1) UK GDPR requires at
      least one lawful basis for every processing activity"

  - name: consent_mechanism_required_if_consent
    field: consent_mechanism
    type: required_if
    required_if:
      field: lawful_basis
      value: "consent"
    error_message: "consent_mechanism is required when lawful_basis is consent
      — Article 7(1) UK GDPR requires the controller to demonstrate how consent
      was obtained"
```

The `GdprProcessingRecord.create()` and `write()` methods serialise the Odoo fields
into a flat dict and pass it to `LocalValidator`:

```python
from opendqv.sdk.local import LocalValidator

validator = LocalValidator()
result = validator.validate(record, contract='gdpr_processing_record')
if not result['valid']:
    raise ValidationError(f"GDPR Article 30 compliance check failed: ...")
```

---

## OpenDQV vs gdpr_processing_record

| Use case | Integration pattern |
|----------|---------------------|
| Odoo ROPA module | `gdpr.processing.record` model, `LocalValidator` in `create()`/`write()` |
| Custom app / REST API | `POST /api/v1/validate` with `gdpr_processing_record` contract |
| Batch validation | DuckDB batch endpoint with `gdpr_processing_record` contract |

---

## Odoo App Store

The module is available at:
`odoo_modules/odoo_opendqv_gdpr/` in the OpenDQV repository.

App Store listing: *(pending submission)*

---

## Related resources

- Module: `odoo_modules/odoo_opendqv_gdpr/`
- Contract: `contracts/gdpr_processing_record.yaml`
- UK GDPR Article 30: [ico.org.uk — Documentation](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/)
- OpenDQV GDPR guide: [docs/integrations/gdpr-compliance.md](gdpr-compliance.md)
