# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Kitchen Work Centers",
    "summary": "Realistic kitchen-cabinet manufacturing work-center "
               "configuration on top of Odoo 19 CE MRP.",
    "description": """
Southbrook Kitchen Work Centers
================================

Extends the Southbrook MRP stack with kitchen-cabinet-specific work-center
configuration, master data (materials / finishes / skills), and a station-type
taxonomy that lets operation templates and routing decisions reason about
the shop floor in the same vocabulary a planner uses.

Phase M1 ships:
  * Master data — Materials (10) + Finishes (9) lightweight CE-safe models
  * mrp.workcenter extension with x_sbk_* configuration fields:
    station_type, machine_code, machine_brand, supported_material_ids,
    supported_finish_ids, required_skill_ids (reuses hr.skill),
    max_panel_length_mm / width_mm, default_setup_time_min,
    changeover_time_min, allows_parallel_jobs, is_bottleneck,
    oee_target, planning_notes, shop_floor_notes, quality_notes,
    active_for_kitchen
  * 2 new work centers seeded — ENG01 (Design Review / Production
    Engineering) and CNC02 (Backup CNC Router)
  * station_type assigned to the 12 existing Southbrook work centers
    via no-noupdate post-load applies

Subsequent phases (M2-M4) layer in operation templates with dynamic
duration formulas, quality + rework + downtime, project/room/cabinet
fields on mrp.production, costing extensions, demo data, and views.

Built strictly for Odoo 19 CE — no Enterprise dependencies. Extends
existing Southbrook modules (southbrook_kitchen_mrp,
southbrook_manufacturing_intelligence, southbrook_mrp_pm,
southbrook_kitchen_workspace) without duplicating their models.
""",
    "version": "19.0.5.1.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "maintainers": ["southbrook"],
    "website": "https://southbrookcabinetry.space",
    "category": "Manufacturing",
    "depends": [
        # Odoo CE core.
        "mrp",
        "mrp_account",
        "stock",
        "hr_skills",
        # SAMI PRD MAINT-03 — auto-escalate breakdown downtime to
        # maintenance.request. Lives in the maintenance module.
        "maintenance",
        # Southbrook upstream — extend, never duplicate.
        "southbrook_mrp_pm",
        "southbrook_manufacturing_intelligence",
        "southbrook_kitchen_mrp",
        "southbrook_kitchen_workspace",
        # QR foundation — provides qr.mixin + scan controller.
        "southbrook_qr_kit",
        # W008 — asbuilt back-references pg.release / pg.ebom / pg.item
        # via stored related fields walking production_id.pg_*. The
        # release addon adds those fields to mrp.production (PG-112)
        # and owns the pg.release / pg.ebom / pg.item models.
        "product_graph_release",
        # W034 (R4.W5, 2026-06-27) — Report-Engineering-Issue wizard
        # creates a draft southbrook.eco. PLM was already transitively
        # in the dep chain via southbrook_mrp_pm; declare it directly
        # so env.ref('southbrook_plm.eco_type_document') is hard-loaded
        # before this addon's xml validation runs.
        "southbrook_plm",
        # W066 (R3.9, 2026-06-27) — Subcontract Decision wizard
        # creates a pg.rfq with source_type='subcontract' (W078) and
        # filters candidates against pg.vendor / pg.vendor.part (W031
        # AVL). Hard-dep on both so env['pg.rfq'] + env['pg.vendor']
        # are guaranteed at install time.
        "product_graph_rfq",
        "product_graph_vendor",
    ],
    "data": [
        "security/ir.model.access.csv",
        # Quarantine location seed (SAMI PRD INV-06). Loaded early so
        # the NCR auto-quarantine flow can resolve it by xml_id.
        "data/southbrook_quarantine_location.xml",
        # As-built ID sequence (SAMI PRD W-08). Loaded before the views
        # that reference the model.
        "data/southbrook_asbuilt_sequence.xml",
        # Shift handover sequence (SAMI PRD MES-10).
        "data/southbrook_shift_handover_seed.xml",
        # Master data — materials + finishes the work-center fields
        # reference. Must load BEFORE the workcenter seed.
        "data/southbrook_kitchen_materials.xml",
        "data/southbrook_kitchen_finishes.xml",
        # Workcenter seeds: 2 new + station_type apply to 12 existing.
        "data/mrp_workcenter_seed.xml",
        # M2: 15 operation templates with duration formulas. Must load
        # AFTER workcenter seed since templates reference work centers.
        "data/southbrook_kitchen_operation_template.xml",
        # Views.
        "views/southbrook_kitchen_material_views.xml",
        "views/southbrook_kitchen_finish_views.xml",
        "views/southbrook_kitchen_operation_template_views.xml",
        "views/southbrook_kitchen_workcenter_downtime_views.xml",
        # mrp_workorder_views must load BEFORE mrp_workcenter_views
        # because the latter's header button references the
        # action_sbk_tablet_wo_queue xmlid defined in the former.
        "views/mrp_workorder_views.xml",
        "views/mrp_workcenter_views.xml",
        "views/mrp_production_views.xml",
        # Path B MO form redesign (19.0.4.16.0): header context bar +
        # MI tab. Lives here (top of the kitchen MRP dep chain) so the
        # x_sbk_* + x_mi_* fields are loaded into the model class by
        # view-validation time. Companion Cabinet Label tab ships from
        # southbrook_kitchen_mrp.
        "views/mrp_production_form_header_redesign.xml",
        "views/mrp_bom_views.xml",
        "views/southbrook_kitchen_menus.xml",
        # NCR / Rework Queue surface (SAMI PRD #8 + #9, 2026-06-25).
        # Loads AFTER menus.xml because it adds an entry under
        # menu_sbk_ops_root.
        "views/southbrook_ncr_views.xml",
        # As-built records surface (SAMI PRD W-08, 2026-06-26). Also
        # hangs off menu_sbk_ops_root.
        "views/southbrook_asbuilt_views.xml",
        # Shift handover surface (SAMI PRD MES-10, 2026-06-26).
        "views/southbrook_shift_handover_views.xml",
        # OPC-UA gateway prototype (SAMI PRD IOT-02, 2026-06-26).
        "views/southbrook_opcua_views.xml",
        # W067 (R3.10, 2026-06-27) — calendar-aware capacity per
        # (WC × day). Cron loads here; views (pivot/graph/list +
        # menu under southbrook_mrp_pm root) load just after.
        "data/southbrook_capacity_day_cron.xml",
        "views/southbrook_capacity_day_views.xml",
        # WO traveler PDF — printable per-WO with embedded scan QR
        # (QR rollout Phase 3+, 2026-06-26). Action lives in
        # mrp.workorder action menu via binding_model_id.
        "reports/wo_traveler_report.xml",
        # W034 (2026-06-27) — operator-facing wizard launched from the
        # WO form. Loads AFTER mrp_workorder_views.xml because the
        # view adds the launcher button.
        "wizards/southbrook_wo_raise_eco_wizard_views.xml",
        # W040 (R2.4, 2026-06-27) — Report-a-Problem single-screen
        # wizard. Same load-after-view rationale.
        "wizards/southbrook_report_problem_wizard_views.xml",
        # W066 (R3.9, 2026-06-27) — Subcontract Decision wizard +
        # at-risk-WOs action + menu. Loads AFTER mrp_workorder_views
        # (button on WO form) and AFTER southbrook_capacity_day_views
        # (menu sequence is 9, sits next to W067's "Capacity
        # (Calendar-Aware)" entry at sequence 8).
        "wizards/southbrook_subcontract_decision_wizard_views.xml",
        # Demo data — loaded only when demo flag is set.
    ],
    "demo": [
        "demo/southbrook_kitchen_workcenters_demo.xml",
    ],
    # W014 (2026-06-27) — tablet kanban SCSS for mrp.workorder.
    # Loads into the backend bundle; scoped to .o_kanban_sb_tablet so
    # it never touches the native workcenter_line_kanban.
    "assets": {
        "web.assets_backend": [
            "southbrook_mrp_kitchen_workcenters/static/src/scss/tablet_kanban.scss",
            # W074 (R8.6, 2026-06-27) — bottom-anchored mobile status
            # bar so the START/DONE buttons sit inside the one-handed
            # thumb safe-zone on portrait tablets. @media-gated at
            # 768px so desktop layout is unchanged.
            "southbrook_mrp_kitchen_workcenters/static/src/scss/tablet_workorder_mobile.scss",
            # W075 (R8.12, 2026-06-27) — onbeforeunload guard so a
            # swipe-back / OS gesture on a dirty WO form prompts
            # before discarding the operator's in-progress edits.
            # Reads the `o_form_dirty` DOM marker so it stays silent
            # on clean / read-only forms and on non-form pages.
            "southbrook_mrp_kitchen_workcenters/static/src/js/wo_dirty_guard.js",
            # W039 (R8.1, 2026-06-27) — glove-grade tap-target density
            # bundle. SCSS is fully scoped to body.sb-shopfloor-dense
            # (off by default — planners + office users see zero
            # change). JS reads ?shopfloor=1 URL param or localStorage
            # `sb.shopfloor.dense` to decide whether to add the class.
            "southbrook_mrp_kitchen_workcenters/static/src/scss/shopfloor_dense.scss",
            "southbrook_mrp_kitchen_workcenters/static/src/js/shopfloor_density_toggle.js",
        ],
        # Same bundle on the frontend so the customer-portal scan /
        # tablet PWA inherits density when the operator hits the
        # public scan endpoint from the same kiosk session.
        "web.assets_frontend": [
            "southbrook_mrp_kitchen_workcenters/static/src/scss/shopfloor_dense.scss",
            "southbrook_mrp_kitchen_workcenters/static/src/js/shopfloor_density_toggle.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
