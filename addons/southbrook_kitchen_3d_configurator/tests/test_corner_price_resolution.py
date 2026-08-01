# SPDX-License-Identifier: LGPL-3.0-only
"""Task 5 (corner remediation 2026-07-30) — ONE corner price-resolution
path.

Required reading: .superpowers/sdd/corner-verify/V3-products.md claim 3.
Before this fix, two write sites resolved DIFFERENT `product.product`
records for the "same" nominal corner SKU:

  - `action_auto_arrange`'s corner insert resolved by EXACT
    `default_code` match, then a hand-aware "Hinge Side" override —
    landing on whichever specific materialized variant carries that
    code (the "$810-style" attribute-laden variant V3 calls the
    semantic target).
  - `kitchen_template._resolve_slot`'s fallback rung resolved
    template+width and took `tmpls[:1].product_variant_id` — Odoo's
    arbitrary "first" variant for the template (by `product.product`'s
    `_order = 'default_code, name, id'`), not guaranteed to be the
    same row.

Both write sites ALSO used two different price fields: some read
`variant.list_price` (which `product.product` delegates to
`product.template` via `_inherits` — IDENTICAL for every variant of
the same template, silently dropping attribute `price_extra`), others
correctly read `variant.lst_price` (`list_price + price_extra`, which
DOES differ per variant).

`southbrook.kitchen.design.line._sb_resolve_corner_variant` /
`_sb_variant_price` (models/kitchen_design.py) are now the ONE shared
resolver + ONE shared price formula used by the auto-arrange corner
writer, the template fallback rung, `action_swap_product`, and the
reconcile freeze (models/reconcile.py). These tests pin that.
"""
from odoo.tests import TransactionCase, tagged

