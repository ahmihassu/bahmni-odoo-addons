# -*- coding: utf-8 -*-
from datetime import datetime
from odoo import models, fields, api
from odoo.tools import SUPERUSER_ID, DEFAULT_SERVER_DATETIME_FORMAT as DTF


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    mrp = fields.Float(string="MRP")
    manufacturer = fields.Many2one('res.partner', string="Manufacturer",
                                   domain=[('manufacturer', '=', True)])
    prod_categ_id = fields.Many2one('product.category', string='Product Category')

    @api.depends('product_qty', 'price_unit', 'taxes_id')
    def _compute_amount(self):
        """Purchase line totals ignore tax."""
        for line in self:
            amount = line.price_unit * line.product_qty
            currency = line.order_id.currency_id or line.company_id.currency_id
            if currency:
                amount = currency.round(amount)
            line.update({
                'price_tax': 0.0,
                'price_total': amount,
                'price_subtotal': amount,
            })

    @api.model
    def create(self, vals):
        vals = dict(vals, taxes_id=[(6, 0, [])])
        return super(PurchaseOrderLine, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals)
        open_lines = self.filtered(lambda line: line.order_id.state not in ('done', 'cancel'))
        closed_lines = self - open_lines
        if open_lines:
            super(PurchaseOrderLine, open_lines).write(dict(vals, taxes_id=[(6, 0, [])]))
        if closed_lines:
            closed_vals = dict(vals)
            closed_vals.pop('taxes_id', None)
            super(PurchaseOrderLine, closed_lines).write(closed_vals)
        return True

    @api.onchange('product_id')
    def onchange_product_id(self):
        '''Set product category in purchase order line, on change of product_id'''
        result = super(PurchaseOrderLine, self).onchange_product_id()
        self.taxes_id = [(6, 0, [])]
        if self.product_id:
            self.prod_categ_id = self.product_id.categ_id.id
        return result

    @api.onchange('product_qty', 'product_uom')
    def _onchange_quantity(self):
        '''Method to get mrp for product from vendor configuration in product master'''
        if not self.product_id:
            return

        seller = self.product_id._select_seller(
            partner_id=self.partner_id,
            quantity=self.product_qty,
            date=self.order_id.date_order and self.order_id.date_order[:10],
            uom_id=self.product_uom)
        if seller or not self.date_planned:
            self.date_planned = self._get_date_planned(seller).strftime(DTF)

        if not seller:
            self.mrp = self.product_id.mrp
            return
        self.manufacturer = seller.manufacturer.id
        price_unit = seller.price if seller else 0.0
        mrp = seller.mrp if seller else 0.0
        if price_unit and seller and self.order_id.currency_id and seller.currency_id != self.order_id.currency_id:
            price_unit = seller.currency_id.compute(price_unit, self.order_id.currency_id)
        if mrp and seller and self.order_id.currency_id and seller.currency_id != self.order_id.currency_id:
            mrp = seller.currency_id.compute(mrp, self.order_id.currency_id)
        if seller and self.product_uom and seller.product_uom != self.product_uom:
            price_unit = seller.product_uom._compute_price(price_unit, self.product_uom)
        if mrp and self.product_uom and seller.product_uom != self.product_uom:
            mrp = seller.product_uom._compute_price(mrp, self.product_uom)
        self.price_unit = price_unit
        self.mrp = mrp
