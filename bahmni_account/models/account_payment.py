# -*- coding: utf-8 -*-
from itertools import groupby
from odoo import models, fields, api


class AccountAbstractPayment(models.AbstractModel):
    _inherit = 'account.abstract.payment'

# commented as per Ajeenckya's suggestion, as keeping bank journals in selection, will fulfill the generic way of using payment
#     journal_id = fields.Many2one('account.journal', string='Payment Journal', required=True,
#                                  domain=[('type', '=', 'cash')])


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.model
    def default_get(self, fields_list):
        """Prefer Cash journal for customer inbound payments (hospital cashiers)."""
        rec = super(AccountPayment, self).default_get(fields_list)
        need_journal = (not fields_list) or ('journal_id' in fields_list)
        if need_journal and not rec.get('journal_id'):
            payment_type = rec.get('payment_type') or self.env.context.get('default_payment_type')
            partner_type = rec.get('partner_type') or self.env.context.get('default_partner_type')
            if payment_type in (False, 'inbound') and partner_type in (False, 'customer'):
                cash = self.env['account.journal'].search([
                    ('type', '=', 'cash'),
                    ('company_id', '=', self.env.user.company_id.id),
                ], order='id asc', limit=1)
                if cash:
                    rec['journal_id'] = cash.id
        return rec

    @api.onchange('partner_id', 'amount')
    def _calculate_balances(self):
        if(self.state != 'posted'):
            partner = self.partner_id
            balance = partner.credit or partner.debit
            self.balance_before_pay = balance
            self.total_balance = balance - self.amount

    @api.onchange('invoice_ids')
    def onchange_partner_id(self):
        if self.invoice_ids:
            bill_amount = 0
            for inv in self.invoice_ids:
                bill_amount += inv.amount_total
            self.bill_amount = bill_amount

    @api.onchange('payment_type')
    def _onchange_payment_type(self):
        if not self.invoice_ids:
            # Set default partner type for the payment type
            if self.payment_type == 'inbound':
                self.partner_type = 'customer'
            elif self.payment_type == 'outbound':
                self.partner_type = 'supplier'
            else:
                self.partner_type = False
        # Set payment method domain
        res = self._onchange_journal()
        if not res.get('domain', {}):
            res['domain'] = {}
        res['domain']['journal_id'] = self.payment_type == 'inbound' and [('at_least_one_inbound', '=', True)] or self.payment_type == 'outbound' and [('at_least_one_outbound', '=', True)] or []
        #res['domain']['journal_id'].append(('type', '=', 'cash'))
        return res

    balance_before_pay = fields.Float(compute=_calculate_balances,
                                      string="Balance before pay")
    total_balance = fields.Float(compute=_calculate_balances,
                                 string="Total Balance")
    invoice_id = fields.Many2one('account.invoice', string='Invoice')
    bill_amount = fields.Float(string="Bill Amount")
