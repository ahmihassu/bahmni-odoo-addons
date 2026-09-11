# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Shop name → OpenMRS/Bahmni order type name (exact match required by atom feed).
DEFAULT_SHOP_ORDER_TYPES = (
    ('MRU', 'Registration Fee'),
    ('Laboratory', 'Laboratory Order'),
    ('Radiology', 'Radiology Order'),
    ('Procedure', 'Procedure Order'),
)


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
        term = PaymentTerm.search([('name', 'ilike', 'Immediate')], limit=1)
        if term:
            return term
        _logger.info("Creating Immediate Payment payment term")
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

    @api.model
    def _get_company_warehouse(self, company):
        Warehouse = self.env['stock.warehouse']
        warehouse = Warehouse.search([('company_id', '=', company.id)], order='id', limit=1)
        if not warehouse:
            warehouse = Warehouse.search([], order='id', limit=1)
        return warehouse

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
    def ensure_default_shops_and_order_types(self):
        """Create missing order types, shops, and default shop maps (idempotent)."""
        self.ensure_payment_attribute_whitelist()
        payment_term = self._get_immediate_payment_term()
        OrderType = self.env['order.type']
        SaleShop = self.env['sale.shop']
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

                shop = SaleShop.search([
                    ('name', '=', shop_name),
                    '|',
                    ('company_id', '=', company.id),
                    ('company_id', '=', False),
                ], limit=1)
                if not shop:
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
                elif not shop.company_id:
                    shop.company_id = company.id

                # Default map: order type with no OpenMRS location filter.
                # Only one such map should exist per order type (atom feed takes the first).
                existing_map = OrderTypeShopMap.search([
                    ('order_type', '=', order_type.id),
                    ('location_name', '=', False),
                ], limit=1)
                if not existing_map:
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

        return True
