# -*- coding: utf-8 -*-
from __future__ import division

import base64
import logging
import re
import zipfile
from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree as ET

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


def _xlsx_text(value):
    """Return text safe for xlsxwriter (unicode on Python 2)."""
    if value is None or value is False:
        return u''
    try:
        text_type = unicode  # noqa: F821  (Python 2)
        bytes_type = str
    except NameError:
        text_type = str
        bytes_type = bytes
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes_type):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('utf-8', 'replace')
    return text_type(value)

CREDIT_TYPES = (
    ('CBHI', 'CBHI'),
    ('SHI', 'SHI'),
    ('Insurance', 'Insurance'),
    ('Credit Companies', 'Credit Companies'),
)

CREDIT_TYPES_WITH_ALL = (
    ('all', 'All Credit Types'),
) + CREDIT_TYPES

# Canonical settlement sheet headers (row 1). "Paid Amount" is filled by the payer remittance.
SETTLEMENT_HEADERS = [
    'Invoice Number',
    'Credit Type',
    'Patient ID',
    'Patient Name',
    'Payer Name',
    'Invoice Date',
    'Amount Due',
    'Paid Amount',
    'Payment Date',
    'Payment Reference',
    'Notes',
]

HEADER_ALIASES = {
    'invoice number': 'Invoice Number',
    'invoice_number': 'Invoice Number',
    'invoice no': 'Invoice Number',
    'invoice no.': 'Invoice Number',
    'credit type': 'Credit Type',
    'credit_type': 'Credit Type',
    'claim type': 'Credit Type',
    'patient id': 'Patient ID',
    'patient_id': 'Patient ID',
    'patient ref': 'Patient ID',
    'patient name': 'Patient Name',
    'payer name': 'Payer Name',
    'payer': 'Payer Name',
    'invoice date': 'Invoice Date',
    'amount due': 'Amount Due',
    'residual': 'Amount Due',
    'invoice amount': 'Amount Due',
    'paid amount': 'Paid Amount',
    'amount paid': 'Paid Amount',
    'payment amount': 'Paid Amount',
    'payment date': 'Payment Date',
    'payment reference': 'Payment Reference',
    'reference': 'Payment Reference',
    'notes': 'Notes',
    'remark': 'Notes',
    'remarks': 'Notes',
}

NS_MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


class BahmniCreditSettlementLog(models.Model):
    """Persistent audit of each credit settlement Excel upload."""

    _name = 'bahmni.credit.settlement.log'
    _description = 'Credit Settlement Reconciliation Log'
    _order = 'create_date desc, id desc'

    name = fields.Char(string="Reference", required=True, copy=False, default='/')
    company_id = fields.Many2one(
        'res.company', string="Facility", required=True,
        default=lambda self: self.env.user.company_id,
    )
    user_id = fields.Many2one(
        'res.users', string="Uploaded By", required=True, readonly=True,
        default=lambda self: self.env.user,
    )
    credit_type = fields.Selection(
        CREDIT_TYPES_WITH_ALL, string="Credit Type", required=True, default='all',
    )
    journal_id = fields.Many2one(
        'account.journal', string="Payment Journal", required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
    )
    payment_date = fields.Date(
        string="Default Payment Date",
        default=fields.Date.context_today,
        help="Used when a row has no Payment Date.",
    )
    upload_fname = fields.Char(string="Uploaded File Name", readonly=True)
    upload_file = fields.Binary(string="Uploaded File", readonly=True, attachment=True)
    report_fname = fields.Char(string="Report File Name", readonly=True)
    report_file = fields.Binary(string="Skip / Result Report", readonly=True, attachment=True)
    matched_count = fields.Integer(string="Matched", readonly=True)
    skipped_count = fields.Integer(string="Skipped", readonly=True)
    total_paid = fields.Float(string="Total Settled", readonly=True, digits=(16, 2))
    line_ids = fields.One2many(
        'bahmni.credit.settlement.log.line', 'log_id', string="Lines", readonly=True,
    )
    state = fields.Selection([
        ('done', 'Done'),
    ], string="Status", default='done', readonly=True)
    notes = fields.Text(string="Summary", readonly=True)

    @api.model
    def create(self, vals):
        vals = dict(vals or {})
        if vals.get('name', '/') == '/':
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'bahmni.credit.settlement.log'
            ) or _('Settlement')
        return super(BahmniCreditSettlementLog, self).create(vals)


