# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Finance Pack (CE)",
    "summary": (
        "CCA depreciation + budget + HST return + WIP report + finance MI "
        "tiles, Odoo 19 CE-native (no Enterprise account_accountant)"
    ),
    "version": "19.0.2.0.1",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry / OdooIQ",
    "category": "Accounting/Localizations",
    "depends": [
        "base",
        "account",
        "l10n_ca",
        "mrp",
        "purchase",
        "sale",
        "southbrook_manufacturing_intelligence",
    ],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/cca_classes.xml",
        "data/ir_sequence.xml",
        "data/mi_tiles.xml",
        "views/cca_class_views.xml",
        "views/asset_views.xml",
        "views/budget_views.xml",
        "views/hst_return_views.xml",
        "views/wip_report_views.xml",
        "views/mi_engine_ext_views.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "post_init_activate_l10n_ca",
    "installable": True,
    "application": False,
}