from .test_corner_repair import _corner_ptav, ensure_repaired_corner


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestSharedCornerVariantResolver(TransactionCase):
    """Low-level pin on a synthetic two-variant corner family with REAL
    attribute `price_extra` divergence — decoupled from any incidental
    catalog id-ordering luck, so this fails under the pre-fix formula
    regardless of what order the real SB-CORNER catalog happens to be
    in on any given DB.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.hinge_attr = cls.env["product.attribute"].create({
            "name": "Hinge Side", "create_variant": "always",
        })
        cls.lh_val = cls.env["product.attribute.value"].create(
            {"name": "LH (Left Hand)", "attribute_id": cls.hinge_attr.id})
        cls.rh_val = cls.env["product.attribute.value"].create(
            {"name": "RH (Right Hand)", "attribute_id": cls.hinge_attr.id})
        cls.tmpl = cls.env["product.template"].create({
            "name": "T5 Test Corner Family", "type": "consu",
            "list_price": 300.0,
        })
        line = cls.env["product.template.attribute.line"].create({
            "product_tmpl_id": cls.tmpl.id,
            "attribute_id": cls.hinge_attr.id,
            "value_ids": [(6, 0, [cls.lh_val.id, cls.rh_val.id])],
        })
        ptavs = line.product_template_value_ids
        cls.lh_ptav = ptavs.filtered(
            lambda p: p.product_attribute_value_id == cls.lh_val)
        cls.rh_ptav = ptavs.filtered(
            lambda p: p.product_attribute_value_id == cls.rh_val)
        cls.lh_ptav.price_extra = 400.0
        cls.rh_ptav.price_extra = 250.0
        variants = cls.tmpl.product_variant_ids
        cls.decoy = variants.filtered(
            lambda v: cls.lh_ptav in v.product_template_attribute_value_ids)
        cls.target = variants.filtered(
            lambda v: cls.rh_ptav in v.product_template_attribute_value_ids)
        # Force codes so the "decoy" (whatever Odoo's default variant
        # accessor surfaces) is NEVER the variant an exact-default_code
        # lookup targets — this is the exact shape of the bug V3 claim 3
        # describes. The pair follows the corner family's own "-R"
        # hand-twin convention (corner_sku_repair owns it): since the
        # 2026-07-31 wizard-variant hardening, the resolver's hand
        # preference only ever picks among the family's coded rows
        # (base code + "-R"), so the fixture must speak that convention
        # for the hand-override tests to exercise the real semantic.
        cls.decoy.default_code = "AAA-T5-DECOY"
        cls.target.default_code = "AAA-T5-DECOY-R"

    def test_00_fixture_reproduces_the_bug_shape(self):
        """Sanity check on the fixture itself: Odoo's `product_variant_id`
        (`product_variant_ids[:1]`, ordered by `default_code, name, id`)
        picks the DECOY, not the variant an exact-code lookup targets —
        i.e. this fixture genuinely reproduces what the old
        `_resolve_slot` fallback rung (`tmpls[:1].product_variant_id`)
        would have picked."""
        self.assertEqual(self.tmpl.product_variant_id.id, self.decoy.id)
        self.assertNotEqual(self.tmpl.product_variant_id.id, self.target.id)

    def test_resolve_corner_variant_picks_the_coded_variant_not_the_default(self):
        variant, tmpl = self.Line._sb_resolve_corner_variant("AAA-T5-DECOY-R")
        self.assertEqual(variant.id, self.target.id)
        self.assertEqual(tmpl.id, self.tmpl.id)

    def test_price_uses_lst_price_not_template_delegated_list_price(self):
        # `list_price` is `_inherits`-delegated to the template — IDENTICAL
        # for every variant regardless of attribute combo.
        self.assertEqual(self.decoy.list_price, self.target.list_price)
        self.assertEqual(self.decoy.list_price, 300.0)
        # `lst_price` (list_price + price_extra) genuinely differs.
        self.assertNotEqual(self.decoy.lst_price, self.target.lst_price)
        self.assertEqual(self.target.lst_price, 550.0)   # 300 + 250
        self.assertEqual(self.decoy.lst_price, 700.0)    # 300 + 400
        # The shared formula must use lst_price, not list_price.
        self.assertEqual(
            self.Line._sb_variant_price(self.target, self.tmpl), 550.0)
        self.assertEqual(
            self.Line._sb_variant_price(self.decoy, self.tmpl), 700.0)

    def test_hand_override_redirects_even_when_code_matches_the_other_hand(self):
        # Exact-code lookup for "AAA-T5-DECOY" always lands on `decoy`
        # (which carries the LH ptav); asking for hand="R" must redirect
        # to `target` (the RH-carrying variant) — same override the
        # auto-arrange corner writer has always applied.
        variant, _tmpl = self.Line._sb_resolve_corner_variant(
            "AAA-T5-DECOY", hand="R")
        self.assertEqual(variant.id, self.target.id)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCornerPricingConvergence(TransactionCase):
    """Integration-level ACs against the REAL SB-CORNER catalog
    (`ensure_repaired_corner` — LH `SB-CORNER` + RH `SB-CORNER-R`
    twins, matching live prod's repaired shape)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corner_tmpl = ensure_repaired_corner(cls.env)
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.partner = cls.env["res.partner"].create(
            {"name": "T5 Convergence Test"})

    def _instantiate_l10x8(self):
        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        self.assertTrue(tpl, "L-10X8 flagship must ship active")
        return tpl, tpl.action_instantiate(partner_id=self.partner.id)

    def test_resolve_slot_corner_rung_matches_auto_arrange_default_path(self):
        """AC — 'the same junction priced via template path and via
        auto-arrange path is identical.' Nudge the LH twin's real
        attribute price_extra away from whatever this DB clone's
        pre-existing combo carries (Harness B is a dev-DB clone with
        data drift — the corner-price divergence claim's own live
        numbers, e.g. $625, come from THIS variant's pre-existing combo,
        not a bare template list_price) so the specific number asserted
        below is provably NOT just an accident of an untouched fixture.
        The generic, DB-independent version of this same regression pin
        lives in TestSharedCornerVariantResolver above."""
        lh_ptav = _corner_ptav(self.env, self.corner_tmpl, "Hinge Side", "LH")
        if lh_ptav:
            lh_ptav.price_extra += 37.0

        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        corner_slot = tpl.line_ids.filtered(
            lambda s: s.cabinet_type == "corner")
        self.assertTrue(corner_slot, "L-10X8 must carry a corner slot")

        # "Template path" — kitchen_template._resolve_slot's fallback rung.
        template_path_variant = tpl._resolve_slot(
            corner_slot, corner_slot.nominal_width_in or 36.0)
        # "Auto-arrange path" — the same default (no rule sku, no hand)
        # resolution action_auto_arrange's corner writer performs when a
        # node carries neither a rule SKU nor a resolved hand.
        auto_arrange_variant, auto_arrange_tmpl = \
            self.Line._sb_resolve_corner_variant("SB-CORNER")

        self.assertTrue(template_path_variant)
        self.assertEqual(template_path_variant.id, auto_arrange_variant.id)
        self.assertEqual(template_path_variant.default_code, "SB-CORNER")
        template_path_price = self.Line._sb_variant_price(
            template_path_variant, template_path_variant.product_tmpl_id)
        auto_arrange_price = self.Line._sb_variant_price(
            auto_arrange_variant, auto_arrange_tmpl)
        self.assertEqual(template_path_price, auto_arrange_price)
        # It's the variant's real lst_price (extras-inclusive), not the
        # template's bare (extras-dropped) list_price of 0.0.
        self.assertNotEqual(template_path_price, 0.0)
        self.assertEqual(template_path_price, template_path_variant.lst_price)

    def test_auto_arrange_regenerates_byte_identical_corner_price(self):
        """AC — 'regenerating the same template twice yields byte-
        identical price_unit on every line.'"""
        _tpl, design = self._instantiate_l10x8()

        def _corner_snapshot():
            corners = design.cabinet_line_ids.filtered(
                lambda l: l.layout_role == "derived"
                and l.cabinet_type == "corner")
            return sorted(
                (l.layout_key, l.product_id.id, l.price_unit)
                for l in corners)

        first = _corner_snapshot()
        self.assertTrue(
            first, "L-10X8 must produce at least one derived corner line")
        design.action_auto_arrange(sync=True)
        second = _corner_snapshot()
        self.assertEqual(
            first, second,
            "regenerating the same template twice must yield byte-"
            "identical corner price_unit per line")

    def test_swap_product_uses_the_same_shared_price_formula(self):
        """`action_swap_product` (the 3rd named call site) must price
        through the same shared formula, not a bespoke inline one.
        Uses the LH `SB-CORNER` variant, which — unlike the RH twin —
        is guaranteed present regardless of this DB clone's corner-
        repair data-drift state (baseline-tests.md)."""
        _tpl, design = self._instantiate_l10x8()
        corner_variant = self.env["product.product"].search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        self.assertTrue(corner_variant)
        expected = self.Line._sb_variant_price(
            corner_variant, corner_variant.product_tmpl_id)
        # Derived corner lines are engine-owned; swap the CANONICAL sink
        # line instead to exercise the API without touching engine state.
        sink_line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "SINK")
        self.assertTrue(sink_line)
        sink_line.action_swap_product(corner_variant.id)
        self.assertEqual(sink_line.price_unit, expected)

    def test_reconcile_recomputes_stale_corner_price_from_current_variant(self):
        """The 4th named call site — the reconcile freeze — must heal a
        stale/wrong frozen `price_unit` on a corner line to the CURRENT
        variant's shared-formula price, without touching the design
        line's own `price_unit` (the Sprint-1 mirror never writes back)."""
        lh_ptav = _corner_ptav(self.env, self.corner_tmpl, "Hinge Side", "LH")
        if lh_ptav:
            lh_ptav.price_extra += 120.0
        corner_variant = self.env["product.product"].search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T5 Reconcile Test Design",
            "partner_id": self.partner.id,
        })
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id,
            "product_id": corner_variant.id,
            "quantity": 1,
            # Deliberately stale/wrong — simulates a snapshot frozen by
            # the old buggy formula (or a since-changed catalog).
            "price_unit": 1.0,
            "cabinet_type": "corner",
            "zone": "base_run",
            "width_in": 36.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "t5-reconcile-corner",
            "layout_role": "derived",
        })
        expected = self.Line._sb_variant_price(
            corner_variant, corner_variant.product_tmpl_id)
        self.assertNotEqual(
            expected, 1.0, "fixture must actually diverge from the "
                           "stale value for this test to mean anything")

        Reconcile = self.env["southbrook.design.reconcile"]
        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        Reconcile._reconcile_one(design, stats)

        self.assertTrue(line.sale_order_line_id)
        self.assertEqual(line.sale_order_line_id.price_unit, expected)
        self.assertEqual(
            line.price_unit, 1.0,
            "the design line's own authored price_unit must stay "
            "untouched — the mirror never writes back")

    def test_reconcile_does_not_reprice_a_confirmed_order(self):
        """T5 review fix (CRITICAL) — the corner-price heal must never
        touch a line on a quote the customer already saw. This runs from
        BOTH the 5-min cron (any design whose write_date advanced) and
        synchronously from action_auto_arrange(sync=True) on a design
        tied to an already-confirmed order, so a later catalog
        price_extra edit must not silently rewrite that order's frozen
        corner line."""
        corner_variant = self.env["product.product"].search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T5 Reconcile Confirmed-Order Test Design",
            "partner_id": self.partner.id,
        })
        original_price = 999.0
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id,
            "product_id": corner_variant.id,
            "quantity": 1,
            "price_unit": original_price,
            "cabinet_type": "corner",
            "zone": "base_run",
            "width_in": 36.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "t5-reconcile-confirmed-corner",
            "layout_role": "derived",
        })
        Reconcile = self.env["southbrook.design.reconcile"]
        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        # First tick (still draft) mirrors + freezes the SO line at the
        # design line's own price_unit (999.0 — deliberately NOT the
        # variant's current lst_price, so a later "heal" would be
        # detectable).
        Reconcile._reconcile_one(design, stats)
        self.assertTrue(design.sale_order_id)
        design.sale_order_id.state = "sale"  # confirm the quote
        frozen_price = line.sale_order_line_id.price_unit

        # A catalog edit after confirmation — the exact scenario the
        # guard exists for. Bump the TEMPLATE's list_price directly
        # (rather than a specific attribute's price_extra) — this DB
        # clone's pre-existing SB-CORNER variant carries whatever
        # attribute combo Harness B's corner-repair data drift left it
        # with (baseline-tests.md), so a specific PTAV bump isn't
        # guaranteed to land on THIS variant's actual combination;
        # list_price unconditionally moves lst_price
        # (`= list_price + price_extra`) regardless of combo specifics.
        corner_variant.product_tmpl_id.list_price += 500.0
        current_variant_price = self.Line._sb_variant_price(
            corner_variant, corner_variant.product_tmpl_id)

        Reconcile._reconcile_one(design, stats)

        self.assertNotEqual(
            current_variant_price, frozen_price,
            "fixture must actually diverge for this test to prove the "
            "guard did something")
        self.assertEqual(
            line.sale_order_line_id.price_unit, frozen_price,
            "a confirmed order's corner line must NOT be repriced by a "
            "later catalog edit")

    def test_reconcile_still_heals_a_draft_orders_corner_price(self):
        """Companion to the confirmed-order guard above: the draft case
        must still heal (the guard must be state-scoped, not a blanket
        regression of the T5 fix)."""
        corner_variant = self.env["product.product"].search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T5 Reconcile Draft-Order Heal Test Design",
            "partner_id": self.partner.id,
        })
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id,
            "product_id": corner_variant.id,
            "quantity": 1,
            "price_unit": 1.0,
            "cabinet_type": "corner",
            "zone": "base_run",
            "width_in": 36.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "t5-reconcile-draft-heal-corner",
            "layout_role": "derived",
        })
        Reconcile = self.env["southbrook.design.reconcile"]
        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        Reconcile._reconcile_one(design, stats)
        self.assertTrue(design.sale_order_id)
        self.assertEqual(design.sale_order_id.state, "draft")
        expected = self.Line._sb_variant_price(
            corner_variant, corner_variant.product_tmpl_id)
        self.assertEqual(line.sale_order_line_id.price_unit, expected)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_price_resolution")
