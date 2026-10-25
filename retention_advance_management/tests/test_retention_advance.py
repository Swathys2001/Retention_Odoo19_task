# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import fields, Command
from odoo.tests import tagged
from odoo.exceptions import UserError, ValidationError
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestRetentionAdvance(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # 1. Setup Company Currency as SAR (Saudi Riyal)
        sar_currency = cls.env.ref('base.SAR', raise_if_not_found=False)
        if not sar_currency:
            sar_currency = cls.env['res.currency'].search([('name', '=', 'SAR')], limit=1)
        if sar_currency:
            sar_currency.active = True
        else:
            sar_currency = cls.env['res.currency'].create({'name': 'SAR', 'symbol': 'SR', 'rounding': 0.01})

        cls.sar_currency = sar_currency
        cls.company = cls.company_data['company']
        cls.company.currency_id = sar_currency

        # Foreign currency (USD)
        usd_currency = cls.env.ref('base.USD', raise_if_not_found=False)
        if usd_currency:
            usd_currency.active = True
        cls.usd_currency = usd_currency

        # 2. 15% VAT Tax (Saudi Arabia Localization context)
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'VAT 15%',
            'amount_type': 'percent',
            'amount': 15.0,
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
        })

        # 3. Retention Receivable Account
        cls.retention_account = cls.env['account.account'].create({
            'name': 'Retention Receivable Account',
            'code': '102999',
            'account_type': 'asset_receivable',
            'company_ids': [Command.link(cls.company.id)],
        })

        # 4. Company Configuration
        cls.company.retention_account_id = cls.retention_account
        cls.company.default_retention_percentage = 10.0

        # 5. Customer Partner
        cls.customer = cls.partner_a
        cls.customer.property_account_receivable_id = cls.company_data['default_account_receivable']

        # 6. Contracting Work Product
        cls.contract_service = cls.env['product.product'].create({
            'name': 'Contracting Construction Work',
            'type': 'service',
            'list_price': 100000.0,
            'taxes_id': [Command.set(cls.tax_15.ids)],
        })

        # Grant sales manager rights to test user
        sales_group = cls.env.ref('sales_team.group_sale_manager', raise_if_not_found=False)
        if sales_group:
            cls.env.user.write({'group_ids': [Command.link(sales_group.id)]})

        # 7. Sales Order for the project (1,000,000 SAR project)
        cls.sale_order = cls.env['sale.order'].sudo().create({
            'partner_id': cls.customer.id,
            'company_id': cls.company.id,
            'order_line': [
                (0, 0, {
                    'product_id': cls.contract_service.id,
                    'product_uom_qty': 10,
                    'price_unit': 100000.0,
                    'tax_ids': [Command.set(cls.tax_15.ids)],
                }),
            ],
        })

    def _create_advance_payment(self, amount, partner=None, company=None, currency=None):
        """Helper to create and post an advance payment received from customer."""
        partner = partner or self.customer
        company = company or self.company
        currency = currency or self.sar_currency

        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id,
            'amount': amount,
            'currency_id': currency.id,
            'company_id': company.id,
            'journal_id': self.company_data['default_journal_bank'].id,
            'is_advance_payment': True,
            'sale_order_id': self.sale_order.id,
            'date': fields.Date.context_today(self.env.user),
        })
        payment.action_post()
        return payment

    def _create_progress_invoice(self, amount=100000.0, retention_pct=10.0, advance_payment=None, advance_pct=20.0, advance_method='percentage', advance_amount=0.0):
        """Helper to create a monthly customer progress invoice."""
        vals = {
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'currency_id': self.sar_currency.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'sale_order_id': self.sale_order.id,
            'is_retention_applicable': True,
            'retention_percentage': retention_pct,
            'advance_payment_id': advance_payment.id if advance_payment else False,
            'advance_recovery_method': advance_method,
            'advance_recovery_percentage': advance_pct,
            'invoice_line_ids': [
                (0, 0, {
                    'product_id': self.contract_service.id,
                    'quantity': 1,
                    'price_unit': amount,
                    'tax_ids': [Command.set(self.tax_15.ids)],
                }),
            ],
        }
        if advance_method == 'fixed':
            vals['advance_recovery_amount'] = advance_amount
        move = self.env['account.move'].create(vals)
        return move

    # -------------------------------------------------------------------------
    # TESTS
    # -------------------------------------------------------------------------

    def test_01_module_installation(self):
        """1. Verify module is installed and default configurations are in place."""
        module = self.env['ir.module.module'].search([('name', '=', 'retention_advance_management')])
        self.assertEqual(module.state, 'installed', "retention_advance_management must be installed.")
        self.assertTrue(self.company.retention_account_id, "Company retention account should be set.")
        self.assertEqual(self.company.default_retention_percentage, 10.0)

    def test_02_retention_configuration(self):
        """2. Verify retention settings constraints and validation."""
        # Percentage must be <= 100
        with self.assertRaises(ValidationError):
            self.company.default_retention_percentage = 120.0
        with self.assertRaises(ValidationError):
            self.company.default_retention_percentage = -5.0

        # Retention account must be receivable
        expense_account = self.company_data['default_account_expense']
        with self.assertRaises(ValidationError):
            self.company.retention_account_id = expense_account

    def test_03_invoice_retention_calculation(self):
        """3. Retention must be calculated from untaxed work value (100,000 * 10% = 10,000)."""
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0)
        self.assertEqual(invoice.amount_untaxed, 100000.0)
        self.assertEqual(invoice.retention_amount, 10000.0)

    def test_04_vat_calculation_full_work_value(self):
        """4. VAT (15%) must be calculated on full taxable work value (100,000 * 15% = 15,000)."""
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0)
        self.assertEqual(invoice.amount_tax, 15000.0)
        self.assertEqual(invoice.amount_total, 115000.0)

    def test_05_advance_recovery_by_percentage(self):
        """5. Advance recovery calculated by percentage (100,000 * 20% = 20,000)."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        self.assertEqual(invoice.advance_recovery_amount, 20000.0)
        self.assertEqual(invoice.amount_payable_immediately, 85000.0)

    def test_06_advance_recovery_fixed_amount(self):
        """6. Advance recovery using fixed amount (e.g. 25,000 SAR)."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(
            amount=100000.0,
            retention_pct=10.0,
            advance_payment=advance,
            advance_method='fixed',
            advance_amount=25000.0,
        )
        self.assertEqual(invoice.advance_recovery_amount, 25000.0)
        # 115,000 - 10,000 - 25,000 = 80,000
        self.assertEqual(invoice.amount_payable_immediately, 80000.0)

    def test_07_prevention_of_over_recovery(self):
        """7. Prevent recovery exceeding the available advance balance."""
        advance = self._create_advance_payment(15000.0)
        invoice = self._create_progress_invoice(
            amount=100000.0,
            retention_pct=10.0,
            advance_payment=advance,
            advance_pct=20.0, # 20,000 recovery, but only 15,000 available
        )
        with self.assertRaises(ValidationError):
            invoice.action_post()

    def test_08_correct_invoice_journal_entries(self):
        """8. Verify journal entries upon posting: Debit AR 105k, Debit Retention 10k, Credit Rev 100k, Credit VAT 15k."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        self.assertEqual(invoice.state, 'posted')

        # Check line balances
        normal_ar = invoice.line_ids.filtered(lambda l: l.account_id == self.customer.property_account_receivable_id)
        retention_ar = invoice.line_ids.filtered(lambda l: l.account_id == self.retention_account)
        tax_line = invoice.line_ids.filtered(lambda l: l.tax_line_id == self.tax_15)

        self.assertEqual(len(normal_ar), 1, "Should have 1 normal receivable line.")
        self.assertEqual(len(retention_ar), 1, "Should have 1 retention receivable line.")
        self.assertEqual(normal_ar.debit, 105000.0, "Normal receivable debit must be 105,000 SAR.")
        self.assertEqual(retention_ar.debit, 10000.0, "Retention receivable debit must be 10,000 SAR.")
        self.assertEqual(tax_line.credit, 15000.0, "VAT payable credit must be 15,000 SAR.")

        # Ensure total move debits == credits
        total_debit = sum(invoice.line_ids.mapped('debit'))
        total_credit = sum(invoice.line_ids.mapped('credit'))
        self.assertEqual(total_debit, 115000.0)
        self.assertEqual(total_credit, 115000.0)

    def test_09_correct_normal_receivable_after_advance_reconciliation(self):
        """9. Verify normal receivable line residual is 85,000 SAR after 20,000 SAR advance recovery."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        normal_ar = invoice.line_ids.filtered(lambda l: l.account_id == self.customer.property_account_receivable_id)
        self.assertEqual(normal_ar.amount_residual, 85000.0, "Normal receivable residual must be 85,000 SAR.")
        self.assertEqual(invoice.amount_payable_immediately, 85000.0)

    def test_10_correct_remaining_advance_balance(self):
        """10. Verify remaining advance balance decreases from 200,000 to 180,000 SAR."""
        advance = self._create_advance_payment(200000.0)
        self.assertEqual(advance.advance_remaining_amount, 200000.0)

        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        advance.invalidate_recordset()
        self.assertEqual(advance.advance_remaining_amount, 180000.0, "Advance remaining balance must be 180,000 SAR.")
        self.assertEqual(advance.advance_recovered_amount, 20000.0, "Advance recovered amount must be 20,000 SAR.")

    def test_11_full_retention_release(self):
        """11. Full retention release of 10,000 SAR to normal receivables."""
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0)
        invoice.action_post()

        # Create full retention release
        release = self.env['retention.release'].create({
            'partner_id': self.customer.id,
            'sale_order_id': self.sale_order.id,
            'company_id': self.company.id,
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [
                (0, 0, {
                    'invoice_id': invoice.id,
                    'amount_to_release': 10000.0,
                }),
            ],
        })
        release.action_post()
        self.assertEqual(release.state, 'posted')
        self.assertEqual(release.amount, 10000.0)

        # Verify entry created: Debit normal AR 10k, Credit retention AR 10k
        move = release.move_id
        debit_line = move.line_ids.filtered(lambda l: l.account_id == self.customer.property_account_receivable_id)
        credit_line = move.line_ids.filtered(lambda l: l.account_id == self.retention_account)
        self.assertEqual(debit_line.debit, 10000.0)
        self.assertEqual(credit_line.credit, 10000.0)

        # Verify invoice retention balance is now 0
        invoice.invalidate_recordset()
        self.assertEqual(invoice.retention_balance_amount, 0.0)
        self.assertEqual(invoice.retention_released_amount, 10000.0)

    def test_12_partial_retention_release(self):
        """12. Partial retention release: release 6,000 of 10,000 SAR, remaining 4,000 SAR."""
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0)
        invoice.action_post()

        # Partial release of 6,000 SAR
        release = self.env['retention.release'].create({
            'partner_id': self.customer.id,
            'sale_order_id': self.sale_order.id,
            'company_id': self.company.id,
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [
                (0, 0, {
                    'invoice_id': invoice.id,
                    'amount_to_release': 6000.0,
                }),
            ],
        })
        release.action_post()

        invoice.invalidate_recordset()
        self.assertEqual(invoice.retention_released_amount, 6000.0)
        self.assertEqual(invoice.retention_balance_amount, 4000.0)

    def test_13_full_credit_note(self):
        """13. Full credit note reverses invoice, restores advance, and reverses retention."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        # Create full credit note
        credit_note = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.customer.id,
            'reversed_entry_id': invoice.id,
            'company_id': self.company.id,
            'currency_id': self.sar_currency.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'sale_order_id': self.sale_order.id,
            'is_retention_applicable': True,
            'retention_percentage': 10.0,
            'advance_payment_id': advance.id,
            'advance_recovery_percentage': 20.0,
            'invoice_line_ids': [
                (0, 0, {
                    'product_id': self.contract_service.id,
                    'quantity': 1,
                    'price_unit': 100000.0,
                    'tax_ids': [Command.set(self.tax_15.ids)],
                }),
            ],
        })
        credit_note.action_post()

        # Advance should be fully restored to 200,000 SAR
        advance.invalidate_recordset()
        self.assertEqual(advance.advance_remaining_amount, 200000.0)

    def test_14_partial_credit_note(self):
        """14. Partial credit note (50%): 5k retention reversed, 10k advance recovery restored."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        # 50% credit note (50,000 SAR work value)
        credit_note = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.customer.id,
            'reversed_entry_id': invoice.id,
            'company_id': self.company.id,
            'currency_id': self.sar_currency.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'sale_order_id': self.sale_order.id,
            'is_retention_applicable': True,
            'retention_percentage': 10.0,
            'advance_payment_id': advance.id,
            'advance_recovery_percentage': 20.0,
            'invoice_line_ids': [
                (0, 0, {
                    'product_id': self.contract_service.id,
                    'quantity': 1,
                    'price_unit': 50000.0,
                    'tax_ids': [Command.set(self.tax_15.ids)],
                }),
            ],
        })
        credit_note.action_post()

        # Advance balance should now be 190,000 SAR (180,000 + 10,000 restored)
        advance.invalidate_recordset()
        self.assertEqual(advance.advance_remaining_amount, 190000.0)

    def test_15_reposting_without_duplicate_transactions(self):
        """15. Resetting to draft and reposting does not duplicate transactions or corrupt balances."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        # Reset to draft
        invoice.button_draft()
        self.assertEqual(invoice.state, 'draft')

        # Advance balance is immediately restored
        advance.invalidate_recordset()
        self.assertEqual(advance.advance_remaining_amount, 200000.0)

        # Repost
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')

        # Check transactions count
        self.assertEqual(len(invoice.retention_transaction_ids), 1)
        self.assertEqual(len(invoice.advance_recovery_ids), 1)

        advance.invalidate_recordset()
        self.assertEqual(advance.advance_remaining_amount, 180000.0)

    def test_16_invoice_without_sales_order(self):
        """16. Invoices without sales order function correctly and group under customer."""
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'currency_id': self.sar_currency.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'is_retention_applicable': True,
            'retention_percentage': 10.0,
            'invoice_line_ids': [
                (0, 0, {
                    'name': 'Ad-hoc Maintenance Service',
                    'quantity': 1,
                    'price_unit': 50000.0,
                    'tax_ids': [Command.set(self.tax_15.ids)],
                }),
            ],
        })
        invoice.action_post()
        self.assertEqual(invoice.retention_amount, 5000.0)
        self.assertEqual(invoice.state, 'posted')

    def test_17_report_balances_as_of_selected_date(self):
        """17. Verify report wizard calculates retention and advance balances as of date accurately."""
        advance = self._create_advance_payment(200000.0)
        invoice = self._create_progress_invoice(amount=100000.0, retention_pct=10.0, advance_payment=advance, advance_pct=20.0)
        invoice.action_post()

        wizard = self.env['retention.report.wizard'].create({
            'as_of_date': fields.Date.context_today(self.env.user),
            'company_id': self.company.id,
        })
        rows = wizard._get_report_data()
        self.assertTrue(rows, "Report should return rows.")

        match = [r for r in rows if r['customer_name'] == self.customer.name and r['project_name'] == self.sale_order.name]
        self.assertTrue(match)
        data = match[0]
        self.assertEqual(data['total_invoiced'], 100000.0)
        self.assertEqual(data['retention_held'], 10000.0)
        self.assertEqual(data['retention_balance'], 10000.0)
        self.assertEqual(data['advance_received'], 200000.0)
        self.assertEqual(data['advance_recovered'], 20000.0)
        self.assertEqual(data['advance_balance'], 180000.0)

    def test_18_xlsx_export(self):
        """18. Verify XLSX export returns binary file and download url."""
        wizard = self.env['retention.report.wizard'].create({
            'as_of_date': fields.Date.context_today(self.env.user),
            'company_id': self.company.id,
        })
        res = wizard.action_export_xlsx()
        self.assertIn('url', res)
        self.assertTrue(wizard.excel_file)
        self.assertTrue(wizard.file_name.endswith('.xlsx'))

    def test_19_foreign_currency_invoice_and_advance(self):
        """19. Foreign currency (USD) invoice in SAR company."""
        if not self.usd_currency:
            return

        # Set rate: 1 USD = 3.75 SAR
        self.env['res.currency.rate'].create({
            'currency_id': self.usd_currency.id,
            'company_id': self.company.id,
            'rate': 1.0 / 3.75,
            'name': fields.Date.context_today(self.env.user),
        })

        advance_usd = self._create_advance_payment(20000.0, currency=self.usd_currency)
        self.assertEqual(advance_usd.advance_remaining_amount, 20000.0)

        # Invoice in USD: 10,000 USD work value, 10% retention, 20% advance recovery
        invoice_usd = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'currency_id': self.usd_currency.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'sale_order_id': self.sale_order.id,
            'is_retention_applicable': True,
            'retention_percentage': 10.0,
            'advance_payment_id': advance_usd.id,
            'advance_recovery_percentage': 20.0,
            'invoice_line_ids': [
                (0, 0, {
                    'product_id': self.contract_service.id,
                    'quantity': 1,
                    'price_unit': 10000.0,
                    'tax_ids': [Command.set(self.tax_15.ids)],
                }),
            ],
        })
        invoice_usd.action_post()

        self.assertEqual(invoice_usd.retention_amount, 1000.0)
        self.assertEqual(invoice_usd.advance_recovery_amount, 2000.0)

        advance_usd.invalidate_recordset()
        self.assertEqual(advance_usd.advance_remaining_amount, 18000.0)

    def test_20_multi_company_separation(self):
        """20. Multi-company separation: cannot recover advances across companies."""
        company_b = self.env['res.company'].create({
            'name': 'Second Construction Co',
            'currency_id': self.sar_currency.id,
        })
        bank_journal_b = self.env['account.journal'].create({
            'name': 'Bank Co B',
            'code': 'BNKB',
            'type': 'bank',
            'company_id': company_b.id,
        })
        # Advance belonging to Company B
        advance_b = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'amount': 50000.0,
            'currency_id': self.sar_currency.id,
            'company_id': company_b.id,
            'journal_id': bank_journal_b.id,
            'is_advance_payment': True,
        })

        invoice_a = self._create_progress_invoice(amount=100000.0, retention_pct=10.0)
        # Attempting to assign advance_b to invoice_a in Company A must fail check_company or constraint
        with self.assertRaises(UserError):
            invoice_a.advance_payment_id = advance_b
            invoice_a._check_retention_and_advance_amounts()

    def test_21_lock_date_behavior(self):
        """21. Operations prior to accounting lock dates are blocked."""
        today = fields.Date.context_today(self.env.user)
        # Set fiscal lock date
        self.company.fiscalyear_lock_date = today

        # Attempt to release retention on or prior to lock date must fail
        release = self.env['retention.release'].create({
            'partner_id': self.customer.id,
            'sale_order_id': self.sale_order.id,
            'company_id': self.company.id,
            'date': today - timedelta(days=5),
            'journal_id': self.company_data['default_journal_misc'].id,
        })
        with self.assertRaises(UserError):
            release.action_post()
