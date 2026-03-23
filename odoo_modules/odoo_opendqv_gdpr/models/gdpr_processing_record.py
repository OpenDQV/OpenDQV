"""
GDPR Article 30 Record of Processing Activities (ROPA) model for Odoo.

Validated at point of write by OpenDQV's gdpr_processing_record contract.
Set ENABLE_OPENDQV_VALIDATION=true to enable enforcement.
opendqv not installed? Validation skips silently.

OpenDQV: https://github.com/OpenDQV/OpenDQV
"""
import os
import logging

from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

_LAWFUL_BASIS = [
    ('consent', 'Consent — Art. 6(1)(a)'),
    ('contract', 'Contract — Art. 6(1)(b)'),
    ('legal_obligation', 'Legal Obligation — Art. 6(1)(c)'),
    ('vital_interests', 'Vital Interests — Art. 6(1)(d)'),
    ('public_task', 'Public Task — Art. 6(1)(e)'),
    ('legitimate_interests', 'Legitimate Interests — Art. 6(1)(f)'),
]

_SPECIAL_CATEGORY_BASIS = [
    ('explicit_consent', 'Explicit Consent — Art. 9(2)(a)'),
    ('employment_law', 'Employment Law — Art. 9(2)(b)'),
    ('vital_interests', 'Vital Interests — Art. 9(2)(c)'),
    ('not_for_profit', 'Not-for-Profit Body — Art. 9(2)(d)'),
    ('made_public', 'Made Manifestly Public — Art. 9(2)(e)'),
    ('legal_claims', 'Legal Claims — Art. 9(2)(f)'),
    ('substantial_public_interest', 'Substantial Public Interest — Art. 9(2)(g)'),
    ('medical_diagnosis', 'Medical Diagnosis — Art. 9(2)(h)'),
    ('public_health', 'Public Health — Art. 9(2)(i)'),
    ('archiving_research', 'Archiving / Research — Art. 9(2)(j)'),
]

_TRANSFER_SAFEGUARD = [
    ('adequacy_decision', 'Adequacy Decision — Art. 45'),
    ('standard_contractual_clauses', 'Standard Contractual Clauses — Art. 46(2)(c)'),
    ('binding_corporate_rules', 'Binding Corporate Rules — Art. 47'),
    ('derogation', 'Derogation — Art. 49'),
    ('approved_code_of_conduct', 'Approved Code of Conduct — Art. 46(2)(e)'),
    ('approved_certification', 'Approved Certification — Art. 46(2)(f)'),
]


