# -*- coding: utf-8 -*-
from odoo import fields, models, api


class SaleShop(models.Model):
    _name = "sale.shop"
    _description = "Sales Shop"

    name = fields.Char('Shop Name', size=64, required=True)
    warehouse_id = fields.Many2one('stock.warehouse', 'Warehouse')
    location_id = fields.Many2one('stock.location', 'Location')
    payment_default_id = fields.Many2one('account.payment.term', 'Default Payment Term', required=True)
    pricelist_id = fields.Many2one('product.pricelist', 'Pricelist')
    project_id = fields.Many2one('account.analytic.account', 'Analytic Account')#domain=[('parent_id', '!=', False)]
    income_account_id = fields.Many2one(
        'account.account',
        string='Income Account',
        domain="[('deprecated', '=', False)]",
        help="Income (revenue) account used on customer invoice lines for this shop. "
             "When set, it overrides the product/category income account.",
    )
    company_id = fields.Many2one('res.company', 'Company', required=False, default=lambda self: self.env['res.company']._company_default_get('sale.shop'))

    @api.multi
    def _bahmni_map_income_account(self, fiscal_position=None):
        """Return this shop's income account, optionally mapped via fiscal position."""
        self.ensure_one()
        account = self.income_account_id
        if account and fiscal_position:
            account = fiscal_position.map_account(account)
        return account
