# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Estimating",
    "summary": "A Prodboard-class kitchen Order Builder on Odoo 19 CE — "
               "multi-zone grid, 6-channel pricelist, parametric BoM, "
               "Signature Spec Sheet PDF.",
    "description": """
Southbrook Estimating
=====================

The sales-rep-facing Order Builder for Southbrook Kitchens on Odoo 19.0
Community Edition, built on top of the OCA product_configurator suite.

This addon ships:

* The Order Builder backend with multi-zone grid (BASE_RUN / WALL /
  TALL / ISLAND / ACCESSORY / OTHER) and "Duplicate as Draft" action
  for iterative-design workflows.
* 11 declarative configurator attributes (plus 3 derived) seeded with
  12 cabinet templates and 65 product.config.line rule records covering
  4 business rules (series-door, box-series, width-doorcount,
  family-softclose).
* 6 channel pricelists (Retail / Dealer / Tradesperson / KD / Big-Box /
  Refacing CTHS) plus 3 tradesperson tier sub-pricelists, dispatched
  via class-level tables.
* Parametric panel-cut math via models/mrp_bom.py with NF14-documented
  geometric conventions (frameless euro construction; named constants
  swap when canonical specifications land).
* QWeb reports: Signature Spec Sheet (sale.order-bound, customer PDF),
  Shop Copy (mrp.production-bound, shop-floor companion), Door Order
  (sale.order-bound per-SO door schedule).
* The southbrook.order.analytics companion model captures channel,
  series, lifecycle timestamps, and BoM-rollup counts on every order
  confirm — the AI data spine for future forecast / quote / yield work.
* 212+ automated tests (post 2026-07-01 E2E audit) including a 10-step
  Phase-1 smoke gate, declarative rule-firing tests (Rules 2/3/4),
  per-channel pricelist math, and a Q7 per-line re-price assertion.

Dependencies
------------

This addon depends on the OCA product_configurator suite (product_configurator,
product_configurator_mrp, product_configurator_sale) which is NOT on the
Odoo Apps Store. Install from https://github.com/OCA/product-configurator
before installing this addon.

Phase scope
-----------

This is Phase 1 of a 4-phase build. The customer-facing one-page
configurator (Phase 2), the Three.js procedural 3D layer (Phase 3),
and the Accucutt cut-list bridge (Phase 4) ship in subsequent releases.

Documentation
-------------

See CHANGELOG.md for the release notes, README.md for the canonical
design-docs index, and PUNCHLIST.md for the locked-decisions trace
(referenced from every commit body by Q-number and NF-number).
""",
    "version": "19.0.9.2.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "maintainers": ["southbrook"],
    "website": "https://southbrookcabinetry.space",
    "support": "support@southbrookcabinetry.space",
    "category": "Sales/Configurator",
    "images": [
        "static/description/banner.png",
    ],
    #
    # See ../../CLAUDE.md for the operating brief and ../../docs/
    # for the canonical architecture, business rules, and reference
    # artifacts. README.md in this directory points to both.
    #
    "depends": [
        # OCA configurator suite (4 modules, untouched per CLAUDE.md §3)
        "product_configurator",
        "product_configurator_mrp",
        "product_configurator_sale",
        # Odoo core — sales + manufacturing + accounting + stock spine
        # per Build Spec §0 / §2.1 ("the data spine").
        "mrp",
        "sale_management",
        # sale_mrp is the BRIDGE addon that wires mrp.production back to
        # sale.order.line (mo.sale_line_id.order_id). Marked auto_install
        # upstream but not picked up reliably in QNAP-style stacks where
        # the install runs against a partially-bootstrapped registry.
        # NF25 (live test run 2026-05-30): Shop Copy report assumed the
        # bridge was loaded; without it `mo.sale_line_id` raises
        # AttributeError. Explicit dep guarantees the install.
        "sale_mrp",
        "stock",
        "account",
        "contacts",
        "crm",
        # QR foundation — qr.mixin + scan controller for builder PO intake.
        "southbrook_qr_kit",
    ],
    # ------------------------------------------------------------------
    # Python external dependencies.
    # `mako` is required by product_configurator (declared upstream); we
    # re-declare here so a fresh Odoo container without the OCA suite
    # pre-warmed still raises a clear ImportError pointing at this
    # manifest, not a tracebacks-deep call inside the configurator.
    # Install via:  pip install --break-system-packages Mako
    # (or your distro's python3-mako). The dependency does NOT come from
    # apt on the Odoo 19 official Debian image — confirmed at NF1 (live
    # install on QNAP southbrook stack 2026-05-30).
    # ------------------------------------------------------------------
    "external_dependencies": {
        "python": ["mako"],
    },
    "data": [
        "security/ir.model.access.csv",
        # Commit 2 — seed parameters + res.partner view extension
        "data/config_parameters.xml",
        # Customer property-type classification — Contact Tags tree
        # (Residential: House/Condo/Apartment · Commercial: Office/Retail/
        # Restaurant/Multi-unit). Pipeline stage is a computed field on
        # res.partner (models/res_partner.py), not a tag.
        "data/customer_category_tags.xml",
        # Branded customer quote email (logo + Sky/Walnut/Linen palette)
        "data/mail_template_quote.xml",
        "views/res_partner_views.xml",
        # Commit 3 — configurator attribute vocabulary
        "data/attributes.xml",
        # 19.0.4.2.0 (2026-06-18) — Product Configurator/MRP
        # BoM-line Configuration Sets. These seed reusable manufacturing
        # conditions but intentionally do not attach to BoM lines yet.
        "data/configuration_sets.xml",
        # Commit 4 — 6 channel pricelists + 3 tradesperson sub-tiers
        "data/pricelists.xml",
        # Commit 7 — 12 cabinet templates + 132 attribute_lines
        # LOAD ORDER MATTERS: templates BEFORE config_rules. The
        # product.config.line records in config_rules.xml use ref()
        # on attribute_line_ids defined in product_templates.xml. If
        # this list gets alphabetised by a future contributor, install
        # fails with "external id not found" on the rule records.
        "data/product_templates.xml",
        # Commit 5+7 — 4 rule triggers + 65 per-template config.line records
        "data/config_rules.xml",
        # Phase 2K (2026-06-09) — wizard step grouping. Four config.step
        # buckets (Construction / Door & Finish / Hardware / Interior) +
        # 40 step_lines binding each cabinet's attribute_lines into
        # exactly one bucket. MUST load AFTER product_templates.xml so
        # the attribute_line refs resolve.
        "data/config_steps.xml",
        # G13 (2026-06-01) — retail list_price seed for the 12 Q8
        # cabinet templates so price transparency in the customer
        # Order Builder has real numbers to show. Must load AFTER
        # product_templates.xml (overrides list_price=0.0 set there).
        "data/cabinet_prices.xml",
        # 19.0.7.2.0 (2026-07-01) — PTAV price_extra backfill on 5 OCA-
        # configured anchor values (Maple, Signature, five-piece,
        # soft-close, Blum Legrabox). noupdate="1" so product-owner
        # runtime edits survive -u. MUST load AFTER cabinet_prices.xml
        # (Maple derivation depends on the seeded list_price) AND
        # AFTER product_templates.xml (PTAV rows must exist). See
        # docs/track_c_price_extra_pending_2026-07-01.md for the anchor
        # decisions + the finish_premium_colors deferral note.
        "data/attribute_values_price_extra.xml",
        # 19.0.1.1.0 (2026-06-02) — catalog-picker redesign metadata
        # seed (category / description / dimensions / icon_key for all
        # 12 Q8 cabinets). Must load AFTER product_templates.xml since
        # it updates records by xml_id reference. Backfill on existing
        # DBs is handled by migrations/19.0.1.1.0/post-migrate.py.
        "data/cabinet_catalog_metadata.xml",
        # Commit 9 — Order Builder views, user-prefs view, menu
        "views/sale_order_views.xml",
        # Room-First UX Phase 1.3 — southbrook.room backend views, SO smart button.
        # MUST load AFTER sale_order_views.xml: inherits the SO form view
        # which is augmented there, and registers the menu under
        # menu_southbrook_root (defined in sale_order_views.xml).
        "views/southbrook_room_views.xml",
        # Phase 6.2 (2026-06-27) — Room Templates library seed.
        # MUST load AFTER security (ACL must exist) and AFTER the room
        # views block (model registration order, conventional).
        # noupdate="0" so seed updates propagate on -u.
        "data/room_templates.xml",
        "views/res_users_views.xml",
        # Commit 10 — QWeb reports (routine #6 partial)
        # Styles MUST load before the report templates that reference them
        # via t-call. If reordered, the templates fail to render.
        "reports/southbrook_report_styles.xml",
        "reports/signature_spec_sheet.xml",
        "reports/shop_copy.xml",
        "reports/door_order.xml",
        # Phase 2 Track 1 — 3D viewport injected into the OCA wizard form.
        "views/product_configurator_3d_view.xml",
        # NF26 — reliable Southbrook 3D Configure stat button so users
        # always have a launch point regardless of OCA header inherit order.
        "views/product_template_3d_launch_view.xml",
        # NF27 — one-click "Launch 3D Configurator" menu item under
        # Southbrook Estimating. Bypasses the product form button entirely.
        # MUST load AFTER sale_order_views.xml (which defines menu_southbrook_root).
        "views/launch_3d_menu.xml",
        # Bug #4 (2026-06-22) — wizard Next→Confirm on the final step.
        # Inherits product_configurator.product_configurator_form so it
        # must load after that addon is installed (depends list already
        # guarantees this).
        "views/product_configurator_wizard_view.xml",
        # C1 fix: plain template-picker dialog for the header Configure button
        "views/config_template_picker_views.xml",
        # A1 (2026-06-18) — Prodboard cabinet-archetype taxonomy seed.
        # Loads after security so the access rules exist when the seed
        # creates archetype records. Idempotent.
        "data/prodboard_taxonomy_seed.xml",
        # A5 (2026-06-18) — Type-encoded SB-* default_code assignment
        # on the Q8 Southbrook templates. Idempotent; never overwrites
        # an existing default_code. Must load AFTER product_templates.xml
        # so the xml_id targets exist.
        "data/template_code_assign.xml",
        # Prodboard catalogue mapping — assigns cloned archetype refs to
        # the locked 12 Q8 templates. Must load after product templates
        # and after the taxonomy seed has upserted archetypes.
        "data/template_archetype_assign.xml",
        # Builder PO intake stub (SAMI PRD #16, 2026-06-26).
        "data/builder_po_intake_seed.xml",
        "views/builder_po_intake_views.xml",
        # M1 rules-as-data corner cabinetry layer
        # (docs/research/corner-engine/09-rule-engine-spec.md §6-7).
        # MUST load AFTER product_templates.xml so the seeder's
        # default_code search for SB-CORNER finds a real template.
        "data/placement_rules.xml",
    ],
    # ------------------------------------------------------------------
    # Asset bundles — Track 1 (3D cabinet viewport).
    #
    # Three.js vendored on 2026-05-30 (r160 / 0.160.0 UMD build, MIT-
    # licensed; LICENSE.txt rides along per static/lib/three/README.md).
    # The two lib entries MUST load before cabinet_viewport.esm.js —
    # the OWL component does a runtime `window.THREE` check and falls
    # back to a "Three.js not loaded" placeholder if absent.
    # ------------------------------------------------------------------
    "assets": {
        "web.assets_backend": [
            # Step 2 (2026-06-01) — shared Signature Series design
            # tokens, loaded FIRST so CSS custom properties + utility
            # classes are available to every downstream Southbrook
            # SCSS file. See docs/CUSTOMER_TO_MANUFACTURING_FLOW.md §5.
            "southbrook_estimating/static/src/scss/_southbrook_design_tokens.scss",
            "southbrook_estimating/static/lib/three/three.min.js",
            "southbrook_estimating/static/lib/three/OrbitControls.js",
            # 2026-06-22 — Tier-1 cabinet GLB pipeline. The GLTFLoader
            # entry is COMMENTED until the vendor lib is dropped at
            # static/lib/three/GLTFLoader.js (see
            # static/lib/cabinets/README.md "Vendoring THREE.GLTFLoader"
            # for the one-line curl command). The loader module below
            # is safe to ship even when GLTFLoader is absent — it
            # console.warn's once and returns null from loadCabinet so
            # every cabinet falls back to BoxGeometry.
            #
            # "southbrook_estimating/static/lib/three/GLTFLoader.js",
            "southbrook_estimating/static/src/js/cabinet_glb_loader.esm.js",
            "southbrook_estimating/static/src/scss/cabinet_viewport.scss",
            "southbrook_estimating/static/src/js/cabinet_viewport.esm.js",
            "southbrook_estimating/static/src/xml/cabinet_viewport.xml",
        ],
    },
    # ------------------------------------------------------------------
    # Demo data — loaded only with --demo flag (or per-module demo flag).
    # All files are noupdate="1" so demo modifications during gate
    # review survive subsequent -u southbrook_estimating runs.
    #
    # LOAD ORDER MATTERS:
    #   1. demo_partners.xml — partners + their property_product_pricelist
    #      (all 6 point at pricelist_retail per the corrected PT-P1-03).
    #   2. demo_variants.xml — one product.product per cabinet template
    #      (the 12 templates use create_variant='dynamic' per Q6, so
    #      variants don't materialise automatically; demo_variants seeds
    #      a representative "default config" variant per template
    #      bypassing the Q6 dynamic-variant rule for demo purposes).
    #   3. demo_orders.xml — 6 quotes + 5 confirmed orders + the
    #      gate-walk-canonical PT-P1-01-reproducibility order, all
    #      referencing demo_variant_* xml_ids from #2.
    #
    # NF22 (caught 2026-05-30) RESOLVED by PT-P1-01 demo XML
    # reproducibility (2026-05-31): the original `product.product_product_4`
    # references in demo_orders.xml were the blocker. Replaced with
    # demo_variant_* references from southbrook_demo_variants.xml.
    # Demo orders now install on a --without-demo DB without dependency
    # on Odoo core demo records.
    #
    # Phase 3 follow-up: replace demo_variants.xml with a Python-side
    # helper that walks the OCA product.config.session flow per cabinet,
    # materialises the variant with value_ids populated, and binds those
    # session-attached variants to the demo orders. That gives the BoM
    # rollup truly attribute-driven values (instead of _SKU_DEFAULTS
    # fallbacks). See PT-P1-01 layer 3 in PUNCHLIST.md.
    # ------------------------------------------------------------------
    "demo": [
        "demo/southbrook_demo_partners.xml",
        "demo/southbrook_demo_variants.xml",
        "demo/southbrook_demo_orders.xml",
    ],
    # REG-C1 (2026-06-18) — heal companies missing a default Sales journal.
    # See _ensure_sales_journal in __init__.py for the why; runs on
    # -i AND -u so live DBs upgrade-heal automatically.
    # Combined hook (chains _ensure_sales_journal + _configure_southbrook_report_branding
    # + _backfill_single_value_attribute_defaults + Task A3's
    # post_init_backfill_geometry). Odoo only dispatches ONE
    # post_init_hook function name per module, so all idempotent
    # post-install steps chain through this single entry point instead
    # of each claiming their own manifest key.
    # See __init__.py for the why; each step is independent and idempotent.
    "post_init_hook": "_southbrook_estimating_post_init",
    "installable": True,
    "application": True,
    "auto_install": False,
}
