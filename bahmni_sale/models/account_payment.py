# -*- coding: utf-8 -*-
from odoo import api, models, _
from odoo.exceptions import UserError

CREDIT_SETTLEMENT_CTX = 'bahmni_credit_settlement_reconcile'

CREDIT_BLOCKED_MSG = (
    "Invoice '%s' is a Credit bill. Register Payment is not allowed. "
    "Settle credit invoices with Accounting → Credit Settlement "
    "Reconciliation (Excel upload)."
)


def _raise_if_credit_invoices(env, invoices):
    if env.context.get(CREDIT_SETTLEMENT_CTX):
        return
    for invoice in invoices:
        if invoice.bahmni_is_credit:
            raise UserError(_(CREDIT_BLOCKED_MSG) % (invoice.number or invoice.id))


def _invoices_from_context(env):
    """Invoices targeted by a payment wizard opened from an invoice (form or list)."""
    Invoice = env['account.invoice']
    invoices = Invoice
    if env.context.get('active_model') == 'account.invoice':
        invoices |= Invoice.browse(env.context.get('active_ids') or [])
    for cmd in (env.context.get('default_invoice_ids') or []):
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2 and cmd[0] == 4:
            invoices |= Invoice.browse(cmd[1])
        elif isinstance(cmd, (list, tuple)) and len(cmd) == 3 and cmd[0] == 6:
            invoices |= Invoice.browse(cmd[2] or [])
    return invoices.exists()


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
        """Credit invoices and payer partners may only be settled via Excel reconciliation."""
        if self.env.context.get(CREDIT_SETTLEMENT_CTX):
            return
        if payment.payment_type != 'inbound' or payment.partner_type != 'customer':
            return
        _raise_if_credit_invoices(self.env, payment.invoice_ids)
        if payment.partner_id.is_bahmni_payer:
            raise UserError(_(
                "'%s' is a Credit payer. Payments from payers can only be recorded "
                "through Accounting → Credit Settlement Reconciliation (Excel upload)."
            ) % payment.partner_id.display_name)

    @api.model
    def default_get(self, fields_list):
        _raise_if_credit_invoices(self.env, _invoices_from_context(self.env))
        return super(AccountPayment, self).default_get(fields_list)

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
        if 'invoice_ids' in (vals or {}) or 'partner_id' in (vals or {}):
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


class AccountRegisterPayments(models.TransientModel):
    """Multi-invoice Register Payment (invoice list → Action → Register Payment)."""

    _inherit = 'account.register.payments'

    @api.model
    def default_get(self, fields_list):
        _raise_if_credit_invoices(self.env, _invoices_from_context(self.env))
        return super(AccountRegisterPayments, self).default_get(fields_list)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.multi
    def reconcile(self, writeoff_acc_id=False, writeoff_journal_id=False):
        """Block manual/bank/outstanding-credit reconciliation against Credit bills."""
        if not self.env.context.get(CREDIT_SETTLEMENT_CTX) and \
                not self.env.context.get('bahmni_credit_refund'):
            credit_invoices = self.filtered(
                lambda l: l.invoice_id and l.account_id.internal_type == 'receivable'
            ).mapped('invoice_id')
            _raise_if_credit_invoices(self.env, credit_invoices)
        return super(AccountMoveLine, self).reconcile(
            writeoff_acc_id=writeoff_acc_id, writeoff_journal_id=writeoff_journal_id)


class AccountInvoiceRefund(models.TransientModel):
    _inherit = 'account.invoice.refund'

    @api.multi
    def invoice_refund(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_invoice_refund',
            _("Cashiers are not allowed to refund payments or create credit notes. "
              "Ask a manager if a refund is required."),
        )
        # Credit notes (cancel/modify) may still close a wrongly issued Credit bill.
        return super(AccountInvoiceRefund, self.with_context(
            bahmni_credit_refund=True)).invoice_refund()
