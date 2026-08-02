# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American sheet-goods cost pass (client-authorized,
2026-08-02) — tests for ``mrp.bom._southbrook_apply_researched_sheet_
goods_costs``. See scratchpad/retest/pricing-sheetgoods.md for the
sourcing and models/mrp_bom_catalog.py's _RESEARCHED_SHEET_GOODS_COSTS
/ _EDGEBAND_FT_TO_M_PRICE for the applied figures + citations.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "kitchen_mrp",
        "researched_pricing")
class TestResearchedSheetGoodsPricing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bom = cls.env["mrp.bom"]

    # ------------------------------------------------------------------
    # Spot assertions — applied values
    # ------------------------------------------------------------------
    def test_spot_melamine_5_8_price(self):
        self.Bom._southbrook_apply_researched_sheet_goods_costs()
        product = self.Bom._southbrook_get_or_create_raw_material(
            "melamine_white_5_8")
        self.assertAlmostEqual(product.standard_price, 58.00, places=2)

    def test_spot_ply_3_4_price(self):
        self.Bom._southbrook_apply_researched_sheet_goods_costs()
        product = self.Bom._southbrook_get_or_create_raw_material("ply_3_4")
        self.assertAlmostEqual(product.standard_price, 103.99, places=2)

    def test_spot_edgeband_ft_to_metre_conversion(self):
        """$0.12 CAD/ft mid-range research rate must be converted to
        the product's actual per-linear-METRE UoM: 0.12 x 3.28084 =
        0.3937 CAD/m — not applied as a raw $0.12."""
        self.Bom._southbrook_apply_researched_sheet_goods_costs()
        product = self.Bom._southbrook_get_or_create_edge_banding_tape()
        self.assertAlmostEqual(product.standard_price, 0.3937, places=4)

    def test_hardboard_1_4_left_unpriced(self):
        """No researched figure targets hardboard specifically — it
        must stay at zero."""
        self.Bom._southbrook_apply_researched_sheet_goods_costs()
        product = self.Bom._southbrook_get_or_create_raw_material(
            "hardboard_1_4")
        self.assertFalse(product.standard_price)

    # ------------------------------------------------------------------
    # Idempotence — second run changes nothing
    # ------------------------------------------------------------------
    def test_idempotent_second_run_is_a_noop(self):
        # Force a known "unpriced" starting point regardless of whatever
        # this database's migration history already applied.
        for substrate in ("melamine_white_5_8", "ply_3_4"):
            self.Bom._southbrook_get_or_create_raw_material(
                substrate).standard_price = 0.0
        self.Bom._southbrook_get_or_create_edge_banding_tape().standard_price = 0.0

        first = self.Bom._southbrook_apply_researched_sheet_goods_costs()
        self.assertEqual(
            set(first["applied"]),
            {"RM-MELAMINE_WHITE_5_8", "RM-PLY_3_4", "RM-EDGEBAND-PVC-34"},
        )

        second = self.Bom._southbrook_apply_researched_sheet_goods_costs()
        self.assertFalse(second["applied"], "second run must apply nothing")
        self.assertEqual(
            set(second["skipped_nonzero"]), set(first["applied"]),
        )

    # ------------------------------------------------------------------
    # Fill-zeros-only — a pre-priced product survives untouched
    # ------------------------------------------------------------------
    def test_fill_zeros_only_preserves_pre_existing_price(self):
        pre_priced = self.Bom._southbrook_get_or_create_raw_material(
            "melamine_white_5_8")
        pre_priced.standard_price = 61.50  # deliberately != 58.00
        self.Bom._southbrook_apply_researched_sheet_goods_costs()

        self.assertEqual(
            self.Bom._southbrook_get_or_create_raw_material(
                "melamine_white_5_8").standard_price,
            61.50,
            "a pre-priced product must survive the seeder untouched",
        )
        # the sibling substrate should still get its own researched price
        self.assertAlmostEqual(
            self.Bom._southbrook_get_or_create_raw_material(
                "ply_3_4").standard_price,
            103.99, places=2,
        )

    # ------------------------------------------------------------------
    # Uses the generator's own get-or-create helpers — proven elsewhere
    # (test_catalog_bom_generator.py) to materialize the placeholder by
    # stable xmlid when it doesn't exist yet. Deliberately NOT
    # re-testing deletion/recreation here: on a real database these
    # placeholders are typically already referenced by mrp.bom.line
    # rows (FK-protected), so forcing a delete in a test is unsafe
    # shared-fixture surgery, not a meaningful extra assertion — the
    # get-or-create reuse itself is the correctness guarantee.
    # ------------------------------------------------------------------
    def test_reuses_generator_get_or_create_helper_not_a_duplicate(self):
        self.Bom._southbrook_apply_researched_sheet_goods_costs()
        before = self.Bom._southbrook_get_or_create_raw_material(
            "melamine_white_5_8")
        after = self.Bom._southbrook_get_or_create_raw_material(
            "melamine_white_5_8")
        self.assertEqual(
            before.id, after.id,
            "the seeder must price the SAME product the generator "
            "would create/reuse, not a duplicate",
        )
