# -*- coding: utf-8 -*-
from odoo import api, models, tools


# Menus restricted cashiers must not see. Paid Payments Export is intentionally
# left off this list (Accounting → Reports → Paid Payments Export).
BAHMNI_CASHIER_HIDDEN_MENU_XMLIDS = (
    # Customers
    'sales_team.menu_partner_form',
    'account.menu_account_customer',
    # Products (Sales + Accounting)
    'sale.menu_product_template_action',
    'sale.menu_products',
    'account.menu_product_template_action',
    # Accounting Purchases branch + Purchases app
    'account.menu_finance_payables',
    'purchase.menu_purchase_root',
    # Accounting Configuration
    'account.menu_finance_configuration',
    # Accounting reports except Paid Payments Export
    'account.account_reports_business_intelligence_menu',
    'account.menu_finance_legal_statement',
    'account.menu_action_account_invoice_report_all',
    'bahmni_account.menu_action_search_account_reports',
    'bahmni_account.menu_action_search_account_count_reports',
    'bahmni_sale.menu_bahmni_cbhi_claim_export',
    'bahmni_sale.menu_bahmni_cbhi_claim_export_sales',
    # Credit settlement (also gated by group; hide if cashier somehow has group)
    'bahmni_sale.menu_bahmni_credit_settlement_wizard',
    'bahmni_sale.menu_bahmni_credit_settlement_log',
    # Shop configuration
    'bahmni_sale.menu_action_shop_form',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    @tools.ormcache('frozenset(self.env.user.groups_id.ids)', 'debug')
    def _visible_menu_ids(self, debug=False):
        visible = super(IrUiMenu, self)._visible_menu_ids(debug=debug)
        if not self.env['res.users'].bahmni_is_restricted_cashier():
            return visible
        # full_list avoids re-entering _visible_menu_ids via child_id search.
        Menu = self.with_context({'ir.ui.menu.full_list': True}).sudo()
        hidden_ids = set()
        for xmlid in BAHMNI_CASHIER_HIDDEN_MENU_XMLIDS:
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if not menu:
                continue
            menu = Menu.browse(menu.id)
            hidden_ids.add(menu.id)
            hidden_ids.update(menu.child_id.ids)
        if not hidden_ids:
            return visible
        return visible - hidden_ids
