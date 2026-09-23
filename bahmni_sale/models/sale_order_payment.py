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
    'police_officer_name',
    'police_officer_phone',
    'insurance_region',
    'insurance_geo_zone',
    'insurance_woreda',
    'cbhi_id',
    'cbhi_expiry_date',
    'cbhi_region',
    'cbhi_zone',
    'cbhi_woreda',
    'cbhi_kebele',
    'shi_id',
    'shi_region',
    'shi_zone',
    'shi_woreda',
    'shi_kebele',
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
    police_officer_name = fields.Char(string="Police Officer Name", readonly=True, copy=False)
    police_officer_phone = fields.Char(string="Police Officer Phone", readonly=True, copy=False)
    insurance_region = fields.Char(string="Insurance Region", readonly=True, copy=False)
    insurance_geo_zone = fields.Char(string="Insurance Geo Zone", readonly=True, copy=False)
    insurance_woreda = fields.Char(string="Insurance Woreda", readonly=True, copy=False)
    cbhi_id = fields.Char(string="CBHI ID", readonly=True, copy=False)
    cbhi_expiry_date = fields.Char(string="CBHI Expiry Date", readonly=True, copy=False)
    cbhi_region = fields.Char(string="CBHI Region", readonly=True, copy=False)
    cbhi_zone = fields.Char(string="CBHI Zone", readonly=True, copy=False)
    cbhi_woreda = fields.Char(string="CBHI Woreda", readonly=True, copy=False)
    cbhi_kebele = fields.Char(string="CBHI Kebele", readonly=True, copy=False)
    shi_id = fields.Char(string="SHI ID", readonly=True, copy=False)
    shi_region = fields.Char(string="SHI Region", readonly=True, copy=False)
    shi_zone = fields.Char(string="SHI Zone", readonly=True, copy=False)
    shi_woreda = fields.Char(string="SHI Woreda", readonly=True, copy=False)
    shi_kebele = fields.Char(string="SHI Kebele", readonly=True, copy=False)
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
        """Return Free Care expense account; create 629000 if CoA exists but xmlid missing."""
        acc = self.env.ref('bahmni_sale.account_free_care', raise_if_not_found=False)
        if acc:
            return acc
        company = self.env.user.company_id
        if not company.chart_template_id:
            return self.env['account.account']
        Account = self.env['account.account'].sudo()
        acc = Account.search([
            ('code', 'in', ['629000', '6290']),
            ('company_id', '=', company.id),
        ], limit=1)
        if not acc:
            acc = Account.create({
                'code': '629000',
                'name': 'Free Care / Charity',
                'user_type_id': self.env.ref('account.data_account_type_expenses').id,
                'reconcile': False,
                'company_id': company.id,
            })
        # Bind xmlid so later lookups are stable.
        if not self.env['ir.model.data'].sudo().search([
            ('module', '=', 'bahmni_sale'),
            ('name', '=', 'account_free_care'),
        ], limit=1):
            self.env['ir.model.data'].sudo().create({
                'module': 'bahmni_sale',
                'name': 'account_free_care',
                'model': 'account.account',
                'res_id': acc.id,
                'noupdate': True,
            })
        return acc

    @api.multi
    def _bahmni_is_ipd_bed_order(self):
        """True when this SO is billed through the IPD / Bed shop (bed fees never Free-waived)."""
        self.ensure_one()
        shop_name = (self.shop_id.name or '').strip() if self.shop_id else ''
        if shop_name == 'IPD':
            return True
        care = (self.care_setting or '').strip().lower()
        return care == 'ipd' and shop_name in ('IPD', 'Bed', '')

    @api.multi
    def _bahmni_apply_free_care_discount(self):
        for order in self:
            # Bed fees are payable for all payment types — never auto-waive Free on IPD bed SOs.
            if order._bahmni_is_ipd_bed_order():
                continue
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

    def _bahmni_is_cash_ipd_order(self):
        self.ensure_one()
        payment_method = (self.payment_method or self.partner_id.payment_method or '').strip()
        care = (self.care_setting or '').strip().lower()
        shop_name = (self.shop_id.name or '').strip() if self.shop_id else ''
        return payment_method == 'Cash' and (care == 'ipd' or shop_name == 'IPD')

    @api.multi
    def _bahmni_allocate_ipd_deposit_on_invoice(self, invoice):
        """Pay invoice residual from IPD deposit for Cash patients. Returns amount applied."""
        self.ensure_one()
        partner = self.partner_id
        payment_method = (self.payment_method or partner.payment_method or '').strip()
        if payment_method != 'Cash':
            return 0.0
        balance = partner.ipd_deposit_balance or 0.0
        if balance <= 0 or invoice.residual <= 0:
            return 0.0
        # Prefer deposit allocation for IPD care setting / IPD shop; also allow if balance exists.
        care = (self.care_setting or '').strip().lower()
        shop_name = (self.shop_id.name or '').strip() if self.shop_id else ''
        if care != 'ipd' and shop_name != 'IPD' and balance <= 0:
            return 0.0

        apply_amount = min(balance, invoice.residual)
        if apply_amount <= 0:
            return 0.0

        # Block confirm-time shortage for pure IPD cash orders that should be deposit-covered.
        if care == 'ipd' or shop_name == 'IPD':
            if balance + 0.00001 < invoice.residual:
                raise UserError(_(
                    "Insufficient IPD deposit for patient '%s'. "
                    "Balance: %.2f, Invoice: %.2f. Collect exact shortfall top-up first."
                ) % (partner.display_name, balance, invoice.residual))

        journal = self._bahmni_get_default_cash_journal()
        Payment = self.env['account.payment']
        method = self.env['account.payment.method'].search([
            ('payment_type', '=', 'inbound'),
            ('code', '=', 'manual'),
        ], limit=1)
        if not method:
            raise UserError(_("No inbound manual payment method found for deposit allocation."))

        payment = Payment.with_context(
            default_invoice_ids=[(4, invoice.id, None)],
            bahmni_ipd_deposit_allocation=True,
        ).create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id,
            'amount': apply_amount,
            'journal_id': journal.id,
            'payment_method_id': method.id,
            'invoice_ids': [(4, invoice.id, None)],
            'communication': _('IPD deposit applied - %s') % (partner.ref or partner.name),
        })
        payment.with_context(bahmni_ipd_deposit_allocation=True).post()
        partner.bahmni_adjust_ipd_deposit(
            -apply_amount, 'charge', payment=payment, sale_order=self, invoice=invoice,
            notes=_("Applied to invoice %s") % (invoice.number or invoice.id),
        )
        invoice.message_post(body=_(
            "Applied %.2f from IPD deposit. Remaining deposit: %.2f."
        ) % (apply_amount, partner.ipd_deposit_balance or 0.0))
        return apply_amount

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
                        "Fix CBHI/SHI Woreda / Insurance Woreda / Credit Company in OpenMRS, "
                        "or create the payer partner in Odoo. Confirm is blocked."
                    ) % (order.partner_id.display_name, order.credit_information or '-'))
            if payment_method == 'Free':
                order._bahmni_apply_free_care_discount()
                # Free + bed order still requires payment (no 100% waive).
                if order._bahmni_is_ipd_bed_order():
                    order.message_post(body=_(
                        "Free care does not waive bed fees; invoice remains payable."
                    ))
            if order._bahmni_is_cash_ipd_order():
                partner = order.partner_id
                available = partner.bahmni_ipd_deposit_available(exclude_order=order)
                # While confirming this order, its own total must be covered by available
                # (available already excludes other open commitments).
                required = order.amount_total or 0.0
                if available + 0.00001 < required:
                    shortfall = required - available
                    raise UserError(_(
                        "Insufficient IPD deposit for patient '%s'. "
                        "Available: %.2f, Order: %.2f, Shortfall: %.2f. "
                        "Collect exact shortfall top-up before confirming."
                    ) % (partner.display_name, available, required, shortfall))

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

    @api.model
    def _bahmni_get_default_cash_journal(self):
        """Company cash journal used for Cash PaymentMethod collections."""
        company = self.env.user.company_id
        Journal = self.env['account.journal']
        journal = Journal.search([
            ('type', '=', 'cash'),
            ('company_id', '=', company.id),
        ], order='id asc', limit=1)
        if not journal:
            raise UserError(_(
                "No cash payment journal found for company '%s'. "
                "Create a Cash journal under Accounting → Configuration → Journals "
                "(with a default cash account), then try again."
            ) % (company.display_name,))
        return journal

    @api.multi
    def _bahmni_register_payment_action(self, invoice):
        self.ensure_one()
        cash_journal = self._bahmni_get_default_cash_journal()
        ctx = dict(
            default_invoice_ids=[(4, invoice.id, None)],
            default_journal_id=cash_journal.id,
            default_payment_type='inbound',
            default_partner_type='customer',
        )
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
            'police_officer_name': self.police_officer_name,
            'police_officer_phone': self.police_officer_phone,
            'insurance_region': self.insurance_region,
            'insurance_geo_zone': self.insurance_geo_zone,
            'insurance_woreda': self.insurance_woreda,
            'cbhi_id': self.cbhi_id,
            'cbhi_expiry_date': self.cbhi_expiry_date,
            'cbhi_region': self.cbhi_region,
            'cbhi_zone': self.cbhi_zone,
            'cbhi_woreda': self.cbhi_woreda,
            'cbhi_kebele': self.cbhi_kebele,
            'shi_id': self.shi_id,
            'shi_region': self.shi_region,
            'shi_zone': self.shi_zone,
            'shi_woreda': self.shi_woreda,
            'shi_kebele': self.shi_kebele,
            'payer_partner_id': self.payer_partner_id.id,
            'patient_partner_id': self.partner_id.id,
        })
        return invoice_vals
