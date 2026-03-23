{
    'name': 'OpenDQV GDPR Article 30 ROPA',
    'version': '17.0.1.0.0',
    'category': 'Privacy',
    'summary': 'GDPR Article 30 Record of Processing Activities with OpenDQV write-time validation',
    'description': """
OpenDQV GDPR Article 30 ROPA
=============================

Adds a dedicated Record of Processing Activities (ROPA) model to Odoo, enforced
at the point of write by OpenDQV's contract-driven validation engine.

Every ROPA entry must satisfy the Article 30 completeness requirements before it
can be saved — lawful basis, data categories, retention period, and conditional
fields for consent, legitimate interests, special category data, and international
transfers.

Validation is powered by OpenDQV (https://github.com/OpenDQV/OpenDQV) and the
``gdpr_processing_record`` contract. Enable by installing opendqv and setting
``ENABLE_OPENDQV_VALIDATION=true``.

Zero breaking changes. Fully opt-in. MIT licensed.
    """,
    'author': 'Sunny Sharma / OpenDQV',
    'website': 'https://github.com/OpenDQV/OpenDQV',
    'license': 'MIT',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/gdpr_processing_record_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
