# -*- coding: utf-8 -*-
import io
import base64
from odoo import models, fields, api, _
from odoo.exceptions import UserError

try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


class RetentionReportWizard(models.TransientModel):
    _name = 'retention.report.wizard'
    _description = "Retention and Advance Project Report Wizard"

    as_of_date = fields.Date(
        string="As-of Date",
        required=True,
        default=fields.Date.context_today,
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string="Customer Filter",
    )
    sale_order_id = fields.Many2one(
        comodel_name='sale.order',
        string="Sales Order / Project Filter",
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    excel_file = fields.Binary(string="Excel Report", readonly=True)
    file_name = fields.Char(string="File Name", readonly=True)

    def _get_report_data(self):
        """Calculate customer and project retention and advance summary as of selected date."""
        self.ensure_one()
        as_of = self.as_of_date
        company = self.company_id

        # Search posted customer invoices and credit notes up to as_of_date
        invoice_domain = [
            ('state', '=', 'posted'),
            ('company_id', '=', company.id),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('invoice_date', '<=', as_of),
        ]
        if self.partner_id:
            invoice_domain.append(('commercial_partner_id', '=', self.partner_id.commercial_partner_id.id))
        if self.sale_order_id:
            invoice_domain.append(('sale_order_id', '=', self.sale_order_id.id))

        moves = self.env['account.move'].search(invoice_domain)

        # Search eligible advances received up to as_of_date
        payment_domain = [
            ('state', 'in', ('paid', 'in_process')),
            ('payment_type', '=', 'inbound'),
            ('company_id', '=', company.id),
            ('date', '<=', as_of),
        ]
        if self.partner_id:
            payment_domain.append(('partner_id.commercial_partner_id', '=', self.partner_id.commercial_partner_id.id))
        if self.sale_order_id:
            payment_domain.append(('sale_order_id', '=', self.sale_order_id.id))

        payments = self.env['account.payment'].search(payment_domain)
        # Filter payments that are explicitly marked as advances or have advance recoveries
        advance_payments = payments.filtered(lambda p: p.is_advance_payment or p.advance_recovery_ids)

        # Search retention releases up to as_of_date
        release_domain = [
            ('state', '=', 'posted'),
            ('company_id', '=', company.id),
            ('date', '<=', as_of),
        ]
        if self.partner_id:
            release_domain.append(('partner_id.commercial_partner_id', '=', self.partner_id.commercial_partner_id.id))
        if self.sale_order_id:
            release_domain.append(('sale_order_id', '=', self.sale_order_id.id))

        releases = self.env['retention.release'].search(release_domain)

        # Group data by (partner, sale_order, currency)
        # Note: Invoices without a sales order are grouped by (partner, False, currency)
        groups = {}

        def get_group(partner, order, currency):
            key = (partner.id, order.id if order else False, currency.id)
            if key not in groups:
                groups[key] = {
                    'partner': partner,
                    'sale_order': order,
                    'currency': currency,
                    'total_invoiced_work': 0.0,
                    'retention_held': 0.0,
                    'retention_released': 0.0,
                    'advance_received': 0.0,
                    'advance_recovered': 0.0,
                }
            return groups[key]

        # Process invoices
        for move in moves:
            partner = move.commercial_partner_id
            order = move.sale_order_id
            currency = move.currency_id
            grp = get_group(partner, order, currency)

            # Work value excluding VAT (negative for credit notes)
            sign = 1.0 if move.move_type == 'out_invoice' else -1.0
            grp['total_invoiced_work'] += sign * move.amount_untaxed
            grp['retention_held'] += sign * move.retention_amount

            # Advance recoveries on invoice
            recoveries = move.advance_recovery_ids.filtered(lambda r: r.state == 'posted' and r.date <= as_of)
            for rec in recoveries:
                rec_sign = 1.0 if rec.recovery_type == 'recovery' else -1.0
                grp['advance_recovered'] += rec_sign * rec.amount

        # Process advance payments received
        for pay in advance_payments:
            partner = pay.partner_id.commercial_partner_id
            order = pay.sale_order_id
            currency = pay.currency_id
            grp = get_group(partner, order, currency)
            grp['advance_received'] += pay.amount

        # Process retention releases
        for rel in releases:
            partner = rel.partner_id.commercial_partner_id
            order = rel.sale_order_id
            currency = rel.currency_id
            grp = get_group(partner, order, currency)
            grp['retention_released'] += rel.amount

        # Calculate balances
        report_rows = []
        for grp in groups.values():
            retention_balance = max(0.0, grp['retention_held'] - grp['retention_released'])
            advance_balance = max(0.0, grp['advance_received'] - grp['advance_recovered'])
            report_rows.append({
                'customer_name': grp['partner'].name,
                'project_name': grp['sale_order'].name if grp['sale_order'] else _("No Project / Direct"),
                'total_invoiced': grp['total_invoiced_work'],
                'retention_held': grp['retention_held'],
                'retention_released': grp['retention_released'],
                'retention_balance': retention_balance,
                'advance_received': grp['advance_received'],
                'advance_recovered': grp['advance_recovered'],
                'advance_balance': advance_balance,
                'currency_name': grp['currency'].name,
            })

        # Sort by customer, then project
        report_rows.sort(key=lambda r: (r['customer_name'], r['project_name']))
        return report_rows

    def action_export_xlsx(self):
        """Generate and download Excel report."""
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(_("The xlsxwriter library is not installed in the Python environment."))

        rows = self._get_report_data()

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Retention & Advance')

        # Formats
        title_format = workbook.add_format({
            'bold': True,
            'font_size': 14,
            'font_color': '#1E3A8A',
            'align': 'left',
            'valign': 'vcenter',
        })
        subtitle_format = workbook.add_format({
            'italic': True,
            'font_size': 10,
            'font_color': '#4B5563',
        })
        header_format = workbook.add_format({
            'bold': True,
            'font_size': 10,
            'bg_color': '#1E3A8A',
            'font_color': '#FFFFFF',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter',
        })
        text_format = workbook.add_format({
            'border': 1,
            'valign': 'vcenter',
            'font_size': 9,
        })
        number_format = workbook.add_format({
            'border': 1,
            'num_format': '#,##0.00',
            'align': 'right',
            'valign': 'vcenter',
            'font_size': 9,
        })
        total_format = workbook.add_format({
            'bold': True,
            'border': 1,
            'bg_color': '#F3F4F6',
            'num_format': '#,##0.00',
            'align': 'right',
            'valign': 'vcenter',
            'font_size': 9,
        })
        total_label_format = workbook.add_format({
            'bold': True,
            'border': 1,
            'bg_color': '#F3F4F6',
            'align': 'left',
            'valign': 'vcenter',
            'font_size': 9,
        })

        # Header Titles
        worksheet.write('A1', f"Retention & Advance Recovery Report - {self.company_id.name}", title_format)
        worksheet.write('A2', f"As-of Date: {self.as_of_date} | Note: Invoiced values represent work completed excluding VAT.", subtitle_format)

        headers = [
            "Customer",
            "Sales Order / Project",
            "Total Invoiced (Work)",
            "Retention Held",
            "Retention Released",
            "Retention Balance",
            "Advance Received",
            "Advance Recovered",
            "Advance Balance",
            "Currency",
        ]

        worksheet.set_row(3, 26)
        for col_num, header in enumerate(headers):
            worksheet.write(3, col_num, header, header_format)

        # Data Rows
        row_idx = 4
        for row in rows:
            worksheet.write(row_idx, 0, row['customer_name'], text_format)
            worksheet.write(row_idx, 1, row['project_name'], text_format)
            worksheet.write(row_idx, 2, row['total_invoiced'], number_format)
            worksheet.write(row_idx, 3, row['retention_held'], number_format)
            worksheet.write(row_idx, 4, row['retention_released'], number_format)
            worksheet.write(row_idx, 5, row['retention_balance'], number_format)
            worksheet.write(row_idx, 6, row['advance_received'], number_format)
            worksheet.write(row_idx, 7, row['advance_recovered'], number_format)
            worksheet.write(row_idx, 8, row['advance_balance'], number_format)
            worksheet.write(row_idx, 9, row['currency_name'], text_format)
            row_idx += 1

        # Total Row
        if rows:
            tot_invoiced = sum(r['total_invoiced'] for r in rows)
            tot_ret_held = sum(r['retention_held'] for r in rows)
            tot_ret_released = sum(r['retention_released'] for r in rows)
            tot_ret_balance = sum(r['retention_balance'] for r in rows)
            tot_adv_received = sum(r['advance_received'] for r in rows)
            tot_adv_recovered = sum(r['advance_recovered'] for r in rows)
            tot_adv_balance = sum(r['advance_balance'] for r in rows)

            worksheet.write(row_idx, 0, "Total", total_label_format)
            worksheet.write(row_idx, 1, "", total_label_format)
            worksheet.write_formula(row_idx, 2, f"=SUM(C5:C{row_idx})", total_format, tot_invoiced)
            worksheet.write_formula(row_idx, 3, f"=SUM(D5:D{row_idx})", total_format, tot_ret_held)
            worksheet.write_formula(row_idx, 4, f"=SUM(E5:E{row_idx})", total_format, tot_ret_released)
            worksheet.write_formula(row_idx, 5, f"=SUM(F5:F{row_idx})", total_format, tot_ret_balance)
            worksheet.write_formula(row_idx, 6, f"=SUM(G5:G{row_idx})", total_format, tot_adv_received)
            worksheet.write_formula(row_idx, 7, f"=SUM(H5:H{row_idx})", total_format, tot_adv_recovered)
            worksheet.write_formula(row_idx, 8, f"=SUM(I5:I{row_idx})", total_format, tot_adv_balance)
            worksheet.write(row_idx, 9, "", total_label_format)

        # Set column widths
        worksheet.set_column(0, 0, 24)
        worksheet.set_column(1, 1, 26)
        worksheet.set_column(2, 8, 18)
        worksheet.set_column(9, 9, 10)

        workbook.close()
        output.seek(0)

        self.file_name = f"Retention_Advance_Report_{self.as_of_date}.xlsx"
        self.excel_file = base64.b64encode(output.read())

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/?model=retention.report.wizard&id={self.id}&field=excel_file&download=true&filename={self.file_name}',
            'target': 'self',
        }
