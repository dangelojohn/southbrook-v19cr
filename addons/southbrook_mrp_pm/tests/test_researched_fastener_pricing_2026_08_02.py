# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American fastener cost pass (client-authorized,
2026-08-02) — tests for ``product.product._southbrook_apply_
researched_fastener_costs``. See scratchpad/retest/pricing-hardware.md
for the sourcing and models/product_fastener_pricing.py for the
applied figures + citations.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "mrp_pm",
        "researched_pricing")
class TestResearchedFastenerPricing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["product.product"]

    def _price(self, default_code):
        product = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        self.assertTrue(product, f"{default_code} not found in catalog")
        return product

    # ------------------------------------------------------------------
    # Spot assertions — applied values
    # ------------------------------------------------------------------
    def test_spot_confirmat_price(self):
        self.Product._southbrook_apply_researched_fastener_costs()
        self.assertAlmostEqual(
            self._price("FAST-CONFIRMAT-50").standard_price,
            0.1924, places=4,
        )

    def test_spot_dowel_price(self):
        self.Product._southbrook_apply_researched_fastener_costs()
        self.assertAlmostEqual(
            self._price("FAST-DOWEL-8MM").standard_price, 0.117, places=4,
        )

    def test_unpriced_fasteners_left_alone(self):
        """FAST-CAM / FAST-HANDLE-SCR / FAST-SLIDE-SCR have no
        researched figure this pass and must stay untouched (still
        zero, per the pre-existing seed data)."""
        self.Product._southbrook_apply_researched_fastener_costs()
        for sku in ("FAST-CAM", "FAST-HANDLE-SCR", "FAST-SLIDE-SCR"):
            self.assertFalse(
                self._price(sku).standard_price,
                f"{sku} has no researched figure and must not be priced",
            )

    # ------------------------------------------------------------------
    # Idempotence — second run changes nothing
    # ------------------------------------------------------------------
    def test_idempotent_second_run_is_a_noop(self):
        # Force a known "unpriced" starting point regardless of whatever
        # this database's migration history already applied.
        for sku in ("FAST-CONFIRMAT-50", "FAST-DOWEL-8MM"):
            self._price(sku).standard_price = 0.0

        first = self.Product._southbrook_apply_researched_fastener_costs()
        self.assertEqual(
            set(first["applied"]),
            {"FAST-CONFIRMAT-50", "FAST-DOWEL-8MM"},
        )

        second = self.Product._southbrook_apply_researched_fastener_costs()
        self.assertFalse(second["applied"], "second run must apply nothing")
        self.assertEqual(
            set(second["skipped_nonzero"]), set(first["applied"]),
        )

    # ------------------------------------------------------------------
    # Fill-zeros-only — a pre-priced product survives untouched
    # ------------------------------------------------------------------
    def test_fill_zeros_only_preserves_pre_existing_price(self):
        pre_priced = self._price("FAST-CONFIRMAT-50")
        pre_priced.standard_price = 0.09  # deliberately != 0.1924
        self.Product._southbrook_apply_researched_fastener_costs()

        self.assertEqual(
            self._price("FAST-CONFIRMAT-50").standard_price, 0.09,
            "a pre-priced product must survive the seeder untouched",
        )
        # the untouched sibling should still get its own researched price
        self.assertAlmostEqual(
            self._price("FAST-DOWEL-8MM").standard_price, 0.117, places=4,
        )
