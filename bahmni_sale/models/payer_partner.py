# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

PAYER_TYPE_CBHI = 'cbhi'
PAYER_TYPE_INSURANCE = 'insurance'
PAYER_TYPE_CREDIT_COMPANY = 'credit_company'
PAYER_TYPE_SHI = 'shi'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_bahmni_payer = fields.Boolean(
        string="Bahmni Payer",
        default=False,
        help="Third-party payer (CBHI woreda, insurer, credit company, SHI).",
    )
    bahmni_payer_type = fields.Selection([
        (PAYER_TYPE_CBHI, 'CBHI'),
        (PAYER_TYPE_INSURANCE, 'Insurance'),
        (PAYER_TYPE_CREDIT_COMPANY, 'Credit Company'),
        (PAYER_TYPE_SHI, 'SHI'),
    ], string="Payer Type", copy=False)
    bahmni_payer_needs_review = fields.Boolean(
        string="Payer Needs Review",
        default=False,
        help="Auto-created from OpenMRS attribute text; verify name/master data.",
    )

    @api.model
    def _bahmni_get_or_create_payers_root(self):
        Partner = self.env['res.partner'].sudo()
        root = Partner.search([
            ('name', '=', 'Payers'),
            ('is_company', '=', True),
            ('parent_id', '=', False),
        ], limit=1)
        if root:
            return root
        return Partner.create({
            'name': 'Payers',
            'is_company': True,
            'customer': True,
            'supplier': False,
            'is_bahmni_payer': True,
        })

    @api.model
    def _bahmni_find_payer(self, name, payer_type, code=None):
        Partner = self.env['res.partner'].sudo()
        domain = [('is_bahmni_payer', '=', True), ('name', '=ilike', name)]
        if payer_type:
            domain.append(('bahmni_payer_type', '=', payer_type))
        payer = Partner.search(domain, limit=1)
        if payer:
            return payer
        if code:
            payer = Partner.search([
                ('is_bahmni_payer', '=', True),
                ('ref', '=', code),
            ], limit=1)
        return payer

    @api.model
    def _bahmni_get_or_create_payer(self, name, payer_type, code=None):
        """Hybrid C: match by name/code; otherwise create under Payers and flag for review."""
        if not name:
            return self.env['res.partner']
        name = name.strip()
        if not name:
            return self.env['res.partner']

        payer = self._bahmni_find_payer(name, payer_type, code=code)
        if payer:
            return payer

        root = self._bahmni_get_or_create_payers_root()
        vals = {
            'name': name,
            'parent_id': root.id,
            'is_company': True,
            'customer': True,
            'supplier': False,
            'is_bahmni_payer': True,
            'bahmni_payer_type': payer_type,
            'bahmni_payer_needs_review': True,
        }
        if code:
            vals['ref'] = code
        payer = self.env['res.partner'].sudo().create(vals)
        _logger.info(
            "Auto-created Bahmni payer '%s' (type=%s, needs_review=True)",
            name,
            payer_type,
        )
        return payer

    @api.multi
    def bahmni_resolve_credit_payer(self):
        """Return payer partner for Credit patients, or empty recordset if unresolved."""
        self.ensure_one()
        if (self.payment_method or '').strip() != 'Credit':
            return self.env['res.partner']

        credit_info = (self.credit_information or '').strip()
        if credit_info == 'CBHI':
            return self._bahmni_get_or_create_payer(
                self.cbhi_woreda,
                PAYER_TYPE_CBHI,
            )
        if credit_info == 'SHI':
            return self._bahmni_get_or_create_payer(
                self.shi_woreda,
                PAYER_TYPE_SHI,
            )
        if credit_info == 'Insurance':
            return self._bahmni_get_or_create_payer(
                self.insurance_woreda or self.insurance_name,
                PAYER_TYPE_INSURANCE,
                code=self.insurance_code,
            )
        if credit_info == 'Credit Companies':
            return self._bahmni_get_or_create_payer(
                self.credit_companies,
                PAYER_TYPE_CREDIT_COMPANY,
            )
        return self.env['res.partner']

    @api.multi
    def bahmni_refresh_draft_sale_orders_payment(self):
        """Re-apply payment classification on draft SOs after OpenMRS correction."""
        SaleOrder = self.env['sale.order'].sudo()
        for partner in self:
            drafts = SaleOrder.search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'draft'),
            ])
            for order in drafts:
                order._bahmni_apply_patient_payment_classification()
