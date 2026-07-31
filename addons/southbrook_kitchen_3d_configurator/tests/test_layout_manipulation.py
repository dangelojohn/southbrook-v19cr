# SPDX-License-Identifier: LGPL-3.0-only
"""Task 5 (kitchen templates) — server-side layout manipulation API.

Every action mutates ONLY canonical semantics (wall/run_seq/product/
width) and re-derives ALL poses through the existing auto-arrange
engine — poses are never transformed numerically. A transform that
doesn't fit raises and leaves the design untouched (savepoint-atomic;
the room is never grown).
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestLayoutManipulation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Same trick as test_zone_corner_l_shape_fixes: the corner
        # engine inserts a derived corner only when the corner SKU has
        # a variant. The canonical SB-CORNER is config_ok/variantless
        # in a bare DB, but SB-WALL-CORNER is creatable — enough for a
        # derived (wall-layer) corner line to exist on L designs.
        Template = cls.env["product.template"]
        if not Template.search([("default_code", "=", "SB-WALL-CORNER")],
                               limit=1):
            Template.create({"name": "Corner Wall",
                             "default_code": "SB-WALL-CORNER",
                             "list_price": 295.0})
        Tpl = cls.env["southbrook.kitchen.template"]
        Slot = cls.env["southbrook.kitchen.template.line"]
        cls.tpl = Tpl.create({
            "name": "T5 Straight 10ft", "code": "T5-SW-10",
            "layout_shape": "straight",
            "default_room_width_in": 120.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
        })
        Slot.create([
            {"template_id": cls.tpl.id, "slot_code": "SINK",
             "cabinet_type": "base", "wall": "back", "run_seq": 10,
             "nominal_width_in": 30.0},
            {"template_id": cls.tpl.id, "slot_code": "B1",
             "cabinet_type": "base", "wall": "back", "run_seq": 20,
             "repeat_ok": True},
            {"template_id": cls.tpl.id, "slot_code": "RANGE",
             "cabinet_type": "appliance", "appliance_type": "range",
             "wall": "back", "run_seq": 30, "nominal_width_in": 30.0},
        ])
        cls.tpl_l = Tpl.create({
            "name": "T5 L 200", "code": "T5-L-200",
            "layout_shape": "l_shape",
            "default_room_width_in": 200.0, "default_room_depth_in": 200.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
        })
        Slot.create([
            {"template_id": cls.tpl_l.id, "slot_code": "SINK",
             "cabinet_type": "base", "wall": "back", "run_seq": 10,
             "nominal_width_in": 30.0},
            {"template_id": cls.tpl_l.id, "slot_code": "B1",
             "cabinet_type": "base", "wall": "back", "run_seq": 20,
             "repeat_ok": True},
            {"template_id": cls.tpl_l.id, "slot_code": "B2",
             "cabinet_type": "base", "wall": "left", "run_seq": 10,
             "nominal_width_in": 24.0},
            {"template_id": cls.tpl_l.id, "slot_code": "B3",
             "cabinet_type": "base", "wall": "left", "run_seq": 20,
             "repeat_ok": True},
            {"template_id": cls.tpl_l.id, "slot_code": "W1",
             "cabinet_type": "wall", "wall": "back", "run_seq": 10,
             "nominal_width_in": 24.0},
            {"template_id": cls.tpl_l.id, "slot_code": "W2",
             "cabinet_type": "wall", "wall": "left", "run_seq": 10,
             "nominal_width_in": 24.0},
        ])

    def _canon(self, design):
        return design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "canonical")

    def test_flip_x_round_trip_is_identity(self):
        design = self.tpl_l.action_instantiate()
        canon = self._canon(design)
        before = {l.id: (l.wall, l.run_seq) for l in canon}
        design.action_flip_layout(axis="x")
        after_once = {l.id: (l.wall, l.run_seq) for l in canon}
        self.assertNotEqual(before, after_once, "flip must change assignment")
        design.action_flip_layout(axis="x")
        self.assertEqual(
            {l.id: (l.wall, l.run_seq) for l in canon}, before,
            "flip twice = identity on (wall, run_seq)")

    def test_rotate_four_quarters_is_identity(self):
        design = self.tpl_l.action_instantiate()
        canon = self._canon(design)
        before = {l.id: (l.wall, l.run_seq) for l in canon}
        for _ in range(4):
            design.action_rotate_layout(quarters=1)
        self.assertEqual({l.id: (l.wall, l.run_seq) for l in canon}, before)

    def test_swap_product_reprices_and_rearranges(self):
        design = self.tpl.action_instantiate()
        line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")[:1]
        # DB24 demo drawer bank — same width, different price (180 -> 210).
        drawer_tmpl = self.env.ref(
            "southbrook_kitchen_3d_configurator.product_tmpl_db24")
        # Task 6 (corner-remediation 2026-07-30) — action_swap_product now
        # gates on southbrook_is_cabinet=True (V3-products.md claim 4,
        # "HIGH-1 money vector"; was previously an unguarded browse()).
        # demo_cabinets.xml ships this record `noupdate="1"` (by design —
        # so runtime edits survive -u), which means a prior mutation on a
        # shared DB clone's southbrook_is_cabinet flag never self-heals on
        # a later module upgrade the way the Q8-locked canonical catalog's
        # tags do (canonical_catalog_tag.xml reapplies every -u). Force
        # the flag here so this test's own precondition doesn't silently
        # depend on which harness/DB-clone data-drift state it happens to
        # run against.
        if not drawer_tmpl.southbrook_is_cabinet:
            drawer_tmpl.southbrook_is_cabinet = True
        drawer = drawer_tmpl.product_variant_id
        price_before = design.estimated_price
        line.action_swap_product(drawer.id)
        self.assertEqual(line.product_id, drawer)
        self.assertNotEqual(design.estimated_price, price_before)

    def test_set_width_capacity_honesty(self):
        design = self.tpl.action_instantiate()
        line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")[:1]
        w = line.width_in
        with self.assertRaises(UserError):
            line.action_set_width(400.0)     # cannot fit a 10ft room
        self.assertEqual(line.width_in, w,
                         "failed action must roll back fully")

    def test_move_reorders_run_and_rearranges(self):
        # Straight design: move the module cabinet to the head of its
        # run (run_seq 20 -> 5, before the sink) — semantics change,
        # poses re-derive, x positions shift. (Cross-wall moves that
        # form a junction can legitimately be superseded by the corner
        # engine, so the stable contract here is in-run reordering.)
        design = self.tpl.action_instantiate()
        line = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")[:1]
        sink = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "SINK")[:1]
        x_before = line.x_position_in
        self.assertGreater(line.x_position_in, sink.x_position_in)
        # Instantiate-time arrange renumbered the run 0..n, so "before
        # the sink" means any key below the sink's current run_seq.
        line.action_move(wall="back", run_seq=sink.run_seq - 1)
        # run_seq is an ORDERING key — auto-arrange renumbers it after
        # layout, so the contract is relative order, not the literal key.
        self.assertEqual(line.wall, "back")
        self.assertLess(line.run_seq, sink.run_seq,
                        "moved cabinet must now precede the sink")
        self.assertNotEqual(line.x_position_in, x_before,
                            "reordering the run must re-derive poses")
        self.assertLess(line.x_position_in, sink.x_position_in)

    def test_reflow_is_auto_arrange_alias(self):
        design = self.tpl.action_instantiate()
        res = design.action_reflow()
        self.assertIn("corners", res)        # auto-arrange's return shape

    def test_derived_lines_are_engine_owned(self):
        design = self.tpl_l.action_instantiate()
        derived = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner")
        self.assertTrue(derived, "L design must derive a corner line")
        with self.assertRaises(UserError):
            derived[:1].action_set_width(30.0)
