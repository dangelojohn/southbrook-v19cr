# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Floor Traveler",
    "summary": (
        "P8 — Per-production-package PDF traveler with QR code, "
        "workcenter scan endpoint, and scan-driven tool-consumption "
        "telemetry. Closes the shop-floor scan loop without "
        "duplicating the existing mrp.workorder.button_finish path."
    ),
    "description": """
Southbrook Floor Traveler (audit P8)
====================================

Adds three pieces:

  1. **QWeb PDF traveler** (``ir.actions.report``) per
     ``sb.production.package`` with a QR code encoding the package id.
     Operators print one per shop-floor job.
  2. **Scan endpoint** ``/southbrook/api/floor-traveler/scan`` that
     advances the next ``mrp.workorder`` and calls the *existing*
     ``button_finish`` so the tool-consumption debit fires exactly
     once (no duplicate telemetry). The audit's load-bearing
     acceptance criterion.
  3. **Scan-log JSON field** on ``sb.production.package`` storing
     ordered scan events; surfaced as durations on the MI dashboard
     via the existing view extension points.

External dependencies:
  - ``qrcode`` (Python) for QR generation. Pure-Python; falls back
    to a tiny stub if the library isn't available so the addon
    still installs and the report renders without the QR image.
""",
    "author": "Southbrook Cabinetry / OdooIQ",
    "license": "LGPL-3",
    "category": "Manufacturing",
    "version": "19.0.2.1.1",
    "depends": [
        "mrp",
        "sale",
        "southbrook_kitchen_mrp",
        "southbrook_premium_orchestration",
        "southbrook_mrp_pm",
        # UX-P1 (2026-07-31, ux-shopfloor.md #2) — the scan-result
        # confirmation page (views/scan_result_template.xml) colours
        # its status card ONLY via southbrook_ui's --sb-status-*
        # tokens (see the southbrook-design-system skill: never
        # hardcode a status colour). southbrook_ui depends on nothing
        # but `web` and ships assets only — safe to add here.
        "southbrook_ui",
    ],
    # qrcode is a soft dependency. The QR computation gracefully falls
    # back to an empty image when the lib is missing (the traveler PDF
    # still renders with a "QR unavailable" placeholder). Declaring it
    # under external_dependencies would block installs on environments
    # without the lib — we'd rather degrade gracefully.
    "data": [
        # No security/ir.model.access.csv: this addon extends an existing
        # model (sb.production.package) via _inherit; existing ACLs apply.
        # P0 hard rule #2 explicitly bars widening existing permissions.
        "reports/floor_traveler_report.xml",
        "reports/floor_traveler_report_action.xml",
        # UX-P1 — human-facing scan confirmation page (see controllers/
        # scan_endpoint.py's new GET route).
        "views/scan_result_template.xml",
        "data/floor_traveler_config_parameters.xml",
    ],
    # UX-P1 (2026-07-31, ux-shopfloor.md #2) — lean, dedicated bundle
    # for the scan-result page only, mirroring southbrook_qr_kit's
    # `web.assets_qr_floor` pattern: NOT web.assets_backend/frontend,
    # so a shop-floor tablet scanning a traveler doesn't pay the full
    # backend/Bootstrap bundle cost for a page that is one status
    # card. Pulls southbrook_ui's token file directly from ITS static
    # path — no edit to southbrook_ui itself, just a reference to a
    # file that addon already ships, gated by the `depends` above so
    # it's guaranteed present at install/upgrade time.
    "assets": {
        "web.assets_floor_traveler_scan": [
            "southbrook_ui/static/src/scss/_sb_tokens.scss",
            "southbrook_floor_traveler/static/src/scss/scan_result.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
