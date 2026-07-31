# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Configurator UX v2",
    "summary": (
        "Redesigned customer-facing configurator UX for southbrookcabinetry.* "
        "— two-pane responsive layout with sticky live preview, chip selectors, "
        "live pricing + completion ring, bulk template/import tooling."
    ),
    "description": """
Southbrook Configurator UX v2
=============================

A UX redesign of the customer-facing product configurator page (the one
rendered at /shop/<cabinet-slug> by the OCA website_product_configurator
module). Replaces the default form layout with a two-pane responsive
design taken from southbrook-configurator-v2.html prototype:

  Left  (sticky)   live cabinet preview, summary card (price, weight,
                   auto-SKU, completion ring), and the primary Add-to-
                   Quote CTA.

  Right (scroll)   4 collapsible attribute groups with chip-style
                   selectors, conditional disable for rule-blocked
                   options, and inline price-delta hints per option.

  Top              breadcrumbs, page title, and a bulk-tools bar with
                   Template Layout (CSV download) + Import Product
                   (CSV preview + commit) workflows.

This module deliberately does NOT modify the OCA product_configurator
or website_product_configurator addons (per the project brief,
CLAUDE.md §3 "what you do not touch"). The visual redesign lands via
QWeb template inheritance and a separate JS/SCSS bundle — uninstall
this addon and the original configurator UI returns unchanged.

Phasing
-------

Phase 1 — Scaffold (THIS COMMIT)
    Layout, chip selectors, live recalc, validation rules driven by
    OPTIONS object hardcoded in the JS. Same behaviour as the
    prototype HTML. Mount-point guard so the new JS no-ops on pages
    without the v2 root div.

Phase 2 — Real data
    Replace the hardcoded OPTIONS object with values pulled from
    product.attribute / product.attribute.value records. Bind
    price_extra from product.template.attribute.value. Server-side
    price recalc via the existing config session controller.

Phase 3 — Conditional rules from data
    Move the disable-finish-when-melamine and custom-needs-signature
    rules from hardcoded JS into a rule-table data model
    (product.config.line domain rules are already there — surface
    them through a JSON endpoint).

Phase 4 — Bulk tools wired to Odoo
    Server-side xlsx template generation (xlsxwriter), CSV/xlsx
    import with row-level validation against the same rule table,
    preview modal + explicit commit gate, error-report download.

Phase 5 — Tests + a11y
    Tour test for the configurator flow, unit tests for the rule
    engine, ARIA roles on chip selectors and import modal,
    keyboard navigation.
""",
    "version": "19.0.6.2.1",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Sales/Configurator",
    "depends": [
        # The OCA configurator stack — provides the /shop/<slug> route
        # override, the product.config.session model, and the QWeb
        # template we inherit.
        "website_product_configurator",
        "product_configurator",
        # The Southbrook estimating addon — owns the 12 cabinet
        # templates with their attribute_lines that this UX renders.
        "southbrook_estimating",
        # Phase 0 design-system consolidation (2026-07-31) —
        # configurator.scss already reads var(--sb-*) tokens;
        # southbrook_ui is the new canonical, dependency-light token
        # layer those names should resolve through going forward.
        "southbrook_ui",
    ],
    "external_dependencies": {
        # Phase 4 import pipeline (controllers/main.py
        # SouthbrookImportAPI) uses openpyxl to parse uploaded xlsx
        # files and to build the template-download response. The Odoo
        # 19 official Debian image typically has openpyxl pre-
        # installed; we declare it explicitly so a fresh container
        # without it raises a clear ImportError pointing here.
        # Install: pip install --break-system-packages openpyxl
        "python": ["openpyxl"],
    },
    "data": [
        # Phase 1 — template inheritance that swaps the configurator
        # body markup. Loads AFTER the OCA module's
        # data/config_form_templates.xml because of the dependency
        # ordering above.
        "views/configurator_template.xml",
        "views/product_template_views.xml",
        "views/website_configurator_onshape.xml",
        # TACTICAL — demo-grade price_extra/weight_extra backfill so the
        # LIVE recalc badges show non-zero deltas. Delete once
        # southbrook_estimating ships authoritative seed data from
        # Price Master.
        "data/tactical_price_extras.xml",
        # Common-category catalog expansion — ~40 industry-standard
        # cabinet templates with full product_configurator wiring.
        # Loads AFTER tactical_price_extras so the price seed re-runs
        # against the freshly-created PTAVs.
        "data/catalog_expansion.xml",
        # Rule-completion — fills OCA product.config.line gaps so
        # Contemporary + Elegance don't disable Box Material values
        # that were only mentioned in Contractor / Signature rules.
        # Loads LAST so the catalog templates are fully wired first.
        "data/rule_completion.xml",
        # P2 — Brand-aware Drawer Slide attribute. Loads after the
        # catalog so per-template wiring finds the drawer templates.
        "data/p2_drawer_slide.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            # SCSS first so its tokens are available to subsequent
            # bundle members.
            "southbrook_configurator_ux/static/src/scss/configurator.scss",
            # Vanilla JS for Phase 1 — refactored to OWL Component in
            # Phase 2 when wiring to live attribute data via JSON-RPC.
            "southbrook_configurator_ux/static/src/js/configurator.esm.js",
        ],
        "web.assets_web": [
            "southbrook_configurator_ux/static/src/js/website_builder_iframe_guard.esm.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
