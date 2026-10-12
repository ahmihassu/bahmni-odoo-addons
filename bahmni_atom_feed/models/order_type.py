# -*- coding: utf-8 -*-
from odoo import models, fields, api


class OrderType(models.Model):
    _name = 'order.type'

    name = fields.Char(string='Name')

    _sql_constraints = [('unique_name', 'unique(name)',
                         'Order type with this name already exists!')]

    @api.model
    def create(self, vals):
        """Skip insert when an order type with the same name already exists.

        Module data / seeding may create rows by name without XML IDs; later
        upgrades that load order_type_data.xml must reuse those rows instead of
        hitting order_type_unique_name.
        """
        name = vals.get('name')
        if name:
            existing = self.search([('name', '=', name)], limit=1)
            if existing:
                return existing
        return super(OrderType, self).create(vals)
