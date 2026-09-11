# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PAYMENT_SNAPSHOT_FIELDS = (
    'payment_method',
    'credit_information',
    'credit_companies',
    'free_reason',
    'insurance_id',
    'insurance_name',
    'insurance_code',
    'insurance_zone',
    'insurance_expiry_date',
    'cbhi_id',
    'cbhi_expiry_date',
    'cbhi_region',
    'cbhi_zone',
    'cbhi_woreda',
)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Snapshot of patient payment classification at order create / draft refresh.
    payment_method = fields.Char(string="Payment Method", readonly=True, copy=False)
    credit_information = fields.Char(string="Credit Information", readonly=True, copy=False)
    credit_companies = fields.Char(string="Credit Companies", readonly=True, copy=False)
    free_reason = fields.Char(string="Free Reason", readonly=True, copy=False)
    insurance_id = fields.Char(string="Insurance ID", readonly=True, copy=False)
    insurance_name = fields.Char(string="Insurance Name", readonly=True, copy=False)
    insurance_code = fields.Char(string="Insurance Code", readonly=True, copy=False)
    insurance_zone = fields.Char(string="Insurance Zone", readonly=True, copy=False)
    insurance_expiry_date = fields.Char(string="Insurance Expiry Date", readonly=True, copy=False)
    cbhi_id = fields.Char(string="CBHI ID", readonly=True, copy=False)
    cbhi_expiry_date = fields.Char(string="CBHI Expiry Date", readonly=True, copy=False)
    cbhi_region = fields.Char(string="CBHI Region", readonly=True, copy=False)
    cbhi_zone = fields.Char(string="CBHI Zone", readonly=True, copy=False)
    cbhi_woreda = fields.Char(string="CBHI Woreda", readonly=True, copy=False)
    payer_partner_id = fields.Many2one(
        'res.partner',
        string="Payer",
        readonly=True,
        copy=False,
        help="Third-party payer for Credit bills. Invoice customer is set to this partner.",
    )

    @api.model
    def create(self, vals):
        order = super(SaleOrder, self).create(vals)
        if order.state == 'draft' and order.partner_id:
            order._bahmni_apply_patient_payment_classification()
        return order

    @api.multi
    def _bahmni_apply_patient_payment_classification(self):
        """Copy patient payment attrs onto the SO; set invoice partner for Credit/Free."""
        for order in self:
            if order.state != 'draft':
                continue
            partner = order.partner_id
            if not partner:
                continue

            vals = {field: partner[field] for field in PAYMENT_SNAPSHOT_FIELDS}
            payment_method = (partner.payment_method or '').strip()
            payer = self.env['res.partner']

            if payment_method == 'Credit':
                payer = partner.bahmni_resolve_credit_payer()
                vals['payer_partner_id'] = payer.id if payer else False
                # Keep a valid invoice partner always (required); confirm blocks if payer missing.
                vals['partner_invoice_id'] = payer.id if payer else partner.id
            else:
                vals['payer_partner_id'] = False
                vals['partner_invoice_id'] = partner.id

            order.sudo().write(vals)

            if payment_method == 'Free':
                order._bahmni_apply_free_care_discount()
            elif order.discount_type == 'percentage' and order.discount_percentage == 100.0:
                # Payment method changed away from Free on a draft — clear auto free waiver.
                free_acc = order._bahmni_get_free_care_account()
                if free_acc and order.disc_acc_id.id == free_acc.id:
                    order.sudo().write({
                        'discount_type': 'none',
                        'discount_percentage': 0.0,
                        'discount': 0.0,
                        'disc_acc_id': False,
                        'chargeable_amount': 0.0,
                    })

    @api.multi
    def _bahmni_get_free_care_account(self):
        return self.env.ref('bahmni_sale.account_free_care', raise_if_not_found=False)

    @api.multi
    def _bahmni_apply_free_care_discount(self):
        for order in self:
            free_acc = order._bahmni_get_free_care_account()
            amount_total = order.amount_untaxed + order.amount_tax
            vals = {
                'discount_type': 'percentage',
                'discount_percentage': 100.0,
                'discount': amount_total,
                'chargeable_amount': 0.0,
            }
            if free_acc:
                vals['disc_acc_id'] = free_acc.id
            order.sudo().write(vals)

    @api.multi
    def _bahmni_validate_payment_for_confirm(self):
        for order in self:
            payment_method = (order.payment_method or '').strip()
            if not payment_method:
                raise UserError(_(
                    "Patient has no PaymentMethod on this order. "
                    "Correct registration in OpenMRS and wait for sync before confirming."
                ))
            if payment_method == 'Credit':
                payer = order.payer_partner_id
                if not payer:
                    raise UserError(_(
                        "Credit payer could not be resolved for patient '%s' "
                        "(Credit Information: %s). "
                        "Fix CBHI Woreda / Insurance Name / Credit Company in OpenMRS, "
                        "or create the payer partner in Odoo. Confirm is blocked."
                    ) % (order.partner_id.display_name, order.credit_information or '-'))
            if payment_method == 'Free':
                order._bahmni_apply_free_care_discount()

    @api.multi
    def _bahmni_create_and_open_invoice(self):
        self.ensure_one()
        inv_data = self._prepare_invoice()
        invoice = self.env['account.invoice'].create(inv_data)
        for line in self.order_line:
            line.invoice_line_create(invoice.id, line.product_uom_qty)
        for line in invoice.invoice_line_ids:
            line._set_additional_fields(invoice)
        invoice.compute_taxes()
        invoice.message_post_with_view(
            'mail.message_origin_link',
            values={'self': invoice, 'origin': self},
            subtype_id=self.env.ref('mail.mt_note').id,
        )
        invoice.action_invoice_open()
        return invoice

    @api.multi
    def _bahmni_register_payment_action(self, invoice):
        self.ensure_one()
        ctx = dict(default_invoice_ids=[(4, invoice.id, None)])
        reg_pay_form = self.env.ref('account.view_account_payment_invoice_form')
        return {
            'name': _('Register Payment'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'account.payment',
            'views': [(reg_pay_form.id, 'form')],
            'view_id': reg_pay_form.id,
            'target': 'new',
            'context': ctx,
        }

    @api.multi
    def _prepare_invoice(self):
        invoice_vals = super(SaleOrder, self)._prepare_invoice()
        invoice_vals.update({
            'payment_method': self.payment_method,
            'credit_information': self.credit_information,
            'credit_companies': self.credit_companies,
            'free_reason': self.free_reason,
            'insurance_id': self.insurance_id,
            'insurance_name': self.insurance_name,
            'insurance_code': self.insurance_code,
            'insurance_zone': self.insurance_zone,
            'insurance_expiry_date': self.insurance_expiry_date,
            'cbhi_id': self.cbhi_id,
            'cbhi_expiry_date': self.cbhi_expiry_date,
            'cbhi_region': self.cbhi_region,
            'cbhi_zone': self.cbhi_zone,
            'cbhi_woreda': self.cbhi_woreda,
            'payer_partner_id': self.payer_partner_id.id,
            'patient_partner_id': self.partner_id.id,
        })
        return invoice_vals
