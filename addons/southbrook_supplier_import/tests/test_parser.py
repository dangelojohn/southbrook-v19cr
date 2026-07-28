# SPDX-License-Identifier: LGPL-3.0-only
"""Unit tests for the pure parsing layer.

These use real shapes observed in the eurofitcanada.com feed on 2026-07-27,
not invented ones.
"""

from odoo.tests import TransactionCase, tagged

from ..models import catalog_parser as parser


@tagged("post_install", "-at_install", "southbrook_supplier_import")
class TestCatalogParser(TransactionCase):

    # --- price tiers --------------------------------------------------
    def test_price_tier_range_takes_lower_bound(self):
        self.assertEqual(parser.parse_price_tier("1-24pcs"), 1)
        self.assertEqual(parser.parse_price_tier("20-59 Sets"), 20)

    def test_price_tier_or_more(self):
        self.assertEqual(parser.parse_price_tier("25pcs or more"), 25)
        self.assertEqual(parser.parse_price_tier("100pcs or more"), 100)
        self.assertEqual(parser.parse_price_tier("60+ Sets"), 60)

    def test_price_tier_case_wording(self):
        self.assertEqual(parser.parse_price_tier("Less than 1 Case"), 1)
        self.assertEqual(parser.parse_price_tier("3 Cases or more"), 3)

    def test_price_tier_unknown_defaults_to_one(self):
        """min_qty=1 always applies, so it loses a discount rather than
        mispricing an order."""
        self.assertEqual(parser.parse_price_tier("call us"), 1)
        self.assertEqual(parser.parse_price_tier(None), 1)
        self.assertEqual(parser.parse_price_tier(""), 1)

    # --- kit formulas -------------------------------------------------
    def test_kit_formula_detection(self):
        self.assertTrue(parser.is_kit_formula(
            "BLUM-22F2500+BLUM-22F3500+BLUM-78Z5500T"))
        self.assertTrue(parser.is_kit_formula("(HTT-9156338 x 2) + HTT-9227245"))
        self.assertTrue(parser.is_kit_formula("(F069135061+F069063310)"))
        self.assertFalse(parser.is_kit_formula("HTT-9156338"))
        self.assertFalse(parser.is_kit_formula("SL-118-270N"))
        self.assertFalse(parser.is_kit_formula(""))

    def test_kit_components_decoded_for_bom_seeding(self):
        parts = parser.parse_kit_components("(HTT-9156338 x 2) + HTT-9227245")
        self.assertEqual(parts, [("HTT-9156338", 2), ("HTT-9227245", 1)])

    def test_kit_components_plain_sum(self):
        parts = parser.parse_kit_components("BLUM-22F2500+BLUM-22F3500")
        self.assertEqual(parts, [("BLUM-22F2500", 1), ("BLUM-22F3500", 1)])

    def test_plain_sku_yields_no_components(self):
        self.assertEqual(parser.parse_kit_components("HTT-9156338"), [])

    # --- categories ---------------------------------------------------
    def test_specific_rules_beat_general_ones(self):
        """'hinge plate' must not be swallowed by the 'hinge' rule."""
        self.assertEqual(parser.normalise_category("Hinge Plate"),
                         ("Hinges", "Mounting Plates"))
        self.assertEqual(parser.normalise_category("Soft Close Hinge"),
                         ("Hinges", "Hinges"))
        self.assertEqual(parser.normalise_category("Hinge Machine"),
                         ("Tools & Jigs", "Machines"))

    def test_case_and_typo_variants_collapse(self):
        """The feed contains both spellings, and a typo."""
        self.assertEqual(parser.normalise_category("push open piston"),
                         parser.normalise_category("Push Open Piston"))
        self.assertEqual(parser.normalise_category("Contact Adhersive"),
                         ("Adhesives & Sealants", "Adhesives"))

    def test_empty_type_is_not_guessed(self):
        self.assertEqual(parser.normalise_category(""), parser.UNCLASSIFIED)
        self.assertEqual(parser.normalise_category(None), parser.UNCLASSIFIED)

    # --- the price-break folding --------------------------------------
    def test_quantity_option_becomes_tiers_not_variants(self):
        """The core of the whole design.

        A real product from the feed: one SKU, two Quantity options, two
        prices. It must produce ONE sellable item with TWO price breaks —
        not two variants of the same physical part.
        """
        feed = [{
            "title": "Handle-Free Piston System",
            "handle": "handle-free-piston-system",
            "vendor": "EUROFIT",
            "product_type": "push open piston",
            "body_html": "<p>Piston</p>",
            "options": [{"name": "Quantity"}],
            "images": [{"src": "https://example.invalid/a.jpg"}],
            "variants": [
                {"sku": "MP-K11", "price": "18.00", "grams": 50,
                 "available": True, "option1": "1-9pcs"},
                {"sku": "MP-K11", "price": "16.20", "grams": 50,
                 "available": True, "option1": "10pcs or more"},
            ],
        }]
        items = parser.parse_shopify_products(feed)
        self.assertEqual(len(items), 1, "One physical part, one item.")
        item = items[0]
        self.assertEqual(item["sku"], "MP-K11")
        self.assertEqual(item["attributes"], {},
                         "Quantity is a price break, never an attribute.")
        self.assertEqual(len(item["tiers"]), 2)
        self.assertEqual(item["tiers"][0], {"min_qty": 1, "price": 18.0,
                                            "label": "1-9pcs"})
        self.assertEqual(item["tiers"][1], {"min_qty": 10, "price": 16.2,
                                            "label": "10pcs or more"})
        self.assertEqual(item["category_branch"], "Opening Systems")

    def test_genuine_attributes_do_create_separate_items(self):
        """Finish and Size are real characteristics and must split."""
        feed = [{
            "title": "Bar Pull", "handle": "bar-pull", "vendor": "EUROFIT",
            "product_type": "Handle", "body_html": "",
            "options": [{"name": "Finish"}, {"name": "Quantity"}],
            "images": [],
            "variants": [
                {"sku": "H-1-BN", "price": "5.00", "option1": "Brushed Nickel",
                 "option2": "1-24pcs"},
                {"sku": "H-1-BN", "price": "4.50", "option1": "Brushed Nickel",
                 "option2": "25pcs or more"},
                {"sku": "H-1-MB", "price": "5.00", "option1": "Matte Black",
                 "option2": "1-24pcs"},
            ],
        }]
        items = parser.parse_shopify_products(feed)
        self.assertEqual(len(items), 2, "Two finishes = two sellable items.")
        by_sku = {i["sku"]: i for i in items}
        self.assertEqual(len(by_sku["H-1-BN"]["tiers"]), 2)
        self.assertEqual(by_sku["H-1-BN"]["attributes"],
                         {"Finish": "Brushed Nickel"})
        self.assertEqual(len(by_sku["H-1-MB"]["tiers"]), 1)

    def test_default_title_option_is_not_an_attribute(self):
        feed = [{
            "title": "Dowel", "handle": "d", "vendor": "EUROFIT",
            "product_type": "Screw", "body_html": "", "images": [],
            "options": [{"name": "Title"}],
            "variants": [{"sku": "D-1", "price": "1.00",
                          "option1": "Default Title"}],
        }]
        items = parser.parse_shopify_products(feed)
        self.assertEqual(items[0]["attributes"], {})
