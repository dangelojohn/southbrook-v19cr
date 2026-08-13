# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Canadian Payroll (CE-only)",
    "summary": "Hand-rolled bi-weekly Canadian payroll engine "
               "(CRA 2026 brackets, CPP/EI/WSIB/EHT) for Odoo 19 CE "
               "without Enterprise hr_payroll",
    "version": "19.0.2.0.1",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry / OdooIQ",
    "category": "Human Resources/Payroll",
    "depends": [
        "base",
        "mail",
        "hr",
        "account",
        "southbrook_manufacturing_intelligence",
    ],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/cra_brackets_2026.xml",
        "data/wsib_rates.xml",
        "data/ir_sequence.xml",
        "data/mi_tiles.xml",
        "data/cron_cert_expiry_alert.xml",
        "views/contract_views.xml",
        "views/payroll_run_views.xml",
        "views/payslip_views.xml",
        "views/t4_preview_views.xml",
        "views/roe_views.xml",
        "views/certification_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
}
