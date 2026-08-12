# SPDX-License-Identifier: LGPL-3.0-only
{
    "name": "Southbrook Materials — Hardware Catalog",
    "version": "19.0.2.3.0",
    "category": "Manufacturing",
    "summary": "Faceted, spec-first catalog for hardware, tooling and shop "
               "consumables. Reads southbrook_mrp_kitchen_tools data; owns no "
               "domain model of its own.",
    "author": "Southbrook / METISA",
    "license": "LGPL-3",
    "depends": ["product", "uom", "stock", "southbrook_mrp_kitchen_tools", "southbrook_ui"],
    "data": [
        "security/ir.model.access.csv",
        "data/catalog_action.xml",
        "data/catalog_declarations.xml",
        "data/catalog_search_synonyms.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sb_materials_hardware/static/src/catalog/catalog.scss",
            "sb_materials_hardware/static/src/catalog/catalog.js",
            "sb_materials_hardware/static/src/catalog/catalog.xml",
        ],
    },
    "installable": True,
}
