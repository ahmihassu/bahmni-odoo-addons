# -*- coding: utf-8 -*-
from __future__ import division

import base64
import logging
from collections import defaultdict, OrderedDict
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

SHOP_SERVICE_COLUMN = {
    'MRU': 'card',
    'Laboratory': 'lab',
    'Radiology': 'imaging',
    'Procedure': 'procedure',
}

SERVICE_KEYS = (
    'card', 'lab', 'imaging', 'procedure', 'drugs', 'bed', 'others', 'total',
)

REGION_SECTION_ORDER = (
    'AMHARA',
    'DEBUB_BIHERESEB',
    'DEBUB_MIRAB',
    'OROMIA',
    'SIDAMA',
    'HARARI',
    'DIREDEWA_KETEMA',
)


class CbhiClaimExport(models.TransientModel):
    _name = 'bahmni.cbhi.claim.export'
    _description = 'Credit Payer Claim Excel Export (CBHI / Insurance / Credit Company)'

    claim_type = fields.Selection([
        ('CBHI', 'CBHI'),
        ('Insurance', 'Insurance'),
        ('Credit Companies', 'Credit Companies'),
    ], string="Claim Type", required=True, default='CBHI')
    eth_month = fields.Selection(
        ETH_MONTHS_SELECTION,
        string="Ethiopian Month",
        required=True,
        default='11',
    )
    eth_year = fields.Integer(
        string="Ethiopian Year",
        required=True,
        default=lambda self: (gregorian_to_ethiopian(date.today()) or (2018, 1, 1))[0],
    )
    company_id = fields.Many2one(
        'res.company',
        string="Facility",
        required=True,
        default=lambda self: self.env.user.company_id,
    )
    data = fields.Binary(string="Excel file", readonly=True)
    data_fname = fields.Char(string="File Name", readonly=True)
    state = fields.Selection(
        [('choose', 'choose'), ('get', 'get')],
        default='choose',
    )
    invoice_count = fields.Integer(string="Invoices included", readonly=True)

    @api.multi
    def action_generate_excel(self):
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(_(
                "Python package 'xlsxwriter' is required to generate the claim Excel."
            ))
        if self.eth_year < 1900 or self.eth_year > 2200:
            raise UserError(_("Please enter a valid Ethiopian year."))

        date_start, date_end = ethiopian_month_date_range(self.eth_year, int(self.eth_month))
        invoices = self._find_credit_invoices(date_start, date_end)
        if not invoices:
            raise UserError(_(
                "No open %s credit invoices found for Ethiopian %s %s "
                "(Gregorian %s \u2192 %s)."
            ) % (
                self.claim_type,
                eth_month_amharic(self.eth_month),
                self.eth_year,
                date_start.strftime(DF),
                date_end.strftime(DF),
            ))

        if self.claim_type == 'CBHI':
            xlsx_bytes = self._build_cbhi_workbook(invoices)
            sheet_label = 'CBHI'
        else:
            xlsx_bytes = self._build_payer_workbook(invoices)
            sheet_label = 'Insurance' if self.claim_type == 'Insurance' else 'CreditCompany'

        month_name = eth_month_amharic(self.eth_month) or ('M%s' % self.eth_month)
        facility = (self.company_id.name or 'Facility').replace(' ', '_')
        fname = '%s_Claim_%s_%s_%s.xlsx' % (
            sheet_label, month_name, self.eth_year, facility)

        self.write({
            'data': base64.b64encode(xlsx_bytes),
            'data_fname': fname,
            'state': 'get',
            'invoice_count': len(invoices),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bahmni.cbhi.claim.export',
            'view_mode': 'form',
            'view_type': 'form',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

    @api.model
    def _find_credit_invoices(self, date_start, date_end):
        Invoice = self.env['account.invoice'].sudo()
        domain = [
            ('type', '=', 'out_invoice'),
            ('state', '=', 'open'),
            ('payment_method', '=', 'Credit'),
            ('credit_information', '=', self.claim_type),
            ('date_invoice', '>=', date_start.strftime(DF)),
            ('date_invoice', '<=', date_end.strftime(DF)),
            ('company_id', '=', self.company_id.id),
        ]
        invoices = Invoice.search(domain, order='date_invoice asc, id asc')
        return invoices.filtered(lambda inv: inv.residual > 0.00001)

    @api.model
    def _split_patient_name(self, name):
        parts = [p for p in (name or '').strip().split() if p]
        given = parts[0] if len(parts) > 0 else ''
        father = parts[1] if len(parts) > 1 else ''
        return given, father

    @api.model
    def _age_on_date(self, birthdate, on_date):
        if not birthdate or not on_date:
            return ''
        try:
            string_types = basestring  # noqa: F821
        except NameError:
            string_types = str
        if isinstance(birthdate, string_types):
            parts = birthdate[:10].split('-')
            birthdate = date(int(parts[0]), int(parts[1]), int(parts[2]))
        if isinstance(on_date, string_types):
            parts = on_date[:10].split('-')
            on_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        years = on_date.year - birthdate.year
        if (on_date.month, on_date.day) < (birthdate.month, birthdate.day):
            years -= 1
        return years if years >= 0 else ''

    @api.model
    def _format_sex(self, gender):
        g = (gender or '').strip().upper()
        if g in ('M', 'MALE'):
            return 'M'
        if g in ('F', 'FEMALE'):
            return 'F'
        return g or ''

    @api.model
    def _service_amounts_for_invoice(self, invoice):
        amounts = {k: 0.0 for k in SERVICE_KEYS}
        total = invoice.residual or invoice.amount_total or 0.0
        shop_name = invoice.shop_id.name if invoice.shop_id else ''
        key = SHOP_SERVICE_COLUMN.get(shop_name, 'others')
        amounts[key] = total
        amounts['total'] = total
        return amounts

    @api.model
    def _row_from_invoice(self, invoice):
        patient = invoice.patient_partner_id or invoice.partner_id
        given, father = self._split_patient_name(patient.name if patient else '')
        amounts = self._service_amounts_for_invoice(invoice)
        birthdate = patient.birthdate if patient else False
        return {
            'date_invoice': invoice.date_invoice,
            'eth_date': format_ethiopian_date(invoice.date_invoice),
            'member_id': (
                invoice.cbhi_id
                or invoice.insurance_id
                or (patient.cbhi_id if patient else '')
                or (patient.insurance_id if patient else '')
                or ''
            ),
            'given': given,
            'father': father,
            'full_name': patient.name if patient else '',
            'sex': self._format_sex(patient.gender if patient else ''),
            'age': self._age_on_date(birthdate, invoice.date_invoice),
            'region': (invoice.cbhi_region or (patient.cbhi_region if patient else '') or '').strip(),
            'zone': (invoice.cbhi_zone or (patient.cbhi_zone if patient else '') or '').strip(),
            'woreda': (invoice.cbhi_woreda or (patient.cbhi_woreda if patient else '') or '').strip(),
            'medical_card': (patient.ref if patient else '') or '',
            'payer_name': (
                invoice.insurance_name
                or invoice.credit_companies
                or (invoice.payer_partner_id.name if invoice.payer_partner_id else '')
                or ''
            ),
            'insurance_code': invoice.insurance_code or '',
            'invoice_number': invoice.number or '',
            'amounts': amounts,
            'invoice': invoice,
        }

    @api.model
    def _group_rows_by_key(self, rows, key_name, preferred_order=()):
        grouped = defaultdict(list)
        for row in rows:
            key = row.get(key_name) or u'(Unknown)'
            grouped[key].append(row)

        ordered = OrderedDict()
        used = set()
        for preferred in preferred_order:
            matches = [r for r in grouped if r.upper() == preferred or r == preferred]
            for key in matches:
                if key not in used:
                    ordered[key] = grouped[key]
                    used.add(key)
        for key in sorted(grouped.keys()):
            if key not in used:
                ordered[key] = grouped[key]
                used.add(key)
        return ordered

    def _region_title(self, region):
        month_am = eth_month_amharic(self.eth_month)
        return u'%s የጤና መድህን ክፍያ መጠየቅያ የ%s ወር %s' % (
            region, month_am, self.eth_year,
        )

    def _payer_section_title(self, payer_name):
        month_am = eth_month_amharic(self.eth_month)
        label = u'Insurance' if self.claim_type == 'Insurance' else u'Credit Company'
        return u'%s Claim \u2014 %s \u2014 %s %s' % (label, payer_name, month_am, self.eth_year)

    def _write_section_headers(self, ws, styles, start_row, region):
        r = start_row
        ws.merge_range(r, 0, r, 18, self._region_title(region), styles['title'])
        r += 1
        ws.merge_range(r, 0, r, 18, u'ጤና መድህን', styles['subtitle'])
        r += 1
        facility_line = u'የጤና ተቋሙ ስም፡ %s' % (self.company_id.name or '')
        ws.merge_range(r, 0, r, 18, facility_line, styles['subtitle'])
        r += 1

        ws.write(r, 0, u'ተ.ቁ.', styles['header'])
        ws.write(r, 1, u'አገልግሎት\nየተሰጠበት ቀን', styles['header'])
        ws.write(r, 2, u'የተጠቃሚ\nመለያ ቁጥር', styles['header'])
        ws.write(r, 3, u'የተጠቃሚ ስም', styles['header'])
        ws.write(r, 4, u'የአበት  ስም', styles['header'])
        ws.write(r, 5, u'ፆታ', styles['header'])
        ws.write(r, 6, u'ዕድሜ', styles['header'])
        ws.merge_range(r, 7, r, 9, u'አድራሻ', styles['header'])
        ws.write(r, 10, u'የተጠቃሚየህክምናካርድቁጥር', styles['header'])
        ws.merge_range(r, 11, r, 18, u'የክፍያመጠን በአገልግሎት ዓይነት', styles['header'])
        r += 1

        for col in range(0, 11):
            ws.write(r, col, '', styles['header'])
        service_headers = [
            u'ካርድ ',
            u'ላብራቶሪ',
            u'ኢሜጂንግ',
            u'ፐሮሲጀር/ቀዶህክምና',
            u'መድሃኒት ና አቅርቦቶች',
            u'መኝታና\nምግብ',
            u'ሌሎች*',
            u'ድምር',
        ]
        for i, val in enumerate(service_headers):
            ws.write(r, 11 + i, val, styles['header'])
        r += 1

        for col in range(0, 19):
            ws.write(r, col, '', styles['header_sub'])
        ws.write(r, 7, u'ክልል', styles['header_sub'])
        ws.write(r, 8, u'ዞን', styles['header_sub'])
        ws.write(r, 9, u'ወራዳ', styles['header_sub'])
        r += 1
        return r

    def _excel_styles(self, workbook):
        return {
            'title': workbook.add_format({
                'bold': True, 'font_size': 12, 'align': 'left', 'valign': 'vcenter',
            }),
            'subtitle': workbook.add_format({
                'bold': True, 'font_size': 11, 'align': 'left',
            }),
            'header': workbook.add_format({
                'bold': True, 'align': 'center', 'valign': 'vcenter',
                'border': 1, 'text_wrap': True,
            }),
            'header_sub': workbook.add_format({
                'bold': True, 'align': 'center', 'border': 1, 'text_wrap': True,
            }),
            'cell': workbook.add_format({'border': 1}),
            'number': workbook.add_format({'border': 1, 'num_format': '#,##0.00'}),
            'total_label': workbook.add_format({'bold': True, 'border': 1}),
            'total_number': workbook.add_format({
                'bold': True, 'border': 1, 'num_format': '#,##0.00',
            }),
        }

    def _write_signature_footer(self, ws, styles, current_row):
        user_name = self.env.user.name or ''
        today_eth = format_ethiopian_date(date.today())
        ws.write(current_row, 2, u'አዘጋጅ  ስም   %s' % user_name)
        current_row += 1
        ws.write(current_row, 2, u'            ፊርማ')
        current_row += 1
        ws.write(current_row, 2, u'       ቀን            %s' % today_eth)
        return current_row

    def _build_cbhi_workbook(self, invoices):
        rows = [self._row_from_invoice(inv) for inv in invoices]
        by_region = self._group_rows_by_key(rows, 'region', REGION_SECTION_ORDER)

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = workbook.add_worksheet(u'CBHI Claim')
        styles = self._excel_styles(workbook)

        ws.set_column(0, 0, 8)
        ws.set_column(1, 1, 12)
        ws.set_column(2, 2, 22)
        ws.set_column(3, 4, 14)
        ws.set_column(5, 6, 8)
        ws.set_column(7, 9, 18)
        ws.set_column(10, 10, 16)
        ws.set_column(11, 18, 12)

        current_row = 0
        grand = {k: 0.0 for k in SERVICE_KEYS}

        for region, region_rows in by_region.items():
            current_row = self._write_section_headers(ws, styles, current_row, region)
            seq = 1
            for row in region_rows:
                amounts = row['amounts']
                values = [
                    seq,
                    row['eth_date'],
                    row['member_id'],
                    row['given'],
                    row['father'],
                    row['sex'],
                    row['age'],
                    row['region'],
                    row['zone'],
                    row['woreda'],
                    row['medical_card'],
                    amounts['card'] or None,
                    amounts['lab'] or None,
                    amounts['imaging'] or None,
                    amounts['procedure'] or None,
                    amounts['drugs'] or None,
                    amounts['bed'] or None,
                    amounts['others'] or None,
                    amounts['total'] or None,
                ]
                for col, val in enumerate(values):
                    if col >= 11:
                        if val:
                            ws.write_number(current_row, col, float(val), styles['number'])
                        else:
                            ws.write(current_row, col, '', styles['cell'])
                    else:
                        ws.write(current_row, col, val if val is not None else '', styles['cell'])
                for k in SERVICE_KEYS:
                    grand[k] += amounts[k]
                seq += 1
                current_row += 1
            current_row += 1

        ws.write(current_row, 0, u'ድምር', styles['total_label'])
        for col in range(1, 11):
            ws.write(current_row, col, '', styles['total_label'])
        for i, key in enumerate(SERVICE_KEYS):
            ws.write_number(current_row, 11 + i, grand[key], styles['total_number'])
        current_row += 2
        self._write_signature_footer(ws, styles, current_row)

        workbook.close()
        return output.getvalue()

    def _build_payer_workbook(self, invoices):
        rows = [self._row_from_invoice(inv) for inv in invoices]
        by_payer = self._group_rows_by_key(rows, 'payer_name')

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet_name = u'Insurance Claim' if self.claim_type == 'Insurance' else u'Credit Company Claim'
        ws = workbook.add_worksheet(sheet_name)
        styles = self._excel_styles(workbook)

        headers = [
            u'Seq', u'Service Date (EC)', u'Member / Policy ID', u'Patient Name',
            u'Sex', u'Age', u'Medical Card', u'Invoice', u'Payer', u'Code',
            u'Card', u'Lab', u'Imaging', u'Procedure', u'Drugs', u'Bed', u'Others', u'Total',
        ]
        for i, width in enumerate([6, 14, 18, 22, 6, 6, 14, 14, 22, 10, 10, 10, 10, 10, 10, 10, 10, 12]):
            ws.set_column(i, i, width)

        current_row = 0
        grand_total = 0.0

        for payer_name, payer_rows in by_payer.items():
            ws.merge_range(
                current_row, 0, current_row, len(headers) - 1,
                self._payer_section_title(payer_name), styles['title'])
            current_row += 1
            facility_line = u'Facility: %s' % (self.company_id.name or '')
            ws.merge_range(
                current_row, 0, current_row, len(headers) - 1,
                facility_line, styles['subtitle'])
            current_row += 1

            for col, header in enumerate(headers):
                ws.write(current_row, col, header, styles['header'])
            current_row += 1

            seq = 1
            section_total = 0.0
            for row in payer_rows:
                amounts = row['amounts']
                values = [
                    seq, row['eth_date'], row['member_id'], row['full_name'],
                    row['sex'], row['age'], row['medical_card'], row['invoice_number'],
                    row['payer_name'], row['insurance_code'],
                    amounts['card'] or None, amounts['lab'] or None,
                    amounts['imaging'] or None, amounts['procedure'] or None,
                    amounts['drugs'] or None, amounts['bed'] or None,
                    amounts['others'] or None, amounts['total'] or None,
                ]
                for col, val in enumerate(values):
                    if col >= 10:
                        if val:
                            ws.write_number(current_row, col, float(val), styles['number'])
                        else:
                            ws.write(current_row, col, '', styles['cell'])
                    else:
                        ws.write(current_row, col, val if val is not None else '', styles['cell'])
                section_total += amounts['total']
                grand_total += amounts['total']
                seq += 1
                current_row += 1

            ws.write(current_row, 0, u'Section Total', styles['total_label'])
            for col in range(1, 17):
                ws.write(current_row, col, '', styles['total_label'])
            ws.write_number(current_row, 17, section_total, styles['total_number'])
            current_row += 2

        ws.write(current_row, 0, u'Grand Total', styles['total_label'])
        for col in range(1, 17):
            ws.write(current_row, col, '', styles['total_label'])
        ws.write_number(current_row, 17, grand_total, styles['total_number'])
        current_row += 2
        self._write_signature_footer(ws, styles, current_row)

        workbook.close()
        return output.getvalue()
