# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class BahmniIpdDepositRegister(models.TransientModel):
    """Collect the fixed minimum IPD deposit for a Cash patient (pre-admit)."""

    _name = 'bahmni.ipd.deposit.register'
    _description = 'Register IPD Deposit'

    partner_id = fields.Many2one('res.partner', string="Patient", required=True)
    payment_method = fields.Char(related='partner_id.payment_method', readonly=True)
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.user.company_id.currency_id)
    current_balance = fields.Monetary(
        string="Current Balance", readonly=True,
        currency_field='currency_id')
    amount = fields.Monetary(
        string="Deposit Amount", required=True, readonly=True,
        currency_field='currency_id',
        help="Fixed to company IPD minimum deposit. Cashiers cannot change this.")
    journal_id = fields.Many2one(
        'account.journal', string="Cash Journal", required=True,
        domain="[('type', 'in', ('cash', 'bank'))]")

    @api.model
    def default_get(self, fields_list):
        res = super(BahmniIpdDepositRegister, self).default_get(fields_list)
        company = self.env.user.company_id
        amount = company.ipd_min_deposit_amount or 0.0
        if amount <= 0:
            raise UserError(_(
                "Set IPD Minimum Deposit under Sales → Settings before collecting deposits."
            ))
        res['amount'] = amount
        journal = self.env['sale.order']._bahmni_get_default_cash_journal()
        res['journal_id'] = journal.id
        if self.env.context.get('active_model') == 'res.partner' and self.env.context.get('active_id'):
            partner = self.env['res.partner'].browse(self.env.context['active_id'])
            res['partner_id'] = partner.id
            res['current_balance'] = partner.ipd_deposit_balance or 0.0
        return res

    @api.onchange('partner_id')
    def _onchange_partner(self):
        self.current_balance = self.partner_id.ipd_deposit_balance or 0.0

    @api.multi
    def action_collect(self):
        self.ensure_one()
        partner = self.partner_id
        if (partner.payment_method or '').strip() != 'Cash':
            raise UserError(_(
                "IPD deposit is only for Cash patients. Patient payment method is '%s'."
            ) % (partner.payment_method or '-'))
        company = self.env.user.company_id
        expected = company.ipd_min_deposit_amount or 0.0
        if abs(self.amount - expected) > 0.00001:
            raise UserError(_(
                "Deposit amount must equal the configured minimum (%.2f)."
            ) % expected)
        if (partner.ipd_deposit_balance or 0.0) >= expected:
            raise UserError(_(
                "Patient already has an IPD deposit balance of %.2f. "
                "Use Top-up only when an order is blocked for shortfall."
            ) % partner.ipd_deposit_balance)

        payment = self._create_inbound_payment(partner, self.amount, self.journal_id)
        movement = partner.bahmni_adjust_ipd_deposit(
            self.amount, 'deposit', payment=payment,
            notes=_("Initial IPD deposit"),
        )
        return self._receipt_action(movement)

    def _create_inbound_payment(self, partner, amount, journal):
        deposit_account = self.env.user.company_id.ipd_deposit_account_id
        Payment = self.env['account.payment']
        method = self.env['account.payment.method'].search([
            ('payment_type', '=', 'inbound'),
            ('code', '=', 'manual'),
        ], limit=1)
        if not method:
            raise UserError(_("No inbound manual payment method found."))
        vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': partner.id,
            'amount': amount,
            'journal_id': journal.id,
            'payment_method_id': method.id,
            'communication': _('IPD Deposit - %s') % (partner.ref or partner.name),
        }
        if deposit_account:
            vals['destination_account_id'] = deposit_account.id
        payment = Payment.create(vals)
        payment.post()
        return payment

    def _receipt_action(self, movement):
        return {
            'type': 'ir.actions.act_window',
            'name': _('IPD Deposit Receipt'),
            'res_model': 'bahmni.ipd.deposit.movement',
            'view_mode': 'form',
            'res_id': movement.id,
            'target': 'current',
        }


