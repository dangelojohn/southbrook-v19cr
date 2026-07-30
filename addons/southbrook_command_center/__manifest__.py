# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Factory Command Center",
    "summary": "Four-zone lens: live factory health, exception queue, "
               "schedule-confidence, margin & PO-risk scoring dashboard.",
    "description": """
Southbrook Central Command
===========================

Leaf aggregation node — reads from every Southbrook production/quality/
delivery module and native purchase/stock, and adds exactly one new model
(`southbrook.command.exception`) plus a read-time scoring library
(`southbrook.command.center`, an AbstractModel — zero stored fields, zero
`@api.depends` across foreign models). Supersedes the
`southbrook.kitchen.ops.dashboard` TransientModel POC's dashboard menu entry
in `southbrook_premium_orchestration` (that module's other responsibilities —
ops event authoring, readiness/MI orchestration — are untouched).

Writes to exactly two places: its own `southbrook.command.exception` records,
and new `southbrook.hermes.recommendation` records when an action requires an
agent-authored proposal. Never writes to project.task, sale.order,
mrp.production, southbrook.mi.check, southbrook.cmms.breakdown_alert, or
southbrook.ncr.

See DELIVERABLE_1_ARCHITECTURE.md / DELIVERABLE_2_DATA_MODEL.md /
DELIVERABLE_3_SCORING.md / DELIVERABLE_8_OPEN_QUESTIONS.md for the full design
record this addon implements.
""",
    "author": "Southbrook Cabinetry",
    "license": "LGPL-3",
    "category": "Manufacturing",
    "version": "19.0.3.0.0",
    "depends": [
        # --- Odoo core / native (read-only, no new business schema) ---
        "mail",     # chatter/activity mixin + owner-assignment notifications
        "bus",      # bus.bus pub/sub transport for the live-update layer
        "web",      # backend OWL client-action registry + asset bundle
        "purchase", # native purchase.order — PO delivery-risk panel
        "stock",    # native stock.picking — planned-vs-effective receipt dates

        # --- Southbrook custom modules (reuse-table sources; explicit even
        # where transitively available already, per Deliverable 1 §1) ---
        "southbrook_project_mrp",
        "southbrook_mrp_pm",
        "southbrook_manufacturing_intelligence",
        "southbrook_mes_mps",
        "southbrook_exec_dashboard",
        "southbrook_hermes",
        "southbrook_premium_orchestration",
        "southbrook_cmms_wms",
        "southbrook_quality",
        "southbrook_installer",
        "southbrook_integrations",
        "southbrook_floor_traveler",
        "southbrook_training_hub",  # contextual "learn this term" help lookup
    ],
    "data": [
        "security/command_center_groups.xml",
        "security/ir.model.access.csv",
        "data/command_center_params.xml",
        "data/command_center_data.xml",
        "data/command_center_alerts.xml",
        "views/command_center_views.xml",
        "views/command_center_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "southbrook_command_center/static/src/scss/central_command.scss",
            "southbrook_command_center/static/src/js/central_command.esm.js",
            "southbrook_command_center/static/src/xml/central_command.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
