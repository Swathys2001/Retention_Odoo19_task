# -*- coding: utf-8 -*-
{
    'name': 'Retention and Advance Recovery Management',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Customer advance payments, retention deductions, advance recovery, retention releases, credit notes, and project reporting',
    'description': """
Retention and Advance Recovery Management for Odoo 19 Enterprise
================================================================
Designed specifically for contracting and project-based companies (including Saudi Arabia VAT & accounting workflows).

Key Features:
-------------
* Company-specific Retention Receivable account configuration with default percentage.
* Automatic separation of Normal Accounts Receivable and Retention Receivable at invoice posting.
* Full VAT calculation on gross taxable work value, unaffected by deductions.
* Advance payment tracking with remaining balance and percentage/fixed recovery.
* Automated reconciliation of advance recovery against customer advances.
* Multi-invoice full or partial retention release wizard with balanced journal entries.
* Comprehensive credit note and refund handling for proportional retention and advance recovery reversal.
* Reposting, reset-to-draft safety, and duplicate prevention.
* Customer and Project Retention Report wizard with Excel (XLSX) export.
* QWeb customer invoice PDF report enhancement with deduction breakdown.
* Full multi-currency, multi-company, and accounting lock-date compliance.
    """,
    'author': 'Antigravity',
    'license': 'OPL-1',
    'depends': [
        'account',
        'sale',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'views/retention_release_views.xml',
        'wizard/retention_report_wizard_views.xml',
        'views/menu_items.xml',
        'report/invoice_report.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
