# SPDX-License-Identifier: LGPL-3.0-only
"""Hardware-resolution tests — the bridge calls
env['southbrook.hardware.catalog'].resolve(...) and gets a deterministic
list back. These tests pin the contract for downstream Module 2 work
that appends hardware lines to the BoM."""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "hardware_catalog", "resolution")
class TestHardwareResolution(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Catalog = cls.env["southbrook.hardware.catalog"]

    def _sku_set(self, picks):
        """Convert a list of (product, qty) tuples into a dict {sku: qty}
        for stable comparison."""
        return {p.x_marathon_sku: qty for p, qty in picks}

    # ------------------------------------------------------------------
    # Canonical carcass shapes
    # ------------------------------------------------------------------
    def test_base_1door_2shelf_soft_close(self):
        picks = self.Catalog.resolve(
            cabinet_family="base",
            door_count=1, drawer_count=0, shelf_count=2,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("BLM-110-SC"), 2, "Need 2 soft-close hinges for 1 door")
        self.assertEqual(skus.get("MRH-HDL-PUL128"), 1, "Need 1 handle per door")
        self.assertEqual(skus.get("MRH-DOORBUMP-CL"), 2, "Need 2 bumper dots per door")
        self.assertEqual(skus.get("MRH-SHLFPIN-5MM"), 8, "Need 4 pins per shelf × 2 shelves")
        self.assertEqual(skus.get("MRH-LVL-50"), 4, "Need 4 levelers per cabinet")
        self.assertEqual(skus.get("MRH-CAMLOCK-KIT"), 1)
        self.assertEqual(skus.get("MRH-MTGSCR-25PK"), 1)
        self.assertNotIn("BLM-110-NSC", skus, "Non-soft-close hinge must NOT appear")

    def test_base_2door_2shelf_soft_close(self):
        picks = self.Catalog.resolve(
            cabinet_family="base",
            door_count=2, drawer_count=0, shelf_count=2,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("BLM-110-SC"), 4, "2 hinges × 2 doors")
        self.assertEqual(skus.get("MRH-HDL-PUL128"), 2, "1 handle × 2 doors")
        self.assertEqual(skus.get("MRH-DOORBUMP-CL"), 4)

    def test_drawer_bank_3_drawer(self):
        picks = self.Catalog.resolve(
            cabinet_family="drawer",
            door_count=0, drawer_count=3, shelf_count=0,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("BLM-MOV-450"), 3, "1 slide pair per drawer")
        self.assertEqual(skus.get("MRH-HDL-PUL128"), 3, "1 handle per drawer")
        self.assertNotIn("BLM-110-SC", skus, "No hinges on a drawer bank")
        self.assertNotIn("MRH-SHLFPIN-5MM", skus, "No shelves in a drawer bank")

    def test_bifold_corner_no_soft_close(self):
        """Rule 4 (family → soft-close): bi-fold corner cabinets ship with
        non-soft-close hinges."""
        picks = self.Catalog.resolve(
            cabinet_family="corner",
            door_count=1, drawer_count=0, shelf_count=1,
            soft_close=False,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("BLM-110-NSC"), 2, "Non-soft-close hinge for bi-fold")
        self.assertNotIn("BLM-110-SC", skus, "Soft-close hinge must NOT appear")

    def test_tall_pantry_extra_levelers(self):
        """Tall pantry doubles the leveler count — base 4 + family extra 4 = 8."""
        picks = self.Catalog.resolve(
            cabinet_family="tall",
            door_count=2, drawer_count=0, shelf_count=3,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("MRH-LVL-50"), 8, "Tall = 4 base + 4 family extra")
        self.assertEqual(skus.get("MRH-SHLFPIN-5MM"), 12, "4 × 3 shelves")

    def test_sink_base_extra_water_barrier(self):
        picks = self.Catalog.resolve(
            cabinet_family="sink",
            door_count=2, drawer_count=0, shelf_count=0,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(
            skus.get("MRH-WATERBARRIER-1M"), 1,
            "Sink-base family must include water barrier",
        )

    def test_unknown_family_doesnt_crash(self):
        """An unfamiliar family should resolve to the common per_door /
        per_cabinet rules without a per_family bonus."""
        picks = self.Catalog.resolve(
            cabinet_family="bogus",
            door_count=1, drawer_count=0, shelf_count=1,
            soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertIn("BLM-110-SC", skus)
        self.assertNotIn("MRH-WATERBARRIER-1M", skus)

    # ------------------------------------------------------------------
    # Catalog-level guarantees
    # ------------------------------------------------------------------
    def test_resolution_picks_only_present_skus(self):
        """All SKUs referenced by the resolution map must actually exist
        in the catalog — no orphan references that would lead to silent
        skips at runtime.

        BOM-generation task (2026-07-31): resolve() now matches a SKU
        against EITHER x_marathon_sku OR default_code (the 5 southbrook_
        mrp_pm fastener products carry only default_code) — this
        integrity check follows the same widened match so it doesn't
        false-positive on those.
        """
        Product = self.env["product.product"]
        hardware_map = self.Catalog._hardware_map()
        referenced_skus = set()
        for key in ("per_door_soft_close", "per_door", "per_drawer",
                    "per_shelf", "per_cabinet"):
            referenced_skus.update((hardware_map.get(key) or {}).keys())
        for fam_rules in (hardware_map.get("per_family") or {}).values():
            referenced_skus.update(fam_rules.keys())
        for fam_rules in (hardware_map.get("per_family_subtype") or {}).values():
            referenced_skus.update(fam_rules.keys())
        referenced_skus.update((hardware_map.get("per_family_subtype_hinge") or {}).values())

        missing = []
        for sku in referenced_skus:
            found = Product.search_count([
                "|", ("x_marathon_sku", "=", sku), ("default_code", "=", sku),
            ])
            if not found:
                missing.append(sku)
        self.assertFalse(
            missing,
            f"hardware_map references SKUs absent from the catalog: {missing}",
        )

    # ------------------------------------------------------------------
    # BOM-generation task (2026-07-31) — fastener wiring + corner subtype
    # ------------------------------------------------------------------
    def test_fasteners_wired_into_per_cabinet_per_door_per_drawer(self):
        """The 5 southbrook_mrp_pm fastener products (default_code-only,
        no x_marathon_sku) now resolve through the standard per_cabinet/
        per_door/per_drawer rules."""
        picks = self.Catalog.resolve(
            cabinet_family="base", door_count=1, drawer_count=1,
            shelf_count=0, soft_close=True,
        )
        by_code = {(p.x_marathon_sku or p.default_code): qty for p, qty in picks}
        self.assertEqual(by_code.get("FAST-CONFIRMAT-50"), 4)
        self.assertEqual(by_code.get("FAST-DOWEL-8MM"), 8)
        self.assertEqual(by_code.get("FAST-CAM"), 4)
        self.assertEqual(by_code.get("FAST-HANDLE-SCR"), 2, "2 handle screws x 1 door")
        self.assertEqual(by_code.get("FAST-SLIDE-SCR"), 4, "4 slide screws x 1 drawer")

    def test_corner_subtype_blind_gets_bco_and_specialty_hinge(self):
        """corner_subtype='blind' swaps in the Salice 165deg blind-corner
        hinge and resolves the BCO mechanism instead of the generic
        corner default (CPOS)."""
        picks = self.Catalog.resolve(
            cabinet_family="corner", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True, corner_subtype="blind",
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("SAL-C2R-165"), 2, "165deg blind-corner hinge x2")
        self.assertNotIn("BLM-110-SC", skus, "standard hinge must be fully replaced")
        self.assertEqual(skus.get("SB-CM-BCO"), 1)
        self.assertNotIn("SB-CM-CPOS", skus, "subtype override replaces, not adds")

    def test_corner_subtype_diag_gets_cpos_standard_hinge(self):
        picks = self.Catalog.resolve(
            cabinet_family="corner", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True, corner_subtype="diag",
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("SB-CM-CPOS"), 1)
        self.assertEqual(skus.get("BLM-110-SC"), 2, "standard hinge, no override for diag")
        self.assertNotIn("SAL-C2R-165", skus)

    def test_corner_subtype_wall_gets_no_mechanism(self):
        """A simple diagonal wall corner is self-contained — the
        per_family_subtype override is deliberately empty."""
        picks = self.Catalog.resolve(
            cabinet_family="corner", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True, corner_subtype="wall",
        )
        skus = self._sku_set(picks)
        self.assertNotIn("SB-CM-CPOS", skus)
        self.assertNotIn("SB-CM-BCO", skus)

    def test_corner_without_subtype_keeps_legacy_generic_mechanism(self):
        """Omitting corner_subtype (every pre-existing caller) still gets
        the coarse per_family['corner'] default — fully backward
        compatible."""
        picks = self.Catalog.resolve(
            cabinet_family="corner", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(skus.get("SB-CM-CPOS"), 1)

    # ------------------------------------------------------------------
    # Audit 2026-06-10 — pull_finish wiring tests.
    # Confirms the resolver swaps the legacy brushed-nickel default
    # (MRH-HDL-PUL128) for finish-specific SKUs when pull_finish is
    # passed, while preserving the per-door/per-drawer aggregation.
    # ------------------------------------------------------------------
    def test_matte_black_pull_swaps_default_handle(self):
        """When pull_finish='matte_black' is passed, MRH-HDL-PUL128
        must NOT appear in the result and MRH-HDL-PUL128-MB must
        replace it at the same total quantity."""
        # Baseline (no finish): default brushed nickel handle.
        baseline = self.Catalog.resolve(
            cabinet_family="base", door_count=2, drawer_count=2,
            shelf_count=0, soft_close=True,
        )
        base_skus = self._sku_set(baseline)
        baseline_handle_qty = base_skus.get("MRH-HDL-PUL128", 0)
        self.assertGreater(
            baseline_handle_qty, 0,
            "baseline must include default brushed nickel handles")

        # With pull_finish="matte_black".
        with_finish = self.Catalog.resolve(
            cabinet_family="base", door_count=2, drawer_count=2,
            shelf_count=0, soft_close=True,
            pull_finish="matte_black", pull_size_mm=128,
        )
        skus = self._sku_set(with_finish)
        self.assertNotIn(
            "MRH-HDL-PUL128", skus,
            "default brushed-nickel pull must be stripped when "
            "pull_finish='matte_black'")
        self.assertEqual(
            skus.get("MRH-HDL-PUL128-MB"), baseline_handle_qty,
            "matte-black pull must replace the default at the same qty")

    def test_brushed_nickel_96mm_picks_size_specific_sku(self):
        """When pull_size_mm=96, the resolver picks MRH-HDL-PUL96-BN."""
        picks = self.Catalog.resolve(
            cabinet_family="base", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True,
            pull_finish="brushed_nickel", pull_size_mm=96,
        )
        skus = self._sku_set(picks)
        self.assertIn(
            "MRH-HDL-PUL96-BN", skus,
            "96mm pull SKU must replace the default 128mm")
        self.assertNotIn("MRH-HDL-PUL128", skus)

    def test_antique_bronze_falls_through_to_default_sku(self):
        """An unmapped size (e.g. 96mm in antique_bronze) falls back
        to the finish's 'default' entry."""
        picks = self.Catalog.resolve(
            cabinet_family="base", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True,
            pull_finish="antique_bronze", pull_size_mm=96,
        )
        skus = self._sku_set(picks)
        # The finish has no 96mm entry, so it should fall back to the
        # 'default' which is the 128mm AB SKU.
        self.assertIn("MRH-HDL-PUL128-AB", skus)

    def test_omitted_pull_finish_preserves_legacy_default(self):
        """If pull_finish is None, the legacy behavior persists —
        backward-compatible for callers not yet updated for the
        audit's attr_pull_finish surface."""
        picks = self.Catalog.resolve(
            cabinet_family="base", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True,
        )
        skus = self._sku_set(picks)
        self.assertIn(
            "MRH-HDL-PUL128", skus,
            "legacy default handle must remain when pull_finish is omitted")

    def test_knob_style_swaps_via_by_knob_finish(self):
        """handle_style='knob' uses by_knob_finish instead of
        by_pull_finish + knob_default_skus."""
        # Manually rebuild a per-door scenario where the default knob
        # is what the user wants swapped. The seed's per_door doesn't
        # include MRH-HDL-KNB30, so we just verify the override path
        # doesn't crash + the knob SKU is added when the call asks
        # for it explicitly via mount_appliance + family that includes
        # the knob. Practical knob-aware resolution will need a richer
        # input (e.g. handle_per_door) — out of Path A scope.
        picks = self.Catalog.resolve(
            cabinet_family="base", door_count=1, drawer_count=0,
            shelf_count=0, soft_close=True,
            pull_finish="matte_black", handle_style="knob",
        )
        # The default knob_default_skus (MRH-HDL-KNB30) isn't in per_door,
        # so the strip is a no-op and no knob SKU is added — that's the
        # spec for now. Test that nothing breaks.
        skus = self._sku_set(picks)
        # At minimum the hinges and bumpers should still resolve.
        self.assertIn("BLM-110-SC", skus)
        self.assertIn("MRH-DOORBUMP-CL", skus)

    def test_mount_appliance_adds_appliance_pull(self):
        """tall_oven / tall_fridge cabinets pass mount_appliance=True
        and get an appliance pull per appliance door."""
        picks = self.Catalog.resolve(
            cabinet_family="tall", door_count=2, drawer_count=0,
            shelf_count=0, soft_close=True,
            pull_finish="brushed_nickel", mount_appliance=True,
        )
        skus = self._sku_set(picks)
        self.assertEqual(
            skus.get("MRH-HDL-APP18-BN"), 2,
            "2-door tall_oven should get 2 appliance pulls")
