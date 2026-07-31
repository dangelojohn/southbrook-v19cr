{
    "name": "Southbrook Kitchen 3D Configurator",
    "summary": (
        "Isometric Three.js kitchen room configurator backed by "
        "live Odoo cabinet inventory — design, save, and quote."
    ),
    "version": "19.0.5.35.1",
    "category": "Manufacturing/Product Configurator",
    "author": "OdooIQ / REAL Partners Ltd.",
    "website": "https://odooiq.com",
    "license": "LGPL-3",
    "depends": [
        "web", "product", "sale_management", "stock", "mrp",
        # 2026-06-28 — `<chatter/>` widget in kitchen_design_views.xml
        # requires mail.thread + mail.activity.mixin on the model.
        # Adding mail as a hard dependency so the inheritance below
        # resolves at registry load and `_get_thread_with_access`
        # is reachable for the mail/data RPC.
        "mail",
        # 2026-06-28 Tier-A — depend on southbrook_estimating so the
        # vendored r160 Three.js + the channel-pricelist resolver
        # (sale.order._resolve_channel_pricelist) are available. Drops
        # the CDN three@0.128 and unblocks dealer/contractor pricing
        # on the configurator's "Create Quotation" action.
        "southbrook_estimating",
        # Phase 0 design-system consolidation (2026-07-31) —
        # kitchen_configurator.scss already reads var(--sb-*) tokens;
        # southbrook_ui is the new canonical, dependency-light token
        # layer those names should resolve through going forward.
        "southbrook_ui",
    ],
    "data": [
        "data/template_support_products.xml",
        # Kitchen Templates T4 — the starter template catalog (6 kept
        # rows from the validated catalog design; 2 ship inactive).
        # noupdate="0" per spec locked decision #2 — dimension fixes
        # land via -u without code deploys. MUST load after
        # template_support_products.xml (appliance stand-ins).
        "data/kitchen_templates.xml",
        # 2026-07-01 E2E audit fix — security/groups.xml + kitchen_design_rules.xml
        # existed on disk but were NOT declared in this manifest. That
        # left ir.model.access.csv referring to group xml_ids
        # (`group_kitchen_readonly` etc) that no XML seed ever created,
        # so on a clean install the CSV load raised
        #    `No matching record found for external id
        #     'southbrook_kitchen_3d_configurator.group_kitchen_readonly'`.
        # Groups MUST load before the ACL CSV and before record rules.
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/kitchen_design_rules.xml",
        "views/product_template_views.xml",
        "views/kitchen_design_views.xml",
        "views/kitchen_configurator_views.xml",
        # Kitchen Templates T1 — prebuilt sample kitchen data models.
        # MUST LOAD AFTER kitchen_configurator_views.xml (menuitem
        # parent="menu_sbk_root" is defined there).
        "views/kitchen_template_views.xml",
        "data/demo_cabinets.xml",
        # v19.0.4.21.0 — audit P0#2 fix. Tags the 11 canonical
        # cabinet templates owned by southbrook_estimating with
        # southbrook_is_cabinet=True so they become visible to
        # the 3D configurator inventory. Worktop is intentionally
        # excluded (it's a countertop, not a cabinet).
        # MUST LOAD AFTER demo_cabinets.xml so any migration that
        # walks the data list bottom-up doesn't hit missing xmlids.
        "data/canonical_catalog_tag.xml",
        # 2026-07-01 E2E audit §4.A — dimensions split into a
        # noupdate="1" sibling so runtime UI edits survive -u.
        # MUST LOAD AFTER canonical_catalog_tag.xml so the two files'
        # write ordering matches the intent (tags re-apply every -u,
        # dimensions seed once and stay editable).
        "data/canonical_catalog_dimensions.xml",
        # 2026-07-01 Track B P0-B fix — attach the Manufacture route
        # (mrp.route_warehouse0_manufacture) to the 11 canonical
        # cabinet templates. Without this, procurement.group only
        # spawns Delivery after SO confirm — mrp.production is
        # NEVER created even when a mrp.bom exists on the template,
        # so Track B's autoseed path succeeds but MO handoff fails
        # silently and manufacturing never sees the order.
        # noupdate="1" so intentional per-template route edits
        # (e.g. dropship-only accessory variants) survive -u.
        # MUST LOAD AFTER canonical_catalog_tag.xml (dependency on
        # southbrook_is_cabinet=True set of templates) and after
        # canonical_catalog_dimensions.xml (same sibling pattern).
        "data/canonical_catalog_routes.xml",
        # Rec D · Sprint 1 · reconciliation cron (design → room + SO
        # line). Runs every 5 min; watermark-driven.
        "data/rec_d_reconcile_cron.xml",
        # Rec D · Sprint 2 first-touch · sale.order visibility
        "views/rec_d_sale_order_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # 2026-06-28 Tier-A — vendor r160 Three.js (UMD, air-gapped)
            # from southbrook_estimating's catalog. Must load FIRST so
            # window.THREE is populated before kitchen_configurator.js
            # references it. Replaces the previous three@0.128 CDN load.
            "southbrook_estimating/static/lib/three/three.min.js",
            # 2026-06-28 D1 — OrbitControls UMD shim (decorates window.THREE
            # with THREE.OrbitControls). Required by the Perspective view's
            # free-camera mode and zoom dolly.
            "southbrook_estimating/static/lib/three/OrbitControls.js",
            "southbrook_kitchen_3d_configurator/static/src/scss/kitchen_configurator.scss",
            # Rec D · Sprint 2d shared canvas modules. All MUST load
            # before kitchen_configurator.js so ES imports resolve.
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/constants.esm.js",
            # Task 22 review fix (SBK-036, 2026-07-30) — shared mixHex,
            # used by selection.esm.js and corner_cabinet.esm.js (both
            # listed below this line).
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/color_utils.esm.js",
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
            # Task 8 (corner-remediation, 2026-07-30) — corner-cabinet
            # massing (diagonal/pie-cut/blind), used by kitchen_canvas
            # .esm.js. Imports buildOtherCabinet from other_cabinets.esm.js
            # above (degrade-to-box path), so it's listed after it and,
            # like every other canvas module, before kitchen_canvas.esm.js.
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/corner_cabinet.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/scene_init.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/scene_dispose.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/camera_controller.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/pointer_pipeline.esm.js",
            # Rec D · Sprint 2d Step 24a — KitchenCanvas skeleton.
            # Empty component registered so the module id resolves;
            # migrated from parent in 24b-e.
            "southbrook_kitchen_3d_configurator/static/src/js/canvas/kitchen_canvas.esm.js",
            # Task 17 (corner-remediation, 2026-07-30) — SBK-030 toast
            # discipline. Pure reducer (dedupe/cap/auto-dismiss), no DOM
            # dependency; imported by kitchen_configurator.js, so it
            # must load before it, like every other ES module here.
            "southbrook_kitchen_3d_configurator/static/src/js/toast/toast_store.esm.js",
            "southbrook_kitchen_3d_configurator/static/src/js/kitchen_configurator.js",
        ],
    },
    "application": True,
    "installable": True,
    "auto_install": False,
}
