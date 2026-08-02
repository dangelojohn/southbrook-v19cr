# SPDX-License-Identifier: LGPL-3.0-only
"""Researched North American hardware cost pass (client-authorized,
2026-08-02) — tests for
``southbrook.hardware.catalog._southbrook_apply_researched_hardware_
costs``. See scratchpad/retest/pricing-hardware.md for the full
sourcing and addons/southbrook_hardware_catalog/models/
southbrook_hardware_catalog.py's _RESEARCHED_HARDWARE_COSTS table for
the applied figures + per-SKU citations.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "hardware_catalog",
        "researched_pricing")
class TestResearchedHardwarePricing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["product.product"]
        cls.Catalog = cls.env["southbrook.hardware.catalog"]

    def _price(self, default_code):
        product = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        self.assertTrue(product, f"{default_code} not found in catalog")
        return product

    # ------------------------------------------------------------------
    # Spot assertions — applied values (at least 3, per task brief)
    # ------------------------------------------------------------------
    def test_spot_blm_110_sc_price(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("BLM-110-SC").standard_price, 9.00, places=2)

    def test_spot_sal_c2r_165_price(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("SAL-C2R-165").standard_price, 5.25, places=2)

    def test_spot_blm_mov_450_price(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("BLM-MOV-450").standard_price, 43.75, places=2)

    def test_spot_sb_cm_bco_blind_pullout_price(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("SB-CM-BCO").standard_price, 991.19, places=2)

    def test_spot_sb_cm_cpos_lazy_susan_price(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("SB-CM-CPOS").standard_price, 173.17, places=2)

    def test_spot_mtgscr_25pk_unit_conversion(self):
        """0.1257 CAD/ea x 25 (pack size) = 3.1425 CAD/pack — the
        per-each research figure must be converted to the product's
        actual 25-pack UoM, not applied as-is."""
        self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertAlmostEqual(
            self._price("MRH-MTGSCR-25PK").standard_price, 3.1425, places=4)

    # ------------------------------------------------------------------
    # Idempotence — second run changes nothing
    # ------------------------------------------------------------------
    def test_idempotent_second_run_is_a_noop(self):
        # Force a known "unpriced" starting point regardless of whatever
        # this database's migration history already applied — the point
        # of this test is the SEEDER's idempotence, not the DB's
        # incidental state when the test transaction opened.
        for sku in self.Catalog._RESEARCHED_HARDWARE_COSTS:
            self._price(sku).standard_price = 0.0

        first = self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertTrue(first["applied"], "first run should apply something")

        prices_after_first = {
            sku: self._price(sku).standard_price for sku in first["applied"]
        }

        second = self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertFalse(
            second["applied"],
            "second run must apply nothing — every SKU it touched the "
            "first time now has a non-zero standard_price",
        )
        self.assertEqual(
            set(second["skipped_nonzero"]), set(first["applied"]),
            "everything applied on run 1 must be skipped-as-nonzero on run 2",
        )
        for sku, price in prices_after_first.items():
            self.assertEqual(
                self._price(sku).standard_price, price,
                f"{sku}'s price must not change between runs",
            )

    # ------------------------------------------------------------------
    # Fill-zeros-only — a pre-priced product survives untouched
    # ------------------------------------------------------------------
    def test_fill_zeros_only_preserves_pre_existing_price(self):
        """Simulates a concurrent Marathon warehouse/brand pricing
        session that already set a real cost — the seeder must never
        clobber it, even though its own researched figure differs."""
        pre_priced = self._price("MRH-HDL-PUL128")
        pre_priced.standard_price = 4.99  # deliberately != 2.83
        self.Catalog._southbrook_apply_researched_hardware_costs()

        self.assertEqual(
            self._price("MRH-HDL-PUL128").standard_price, 4.99,
            "a pre-priced product must survive the seeder untouched",
        )

    # ------------------------------------------------------------------
    # x_pricing_pending invariant — must remain untouched
    # ------------------------------------------------------------------
    def test_pricing_pending_flag_untouched_by_seeder(self):
        skus = list(self.Catalog._RESEARCHED_HARDWARE_COSTS)
        before = {sku: self._price(sku).x_pricing_pending for sku in skus}
        self.Catalog._southbrook_apply_researched_hardware_costs()
        for sku in skus:
            self.assertEqual(
                self._price(sku).x_pricing_pending, before[sku],
                f"{sku}'s x_pricing_pending must not be changed by the "
                "pricing seeder — see test_pending_pricing.py's "
                "test_every_seed_sku_is_pricing_pending invariant",
            )

    # ------------------------------------------------------------------
    # UNRELIABLE-tier flag — VS COR premium mechanism note
    # ------------------------------------------------------------------
    def test_sb_cm_pbco_gets_low_estimate_and_ui_note(self):
        self.Catalog._southbrook_apply_researched_hardware_costs()
        product = self._price("SB-CM-PBCO")
        self.assertAlmostEqual(product.standard_price, 1120.00, places=2)
        self.assertIn("PRICING ESTIMATE", product.x_material or "")
        self.assertIn("2026-08-02", product.x_material or "")

    # ------------------------------------------------------------------
    # Catalog integrity — every researched SKU resolves to a real
    # product on a normally-seeded database (no silent typos in the
    # _RESEARCHED_HARDWARE_COSTS table above).
    # ------------------------------------------------------------------
    def test_every_researched_sku_resolves_to_a_real_product(self):
        result = self.Catalog._southbrook_apply_researched_hardware_costs()
        self.assertEqual(
            result["skipped_no_product"], [],
            "every SKU in _RESEARCHED_HARDWARE_COSTS must exist in the "
            "installed catalog — a non-empty list here means a typo in "
            "the pricing table",
        )
