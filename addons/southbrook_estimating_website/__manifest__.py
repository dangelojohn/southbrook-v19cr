# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Estimating — Website",
    "summary": "The customer-facing one-page kitchen configurator on "
               "southbrookcabinetry.space (Phase 2 + Phase 3 deliverable).",
    "version": "19.0.31.16.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Website/eCommerce",
    #
    # Independently deployable from southbrook_estimating so:
    #   - The sales-rep backend can run without the public website addon
    #     (useful for early integration testing + dealer terminals).
    #   - Backend load is not bloated by the Three.js asset bundle.
    #
    # See ../../CLAUDE.md §3 "Why two addons, not one" for the rationale.
    #
    "depends": [
        "southbrook_estimating",
        "website_product_configurator",   # OCA — public-facing wizard base
        # Declared explicitly (already pulled in transitively via
        # website_product_configurator) because models/website.py
        # inherits website_sale's Website model directly (Task 30,
        # corner-remediation-2026-07-30 shop-exposure gate).
        "website_sale",
        "portal",                          # /my/... portal layout + auth
        # views/auth_template.xml inherits auth_passkey.auth_passkey_login_oauth;
        # it is auto_install upstream, but declare it so the inherit is explicit
        # (avoids an install-order ParseError if auto_install ever changes).
        "auth_passkey",
        # 2026-07-03 T1 — the new "3D Design" tab reuses the standalone
        # Configurator's KitchenCanvas OWL engine + persists cabinet
        # positions to the existing southbrook.kitchen.design(.line)
        # model. We depend on the configurator for those models + the
        # canvas/* JS modules (added to web.assets_frontend below).
        "southbrook_kitchen_3d_configurator",
        # 2026-08-02 — design-token lint ratchet: design_tab.scss,
        # planner.scss, portal_root.scss and room_layout.scss now
        # reference the canonical --sb-status-* tokens directly.
        "southbrook_ui",
    ],
    "data": [
        # Track 2 commit 1 — portal-route view templates.
        "security/ir.model.access.csv",
        "views/portal_template.xml",
        # Phase 2 commit 1 — /kitchen-planner customer route + template.
        "views/kitchen_planner_template.xml",
        # G1 + G2 (2026-06-01) — public Southbrook homepage at /.
        "views/homepage_template.xml",
        # 2026-06-15 — public commercial Odoo Projects landing page.
        "views/commercial_template.xml",
        # 2026-07-06 — downloadable documentation PDFs (Features, Brochure,
        # Quick Start Guide, User Manual) + A4 paperformat + report actions.
        # Rendered publicly by controllers/docs.py; linked from the homepage
        # Resources section.
        "reports/southbrook_docs.xml",
        # G4 + G5 + G6 + G8 (2026-06-01) — branded auth pages
        # (login/signup chrome) + project-name field on signup.
        "views/auth_template.xml",
        # 2026-06-02 — fix 500 on /shop/<slug> caused by upstream
        # OCA website_product_configurator chaining .currency_id on a
        # potentially non-singleton pricelist recordset. Override
        # swaps to website.currency_id (singleton, always available).
        "views/shop_configurator_currency_fix.xml",
        # 2026-06-26 Stage 3a — anti-FOUC inline <script> for the
        # design system theme toggle. MUST inline in <head> per the
        # brief (asset bundle JS loads too late to prevent flash).
        "views/design_system_chrome.xml",
    ],
    # Dedicated asset bundle (charter Q4 answer) so the OWL portal
    # components only load on the Order Builder route. Other portal
    # pages (/shop, /my, etc.) stay clean.
    #
    # T2C2 adds portal_boot.esm.js — the OWL bootstrap script that
    # finds the mount-point div on the portal page and mounts the
    # <OrderBuilder/> component into it.
    "assets": {
        "web.assets_frontend": [
            # Phase 3 Sprint A1 — vendored @font-face declarations
            # for Roboto Flex + JetBrains Mono. MUST load before the
            # design tokens so subsequent SCSS sees the families
            # already declared. Air-gapped (no Google Fonts CDN).
            "southbrook_estimating_website/static/src/scss/fonts.scss",
            # Step 2 (2026-06-01) — shared Signature Series design
            # tokens, loaded FIRST after fonts. Cross-addon path
            # because the website depends on the estimating addon
            # (which owns the design spine). See
            # docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §5.
            "southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss",
            # Phase 2.5 — Three.js library back-ported from
            # southbrook_estimating Track 1. Same vendored r160 bundle.
            "southbrook_estimating/static/lib/three/three.min.js",
            "southbrook_estimating/static/lib/three/OrbitControls.js",
            # 2026-07-03 T1 — the standalone Configurator's KitchenCanvas
            # engine, reused verbatim for the new "3D Design" tab. These
            # are the SAME files the configurator loads in web.assets_backend
            # (southbrook_kitchen_3d_configurator/__manifest__.py); an Odoo
            # bundle may reference files from any installed addon. Order
            # mirrors the backend bundle's dependency order. kitchen_configurator.js
            # (the backend parent) is deliberately NOT included — it uses
            # useService("action"), whose service is absent from
            # web.assets_frontend. A new portal parent (design_tab.esm.js)
            # drives KitchenCanvas instead.
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/constants.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/three_loader.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/pointer_helpers.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/view_specs.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/mesh_factory.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/pack_row.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/easing.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/ortho_frustum.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/selection.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/drop_raycaster.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/room_shell.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/drag_handle.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/drop_lanes.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/pbr_env_map.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/base_cabinet.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/wall_cabinet.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/other_cabinets.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/scene_init.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/scene_dispose.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/camera_controller.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/pointer_pipeline.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/kitchen_canvas.esm.js",
            # Touch editing v1 (2026-08-02 spec) — the shared control-pad
            # SCSS (T3, Wave 1) + component/pure-helpers module (Wave 2).
            # Same file, same order-relative-to-kitchen_canvas.esm.js
            # rule as the backend bundle (southbrook_kitchen_3d_
            # configurator/__manifest__.py) — design_tab.esm.js (below)
            # imports <TouchPad>, so this must load before it.
            "southbrook_kitchen_3d_configurator/static/src/scss/touch_pad.scss",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/touch_pad.esm.js",
            # 2026-06-22 — Tier-1 cabinet GLB pipeline (see
            # static/lib/cabinets/README.md). The GLTFLoader entry is
            # COMMENTED until the vendor lib is dropped at
            # static/lib/three/GLTFLoader.js. The loader module stays
            # registered either way — it degrades to a console.warn +
            # BoxGeometry fallback when GLTFLoader is missing.
            #
            # "southbrook_estimating/static/lib/three/GLTFLoader.js",
            "southbrook_estimating/static/src/js/cabinet_glb_loader.esm.js",
            "southbrook_estimating_website/static/src/scss/portal_root.scss",
            # 2026-06-26 Stage 3c — cascade-tie resolver. MUST load
            # AFTER portal_root.scss so equal-specificity .sb-* rules
            # win the source-order tie against legacy .o_owl_* rules.
            # Without this, the modal Send-to-Production primary, the
            # ILLUSTRATIVE SEED banner border/radius, and the order-
            # lines table header (hardcoded #faf4e8) stay on the old
            # palette.
            "southbrook_estimating_website/static/src/scss/_southbrook_design_overrides.scss",
            # Phase 2.B (2026-06-27) — Room Setup tab styles. Loaded
            # after portal_root.scss so the --sb-* tokens are bound to
            # the active theme; sb-room-* classes only attach inside
            # the new tab panel, no other surface is affected.
            "southbrook_estimating_website/static/src/scss/room_layout.scss",
            # G1 + G2 (2026-06-01) — homepage hero + features SCSS.
            "southbrook_estimating_website/static/src/scss/homepage.scss",
            # Phase 2 commit 1 — kitchen-planner three-pane SCSS.
            # Loads AFTER portal_root.scss so the :root tokens defined
            # there are available to .o_kp_* selectors.
            "southbrook_estimating_website/static/src/scss/planner.scss",
            # 2026-07-03 T1 — "3D Design" tab layout (hosts the reused
            # KitchenCanvas .o_sbk_canvas3d in the portal).
            "southbrook_estimating_website/static/src/scss/design_tab.scss",
            # Order matters: KitchenViewport class is imported by
            # portal_boot, so it must load first.
            "southbrook_estimating_website/static/src/js/kitchen_viewport.esm.js",
            # 2026-07-03 T1 — new portal parent that drives KitchenCanvas
            # in the "3D Design" tab (imported by portal_boot below).
            "southbrook_estimating_website/static/src/js/design_tab.esm.js",
            "southbrook_estimating_website/static/src/js/portal_boot.esm.js",
            # Phase 2.C (2026-06-27) — Room Setup wizard. Loaded AFTER
            # portal_boot.esm.js so the rpcJsonCall export resolves; the
            # XML template ships in the same bundle so OWL can find the
            # registered templates at component instantiation.
            # Phase 3.B (2026-06-27) — room_geometry.esm.js is the shared
            # shape→walls helper consumed by both RoomOutlinePreview
            # (wizard) and FloorPlanSVG (Room Layout tab). Loads BEFORE
            # both consumers.
            "southbrook_estimating_website/static/src/js/room_geometry.esm.js",
            # Stage B (2026-06-28) — architectural symbol library
            # consumed by FloorPlanSVG to render per-type top-down
            # symbols inside constraint polygons. Loads BEFORE
            # room_layout.esm.js so the import resolves at first
            # FloorPlanSVG mount.
            "southbrook_estimating_website/static/src/js/architectural_symbols.esm.js",
            # Stage C (2026-06-28) — AppliancePalette docked sidebar +
            # drag-and-drop. Loads BEFORE room_layout.esm.js so the
            # import resolves at RoomLayoutTab mount.
            "southbrook_estimating_website/static/src/js/appliance_palette.esm.js",
            "southbrook_estimating_website/static/src/xml/appliance_palette.xml",
            "southbrook_estimating_website/static/src/js/room_setup_wizard.esm.js",
            "southbrook_estimating_website/static/src/xml/room_setup_wizard.xml",
            # Phase 3.B (2026-06-27) — Room Layout tab. Read-only top-
            # down floor plan SVG + per-wall metrics + unplaced cabinet
            # sidebar. Click handlers, drag, and the elevation toggle
            # are Phase 3.C. Stage B (2026-06-28) — type-specific
            # architectural symbols replace the labeled rectangles.
            "southbrook_estimating_website/static/src/js/room_layout.esm.js",
            "southbrook_estimating_website/static/src/xml/room_layout.xml",
            # Phase 2 commit 2 — OWL <KitchenPlanner/> boot for
            # /kitchen-planner customer route. Independent of
            # portal_boot.esm.js (each bootstrap finds its own
            # mount-point div and returns early if absent on the
            # current page).
            "southbrook_estimating_website/static/src/js/planner_boot.esm.js",
            # 2026-06-26 Stage 3a — design-system theme toggle button
            # (creates the fixed-position top-right toggle on every
            # portal page; pairs with views/design_system_chrome.xml
            # which inlines the anti-FOUC guard).
            "southbrook_estimating_website/static/src/js/sb_theme_toggle.esm.js",
            # 2026-07-06 — public homepage hero: interactive 3D sample
            # preview (a non-persisting, backend-free Three.js toy that
            # teases the real Order Builder). Self-mounts on
            # .o_sb_sample3d_host (the homepage hero card) and no-ops on
            # every other frontend page. Uses the vendored window.THREE +
            # THREE.OrbitControls loaded above (lines ~75-76), so it must
            # come after them — it does. No imports; independent bootstrap.
            "southbrook_estimating_website/static/src/js/sample_3d_widget.esm.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
