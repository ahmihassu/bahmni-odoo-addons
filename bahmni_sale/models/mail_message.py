# -*- coding: utf-8 -*-
from email.utils import formataddr

from odoo import api, models


class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model
    def _get_default_from(self):
        """Allow chatter posts when users have no email (typical in facilities)."""
        user = self.env.user
        if user.email:
            return formataddr((user.name, user.email))
        company = user.company_id
        if company.email:
            return formataddr((company.name or user.name, company.email))
        login = (user.login or 'odoo').replace(' ', '_')
        return formataddr((user.name or 'Odoo', '%s@localhost' % login))
