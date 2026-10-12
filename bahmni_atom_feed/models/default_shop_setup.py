# -*- coding: utf-8 -*-
import logging

from psycopg2 import IntegrityError

from odoo import api, models, tools

_logger = logging.getLogger(__name__)

# Shop name → OpenMRS/Bahmni order type name (exact match required by atom feed).
DEFAULT_SHOP_ORDER_TYPES = (
    ('MRU', 'Registration Fee'),
    ('Laboratory', 'Lab Order'),
    ('Radiology', 'Radiology Order'),
    ('Procedure', 'Procedure Order'),
    ('IPD', 'Bed Service'),
)

# XML IDs from order_type_data.xml — kept in sync so upgrades bind existing rows.
ORDER_TYPE_XMLIDS = (
    ('order_type_registration_fee', 'Registration Fee'),
    ('order_type_laboratory_order', 'Lab Order'),
    ('order_type_radiology_order', 'Radiology Order'),
    ('order_type_procedure_order', 'Procedure Order'),
    ('order_type_bed_service', 'Bed Service'),
)

# Shop name → (account code, account name). Codes use 6 digits for Ethiopia CoA.
# Sub-accounts under 110000 Sales of Goods and Services.
DEFAULT_SHOP_INCOME_ACCOUNTS = {
    'MRU': ('110100', 'MRU / Registration Income'),
    'Laboratory': ('110200', 'Laboratory Income'),
    'Radiology': ('110300', 'Radiology Income'),
    'Procedure': ('110400', 'Procedure Income'),
    'IPD': ('110500', 'Bed / IPD Income'),
}