class BahmniCreditSettlementLogLine(models.Model):
    _name = 'bahmni.credit.settlement.log.line'
    _description = 'Credit Settlement Log Line'
    _order = 'row_number asc, id asc'

    log_id = fields.Many2one(
        'bahmni.credit.settlement.log', string="Log", required=True, ondelete='cascade',
    )
    row_number = fields.Integer(string="Excel Row")
    invoice_number = fields.Char(string="Invoice Number")
    credit_type = fields.Char(string="Credit Type")
    paid_amount = fields.Float(string="Paid Amount", digits=(16, 2))
    status = fields.Selection([
        ('matched', 'Matched'),
        ('skipped', 'Skipped'),
    ], string="Status", required=True)
    skip_reason = fields.Char(string="Skip Reason")
    invoice_id = fields.Many2one('account.invoice', string="Invoice", ondelete='set null')
    payment_id = fields.Many2one('account.payment', string="Payment", ondelete='set null')


class BahmniCreditSettlementWizard(models.TransientModel):
    """Download settlement template and/or upload Excel to reconcile credit invoices."""

    _name = 'bahmni.credit.settlement.wizard'
    _description = 'Credit Settlement Reconciliation'

    state = fields.Selection([
        ('choose', 'choose'),
        ('done', 'done'),
    ], default='choose')
    credit_type = fields.Selection(
        CREDIT_TYPES_WITH_ALL, string="Credit Type", required=True, default='all',
    )
    company_id = fields.Many2one(
        'res.company', string="Facility", required=True,
        default=lambda self: self.env.user.company_id,
    )
    journal_id = fields.Many2one(
        'account.journal', string="Payment Journal", required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        help="Bank/cash journal used to post remittance payments for matched rows.",
    )
    payment_date = fields.Date(
        string="Default Payment Date",
        required=True,
        default=fields.Date.context_today,
    )
    include_open_invoices = fields.Boolean(
        string="Pre-fill Open Invoices",
        default=True,
        help="When downloading the template, include open credit invoices so "
             "you only fill Paid Amount / Payment Date / Reference.",
    )
    upload_fname = fields.Char(string="File Name")
    upload_file = fields.Binary(string="Settlement Excel", attachment=False)
    template_fname = fields.Char(string="Template File Name", readonly=True)
    template_file = fields.Binary(string="Template Excel", readonly=True)
    report_fname = fields.Char(string="Report File Name", readonly=True)
    report_file = fields.Binary(string="Result Report", readonly=True)
    log_id = fields.Many2one('bahmni.credit.settlement.log', string="Log", readonly=True)
    matched_count = fields.Integer(string="Matched", readonly=True)
    skipped_count = fields.Integer(string="Skipped", readonly=True)
    total_paid = fields.Float(string="Total Settled", readonly=True, digits=(16, 2))

    @api.model
    def default_get(self, fields_list):
        res = super(BahmniCreditSettlementWizard, self).default_get(fields_list)
        company = self.env.user.company_id
        if 'journal_id' in (fields_list or []) or not fields_list:
            if not res.get('journal_id'):
                journal = self.env['account.journal'].search([
                    ('type', '=', 'bank'),
                    ('company_id', '=', company.id),
                ], order='id asc', limit=1)
                if not journal:
                    journal = self.env['account.journal'].search([
                        ('type', 'in', ('bank', 'cash')),
                        ('company_id', '=', company.id),
                    ], order='id asc', limit=1)
                if journal:
                    res['journal_id'] = journal.id
        return res

    # ------------------------------------------------------------------
    # Template download
    # ------------------------------------------------------------------

    @api.multi
    def action_download_template(self):
        self.ensure_one()
        if not xlsxwriter:
            raise UserError(_(
                "Python package 'xlsxwriter' is required to generate the settlement Excel."
            ))
        invoices = self.env['account.invoice']
        if self.include_open_invoices:
            invoices = self._find_open_credit_invoices()
        xlsx_bytes = self._build_template_workbook(invoices)
        label = 'All' if self.credit_type == 'all' else self.credit_type.replace(' ', '')
        fname = 'Credit_Settlement_%s_%s.xlsx' % (
            label, fields.Date.context_today(self).replace('-', ''),
        )
        self.write({
            'template_file': base64.b64encode(xlsx_bytes),
            'template_fname': fname,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': self.env.context,
        }

    def _find_open_credit_invoices(self):
        domain = [
            ('type', '=', 'out_invoice'),
            ('state', '=', 'open'),
            ('payment_method', '=', 'Credit'),
            ('company_id', '=', self.company_id.id),
        ]
        if self.credit_type and self.credit_type != 'all':
            domain.append(('credit_information', '=', self.credit_type))
        invoices = self.env['account.invoice'].sudo().search(
            domain, order='date_invoice asc, id asc',
        )
        return invoices.filtered(lambda inv: (inv.residual or 0.0) > 0.00001)

    def _build_template_workbook(self, invoices):
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = workbook.add_worksheet('Settlement')
        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'align': 'center',
        })
        text_fmt = workbook.add_format({'border': 1})
        num_fmt = workbook.add_format({'border': 1, 'num_format': '#,##0.00'})
        paid_fmt = workbook.add_format({
            'border': 1, 'num_format': '#,##0.00', 'bg_color': '#FFF2CC',
        })

        widths = [18, 16, 14, 22, 22, 12, 12, 12, 12, 18, 20]
        for i, w in enumerate(widths):
            ws.set_column(i, i, w)

        for col, header in enumerate(SETTLEMENT_HEADERS):
            ws.write(0, col, _xlsx_text(header), header_fmt)

        row_idx = 1
        for invoice in invoices:
            patient = invoice.patient_partner_id or invoice.partner_id
            payer = invoice.payer_partner_id or invoice.partner_id
            values = [
                invoice.number or '',
                invoice.credit_information or '',
                (patient.ref if patient else '') or '',
                (patient.name if patient else '') or '',
                (payer.name if payer else '') or '',
                invoice.date_invoice or '',
                float(invoice.residual or 0.0),
                '',  # Paid Amount - user fills
                '',  # Payment Date
                '',  # Payment Reference
                '',  # Notes
            ]
            for col, val in enumerate(values):
                if col == 6:
                    ws.write_number(row_idx, col, float(val or 0.0), num_fmt)
                elif col == 7:
                    ws.write(row_idx, col, u'', paid_fmt)
                else:
                    ws.write(row_idx, col, _xlsx_text(val), text_fmt)
            row_idx += 1

        # Instructions sheet (ASCII-only: Py2 xlsxwriter rejects UTF-8 byte strings)
        info = workbook.add_worksheet('Instructions')
        info.set_column(0, 0, 100)
        lines = [
            u'Credit Settlement Reconciliation - instructions',
            u'',
            u'1. Fill Paid Amount for each row you want to settle (must equal Amount Due exactly).',
            u'2. Partial payments are not allowed; leave Paid Amount blank to skip a row.',
            u'3. Optional: Payment Date (YYYY-MM-DD), Payment Reference, Notes.',
            u'4. Do not change Invoice Number - it is the match key.',
            u'5. Credit Type must be one of: CBHI, SHI, Insurance, Credit Companies.',
            u'6. Upload this file from Accounting > Credit Settlement Reconciliation.',
            u'7. Rows that do not match an open credit invoice, or whose Paid Amount '
            u'differs from Amount Due, are skipped and listed in the result report.',
        ]
        for i, line in enumerate(lines):
            info.write(i, 0, line)

        workbook.close()
        return output.getvalue()

    # ------------------------------------------------------------------
    # Upload / reconcile
    # ------------------------------------------------------------------

    @api.multi
    def action_reconcile(self):
        self.ensure_one()
        if not self.env.user.has_group('bahmni_sale.group_credit_settlement_reconcile'):
            raise UserError(_(
                "You need the 'Credit Settlement Reconciliation' privilege to upload "
                "and reconcile credit payments."
            ))
        if not self.upload_file:
            raise UserError(_("Please upload a settlement Excel file (.xlsx)."))
        if not self.journal_id:
            raise UserError(_("Please select a payment journal."))

        raw = base64.b64decode(self.upload_file)
        try:
            rows = self._parse_settlement_xlsx(raw)
        except UserError:
            raise
        except Exception as exc:
            _logger.exception('Credit settlement Excel parse failed')
            raise UserError(_(
                "Could not read the Excel file. Use the downloaded .xlsx template. "
                "Details: %s"
            ) % exc)

        if not rows:
            raise UserError(_("No data rows found in the Excel file."))

        log = self.env['bahmni.credit.settlement.log'].create({
            'company_id': self.company_id.id,
            'credit_type': self.credit_type,
            'journal_id': self.journal_id.id,
            'payment_date': self.payment_date,
            'upload_file': self.upload_file,
            'upload_fname': self.upload_fname or 'settlement.xlsx',
        })

        line_vals = []
        matched = 0
        skipped = 0
        total_paid = 0.0
        currency = self.company_id.currency_id

        for row in rows:
            result = self._process_row(row, currency)
            line_vals.append(dict(result['line'], log_id=log.id))
            if result['line']['status'] == 'matched':
                matched += 1
                total_paid += result['line'].get('paid_amount') or 0.0
            else:
                skipped += 1

        if line_vals:
            LogLine = self.env['bahmni.credit.settlement.log.line']
            for vals in line_vals:
                LogLine.create(vals)

        report_bytes = self._build_result_report(line_vals)
        report_fname = 'Credit_Settlement_Report_%s.xlsx' % (
            (log.name or 'log').replace('/', '_'),
        )
        summary = _(
            "Matched %s, skipped %s, total settled %.2f"
        ) % (matched, skipped, total_paid)
        log.write({
            'matched_count': matched,
            'skipped_count': skipped,
            'total_paid': total_paid,
            'report_file': base64.b64encode(report_bytes),
            'report_fname': report_fname,
            'notes': summary,
        })

        self.write({
            'state': 'done',
            'log_id': log.id,
            'matched_count': matched,
            'skipped_count': skipped,
            'total_paid': total_paid,
            'report_file': base64.b64encode(report_bytes),
            'report_fname': report_fname,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': self.env.context,
        }

    @api.multi
    def action_open_log(self):
        self.ensure_one()
        if not self.log_id:
            return True
        return {
            'type': 'ir.actions.act_window',
            'name': _('Settlement Log'),
            'res_model': 'bahmni.credit.settlement.log',
            'view_mode': 'form',
            'res_id': self.log_id.id,
            'target': 'current',
        }

    def _process_row(self, row, currency):
        row_number = row['row_number']
        invoice_number = (row.get('Invoice Number') or '').strip()
        paid_raw = row.get('Paid Amount')
        credit_type_cell = (row.get('Credit Type') or '').strip()
        payment_date = self._parse_date(row.get('Payment Date')) or self.payment_date
        payment_ref = (row.get('Payment Reference') or '').strip()
        notes = (row.get('Notes') or '').strip()

        base_line = {
            'row_number': row_number,
            'invoice_number': invoice_number,
            'credit_type': credit_type_cell,
            'paid_amount': 0.0,
            'status': 'skipped',
            'skip_reason': False,
            'invoice_id': False,
            'payment_id': False,
        }

        if not invoice_number:
            base_line['skip_reason'] = _('Missing Invoice Number')
            return {'line': base_line}

        if paid_raw in (None, ''):
            base_line['skip_reason'] = _('Paid Amount blank - skipped')
            return {'line': base_line}

        try:
            paid_amount = float(paid_raw)
        except (TypeError, ValueError):
            base_line['skip_reason'] = _('Invalid Paid Amount')
            return {'line': base_line}

        base_line['paid_amount'] = paid_amount
        if paid_amount <= 0:
            base_line['skip_reason'] = _('Paid Amount must be greater than zero')
            return {'line': base_line}

        invoice = self._find_invoice(invoice_number)
        if not invoice:
            base_line['skip_reason'] = _('No open Credit invoice with this number')
            return {'line': base_line}

        base_line['invoice_id'] = invoice.id
        base_line['credit_type'] = invoice.credit_information or credit_type_cell

        if self.credit_type != 'all':
            if (invoice.credit_information or '') != self.credit_type:
                base_line['skip_reason'] = _(
                    "Invoice credit type '%s' does not match filter '%s'"
                ) % (invoice.credit_information or '-', self.credit_type)
                return {'line': base_line}

        if credit_type_cell and invoice.credit_information:
            if self._norm(credit_type_cell) != self._norm(invoice.credit_information):
                base_line['skip_reason'] = _(
                    "Excel Credit Type '%s' does not match invoice '%s'"
                ) % (credit_type_cell, invoice.credit_information)
                return {'line': base_line}

        residual = invoice.residual or 0.0
        precision = currency.decimal_places if currency else 2
        if float_compare(paid_amount, residual, precision_digits=precision) != 0:
            base_line['skip_reason'] = _(
                "Paid Amount %.2f does not equal Amount Due %.2f (partials not allowed)"
            ) % (paid_amount, residual)
            return {'line': base_line}

        try:
            payment = self._create_credit_payment(
                invoice, paid_amount, payment_date, payment_ref, notes,
            )
        except UserError as exc:
            base_line['skip_reason'] = (
                exc.name if getattr(exc, 'name', None) else (exc.args[0] if exc.args else str(exc))
            )
            return {'line': base_line}
        except Exception as exc:
            _logger.exception(
                'Credit settlement payment failed for invoice %s', invoice_number,
            )
            base_line['skip_reason'] = _('Payment failed: %s') % exc
            return {'line': base_line}

        base_line.update({
            'status': 'matched',
            'skip_reason': False,
            'payment_id': payment.id,
            'paid_amount': paid_amount,
        })
        return {'line': base_line}

    def _find_invoice(self, invoice_number):
        Invoice = self.env['account.invoice'].sudo()
        domain = [
            ('type', '=', 'out_invoice'),
            ('state', '=', 'open'),
            ('payment_method', '=', 'Credit'),
            ('company_id', '=', self.company_id.id),
            ('number', '=', invoice_number),
        ]
        invoice = Invoice.search(domain, limit=1)
        if invoice:
            return invoice
        # Soft match: strip spaces
        compact = re.sub(r'\s+', '', invoice_number)
        candidates = Invoice.search([
            ('type', '=', 'out_invoice'),
            ('state', '=', 'open'),
            ('payment_method', '=', 'Credit'),
            ('company_id', '=', self.company_id.id),
        ])
        for inv in candidates:
            if inv.number and re.sub(r'\s+', '', inv.number) == compact:
                return inv
        return Invoice.browse()

    def _create_credit_payment(self, invoice, amount, payment_date, payment_ref, notes):
        Payment = self.env['account.payment']
        method = self.env['account.payment.method'].search([
            ('payment_type', '=', 'inbound'),
            ('code', '=', 'manual'),
        ], limit=1)
        if not method:
            raise UserError(_("No inbound manual payment method found."))

        partner = invoice.partner_id
        communication = payment_ref or _(
            'Credit settlement - %s'
        ) % (invoice.number or invoice.id)
        if notes:
            communication = '%s | %s' % (communication, notes)

        payment = Payment.with_context(
            default_invoice_ids=[(4, invoice.id, None)],
            bahmni_credit_settlement_reconcile=True,
        ).create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id,
            'amount': amount,
            'journal_id': self.journal_id.id,
            'payment_method_id': method.id,
            'payment_date': payment_date,
            'communication': communication,
            'invoice_ids': [(4, invoice.id, None)],
        })
        payment.with_context(bahmni_credit_settlement_reconcile=True).post()
        invoice.message_post(body=_(
            "Credit settlement payment %.2f posted via Excel reconciliation "
            "(journal: %s, ref: %s)."
        ) % (amount, self.journal_id.display_name, communication))
        return payment

    # ------------------------------------------------------------------
    # Excel parse / report
    # ------------------------------------------------------------------

    @api.model
    def _norm(self, value):
        return re.sub(r'\s+', ' ', (value or '').strip()).lower()

    @api.model
    def _parse_date(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d')
        text = str(value).strip()
        if not text:
            return False
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y'):
            try:
                return datetime.strptime(text[:10], fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        # Excel serial date as number
        try:
            serial = float(text)
            # Excel epoch 1899-12-30
            from datetime import timedelta, date as date_cls
            dt = date_cls(1899, 12, 30) + timedelta(days=int(serial))
            return dt.strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            return False

    def _parse_settlement_xlsx(self, raw_bytes):
        """Parse first sheet of an xlsx using stdlib zipfile (no openpyxl)."""
        bio = BytesIO(raw_bytes)
        try:
            zf = zipfile.ZipFile(bio)
        except Exception:
            # Py2: BadZipfile; Py3: BadZipFile
            raise UserError(_("File is not a valid .xlsx workbook."))

        shared = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall(NS_MAIN + 'si'):
                texts = [t.text or '' for t in si.iter(NS_MAIN + 't')]
                shared.append(''.join(texts))

        sheet_path = 'xl/worksheets/sheet1.xml'
        if sheet_path not in zf.namelist():
            # fall back to first worksheet
            sheets = [n for n in zf.namelist() if n.startswith('xl/worksheets/sheet')]
            if not sheets:
                raise UserError(_("No worksheet found in the Excel file."))
            sheet_path = sorted(sheets)[0]

        root = ET.fromstring(zf.read(sheet_path))
        rows_by_r = {}
        for row_el in root.iter(NS_MAIN + 'row'):
            r_idx = int(row_el.get('r', '0'))
            cells = {}
            for c_el in row_el.findall(NS_MAIN + 'c'):
                ref = c_el.get('r') or ''
                col = self._col_letters_to_index(''.join(ch for ch in ref if ch.isalpha()))
                cell_type = c_el.get('t')
                v_el = c_el.find(NS_MAIN + 'v')
                is_el = c_el.find(NS_MAIN + 'is')
                value = ''
                if cell_type == 's' and v_el is not None and v_el.text is not None:
                    try:
                        value = shared[int(v_el.text)]
                    except (IndexError, ValueError):
                        value = v_el.text
                elif cell_type == 'inlineStr' and is_el is not None:
                    value = ''.join(t.text or '' for t in is_el.iter(NS_MAIN + 't'))
                elif v_el is not None and v_el.text is not None:
                    value = v_el.text
                cells[col] = value
            rows_by_r[r_idx] = cells

        if not rows_by_r:
            return []

        header_row_idx = min(rows_by_r.keys())
        header_cells = rows_by_r[header_row_idx]
        col_map = {}
        for col, raw_header in header_cells.items():
            key = HEADER_ALIASES.get(self._norm(raw_header))
            if key:
                col_map[col] = key

        if 'Invoice Number' not in col_map.values():
            raise UserError(_(
                "Settlement Excel must include an 'Invoice Number' column. "
                "Download the template and try again."
            ))
        if 'Paid Amount' not in col_map.values():
            raise UserError(_(
                "Settlement Excel must include a 'Paid Amount' column. "
                "Download the template and try again."
            ))

        parsed = []
        for r_idx in sorted(rows_by_r.keys()):
            if r_idx == header_row_idx:
                continue
            cells = rows_by_r[r_idx]
            if not any(str(v).strip() for v in cells.values()):
                continue
            row = {'row_number': r_idx}
            for col, key in col_map.items():
                row[key] = cells.get(col, '')
            # Skip empty invoice + empty paid
            if not str(row.get('Invoice Number') or '').strip() and \
                    not str(row.get('Paid Amount') or '').strip():
                continue
            parsed.append(row)
        return parsed

    @api.model
    def _col_letters_to_index(self, letters):
        idx = 0
        for ch in (letters or '').upper():
            if not ('A' <= ch <= 'Z'):
                continue
            idx = idx * 26 + (ord(ch) - ord('A') + 1)
        return idx - 1

    def _build_result_report(self, line_vals):
        if not xlsxwriter:
            # Minimal fallback: empty workbook bytes not useful; still require xlsxwriter
            raise UserError(_(
                "Python package 'xlsxwriter' is required to generate the result report."
            ))
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = workbook.add_worksheet('Result')
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1})
        ok_fmt = workbook.add_format({'bg_color': '#C6EFCE', 'border': 1})
        skip_fmt = workbook.add_format({'bg_color': '#FFC7CE', 'border': 1})
        headers = [
            'Excel Row', 'Invoice Number', 'Credit Type', 'Paid Amount',
            'Status', 'Skip Reason', 'Invoice ID', 'Payment ID',
        ]
        for col, h in enumerate(headers):
            ws.write(0, col, _xlsx_text(h), header_fmt)
        for i, line in enumerate(line_vals, start=1):
            fmt = ok_fmt if line.get('status') == 'matched' else skip_fmt
            ws.write(i, 0, line.get('row_number') or '', fmt)
            ws.write(i, 1, _xlsx_text(line.get('invoice_number')), fmt)
            ws.write(i, 2, _xlsx_text(line.get('credit_type')), fmt)
            ws.write_number(i, 3, float(line.get('paid_amount') or 0.0), fmt)
            ws.write(i, 4, _xlsx_text(line.get('status')), fmt)
            ws.write(i, 5, _xlsx_text(line.get('skip_reason')), fmt)
            ws.write(i, 6, line.get('invoice_id') or '', fmt)
            ws.write(i, 7, line.get('payment_id') or '', fmt)
        for col, width in enumerate([10, 18, 16, 12, 10, 50, 10, 10]):
            ws.set_column(col, col, width)
        workbook.close()
        return output.getvalue()
