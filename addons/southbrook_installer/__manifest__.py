# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Cabinet Installer",
    "summary": "End-to-end on-site cabinetry installation management: "
               "stage workflow, 11-phase install log, photo gates, GPS "
               "arrival/departure, builder sign-off, and dispatcher view. "
               "Phase 1 of the SAMI installer build.",
    "description": """
Southbrook Cabinet Installer
=============================

Foundation module for installation crew operations. Models the
installer job lifecycle from scheduled → invoiced through an 8-stage
state machine and an 11-phase per-job install log with photo-gated
phase completion.

CURRENTLY SHIPS (Phase 1.1 of v1 spec):

- ``southbrook.installer.stage`` — workflow stages with exit-gate
  conditions (delivery confirmed, all phases done, sign-off, closeout).
- ``southbrook.installer.phase`` — 11-phase template (site assessment,
  layout, uppers, bases, tall, fillers, doors, hardware, lighting,
  trim, punch-list prep).
- ``southbrook.installer.stage.log`` — per-job phase log with
  photo-gated completion, GPS, and duration tracking.
- ``southbrook.installer.job`` — the central job record. Inherits
  chatter and activity mixins. Auto-numbered via ``ir.sequence``
  (``INST-%(year)s-%(seq)05d``). On create, auto-spawns one
  stage_log per active phase.

GATE PHILOSOPHY:

  Gates are *exit conditions* on a stage. ``action_advance_stage()``
  collects ALL failing gates and surfaces them in a single
  ``UserError`` — the installer is never asked to submit ten times
  to discover ten different problems.

PLACEHOLDER FIELDS (wired in later phases):

- ``delivery_confirmed`` — flipped manually here; computed from
  ``southbrook.delivery.manifest`` in Phase 1.2.
- ``sign_request_ref`` — Char placeholder; replaced with a
  Many2one → sign.request in Phase 2.3 (sign module is not in
  this stack today).
- ``closeout_done`` — flipped manually here; computed from
  ``southbrook.installer.closeout`` in Phase 1.3.

SAFE DEPS ONLY:

  ``project`` for project_id, ``hr`` for installer/crew, ``stock``
  for kit_picking_id. ``sign`` and ``sms`` deliberately omitted;
  cold install must succeed in this stack as-is.
""",
    "version": "19.0.4.0.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "category": "Manufacturing",
    "depends": [
        "base",
        "mail",
        "project",
        "hr",
        "stock",
        "purchase",
        "maintenance",
        "account",
        "bus",
        "web",
        "southbrook_ui",
    ],
    "data": [
        "security/southbrook_installer_groups.xml",
        "security/ir.model.access.csv",
        "security/southbrook_installer_record_rules.xml",
        "data/southbrook_installer_sequence_data.xml",
        "data/southbrook_installer_stage_data.xml",
        "data/southbrook_installer_phase_data.xml",
        "data/southbrook_installer_punchlist_template_data.xml",
        "views/southbrook_installer_stage_views.xml",
        "views/southbrook_installer_phase_views.xml",
        "views/southbrook_installer_stage_log_views.xml",
        "views/southbrook_damage_flag_views.xml",
        "views/southbrook_delivery_manifest_views.xml",
        "views/southbrook_installer_tool_loan_views.xml",
        "views/southbrook_installer_closeout_views.xml",
        "views/southbrook_installer_punchlist_views.xml",
        "views/southbrook_installer_job_views.xml",
        "views/southbrook_installer_dashboard_views.xml",
        "views/southbrook_installer_menus.xml",
        "reports/installer_job_summary_report.xml",
        "reports/return_manifest_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "southbrook_installer/static/src/js/installer_dashboard.js",
            "southbrook_installer/static/src/xml/installer_dashboard.xml",
            "southbrook_installer/static/src/css/installer_dashboard.css",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
