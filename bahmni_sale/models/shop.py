# -*- coding: utf-8 -*-
from lxml import etree

from odoo import fields, models, api, _
from odoo.exceptions import AccessError
from odoo.osv.orm import setup_modifiers


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

    def _bahmni_raise_if_cashier_edits_shop(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            None,
            _("Cashiers are not allowed to create or edit shops."),
        )

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        """Deny write/create/unlink for restricted cashiers even if ACL is stale."""
        if operation != 'read' and self.env['res.users'].bahmni_is_restricted_cashier():
            if raise_exception:
                raise AccessError(_(
                    "Cashiers are not allowed to create or edit shops."
                ))
            return False
        return super(SaleShop, self).check_access_rights(
            operation, raise_exception=raise_exception)

    @api.model
    def create(self, vals):
        self._bahmni_raise_if_cashier_edits_shop()
        return super(SaleShop, self).create(vals)

    @api.multi
    def write(self, vals):
        self._bahmni_raise_if_cashier_edits_shop()
        return super(SaleShop, self).write(vals)

    @api.multi
    def unlink(self):
        self._bahmni_raise_if_cashier_edits_shop()
        return super(SaleShop, self).unlink()

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        result = super(SaleShop, self).fields_view_get(
            view_id, view_type, toolbar=toolbar, submenu=submenu)
        if not self.env['res.users'].bahmni_is_restricted_cashier():
            return result
        doc = etree.XML(result['arch'])
        if view_type == 'form':
            for form in doc.xpath('//form'):
                form.set('create', 'false')
                form.set('edit', 'false')
                form.set('delete', 'false')
            for node in doc.xpath('//field'):
                node.set('readonly', '1')
                fname = node.get('name')
                if fname and fname in result.get('fields', {}):
                    setup_modifiers(node, result['fields'][fname])
        elif view_type == 'tree':
            for tree in doc.xpath('//tree'):
                tree.set('create', 'false')
                tree.set('edit', 'false')
                tree.set('delete', 'false')
        result['arch'] = etree.tostring(doc)
        return result

    @api.multi
    def _bahmni_map_income_account(self, fiscal_position=None):
        """Return this shop's income account, optionally mapped via fiscal position."""
        self.ensure_one()
        account = self.income_account_id
        if account and fiscal_position:
            account = fiscal_position.map_account(account)
        return account
