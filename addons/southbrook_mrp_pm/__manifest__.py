# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Manufacturing PM",
    "summary": (
        "Manufacturing PM toolkit: work centers + routing templates "
        "for the 12 Q8 cabinets, Send-to-Production wiring, PM "
        "dashboard, KPI surface, Floor Manager portal."
    ),
    "description": """
Southbrook Manufacturing PM
===========================

Bridges the customer-facing Order Builder (southbrook_estimating +
southbrook_estimating_website) into Odoo's mrp + maintenance stack
so a Manufacturing PM (Marcus Chen persona) can convert confirmed
orders into manufacturing orders + work orders with real cycle
times, monitor floor load + equipment condition, and hand off to
Floor Managers.

Closes M1-M20 from the 2026-06-01 Manufacturing PM JTBD gap
analysis. Layered build:

  Layer 1 — Foundation (this addon's data + plumbing)
      M8  8 mrp.workcenter records for Southbrook stations
      M6  Standard routing template per cabinet family
      M9  Cycle-time defaults per (cabinet × station)
      M3  Send-to-Production action wired to mrp.production create
      M7  Order Builder → Work Order generation per line

  Layer 2 — PM surface (follow-up commits)
      M4  Ready-for-Production queue (Confirmed MOs by deadline)
      M1  PM dashboard (queue + load + alerts + late)
      M10 Cross-station KPI panel
      M19 Lead-time-extra surfaced on Order Builder

  Layer 3 — Floor Manager + edges (follow-up commits)
      M13 maintenance.equipment.condition selection field
      M14 Equipment → MO impact chain
      M16 Floor Manager portal route + tablet layout
      M17 Floor Manager access group
      M20 ECO → in-flight MO notification rule
""",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Manufacturing/PM",
    "depends": [
        "southbrook_estimating",         # the 12 cabinet templates + BoMs
        "southbrook_estimating_website", # the Order Builder action endpoint
        "southbrook_plm",                # ECO model for M20 notification
        "mrp",                           # workcenter + production + routing
        "maintenance",                   # equipment + condition
    ],
    "data": [
        # M17 — Floor Manager security group + ACL. Loads FIRST so
        # any subsequent record-create / access check resolves the
        # group_floor_manager reference.
        "security/floor_manager.xml",
        # Production-approval gate (2026-06-03) — approver group. Loads
        # before views/sale_order_views.xml, which references it in the
        # Approve/Reject button groups= attribute.
        "security/production_approval.xml",
        "security/ir.model.access.csv",
        # Layer 1 commit 1 — 8 Southbrook stations as mrp.workcenter.
        "data/workcenters.xml",
        # Layer 1 commit 2 — canonical BoM + 8-station routing for
        # SB-BASE-1DR. Other 11 SKUs follow the same skeleton in a
        # later commit (per-cabinet cycle times differ).
        "data/routing_base_1dr.xml",
        # 11 remaining SB-* cabinets backfilled — closes the
        # 'no_bom_lines' debt where Send-to-Production skipped
        # every non-SB-BASE-1DR line.
        "data/routings_other_11.xml",
        # 2026-06-01 user CSV import — full cabinet shop catalogue.
        # 10 extended work centers (CNC, EB, DOOR, SAND, PAINT,
        # CURE, ASM, HW, QC, PACK) coexist with the 8 SB-* records.
        "data/workcenters_extended.xml",
        # 5 maintenance.equipment + 4 categories (CNC Router,
        # Edge Bander, Paint Booth, Utility).
        "data/equipment.xml",
        # 15 product.product across 5 categories (Fasteners,
        # Adhesives, Abrasives, Finishing, CNC Tools).
        "data/products_mfg.xml",
        # Layer 2 commit 1 — M1 v0 + M4 — Southbrook PM menu root
        # with Ready Queue / In Production / Late / Floor Load /
        # Equipment actions.
        "views/pm_menus.xml",
        # Layer 2 commit 2 — M13 — equipment condition field
        # surfaced on the maintenance.equipment form + list views.
        "views/equipment_views.xml",
        # Layer 3 commit 1 — M16 — Floor Manager portal templates
        # (/my/southbrook/floor and /my/southbrook/floor/<wc_id>).
        "views/floor_template.xml",
        # M11 — 9 cabinet families seeded for the throughput KPI.
        "data/cabinet_families.xml",
        # M10 + M11 — PM KPI dashboards (Dashboard + Family
        # Throughput entries under Southbrook PM).
        "views/pm_dashboard.xml",
        # M18 — weekly capacity planning (pivot + graph on
        # mrp.workorder, 'Capacity' menu entry).
        "views/pm_capacity.xml",
        # Production-approval gate (2026-06-03) — 2 customer-facing
        # mail templates (submitted / approved). Internal approver
        # hand-off stays chatter+activity (hybrid notification).
        "data/mail_templates.xml",
        # Production-approval gate (2026-06-03) — Request/Approve/Reject
        # buttons + status badge + audit page on the sale order form.
        "views/sale_order_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            # M16 — Floor Manager portal styling. Touch-friendly
            # tablet layout using shared Signature Series tokens.
            "southbrook_mrp_pm/static/src/scss/floor.scss",
        ],
        "web.assets_backend": [
            # M10 — PM KPI dashboard kanban styling. Backend bundle
            # because the dashboard is an internal-user view.
            "southbrook_mrp_pm/static/src/scss/dashboard.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
