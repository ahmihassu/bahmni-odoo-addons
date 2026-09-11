# -*- coding: utf-8 -*-
from odoo import models, fields


class ResUsers(models.Model):
    _inherit = 'res.users'

    shop_ids = fields.Many2many(
        'sale.shop',
        'res_users_sale_shop_rel',
        'user_id',
        'shop_id',
        string='Shops',
        help="Shops this user may access when in group "
             "'Cashier: Own Shop Orders Only'.",
    )
    shop_id = fields.Many2one(
        'sale.shop',
        string='Default Shop',
        help="Default shop on new sale orders. Should be one of Shops.",
    )
