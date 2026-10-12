# -*- coding: utf-8 -*-
"""Keep customer, vendor, and account taxes empty across the system.

Posted invoices are left unchanged so historical journal entries stay balanced.
Open documents and every new product, account, sale, purchase, and invoice
are stored and computed with no tax.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

_NO_TAX = [(6, 0, [])]


class AccountAccount(models.Model):
    _inherit = 'account.account'

    @api.model
    def create(self, vals):
        vals = dict(vals, tax_ids=_NO_TAX)
        return super(AccountAccount, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals, tax_ids=_NO_TAX)
        return super(AccountAccount, self).write(vals)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def create(self, vals):
        vals = dict(vals, taxes_id=_NO_TAX, supplier_taxes_id=_NO_TAX)
        return super(ProductTemplate, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals, taxes_id=_NO_TAX, supplier_taxes_id=_NO_TAX)
        return super(ProductTemplate, self).write(vals)


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def bahmni_disable_taxes(self):
        """Drop taxes from master data and recompute open documents without tax."""
        cr = self.env.cr
        for table in (
            'account_account_tax_default_rel',
            'product_taxes_rel',
            'product_supplier_taxes_rel',
            'account_fiscal_position_tax',
        ):
            self._bahmni_delete_table(table)

        cr.execute("""
            DELETE FROM ir_values
            WHERE model = 'product.template'
              AND name IN ('taxes_id', 'supplier_taxes_id')
        """)
        cr.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'res_company'
              AND column_name IN ('account_sale_tax_id', 'account_purchase_tax_id')
        """)
        company_tax_cols = [row[0] for row in cr.fetchall()]
        if company_tax_cols:
            cr.execute(
                "UPDATE res_company SET %s"
                % ', '.join('%s = NULL' % col for col in company_tax_cols)
            )
        if self._bahmni_table_exists('account_tax'):
            cr.execute("UPDATE account_tax SET active = false WHERE active IS TRUE")

        self.env['product.template'].invalidate_cache()
        self.env['account.account'].invalidate_cache()
        self.env['account.tax'].invalidate_cache()
        self.env['ir.values'].invalidate_cache()

        self._bahmni_clear_open_sale_taxes()
        self._bahmni_clear_open_purchase_taxes()
        self._bahmni_clear_draft_invoice_taxes()
        self._bahmni_clear_draft_pos_taxes()
        _logger.info("Bahmni: taxes removed from accounts, products, and open documents")
        return True

    @api.model
    def _bahmni_table_exists(self, table):
        self.env.cr.execute("SELECT to_regclass(%s)", (table,))
        return bool(self.env.cr.fetchone()[0])

    @api.model
    def _bahmni_delete_table(self, table):
        if self._bahmni_table_exists(table):
            self.env.cr.execute('DELETE FROM "%s"' % table)

    @api.model
    def _bahmni_model(self, model_name):
        try:
            return self.env[model_name]
        except KeyError:
            return None

    @api.model
    def _bahmni_clear_open_sale_taxes(self):
        if not self._bahmni_table_exists('account_tax_sale_order_line_rel'):
            return
        self.env.cr.execute("""
            DELETE FROM account_tax_sale_order_line_rel rel
            USING sale_order_line sol
            JOIN sale_order so ON so.id = sol.order_id
            WHERE rel.sale_order_line_id = sol.id
              AND so.state IN ('draft', 'sent', 'sale')
              AND COALESCE(so.invoice_status, 'no') != 'invoiced'
        """)
        SaleOrder = self._bahmni_model('sale.order')
        if SaleOrder is None:
            return
        orders = SaleOrder.search([
            ('state', 'in', ('draft', 'sent', 'sale')),
            ('invoice_status', '!=', 'invoiced'),
        ])
        lines = orders.mapped('order_line')
        if lines:
            lines.invalidate_cache()
            lines._compute_amount()
        if orders:
            orders.invalidate_cache()
            orders._amount_all()

    @api.model
    def _bahmni_clear_open_purchase_taxes(self):
        if not self._bahmni_table_exists('account_tax_purchase_order_line_rel'):
            return
        self.env.cr.execute("""
            DELETE FROM account_tax_purchase_order_line_rel rel
            USING purchase_order_line pol
            JOIN purchase_order po ON po.id = pol.order_id
            WHERE rel.purchase_order_line_id = pol.id
              AND po.state NOT IN ('done', 'cancel')
        """)
        PurchaseOrder = self._bahmni_model('purchase.order')
        if PurchaseOrder is None:
            return
        orders = PurchaseOrder.search([('state', 'not in', ('done', 'cancel'))])
        lines = orders.mapped('order_line')
        if lines and hasattr(lines, '_compute_amount'):
            lines.invalidate_cache()
            lines._compute_amount()
        if orders and hasattr(orders, '_amount_all'):
            orders.invalidate_cache()
            orders._amount_all()

    @api.model
    def _bahmni_clear_draft_invoice_taxes(self):
        if not self._bahmni_table_exists('account_invoice_line_tax'):
            return
        cr = self.env.cr
        cr.execute("""
            DELETE FROM account_invoice_line_tax rel
            USING account_invoice_line ail
            JOIN account_invoice ai ON ai.id = ail.invoice_id
            WHERE rel.invoice_line_id = ail.id
              AND ai.state = 'draft'
        """)
        if self._bahmni_table_exists('account_invoice_tax'):
            cr.execute("""
                DELETE FROM account_invoice_tax
                WHERE invoice_id IN (
                    SELECT id FROM account_invoice WHERE state = 'draft'
                )
            """)
        Invoice = self._bahmni_model('account.invoice')
        if Invoice is None:
            return
        drafts = Invoice.search([('state', '=', 'draft')])
        lines = drafts.mapped('invoice_line_ids')
        if lines:
            lines.invalidate_cache()
            if hasattr(lines, '_compute_price'):
                lines._compute_price()
        if drafts:
            drafts.invalidate_cache()
            drafts.compute_taxes()

    @api.model
    def _bahmni_clear_draft_pos_taxes(self):
        PosLine = self._bahmni_model('pos.order.line')
        if PosLine is None or 'tax_ids' not in PosLine._fields:
            return
        lines = PosLine.search([('order_id.state', '=', 'draft')])
        if lines:
            lines.write({'tax_ids': _NO_TAX})
