# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Kitchen Tool Control",
    "summary": "Direct + indirect tool, consumable, maintenance-supply, and "
               "tool-crib control for the Southbrook kitchen/cabinet shop "
               "floor — saw blades, CNC bits, drill bits, screws, glues, "
               "abrasives, finishing materials, oils, grease, PPE, packing.",
    "description": """
Southbrook Kitchen Tool Control
================================

Adds a realistic tool + consumable + maintenance-supply control system on
top of the Southbrook MRP work-center seed shipped by ``southbrook_mrp_pm``.

Commit 1 — Foundation (this commit)
-----------------------------------

* ``southbrook.tool.category`` — hierarchical category model with
  ``tool_family`` (32 values) + ``directness`` (7 values) selection
  fields plus reusable / consumable / maintenance / hazard policy
  flags and replenishment defaults.
* 11-section seed (sections A..K from the build brief): cutting tools,
  fasteners + assembly consumables, adhesives + glues + sealants,
  abrasives, finishing + paint tools, measuring + layout + quality,
  clamps + jigs + fixtures, hand / power / pneumatic, machine
  maintenance, safety + PPE, packing + dispatch — ~110 categories
  arranged in 3-level parent/child trees.
* ``product.template`` + ``product.product`` extensions: ~40 ``x_southbrook_*``
  fields covering tool classification, cutting / fastener / glue / abrasive
  / paint geometry + chemistry, expiry + hazard flags, replenishment, and
  life-tracking baselines (used by commits 2-5).
* 3 security groups: Tool Operator (read + report), Tool Crib Manager
  (CRUD on assets + kits), Maintenance Technician (CRUD on
  requests + usage). Existing ``southbrook_mrp_pm.group_floor_manager``
  picks up read-only access to the readiness fields in commit 4.

Subsequent commits land tool assets + cribs (2), work-center / operation
requirements + kits (3), work-order readiness + checkout + maintenance
(4), consumption + cost + usage logging (5), and QC + downtime + reports
+ demo + docs (6).

19.0.2.0.0 — Safety three-state remediation
--------------------------------------------

* The four safety flags (``x_southbrook_hazardous``, ``_flammable``,
  ``_requires_ventilation``, ``_msds_required``) convert from Boolean to
  a three-state Selection (Yes / No / Not assessed, default Not
  assessed) — a plain boolean cannot distinguish "verified" from
  "never looked at", and both used to render identically as an
  unchecked box. Migration backfills existing data (prod: all rows
  land on Not assessed, since nothing was ever set True) and drops the
  legacy boolean columns.
* New release-gate constraint blocks marking an adhesive/finishing
  product available/new while any of the four is still Not assessed.
* New consumption fields ``x_southbrook_coverage_rate`` /
  ``_coverage_uom`` (vendor spec-sheet data, unseeded).
* New "Data Quality — Unclassified Consumables" worklist + a reverse
  substrate-lens smart button on the base Product Category form.
* Classifies four chemical products that shipped with no tool category
  (PUR Glue, EVA Glue Cartridge, Cabinet Paint White, Spray Booth
  Filter).

Compatible with Odoo 19.0 Community Edition. No Enterprise-only deps.
    """,
    "author": "Southbrook Cabinetry / OdooIQ",
    "license": "LGPL-3",
    "category": "Manufacturing",
    "version": "19.0.2.0.0",
    "depends": [
        "mrp",
        "stock",
        "purchase",
        "purchase_requisition",
        "maintenance",
        "hr",
        "product",
        "uom",
        # Southbrook foundation — work centers + equipment + Floor Manager group
        "southbrook_mrp_pm",
        # Southbrook MO orchestration (sb.production.package) — consumption + cost
        # rollup hooks attach to it in commits 5-6
        "southbrook_kitchen_mrp",
        # QR foundation — qr.mixin + scan controller.
        "southbrook_qr_kit",
    ],
    "data": [
        "security/kitchen_tools_groups.xml",
        "security/ir.model.access.csv",
        "data/sequences.xml",
        "data/tool_categories_seed.xml",
        "data/crons.xml",
        "views/tool_category_views.xml",
        "views/product_template_views.xml",
        "views/dq_views.xml",
        "views/tool_crib_views.xml",
        "views/tool_asset_views.xml",
        "views/workcenter_tool_requirement_views.xml",
        "views/tool_kit_views.xml",
        "views/mrp_workorder_views.xml",
        "views/workorder_tool_consumption_views.xml",
        # QR-driven tool usage log (Phase 6, 2026-06-26).
        "views/tool_usage_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "demo/tool_cribs_demo.xml",
        "demo/tool_assets_demo.xml",
        "demo/tool_kits_demo.xml",
        "demo/requirements_demo.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