class BahmniIpdDepositTopup(models.TransientModel):
    """Top up deposit by exact shortfall only."""

    _name = 'bahmni.ipd.deposit.topup'
    _description = 'IPD Deposit Top-up'

    partner_id = fields.Many2one('res.partner', string="Patient", required=True)
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.user.company_id.currency_id)
    current_balance = fields.Monetary(
        string="Current Balance", readonly=True,
        currency_field='currency_id')
    required_amount = fields.Monetary(
        string="Required Amount", required=True,
        currency_field='currency_id',
        help="Total amount the patient must hold (e.g. order price or min deposit).")
    amount = fields.Monetary(
        string="Top-up Amount", readonly=True,
        currency_field='currency_id',
        help="Exact shortfall: required − current balance.")
    journal_id = fields.Many2one(
        'account.journal', string="Cash Journal", required=True,
        domain="[('type', 'in', ('cash', 'bank'))]")

    def _shortfall_vals(self, partner, required_amount):
        balance = (partner.ipd_deposit_balance or 0.0) if partner else 0.0
        required = required_amount or 0.0
        return {
            'current_balance': balance,
            'amount': max(required - balance, 0.0),
        }

    @api.model
    def default_get(self, fields_list):
        res = super(BahmniIpdDepositTopup, self).default_get(fields_list)
        journal = self.env['sale.order']._bahmni_get_default_cash_journal()
        res['journal_id'] = journal.id
        ctx = self.env.context
        partner = False
        if ctx.get('active_model') == 'res.partner' and ctx.get('active_id'):
            partner = self.env['res.partner'].browse(ctx['active_id'])
            res['partner_id'] = partner.id
        if ctx.get('required_amount') is not None:
            res['required_amount'] = ctx['required_amount']
        res.update(self._shortfall_vals(partner, res.get('required_amount')))
        return res

    @api.model
    def create(self, vals):
        """Web client skips readonly amount — always persist exact shortfall."""
        vals = dict(vals or {})
        partner = self.env['res.partner'].browse(vals['partner_id']) if vals.get('partner_id') else None
        vals.update(self._shortfall_vals(partner, vals.get('required_amount')))
        return super(BahmniIpdDepositTopup, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals or {})
        res = super(BahmniIpdDepositTopup, self).write(vals)
        if 'required_amount' in vals or 'partner_id' in vals:
            for wizard in self:
                shortfall_vals = self._shortfall_vals(wizard.partner_id, wizard.required_amount)
                super(BahmniIpdDepositTopup, wizard).write(shortfall_vals)
        return res

    @api.onchange('partner_id', 'required_amount')
    def _onchange_shortfall(self):
        vals = self._shortfall_vals(self.partner_id, self.required_amount)
        self.current_balance = vals['current_balance']
        self.amount = vals['amount']

    @api.multi
    def action_collect(self):
        self.ensure_one()
        partner = self.partner_id
        if (partner.payment_method or '').strip() != 'Cash':
            raise UserError(_("IPD top-up is only for Cash patients."))
        balance = partner.ipd_deposit_balance or 0.0
        required = self.required_amount or 0.0
        if required <= 0:
            raise UserError(_("Enter the required amount (order total or blocked shortfall target)."))
        shortfall = max(required - balance, 0.0)
        if shortfall <= 0:
            raise UserError(_("No shortfall: current deposit already covers the required amount."))
        amount = shortfall
        self.write({'amount': amount, 'current_balance': balance})
        register = self.env['bahmni.ipd.deposit.register']
        payment = register._create_inbound_payment(partner, amount, self.journal_id)
        movement = partner.bahmni_adjust_ipd_deposit(
            amount, 'topup', payment=payment,
            notes=_("Top-up exact shortfall for required %.2f") % required,
        )
        return register._receipt_action(movement)


class BahmniIpdDepositRefund(models.TransientModel):
    """Refund remaining deposit; amount is read-only; requires supervisor approval."""

    _name = 'bahmni.ipd.deposit.refund'
    _description = 'IPD Deposit Refund'

    partner_id = fields.Many2one('res.partner', string="Patient", required=True)
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.user.company_id.currency_id)
    current_balance = fields.Monetary(
        string="Current Balance", readonly=True,
        currency_field='currency_id')
    amount = fields.Monetary(
        string="Refund Amount", readonly=True,
        currency_field='currency_id',
        help="Always equal to remaining IPD deposit balance.")
    journal_id = fields.Many2one(
        'account.journal', string="Cash Journal", required=True,
        domain="[('type', 'in', ('cash', 'bank'))]")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_approval', 'Pending Approval'),
    ], default='draft')
    movement_id = fields.Many2one('bahmni.ipd.deposit.movement', string="Pending Movement")

    def _refund_amount_vals(self, partner):
        balance = (partner.ipd_deposit_balance or 0.0) if partner else 0.0
        return {
            'current_balance': balance,
            'amount': balance,
        }

    @api.model
    def default_get(self, fields_list):
        res = super(BahmniIpdDepositRefund, self).default_get(fields_list)
        journal = self.env['sale.order']._bahmni_get_default_cash_journal()
        res['journal_id'] = journal.id
        partner = False
        if self.env.context.get('active_model') == 'res.partner' and self.env.context.get('active_id'):
            partner = self.env['res.partner'].browse(self.env.context['active_id'])
            res['partner_id'] = partner.id
        res.update(self._refund_amount_vals(partner))
        return res

    @api.model
    def create(self, vals):
        """Web client skips readonly amount — always persist remaining balance."""
        vals = dict(vals or {})
        partner = self.env['res.partner'].browse(vals['partner_id']) if vals.get('partner_id') else None
        vals.update(self._refund_amount_vals(partner))
        return super(BahmniIpdDepositRefund, self).create(vals)

    @api.multi
    def write(self, vals):
        vals = dict(vals or {})
        res = super(BahmniIpdDepositRefund, self).write(vals)
        if 'partner_id' in vals:
            for wizard in self:
                super(BahmniIpdDepositRefund, wizard).write(
                    self._refund_amount_vals(wizard.partner_id))
        return res

    @api.onchange('partner_id')
    def _onchange_partner(self):
        vals = self._refund_amount_vals(self.partner_id)
        self.amount = vals['amount']
        self.current_balance = vals['current_balance']

    @api.multi
    def action_submit_for_approval(self):
        self.ensure_one()
        partner = self.partner_id
        amount = partner.ipd_deposit_balance or 0.0
        if amount <= 0:
            raise UserError(_("No IPD deposit balance to refund."))
        pending = self.env['bahmni.ipd.deposit.movement'].search([
            ('partner_id', '=', partner.id),
            ('movement_type', '=', 'refund'),
            ('state', '=', 'pending_approval'),
        ], limit=1)
        if pending:
            raise UserError(_(
                "A refund for this patient is already pending approval (%s). "
                "Wait for a supervisor to approve or reject it."
            ) % (pending.receipt_number or pending.id))
        # Force amount from ledger (readonly field may be empty on the client).
        self.write({'amount': amount, 'current_balance': amount})
        self.env['bahmni.ipd.deposit.movement'].create_posted_movement(
            partner, 'refund', amount, partner.ipd_deposit_balance,
            notes=_("Refund pending supervisor approval"),
            state='pending_approval',
        )
        return {
            'type': 'ir.actions.act_window_close',
        }