class TestResolverIgnoresWizardVariants(TransactionCase):
    """Prod regression (2026-07-31, smoke B): a customer's wizard-
    configured variant — code-less, carrying a Hinge Side PTAV — was
    the ONLY hinge-carrying variant on the blind template, so the
    resolver's hand preference (formerly filtered over ALL variants)
    outranked the exact-code match and auto-arrange wrote the user's
    personal configuration (a code-less line) into brand-new designs.
    The hand preference must only ever pick among the family's own
    coded rows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        hinge = cls.env["product.attribute"].create({
            "name": "Hinge Side", "create_variant": "dynamic",
        })
        lh_val = cls.env["product.attribute.value"].create(
            {"name": "LH (Left Hand)", "attribute_id": hinge.id})
        rh_val = cls.env["product.attribute.value"].create(
            {"name": "RH (Right Hand)", "attribute_id": hinge.id})
        width = cls.env["product.attribute"].create({
            "name": "Width", "create_variant": "dynamic",
        })
        w33 = cls.env["product.attribute.value"].create(
            {"name": "33 in", "attribute_id": width.id})
        w45 = cls.env["product.attribute.value"].create(
            {"name": "45 in", "attribute_id": width.id})
        cls.tmpl = cls.env["product.template"].create({
            "name": "T6 Blind-Shape Corner", "type": "consu",
            "list_price": 500.0,
        })
        hinge_line = cls.env["product.template.attribute.line"].create({
            "product_tmpl_id": cls.tmpl.id, "attribute_id": hinge.id,
            "value_ids": [(6, 0, [lh_val.id, rh_val.id])],
        })
        width_line = cls.env["product.template.attribute.line"].create({
            "product_tmpl_id": cls.tmpl.id, "attribute_id": width.id,
            "value_ids": [(6, 0, [w33.id, w45.id])],
        })
        hinge_ptavs = hinge_line.product_template_value_ids
        cls.lh_ptav = hinge_ptavs.filtered(
            lambda p: p.product_attribute_value_id == lh_val)
        width_ptavs = width_line.product_template_value_ids
        cls.w33_ptav = width_ptavs.filtered(
            lambda p: p.product_attribute_value_id == w33)
        cls.w45_ptav = width_ptavs.filtered(
            lambda p: p.product_attribute_value_id == w45)
        Product = cls.env["product.product"]
        # The family's seeded row: coded, hinge-LESS (the prod blind
        # 1104 shape — a single Width value, no Hinge Side at all).
        cls.seeded = Product.create({
            "product_tmpl_id": cls.tmpl.id,
            "product_template_attribute_value_ids":
                [(6, 0, cls.w45_ptav.ids)],
            "default_code": "T6-BLIND",
        })
        # The customer's wizard config: code-less, and the template's
        # ONLY variant carrying a Hinge Side PTAV.
        cls.wizard = Product.create({
            "product_tmpl_id": cls.tmpl.id,
            "product_template_attribute_value_ids":
                [(6, 0, (cls.lh_ptav | cls.w33_ptav).ids)],
        })

    def test_hand_preference_never_picks_a_code_less_wizard_variant(self):
        variant, tmpl = self.Line._sb_resolve_corner_variant(
            "T6-BLIND", hand="L")
        self.assertEqual(tmpl.id, self.tmpl.id)
        self.assertEqual(
            variant.id, self.seeded.id,
            "hand preference scanned ALL variants and picked the "
            "customer's code-less wizard config (%s) instead of the "
            "family's own coded row" % variant.default_code)

    def test_wizard_variant_untouched_by_resolution(self):
        self.Line._sb_resolve_corner_variant("T6-BLIND", hand="L")
        self.assertFalse(self.wizard.default_code or "")
        self.assertIn(self.w33_ptav,
                      self.wizard.product_template_attribute_value_ids)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestNonCornerReconcileConfirmedOrderFreeze(TransactionCase):
    """Task 33 (2026-07-31, T5 follow-up) — the confirmed-order price
    freeze T5 built (see `test_reconcile_does_not_reprice_a_confirmed_order`
    above) was CORNER-scoped: `_reconcile_one`'s per-line
    `sol.sudo().write(line_vals)` (models/reconcile.py) always carries
    "price_unit" in line_vals, and sale.order.line.price_unit is
    `compute='_compute_price_unit', store=True, readonly=False` in stock
    Odoo — writing product_id/product_uom_qty/etc (which line_vals
    legitimately still carries for every non-corner mirror write too)
    re-triggers that compute and silently overwrites price_unit,
    regardless of cabinet_type. These tests mirror
    TestCornerPricingConvergence's confirmed-order pair exactly, but for
    an ordinary "base" cabinet line and a throwaway in-test product —
    decoupled from the SB-CORNER catalog fixture entirely (never touches
    record 102 or any real sale order)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.partner = cls.env["res.partner"].create(
            {"name": "T33 Non-Corner Convergence Test"})
        cls.base_product = cls.env["product.product"].create({
            "name": "T33 Test Base Cabinet",
            "type": "consu",
            "list_price": 400.0,
        })

    def test_reconcile_does_not_reprice_a_confirmed_orders_noncorner_line(self):
        """The exposure this task closes: a non-corner reconcile write
        must never touch a line on a quote the customer already saw.
        Reproduce-first: this must FAIL on pre-fix code (the old guard
        only pinned price_unit when cabinet_type == "corner")."""
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T33 Reconcile Confirmed-Order Test Design",
            "partner_id": self.partner.id,
        })
        original_price = 777.0
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id,
            "product_id": self.base_product.id,
            "quantity": 1,
            "price_unit": original_price,
            "cabinet_type": "base",
            "zone": "base_run",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "t33-reconcile-confirmed-base",
            "layout_role": "canonical",
        })
        Reconcile = self.env["southbrook.design.reconcile"]
        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        # First tick (still draft) mirrors the design line's authored
        # price_unit (777.0) onto the new SO line verbatim.
        Reconcile._reconcile_one(design, stats)
        self.assertTrue(design.sale_order_id)
        self.assertTrue(line.sale_order_line_id)
        self.assertEqual(line.sale_order_line_id.price_unit, original_price)
        design.sale_order_id.state = "sale"  # confirm the quote
        frozen_price = line.sale_order_line_id.price_unit

        # Change the design-side price input AFTER confirmation — the
        # exact scenario the guard exists for (e.g. a designer edits the
        # line, or an upstream price recompute changes it).
        line.price_unit = 1500.0

        Reconcile._reconcile_one(design, stats)

        self.assertNotEqual(
            line.price_unit, frozen_price,
            "fixture must actually diverge for this test to prove the "
            "guard did something")
        self.assertEqual(
            line.sale_order_line_id.price_unit, frozen_price,
            "a confirmed order's non-corner line must NOT be repriced "
            "by a later design-side price change")

    def test_reconcile_still_heals_a_draft_orders_noncorner_line(self):
        """Companion to the confirmed-order guard above: the draft case
        must still heal (the guard must be state-scoped, not a blanket
        regression of the Task 33 fix)."""
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T33 Reconcile Draft-Order Heal Test Design",
            "partner_id": self.partner.id,
        })
        line = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id,
            "product_id": self.base_product.id,
            "quantity": 1,
            "price_unit": 111.0,
            "cabinet_type": "base",
            "zone": "base_run",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "origin": "configurator",
            "layout_key": "t33-reconcile-draft-heal-base",
            "layout_role": "canonical",
        })
        Reconcile = self.env["southbrook.design.reconcile"]
        stats = {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                  "created_lines": 0, "divergence": 0, "errors": 0}
        Reconcile._reconcile_one(design, stats)
        self.assertTrue(design.sale_order_id)
        self.assertEqual(design.sale_order_id.state, "draft")
        self.assertEqual(line.sale_order_line_id.price_unit, 111.0)

        # Edit the design-side price input while still draft — must heal.
        line.price_unit = 222.0
        Reconcile._reconcile_one(design, stats)
        self.assertEqual(line.sale_order_line_id.price_unit, 222.0)
