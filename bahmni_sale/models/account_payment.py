# -*- coding: utf-8 -*-
from odoo import api, models, _
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def _bahmni_assert_not_bypassing_ipd_deposit(self, payment):
        """Cash IPD invoices must be settled from deposit (top-up then allocate), not ad-hoc cash."""
        if self.env.context.get('bahmni_ipd_deposit_allocation'):
            return
        if payment.payment_type != 'inbound' or payment.partner_type != 'customer':
            return
        invoices = payment.invoice_ids
        if not invoices and hasattr(payment, 'invoice_ids'):
            return
        for invoice in invoices:
            sale_orders = invoice.mapped('invoice_line_ids.sale_line_ids.order_id')
            for order in sale_orders:
                if order._bahmni_is_cash_ipd_order():
                    raise UserError(_(
                        "Cash IPD invoice '%s' must be paid from the patient IPD deposit. "
                        "Collect an exact shortfall top-up on the patient, then confirm/"
                        "allocate deposit. Direct cash Register Payment is blocked."
                    ) % (invoice.number or invoice.id))

    def _bahmni_assert_not_manual_credit_payment(self, payment):
        """Credit invoices may only be settled via Excel reconciliation upload."""
        if self.env.context.get('bahmni_credit_settlement_reconcile'):
            return
        if payment.payment_type != 'inbound' or payment.partner_type != 'customer':
            return
        for invoice in payment.invoice_ids:
            if (invoice.payment_method or '').strip().lower() == 'credit':
                raise UserError(_(
                    "Invoice '%s' is a Credit bill. Register Payment is not allowed. "
                    "Settle credit invoices with Accounting → Credit Settlement "
                    "Reconciliation (Excel upload)."
                ) % (invoice.number or invoice.id))

    @api.model
    def default_get(self, fields_list):
        """Block opening Register Payment for Credit invoices (SO confirm / invoice button)."""
        rec = super(AccountPayment, self).default_get(fields_list)
        if self.env.context.get('bahmni_credit_settlement_reconcile'):
            return rec
        invoices = self.env['account.invoice']
        if self.env.context.get('active_model') == 'account.invoice':
            invoices |= self.env['account.invoice'].browse(
                self.env.context.get('active_ids') or [])
        # default_invoice_ids from SO confirm: [(4, id, None), ...]
        for cmd in (self.env.context.get('default_invoice_ids') or []):
            if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 4:
                invoices |= self.env['account.invoice'].browse(cmd[1])
        for invoice in invoices:
            if (invoice.payment_method or '').strip().lower() == 'credit':
                raise UserError(_(
                    "Invoice '%s' is a Credit bill. Register Payment is not allowed. "
                    "Settle credit invoices with Accounting → Credit Settlement "
                    "Reconciliation (Excel upload)."
                ) % (invoice.number or invoice.id))
        return rec

    @api.model
    def create(self, vals):
        vals = dict(vals or {})
        if vals.get('payment_type') == 'outbound' and vals.get('partner_type') == 'customer':
            self.env['res.users'].bahmni_cashier_raise_if_restricted(
                'bahmni_sale.group_allow_invoice_refund',
                _("Cashiers are not allowed to refund payments. "
                  "Ask a manager if a refund is required."),
            )
        payment = super(AccountPayment, self).create(vals)
        self._bahmni_assert_not_manual_credit_payment(payment)
        return payment

    @api.multi
    def write(self, vals):
        for payment in self:
            payment_type = vals.get('payment_type', payment.payment_type)
            partner_type = vals.get('partner_type', payment.partner_type)
            if payment_type == 'outbound' and partner_type == 'customer':
                self.env['res.users'].bahmni_cashier_raise_if_restricted(
                    'bahmni_sale.group_allow_invoice_refund',
                    _("Cashiers are not allowed to refund payments. "
                      "Ask a manager if a refund is required."),
                )
                break
        res = super(AccountPayment, self).write(vals)
        if 'invoice_ids' in (vals or {}):
            for payment in self:
                self._bahmni_assert_not_manual_credit_payment(payment)
        return res

    @api.multi
    def cancel(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_invoice_refund',
            _("Cashiers are not allowed to cancel payments. "
              "Ask a manager if cancellation is required."),
        )
        return super(AccountPayment, self).cancel()

    @api.multi
    def post(self):
        for payment in self:
            if payment.payment_type == 'outbound' and payment.partner_type == 'customer':
                self.env['res.users'].bahmni_cashier_raise_if_restricted(
                    'bahmni_sale.group_allow_invoice_refund',
                    _("Cashiers are not allowed to refund payments. "
                      "Ask a manager if a refund is required."),
                )
            self._bahmni_assert_not_bypassing_ipd_deposit(payment)
            self._bahmni_assert_not_manual_credit_payment(payment)
        return super(AccountPayment, self).post()


class AccountInvoiceRefund(models.TransientModel):
    _inherit = 'account.invoice.refund'

    @api.multi
    def invoice_refund(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_invoice_refund',
            _("Cashiers are not allowed to refund payments or create credit notes. "
              "Ask a manager if a refund is required."),
        )
        return super(AccountInvoiceRefund, self).invoice_refund()
