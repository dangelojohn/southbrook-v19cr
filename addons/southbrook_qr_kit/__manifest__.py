# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook QR Kit",
    "summary": "Shared QR-code foundation: HMAC-signed payloads, scan "
               "controller, scan log, print wizard, kind-registry pattern.",
    "description": """
Southbrook QR Kit
=================

Foundation addon for all QR-driven workflows across Southbrook.
Provides:

- `southbrook.qr.payload` service — HMAC-SHA256 signed payloads of
  the form `sb://<kind>/<id>?t=<unix>&s=<hmac>`. The HMAC secret is
  stored in `ir.config_parameter` `southbrook.qr_kit.hmac_secret`
  (auto-generated on first use; 32 bytes urlsafe).
- `/sb/qr/scan` controller — single entry point for every QR scan.
  Resolves the kind to a registered handler, enforces ACL, logs the
  scan, returns JSON or HTML redirect depending on request.
- `southbrook.qr.scan.log` model — append-only audit of every scan.
- "Print QR Label" wizard — generic per-model QWeb report renderer.
- "Recent Scans" tile pattern for dashboards.

KIND REGISTRY:

  Other addons register their kinds via subclassing `QrKindHandler`:

      class AsbuiltKind(models.AbstractModel):
          _name = "southbrook.qr.kind.asbuilt"
          _inherit = "southbrook.qr.kind"
          _kind_name = "asbuilt"
          _target_model = "southbrook.asbuilt"
          _default_action = "open"

  This file pulls them from the registry — no central kind list.

SECURITY:

  HMAC is the forgery defense, not a permission gate. ACL on the
  target model is still enforced. A malicious actor with a stolen
  secret could still only do what their Odoo user is allowed to do.

  Optional `expires_in_seconds` per kind — handles "one-time use"
  receipts (e.g. ephemeral POD QRs that expire in 24h).
""",
    "version": "19.0.0.16.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "category": "Manufacturing",
    # W035 (R8.14, 2026-06-27): hr depends added — scan log gains
    # `employee_id` and /sb/qr/identify resolves hr.employee.pin.
    # W071 (R8.10, 2026-06-27): mrp depends added — trolley QR kind
    # binds a pre-staged stock.picking (internal) to mrp.workorder via
    # the new x_sbk_trolley_id field; resolution code reads
    # employee_assigned_ids / operator_id from mrp.workorder.
    "depends": ["base", "web", "mail", "stock", "hr", "mrp"],
    "data": [
        "security/ir.model.access.csv",
        "data/qr_kit_config_parameters.xml",
        "data/ir_cron.xml",
        "data/shipping_unit_seed.xml",
        "data/truck_load_seed.xml",
        "views/southbrook_qr_scan_log_views.xml",
        "views/shipping_unit_views.xml",
        "views/truck_load_views.xml",
        "views/floor_shell_template.xml",
        "wizards/southbrook_qr_label_print_wizard_views.xml",
    ],
    "external_dependencies": {
        "python": ["qrcode"],
    },
    # W036 (R8.7, 2026-06-27) — scan-success/fail audio cues for the
    # tablet/handheld scan flow. Loads into web.assets_backend AND
    # web.assets_frontend so both backend OWL clients and any public
    # PWA scan UI emit the same beep. Self-installs by patching the
    # global fetch + XMLHttpRequest layers; default = unmuted.
    # W035 (R8.14, 2026-06-27) — operator PIN modal + top-bar badge.
    # UX-P1 (2026-07-31, ux-shopfloor.md #1) — this comment used to
    # claim the two files below were "also bundled into BOTH backend
    # and frontend"; they were NOT actually listed in the assets dict
    # (verified by grep before this fix), so no page ever loaded them
    # and every floor scan was attributed anonymously. Now bundled
    # into backend, frontend, AND web.assets_qr_floor — the floor
    # shell (views/floor_shell_template.xml) only pulls the
    # `web.assets_qr_floor` bundle, NOT backend/frontend, so the
    # qr_floor entry is the load-bearing one for Bin-Scan/Load-Unit;
    # backend+frontend keep the badge visible everywhere else the
    # kiosk might land, matching the original intent.
    # W038 (R8.5, 2026-06-27) — Night-shift dark mode. SCSS + toggle
    # JS bundled into BOTH backend and frontend so kanban, scan modal,
    # POD page, and traveler-print preview all honour the same body
    # class. Default OFF — day-shift users see zero visual change.
    # W037 (R8.4, 2026-06-27) — Offline scan queue: the SW glue
    # (`offline_scan_queue.js`) lives in the bundle so it
    # auto-registers on every page; the SW itself (`offline_scan_sw.js`)
    # is NOT bundled — it's served by the `/sb/qr/sw.js` route at
    # top-level scope so the browser grants it `/sb/qr/*` scope.
    "assets": {
        "web.assets_backend": [
            "southbrook_qr_kit/static/src/js/scan_audio_cue.js",
            "southbrook_qr_kit/static/src/js/dark_mode_toggle.js",
            "southbrook_qr_kit/static/src/scss/dark_mode.scss",
            "southbrook_qr_kit/static/src/js/operator_pin_modal.js",
            "southbrook_qr_kit/static/src/scss/operator_pin_modal.scss",
            "southbrook_qr_kit/static/src/js/offline_scan_queue.js",
        ],
        "web.assets_frontend": [
            "southbrook_qr_kit/static/src/js/scan_audio_cue.js",
            "southbrook_qr_kit/static/src/js/dark_mode_toggle.js",
            "southbrook_qr_kit/static/src/scss/dark_mode.scss",
            "southbrook_qr_kit/static/src/js/operator_pin_modal.js",
            "southbrook_qr_kit/static/src/scss/operator_pin_modal.scss",
            "southbrook_qr_kit/static/src/js/offline_scan_queue.js",
        ],
        # W073 (R8.8, 2026-06-27) — Floor companion UI (Bin Scan +
        # Load Unit standalone screens). Lazy by route, not by bundle:
        # the screens are gated behind `data-sb-floor-app` on the
        # mount element, so the boot is a no-op on every other page
        # in the frontend bundle. Net cost to non-floor pages is the
        # one tiny `if (!root) return;` check at DOMContentLoaded.
        # The scan-audio + offline-queue already live in the same
        # bundle so they get reused; SCSS is body-class-scoped to
        # `.sb_floor` so no other page is restyled.
        # UX-P1 (2026-07-31) — operator_pin_modal.js/.scss added here
        # too: this is the ONLY bundle the floor shell template loads
        # (floor_shell_template.xml calls `web.assets_qr_floor`, not
        # assets_backend/assets_frontend), so without this entry the
        # PIN modal still never appears on the actual Bin-Scan /
        # Load-Unit tablets no matter what's in the other two bundles.
        "web.assets_qr_floor": [
            "southbrook_qr_kit/static/src/js/scan_audio_cue.js",
            "southbrook_qr_kit/static/src/js/offline_scan_queue.js",
            "southbrook_qr_kit/static/src/js/operator_pin_modal.js",
            "southbrook_qr_kit/static/src/scss/operator_pin_modal.scss",
            "southbrook_qr_kit/static/src/js/floor_screens.esm.js",
            "southbrook_qr_kit/static/src/scss/floor_screens.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
