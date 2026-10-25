# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    retention_account_id = fields.Many2one(
        comodel_name='account.account',
        related='company_id.retention_account_id',
        readonly=False,
        string="Retention Receivable Account",
        domain="[('account_type', '=', 'asset_receivable')]",
        help="Account where customer retention deductions will be recorded.",
    )
    retention_default_percentage = fields.Float(
        related='company_id.default_retention_percentage',
        readonly=False,
        string="Default Retention Percentage",
        help="Default percentage applied on new customer invoices for retention.",
    )
