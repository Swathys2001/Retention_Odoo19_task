# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import frozendict


class AccountMove(models.Model):
    _inherit = 'account.move'

    is_retention_applicable = fields.Boolean(
        string="Apply Retention / Advance",
        compute='_compute_is_retention_applicable',
        store=True,
        readonly=False,
        help="Check to apply retention deduction and/or advance recovery on this invoice.",
    )
    retention_percentage = fields.Float(
        string="Retention Percentage",
        digits=(5, 2),
        compute='_compute_default_retention_percentage',
        store=True,
        readonly=False,
        help="Percentage deducted as retention from taxable work value.",
    )
    retention_amount = fields.Monetary(
        string="Retention Amount",
        compute='_compute_retention_amount',
        store=True,
        readonly=False,
        currency_field='currency_id',
        help="Retention amount held in Retention Receivable account.",
    )
    expected_retention_handover_date = fields.Date(
        string="Expected Retention Handover Date",
        help="Expected maturity/release date for the retained receivable.",
    )

    advance_payment_id = fields.Many2one(
        comodel_name='account.payment',
        string="Selected Advance Payment",
        domain="[('partner_id', '=', commercial_partner_id), ('state', 'in', ('paid', 'in_process')), ('payment_type', '=', 'inbound'), ('company_id', '=', company_id)]",
        check_company=True,
        help="Customer advance payment from which recovery will be deducted.",
    )
    advance_recovery_method = fields.Selection(
        selection=[
            ('percentage', 'Percentage'),
            ('fixed', 'Fixed Amount'),
        ],
        string="Advance Recovery Method",
        default='percentage',
        required=True,
    )
    advance_recovery_percentage = fields.Float(
        string="Advance Recovery Percentage",
        digits=(5, 2),
        default=0.0,
        help="Percentage of work value deducted to recover the customer advance.",
    )
    advance_recovery_amount = fields.Monetary(
        string="Advance Recovery Amount",
        compute='_compute_advance_recovery_amount',
        store=True,
        readonly=False,
        currency_field='currency_id',
        help="Amount recovered from the selected advance payment.",
    )
    amount_payable_immediately = fields.Monetary(
        string="Amount Payable Immediately",
        compute='_compute_amount_payable_immediately',
        currency_field='currency_id',
        help="Net amount due for immediate payment after retention and advance recovery.",
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order / Project",
        compute='_compute_sale_order_id',
        store=True,
        readonly=False,
        check_company=True,
        help="Linked sales order or contracting project.",
    )

    retention_transaction_ids = fields.One2many(
        comodel_name='retention.transaction',
        inverse_name='move_id',
        string="Retention Transactions",
        readonly=True,
    )
    advance_recovery_ids = fields.One2many(
        comodel_name='advance.recovery.transaction',
        inverse_name='move_id',
        string="Advance Recoveries",
        readonly=True,
    )
    advance_partial_reconcile_id = fields.Many2one(
        comodel_name='account.partial.reconcile',
        string="Advance Reconciliation Record",
        copy=False,
        readonly=True,
    )
    retention_released_amount = fields.Monetary(
        string="Retention Released",
        compute='_compute_retention_balances',
        currency_field='currency_id',
        help="Total retention released to normal receivables for this invoice.",
    )
    retention_balance_amount = fields.Monetary(
        string="Retention Remaining Balance",
        compute='_compute_retention_balances',
        currency_field='currency_id',
        help="Remaining retention still withheld for this invoice.",
    )

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------

    @api.depends('move_type', 'company_id')
    def _compute_is_retention_applicable(self):
        for move in self:
            if move.is_sale_document(include_receipts=True) and move.company_id.default_retention_percentage > 0:
                move.is_retention_applicable = True
            elif not move.is_retention_applicable:
                move.is_retention_applicable = False

    @api.depends('company_id', 'is_retention_applicable')
    def _compute_default_retention_percentage(self):
        for move in self:
            if move.is_sale_document(include_receipts=True) and move.is_retention_applicable:
                if not move.retention_percentage:
                    move.retention_percentage = move.company_id.default_retention_percentage
            elif not move.is_retention_applicable:
                move.retention_percentage = 0.0

    @api.depends('invoice_line_ids.sale_line_ids.order_id')
    def _compute_sale_order_id(self):
        for move in self:
            orders = move.invoice_line_ids.sale_line_ids.order_id
            if orders and not move.sale_order_id:
                move.sale_order_id = orders[0]

    @api.depends(
        'retention_percentage',
        'amount_untaxed',
        'currency_id',
        'is_retention_applicable',
        'invoice_line_ids.price_subtotal',
        'invoice_line_ids.price_unit',
        'invoice_line_ids.quantity'
    )
    def _compute_retention_amount(self):
        for move in self:
            if move.is_sale_document(include_receipts=True) and move.is_retention_applicable and move.retention_percentage > 0:
                base_amount = move.amount_untaxed or sum(move.invoice_line_ids.mapped('price_subtotal'))
                amount = move.currency_id.round(base_amount * (move.retention_percentage / 100.0))
                move.retention_amount = amount
            elif not move.is_retention_applicable or move.retention_percentage <= 0:
                move.retention_amount = 0.0

    @api.depends(
        'advance_payment_id',
        'advance_recovery_method',
        'advance_recovery_percentage',
        'amount_untaxed',
        'currency_id',
        'invoice_line_ids.price_subtotal',
        'invoice_line_ids.price_unit',
        'invoice_line_ids.quantity'
    )
    def _compute_advance_recovery_amount(self):
        for move in self:
            if move.is_sale_document(include_receipts=True) and move.advance_payment_id:
                if move.advance_recovery_method == 'percentage' and move.advance_recovery_percentage > 0:
                    base_amount = move.amount_untaxed or sum(move.invoice_line_ids.mapped('price_subtotal'))
                    amount = move.currency_id.round(base_amount * (move.advance_recovery_percentage / 100.0))
                    move.advance_recovery_amount = amount
                elif move.advance_recovery_method == 'percentage' and move.advance_recovery_percentage <= 0:
                    move.advance_recovery_amount = 0.0
            else:
                move.advance_recovery_amount = 0.0

    @api.depends(
        'amount_total',
        'amount_untaxed',
        'retention_amount',
        'advance_recovery_amount',
        'invoice_line_ids.price_subtotal',
        'invoice_line_ids.price_total'
    )
    def _compute_amount_payable_immediately(self):
        for move in self:
            total = move.amount_total or sum(move.invoice_line_ids.mapped('price_total'))
            payable = total - move.retention_amount - move.advance_recovery_amount
            move.amount_payable_immediately = max(0.0, payable)

    @api.depends('retention_amount', 'retention_transaction_ids.state', 'retention_transaction_ids.amount')
    def _compute_retention_balances(self):
        for move in self:
            origin_id = move._origin.id or (move.id if isinstance(move.id, int) else False)
            if not origin_id:
                move.retention_released_amount = 0.0
                move.retention_balance_amount = move.retention_amount
                continue
            release_lines = self.env['retention.release.line'].search([
                ('invoice_id', '=', origin_id),
                ('release_id.state', '=', 'posted'),
            ])
            released = sum(release_lines.mapped('amount_to_release'))
            move.retention_released_amount = released
            move.retention_balance_amount = max(0.0, move.retention_amount - released)

    # -------------------------------------------------------------------------
    # CONSTRAINTS
    # -------------------------------------------------------------------------

    @api.constrains('retention_percentage', 'advance_recovery_percentage', 'retention_amount', 'advance_recovery_amount', 'state')
    def _check_retention_and_advance_amounts(self):
        for move in self:
            if move.retention_percentage < 0.0 or move.retention_percentage > 100.0:
                raise ValidationError(_("Retention percentage must be between 0%% and 100%%."))
            if move.advance_recovery_percentage < 0.0 or move.advance_recovery_percentage > 100.0:
                raise ValidationError(_("Advance recovery percentage must be between 0%% and 100%%."))
            if move.retention_amount < 0.0:
                raise ValidationError(_("Retention amount cannot be negative."))
            if move.advance_recovery_amount < 0.0:
                raise ValidationError(_("Advance recovery amount cannot be negative."))

            if move.retention_amount > 0 and not move.company_id.retention_account_id:
                raise ValidationError(_("Please configure a Retention Receivable Account in Settings for company %s.", move.company_id.display_name))

            if move.advance_payment_id:
                if move.advance_payment_id.company_id != move.company_id:
                    raise ValidationError(_("The advance payment must belong to the same company as the invoice."))
                if move.advance_payment_id.partner_id.commercial_partner_id != move.commercial_partner_id:
                    raise ValidationError(_("The advance payment must belong to the invoice customer."))

                # Check available advance balance when posting
                if move.state == 'posted' and move.advance_recovery_amount > 0:
                    avail = move.advance_payment_id.get_available_advance_balance(move.currency_id, move.invoice_date or move.date)
                    # If this move is already reconciled with the advance, include its own reconciled amount
                    if move.advance_partial_reconcile_id:
                        avail += move.advance_recovery_amount
                    if move.currency_id.compare_amounts(move.advance_recovery_amount, avail) > 0:
                        raise ValidationError(_(
                            "Cannot recover %(recovery)s from advance %(payment)s: only %(avail)s is genuinely available.",
                            recovery=move.currency_id.format(move.advance_recovery_amount),
                            payment=move.advance_payment_id.name,
                            avail=move.currency_id.format(avail),
                        ))

    # -------------------------------------------------------------------------
    # DYNAMIC LINES / TERMS SYNCHRONIZATION
    # -------------------------------------------------------------------------

    def _compute_needed_terms(self):
        super()._compute_needed_terms()
        for invoice in self:
            if not invoice.is_sale_document(include_receipts=True) or not invoice.invoice_line_ids:
                continue
            retention = invoice.retention_amount
            if invoice.currency_id.is_zero(retention):
                continue

            retention_account = invoice.company_id.retention_account_id
            if not retention_account:
                continue

            sign = 1 if invoice.is_inbound(include_receipts=True) else -1
            retention_curr = invoice.currency_id.round(retention * sign)
            retention_comp = invoice.company_currency_id.round(
                invoice.currency_id._convert(
                    retention,
                    invoice.company_currency_id,
                    invoice.company_id,
                    invoice.invoice_date or invoice.date or fields.Date.context_today(invoice),
                ) * sign
            )

            if invoice.needed_terms:
                total_needed_curr = sum(v['amount_currency'] for v in invoice.needed_terms.values())
                new_needed_terms = {}

                # Adjust normal term lines: reduce them by retention
                for key, val in invoice.needed_terms.items():
                    new_key = frozendict({**key, 'is_retention': False})
                    val_copy = dict(val)
                    if total_needed_curr != 0:
                        ratio = val['amount_currency'] / total_needed_curr
                        deduct_curr = invoice.currency_id.round(retention_curr * ratio)
                        deduct_comp = invoice.company_currency_id.round(retention_comp * ratio)
                    else:
                        deduct_curr = 0.0
                        deduct_comp = 0.0

                    val_copy['amount_currency'] -= deduct_curr
                    val_copy['balance'] -= deduct_comp
                    val_copy['is_retention'] = False
                    new_needed_terms[new_key] = val_copy

                # Add retention term line
                ret_maturity = invoice.expected_retention_handover_date or invoice.invoice_date_due or invoice.invoice_date or fields.Date.context_today(invoice)
                ret_key = frozendict({
                    'move_id': invoice.id,
                    'date_maturity': fields.Date.to_date(ret_maturity),
                    'discount_date': False,
                    'is_retention': True,
                })
                new_needed_terms[ret_key] = {
                    'balance': retention_comp,
                    'amount_currency': retention_curr,
                    'account_id': retention_account.id,
                    'is_retention': True,
                    'name': _("Retention: %s", invoice.name or ''),
                }
                invoice.needed_terms = new_needed_terms
                invoice.needed_terms_dirty = True

    # -------------------------------------------------------------------------
    # POSTING & LIFECYCLE HOOKS
    # -------------------------------------------------------------------------

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted:
            move._process_retention_and_advance()
        return posted

    def _process_retention_and_advance(self):
        """Perform retention transaction tracking and advance reconciliation on posted invoice."""
        self.ensure_one()
        if not self.is_sale_document(include_receipts=True):
            return

        # 1. Retention Transaction Tracking
        if self.retention_amount > 0 and self.company_id.retention_account_id:
            ret_aml = self.line_ids.filtered(
                lambda l: l.is_retention_line or l.account_id == self.company_id.retention_account_id
            )
            existing_tx = self.env['retention.transaction'].search([
                ('move_id', '=', self.id),
                ('state', '=', 'posted'),
            ])
            if not existing_tx:
                tx_type = 'retention_reversal' if self.move_type == 'out_refund' else 'retention_held'
                comp_amount = self.currency_id._convert(
                    self.retention_amount,
                    self.company_currency_id,
                    self.company_id,
                    self.invoice_date or self.date,
                )
                self.env['retention.transaction'].create({
                    'move_id': self.id,
                    'move_line_id': ret_aml[:1].id if ret_aml else False,
                    'partner_id': self.commercial_partner_id.id,
                    'sale_order_id': self.sale_order_id.id,
                    'company_id': self.company_id.id,
                    'currency_id': self.currency_id.id,
                    'date': self.invoice_date or self.date,
                    'expected_release_date': self.expected_retention_handover_date,
                    'retention_type': tx_type,
                    'amount': self.retention_amount,
                    'amount_company_currency': comp_amount,
                    'state': 'posted',
                })

        # 2. Credit Note Reversals (Retention & Advance)
        if self.move_type == 'out_refund' and self.reversed_entry_id:
            # Reconcile retention reversal with original invoice retention line
            if self.retention_amount > 0 and self.reversed_entry_id.retention_amount > 0:
                cn_ret_aml = self.line_ids.filtered(
                    lambda l: (l.is_retention_line or l.account_id == self.company_id.retention_account_id) and not l.reconciled
                )
                orig_ret_aml = self.reversed_entry_id.line_ids.filtered(
                    lambda l: (l.is_retention_line or l.account_id == self.company_id.retention_account_id) and not l.reconciled
                )
                if cn_ret_aml and orig_ret_aml:
                    (cn_ret_aml + orig_ret_aml).reconcile()

            # Reverse advance recovery if applicable
            if self.advance_recovery_amount > 0 and self.reversed_entry_id.advance_partial_reconcile_id:
                orig_partial = self.reversed_entry_id.advance_partial_reconcile_id
                if orig_partial.exists():
                    reversed_recovery = min(self.advance_recovery_amount, orig_partial.debit_amount_currency)
                    if reversed_recovery > 0:
                        new_debit_curr = orig_partial.debit_amount_currency - reversed_recovery
                        new_credit_curr = orig_partial.credit_amount_currency - reversed_recovery
                        new_comp = orig_partial.company_currency_id.round(
                            self.currency_id._convert(
                                new_debit_curr,
                                orig_partial.company_currency_id,
                                self.company_id,
                                self.invoice_date or self.date,
                            )
                        )
                        if self.currency_id.is_zero(new_debit_curr):
                            debit_aml = orig_partial.debit_move_id
                            credit_aml = orig_partial.credit_move_id
                            orig_partial.unlink()
                            self.reversed_entry_id.advance_partial_reconcile_id = False
                            amls = debit_aml + credit_aml
                            amls._compute_amount_residual()
                            amls.flush_recordset(['amount_residual', 'amount_residual_currency'])
                        else:
                            orig_partial.write({
                                'amount': new_comp,
                                'debit_amount_currency': new_debit_curr,
                                'credit_amount_currency': new_credit_curr,
                            })
                            amls = orig_partial.debit_move_id + orig_partial.credit_move_id
                            amls._compute_amount_residual()
                            amls.flush_recordset(['amount_residual', 'amount_residual_currency'])

                        # Audit log for advance recovery reversal
                        comp_amount = self.currency_id._convert(
                            reversed_recovery,
                            self.company_currency_id,
                            self.company_id,
                            self.invoice_date or self.date,
                        )
                        self.env['advance.recovery.transaction'].create({
                            'move_id': self.id,
                            'payment_id': self.advance_payment_id.id if self.advance_payment_id else self.reversed_entry_id.advance_payment_id.id,
                            'partner_id': self.commercial_partner_id.id,
                            'sale_order_id': self.sale_order_id.id,
                            'company_id': self.company_id.id,
                            'currency_id': self.currency_id.id,
                            'date': self.invoice_date or self.date,
                            'recovery_type': 'reversal',
                            'amount': reversed_recovery,
                            'amount_company_currency': comp_amount,
                            'state': 'posted',
                        })

        # 3. Customer Invoice Advance Recovery Reconciliation
        elif self.move_type == 'out_invoice' and self.advance_payment_id and self.advance_recovery_amount > 0:
            if not self.advance_partial_reconcile_id:
                normal_aml = self.line_ids.filtered(
                    lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
                    and not l.is_retention_line
                    and l.account_id != self.company_id.retention_account_id
                    and not l.reconciled
                    and l.debit > 0
                )[:1]
                advance_aml = self.advance_payment_id._get_receivable_line()

                if normal_aml and advance_aml:
                    recovery_curr = self.advance_recovery_amount
                    comp_amount = self.currency_id._convert(
                        recovery_curr,
                        self.company_currency_id,
                        self.company_id,
                        self.invoice_date or self.date,
                    )
                    if advance_aml.currency_id == self.currency_id:
                        credit_curr = recovery_curr
                    else:
                        credit_curr = self.currency_id._convert(
                            recovery_curr,
                            advance_aml.currency_id,
                            self.company_id,
                            self.invoice_date or self.date,
                        )

                    partial = self.env['account.partial.reconcile'].create({
                        'debit_move_id': normal_aml.id,
                        'credit_move_id': advance_aml.id,
                        'amount': comp_amount,
                        'debit_amount_currency': recovery_curr,
                        'credit_amount_currency': credit_curr,
                    })
                    self.advance_partial_reconcile_id = partial.id

                    self.env['advance.recovery.transaction'].create({
                        'move_id': self.id,
                        'payment_id': self.advance_payment_id.id,
                        'partial_reconcile_id': partial.id,
                        'partner_id': self.commercial_partner_id.id,
                        'sale_order_id': self.sale_order_id.id,
                        'company_id': self.company_id.id,
                        'currency_id': self.currency_id.id,
                        'date': self.invoice_date or self.date,
                        'recovery_type': 'recovery',
                        'amount': recovery_curr,
                        'amount_company_currency': comp_amount,
                        'state': 'posted',
                    })

    def button_draft(self):
        """Prevent draft reset if retention released, and clean up advance reconciliations safely."""
        for move in self:
            if move.retention_released_amount > 0:
                raise UserError(_(
                    "Cannot reset invoice %(name)s to draft because %(released)s of retention has already been released.",
                    name=move.name,
                    released=move.currency_id.format(move.retention_released_amount),
                ))

            # Remove advance recovery partial reconciliation to restore payment credit
            if move.advance_partial_reconcile_id:
                move.advance_partial_reconcile_id.unlink()
                move.advance_partial_reconcile_id = False

            # Clean up audit transactions
            move.retention_transaction_ids.unlink()
            move.advance_recovery_ids.unlink()

        return super().button_draft()


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    is_retention = fields.Boolean(
        string="Is Retention Line",
        copy=False,
        default=False,
        help="Distinguishes retention receivable journal items from normal receivables.",
    )
    is_retention_line = fields.Boolean(
        string="Retention Line Flag",
        related='is_retention',
        store=True,
        readonly=False,
        help="Alias for is_retention for backward compatibility.",
    )

    @api.depends('date_maturity', 'is_retention')
    def _compute_term_key(self):
        super()._compute_term_key()
        for line in self:
            if line.display_type == 'payment_term':
                line.term_key = frozendict({
                    'move_id': line.move_id.id,
                    'date_maturity': fields.Date.to_date(line.date_maturity),
                    'discount_date': line.discount_date,
                    'is_retention': line.is_retention,
                })

    def _compute_account_id(self):
        super()._compute_account_id()
        for line in self:
            if line.is_retention and line.move_id.company_id.retention_account_id:
                line.account_id = line.move_id.company_id.retention_account_id.id
