# SPDX-License-Identifier: LGPL-3.0-only
"""Task 30 (corner-remediation-2026-07-30) — public shop exposure gate.

Prod verification found SB-CORNER-BLIND (223) / SB-CORNER-DIAG (224) /
SB-WALL-CORNER (236) — southbrook_cabinet_type='corner' since the corner
remediation — already carry ``is_published=True`` on a site with
``ecommerce_access=everyone``: they are (were) live on the public /shop
grid, search, and direct product page, alongside the SBK-APPL-*
appliance stand-ins (southbrook_cabinet_type='appliance', hard $0.0
list_price). Both families have $0 standard_price and empty-or-absent
BOMs on prod. models/website.py closes the listing/search/sitemap
surface via ``Website._product_domain()`` (the shared root domain every
website_sale shop surface ANDs in).

Not covered here (out of scope for a domain-only fix, needs a prod data
change): a customer who already has/guesses a still-``is_published``
corner/appliance product's direct URL. The core "Public product
template" ir.rule gates that on ``is_published`` alone, not on this
domain.
"""
from odoo.tests import HttpCase, TransactionCase, tagged


def _make_cabinet(env, *, name, cabinet_type, code):
    return env["product.template"].create({
        "name": name,
        "default_code": code,
        "type": "consu",
        "is_storable": True,
        "sale_ok": True,
        "is_published": True,
        "list_price": 545.0,
        "southbrook_is_cabinet": cabinet_type != "appliance",
        "southbrook_cabinet_type": cabinet_type,
    })


@tagged("post_install", "-at_install", "southbrook", "shop_exposure")
class TestShopDomainExcludesCornerAndAppliance(TransactionCase):
    """Unit-level: Website._product_domain() drops corner/appliance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].get_current_website()
        cls.corner = _make_cabinet(
            cls.env, name="T30 Corner Base · Blind",
            cabinet_type="corner", code="T30-CORNER-TEST",
        )
        cls.appliance = _make_cabinet(
            cls.env, name="T30 Appliance Space — Range",
            cabinet_type="appliance", code="T30-APPL-TEST",
        )
        cls.base_cabinet = _make_cabinet(
            cls.env, name="T30 Base 2-Door",
            cabinet_type="base", code="T30-BASE-TEST",
        )

    def test_corner_and_appliance_excluded_from_shop_domain(self):
        domain = self.website._product_domain()
        visible = self.env["product.template"].search(domain)
        self.assertNotIn(self.corner.id, visible.ids,
                          "corner-family cabinet must not satisfy the "
                          "public shop domain")
        self.assertNotIn(self.appliance.id, visible.ids,
                          "appliance placeholder must not satisfy the "
                          "public shop domain")

    def test_other_cabinet_types_untouched(self):
        """Scope discipline: base/wall/tall are a separate, wider policy
        question (pre-existing, out of this audit's scope) — the fix
        must not silently widen to exclude them too."""
        domain = self.website._product_domain()
        visible = self.env["product.template"].search(domain)
        self.assertIn(self.base_cabinet.id, visible.ids)


@tagged("post_install", "-at_install", "southbrook", "shop_exposure")
class TestShopPageExcludesCornerAndAppliance(HttpCase):
    """HTTP-level: an anonymous visit to /shop must not surface a
    published corner or appliance SKU, mirroring the exact prod
    exposure this task audited."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corner = _make_cabinet(
            cls.env, name="T30 Corner Base · Blind HTTP",
            cabinet_type="corner", code="T30-CORNER-HTTP",
        )
        cls.appliance = _make_cabinet(
            cls.env, name="T30 Appliance Space — Range HTTP",
            cabinet_type="appliance", code="T30-APPL-HTTP",
        )
        cls.base_cabinet = _make_cabinet(
            cls.env, name="T30 Base 2-Door HTTP",
            cabinet_type="base", code="T30-BASE-HTTP",
        )

    def test_shop_listing_hides_corner_and_appliance(self):
        resp = self.url_open("/shop?search=T30")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertNotIn("T30 Corner Base · Blind HTTP", html)
        self.assertNotIn("T30 Appliance Space — Range HTTP", html)

    def test_shop_listing_still_shows_other_cabinet_types(self):
        resp = self.url_open("/shop?search=T30")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("T30 Base 2-Door HTTP", resp.text)
