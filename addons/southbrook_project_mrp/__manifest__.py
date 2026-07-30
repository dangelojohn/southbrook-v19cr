# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Project ↔ Manufacturing",
    "summary": "Customer-job coordination layer above MRP: link a project.task "
               "to its Manufacturing Order(s), surface build status read-only, "
               "and auto-create/link the job from a confirmed sale.",
    "description": """
Southbrook Project ↔ Manufacturing
==================================

The job/coordination layer ABOVE the MRP backbone — it does NOT rebuild any
manufacturing logic (BoMs, work orders, routings, costing all stay in mrp).

* T1.1  Link  — project.task.production_ids <-> mrp.production.project_task_id
        (a customer job spans several MOs: base + worktop + pantry…). MO
        number(s), product/SKU and BoM surface on the task.
* T1.2  Status — read-only computed roll-up on the task, sourced from mrp/stock:
        each MO's state + component availability (reservation_state), MO refs,
        and a cost roll-up pulled from the costing fields when present. Lets the
        PM answer "can we build this, and where is it?" from the job.
* T1.3  Flow  — confirming a sale auto-creates/links the project.task job and
        attaches its MOs (also linked at MO-creation time, since procurement
        may create MOs after confirmation).

Reuses the custom MO tabs (CAD / Intelligence / Production Costs / Shop Floor)
rather than duplicating them — material/BoM/cost are pulled FROM the linked MO.
""",
    "version": "19.0.1.5.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry / OdooIQ",
    "category": "Services/Project",
    # sale_mrp brings sale<->mrp; southbrook_project brings the task polish we
    # extend. mrp_product_costing is NOT depended on (it lives outside this
    # repo) — its cost fields are read defensively via getattr.
    # W029 (R3.W5, 2026-06-27) — added southbrook_mrp_pm so the new
    # Bottleneck Contention menu can hang under menu_southbrook_pm_root
    # (where the planner already lives for capacity / ready-queue work).
    "depends": [
        "southbrook_project",
        "southbrook_mrp_pm",
        "sale_mrp",
        "purchase_mrp",
        "maintenance",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_readiness_retick.xml",
        "data/project_job_templates.xml",
        "views/data_quality_report_views.xml",
        "views/mrp_production_views.xml",
        "views/project_task_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "southbrook_project_mrp/static/src/scss/kanban_pipeline.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
