# -*- coding: utf-8 -*-
from odoo import models, fields, api
import odoo.addons.decimal_precision as dp


class StockPackOperationLot(models.Model):
    _inherit = 'stock.pack.operation.lot'

    @api.model
    def default_get(self, fields):
        res = {}
        ctx = self._context.copy()
        operation_id = ctx.get('operation_id')
        # get cost price of product from purchase order line, linked with stock_move
        if operation_id:
            pack_operation = self.env['stock.pack.operation'].browse(operation_id)
            mv_op_link_ids = self.env['stock.move.operation.link'].search([('operation_id', '=', operation_id)],limit=1)
    
            if mv_op_link_ids:
                for link in mv_op_link_ids:
                    res.update({'move_id': link.move_id.id})
                    purchase_line = link.move_id.purchase_line_id
                    res.update({'cost_price': purchase_line.price_unit})
        # calculate sale price, based on markup percentage defined in price markup table
        if res.get('cost_price'):
            cost_price = res.get('cost_price')
            markup_table_line = self.env['price.markup.table'].search([('lower_price', '<', cost_price),
                                                                       '|', ('higher_price', '>=', cost_price),
                                                                       ('higher_price', '=', 0)])
            if markup_table_line and len(markup_table_line)==1:
                res.update({'sale_price': cost_price + (cost_price * markup_table_line.markup_percentage / 100)})
        return res

    sale_price = fields.Float(string="Sale Price", digits=dp.get_precision('Product Price'))
    cost_price = fields.Float(string="Cost Price", digits=dp.get_precision('Product Price'))
    mrp = fields.Float(string="MRP", digits=dp.get_precision('Product Price'))
    expiry_date = fields.Date(string="Expiry Date", digits=dp.get_precision('Product Price'))
    move_id = fields.Many2one('stock.move',
                              string="Stock Move",
                              help="This field is used to track, which all move_ids are utilized to fetch cost_price")
                              
    @api.onchange('cost_price')
    def onchange_cost_price(self):
        for record in self:
            if record.cost_price:
                markup_table_line = self.env['price.markup.table'].search([('lower_price', '<', record.cost_price),
                                                                       '|', ('higher_price', '>=', record.cost_price),
                                                                       ('higher_price', '=', 0)])
                if markup_table_line and len(markup_table_line)==1:
                    self.sale_price =  record.cost_price + (record.cost_price * markup_table_line.markup_percentage / 100)
