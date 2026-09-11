# -*- coding: utf-8 -*-
from odoo import models, api


class AccountInvoiceLine(models.Model):
    _inherit = 'account.invoice.line'

    @api.onchange('product_id')
    def _onchange_product_id(self):
        res = super(AccountInvoiceLine, self)._onchange_product_id()
        shop = self.invoice_id.shop_id
        if shop and shop.income_account_id:
            account = shop._bahmni_map_income_account(self.invoice_id.fiscal_position_id)
            if account:
                self.account_id = account
        return res
