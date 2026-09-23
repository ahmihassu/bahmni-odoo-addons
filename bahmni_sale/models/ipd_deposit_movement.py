# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BahmniIpdDepositMovement(models.Model):
    """Immutable audit ledger for IPD deposit in / top-up / charge / refund."""

    _name = 'bahmni.ipd.deposit.movement'
    _description = 'IPD Deposit Movement'
    _order = 'create_date desc, id desc'

    partner_id = fields.Many2one(
        'res.partner', string="Patient", required=True, index=True, ondelete='restrict')
    movement_type = fields.Selection([
        ('deposit', 'Initial Deposit'),
        ('topup', 'Top-up'),
        ('charge', 'Charge Deduction'),
        ('refund', 'Refund'),
        ('void', 'Void'),
    ], string="Type", required=True, index=True)
    amount = fields.Monetary(string="Amount", required=True)
    currency_id = fields.Many2one(
        'res.currency', string="Currency", required=True,
        default=lambda self: self.env.user.company_id.currency_id)
    balance_after = fields.Monetary(string="Balance After")
    receipt_number = fields.Char(string="Receipt Number", index=True, copy=False)
    payment_id = fields.Many2one('account.payment', string="Payment", ondelete='set null')
    sale_order_id = fields.Many2one('sale.order', string="Sale Order", ondelete='set null')
    invoice_id = fields.Many2one('account.invoice', string="Invoice", ondelete='set null')
    company_id = fields.Many2one(
        'res.company', string="Company", required=True,
        default=lambda self: self.env.user.company_id)
    notes = fields.Char(string="Notes")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('pending_approval', 'Pending Approval'),
        ('cancelled', 'Cancelled'),
    ], string="Status", default='posted', required=True, index=True)
    approved_by = fields.Many2one('res.users', string="Approved By", readonly=True)
    approved_date = fields.Datetime(string="Approved On", readonly=True)
    cashier_id = fields.Many2one(
        'res.users', string="Cashier", default=lambda self: self.env.user, readonly=True)

    @api.model
    def _next_receipt_number(self):
        return self.env['ir.sequence'].next_by_code('bahmni.ipd.deposit.receipt') or '/'

    @api.model
    def create_posted_movement(self, partner, movement_type, amount, balance_after,
                               payment=None, sale_order=None, invoice=None, notes=None,
                               state='posted', approved_by=None):
        vals = {
            'partner_id': partner.id,
            'movement_type': movement_type,
            'amount': amount,
            'balance_after': balance_after,
            'currency_id': self.env.user.company_id.currency_id.id,
            'company_id': self.env.user.company_id.id,
            'receipt_number': self._next_receipt_number(),
            'payment_id': payment.id if payment else False,
            'sale_order_id': sale_order.id if sale_order else False,
            'invoice_id': invoice.id if invoice else False,
            'notes': notes,
            'state': state,
            'cashier_id': self.env.user.id,
        }
        if approved_by:
            vals['approved_by'] = approved_by.id
            vals['approved_date'] = fields.Datetime.now()
        return self.sudo().create(vals)

    @api.multi
    def action_approve_refund(self):
        """Approve a pending refund from the admin Approve Refunds queue."""
        from odoo.exceptions import UserError
        from odoo import _
        if not self.env.user.has_group('bahmni_sale.group_ipd_deposit_refund_approve'):
            raise UserError(_(
                "You need the 'Approve IPD Deposit Refunds' privilege to approve this refund."
            ))
        for movement in self:
            if movement.movement_type != 'refund' or movement.state != 'pending_approval':
                raise UserError(_("Only refunds pending approval can be approved."))
            if movement.cashier_id and movement.cashier_id.id == self.env.user.id:
                raise UserError(_(
                    "The same user who requested the refund cannot approve it. "
                    "Ask another supervisor."
                ))
            partner = movement.partner_id
            amount = partner.ipd_deposit_balance or 0.0
            if amount <= 0:
                raise UserError(_("No IPD deposit balance left to refund for '%s'.") % partner.display_name)
            if abs(amount - (movement.amount or 0.0)) > 0.00001:
                # Ledger may have changed; refund whatever remains, capped at requested.
                amount = min(amount, movement.amount or 0.0)
            if amount <= 0:
                raise UserError(_("Nothing left to refund for '%s'.") % partner.display_name)

            journal = self.env['sale.order']._bahmni_get_default_cash_journal()
            Payment = self.env['account.payment']
            method = self.env['account.payment.method'].search([
                ('payment_type', '=', 'outbound'),
                ('code', '=', 'manual'),
            ], limit=1)
            if not method:
                raise UserError(_("No outbound manual payment method found."))
            vals = {
                'payment_type': 'outbound',
                'partner_type': 'customer',
                'partner_id': partner.id,
                'amount': amount,
                'journal_id': journal.id,
                'payment_method_id': method.id,
                'communication': _('IPD Deposit Refund - %s') % (partner.ref or partner.name),
            }
            deposit_account = self.env.user.company_id.ipd_deposit_account_id
            if deposit_account:
                vals['destination_account_id'] = deposit_account.id
            payment = Payment.create(vals)
            payment.post()

            movement.sudo().write({'state': 'cancelled'})
            posted = partner.bahmni_adjust_ipd_deposit(
                -amount, 'refund', payment=payment,
                notes=_("Approved IPD deposit refund (request %s)") % (movement.receipt_number or movement.id),
                approved_by=self.env.user,
            )
            return {
                'type': 'ir.actions.act_window',
                'name': _('IPD Deposit Refund Receipt'),
                'res_model': 'bahmni.ipd.deposit.movement',
                'view_mode': 'form',
                'res_id': posted.id,
                'target': 'current',
            }
        return True

    @api.multi
    def action_reject_refund(self):
        """Cancel a pending refund request without paying out."""
        from odoo.exceptions import UserError
        from odoo import _
        if not self.env.user.has_group('bahmni_sale.group_ipd_deposit_refund_approve'):
            raise UserError(_(
                "You need the 'Approve IPD Deposit Refunds' privilege to reject this refund."
            ))
        for movement in self:
            if movement.movement_type != 'refund' or movement.state != 'pending_approval':
                raise UserError(_("Only refunds pending approval can be rejected."))
            movement.sudo().write({
                'state': 'cancelled',
                'notes': ((movement.notes or '') + ' | Rejected by %s' % self.env.user.name).strip(' |'),
            })
        return True
