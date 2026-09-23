# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    ipd_min_deposit_amount = fields.Float(
        string="IPD Minimum Deposit",
        default=0.0,
        help="Fixed amount cashiers collect as the initial IPD deposit for Cash patients.",
    )
    ipd_deposit_account_id = fields.Many2one(
        'account.account',
        string="IPD Deposit Liability Account",
        help="Liability account holding patient IPD deposits.",
    )
