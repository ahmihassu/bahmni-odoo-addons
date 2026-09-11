from odoo import fields, models, api

class AccountInvoice(models.Model):
    _inherit = 'account.invoice'
	
    shop_id = fields.Many2one('sale.shop', 'Shop')
    patient_partner_id = fields.Many2one(
        'res.partner',
        string="Patient",
        readonly=True,
        copy=False,
        help="Care recipient when invoice partner is a Credit payer.",
    )
    payer_partner_id = fields.Many2one(
        'res.partner',
        string="Payer",
        readonly=True,
        copy=False,
    )
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
