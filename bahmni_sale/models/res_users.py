# -*- coding: utf-8 -*-
from odoo import api, models, fields, _
from odoo.exceptions import UserError


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

    @api.model
    def bahmni_is_restricted_cashier(self, privilege_xmlid=None):
        """True when the target user is a shop cashier lacking a privilege.

        Prefer calling as ``env['res.users'].bahmni_is_restricted_cashier()`` so
        the check uses ``env.uid``. When called on a singleton user record, that
        record is checked (``has_group`` uses the record id).

        Sales managers are never treated as restricted cashiers. Other cashiers
        regain an action when they also belong to the matching allow-* group.
        """
        if self and len(self) == 1:
            user = self
        else:
            user = self.env['res.users'].browse(self.env.uid)
        if not user.has_group('bahmni_sale.group_cashier_own_shop'):
            return False
        if user.has_group('sales_team.group_sale_manager'):
            return False
        if privilege_xmlid and user.has_group(privilege_xmlid):
            return False
        return True

    @api.model
    def bahmni_cashier_raise_if_restricted(self, privilege_xmlid, message):
        if self.bahmni_is_restricted_cashier(privilege_xmlid):
            raise UserError(message)