class DefaultShopSetup(models.Model):
    """Ensure default shops, order types, and mappings exist for each company."""

    _name = 'bahmni.default.shop.setup'
    _description = 'Bahmni Default Shop and Order Type Setup'
    _auto = False

    @api.model
    def _get_immediate_payment_term(self):
        term = self.env.ref('account.account_payment_term_immediate', raise_if_not_found=False)
        if term:
            return term
        PaymentTerm = self.env['account.payment.term']
        term = PaymentTerm.search([('name', '=', 'Immediate Payment')], limit=1)
        if term:
            return term
        term = PaymentTerm.search([('name', 'ilike', 'Immediate')], limit=1)
        if term:
            return term
        _logger.info("Creating Immediate Payment payment term")
        try:
            with self.env.cr.savepoint(), tools.mute_logger('odoo.sql_db'):
                return PaymentTerm.create({
                    'name': 'Immediate Payment',
                    'note': 'Immediate Payment',
                    'line_ids': [(0, 0, {
                        'value': 'balance',
                        'value_amount': 0.0,
                        'sequence': 500,
                        'days': 0,
                        'option': 'day_after_invoice_date',
                    })],
                })
        except IntegrityError:
            term = PaymentTerm.search([('name', '=', 'Immediate Payment')], limit=1)
            if term:
                return term
            raise

    @api.model
    def _get_company_warehouse(self, company):
        Warehouse = self.env['stock.warehouse']
        warehouse = Warehouse.search([('company_id', '=', company.id)], order='id', limit=1)
        if not warehouse:
            warehouse = Warehouse.search([], order='id', limit=1)
        return warehouse

    @api.model
    def _ensure_shop_income_account(self, shop, company):
        """Create/find a dedicated income account for the shop and link it if unset.

        Requires a loaded CoA. Does not overwrite an income account already set on the shop.
        """
        if shop.income_account_id:
            return shop.income_account_id
        if not company.chart_template_id:
            _logger.info(
                "Skip income account for shop '%s': company '%s' has no chart of accounts",
                shop.name,
                company.name,
            )
            return self.env['account.account']

        mapping = DEFAULT_SHOP_INCOME_ACCOUNTS.get(shop.name)
        if not mapping:
            return self.env['account.account']

        code, name = mapping
        Account = self.env['account.account'].sudo()
        account = Account.search([
            ('code', '=', code),
            ('company_id', '=', company.id),
        ], limit=1)
        if not account:
            revenue_type = self.env.ref(
                'account.data_account_type_revenue', raise_if_not_found=False)
            if not revenue_type:
                _logger.warning(
                    "Cannot create income account %s: revenue account type missing", code)
                return self.env['account.account']
            try:
                with self.env.cr.savepoint(), tools.mute_logger('odoo.sql_db'):
                    account = Account.create({
                        'code': code,
                        'name': name,
                        'user_type_id': revenue_type.id,
                        'reconcile': False,
                        'company_id': company.id,
                    })
                _logger.info(
                    "Created income account %s %s for shop '%s'",
                    code, name, shop.name,
                )
            except IntegrityError:
                account = Account.search([
                    ('code', '=', code),
                    ('company_id', '=', company.id),
                ], limit=1)
                if not account:
                    raise
                _logger.info(
                    "Income account %s already exists for company '%s'; reusing",
                    code, company.name,
                )
        shop.income_account_id = account.id
        _logger.info(
            "Linked shop '%s' → income account %s %s",
            shop.name, account.code, account.name,
        )
        return account

    @api.model
    def ensure_payment_attribute_whitelist(self):
        """Merge payment-related OpenMRS attributes into the sync whitelist.

        The config parameter is noupdate=1, so upgrades must merge explicitly
        without wiping any facility-specific extras already configured.
        """
        from odoo.addons.bahmni_atom_feed.models.payment_attributes import PAYMENT_PERSON_ATTRIBUTES

        param = self.env.ref('bahmni_atom_feed.openmrs_patient_attributes', raise_if_not_found=False)
        if not param:
            _logger.warning("openmrs_patient_attributes config parameter not found")
            return True
        current = [s.strip() for s in (param.value or '').split(',') if s.strip()]
        updated = False
        for attr in PAYMENT_PERSON_ATTRIBUTES:
            if attr not in current:
                current.append(attr)
                updated = True
        if updated:
            param.write({'value': ','.join(current)})
            _logger.info("Updated openmrs_patient_attributes whitelist: %s", param.value)
        return True

    @api.model
    def _rename_legacy_lab_order_type(self):
        """OpenMRS sends 'Lab Order'; older seeds used 'Laboratory Order'."""
        OrderType = self.env['order.type']
        legacy = OrderType.search([('name', '=', 'Laboratory Order')], limit=1)
        if not legacy:
            return
        current = OrderType.search([('name', '=', 'Lab Order')], limit=1)
        if current and current.id != legacy.id:
            # Prefer keeping Lab Order; move maps off the legacy type then remove it.
            self.env['order.type.shop.map'].search([
                ('order_type', '=', legacy.id),
            ]).write({'order_type': current.id})
            legacy.unlink()
            _logger.info("Removed legacy order type 'Laboratory Order' (maps moved to 'Lab Order')")
        else:
            legacy.write({'name': 'Lab Order'})
            _logger.info("Renamed order type 'Laboratory Order' → 'Lab Order'")

    @api.model
    def _bind_xmlid(self, xmlid_name, record):
        """Ensure bahmni_atom_feed.<xmlid_name> points at record (no-op if bound)."""
        Imd = self.env['ir.model.data']
        existing = Imd.search([
            ('module', '=', 'bahmni_atom_feed'),
            ('name', '=', xmlid_name),
        ], limit=1)
        if existing:
            if existing.model != record._name or existing.res_id != record.id:
                existing.write({'model': record._name, 'res_id': record.id, 'noupdate': True})
            return existing
        return Imd.create({
            'name': xmlid_name,
            'module': 'bahmni_atom_feed',
            'model': record._name,
            'res_id': record.id,
            'noupdate': True,
        })

    @api.model
    def ensure_order_types(self):
        """Create missing order types and bind XML IDs. Safe to run on every upgrade."""
        self._rename_legacy_lab_order_type()
        OrderType = self.env['order.type']
        for xmlid_name, name in ORDER_TYPE_XMLIDS:
            order_type = self.env.ref(
                'bahmni_atom_feed.%s' % xmlid_name, raise_if_not_found=False)
            if order_type:
                if order_type.name != name:
                    clash = OrderType.search([
                        ('name', '=', name),
                        ('id', '!=', order_type.id),
                    ], limit=1)
                    if clash:
                        _logger.info(
                            "Order type XML ID %s already bound; name '%s' exists as id=%s — skipping rename",
                            xmlid_name, name, clash.id,
                        )
                    else:
                        order_type.write({'name': name})
                continue

            order_type = OrderType.search([('name', '=', name)], limit=1)
            if order_type:
                _logger.info("Reusing existing order type '%s' (id=%s)", name, order_type.id)
            else:
                order_type = OrderType.create({'name': name})
                _logger.info("Created order type '%s'", name)
            self._bind_xmlid(xmlid_name, order_type)
        return True

    @api.model
    def _find_or_create_shop(self, shop_name, company, warehouse, payment_term):
        SaleShop = self.env['sale.shop']
        shop = SaleShop.search([
            ('name', '=', shop_name),
            '|',
            ('company_id', '=', company.id),
            ('company_id', '=', False),
        ], limit=1)
        if shop:
            if not shop.company_id:
                shop.company_id = company.id
            _logger.info(
                "Reusing existing shop '%s' for company '%s'",
                shop_name, company.name,
            )
            return shop
        try:
            with self.env.cr.savepoint(), tools.mute_logger('odoo.sql_db'):
                shop = SaleShop.create({
                    'name': shop_name,
                    'warehouse_id': warehouse.id,
                    'location_id': warehouse.lot_stock_id.id,
                    'payment_default_id': payment_term.id,
                    'company_id': company.id,
                })
            _logger.info(
                "Created shop '%s' for company '%s'",
                shop_name,
                company.name,
            )
            return shop
        except IntegrityError:
            shop = SaleShop.search([
                ('name', '=', shop_name),
                '|',
                ('company_id', '=', company.id),
                ('company_id', '=', False),
            ], limit=1)
            if shop:
                _logger.info(
                    "Shop '%s' already exists for company '%s'; skipping create",
                    shop_name, company.name,
                )
                return shop
            raise

    @api.model
    def ensure_default_shops_and_order_types(self):
        """Create missing order types, shops, and default shop maps (idempotent)."""
        self.ensure_payment_attribute_whitelist()
        self.ensure_order_types()
        payment_term = self._get_immediate_payment_term()
        OrderType = self.env['order.type']
        OrderTypeShopMap = self.env['order.type.shop.map']

        for company in self.env['res.company'].search([]):
            warehouse = self._get_company_warehouse(company)
            if not warehouse:
                _logger.warning(
                    "Skipping default shop setup for company '%s': no warehouse found",
                    company.name,
                )
                continue
            if not warehouse.lot_stock_id:
                _logger.warning(
                    "Skipping default shop setup for company '%s': warehouse '%s' has no stock location",
                    company.name,
                    warehouse.name,
                )
                continue

            for shop_name, order_type_name in DEFAULT_SHOP_ORDER_TYPES:
                order_type = OrderType.search([('name', '=', order_type_name)], limit=1)
                if not order_type:
                    order_type = OrderType.create({'name': order_type_name})
                    _logger.info("Created order type '%s'", order_type_name)

                shop = self._find_or_create_shop(
                    shop_name, company, warehouse, payment_term)

                self._ensure_shop_income_account(shop, company)

                # Default map: order type with no OpenMRS location filter.
                # Only one such map should exist per order type (atom feed takes the first).
                existing_map = OrderTypeShopMap.search([
                    ('order_type', '=', order_type.id),
                    ('location_name', '=', False),
                ], limit=1)
                if not existing_map:
                    try:
                        with self.env.cr.savepoint(), tools.mute_logger('odoo.sql_db'):
                            OrderTypeShopMap.create({
                                'order_type': order_type.id,
                                'shop_id': shop.id,
                                'location_id': shop.location_id.id,
                            })
                        _logger.info(
                            "Mapped order type '%s' → shop '%s'",
                            order_type_name,
                            shop_name,
                        )
                    except IntegrityError:
                        _logger.info(
                            "Order type map for '%s' already exists; skipping",
                            order_type_name,
                        )

        self.ensure_demo_cashiers()
        return True

    @api.model
    def ensure_demo_cashiers(self):
        """Create standard cashier users (single- and multi-shop). Idempotent."""
        Users = self.env['res.users'].sudo()
        Shop = self.env['sale.shop'].sudo()
        group_cashier = self.env.ref(
            'bahmni_sale.group_cashier_own_shop', raise_if_not_found=False)
        group_auto_inv = self.env.ref(
            'bahmni_sale.group_skip_invoice_options', raise_if_not_found=False)
        group_billing = self.env.ref(
            'account.group_account_invoice', raise_if_not_found=False)
        if not group_cashier:
            _logger.warning("Cashier shop group missing; skip demo cashier creation")
            return True

        # login, display name, shop name list
        cashiers = (
            ('cashier_mru', 'MRU Cashier', ('MRU',)),
            ('cashier_lab', 'Laboratory Cashier', ('Laboratory',)),
            ('cashier_radiology', 'Radiology Cashier', ('Radiology',)),
            ('cashier_procedure', 'Procedure Cashier', ('Procedure',)),
            ('cashier_ipd', 'IPD Cashier', ('IPD',)),
            ('cashier_lab_rad', 'Lab + Radiology Cashier', ('Laboratory', 'Radiology')),
            ('cashier_clinical', 'Clinical Shops Cashier',
             ('Laboratory', 'Radiology', 'Procedure')),
            ('cashier_front', 'Front Desk Cashier', ('MRU', 'Laboratory', 'IPD')),
        )
        password = 'Cashier@123'
        group_ids = [group_cashier.id]
        if group_auto_inv:
            group_ids.append(group_auto_inv.id)
        if group_billing:
            group_ids.append(group_billing.id)

        for login, name, shop_names in cashiers:
            shops = Shop.search([('name', 'in', list(shop_names))])
            if not shops:
                _logger.warning(
                    "Skipping cashier '%s': shops %s not found", login, shop_names)
                continue
            # Preserve shop order from config as much as possible
            ordered = self.env['sale.shop']
            for shop_name in shop_names:
                match = shops.filtered(lambda s: s.name == shop_name)
                ordered |= match

            user = Users.search([('login', '=', login)], limit=1)
            vals = {
                'name': name,
                'shop_ids': [(6, 0, ordered.ids)],
                'shop_id': ordered[0].id,
            }
            if user:
                # Do not reset password on every upgrade
                user.write(vals)
                user.write({'groups_id': [(4, gid) for gid in group_ids]})
                _logger.info("Updated cashier user '%s' shops=%s", login, shop_names)
            else:
                vals.update({
                    'login': login,
                    'password': password,
                    'groups_id': [(6, 0, group_ids)],
                })
                Users.create(vals)
                _logger.info("Created cashier user '%s' / %s shops=%s", login, password, shop_names)

        return True
