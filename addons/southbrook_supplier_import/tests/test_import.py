# SPDX-License-Identifier: LGPL-3.0-only
"""End-to-end import tests.

The most important assertion in this file is that the dry run writes NOTHING
to product.*. Everything else is detail.
"""

import base64
import json

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

FEED = [{
    "title": "Handle-Free Piston System",
    "handle": "handle-free-piston-system",
    "vendor": "EUROFIT",
    "product_type": "push open piston",
    "body_html": "<p>A piston that opens a door without a handle.</p>",
    "options": [{"name": "Quantity"}],
    "images": [{"src": "https://example.invalid/piston.jpg"}],
    "variants": [
        {"sku": "TEST-MP-K11", "price": "18.00", "grams": 50,
         "available": True, "option1": "1-9pcs"},
        {"sku": "TEST-MP-K11", "price": "16.20", "grams": 50,
         "available": True, "option1": "10pcs or more"},
    ],
}, {
    "title": "Blum Drawer Kit",
    "handle": "blum-drawer-kit",
    "vendor": "BLUM",
    "product_type": "Metal Drawer System",
    "body_html": "<p>Kit.</p>",
    "options": [{"name": "Title"}],
    "images": [],
    "variants": [
        {"sku": "TEST-BLUM-A+TEST-BLUM-B", "price": "99.00", "grams": 900,
         "available": True, "option1": "Default Title"},
    ],
}, {
    "title": "Mystery Item",
    "handle": "mystery",
    "vendor": "EUROFIT",
    "product_type": "",
    "body_html": "",
    "images": [],
    "options": [{"name": "Title"}],
    "variants": [
        {"sku": "TEST-MYSTERY-1", "price": "3.00", "grams": 0,
         "available": True, "option1": "Default Title"},
    ],
}]


@tagged("post_install", "-at_install", "southbrook_supplier_import")
class TestSupplierCatalogImport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({
            "name": "TEST Eurofit Canada", "supplier_rank": 1})

    def _run(self, feed=None):
        return self.env["supplier.catalog.import"].create({
            "name": "TEST run",
            "partner_id": self.vendor.id,
            "payload": base64.b64encode(
                json.dumps({"products": feed or FEED}).encode()),
            "payload_name": "feed.json",
        })

    def _skus(self):
        return set(self.env["product.product"].search(
            [("default_code", "like", "TEST-")]).mapped("default_code"))

    # --- the dry-run guarantee ----------------------------------------
    def test_dry_run_writes_nothing_to_products(self):
        before_products = self.env["product.product"].search_count([])
        before_categs = self.env["product.category"].search_count([])
        before_seller = self.env["product.supplierinfo"].search_count([])
        run = self._run()
        run.action_fetch()
        run.action_analyse()
        self.assertEqual(run.state, "analysed")
        self.assertEqual(
            self.env["product.product"].search_count([]), before_products,
            "A dry run must not create products.")
        self.assertEqual(
            self.env["product.category"].search_count([]), before_categs,
            "A dry run must not create categories.")
        self.assertEqual(
            self.env["product.supplierinfo"].search_count([]), before_seller,
            "A dry run must not create vendor prices.")

    def test_commit_refused_before_dry_run(self):
        run = self._run()
        run.action_fetch()
        with self.assertRaises(UserError):
            run.action_commit()

    # --- staging ------------------------------------------------------
    def test_price_breaks_do_not_become_separate_items(self):
        run = self._run()
        run.action_fetch()
        piston = run.line_ids.filtered(lambda l: l.sku == "TEST-MP-K11")
        self.assertEqual(len(piston), 1,
                         "Two price breaks are one sellable item.")
        tiers = json.loads(piston.tiers_json)
        self.assertEqual([t["min_qty"] for t in tiers], [1, 10])

    def test_kit_formula_held_for_review(self):
        run = self._run()
        run.action_fetch()
        run.action_analyse()
        kit = run.line_ids.filtered("is_kit_formula")
        self.assertEqual(len(kit), 1)
        self.assertEqual(kit.action, "review",
                         "An assembly formula must never become a default_code.")
        self.assertIn("TEST-BLUM-A x1", kit.kit_components,
                      "Components are decoded so they can seed a BoM.")

    def test_missing_category_held_for_review(self):
        run = self._run()
        run.action_fetch()
        run.action_analyse()
        mystery = run.line_ids.filtered(lambda l: l.sku == "TEST-MYSTERY-1")
        self.assertEqual(mystery.action, "review")
        self.assertEqual(mystery.category_leaf, "Needs Triage")

    # --- commit -------------------------------------------------------
    def test_commit_creates_product_category_and_price_breaks(self):
        run = self._run()
        run.action_fetch()
        run.action_analyse()
        run.action_commit()
        self.assertEqual(run.state, "committed")
        product = self.env["product.product"].search(
            [("default_code", "=", "TEST-MP-K11")])
        self.assertEqual(len(product), 1)
        self.assertEqual(product.categ_id.complete_name,
                         "Hardware / Opening Systems / Push-to-Open")
        self.assertAlmostEqual(product.weight, 0.05, places=3,
                               msg="50 g must land as 0.05 kg.")
        sellers = self.env["product.supplierinfo"].search([
            ("partner_id", "=", self.vendor.id),
            ("product_id", "=", product.id)])
        self.assertEqual(len(sellers), 2, "One row per price break.")
        self.assertEqual(
            sorted(sellers.mapped("min_qty")), [1.0, 10.0])
        cheap = sellers.filtered(lambda s: s.min_qty == 10)
        self.assertAlmostEqual(cheap.price, 16.20, places=2)

    def test_commit_leaves_review_items_untouched(self):
        run = self._run()
        run.action_fetch()
        run.action_analyse()
        run.action_commit()
        self.assertFalse(
            self.env["product.product"].search(
                [("default_code", "=", "TEST-BLUM-A+TEST-BLUM-B")]),
            "A kit formula must not be created as a product.")
        self.assertFalse(
            self.env["product.product"].search(
                [("default_code", "=", "TEST-MYSTERY-1")]),
            "An unclassified item must not be created.")

    def test_second_run_updates_rather_than_duplicates(self):
        first = self._run()
        first.action_fetch(); first.action_analyse(); first.action_commit()
        count = self.env["product.product"].search_count(
            [("default_code", "=", "TEST-MP-K11")])
        second = self._run()
        second.action_fetch(); second.action_analyse()
        line = second.line_ids.filtered(lambda l: l.sku == "TEST-MP-K11")
        self.assertEqual(line.action, "update",
                         "An existing SKU must match, not duplicate.")
        second.action_commit()
        self.assertEqual(
            self.env["product.product"].search_count(
                [("default_code", "=", "TEST-MP-K11")]), count,
            "Re-importing must not create a second product.")

    def test_update_does_not_clobber_curated_names(self):
        """A supplier feed is not authoritative over Southbrook's naming."""
        first = self._run()
        first.action_fetch(); first.action_analyse(); first.action_commit()
        product = self.env["product.product"].search(
            [("default_code", "=", "TEST-MP-K11")])
        product.product_tmpl_id.name = "Piston (Southbrook curated name)"
        second = self._run()
        second.action_fetch(); second.action_analyse(); second.action_commit()
        self.assertEqual(
            product.product_tmpl_id.name, "Piston (Southbrook curated name)",
            "Re-import must not overwrite a human-edited name.")

    def test_reset_after_commit_is_refused(self):
        run = self._run()
        run.action_fetch(); run.action_analyse(); run.action_commit()
        with self.assertRaises(UserError):
            run.action_reset()
