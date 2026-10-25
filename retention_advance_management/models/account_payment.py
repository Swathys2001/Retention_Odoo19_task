# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_advance_payment = fields.Boolean(
        string="Is Advance Payment",
        default=False,
        tracking=True,
        help="Check this if this payment represents an upfront customer project advance.",
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order / Project",
        check_company=True,
        tracking=True,
        help="Optional reference to the sales order or contract project associated with this advance.",
    )
    advance_remaining_amount = fields.Monetary(
        string="Available Advance Balance",
        compute='_compute_advance_balances',
        currency_field='currency_id',
        help="Current unreconciled advance balance available for recovery.",
    )
    advance_recovered_amount = fields.Monetary(
        string="Recovered Advance Amount",
        compute='_compute_advance_balances',
        currency_field='currency_id',
        help="Total amount recovered against customer progress invoices.",
    )
    advance_recovery_ids = fields.One2many(
        comodel_name='advance.recovery.transaction',
        inverse_name='payment_id',
        string="Advance Recoveries",
        readonly=True,
    )

    def _get_receivable_line(self):
        """Retrieve the credit receivable line on the payment's journal entry."""
        self.ensure_one()
        if not self.move_id:
            return self.env['account.move.line']
        return self.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
            and l.credit > 0
        )[:1]

    @api.depends('state', 'amount', 'currency_id', 'move_id.line_ids.amount_residual', 'move_id.line_ids.amount_residual_currency', 'advance_recovery_ids.state', 'advance_recovery_ids.amount')
    def _compute_advance_balances(self):
        for payment in self:
            if payment.state not in ('paid', 'in_process') or payment.payment_type != 'inbound':
                payment.advance_remaining_amount = 0.0
                payment.advance_recovered_amount = 0.0
                continue

            rec_line = payment._get_receivable_line()
            if rec_line:
                if rec_line.currency_id == payment.currency_id:
                    remaining = abs(rec_line.amount_residual_currency)
                else:
                    remaining = abs(rec_line.company_currency_id._convert(
                        abs(rec_line.amount_residual),
                        payment.currency_id,
                        payment.company_id,
                        rec_line.date or fields.Date.context_today(payment),
                    ))
                payment.advance_remaining_amount = remaining
                payment.advance_recovered_amount = max(0.0, payment.amount - remaining)
            else:
                payment.advance_remaining_amount = 0.0
                payment.advance_recovered_amount = 0.0

    def get_available_advance_balance(self, target_currency=None, conversion_date=None):
        """Returns the genuinely available advance balance in the target currency."""
        self.ensure_one()
        rec_line = self._get_receivable_line()
        if not rec_line or self.state not in ('paid', 'in_process'):
            return 0.0

        if rec_line.currency_id == self.company_id.currency_id:
            balance = abs(rec_line.amount_residual)
            source_curr = self.company_id.currency_id
        else:
            balance = abs(rec_line.amount_residual_currency)
            source_curr = rec_line.currency_id

        if not target_currency or target_currency == source_curr:
            return balance

        date = conversion_date or rec_line.date or fields.Date.context_today(self)
        return source_curr._convert(balance, target_currency, self.company_id, date)

    def action_post(self):
        for pay in self:
            if pay.is_advance_payment and not pay.outstanding_account_id:
                try:
                    pay.outstanding_account_id = pay._get_outstanding_account(pay.payment_type)
                except Exception:
                    pass
                if not pay.move_id and pay.outstanding_account_id:
                    pay._generate_journal_entry()
        res = super().action_post()
        for pay in self:
            if pay.is_advance_payment and pay.move_id and pay.move_id.state == 'draft':
                pay.move_id.action_post()
        return res
