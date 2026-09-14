# -*- coding: utf-8 -*-
from lxml import etree

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.osv.orm import setup_modifiers


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'

    shop_id = fields.Many2one('sale.shop', 'Shop')
    patient_partner_id = fields.Many2one(
        'res.partner',
        string="Patient",
        readonly=True,
        copy=False,
        help="Care recipient when invoice partner is a Credit payer.",
    )
    payer_partner_id = fields.Many2one(
        'res.partner',
        string="Payer",
        readonly=True,
        copy=False,
    )
    payment_method = fields.Char(string="Payment Method", readonly=True, copy=False)
    credit_information = fields.Char(string="Credit Information", readonly=True, copy=False)
    credit_companies = fields.Char(string="Credit Companies", readonly=True, copy=False)
    free_reason = fields.Char(string="Free Reason", readonly=True, copy=False)
    insurance_id = fields.Char(string="Insurance ID", readonly=True, copy=False)
    insurance_name = fields.Char(string="Insurance Name", readonly=True, copy=False)
    insurance_code = fields.Char(string="Insurance Code", readonly=True, copy=False)
    insurance_zone = fields.Char(string="Insurance Zone", readonly=True, copy=False)
    insurance_expiry_date = fields.Char(string="Insurance Expiry Date", readonly=True, copy=False)
    cbhi_id = fields.Char(string="CBHI ID", readonly=True, copy=False)
    cbhi_expiry_date = fields.Char(string="CBHI Expiry Date", readonly=True, copy=False)
    cbhi_region = fields.Char(string="CBHI Region", readonly=True, copy=False)
    cbhi_zone = fields.Char(string="CBHI Zone", readonly=True, copy=False)
    cbhi_woreda = fields.Char(string="CBHI Woreda", readonly=True, copy=False)

    _BAHMNI_DISCOUNT_WRITE_FIELDS = (
        'discount', 'discount_percentage', 'discount_type', 'disc_acc_id',
    )
    _BAHMNI_CASHIER_LOCKED_FIELDS = ('shop_id', 'team_id', 'user_id')

    @api.multi
    def copy(self, default=None):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            None,
            _("Cashiers are not allowed to duplicate invoices."),
        )
        return super(AccountInvoice, self).copy(default=default)

    @api.multi
    def write(self, vals):
        Users = self.env['res.users']
        if any(field in vals for field in self._BAHMNI_CASHIER_LOCKED_FIELDS):
            Users.bahmni_cashier_raise_if_restricted(
                None,
                _("Cashiers are not allowed to change Shop, Salesperson, or "
                  "Sales Team on invoices."),
            )
        discount_keys = [f for f in self._BAHMNI_DISCOUNT_WRITE_FIELDS if f in vals]
        if discount_keys:
            restricted = Users.bahmni_is_restricted_cashier(
                'bahmni_sale.group_allow_apply_discount')
            if restricted:
                # Invoice line create recalculates monetary discount for percentage SOs.
                allowed_recalc = (
                    discount_keys == ['discount']
                    and all(inv.discount_type in ('fixed', 'percentage') for inv in self)
                )
                if not allowed_recalc:
                    raise UserError(_(
                        "Cashiers are not allowed to apply or change discounts. "
                        "Ask a sales manager if a discount is required."
                    ))
        return super(AccountInvoice, self).write(vals)

    @api.multi
    def action_invoice_cancel(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_invoice_refund',
            _("Cashiers are not allowed to cancel invoices. "
              "Ask a manager if cancellation is required."),
        )
        return super(AccountInvoice, self).action_invoice_cancel()

    @api.model
    def _prepare_refund(self, invoice, date_invoice=None, date=None, description=None, journal_id=None):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_invoice_refund',
            _("Cashiers are not allowed to refund payments or create credit notes. "
              "Ask a manager if a refund is required."),
        )
        return super(AccountInvoice, self)._prepare_refund(
            invoice, date_invoice=date_invoice, date=date,
            description=description, journal_id=journal_id,
        )

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        result = super(AccountInvoice, self).fields_view_get(
            view_id, view_type, toolbar=toolbar, submenu=submenu)
        if view_type != 'form':
            return result
        Users = self.env['res.users']
        doc = etree.XML(result['arch'])
        if Users.bahmni_is_restricted_cashier('bahmni_sale.group_allow_invoice_refund'):
            refund_action = self.env.ref(
                'account.action_account_invoice_refund', raise_if_not_found=False)
            refund_action_id = str(refund_action.id) if refund_action else None
            for node in doc.xpath("//button"):
                name = node.get('name') or ''
                string = (node.get('string') or '').lower()
                if name == 'action_invoice_cancel' or 'refund' in string or (
                        refund_action_id and name == refund_action_id):
                    node.set('invisible', '1')
                    setup_modifiers(node)
        if Users.bahmni_is_restricted_cashier('bahmni_sale.group_allow_apply_discount'):
            for fname in ('discount_type', 'discount_percentage', 'discount', 'disc_acc_id'):
                for node in doc.xpath("//field[@name='%s']" % fname):
                    node.set('readonly', '1')
                    if fname in result.get('fields', {}):
                        setup_modifiers(node, result['fields'][fname])
        if Users.bahmni_is_restricted_cashier():
            for fname in self._BAHMNI_CASHIER_LOCKED_FIELDS:
                for node in doc.xpath("//field[@name='%s']" % fname):
                    node.set('readonly', '1')
                    if fname == 'shop_id':
                        # Block Accounting → Invoice → Shop → Edit related form.
                        node.set(
                            'options',
                            "{'no_open': True, 'no_create': True, 'no_create_edit': True}",
                        )
                    if fname in result.get('fields', {}):
                        setup_modifiers(node, result['fields'][fname])
        result['arch'] = etree.tostring(doc)
        return result
