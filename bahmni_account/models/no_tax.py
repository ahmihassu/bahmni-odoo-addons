# -*- coding: utf-8 -*-
"""Keep customer, vendor, and account taxes empty on create and write.

Posted invoices are left unchanged so historical journal entries stay balanced.
"""
from odoo import api, models

_NO_TAX = [(6, 0, [])]


class AccountAccount(models.Model):
    _inherit = 'account.account'

    @api.model
    def create(self, vals):
        vals = dict(vals, tax_ids=_NO_TAX)
        return super(AccountAccount, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals, tax_ids=_NO_TAX)
        return super(AccountAccount, self).write(vals)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def create(self, vals):
        vals = dict(vals, taxes_id=_NO_TAX, supplier_taxes_id=_NO_TAX)
        return super(ProductTemplate, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals, taxes_id=_NO_TAX, supplier_taxes_id=_NO_TAX)
        return super(ProductTemplate, self).write(vals)
