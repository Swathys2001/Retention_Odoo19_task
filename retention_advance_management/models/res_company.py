# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    retention_account_id = fields.Many2one(
        comodel_name='account.account',
        string="Retention Receivable Account",
        domain="[('account_type', '=', 'asset_receivable')]",
        check_company=True,
        help="Receivable account specifically used to hold customer retention deductions until project release.",
    )
    default_retention_percentage = fields.Float(
        string="Default Retention Percentage",
        digits=(5, 2),
        default=0.0,
        help="Default percentage deducted as retention on new customer progress invoices.",
    )

    @api.constrains('default_retention_percentage')
    def _check_default_retention_percentage(self):
        for company in self:
            if company.default_retention_percentage < 0.0 or company.default_retention_percentage > 100.0:
                raise ValidationError(_("Default retention percentage must be between 0%% and 100%%."))

    @api.constrains('retention_account_id')
    def _check_retention_account_id(self):
        for company in self:
            if company.retention_account_id:
                if company.retention_account_id.account_type != 'asset_receivable':
                    raise ValidationError(_("Retention account must be of type 'Receivable'."))
                if company not in company.retention_account_id.company_ids and company.retention_account_id.company_id and company.retention_account_id.company_id != company:
                    raise ValidationError(_("Retention account must belong to company %s.", company.display_name))