class GdprProcessingRecord(models.Model):
    _name = 'gdpr.processing.record'
    _description = 'GDPR Article 30 Record of Processing Activities'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'dpo_review_date desc, name'

    # ── RECORD IDENTITY ───────────────────────────────────────────────────────
    name = fields.Char(
        'Record ID', required=True, tracking=True,
        help='Unique identifier for this ROPA entry — Article 30(1) UK GDPR',
    )
    controller_name = fields.Char(
        'Controller Name', required=True, tracking=True,
        help='Name of the data controller — Article 30(1)(a) UK GDPR',
    )

    # ── PROCESSING PURPOSE AND LAWFUL BASIS ───────────────────────────────────
    processing_purpose = fields.Text(
        'Processing Purpose', required=True,
        help='Purpose of the processing activity — Article 30(1)(b) UK GDPR',
    )
    lawful_basis = fields.Selection(
        _LAWFUL_BASIS, 'Lawful Basis', required=True, tracking=True,
        help='Article 6(1) UK GDPR lawful basis for processing',
    )

    # ── DATA CATEGORIES AND SUBJECTS ──────────────────────────────────────────
    data_categories = fields.Text(
        'Data Categories', required=True,
        help='Categories of personal data processed — Article 30(1)(c) UK GDPR',
    )
    data_subjects = fields.Text(
        'Data Subjects', required=True,
        help='Categories of data subjects — Article 30(1)(c) UK GDPR',
    )
    recipients = fields.Text(
        'Recipients', required=True,
        help='Categories of recipients — Article 30(1)(d) UK GDPR. Use "none" if not shared.',
    )
    retention_period = fields.Char(
        'Retention Period', required=True,
        help='Envisaged time limits for erasure — Article 30(1)(f) UK GDPR',
    )

    # ── CONSENT-SPECIFIC FIELDS ───────────────────────────────────────────────
    consent_mechanism = fields.Char(
        'Consent Mechanism',
        help='How consent was obtained — required when lawful basis is consent (Article 7(1))',
    )
    consent_timestamp = fields.Date(
        'Consent Date',
        help='Date consent was given — required when lawful basis is consent',
    )
    withdrawal_mechanism = fields.Char(
        'Withdrawal Mechanism',
        help='How data subjects can withdraw consent — Article 7(3) UK GDPR',
    )

    # ── LEGITIMATE INTERESTS ASSESSMENT ──────────────────────────────────────
    lia_completed = fields.Boolean(
        'LIA Completed',
        help='Has a Legitimate Interests Assessment been completed? Required when lawful basis is legitimate_interests',
    )
    lia_date = fields.Date(
        'LIA Date',
        help='Date the Legitimate Interests Assessment was completed',
    )

    # ── SPECIAL CATEGORY DATA ─────────────────────────────────────────────────
    special_category_data = fields.Boolean(
        'Special Category Data?', tracking=True,
        help='Does this processing activity involve Article 9 special category data?',
    )
    special_category_basis = fields.Selection(
        _SPECIAL_CATEGORY_BASIS, 'Special Category Basis',
        help='Article 9(2) basis — required when special category data is processed',
    )
    special_category_types = fields.Char(
        'Special Category Types',
        help='Which special categories are processed (e.g. health, biometric, racial origin)',
    )

    # ── INTERNATIONAL TRANSFERS ───────────────────────────────────────────────
    international_transfer = fields.Boolean(
        'International Transfer?', tracking=True,
        help='Is personal data transferred to a third country? — Article 30(1)(e) UK GDPR',
    )
    transfer_safeguard = fields.Selection(
        _TRANSFER_SAFEGUARD, 'Transfer Safeguard',
        help='Safeguard mechanism for international transfers — Articles 44–49 UK GDPR',
    )

    # ── DPO AUDIT TRAIL ───────────────────────────────────────────────────────
    dpo_reviewed_by = fields.Char(
        'DPO Reviewed By', tracking=True,
        help='Name or role of the person who reviewed this ROPA entry for Article 30 compliance',
    )
    dpo_review_date = fields.Date(
        'DPO Review Date', tracking=True,
        help='Date the compliance review was completed',
    )

    # ── OPENDQV VALIDATION ────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._validate_opendqv(vals)
        return super().create(vals_list)

    def write(self, vals):
        for record in self:
            merged = {
                'name': record.name,
                'controller_name': record.controller_name,
                'processing_purpose': record.processing_purpose,
                'lawful_basis': record.lawful_basis,
                'data_categories': record.data_categories,
                'data_subjects': record.data_subjects,
                'recipients': record.recipients,
                'retention_period': record.retention_period,
                'consent_mechanism': record.consent_mechanism,
                'consent_timestamp': record.consent_timestamp.isoformat() if record.consent_timestamp else None,
                'withdrawal_mechanism': record.withdrawal_mechanism,
                'lia_completed': record.lia_completed,
                'lia_date': record.lia_date.isoformat() if record.lia_date else None,
                'special_category_data': record.special_category_data,
                'special_category_basis': record.special_category_basis,
                'special_category_types': record.special_category_types,
                'international_transfer': record.international_transfer,
                'transfer_safeguard': record.transfer_safeguard,
                'dpo_reviewed_by': record.dpo_reviewed_by,
                'dpo_review_date': record.dpo_review_date.isoformat() if record.dpo_review_date else None,
            }
            merged.update(vals)
            self._validate_opendqv(merged)
        return super().write(vals)

    def _validate_opendqv(self, vals):
        """Validate this ROPA entry against OpenDQV's gdpr_processing_record contract.

        Opt-in: set ENABLE_OPENDQV_VALIDATION=true in your environment.
        Silent if opendqv is not installed.
        """
        if not os.environ.get('ENABLE_OPENDQV_VALIDATION'):
            return

        try:
            from opendqv.sdk.local import LocalValidator
        except ImportError:
            _logger.debug('opendqv not installed — GDPR ROPA validation skipped')
            return

        def _bool_str(val):
            if val is None:
                return ''
            return 'true' if val else 'false'

        def _date_str(val):
            if not val:
                return ''
            return val.isoformat() if hasattr(val, 'isoformat') else str(val)

        record = {
            'record_id':            vals.get('name') or '',
            'controller_name':      vals.get('controller_name') or '',
            'processing_purpose':   vals.get('processing_purpose') or '',
            'lawful_basis':         vals.get('lawful_basis') or '',
            'data_categories':      vals.get('data_categories') or '',
            'data_subjects':        vals.get('data_subjects') or '',
            'recipients':           vals.get('recipients') or '',
            'retention_period':     vals.get('retention_period') or '',
            'consent_mechanism':    vals.get('consent_mechanism') or '',
            'consent_timestamp':    _date_str(vals.get('consent_timestamp')),
            'withdrawal_mechanism': vals.get('withdrawal_mechanism') or '',
            'lia_completed':        _bool_str(vals.get('lia_completed')),
            'lia_date':             _date_str(vals.get('lia_date')),
            'special_category_data': _bool_str(vals.get('special_category_data')),
            'special_category_basis': vals.get('special_category_basis') or '',
            'special_category_types': vals.get('special_category_types') or '',
            'international_transfer': _bool_str(vals.get('international_transfer')),
            'transfer_safeguard':   vals.get('transfer_safeguard') or '',
            'dpo_reviewed_by':      vals.get('dpo_reviewed_by') or '',
            'dpo_review_date':      _date_str(vals.get('dpo_review_date')),
        }

        validator = LocalValidator()
        result = validator.validate(record, contract='gdpr_processing_record')
        if not result['valid']:
            errors = '\n'.join(
                f"  • {e['field']}: {e['message']}"
                for e in result.get('errors', [])
            )
            raise ValidationError(
                f"GDPR Article 30 compliance check failed:\n{errors}\n\n"
                f"All required fields must be completed before this record can be saved."
            )
