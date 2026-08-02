# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Executive Dashboard",
    "summary": (
        "Single-pane mobile-first morning briefing for the GM/COO/CFO — "
        "9 tiles, 12 KPIs, sub-3s load"
    ),
    "version": "19.0.2.0.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry / OdooIQ",
    "category": "Tools/Dashboards",
    "depends": [
        "base",
        "web",
        "mail",
        "mrp",
        "sale",
        "stock",
        "account",
        "southbrook_manufacturing_intelligence",
        "southbrook_kitchen_mrp",
        "southbrook_quality",
        "southbrook_finance_pack",
        "southbrook_integrations",
        "southbrook_ui",
    ],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "views/exec_dashboard_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "southbrook_exec_dashboard/static/src/js/morning_briefing.js",
            "southbrook_exec_dashboard/static/src/xml/morning_briefing.xml",
            "southbrook_exec_dashboard/static/src/scss/morning_briefing.scss",
        ],
    },
    "installable": True,
    "application": True,
}
