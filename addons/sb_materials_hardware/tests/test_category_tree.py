# SPDX-License-Identifier: LGPL-3.0-only
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "sbk_mathw")
class TestCategoryTree(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Provider = self.env["materials.catalog.provider"]
        self.cat_screws = self.env.ref("southbrook_mrp_kitchen_tools.cat_screws")
        self.cat_conf = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_screw_confirmat")
        self.tmpl = self.env["product.template"].create({
            "name": "TEST Confirmat 7x50",
            "x_southbrook_tool_category_id": self.cat_conf.id,
        })

    def _cats(self):
        payload = self.Provider.get_catalog(scope="tools")
        return {c["id"]: c for c in payload["categories"]}

    def test_tree_includes_seeded_categories(self):
        cats = self._cats()
        self.assertIn(self.cat_screws.id, cats)
        self.assertIn(self.cat_conf.id, cats)

    def test_parent_link_is_reported(self):
        cats = self._cats()
        self.assertEqual(cats[self.cat_conf.id]["parent_id"], self.cat_screws.id)

    def test_has_children_flag(self):
        cats = self._cats()
        self.assertTrue(cats[self.cat_screws.id]["has_children"])
        self.assertFalse(cats[self.cat_conf.id]["has_children"])

    def test_count_rolls_up_to_ancestors(self):
        cats = self._cats()
        self.assertGreaterEqual(cats[self.cat_conf.id]["count"], 1)
        self.assertGreaterEqual(
            cats[self.cat_screws.id]["count"], cats[self.cat_conf.id]["count"])

    def test_rollup_arithmetic_with_siblings(self):
        """Verify rollup counts exactly match descendants, not overcount."""
        # Create fixtures with known, DIFFERENT counts to catch branch-swapping bugs
        cat_cabinet = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_screw_cabinet")
        cat_euro = self.env.ref("southbrook_mrp_kitchen_tools.cat_screw_euro")
        cat_adhesives = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_adhesives")

        # The adhesives subtree is no longer guaranteed empty: the
        # 19.0.2.0.0 migration correctly classifies real chemical products
        # into it on any DB that carries them. Its assertion below is
        # baseline-relative for that reason; the screw branches stay
        # absolute (nothing ships data into them).
        adhesives_before = self._cats()[cat_adhesives.id]["count"]

        # Create 2 templates under cat_screw_cabinet (sibling to cat_screw_confirmat)
        # Each template creates 1 default variant automatically
        tmpl_cabinet_1 = self.env["product.template"].create({
            "name": "Cabinet Screw 3.5x25",
            "x_southbrook_tool_category_id": cat_cabinet.id,
        })
        tmpl_cabinet_2 = self.env["product.template"].create({
            "name": "Cabinet Screw 4x30",
            "x_southbrook_tool_category_id": cat_cabinet.id,
        })

        # Create 3 templates under cat_screw_euro (DIFFERENT count to catch swaps)
        tmpl_euro_1 = self.env["product.template"].create({
            "name": "Euro Screw 5x35",
            "x_southbrook_tool_category_id": cat_euro.id,
        })
        tmpl_euro_2 = self.env["product.template"].create({
            "name": "Euro Screw 6x40",
            "x_southbrook_tool_category_id": cat_euro.id,
        })
        tmpl_euro_3 = self.env["product.template"].create({
            "name": "Euro Screw 7x45",
            "x_southbrook_tool_category_id": cat_euro.id,
        })

        # Create 1 product in an unrelated branch (adhesives)
        tmpl_adhesive = self.env["product.template"].create({
            "name": "Wood Glue 1L",
            "x_southbrook_tool_category_id": cat_adhesives.id,
        })

        # Refresh categories to pick up new templates
        cats = self._cats()

        # Verify sibling counts with exact arithmetic (not >=)
        self.assertEqual(
            cats[cat_cabinet.id]["count"], 2,
            f"Cabinet category should have exactly 2 variants (1 per template), "
            f"got {cats[cat_cabinet.id]['count']}")
        self.assertEqual(
            cats[cat_euro.id]["count"], 3,
            f"Euro category should have exactly 3 variants (1 per template), "
            f"got {cats[cat_euro.id]['count']}")
        self.assertEqual(
            cats[cat_adhesives.id]["count"], adhesives_before + 1,
            f"Adhesives category should have gained exactly 1 variant over "
            f"its baseline of {adhesives_before}, "
            f"got {cats[cat_adhesives.id]['count']}")

        # Verify parent (cat_screws) sums siblings exactly
        # Arithmetic: 2 cabinet + 3 euro + 1 confirmat (from setUp) = 6
        expected_screw_count = 6
        self.assertEqual(
            cats[self.cat_screws.id]["count"], expected_screw_count,
            f"Screws parent should have exactly {expected_screw_count} "
            f"(2 cabinet + 3 euro + 1 confirmat from setUp), "
            f"got {cats[self.cat_screws.id]['count']}")

        # Prove unrelated branch is genuinely excluded by exact numbers
        # Screws total (6) does NOT equal adhesives total (1) ✓
        self.assertNotEqual(
            cats[self.cat_screws.id]["count"], cats[cat_adhesives.id]["count"],
            f"Screw count must exclude adhesives: screws={cats[self.cat_screws.id]['count']}, "
            f"adhesives={cats[cat_adhesives.id]['count']}")

    def test_rollup_counts_variants_not_templates(self):
        """Verify _categories counts product.product (variants), not templates.

        Regression test: counts must include all variants of a template,
        not just the template itself. Without this test, a reversion to
        grouping product.template would silently pass the suite if variants
        are never created during test runs.

        Two genuine variants need two distinct attribute-value combinations
        — an attribute line with two values lets Odoo generate them itself.
        (A manually created second product.product with no attribute
        combination collides with the template's own auto-created default
        variant on the real `product_product_combination_unique` constraint
        — both would carry the same empty combination_indices — so it is
        not a legitimate way to get a second variant.)
        """
        cat_drawer = self.env.ref(
            "southbrook_mrp_kitchen_tools.cat_screw_drawer")

        attribute = self.env["product.attribute"].create({
            "name": "TEST Drawer Screw Finish",
            "value_ids": [
                (0, 0, {"name": "TEST Finish A"}),
                (0, 0, {"name": "TEST Finish B"}),
            ],
        })
        tmpl_drawer = self.env["product.template"].create({
            "name": "Drawer Screw Multi-Variant",
            "x_southbrook_tool_category_id": cat_drawer.id,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, attribute.value_ids.ids)],
            })],
        })
        # Two attribute values on one attribute line -> Odoo auto-generates
        # 2 variants, each with its own distinct combination.
        self.assertEqual(tmpl_drawer.product_variant_count, 2)

        # Refresh categories
        cats = self._cats()

        # Crucial: category count must reflect BOTH variants (count=2),
        # not just the template (count=1). This proves _read_group groups
        # product.product, not product.template.
        self.assertEqual(
            cats[cat_drawer.id]["count"], 2,
            f"Drawer category should count both variants (2), not just template (1), "
            f"got {cats[cat_drawer.id]['count']}")
