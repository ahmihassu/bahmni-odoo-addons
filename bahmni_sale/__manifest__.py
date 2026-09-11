# -*- coding: utf-8 -*-
{
    'name': 'Bahmni Sale',
    'version': '1.8',
    'summary': 'Custom Sales module to meet bahmni requirement',
    'sequence': 1,
    'description': """
Bahmni Sale
====================
""",
    'category': 'Sales',
    'website': '',
    'images': [],
    'depends': ['sale', 'sale_stock','sales_team', 'bahmni_account','point_of_sale','account', 'mail'],
    'external_dependencies': {
        'python': ['xlsxwriter'],
    },
    'data': ['security/ir.model.access.csv',
             'security/security_groups.xml',
             'security/sale_shop_cashier_security.xml',
             'data/data.xml',
             'data/free_care_account.xml',
             'data/sale_config_setting.xml',
             'views/bahmni_sale.xml',
             'views/res_partner_view.xml',
             'views/res_users_view.xml',
             'views/village_master_view.xml',
             'views/sale_order_views.xml',
             'views/sale_config_settings.xml',
	     'views/pos_view.xml',
             'views/account_invoice_view.xml',
             'wizard/cbhi_claim_export_view.xml'],
    'demo': [],
    'qweb': [],
    'installable': True,
    'application': True,
    'auto_install': False,
}
