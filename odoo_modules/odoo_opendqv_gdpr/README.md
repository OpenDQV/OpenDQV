# OpenDQV GDPR Article 30 ROPA — Odoo Module

Adds a **Record of Processing Activities (ROPA)** model to Odoo, with every entry
enforced at the point of write by [OpenDQV](https://github.com/OpenDQV/OpenDQV)'s
`gdpr_processing_record` contract.

UK GDPR Article 30 requires every data controller to maintain a record of processing
activities. The failure mode this module closes: a ROPA entry saved with no lawful
basis, no retention period, or conditional fields silently blank.

---

## What this module adds

- A dedicated `gdpr.processing.record` model covering all Article 30(1) fields
- Write-time validation via OpenDQV's `LocalValidator` — missing or invalid fields
  raise a clear validation error before the record reaches the database
- Odoo chatter / audit trail on every ROPA entry (who changed what and when)
- Tree view with "Awaiting DPO Review" filter
- Form view with conditional field visibility (consent fields shown only when
  lawful basis is consent; LIA fields only when legitimate interests; etc.)

## Fully opt-in

- Validation is **off by default** — set `ENABLE_OPENDQV_VALIDATION=true` to enable
- `opendqv` not installed? The validation step skips silently
- Zero breaking changes to existing Odoo data

---

## Installation

### 1. Copy the module

```bash
cp -r odoo_opendqv_gdpr /path/to/your/odoo/addons/
```

### 2. Install opendqv

```bash
pip install opendqv>=1.4.0
```

### 3. Install the Odoo module

In Odoo: **Settings → Apps → Search "OpenDQV GDPR"** → Install

Or via CLI:

```bash
python odoo-bin -u odoo_opendqv_gdpr -d your_database
```

### 4. Enable validation

```bash
export ENABLE_OPENDQV_VALIDATION=true
```

### 5. Add the contract

`LocalValidator` looks for contracts in `./contracts/` relative to the working
directory (or `OPENDQV_CONTRACTS_DIR` env var). Copy or symlink the
`gdpr_processing_record.yaml` contract from the OpenDQV package:

```bash
mkdir -p contracts
cp $(python -c "import opendqv; import os; print(os.path.dirname(opendqv.__file__))") \
   /path/to/contracts/gdpr_processing_record.yaml
```

Or set the env var:

```bash
export OPENDQV_CONTRACTS_DIR=/path/to/opendqv/contracts
```

---

## Validation in action

Try saving a ROPA entry with `lawful_basis = consent` and no `consent_mechanism`.
The save is blocked with:

```
GDPR Article 30 compliance check failed:
  • consent_mechanism: consent_mechanism is required when lawful_basis is consent
    — Article 7(1) UK GDPR requires the controller to demonstrate how consent was obtained
  • withdrawal_mechanism: withdrawal_mechanism is required when lawful_basis is consent
    — Article 7(3) UK GDPR requires that withdrawal is as easy as giving consent
```

---

## Related

- OpenDQV: https://github.com/OpenDQV/OpenDQV
- Contract: `contracts/gdpr_processing_record.yaml` (ships with `pip install opendqv`)
- Integration guide: `docs/integrations/odoo.md`
- UK GDPR Article 30: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/
