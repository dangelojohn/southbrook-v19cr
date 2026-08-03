# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-041 — the reconciler must mirror the walls a design actually uses.

Until 19.0.5.47.0 `southbrook.design.reconcile._reconcile_one` created a
single synthetic wall ("Wall A", spanning room_width_in) per room and
hardcoded `layout_shape = "straight"` at room-creation time, never
revisiting it. A comment deferred multi-wall shapes to "Sprint 2"; the
reverse cron shipped, this did not.

Two findings in the 2026-08-03 E2E review were that one line of code:

  C-03  every L- and U-shaped design was stored as layout_shape
        "straight" / wall_count 1. On production, 7 of the 8 bridged
        designs contradicted their own geometry, so the quote, the BOM
        and the plan all described a straight kitchen.

  C-06  every sale.order.line was pinned to that single wall, so which
        wall a cabinet stands on never survived the trip to the quote.

These tests pin the corrected behaviour so a future refactor cannot
quietly collapse back to one wall. Fixture style follows the sibling
`test_m1_m2_reconcile_fixes.py`: TransactionCase, test-only records, no
reliance on demo or live data.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "rec_d_idempotency")
class TestReconcileMultiWall(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Design = cls.env["southbrook.kitchen.design"]
        cls.Line = cls.env["southbrook.kitchen.design.line"]
        cls.Partner = cls.env["res.partner"]
        cls.Template = cls.env["product.template"]
        cls.Reconcile = cls.env["southbrook.design.reconcile"]

        cls.partner = cls.Partner.create({
            "name": "SBK041 Test Customer",
            "email": "sbk041.customer@southbrook.test",
        })

        tmpl = cls.Template.create({
            "name": "SBK041 Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 500.0,
        })
        tmpl.write({
            "southbrook_is_cabinet": True,
            "southbrook_cabinet_type": "base",
            "southbrook_width_in": 24.0,
        })
        cls.variant = tmpl.product_variant_id

    # ── helpers ───────────────────────────────────────────────────────
    def _stats(self):
        return {"mirrored": 0, "created_orders": 0, "created_rooms": 0,
                "created_walls": 0, "created_lines": 0, "divergence": 0,
                "errors": 0}

    def _design_on_walls(self, walls, name="SBK041 Design"):
        """A design with one configurator cabinet on each named wall."""
        design = self.Design.create({
            "name": name,
            "partner_id": self.partner.id,
            "room_width_in": 120.0,
            "room_depth_in": 96.0,
            "room_height_in": 96.0,
        })
        for idx, wall in enumerate(walls):
            self.Line.create({
                "design_id":    design.id,
                "product_id":   self.variant.id,
                "quantity":     1,
                "price_unit":   500.0,
                "cabinet_type": "base",
                "origin":       "configurator",
                "wall":         wall,
                "run_seq":      idx,
                "width_in":     24.0,
                "height_in":    34.5,
                "depth_in":     24.0,
            })
        return design

    # ── layout_shape derivation ───────────────────────────────────────
    def test_01_single_wall_is_straight(self):
        design = self._design_on_walls(["back"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(design.room_id.layout_shape, "straight")
        self.assertEqual(design.room_id.wall_count, 1)

    def test_02_two_adjacent_walls_is_an_l_shape(self):
        design = self._design_on_walls(["back", "left"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            design.room_id.layout_shape, "l_shape",
            "two runs meeting at a corner is an L, not a straight run")
        self.assertEqual(design.room_id.wall_count, 2)

    def test_03_two_opposite_walls_is_a_galley(self):
        design = self._design_on_walls(["back", "front"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            design.room_id.layout_shape, "galley",
            "two runs facing each other across the room is a galley")

    def test_04_three_walls_is_a_u_shape(self):
        design = self._design_on_walls(["back", "left", "right"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(design.room_id.layout_shape, "u_shape")
        self.assertEqual(design.room_id.wall_count, 3)

    def test_05_four_walls_is_a_g_shape(self):
        design = self._design_on_walls(["back", "left", "right", "front"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(design.room_id.layout_shape, "g_shape")
        self.assertEqual(design.room_id.wall_count, 4)

    def test_06_empty_design_reconciles_as_straight_not_as_an_error(self):
        """No lines means no information, which reads as a straight run —
        never a fabricated shape."""
        design = self.Design.create({
            "name": "SBK041 Empty Design",
            "partner_id": self.partner.id,
            "room_width_in": 120.0,
            "room_depth_in": 96.0,
        })
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(design.room_id.layout_shape, "straight")

    # ── the shape must keep up as the design changes ──────────────────
    def test_07_shape_is_re_derived_when_a_design_grows_into_a_u(self):
        """The original bug in its purest form: a design reconciled while
        straight, then extended onto two more walls, used to stay
        "straight" forever because only the create branch set it."""
        design = self._design_on_walls(["back"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(design.room_id.layout_shape, "straight")

        for idx, wall in enumerate(("left", "right")):
            self.Line.create({
                "design_id":    design.id,
                "product_id":   self.variant.id,
                "quantity":     1,
                "price_unit":   500.0,
                "cabinet_type": "base",
                "origin":       "configurator",
                "wall":         wall,
                "run_seq":      10 + idx,
                "width_in":     24.0,
                "height_in":    34.5,
                "depth_in":     24.0,
            })
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            design.room_id.layout_shape, "u_shape",
            "the refresh branch must re-derive the shape, not only create")

    def test_08_a_human_chosen_shape_is_never_overwritten(self):
        """island / peninsula / custom express intent a wall set cannot
        encode. Flattening them to a derived value would be exactly the
        unrequested edit this product is trying to stop making."""
        design = self._design_on_walls(["back", "left"])
        self.Reconcile._reconcile_one(design, self._stats())
        design.room_id.layout_shape = "island"

        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            design.room_id.layout_shape, "island",
            "a shape the reconciler could not have produced is the "
            "designer's, and must survive reconciliation")

    # ── per-wall records and per-line attribution ─────────────────────
    def test_09_one_wall_record_per_occupied_wall_with_correct_spans(self):
        design = self._design_on_walls(["back", "left"])
        self.Reconcile._reconcile_one(design, self._stats())

        walls = {w.name: w for w in design.room_id.wall_ids}
        self.assertEqual(set(walls), {"Back Wall", "Left Wall"})
        # Back/front span the room's width; left/right span its depth.
        self.assertEqual(walls["Back Wall"].length_mm, round(120.0 * 25.4))
        self.assertEqual(walls["Left Wall"].length_mm, round(96.0 * 25.4))

    def test_10_quote_lines_point_at_the_wall_they_are_actually_on(self):
        """C-06: this is what made a U-shaped kitchen arrive at the shop
        as an unordered pile of boxes."""
        design = self._design_on_walls(["back", "left", "right"])
        self.Reconcile._reconcile_one(design, self._stats())

        by_wall = {}
        for dline in design.cabinet_line_ids:
            sol = dline.sale_order_line_id
            self.assertTrue(sol, "every configurator line must mirror")
            by_wall[dline.wall] = sol.wall_id.name

        self.assertEqual(by_wall, {
            "back":  "Back Wall",
            "left":  "Left Wall",
            "right": "Right Wall",
        })
        self.assertEqual(
            len({v for v in by_wall.values()}), 3,
            "three cabinets on three walls must not collapse onto one")

    def test_11_reconciling_twice_creates_no_duplicate_walls(self):
        design = self._design_on_walls(["back", "left", "right"])
        self.Reconcile._reconcile_one(design, self._stats())
        first = len(design.room_id.wall_ids)
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            len(design.room_id.wall_ids), first,
            "wall identity is wall_order, so re-running must be a no-op")

    def test_12_a_legacy_wall_a_is_adopted_not_duplicated(self):
        """Every room in production already carries a wall named "Wall A"
        at wall_order 10. It must become the back wall in place, so that
        existing sale.order.line.wall_id keeps pointing at a live record."""
        design = self._design_on_walls(["back"])
        self.Reconcile._reconcile_one(design, self._stats())
        legacy = design.room_id.wall_ids
        self.assertEqual(len(legacy), 1)
        legacy.name = "Wall A"          # restore the pre-fix state

        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(
            len(design.room_id.wall_ids), 1,
            "the legacy wall must be adopted, not joined by a second one")
        self.assertEqual(design.room_id.wall_ids.id, legacy.id)
        self.assertEqual(design.room_id.wall_ids.name, "Back Wall")

    def test_13_walls_that_fall_out_of_use_are_kept_not_destroyed(self):
        """The cron's contract is that it never destroys authoring data.
        A wall may still carry constraints or manually-authored lines."""
        design = self._design_on_walls(["back", "left"])
        self.Reconcile._reconcile_one(design, self._stats())
        self.assertEqual(len(design.room_id.wall_ids), 2)

        design.cabinet_line_ids.filtered(lambda line: line.wall == "left").unlink()
        self.Reconcile._reconcile_one(design, self._stats())

        self.assertEqual(
            len(design.room_id.wall_ids), 2,
            "an emptied wall is left in place; deleting it would take "
            "its constraints and any manual lines with it")
        self.assertEqual(design.room_id.layout_shape, "straight")
