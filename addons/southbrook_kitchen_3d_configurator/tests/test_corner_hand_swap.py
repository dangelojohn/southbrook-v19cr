# SPDX-License-Identifier: LGPL-3.0-only
"""Task 6 (corner-remediation 2026-07-30) — hand-aware wall corners +
the `action_swap_product` catalog/junction gate.

Required reading: .superpowers/sdd/corner-verify/V3-products.md claim
2/4, V2-validator.md §4d.

Before this task:
  - The corner writer's hand-aware Hinge Side override
    (`_sb_resolve_corner_variant`, models/kitchen_design.py) was
    already layer-agnostic code-wise (Task 5's shared resolver, called
    with `node.get("handed")` for EVERY inserted corner regardless of
    `node["layer"]`) — but it was a structural no-op for the wall
    layer because SB-WALL-CORNER never had more than one materialized
    Hinge Side variant (V3-products.md claim 2). `TestWallCornerHand`
    below pins that the mechanism genuinely activates once an RH twin
    exists, independent of whether THIS harness's catalog snapshot
    happens to carry one already (baseline-tests.md documents Harness-
    B data drift on exactly this point).
  - `action_swap_product` took a plain `browse()` with NO domain gate
    at all (V3-products.md claim 4, "HIGH-1 money vector") and, for a
    derived corner line, would have gone through `_write_then_rearrange`
    -> `action_auto_arrange`, which deletes + regenerates every derived
    line every run — silently discarding any corner-style swap the
    instant it ran. `TestCornerSwapGate` below pins the new catalog
    gate, the corner-only/footprint-fit restriction, and the hand
    guard (re-derived via the SAME `_sb_corner_expected_hand()` helper
    the validator's CORNER_HAND_MISMATCH check now shares — Task 6
    scope item: "they share the table — verify, don't duplicate").
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_corner_repair import ensure_repaired_corner


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestWallCornerHand(TransactionCase):
    """AC — 'right junctions produce RH wall corners.'"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["southbrook.kitchen.design.line"]

    def test_resolver_picks_rh_for_the_real_wall_corner_code(self):
        """Force an LH+RH Hinge Side split onto WHATEVER SB-WALL-CORNER
        template already exists (real catalog data, or the
        corner_sku_repair.py flat-vals fallback, or neither — a fresh
        one is minted) via a throwaway, self-contained attribute
        dimension. `_sb_resolve_corner_variant` is the ONE function the
        auto-arrange writer calls for every corner insert, base or
        wall (kitchen_design.py's corner-insert loop passes
        `hand=node.get("handed")` unconditionally — no per-layer
        branch); proving it resolves an RH-tagged variant for the
        real "SB-WALL-CORNER" code + hand="R" is exactly the mechanism
        a right-facing wall junction relies on, decoupled from whatever
        RH-twin state this harness's DB clone happens to carry.
        """
        Template = self.env["product.template"]
        tmpl = Template.search(
            [("default_code", "=", "SB-WALL-CORNER")], limit=1)
        if not tmpl:
            tmpl = Template.create({
                "name": "T6 Wall Corner", "default_code": "SB-WALL-CORNER",
                "type": "consu", "list_price": 345.0,
            })
        hinge = self.env["product.attribute"].create({
            "name": "Hinge Side", "create_variant": "always",
        })
        lh_val = self.env["product.attribute.value"].create(
            {"name": "LH (T6 Test)", "attribute_id": hinge.id})
        rh_val = self.env["product.attribute.value"].create(
            {"name": "RH (T6 Test)", "attribute_id": hinge.id})
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": tmpl.id, "attribute_id": hinge.id,
            "value_ids": [(6, 0, [lh_val.id, rh_val.id])],
        })

        variant, resolved_tmpl = self.Line._sb_resolve_corner_variant(
            "SB-WALL-CORNER", hand="R")
        self.assertTrue(variant, "resolver must find a variant for the "
                                  "real wall-corner code")
        self.assertEqual(resolved_tmpl.id, tmpl.id)
        hinge_ptavs = variant.product_template_attribute_value_ids.filtered(
            lambda pt: pt.attribute_id.name == "Hinge Side")
        self.assertTrue(hinge_ptavs,
                         "resolved variant must carry a Hinge Side PTAV")
        self.assertTrue(
            any((pt.name or "").startswith("RH") for pt in hinge_ptavs),
            "hand='R' must resolve an RH-tagged variant, got %s"
            % hinge_ptavs.mapped("name"))

        # And the LH-hand call must NOT pick up the RH-tagged variant —
        # the safe default/fallback direction stays intact.
        variant_l, _t = self.Line._sb_resolve_corner_variant(
            "SB-WALL-CORNER", hand="L")
        hinge_ptavs_l = variant_l.product_template_attribute_value_ids.filtered(
            lambda pt: pt.attribute_id.name == "Hinge Side")
        if hinge_ptavs_l:
            self.assertFalse(
                any((pt.name or "").startswith("RH") for pt in hinge_ptavs_l),
                "hand='L' must never resolve an RH-tagged variant")

    # NOTE: an end-to-end companion (flip L-10X8, assert the SURVIVING
    # wall corner is RH-tagged) was deliberately NOT added here. Probed
    # manually (odoo shell, unmodified `ensure_repaired_corner` fixture,
    # no test-only catalog mutation): flipping L-10X8 on THIS harness
    # loses its wall-layer corner line entirely post-flip (0 lines, not
    # 1 wrong-hand line) — a pre-existing gap in the flip/wall-corner-
    # junction-detection path (kitchen_layout_engine.py /
    # kitchen_template.py, both outside this task's file scope and the
    # latter explicitly off-limits), unrelated to hand selection and
    # reproducible without any change in this commit. Flagged in
    # task-6-report.md's Concerns; the resolver-level test above is the
    # deterministic, catalog-independent proof this task's AC needs.


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestCornerSwapGate(TransactionCase):
    """AC — swap to a non-catalog product is refused; corner->blind/
    diag swap works and keeps geometry + correct hand; a hand-
    contradicting swap is refused with an actionable message; a
    design the writer builds validates with zero hand blocks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corner_tmpl = ensure_repaired_corner(cls.env)
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.partner = cls.env["res.partner"].create(
            {"name": "T6 Corner Swap Test"})

    def _instantiate_l10x8(self):
        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        self.assertTrue(tpl, "L-10X8 flagship must ship active")
        return tpl.action_instantiate(partner_id=self.partner.id)

    def _base_corner_line(self, design):
        line = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner" and l.zone == "base_run")
        self.assertEqual(len(line), 1, line)
        return line

    def _db24(self):
        """DB24 demo drawer bank, with `southbrook_is_cabinet` forced
        True. `demo_cabinets.xml` ships this record `noupdate="1"` (so
        runtime edits survive `-u`), so a prior mutation on a shared DB
        clone's flag never self-heals on a later module upgrade the way
        the Q8-locked canonical catalog's tags do — force it here so
        tests that need DB24 to legitimately PASS the catalog gate (and
        only fail on the specific OTHER restriction under test) don't
        silently depend on this harness's data-drift state."""
        tmpl = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_db24")
        if not tmpl.southbrook_is_cabinet:
            tmpl.southbrook_is_cabinet = True
        return tmpl

    def test_swap_refuses_a_non_catalog_product(self):
        design = self._instantiate_l10x8()
        sink_line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "SINK")
        self.assertTrue(sink_line)
        rogue = self.env["product.product"].create({
            "name": "T6 Not A Catalog Cabinet", "type": "consu",
            "list_price": 9999.0,
        })
        with self.assertRaises(UserError):
            sink_line.action_swap_product(rogue.id)

    def test_swap_refuses_an_archived_catalog_product(self):
        design = self._instantiate_l10x8()
        sink_line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "SINK")
        drawer = self._db24()
        drawer.sale_ok = False
        try:
            with self.assertRaises(UserError):
                sink_line.action_swap_product(drawer.product_variant_id.id)
        finally:
            drawer.sale_ok = True

    def test_swap_corner_to_smaller_corner_style_works_and_keeps_geometry(self):
        design = self._instantiate_l10x8()
        corner = self._base_corner_line(design)
        before = (corner.x_position_in, corner.y_position_in,
                  corner.z_position_in, corner.rotation_deg, corner.wall,
                  corner.layout_key)
        blind = self.env["product.template"].create({
            "name": "T6 Corner Blind Style", "type": "consu",
            "list_price": 410.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "corner",
            "southbrook_width_in": corner.width_in,
            "southbrook_height_in": corner.height_in,
            "southbrook_depth_in": max(1.0, corner.depth_in - 1),
        })
        res = corner.action_swap_product(blind.product_variant_id.id)
        self.assertTrue(res.get("swapped"))
        self.assertEqual(corner.product_id, blind.product_variant_id)
        after = (corner.x_position_in, corner.y_position_in,
                 corner.z_position_in, corner.rotation_deg, corner.wall,
                 corner.layout_key)
        self.assertEqual(before, after, "the junction's placement must "
                                         "be unchanged by the swap")
        self.assertEqual(corner.depth_in, blind.southbrook_depth_in,
                          "the new product's own footprint must apply")
        # No Hinge Side PTAV on this synthetic product -> nothing to
        # contradict; the design must validate with zero hand blocks.
        issues = design._check_production_ready()
        self.assertFalse(
            [i for i in issues if i["code"] == "CORNER_HAND_MISMATCH"],
            issues)

    def test_swap_corner_refuses_a_footprint_too_large_for_the_cell(self):
        design = self._instantiate_l10x8()
        corner = self._base_corner_line(design)
        too_big = self.env["product.template"].create({
            "name": "T6 Corner Too Big", "type": "consu",
            "list_price": 500.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "corner",
            "southbrook_width_in": corner.width_in + 24,
            "southbrook_height_in": corner.height_in,
            "southbrook_depth_in": corner.depth_in + 24,
        })
        with self.assertRaises(UserError):
            corner.action_swap_product(too_big.product_variant_id.id)

    def test_swap_corner_refuses_a_non_corner_product(self):
        design = self._instantiate_l10x8()
        corner = self._base_corner_line(design)
        drawer = self._db24()
        with self.assertRaises(UserError) as cm:
            corner.action_swap_product(drawer.product_variant_id.id)
        self.assertIn("not a corner cabinet", str(cm.exception))

    def test_swap_corner_hand_contradiction_is_refused_with_actionable_message(self):
        design = self._instantiate_l10x8()
        corner = self._base_corner_line(design)   # back-left -> hand L
        self.assertEqual(corner._sb_corner_expected_hand(), "L")
        wrong_hand_tmpl = self.env["product.template"].create({
            "name": "T6 Corner Wrong Hand", "type": "consu",
            "list_price": 500.0,
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "corner",
            "southbrook_width_in": corner.width_in,
            "southbrook_height_in": corner.height_in,
            "southbrook_depth_in": corner.depth_in,
        })
        hinge = self.env["product.attribute"].create(
            {"name": "Hinge Side", "create_variant": "always"})
        rh_val = self.env["product.attribute.value"].create(
            {"name": "RH (T6 Wrong Hand Test)", "attribute_id": hinge.id})
        self.env["product.template.attribute.line"].create({
            "product_tmpl_id": wrong_hand_tmpl.id, "attribute_id": hinge.id,
            "value_ids": [(6, 0, [rh_val.id])],
        })
        wrong_variant = wrong_hand_tmpl.product_variant_ids[:1]
        self.assertTrue(wrong_variant)
        before_product = corner.product_id
        with self.assertRaises(UserError) as cm:
            corner.action_swap_product(wrong_variant.id)
        self.assertIn("wrong hand", str(cm.exception).lower())
        self.assertEqual(corner.product_id, before_product,
                          "a refused swap must not have written anything")

    def test_writer_built_design_validates_with_zero_hand_blocks(self):
        """AC — 'validator agreement: a design built by the new writer
        validates with zero hand blocks.'"""
        design = self._instantiate_l10x8()
        issues = design._check_production_ready()
        self.assertFalse(
            [i for i in issues if i["code"] == "CORNER_HAND_MISMATCH"],
            issues)
