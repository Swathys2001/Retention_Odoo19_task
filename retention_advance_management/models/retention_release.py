# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class RetentionRelease(models.Model):
    _name = 'retention.release'
    _description = "Retention Release"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Release Number",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('retention.release') or _('New'),
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Customer",
        required=True,
        tracking=True,
        check_company=True,
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order / Project",
        tracking=True,
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
        default=lambda self: self.env.company.currency_id,
    )
    company_currency_id = fields.Many2one(
        comodel_name='res.currency',
        related='company_id.currency_id',
        string="Company Currency",
        readonly=True,
    )
    date = fields.Date(
        string="Release Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Journal",
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
        required=True,
        check_company=True,
        default=lambda self: self.env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', self.env.company.id),
        ], limit=1),
    )
    amount = fields.Monetary(
        string="Total Release Amount",
        compute='_compute_amount',
        store=True,
        readonly=False,
        currency_field='currency_id',
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('posted', 'Released'),
            ('cancel', 'Cancelled'),
        ],
        string="Status",
        default='draft',
        required=True,
        tracking=True,
        index=True,
    )
    move_id = fields.Many2one(
        comodel_name='account.move',
        string="Accounting Entry",
        readonly=True,
        copy=False,
        check_company=True,
    )
    line_ids = fields.One2many(
        comodel_name='retention.release.line',
        inverse_name='release_id',
        string="Allocations per Invoice",
        copy=True,
    )
    retention_transaction_ids = fields.One2many(
        comodel_name='retention.transaction',
        inverse_name='release_id',
        string="Retention Transactions",
        readonly=True,
    )

    @api.depends('line_ids.amount_to_release')
    def _compute_amount(self):
        for release in self:
            release.amount = sum(release.line_ids.mapped('amount_to_release'))

    @api.constrains('amount')
    def _check_amount(self):
        for release in self:
            if release.state == 'posted' and release.currency_id.is_zero(release.amount):
                raise ValidationError(_("Release amount must be strictly greater than zero."))

    def action_post(self):
        """Create and post the retention release accounting entry, transferring from retention to normal receivable."""
        for release in self:
            if release.state != 'draft':
                raise UserError(_("Only draft retention releases can be posted."))
            if not release.line_ids:
                raise UserError(_("Please add at least one invoice allocation line."))
            if release.currency_id.is_zero(release.amount):
                raise UserError(_("Total release amount cannot be zero."))

            # Lock date check
            violated = release.company_id._get_lock_date_violations(release.date)
            if violated:
                raise UserError(_(
                    "Cannot release retention on %(date)s: locked period prior to %(lock_info)s.",
                    date=release.date,
                    lock_info=release.company_id._format_lock_dates(violated),
                ))

            # Validate retention account and receivable account
            retention_account = release.company_id.retention_account_id
            if not retention_account:
                raise UserError(_("Retention Receivable Account is not configured in Settings for company %s.", release.company_id.display_name))

            normal_account = release.partner_id.property_account_receivable_id
            if not normal_account:
                raise UserError(_("Please define a receivable account for customer %s.", release.partner_id.display_name))

            # Validate each line does not exceed available retention
            for line in release.line_ids:
                if line.amount_to_release <= 0:
                    raise UserError(_("Release amount for invoice %s must be greater than zero.", line.invoice_id.name))
                if line.currency_id.compare_amounts(line.amount_to_release, line.retention_available) > 0:
                    raise UserError(_(
                        "Cannot release %(amount)s for invoice %(inv)s: only %(avail)s is available.",
                        amount=line.currency_id.format(line.amount_to_release),
                        inv=line.invoice_id.name,
                        avail=line.currency_id.format(line.retention_available),
                    ))

            # Compute company currency amounts
            amount_comp = release.company_currency_id.round(
                release.currency_id._convert(
                    release.amount,
                    release.company_currency_id,
                    release.company_id,
                    release.date,
                )
            )

            # Generate balanced journal entry:
            # Debit: Normal Accounts Receivable (customer is charged normally)
            # Credit: Retention Receivable (clearing the retained balance)
            move_vals = {
                'move_type': 'entry',
                'date': release.date,
                'journal_id': release.journal_id.id,
                'company_id': release.company_id.id,
                'ref': _("Retention Release: %s", release.name),
                'line_ids': [
                    (0, 0, {
                        'name': _("Retention Released (Normal Receivable): %s", release.name),
                        'partner_id': release.partner_id.id,
                        'account_id': normal_account.id,
                        'debit': amount_comp,
                        'credit': 0.0,
                        'currency_id': release.currency_id.id,
                        'amount_currency': release.amount,
                    }),
                    (0, 0, {
                        'name': _("Retention Released (Clearing): %s", release.name),
                        'partner_id': release.partner_id.id,
                        'account_id': retention_account.id,
                        'debit': 0.0,
                        'credit': amount_comp,
                        'currency_id': release.currency_id.id,
                        'amount_currency': -release.amount,
                    }),
                ],
            }
            move = self.env['account.move'].create(move_vals)
            move._post(soft=False)
            release.move_id = move.id

            # Reconcile the credit line with each invoice's Retention Receivable debit line
            rel_credit_aml = move.line_ids.filtered(
                lambda l: l.account_id == retention_account and l.credit > 0
            )[:1]

            for line in release.line_ids:
                inv_ret_aml = line.invoice_id.line_ids.filtered(
                    lambda l: (l.is_retention_line or l.account_id == retention_account) and not l.reconciled and l.debit > 0
                )[:1]
                if inv_ret_aml and rel_credit_aml:
                    line_comp = release.company_currency_id.round(
                        line.currency_id._convert(
                            line.amount_to_release,
                            release.company_currency_id,
                            release.company_id,
                            release.date,
                        )
                    )
                    self.env['account.partial.reconcile'].create({
                        'debit_move_id': inv_ret_aml.id,
                        'credit_move_id': rel_credit_aml.id,
                        'amount': line_comp,
                        'debit_amount_currency': line.amount_to_release,
                        'credit_amount_currency': line.amount_to_release,
                    })

                # Create audit transaction record
                self.env['retention.transaction'].create({
                    'move_id': line.invoice_id.id,
                    'release_id': release.id,
                    'partner_id': release.partner_id.id,
                    'sale_order_id': line.invoice_id.sale_order_id.id or release.sale_order_id.id,
                    'company_id': release.company_id.id,
                    'currency_id': line.currency_id.id,
                    'date': release.date,
                    'retention_type': 'retention_released',
                    'amount': line.amount_to_release,
                    'amount_company_currency': release.currency_id._convert(
                        line.amount_to_release,
                        release.company_currency_id,
                        release.company_id,
                        release.date,
                    ),
                    'state': 'posted',
                })

            release.state = 'posted'

    def action_cancel(self):
        """Cancel the retention release by unreconciling and reversing the journal entry."""
        for release in self:
            if release.state != 'posted':
                raise UserError(_("Only posted retention releases can be cancelled."))

            violated = release.company_id._get_lock_date_violations(release.date)
            if violated:
                raise UserError(_(
                    "Cannot cancel retention release: date %(date)s is in locked period.",
                    date=release.date,
                ))

            if release.move_id:
                normal_debit_aml = release.move_id.line_ids.filtered(
                    lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable') and l.debit > 0
                )
                if normal_debit_aml.matched_credit_ids:
                    raise UserError(_(
                        "Cannot cancel: the released receivable has already been paid or reconciled against payments. "
                        "Please unreconcile payments on customer %s first.",
                        release.partner_id.name,
                    ))

                # Unreconcile clearing lines
                release.move_id.line_ids.remove_move_reconcile()
                # Reverse the journal entry
                release.move_id._reverse_moves(cancel=True)

            release.retention_transaction_ids.write({'state': 'cancel'})
            release.state = 'cancel'


