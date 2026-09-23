# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # ref field is a default field of this class
    _sql_constraints = [('unique_ref', 'unique(ref)',
                         'Internal Reference for Customer should be unique!')]

    village_id = fields.Many2one('village.village', string="Village")
    tehsil_id = fields.Many2one('district.tehsil', string="Tehsil")
    district_id = fields.Many2one('state.district', string="District")
    local_name = fields.Char(string="Local Name")
    uuid = fields.Char(string = "UUID")
    attribute_ids = fields.One2many('res.partner.attributes', 'partner_id', string='Attributes')
    # Synced from OpenMRS person (via erp-connect create.customer).
    gender = fields.Char(
        string="Gender",
        readonly=True,
        copy=False,
        help="OpenMRS person.gender (M/F).",
    )
    birthdate = fields.Date(
        string="Birthdate",
        readonly=True,
        copy=False,
        help="OpenMRS person.birthdate.",
    )

    # Synced from OpenMRS person attributes (source of truth). Cashiers must not edit.
    payment_method = fields.Char(string="Payment Method", readonly=True, copy=False,
                                 help="From OpenMRS PaymentMethod. Cash, Credit, or Free.")
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

    ipd_deposit_balance = fields.Monetary(
        string="IPD Deposit Balance",
        currency_field='ipd_deposit_currency_id',
        readonly=True,
        copy=False,
        help="Prepaid IPD deposit for Cash patients. Changed only via deposit wizards.",
    )
    ipd_deposit_currency_id = fields.Many2one(
        'res.currency', string="Deposit Currency",
        default=lambda self: self.env.user.company_id.currency_id,
        readonly=True,
    )
    ipd_deposit_movement_ids = fields.One2many(
        'bahmni.ipd.deposit.movement', 'partner_id', string="IPD Deposit Movements")

    @api.multi
    def write(self, vals):
        if 'ipd_deposit_balance' in vals and not self.env.context.get('allow_ipd_deposit_write'):
            from odoo.exceptions import AccessError
            from odoo import _
            if not self.env.user.has_group('base.group_system'):
                raise AccessError(_(
                    "IPD deposit balance can only be changed through the deposit wizards."
                ))
        return super(ResPartner, self).write(vals)

    @api.multi
    def bahmni_ipd_deposit_committed(self, exclude_order=None):
        """Amount already spoken for by open IPD sale orders / unpaid invoices.

        Draft/sent orders reserve their full total. Confirmed orders reserve unpaid
        invoice residual, or the full total when not yet invoiced.
        """
        self.ensure_one()
        SaleOrder = self.env['sale.order']
        domain = [
            ('partner_id', '=', self.id),
            ('care_setting', '=', 'ipd'),
            ('state', 'in', ('draft', 'sent', 'sale')),
        ]
        if exclude_order:
            domain.append(('id', '!=', exclude_order.id))
        committed = 0.0
        Movement = self.env['bahmni.ipd.deposit.movement']
        for order in SaleOrder.search(domain):
            charged = sum(Movement.search([
                ('partner_id', '=', self.id),
                ('sale_order_id', '=', order.id),
                ('movement_type', '=', 'charge'),
                ('state', '=', 'posted'),
            ]).mapped('amount'))
            if order.state in ('draft', 'sent'):
                remaining = max((order.amount_total or 0.0) - charged, 0.0)
            else:
                invoices = order.invoice_ids.filtered(lambda i: i.state not in ('cancel',))
                if invoices:
                    remaining = max(sum(invoices.mapped('residual')), 0.0)
                else:
                    remaining = max((order.amount_total or 0.0) - charged, 0.0)
            committed += remaining
        return committed

    @api.multi
    def bahmni_ipd_deposit_available(self, exclude_order=None):
        """Deposit balance minus open IPD commitments."""
        self.ensure_one()
        balance = self.ipd_deposit_balance or 0.0
        return max(balance - self.bahmni_ipd_deposit_committed(exclude_order=exclude_order), 0.0)

    @api.multi
    def bahmni_adjust_ipd_deposit(self, delta, movement_type, payment=None,
                                  sale_order=None, invoice=None, notes=None,
                                  state='posted', approved_by=None):
        """Adjust deposit balance and create an audit movement. Returns the movement."""
        from odoo.exceptions import UserError
        from odoo import _
        self.ensure_one()
        new_balance = (self.ipd_deposit_balance or 0.0) + delta
        if new_balance < -0.00001:
            raise UserError(_(
                "IPD deposit balance for '%s' would become negative (%.2f)."
            ) % (self.display_name, new_balance))
        self.with_context(allow_ipd_deposit_write=True).sudo().write({
            'ipd_deposit_balance': max(new_balance, 0.0),
        })
        return self.env['bahmni.ipd.deposit.movement'].create_posted_movement(
            self, movement_type, abs(delta), self.ipd_deposit_balance,
            payment=payment, sale_order=sale_order, invoice=invoice,
            notes=notes, state=state, approved_by=approved_by,
        )


    # inherited to update display name w.r.t. ref field 
    # and hence user can search customer with reference too
    @api.depends('is_company', 'name', 'parent_id.name',
                 'type', 'company_name', 'ref')
    def _compute_display_name(self):
        diff = dict(show_address=None, show_address_only=None, show_email=None)
        names = dict(self.with_context(**diff).name_get())
        for partner in self:
            partner.display_name = names.get(partner.id)

    # method is overridden to set ref in string returned by name_get
    @api.multi
    def name_get(self):
        res = []
        for partner in self:
            name = partner.name or ''
            if partner.ref:
                name += ' [' + partner.ref + ']'
            if partner.company_name or partner.parent_id:
                if not name and partner.type in ['invoice', 'delivery', 'other']:
                    name = dict(self.fields_get(['type'])['type']['selection'])[partner.type]
                if not partner.is_company:
                    name = "%s, %s" % (partner.commercial_company_name or partner.parent_id.name, name)
            if self._context.get('show_address_only'):
                name = partner._display_address(without_company=True)
            if self._context.get('show_address'):
                name = name + "\n" + partner._display_address(without_company=True)
            name = name.replace('\n\n', '\n')
            name = name.replace('\n\n', '\n')
            if self._context.get('show_email') and partner.email:
                name = "%s <%s>" % (name, partner.email)
            if self._context.get('html_format'):
                name = name.replace('\n', '<br/>')
            res.append((partner.id, name))
        return res

    @api.onchange('village_id')
    def onchange_village_id(self):
        if self.village_id:
            self.district_id = self.village_id.district_id.id
            self.tehsil_id = self.village_id.tehsil_id.id
            self.state_id = self.village_id.state_id.id
            self.country_id = self.village_id.country_id.id
            return {'domain': {'tehsil_id': [('id', '=', self.village_id.tehsil_id.id)],
                               'state_id': [('id', '=', self.village_id.state_id.id)],
                               'district_id': [('id', '=', self.village_id.district_id.id)],
                               'country_id': [('id', '=', self.village_id.country_id.id)]}}
        else:
            return {'domain': {'tehsil_id': [],
                               'state_id': [],
                               'district_id': [],
                               'country_id': []}}

class ResPartnerAttributes(models.Model):
    _name = 'res.partner.attributes'
    
    partner_id = fields.Many2one('res.partner', string='Partner', required=True, index=True, readonly=False)
    name = fields.Char(string='Name', size=128, required=True)
    value = fields.Char(string='Value', size=128, required=False)
