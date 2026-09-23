# -*- coding: utf-8 -*-
from datetime import datetime, date
from lxml import etree

from odoo import fields, models, api, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DSDF
from odoo.tools import float_is_zero
from odoo.exceptions import UserError
from odoo.osv.orm import setup_modifiers
from odoo.tools import pickle
import logging
_logger = logging.getLogger(__name__)



class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.depends('order_line.price_total', 'discount', 'chargeable_amount')
    def _amount_all(self):
        """
        Compute the total amounts of the SO.
        """
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                # FORWARDPORT UP TO 10.0
                if order.company_id.tax_calculation_rounding_method == 'round_globally':
                    price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                    taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.product_uom_qty, product=line.product_id, partner=order.partner_shipping_id)
                    amount_tax += sum(t.get('amount', 0.0) for t in taxes.get('taxes', []))
                else:
                    amount_tax += line.price_tax
            amount_total = amount_untaxed + amount_tax
            if order.chargeable_amount > 0.0:
                discount = amount_total - order.chargeable_amount
            else:
                discount = order.discount
            amount_total = amount_total - discount
            round_off_amount = self.env['rounding.off'].round_off_value_to_nearest(amount_total)
            order.update({
                'amount_untaxed': order.pricelist_id.currency_id.round(amount_untaxed),
                'amount_tax': order.pricelist_id.currency_id.round(amount_tax),
                'amount_total': amount_total + round_off_amount,
                'round_off_amount': round_off_amount,
                'total_outstanding_balance': order.prev_outstanding_balance + amount_total + round_off_amount
            })

    @api.depends('partner_id')
    def _calculate_balance(self):
        for order in self:
            order.prev_outstanding_balance = 0.0
            order.total_outstanding_balance = 0.0
            total_receivable = order._total_receivable()
            order.prev_outstanding_balance = total_receivable
    
    def _total_receivable(self):
        receivable = 0.0
        if self.partner_id:
            self._cr.execute("""SELECT l.partner_id, at.type, SUM(l.debit-l.credit)
                          FROM account_move_line l
                          LEFT JOIN account_account a ON (l.account_id=a.id)
                          LEFT JOIN account_account_type at ON (a.user_type_id=at.id)
                          WHERE at.type IN ('receivable','payable')
                          AND l.partner_id = %s
                          AND l.full_reconcile_id IS NULL
                          GROUP BY l.partner_id, at.type
                          """, (self.partner_id.id,))
            for pid, type, val in self._cr.fetchall():
                if val is None:
                    val=0
                receivable = (type == 'receivable') and val or -val
        return receivable

    @api.depends('partner_id')
    def _get_partner_details(self):
        for order in self:
            partner = order.partner_id
            order.update({
                'partner_uuid': partner.uuid,
                #'partner_village': partner.village,
            })


    partner_village = fields.Many2one("village.village", string="Partner Village")
    care_setting = fields.Selection([('ipd', 'IPD'),
                                     ('opd', 'OPD')], string="Care Setting")
    provider_name = fields.Char(string="Provider Name")
    discount_percentage = fields.Float(string="Discount Percentage")
    default_quantity = fields.Integer(string="Default Quantity")
    # above field is used to allow setting quantity as -1 in sale order line, when it is created through bahmni
    discount_type = fields.Selection([('none', 'No Discount'),
                                      ('fixed', 'Fixed'),
                                      ('percentage', 'Percentage')], string="Discount Type",
                                     default='none')
    discount = fields.Monetary(string="Discount")
    disc_acc_id = fields.Many2one('account.account', string="Discount Account Head")
    round_off_amount = fields.Float(string="Round Off Amount", compute=_amount_all)
    prev_outstanding_balance = fields.Monetary(string="Previous Outstanding Balance",
                                               compute=_calculate_balance)
    total_outstanding_balance = fields.Monetary(string="Total Outstanding Balance",
                                                compute=_amount_all)
    chargeable_amount = fields.Float(string="Chargeable Amount")
    amount_round_off = fields.Float(string="Round Off Amount")
    # location to identify from which location order is placed.
    location_id = fields.Many2one('stock.location', string="Location")
    partner_uuid = fields.Char(string='Customer UUID', store=True, readonly=True, compute='_get_partner_details')
    shop_id = fields.Many2one('sale.shop', 'Shop', required=True)


    @api.model
    def default_get(self, fields_list):
        res = super(SaleOrder, self).default_get(fields_list)
        user = self.env.user
        if user.shop_id and (not user.shop_ids or user.shop_id in user.shop_ids):
            res['shop_id'] = user.shop_id.id
        elif user.shop_ids:
            res['shop_id'] = user.shop_ids[0].id
        return res

    @api.model
    def create(self, vals):
        if self.env.user.has_group('bahmni_sale.group_cashier_own_shop'):
            allowed = self.env.user.shop_ids
            if not allowed:
                raise UserError(_(
                    "Your user has no Shops assigned. Ask an administrator to set "
                    "Settings → Users → Shops."
                ))
            vals = dict(vals or {})
            shop_id = vals.get('shop_id')
            if not shop_id or shop_id not in allowed.ids:
                vals['shop_id'] = allowed[0].id
        return super(SaleOrder, self).create(vals)

    _BAHMNI_DISCOUNT_WRITE_FIELDS = (
        'discount', 'discount_percentage', 'discount_type',
        'disc_acc_id', 'chargeable_amount',
    )

    @api.multi
    def write(self, vals):
        Users = self.env['res.users']
        if Users.bahmni_is_restricted_cashier():
            locked = [f for f in ('shop_id', 'team_id', 'user_id') if f in vals]
            if locked:
                raise UserError(_(
                    "Cashiers are not allowed to change Shop, Salesperson, or "
                    "Sales Team on sales orders."
                ))
        if self.env.user.has_group('bahmni_sale.group_cashier_own_shop') and 'shop_id' in vals:
            allowed_ids = self.env.user.shop_ids.ids
            if not allowed_ids or vals['shop_id'] not in allowed_ids:
                raise UserError(_("You can only work on sale orders for your assigned shop(s)."))
        if any(field in vals for field in self._BAHMNI_DISCOUNT_WRITE_FIELDS):
            Users.bahmni_cashier_raise_if_restricted(
                'bahmni_sale.group_allow_apply_discount',
                _("Cashiers are not allowed to apply or change discounts. "
                  "Ask a sales manager if a discount is required."),
            )
        return super(SaleOrder, self).write(vals)

    @api.multi
    def action_cancel(self):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            'bahmni_sale.group_allow_so_cancel',
            _("Cashiers are not allowed to cancel quotations or sales orders. "
              "Ask a sales manager if cancellation is required."),
        )
        return super(SaleOrder, self).action_cancel()

    @api.multi
    def copy(self, default=None):
        self.env['res.users'].bahmni_cashier_raise_if_restricted(
            None,
            _("Cashiers are not allowed to duplicate quotations or sales orders."),
        )
        return super(SaleOrder, self).copy(default=default)

    @api.onchange('order_line')
    def onchange_order_line(self):
        '''Calculate discount amount, when discount is entered in terms of %'''
        amount_total = self.amount_untaxed + self.amount_tax
        if self.discount_type == 'fixed':
            self.discount_percentage = self.discount/amount_total * 100
        elif self.discount_type == 'percentage':
            self.discount = amount_total * self.discount_percentage / 100

    @api.onchange('discount', 'discount_percentage', 'discount_type', 'chargeable_amount')
    def onchange_discount(self):
        amount_total = self.amount_untaxed + self.amount_tax
        if self.chargeable_amount:
            if self.discount_type == 'none' and self.chargeable_amount:
                self.discount_type = 'fixed'
                discount = amount_total - self.chargeable_amount
                self.discount_percentage = (discount / amount_total) * 100
        else:
            if self.discount_type == 'none':
                self.discount_percentage = 0
                self.discount = 0
            if self.discount:
                self.discount_percentage = (self.discount / amount_total) * 100
            if self.discount_percentage:
                self.discount = amount_total * self.discount_percentage / 100

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        '''1. make percentage and discount field readonly, when chargeable amount is allowed to enter
           2. lock cancel / discount / price UI for restricted cashiers'''
        result = super(SaleOrder, self).fields_view_get(view_id, view_type, toolbar=toolbar, submenu=submenu)
        if view_type == 'form':
            group_id = self.env.ref("bahmni_sale.group_allow_change_so_charge").id
            doc = etree.XML(result['arch'])
            if group_id in self.env.user.groups_id.ids:
                for node in doc.xpath("//field[@name='discount_percentage']"):
                    node.set('readonly', '1')
                    setup_modifiers(node, result['fields']['discount_percentage'])
                for node in doc.xpath("//field[@name='discount']"):
                    node.set('readonly', '1')
                    setup_modifiers(node, result['fields']['discount'])
                for node in doc.xpath("//field[@name='discount_type']"):
                    node.set('readonly', '1')
                    setup_modifiers(node, result['fields']['discount_type'])
            Users = self.env['res.users']
            if Users.bahmni_is_restricted_cashier('bahmni_sale.group_allow_so_cancel'):
                for node in doc.xpath("//button[@name='action_cancel']"):
                    node.set('invisible', '1')
                    setup_modifiers(node)
            if Users.bahmni_is_restricted_cashier('bahmni_sale.group_allow_apply_discount'):
                for fname in ('discount_type', 'discount_percentage', 'discount',
                              'disc_acc_id', 'chargeable_amount'):
                    for node in doc.xpath("//field[@name='%s']" % fname):
                        node.set('readonly', '1')
                        if fname in result.get('fields', {}):
                            setup_modifiers(node, result['fields'][fname])
            if Users.bahmni_is_restricted_cashier('bahmni_sale.group_allow_edit_price'):
                for node in doc.xpath("//field[@name='price_unit']"):
                    node.set('readonly', '1')
                    if 'price_unit' in result.get('fields', {}):
                        setup_modifiers(node, result['fields']['price_unit'])
            if Users.bahmni_is_restricted_cashier():
                for fname in ('shop_id', 'team_id', 'user_id'):
                    for node in doc.xpath("//field[@name='%s']" % fname):
                        node.set('readonly', '1')
                        if fname == 'shop_id':
                            node.set(
                                'options',
                                "{'no_open': True, 'no_create': True, 'no_create_edit': True}",
                            )
                        if fname in result.get('fields', {}):
                            setup_modifiers(node, result['fields'][fname])
            result['arch'] = etree.tostring(doc)
        return result

    @api.multi
    def _prepare_invoice(self):
        """
        Prepare the dict of values to create the new invoice for a sales order. This method may be
        overridden to implement custom invoice generation (making sure to call super() to establish
        a clean extension chain).
        """
        self.ensure_one()
        journal_id = self.env['account.invoice'].default_get(['journal_id'])['journal_id']
        if not journal_id:
            raise UserError(_('Please define an accounting sale journal for this company.'))
        invoice_vals = {
            'name': self.client_order_ref or '',
            'origin': self.name,
            'type': 'out_invoice',
            'account_id': self.partner_invoice_id.property_account_receivable_id.id,
            'partner_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'journal_id': journal_id,
            'currency_id': self.pricelist_id.currency_id.id,
            'comment': self.note,
            'payment_term_id': self.payment_term_id.id,
            'fiscal_position_id': self.fiscal_position_id.id or self.partner_invoice_id.property_account_position_id.id,
            'company_id': self.company_id.id,
            'user_id': self.user_id and self.user_id.id,
            'team_id': self.team_id.id,
            'discount_type': self.discount_type,
            'discount_percentage': self.discount_percentage,
            'disc_acc_id': self.disc_acc_id.id,
            'discount': self.discount,
            'shop_id': self.shop_id.id,
        }
        return invoice_vals


    #By Pass the Invoice wizard while we press the "Create Invoice" button in sale order afer confirmation.
    #So Once we Confirm the sale order it will create the invoice and ask for the register payment.
    @api.multi
    def action_confirm(self):
        self._bahmni_validate_payment_for_confirm()
        res = super(SaleOrder, self).action_confirm()
        self.validate_delivery()
        #here we need to set condition for if the its enabled then can continuw owise return True in else condition
        if self.env.user.has_group('bahmni_sale.group_skip_invoice_options'):
            payment_action = True
            for order in self:
                created_invoice = order._bahmni_create_and_open_invoice()
                if order._bahmni_is_credit_flow():
                    created_invoice.message_post(body=_(
                        "Credit bill: receivable left open on payer '%s'. "
                        "Settle via Credit Settlement Reconciliation (Excel)."
                    ) % (order.payer_partner_id.display_name or order.partner_invoice_id.display_name))
                    payment_action = order._bahmni_invoice_form_action(created_invoice)
                elif (order.payment_method or '').strip() == 'Free':
                    if order._bahmni_is_ipd_bed_order():
                        # Free does not waive bed — collect like Cash (deposit or register payment).
                        order._bahmni_allocate_ipd_deposit_on_invoice(created_invoice)
                        created_invoice.invalidate_cache()
                        if created_invoice.residual > 0.00001:
                            payment_action = order._bahmni_register_payment_action(created_invoice)
                        else:
                            payment_action = order._bahmni_invoice_form_action(created_invoice)
                    else:
                        created_invoice.message_post(body=_(
                            "Free care: no payment collection required."
                        ))
                        payment_action = order._bahmni_invoice_form_action(created_invoice)
                else:
                    # Cash: apply IPD deposit first, then Register Payment for remainder.
                    order._bahmni_allocate_ipd_deposit_on_invoice(created_invoice)
                    created_invoice.invalidate_cache()
                    if created_invoice.residual > 0.00001:
                        payment_action = order._bahmni_register_payment_action(created_invoice)
                    else:
                        payment_action = order._bahmni_invoice_form_action(created_invoice)
            return payment_action
        else:
            # Cash IPD must still settle from deposit even without Auto Invoice group.
            for order in self:
                if order._bahmni_is_cash_ipd_order():
                    created_invoice = order._bahmni_create_and_open_invoice()
                    order._bahmni_allocate_ipd_deposit_on_invoice(created_invoice)
            return res


    #This method will be called when validation is happens from the Bahmni side
    @api.multi
    def auto_validate_delivery(self):
        self._bahmni_validate_payment_for_confirm()
        super(SaleOrder, self).action_confirm()
        self.validate_delivery()

    @api.multi
    def validate_delivery(self):
        if self.env.ref('bahmni_sale.validate_delivery_when_order_confirmed').value == '1':
            allow_negative = self.env.ref('bahmni_sale.allow_negative_stock')
            if self.picking_ids:
                for picking in self.picking_ids:
                    if picking.state in ('waiting','confirmed','partially_available') and allow_negative.value == '1':
                        picking.force_assign()#Force Available
                    found_issue = False
                    if picking.state not in ('waiting','confirmed','partially_available'):
                        for pack in picking.pack_operation_product_ids:
                            if pack.product_id.tracking != 'none':
                                line = self.order_line.filtered(lambda l:l.product_id == pack.product_id)
                                lot_ids = None
                                if line.lot_id:
                                    lot_ids = line.lot_id
                                else:
                                    lot_ids = self._find_batch(pack.product_id,pack.product_qty,pack.location_id,picking)
                                if lot_ids:
                                    #First need to Find the related move_id of this operation
                                    operation_link_obj = self.env['stock.move.operation.link'].search([('operation_id','=',pack.id)],limit=1)
                                    move_obj = operation_link_obj.move_id
                                    #Now we have to update entry to the related table which holds the lot, stock_move and operation entrys
                                    pack_operation_lot = self.env['stock.pack.operation.lot'].search([('operation_id','=',pack.id)],limit=1)
                                    for lot in lot_ids:
                                        pack_operation_lot.write({
                                            'lot_name': lot.name,
                                            'qty': pack.product_qty,
                                            'operation_id': pack.id,
                                            'move_id': move_obj.id,
                                            'lot_id': lot.id,
                                            'cost_price': lot.cost_price,
                                            'sale_price': lot.sale_price,
                                            'mrp': lot.mrp
                                            })
                                    pack.qty_done = pack.product_qty
                                else:
                                    found_issue = True
                            else:
                                pack.qty_done = pack.product_qty
                        if not found_issue:
                            picking.do_new_transfer()#Validate
                    else:
                        message = ("<b>Auto validation Failed</b> <br/> <b>Reason:</b> There are not enough stock available for Some product on <a href=# data-oe-model=stock.location data-oe-id=%d>%s</a> Location") % (self.location_id,self.location_id.name)
                        self.message_post(body=message)

    def _find_batch(self, product, qty, location, picking):
        _logger.info("\n\n***** Product :%s, Quantity :%s Location :%s\n*****",product,qty,location)
        lot_objs = self.env['stock.production.lot'].search([('product_id','=',product.id),('life_date','>=',str(fields.datetime.now()))])
        _logger.info('\n *** Searched Lot Objects:%s \n',lot_objs)
        if any(lot_objs):
            #Sort losts based on the expiry date FEFO(First Expiry First Out)
            lot_objs = list(lot_objs)
            sorted_lot_list = sorted(lot_objs, key=lambda l: l.life_date)
            _logger.info('\n *** Sorted based on FEFO :%s \n',sorted_lot_list)
            done_qty = qty
            res_lot_ids = []
            lot_ids_for_query = tuple([lot.id for lot in sorted_lot_list])
            self._cr.execute("SELECT SUM(qty) FROM stock_quant WHERE lot_id IN %s and location_id=%s",(lot_ids_for_query,location.id,))
            qry_rslt = self._cr.fetchall()
            available_qty = qry_rslt[0] and qry_rslt[0][0] or 0
            if available_qty >= qty:
                for lot_obj in sorted_lot_list:
                    quants = lot_obj.quant_ids.filtered(lambda q: q.location_id == location)
                    for quant in quants:
                        if done_qty >= 0:
                            res_lot_ids.append(lot_obj)
                            done_qty = done_qty - quant.qty
                return res_lot_ids
            else:
                message = ("<b>Auto validation Failed</b> <br/> <b>Reason:</b> There are not enough stock available for <a href=# data-oe-model=product.product data-oe-id=%d>%s</a> product on <a href=# data-oe-model=stock.location data-oe-id=%d>%s</a> Location") % (product.id,product.name,location.id,location.name)
                self.message_post(body=message)
        else:
            message = ("<b>Auto validation Failed</b> <br/> <b>Reason:</b> There are no Batches/Serial no's available for <a href=# data-oe-model=product.product data-oe-id=%d>%s</a> product") % (product.id,product.name)
            self.message_post(body=message)
            return False
       
    @api.onchange('shop_id')
    def onchange_shop_id(self):
        self.warehouse_id = self.shop_id.warehouse_id.id
        self.location_id = self.shop_id.location_id.id
        self.payment_term_id = self.shop_id.payment_default_id.id
        self.project_id = self.shop_id.project_id.id if self.shop_id.project_id else False
        if self.shop_id.pricelist_id:
            self.pricelist_id = self.shop_id.pricelist_id.id
            
    @api.multi
    def validate_payment(self):
        for obj in self:
            payment_method = (obj.payment_method or '').strip()
            if payment_method == 'Credit':
                obj._bahmni_validate_payment_for_confirm()
                # Invoice only — leave AR open on the payer.
                inv_data = obj._prepare_invoice()
                invoice = self.env['account.invoice'].create(inv_data)
                for line in obj.order_line:
                    line.invoice_line_create(invoice.id, line.product_uom_qty)
                for line in invoice.invoice_line_ids:
                    line._set_additional_fields(invoice)
                invoice.compute_taxes()
                invoice.action_invoice_open()
                invoice.message_post(body=_(
                    "Credit bill auto-invoiced without cash payment (payer '%s')."
                ) % (obj.payer_partner_id.display_name or '-'))
                continue
            if payment_method == 'Free':
                obj._bahmni_apply_free_care_discount()
                inv_data = obj._prepare_invoice()
                invoice = self.env['account.invoice'].create(inv_data)
                for line in obj.order_line:
                    line.invoice_line_create(invoice.id, line.product_uom_qty)
                for line in invoice.invoice_line_ids:
                    line._set_additional_fields(invoice)
                invoice.compute_taxes()
                invoice.action_invoice_open()
                invoice.message_post(body=_("Free care auto-invoiced; no payment collected."))
                continue

            ctx = {'active_ids': [obj.id]}
            default_vals = self.env['sale.advance.payment.inv'
                                        ].with_context(ctx).default_get(['count', 'deposit_taxes_id',
                                                                         'advance_payment_method', 'product_id',
                                                                         'deposit_account_id'])
            payment_inv_wiz = self.env['sale.advance.payment.inv'].with_context(ctx).create(default_vals)
            payment_inv_wiz.with_context(ctx).create_invoices()
            for inv in obj.invoice_ids:
                inv.action_invoice_open()
                if inv.state == 'paid':
                    continue
                elif inv.amount_total > 0:
                    account_payment_env = self.env['account.payment']
                    fields = account_payment_env.fields_get().keys()
                    default_fields = account_payment_env.with_context({
                        'default_invoice_ids': [(4, inv.id, None)],
                    }).default_get(fields)
                    journal = obj._bahmni_get_default_cash_journal()
                    default_fields.update({'journal_id': journal.id})
                    payment_method_ids = self.env['account.payment.method'].search([
                        ('payment_type', '=', default_fields.get('payment_type')),
                    ]).ids
                    if default_fields.get('payment_type') == 'inbound':
                        journal_payment_methods = journal.inbound_payment_method_ids.ids
                    elif default_fields.get('payment_type') == 'outbound':
                        journal_payment_methods = journal.outbound_payment_method_ids.ids
                    else:
                        journal_payment_methods = []
                    common_payment_method = list(
                        set(payment_method_ids).intersection(set(journal_payment_methods)))
                    common_payment_method.sort()
                    if not common_payment_method:
                        raise UserError(_(
                            "Cash journal '%s' has no payment method configured "
                            "for this payment type."
                        ) % journal.display_name)
                    default_fields.update({'payment_method_id': common_payment_method[0]})
                    account_payment = account_payment_env.create(default_fields)
                    account_payment.post()
                else:
                    message = "<b>Auto validation Failed</b> <br/> <b>Reason:</b> The Total amount is 0 So, Can't Register Payment."
                    inv.message_post(body=message)

