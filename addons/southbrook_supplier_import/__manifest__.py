# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Supplier Catalogue Import",
    "summary": "Import a supplier's online catalogue into Inventory with a "
               "mandatory dry run: categories, price breaks, weights and "
               "images, with conflicts surfaced before anything is written.",
    "description": "Imports Shopify products.json feeds (eurofitcanada.com "
                   "and similar). Three steps: Fetch, Analyse, Commit. "
                   "Analyse is a true dry run that writes nothing outside "
                   "this module's own tables, so the whole plan including "
                   "every conflict is visible first. Volume price breaks, "
                   "which Shopify encodes as a Quantity variant option, are "
                   "written as product.supplierinfo min_qty rows instead of "
                   "becoming product variants. Assembly-formula SKUs and "
                   "items already tracked as Southbrook shop tools are held "
                   "back for review rather than silently duplicated.",
    "version": "19.0.1.0.0",
    "license": "LGPL-3",
    "author": "Southbrook Cabinetry",
    "website": "https://southbrookcabinetry.space",
    "category": "Inventory/Purchase",
    # stock is required, not optional: is_storable and weight — both of which
    # this importer sets — are contributed by stock, not by product. Without
    # it the module installs happily and then raises on the first commit.
    "depends": ["product", "purchase", "stock"],
    "data": ["security/ir.model.access.csv", "views/supplier_import_views.xml"],
    "installable": True,
    "application": False,
    "auto_install": False,
}
