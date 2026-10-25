# -*- coding: utf-8 -*-
from odoo import models, fields, api


class RetentionTransaction(models.Model):
    _name = 'retention.transaction'
    _description = "Retention Transaction Audit Record"
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Reference", required=True, copy=False, default=lambda self: self.env['ir.sequence'].next_by_code('retention.transaction') or '/')
    move_id = fields.Many2one(
        comodel_name='account.move',
        string="Invoice / Entry",
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    move_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string="Retention Journal Item",
        ondelete='set null',
        check_company=True,
    )
    release_id = fields.Many2one(
        comodel_name='retention.release',
        string="Retention Release",
        ondelete='cascade',
        index=True,
        check_company=True,
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Customer",
        required=True,
        index=True,
        check_company=True,
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order / Project",
        index=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        string="Currency",
        required=True,
    )
    company_currency_id = fields.Many2one(
        comodel_name='res.currency',
        related='company_id.currency_id',
        string="Company Currency",
        readonly=True,
    )
    date = fields.Date(string="Date", required=True, index=True)
    expected_release_date = fields.Date(string="Expected Handover Date")
    retention_type = fields.Selection(
        selection=[
            ('retention_held', 'Retention Held'),
            ('retention_released', 'Retention Released'),
            ('retention_reversal', 'Retention Reversal'),
        ],
        string="Transaction Type",
        required=True,
        index=True,
    )
    amount = fields.Monetary(
        string="Amount in Currency",
        currency_field='currency_id',
        required=True,
    )
    amount_company_currency = fields.Monetary(
        string="Amount in Company Currency",
        currency_field='company_currency_id',
        required=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('posted', 'Posted'),
            ('cancel', 'Cancelled'),
        ],
        string="Status",
        default='posted',
        required=True,
        index=True,
    )
    notes = fields.Text(string="Notes")
