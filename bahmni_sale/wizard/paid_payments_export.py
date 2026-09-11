# -*- coding: utf-8 -*-
from __future__ import division

import base64
import logging
from datetime import date
from io import BytesIO

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT as DF

from odoo.addons.bahmni_sale.wizard.ethiopian_calendar import (
    ETH_MONTHS_SELECTION,
    eth_month_amharic,
    ethiopian_month_date_range,
    format_ethiopian_date,
    gregorian_to_ethiopian,
)

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


class PaidPaymentsExport(models.TransientModel):
    _name = 'bahmni.paid.payments.export'
    _description = 'Paid Payments Excel Export (Cash / Credit / Free)'

    period_type = fields.Selection([
        ('ethiopian', 'Ethiopian Month'),
        ('range', 'Date Range'),
    ], string="Period", required=True, default='ethiopian')
    eth_month = fields.Selection(
        ETH_MONTHS_SELECTION,
        string="Ethiopian Month",
        default='11',
    )
    eth_year = fields.Integer(
        string="Ethiopian Year",
        default=lambda self: (gregorian_to_ethiopian(date.today()) or (2018, 1, 1))[0],
    )
    date_from = fields.Date(
        string="From",
        default=lambda self: (date.today().replace(day=1)).strftime(DF),
    )
    date_to = fields.Date(
        string="To",
        default=lambda self: date.today().strftime(DF),
    )
    date_basis = fields.Selection([
        ('invoice', 'Invoice Date'),
        ('payment', 'Payment Date'),
    ], string="Date Based On", required=True, default='payment',
       help="Invoice Date covers Free/Credit paid bills. "
            "Payment Date focuses on posted cash/bank receipts.")
    company_id = fields.Many2one(
        'res.company',
        string="Facility",
        required=True,
        default=lambda self: self.env.user.company_id,
    )
    payment_method = fields.Selection([
        ('all', 'All'),
        ('Cash', 'Cash'),
        ('Credit', 'Credit'),
        ('Free', 'Free'),
    ], string="Payment Method", required=True, default='all')
    credit_information = fields.Selection([
        ('all', 'All'),
        ('CBHI', 'CBHI'),
        ('Insurance', 'Insurance'),
        ('Credit Companies', 'Credit Companies'),
    ], string="Credit Type", default='all')
    shop_id = fields.Many2one('sale.shop', string="Shop")
    journal_id = fields.Many2one(
        'account.journal',
        string="Payment Journal",
        domain="[('type', 'in', ('cash', 'bank'))]",
    )
    data = fields.Binary(string="Excel file", readonly=True)
    data_fname = fields.Char(string="File Name", readonly=True)
    state = fields.Selection(
        [('choose', 'choose'), ('get', 'get')],
        default='choose',
    )
    row_count = fields.Integer(string="Rows included", readonly=True)
    total_amount = fields.Float(string="Total amount", readonly=True)

    @api.onchange('payment_method')
    def _onchange_payment_method(self):
        if self.payment_method != 'Credit':
            self.credit_information = 'all'

    @api.multi
    def action_generate_excel(self):
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(_(
                "Python package 'xlsxwriter' is required to generate the payments Excel."
            ))
        date_start, date_end = self._resolve_date_range()
        rows = self._collect_rows(date_start, date_end)
        if not rows:
            raise UserError(_(
                "No paid payments found for the selected filters "
                "(Gregorian %s \u2192 %s)."
            ) % (date_start.strftime(DF), date_end.strftime(DF)))

        xlsx_bytes = self._build_workbook(rows, date_start, date_end)
        method_label = self.payment_method if self.payment_method != 'all' else 'All'
        facility = (self.company_id.name or 'Facility').replace(' ', '_')
        fname = 'Paid_Payments_%s_%s_%s_%s.xlsx' % (
            method_label.replace(' ', ''),
            date_start.strftime('%Y%m%d'),
            date_end.strftime('%Y%m%d'),
            facility,
        )
        total = sum(r['amount'] for r in rows)
        self.write({
            'data': base64.b64encode(xlsx_bytes),
            'data_fname': fname,
            'state': 'get',
            'row_count': len(rows),
            'total_amount': total,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bahmni.paid.payments.export',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

    def _resolve_date_range(self):
        self.ensure_one()
        if self.period_type == 'ethiopian':
            if not self.eth_month or not self.eth_year:
                raise UserError(_("Please select Ethiopian month and year."))
            if self.eth_year < 1900 or self.eth_year > 2200:
                raise UserError(_("Please enter a valid Ethiopian year."))
            return ethiopian_month_date_range(self.eth_year, int(self.eth_month))

        if not self.date_from or not self.date_to:
            raise UserError(_("Please set both From and To dates."))
        date_start = fields.Date.from_string(self.date_from)
        date_end = fields.Date.from_string(self.date_to)
        if date_start > date_end:
            raise UserError(_("From date must be on or before To date."))
        return date_start, date_end

    @api.model
    def _parse_date(self, value):
        if not value:
            return False
        if isinstance(value, date):
            return value
        try:
            string_types = basestring  # noqa: F821
        except NameError:
            string_types = str
        if isinstance(value, string_types):
            parts = value[:10].split('-')
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        return value

    def _payment_method_domain(self):
        if self.credit_information != 'all':
            return [
                ('payment_method', '=', 'Credit'),
                ('credit_information', '=', self.credit_information),
            ]
        if self.payment_method == 'all':
            return []
        return [('payment_method', '=', self.payment_method)]

    def _shop_domain(self):
        if not self.shop_id:
            return []
        return [('shop_id', '=', self.shop_id.id)]

    def _collect_rows(self, date_start, date_end):
        if self.date_basis == 'payment':
            return self._rows_from_payments(date_start, date_end)
        return self._rows_from_paid_invoices(date_start, date_end)

    def _rows_from_payments(self, date_start, date_end):
        """Posted inbound customer payments, enriched from linked invoices.

        Also adds paid Free invoices in the same period that have no payment
        (Free care is settled without cash collection).
        """
        Payment = self.env['account.payment'].sudo()
        domain = [
            ('state', '=', 'posted'),
            ('payment_type', '=', 'inbound'),
            ('partner_type', '=', 'customer'),
            ('payment_date', '>=', date_start.strftime(DF)),
            ('payment_date', '<=', date_end.strftime(DF)),
            ('company_id', '=', self.company_id.id),
        ]
        if self.journal_id:
            domain.append(('journal_id', '=', self.journal_id.id))

        payments = Payment.search(domain, order='payment_date asc, id asc')
        rows = []
        seen_invoice_ids = set()

        for payment in payments:
            invoices = payment.invoice_ids
            if payment.invoice_id and payment.invoice_id not in invoices:
                invoices |= payment.invoice_id
            if not invoices:
                if not self._payment_matches_filters(None):
                    continue
                rows.append(self._row_from_payment(payment, None))
                continue
            for invoice in invoices:
                if not self._invoice_matches_filters(invoice):
                    continue
                seen_invoice_ids.add(invoice.id)
                rows.append(self._row_from_payment(payment, invoice))

        # Free care (and zero-total paid bills) may never create account.payment.
        if self.payment_method in ('all', 'Free') and not self.journal_id:
            free_rows = self._rows_from_paid_invoices(
                date_start, date_end,
                force_methods=('Free',),
                exclude_invoice_ids=seen_invoice_ids,
            )
            rows.extend(free_rows)

        rows.sort(key=lambda r: (r['sort_date'] or date.min, r['payment_name'] or '', r['invoice_number'] or ''))
        return rows

    def _rows_from_paid_invoices(self, date_start, date_end, force_methods=None, exclude_invoice_ids=None):
        Invoice = self.env['account.invoice'].sudo()
        domain = [
            ('type', '=', 'out_invoice'),
            ('state', '=', 'paid'),
            ('date_invoice', '>=', date_start.strftime(DF)),
            ('date_invoice', '<=', date_end.strftime(DF)),
            ('company_id', '=', self.company_id.id),
        ]
        domain.extend(self._shop_domain())
        if force_methods:
            domain.append(('payment_method', 'in', list(force_methods)))
        else:
            domain.extend(self._payment_method_domain())

        invoices = Invoice.search(domain, order='date_invoice asc, id asc')
        exclude_invoice_ids = exclude_invoice_ids or set()
        rows = []

        for invoice in invoices:
            if invoice.id in exclude_invoice_ids:
                continue
            if force_methods and not self._invoice_matches_filters(invoice):
                continue
            payments = self._posted_payments_for_invoice(invoice)
            if self.journal_id:
                payments = payments.filtered(lambda p: p.journal_id == self.journal_id)
                if not payments:
                    continue
            if payments:
                for payment in payments:
                    rows.append(self._row_from_payment(payment, invoice))
            else:
                rows.append(self._row_from_invoice_only(invoice))
        return rows

    def _posted_payments_for_invoice(self, invoice):
        Payment = self.env['account.payment'].sudo()
        return Payment.search([
            ('state', '=', 'posted'),
            ('invoice_ids', 'in', invoice.id),
            ('company_id', '=', self.company_id.id),
        ], order='payment_date asc, id asc')

    def _payment_matches_filters(self, invoice):
        if invoice is None:
            # Orphan payment: only include when method filter is All or Cash
            return self.payment_method in ('all', 'Cash')
        return self._invoice_matches_filters(invoice)

    def _invoice_matches_filters(self, invoice):
        method = (invoice.payment_method or '').strip() or 'Cash'
        if self.payment_method != 'all' and method != self.payment_method:
            return False
        if self.credit_information != 'all':
            if method != 'Credit':
                return False
            if (invoice.credit_information or '') != self.credit_information:
                return False
        if self.shop_id and invoice.shop_id != self.shop_id:
            return False
        return True

    def _row_from_payment(self, payment, invoice):
        patient, payer, method, credit_info, free_reason, shop_name = self._invoice_attrs(invoice)
        patient_partner = patient or payment.partner_id
        amount = payment.amount or 0.0
        pay_date = self._parse_date(payment.payment_date)
        inv_date = self._parse_date(invoice.date_invoice) if invoice else False
        return {
            'sort_date': pay_date or inv_date,
            'payment_date': pay_date,
            'payment_date_eth': format_ethiopian_date(pay_date) if pay_date else '',
            'invoice_date': inv_date,
            'invoice_date_eth': format_ethiopian_date(inv_date) if inv_date else '',
            'payment_name': payment.name or '',
            'invoice_number': invoice.number if invoice else '',
            'patient_name': patient_partner.name if patient_partner else '',
            'patient_ref': patient_partner.ref if patient_partner else '',
            'payer_name': payer.name if payer else (payment.partner_id.name if payment.partner_id else ''),
            'payment_method': method,
            'credit_information': credit_info,
            'free_reason': free_reason,
            'shop': shop_name,
            'journal': payment.journal_id.name if payment.journal_id else '',
            'amount': amount,
            'invoice_total': invoice.amount_total if invoice else amount,
            'cashier': payment.create_uid.name if payment.create_uid else '',
            'origin': invoice.origin if invoice else '',
        }

    def _row_from_invoice_only(self, invoice):
        patient, payer, method, credit_info, free_reason, shop_name = self._invoice_attrs(invoice)
        patient_partner = patient or invoice.partner_id
        inv_date = self._parse_date(invoice.date_invoice)
        return {
            'sort_date': inv_date,
            'payment_date': False,
            'payment_date_eth': '',
            'invoice_date': inv_date,
            'invoice_date_eth': format_ethiopian_date(inv_date) if inv_date else '',
            'payment_name': '',
            'invoice_number': invoice.number or '',
            'patient_name': patient_partner.name if patient_partner else '',
            'patient_ref': patient_partner.ref if patient_partner else '',
            'payer_name': payer.name if payer else (invoice.partner_id.name if invoice.partner_id else ''),
            'payment_method': method,
            'credit_information': credit_info,
            'free_reason': free_reason,
            'shop': shop_name,
            'journal': '',
            'amount': invoice.amount_total or 0.0,
            'invoice_total': invoice.amount_total or 0.0,
            'cashier': invoice.create_uid.name if invoice.create_uid else '',
            'origin': invoice.origin or '',
        }

    @api.model
    def _invoice_attrs(self, invoice):
        if not invoice:
            return None, None, 'Cash', '', '', ''
        patient = invoice.patient_partner_id or None
        payer = invoice.payer_partner_id or None
        method = (invoice.payment_method or '').strip() or 'Cash'
        credit_info = invoice.credit_information or ''
        free_reason = invoice.free_reason or ''
        shop_name = invoice.shop_id.name if invoice.shop_id else ''
        return patient, payer, method, credit_info, free_reason, shop_name

    def _excel_styles(self, workbook):
        return {
            'title': workbook.add_format({
                'bold': True, 'font_size': 13, 'align': 'left', 'valign': 'vcenter',
            }),
            'subtitle': workbook.add_format({
                'bold': True, 'font_size': 11, 'align': 'left',
            }),
            'header': workbook.add_format({
                'bold': True, 'align': 'center', 'valign': 'vcenter',
                'border': 1, 'text_wrap': True, 'bg_color': '#D9E1F2',
            }),
            'cell': workbook.add_format({'border': 1}),
            'number': workbook.add_format({'border': 1, 'num_format': '#,##0.00'}),
            'total_label': workbook.add_format({'bold': True, 'border': 1}),
            'total_number': workbook.add_format({
                'bold': True, 'border': 1, 'num_format': '#,##0.00',
            }),
        }

    def _period_label(self, date_start, date_end):
        if self.period_type == 'ethiopian':
            return u'%s %s (EC) / %s \u2192 %s' % (
                eth_month_amharic(self.eth_month),
                self.eth_year,
                date_start.strftime(DF),
                date_end.strftime(DF),
            )
        return u'%s \u2192 %s' % (date_start.strftime(DF), date_end.strftime(DF))

    def _build_workbook(self, rows, date_start, date_end):
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = workbook.add_worksheet(u'Paid Payments')
        styles = self._excel_styles(workbook)

        headers = [
            u'Seq',
            u'Payment Date',
            u'Payment Date (EC)',
            u'Invoice Date',
            u'Invoice Date (EC)',
            u'Payment No.',
            u'Invoice No.',
            u'Patient',
            u'Medical Card',
            u'Payer',
            u'Payment Method',
            u'Credit Type',
            u'Free Reason',
            u'Shop',
            u'Journal',
            u'Amount Paid',
            u'Invoice Total',
            u'Cashier',
            u'Origin / SO',
        ]
        widths = [6, 12, 14, 12, 14, 16, 14, 22, 14, 22, 12, 16, 16, 14, 14, 12, 12, 16, 16]
        for i, width in enumerate(widths):
            ws.set_column(i, i, width)

        method_label = dict(self._fields['payment_method'].selection).get(
            self.payment_method, self.payment_method)
        title = u'Paid Payments Report \u2014 %s' % method_label
        ws.merge_range(0, 0, 0, len(headers) - 1, title, styles['title'])
        ws.merge_range(
            1, 0, 1, len(headers) - 1,
            u'Facility: %s' % (self.company_id.name or ''),
            styles['subtitle'],
        )
        ws.merge_range(
            2, 0, 2, len(headers) - 1,
            u'Period: %s | Date basis: %s' % (
                self._period_label(date_start, date_end),
                dict(self._fields['date_basis'].selection).get(self.date_basis, self.date_basis),
            ),
            styles['subtitle'],
        )

        filter_bits = []
        if self.shop_id:
            filter_bits.append(u'Shop: %s' % self.shop_id.name)
        if self.journal_id:
            filter_bits.append(u'Journal: %s' % self.journal_id.name)
        if self.payment_method in ('all', 'Credit') and self.credit_information != 'all':
            filter_bits.append(u'Credit Type: %s' % self.credit_information)
        if filter_bits:
            ws.merge_range(
                3, 0, 3, len(headers) - 1,
                u'Filters: %s' % u' | '.join(filter_bits),
                styles['subtitle'],
            )
            header_row = 4
        else:
            header_row = 3

        for col, header in enumerate(headers):
            ws.write(header_row, col, header, styles['header'])

        current_row = header_row + 1
        grand_total = 0.0
        invoice_total_sum = 0.0
        seq = 1
        for row in rows:
            values = [
                seq,
                row['payment_date'].strftime(DF) if row['payment_date'] else '',
                row['payment_date_eth'],
                row['invoice_date'].strftime(DF) if row['invoice_date'] else '',
                row['invoice_date_eth'],
                row['payment_name'],
                row['invoice_number'],
                row['patient_name'],
                row['patient_ref'],
                row['payer_name'],
                row['payment_method'],
                row['credit_information'],
                row['free_reason'],
                row['shop'],
                row['journal'],
                row['amount'],
                row['invoice_total'],
                row['cashier'],
                row['origin'],
            ]
            for col, val in enumerate(values):
                if col in (15, 16):
                    ws.write_number(current_row, col, float(val or 0.0), styles['number'])
                else:
                    ws.write(current_row, col, val if val is not None else '', styles['cell'])
            grand_total += row['amount'] or 0.0
            invoice_total_sum += row['invoice_total'] or 0.0
            seq += 1
            current_row += 1

        ws.write(current_row, 0, u'Total', styles['total_label'])
        for col in range(1, 15):
            ws.write(current_row, col, '', styles['total_label'])
        ws.write_number(current_row, 15, grand_total, styles['total_number'])
        ws.write_number(current_row, 16, invoice_total_sum, styles['total_number'])
        for col in (17, 18):
            ws.write(current_row, col, '', styles['total_label'])

        current_row += 2
        ws.write(current_row, 0, u'Prepared by: %s' % (self.env.user.name or ''))
        current_row += 1
        ws.write(current_row, 0, u'Date (EC): %s' % format_ethiopian_date(date.today()))

        # Summary sheet by payment method
        summary = workbook.add_worksheet(u'Summary')
        summary.set_column(0, 0, 20)
        summary.set_column(1, 2, 14)
        summary.write(0, 0, u'By Payment Method', styles['title'])
        summary.write(1, 0, u'Payment Method', styles['header'])
        summary.write(1, 1, u'Count', styles['header'])
        summary.write(1, 2, u'Amount', styles['header'])
        by_method = {}
        for row in rows:
            key = row['payment_method'] or u'(Unknown)'
            bucket = by_method.setdefault(key, {'count': 0, 'amount': 0.0})
            bucket['count'] += 1
            bucket['amount'] += row['amount'] or 0.0
        r = 2
        for key in sorted(by_method.keys()):
            summary.write(r, 0, key, styles['cell'])
            summary.write_number(r, 1, by_method[key]['count'], styles['cell'])
            summary.write_number(r, 2, by_method[key]['amount'], styles['number'])
            r += 1
        summary.write(r, 0, u'Total', styles['total_label'])
        summary.write_number(r, 1, len(rows), styles['total_label'])
        summary.write_number(r, 2, grand_total, styles['total_number'])

        workbook.close()
        return output.getvalue()
