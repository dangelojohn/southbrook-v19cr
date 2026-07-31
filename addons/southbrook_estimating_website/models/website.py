# SPDX-License-Identifier: LGPL-3.0-only
from odoo import models


class Website(models.Model):
    _inherit = "website"

    def _product_domain(self):
        """Keep cabinet-engine-only SKUs off the public/portal eCommerce
        shop (Task 30, corner-remediation-2026-07-30 exposure audit).

        ``Website._product_domain()`` (website_sale) is the single root
        domain every public shop surface builds on —
        ``sale_product_domain()`` -> ``_get_shop_domain()`` (the /shop
        grid + search) and the sitemap, all AND it in. Restricting it
        here therefore closes the listing/search/sitemap surface in one
        place instead of one controller at a time.

        Why: prod verification (task-30-report.md) found the corner
        family SB-CORNER-BLIND (223) / SB-CORNER-DIAG (224) /
        SB-WALL-CORNER (236) — promoted to
        southbrook_cabinet_type='corner' by the corner remediation —
        already carry ``is_published=True`` (a pre-existing flag the
        remediation never touched) on a site with
        ``ecommerce_access=everyone``. They were therefore LIVE on
        ``/shop`` right now, alongside the SBK-APPL-* appliance
        stand-ins (hard $0.0 list_price, never meant to be sold on
        their own). The corner family also carries $0 standard_price
        and empty-or-absent BOMs on prod (SB-CORNER tmpl 44's
        "Canonical" BOM and SB-WALL-CORNER's auto-seeded BOM both have
        zero lines; SB-CORNER-BLIND/-DIAG have no BOM record at all) —
        a generic-shop purchase of one would be both cost-blind and
        unmanufacturable.

        These SKUs are consumed ONLY through the guided kitchen-planner
        / Order Builder flow (custom jsonrpc controllers in
        southbrook_kitchen_3d_configurator +
        southbrook_estimating_website — never website_sale's generic
        cart/checkout), so excluding them from the shop domain does not
        touch any real purchase path.

        Scope discipline: only ``corner`` and ``appliance``
        southbrook_cabinet_type — the exact two families task-30 named
        as newly/unintentionally exposed. The other published cabinet
        types (base/wall/tall — 6 templates, pre-existing, unrelated to
        this remediation) are left untouched; broadening the policy to
        the whole southbrook_is_cabinet catalog is a separate business
        decision for the catalog owner, not this audit's call.
        """
        domain = super()._product_domain()
        return domain + [
            ("southbrook_cabinet_type", "not in", ["corner", "appliance"]),
        ]