class RetentionReleaseLine(models.Model):
    _name = 'retention.release.line'
    _description = "Retention Release Allocation Line"

    release_id = fields.Many2one(
        comodel_name='retention.release',
        string="Release",
        ondelete='cascade',
        required=True,
    )
    invoice_id = fields.Many2one(
        comodel_name='account.move',
        string="Customer Invoice",
        required=True,
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]",
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        related='invoice_id.currency_id',
        string="Currency",
        readonly=True,
    )
    retention_held = fields.Monetary(
        string="Retention Held",
        compute='_compute_retention_info',
        store=True,
        currency_field='currency_id',
    )
    retention_already_released = fields.Monetary(
        string="Previously Released",
        compute='_compute_retention_info',
        store=True,
        currency_field='currency_id',
    )
    retention_available = fields.Monetary(
        string="Available Retention",
        compute='_compute_retention_info',
        store=True,
        currency_field='currency_id',
    )
    amount_to_release = fields.Monetary(
        string="Amount to Release",
        required=True,
        currency_field='currency_id',
    )

    @api.depends('invoice_id', 'invoice_id.retention_amount')
    def _compute_retention_info(self):
        for line in self:
            if not line.invoice_id:
                line.retention_held = 0.0
                line.retention_already_released = 0.0
                line.retention_available = 0.0
                continue

            inv = line.invoice_id._origin or line.invoice_id
            inv_id = inv.id if isinstance(inv.id, int) else (inv._origin.id or 0)
            held = inv.retention_amount
            # Search other posted releases for this invoice
            other_lines = self.env['retention.release.line'].search([
                ('invoice_id', '=', inv_id),
                ('release_id.state', '=', 'posted'),
                ('id', '!=', line._origin.id or 0),
            ])
            already_rel = sum(other_lines.mapped('amount_to_release'))
            avail = max(0.0, held - already_rel)

            line.retention_held = held
            line.retention_already_released = already_rel
            line.retention_available = avail
            if not line.amount_to_release:
                line.amount_to_release = avail
