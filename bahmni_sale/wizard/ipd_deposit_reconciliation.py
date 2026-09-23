# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import api, fields, models, _
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT as DF


class BahmniIpdDepositReconciliation(models.TransientModel):
    """End-of-day expected cash from deposits/top-ups minus refunds."""

    _name = 'bahmni.ipd.deposit.reconciliation'
    _description = 'IPD Deposit Till Reconciliation'

    date_from = fields.Date(string="From", required=True, default=fields.Date.context_today)
    date_to = fields.Date(string="To", required=True, default=fields.Date.context_today)
    cashier_id = fields.Many2one('res.users', string="Cashier")
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.user.company_id.currency_id)
    total_deposits = fields.Monetary(string="Deposits", readonly=True)
    total_topups = fields.Monetary(string="Top-ups", readonly=True)
    total_refunds = fields.Monetary(string="Refunds", readonly=True)
    expected_cash = fields.Monetary(string="Expected Cash", readonly=True)
    counted_cash = fields.Monetary(string="Counted Cash")
    variance = fields.Monetary(string="Variance", readonly=True)
    variance_note = fields.Char(string="Variance Note")

    @api.multi
    def action_compute(self):
        self.ensure_one()
        Movement = self.env['bahmni.ipd.deposit.movement']
        domain = [
            ('state', '=', 'posted'),
            ('create_date', '>=', self.date_from + ' 00:00:00'),
            ('create_date', '<=', self.date_to + ' 23:59:59'),
        ]
        if self.cashier_id:
            domain.append(('cashier_id', '=', self.cashier_id.id))
        moves = Movement.search(domain)
        deposits = sum(m.amount for m in moves if m.movement_type == 'deposit')
        topups = sum(m.amount for m in moves if m.movement_type == 'topup')
        refunds = sum(m.amount for m in moves if m.movement_type == 'refund')
        expected = deposits + topups - refunds
        counted = self.counted_cash or 0.0
        self.write({
            'total_deposits': deposits,
            'total_topups': topups,
            'total_refunds': refunds,
            'expected_cash': expected,
            'variance': counted - expected,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bahmni.ipd.deposit.reconciliation',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
        }
