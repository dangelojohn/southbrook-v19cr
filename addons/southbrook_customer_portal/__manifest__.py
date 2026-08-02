# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Customer Portal",
    "summary": "Customer-facing /my/kitchen-projects portal — review A/B/C "
               "concepts, approve one, download quote + drawings.",
    "description": """
Southbrook Customer Portal (Module 8 — Phase 1)
================================================

Extends Odoo's portal module with the customer-facing kitchen-project
review experience.

Routes:
  GET  /my/kitchen-projects                — list customer's projects
  GET  /my/kitchen-project/<id>            — review the A/B/C concepts
  POST /my/kitchen-project/<id>/select/<option_id>   — pick an option
  POST /my/kitchen-project/<id>/approve    — final customer approval

Approval wiring:
  Selecting an option flips sb.kitchen.design.option.is_selected
  (Module 5 one-of-N enforcement).
  Approving creates an sb.kitchen.approval (approver_type=customer)
  and advances the project state via action_customer_approves().

ACL discipline (init-doc):
  Public ACL boundary tested as anonymous SECOND customer. A customer
  must NEVER see another customer's project. Record rule on
  sb.kitchen.project: visible to a portal user only when partner_id
  matches their res.users.partner_id.

Three.js KitchenCanvas (Phase 2):
  The init-doc Module 8 calls for a Three.js live preview of the
  Configuration Engine output. That's a substantial OWL frontend
  deliverable; Phase 1 ships server-rendered QWeb option cards so the
  approval flow works end-to-end. KitchenCanvas plugs in via an extra
  template later — no model changes.
""",
    "author": "Southbrook Kitchens / OdooIQ",
    "license": "LGPL-3",
    "category": "Manufacturing",
    "version": "19.0.0.8.1",
    "depends": [
        "web",
        "website",
        "portal",
        "sale",
        "southbrook_hermes",
        # 2026-07-07 — Room Chat widget relocated here from the Order
        # Builder (views/relocated_app_mounts.xml drops its data-room-chat
        # mount into the kitchen-project detail page). Declared so the
        # widget's frontend bundle is present and load-ordered before this
        # addon's template renders the mount div.
        "southbrook_room_chat",
        "southbrook_kitchen_workspace",
        "southbrook_config_engine",
        # 2026-07-01 E2E audit — views/kitchen_portal_templates.xml lines
        # 251, 257 hit `southbrook_estimating.report_signature_spec_sheet_doc`
        # via `t-attf-href`. Runtime coupling was surviving on the
        # transitive chain (kitchen_workspace + config_engine both depend
        # on estimating). Declaring it explicitly guarantees the QWeb
        # ref resolves even if either transitive edge is dropped.
        "southbrook_estimating",
    ],
    "data": [
        "security/southbrook_customer_portal_security.xml",
        "security/ir.model.access.csv",
        "views/kitchen_portal_templates.xml",
        "views/debrand_views.xml",
        "views/relocated_app_mounts.xml",
    ],
    # NB: kitchen_canvas.js + kitchen_dims.js are NOT registered as
    # asset-bundle entries. Odoo's bundler does not transform their
    # native ES module imports (Three.js from CDN, relative
    # ./kitchen_dims.js). They are loaded directly via
    # <script type="module" src="..."> in views/kitchen_portal_templates.xml
    # and served from /southbrook_customer_portal/static/src/js/* by the
    # standard static-file handler.
    "installable": True,
    "application": False,
    "auto_install": False,
}
