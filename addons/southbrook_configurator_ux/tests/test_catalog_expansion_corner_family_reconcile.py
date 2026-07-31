# SPDX-License-Identifier: LGPL-3.0-only
"""T4 code review finding 1 (corner-remediation-2026-07-30) — regression
covering the cross-module interaction between southbrook_estimating's
corner_sku_repair.repair_corner_family_skus() and this module's own
catalog_expansion.build_catalog().

southbrook_configurator_ux depends on southbrook_estimating and its
catalog data is noupdate="0", so build_catalog() re-runs on every
upgrade that also touches southbrook_estimating (module graph: this
module loads AFTER it, and gets cascade-upgraded). Two ways that used
to silently undo the repair:

  (a) the "does this SKU exist" lookup was active-only, so it couldn't
      see SB-CORNER-LSUSAN once the repair archived it — re-minting a
      fresh active duplicate on the very next build_catalog() run.
  (b) SB-CORNER-BLIND's hardcoded Width subset (_W_CORNER, 33/36in)
      didn't include "45 in" — the repair's own new value — so the
      reconcile's write-if-changed guard saw the line's real value_ids
      (33/36/45) differ from the hardcoded list and stripped 45in back
      out, which cascades to unlinking the PTAV the repair's two new
      BLIND variants depend on.

Both are fixed in catalog_expansion.py (active_test=False on the
existence check; a dedicated _W_CORNER_BLIND width subset that
includes 45in). This test simulates the exact sequence — the repair,
then a build_catalog() re-run — and asserts neither regression
reappears.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
    repair_corner_family_skus,
)


@tagged("post_install", "-at_install", "southbrook", "configurator_ux",
        "corner_catalog_truth")
class TestCatalogExpansionCornerFamilyReconcile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["product.template"]
        cls.Product = cls.env["product.product"]
        cls.Expansion = cls.env[
            "southbrook.configurator_ux.catalog_expansion"]

    def _tmpl(self, default_code):
        # Variant-first resolution: product.template.default_code reads
        # back False once a template has more than one variant (see
        # corner_sku_repair.py's _resolve_tmpl docstring) — the repair
        # gives BLIND exactly that (LH + RH twin).
        variant = self.Product.search(
            [("default_code", "=", default_code)], limit=1)
        tmpl = (variant.product_tmpl_id if variant else
                self.Template.search(
                    [("default_code", "=", default_code)], limit=1))
        if not tmpl:
            self.skipTest("%s not seeded on this DB." % default_code)
        return tmpl

    def test_build_catalog_rerun_does_not_undo_corner_repair(self):
        # 1) Run the T4 repair (already covered end-to-end by
        #    southbrook_estimating's own test suite; here it's just the
        #    setup half of the cross-module interaction).
        repair_corner_family_skus(self.env)

        self._tmpl("SB-CORNER-BLIND")
        rh_before = self.Product.search(
            [("default_code", "=", "SB-CORNER-BLIND-R")])
        self.assertTrue(rh_before, "repair must have created the RH twin")
        lsusan_active_before = self.Template.search(
            [("default_code", "=", "SB-CORNER-LSUSAN")])
        self.assertFalse(
            lsusan_active_before,
            "repair must have archived every active LSUSAN duplicate")

        # 2) Simulate the sibling module's own upgrade-time reconcile —
        #    this is what re-fires on every southbrook_configurator_ux
        #    upgrade cascaded from a southbrook_estimating upgrade.
        self.Expansion.build_catalog()

        # (a) no fresh active LSUSAN duplicate re-minted.
        lsusan_active_after = self.Template.search(
            [("default_code", "=", "SB-CORNER-LSUSAN")])
        self.assertFalse(
            lsusan_active_after,
            "build_catalog() must not resurrect an archived "
            "SB-CORNER-LSUSAN as a fresh active duplicate")

        # (b) BLIND's Width line still carries 45in — not stripped back
        #     to the hardcoded 33/36 subset.
        blind = self._tmpl("SB-CORNER-BLIND")
        width_line = blind.attribute_line_ids.filtered(
            lambda l: l.attribute_id.name == "Width")
        self.assertTrue(width_line, "SB-CORNER-BLIND must have a Width line")
        value_names = set(width_line.mapped("value_ids.name"))
        self.assertIn(
            "45 in", value_names,
            "build_catalog() must not strip the 45in Width value the "
            "repair added — got %s" % value_names)

        # The repaired variants (and the PTAVs they reference) are
        # still alive — this is the actual failure mode a stripped
        # 45in causes: _update_product_template_attribute_values()
        # unlinks PTAVs no longer in value_ids, taking any variant
        # combination that depended on them with it.
        rh_after = self.Product.search(
            [("default_code", "=", "SB-CORNER-BLIND-R")])
        self.assertTrue(
            rh_after,
            "the repair's RH twin must survive a build_catalog() rerun")
        widths_on_rh = rh_after.product_template_attribute_value_ids.filtered(
            lambda p: p.attribute_id.name == "Width")
        self.assertTrue(
            all((w.name or "").startswith("45") for w in widths_on_rh),
            "the RH twin's own Width PTAV must still be 45in")

    def test_build_catalog_two_passes_after_repair_mints_no_duplicate(self):
        """Deploy-gate hotfix regression (2026-07-31) — the test above
        (test_build_catalog_rerun_does_not_undo_corner_repair) calls
        repair_corner_family_skus() then build_catalog() in immediate
        succession with no flush/invalidate between them, so Odoo's
        stored compute for product.template.default_code never
        actually recomputed-and-persisted as blank in that window —
        build_catalog() still saw the pre-repair non-blank value and
        resolved the real templates fine. That's exactly why the gate
        caught a duplicate SB-WALL-CORNER template and this suite
        didn't: a real `-u` run's two migrations (southbrook_
        estimating's 19.0.9.1.0 then 19.0.9.2.0) each build a FRESH
        env, forcing a genuine DB re-read of the now-blanked field.
        This test forces the same persist-then-reread cycle
        explicitly, then calls build_catalog() TWICE (fresh calls,
        post-persist) and asserts neither mints a new template."""
        from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
            repair_corner_sku,
        )
        repair_corner_sku(self.env)
        real = self._tmpl("SB-WALL-CORNER")
        # Force the exact precondition the gate hit: the template-
        # level default_code genuinely blank in the DB. Odoo's own
        # stored compute eventually reaches this once a template has
        # more than one variant, but the EXACT recompute timing is
        # DB-state-dependent (e.g. flush_all() alone is not reliably
        # enough to observe it, empirically) — pin the observable
        # end-state directly via raw SQL, the same technique
        # southbrook_estimating's own test_cabinet_variant_ref_repair.
        # py uses for the identical class of precondition.
        self.env.cr.execute(
            "UPDATE product_template SET default_code = NULL WHERE id = %s",
            (real.id,))
        real.invalidate_recordset(["default_code"])
        self.assertFalse(
            real.default_code,
            "sanity: the real template must read blank — the exact "
            "condition the gate hit")

        def _wall_corner_tmpl_ids():
            return set(
                self.Product.search([("default_code", "=", "SB-WALL-CORNER")])
                .mapped("product_tmpl_id").ids)

        # Pass 1 (fresh call, post-persist).
        self.Expansion.build_catalog()
        self.env.flush_all()
        tmpl_ids_pass1 = _wall_corner_tmpl_ids()
        self.assertEqual(
            tmpl_ids_pass1, {real.id},
            "pass 1 must resolve to the real template via the variant, "
            "not mint a duplicate")

        # Pass 2 — fresh call again, post-persist. This is the exact
        # shape that minted the gate's duplicate tmpl 13790 before the
        # variant-first fix.
        self.Expansion.build_catalog()
        self.env.flush_all()
        tmpl_ids_pass2 = _wall_corner_tmpl_ids()
        self.assertEqual(
            tmpl_ids_pass2, tmpl_ids_pass1,
            "pass 2 (fresh build_catalog call, post-persist) must not "
            "mint a NEW template — exactly the deploy-gate-caught bug")

        real_after = self._tmpl("SB-WALL-CORNER")
        self.assertEqual(
            real_after.id, real.id, "the real template must still resolve")
        rh = self.Product.search([("default_code", "=", "SB-WALL-CORNER-R")])
        self.assertTrue(rh, "the RH twin must remain intact")

    def test_build_catalog_two_passes_blind_and_diag_mint_no_duplicate(self):
        """Same two-pass probe as above, for SB-CORNER-BLIND and
        SB-CORNER-DIAG — their templates also go multi-variant via the
        repair's RH twins, so they carry the identical latent risk
        even though (per the T4 code review's own "inert today" note)
        they never actually triggered the bug in the live gate run."""
        from odoo.addons.southbrook_estimating.models.corner_sku_repair import (
            repair_corner_sku,
        )
        repair_corner_sku(self.env)

        for code in ("SB-CORNER-BLIND", "SB-CORNER-DIAG"):
            real = self._tmpl(code)
            # See test_build_catalog_two_passes_after_repair_mints_no_
            # duplicate above for why this is forced via raw SQL
            # rather than relied on from the ORM's own compute timing.
            self.env.cr.execute(
                "UPDATE product_template SET default_code = NULL "
                "WHERE id = %s", (real.id,))
            real.invalidate_recordset(["default_code"])
            self.assertFalse(
                real.default_code,
                "sanity: %s must read blank — the exact condition the "
                "gate hit" % code)

            def _tmpl_ids(code=code):
                return set(
                    self.Product.search([("default_code", "=", code)])
                    .mapped("product_tmpl_id").ids)

            self.Expansion.build_catalog()
            self.env.flush_all()
            first = _tmpl_ids()
            self.assertEqual(first, {real.id}, "%s: pass 1" % code)

            self.Expansion.build_catalog()
            self.env.flush_all()
            second = _tmpl_ids()
            self.assertEqual(
                second, first,
                "%s: pass 2 (fresh build_catalog call) must not mint a "
                "new template" % code)
