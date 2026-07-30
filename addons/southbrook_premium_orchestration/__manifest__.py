{
    'name': 'Southbrook Premium MRP Orchestration',
    'version': '19.0.4.9.0',
    'summary': 'Closes the loop: cron-driven readiness/MI/analytics, always-on project-task spine, '
               'practical-intelligence telemetry, generative + planning activation.',
    'description': """
Southbrook Premium MRP Orchestration
====================================

Activates the platform's triarchic architecture (analytical / creative / practical) by closing the
data flow loop the underlying southbrook_* addons already have schemas for. See docs/DESIGN_SPEC.md.

PHASE 1 — Spine Activation
    - sale.order.action_confirm override: always create the project.task spine + backlink MOs
    - 5 scheduled crons: readiness, MI gate-checks, order analytics backfill, planning baseline,
      data quality dry-run
    - southbrook.mi.engine body (last_run telemetry + _cron_refire_gates)
    - Kitchen Ops menu surface: Kitchen Jobs / Production Release Queue / Install Risk /
      Workcenter Bottlenecks / MI Status / Tool Lifecycle Board
    - Test-user archive wizard + OPL-1 legal hold memo

PHASE 2 — Practical-Intelligence Loop
    - 30 seeded southbrook.tool.asset records keyed to real shop equipment
    - mrp.workorder.button_finish: debit tool consumption, log duration
    - southbrook.cut.spec.override model + cron that proposes ECOs when override frequency > 50%

PHASE 3 — Generative + Planning Activation
    - Gemini activation helpers + go-live checklist
    - FreeCAD bridge enable + healthcheck + first-render acceptance
    - Job templates that actually spawn task lines
    - Project data quality auto-population
""",
    'author': 'Southbrook Cabinetry',
    'website': 'https://southbrookcabinetry.space',
    'license': 'LGPL-3',
    'category': 'Manufacturing',
    'depends': [
        'mail',
        'sale_management',
        'project',
        'mrp',
        'southbrook_estimating',
        'southbrook_project',
        'southbrook_project_mrp',
        'southbrook_mrp_pm',
        'southbrook_manufacturing_intelligence',
        'southbrook_kitchen_mrp',
        'southbrook_mrp_kitchen_tools',
        'southbrook_mrp_kitchen_workcenters',
        'southbrook_plm',
        'southbrook_ai_design',
        'southbrook_freecad_bridge',
    ],
    'data': [
        # security
        'security/ir.model.access.csv',
        # phase 1 data
        'data/ir_cron.xml',
        'data/server_actions.xml',
        # phase 2 data
        'data/tool_asset_seed.xml',
        'data/eco_proposal_cron.xml',
        # phase 3 data
        'data/job_template_seed_links.xml',
        # views — kitchen ops surface
        # Action-defining view files MUST load BEFORE menus.xml. Odoo
        # resolves action="..." in <menuitem> at parse time, not at
        # end-of-data (the old comment in menus.xml claiming otherwise
        # was wrong — fresh installs failed with ParseError at menus.xml
        # while warm-registry upgrades succeeded because the records
        # were already in the DB from a prior install). Surfaced
        # 2026-06-23 by the cold-install-test.sh survey for Tier B
        # floor_traveler. Same load-order rule applies to wizards
        # referenced by menus.xml (test_user_archive_views).
        'views/kitchen_jobs_views.xml',
        'views/production_release_views.xml',
        'views/install_risk_views.xml',
        'views/workcenter_bottleneck_views.xml',
        'views/mi_engine_views.xml',
        'views/tool_lifecycle_views.xml',
        # views — extensions to existing models
        'views/sale_order_views.xml',
        'views/mrp_workorder_views.xml',
        'views/cut_spec_override_views.xml',
        # Kitchen Ops manager dashboard (proposals §3.1 POC, 2026-06-25).
        # Must load before menus.xml — menu_kitchen_ops_root's action="..."
        # references action_kitchen_ops_dashboard defined here.
        'views/kitchen_ops_dashboard_view.xml',
        # wizards (action_test_user_archive_wizard is referenced by menus.xml)
        'wizards/test_user_archive_views.xml',
        'wizards/gemini_activation_views.xml',
        'wizards/freecad_activation_views.xml',
        # menus LAST — every action it references must already be loaded.
        'views/menus.xml',
    ],
    'demo': [],
    'assets': {
        'web.assets_backend': [
            # SAMI PRD N-12 (2026-06-25): mobile-responsive Kitchen Ops
            # dashboard. Stacks the two top tile groups vertically on
            # phones, shrinks display-4 numbers, hides verbose columns.
            'southbrook_premium_orchestration/static/src/scss/kitchen_ops_dashboard.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
