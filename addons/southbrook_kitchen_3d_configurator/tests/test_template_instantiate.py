# SPDX-License-Identifier: LGPL-3.0-only
"""Task 2 (kitchen templates) — resolver + action_instantiate.

Covers the instantiation contract from the spec:
- canonical design lines + poses from the EXISTING auto-arrange engine,
- appliance slots become design lines (dropdown width wins),
- unresolved slots land as VISIBLE placeholders (never dropped/substituted),
- no silent room growth (savepoint-atomic failure),
- module_width_in is a real parameter.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestTemplateInstantiate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tpl = cls.env["southbrook.kitchen.template"]
        Slot = cls.env["southbrook.kitchen.template.line"]
        base = cls.env.ref("southbrook_estimating.base_2dr")
        sink = cls.env.ref("southbrook_estimating.sink_base")
        wall = cls.env.ref("southbrook_estimating.wall_2dr")
        cls.tpl = Tpl.create({
            "name": "T2 Straight 10ft", "code": "T2-SW-10",
            "layout_shape": "straight",
            "default_room_width_in": 120.0, "default_room_depth_in": 96.0,
            "default_room_height_in": 96.0, "default_module_width_in": 24.0,
            "min_cabinet_count": 2,
        })
        Slot.create([
            {"template_id": cls.tpl.id, "slot_code": "SINK", "cabinet_type": "base",
             "wall": "back", "run_seq": 10, "product_id": sink.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "B1", "cabinet_type": "base",
             "wall": "back", "run_seq": 20, "repeat_ok": True,
             "product_id": base.product_variant_id.id},
            {"template_id": cls.tpl.id, "slot_code": "RANGE",
             "cabinet_type": "appliance", "appliance_type": "range",
             "wall": "back", "run_seq": 30, "nominal_width_in": 30.0},
            {"template_id": cls.tpl.id, "slot_code": "W1", "cabinet_type": "wall",
             "wall": "back", "run_seq": 10,
             "product_id": wall.product_variant_id.id},
        ])

    def test_instantiate_creates_canonical_lines_with_engine_poses(self):
        design = self.tpl.action_instantiate()
        self.assertEqual(design.state, "configured")
        self.assertEqual(design.room_width_in, 120.0)  # template defaults, untouched
        canon = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "canonical")
        self.assertGreaterEqual(len(canon), 4)
        xs = sorted(canon.filtered(lambda l: l.cabinet_type == "base")
                    .mapped("x_position_in"))
        self.assertEqual(len(xs), len(set(xs)),
                         "base run x positions must be distinct")

    def test_appliance_slot_becomes_design_line(self):
        design = self.tpl.action_instantiate(
            appliance_widths={"range": 36.0})
        appl = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "appliance")
        self.assertEqual(len(appl), 1)
        self.assertEqual(appl.zone, "accessory")
        self.assertEqual(appl.width_in, 36.0)  # dropdown width wins
        self.assertEqual(appl.appliance_type, "range")
        self.assertIn("APPLIANCE", appl.position_label)

    def test_unresolved_slot_is_visible_placeholder(self):
        arch = self.env["southbrook.cabinet.archetype"].create({
            "code": "T2-NOPRODUCT", "name": "T2 ghost archetype",
            "collection": "other", "body_class": "base",
            "cabinet_type": "XX"})
        self.env["southbrook.kitchen.template.line"].create({
            "template_id": self.tpl.id, "slot_code": "GHOST",
            "cabinet_type": "base", "wall": "back", "run_seq": 40,
            "archetype_id": arch.id})
        design = self.tpl.action_instantiate()
        ghost = design.cabinet_line_ids.filtered(lambda l: l.is_unresolved)
        self.assertEqual(len(ghost), 1, "unresolved slot must land as a line")
        self.assertTrue(ghost.position_label.startswith("UNRESOLVED"))
        issues = design._check_production_ready()
        self.assertIn("UNRESOLVED_SLOT", [i["code"] for i in issues])

    def test_no_silent_room_growth(self):
        before = self.env["southbrook.kitchen.design"].search_count([])
        with self.assertRaises(UserError):
            self.tpl.action_instantiate(cabinet_count=40)  # can't fit 10 ft
        self.assertEqual(
            self.env["southbrook.kitchen.design"].search_count([]), before,
            "failed instantiation must not leave a half-built design")

    def test_module_width_parametric(self):
        design = self.tpl.action_instantiate(module_width_in=21.0)
        b1 = design.cabinet_line_ids.filtered(
            lambda l: l.template_slot_code == "B1")
        self.assertEqual(b1.width_in, 21.0)

    # ── Task 16 (SBK-028) — appliance footprint is genuinely RESERVED ──
    # by the run math, not just cosmetically present as a line. T2-SW-10's
    # back wall: SINK (33in, fixed) + B1 (24in, repeat_ok) + RANGE
    # (30in, fixed appliance) + W1 (wall layer, doesn't consume floor
    # capacity). Room width 120in, module 24in.
    #   fixed[back]    = 33 (SINK) + 30 (RANGE) = 63
    #   capacity[back] = floor((120 - 63) / 24) = 2   <- B1 may grow by 2
    #   n_fixed_modules = 2 floor non-repeat/repeat slots (SINK+B1) - 1 repeat = 1
    #   n_max = 1 + 2 = 3
    # If the 30in RANGE were NOT reserved, capacity would floor
    # ((120-33)/24) = 3 and n_max would be 4 instead — the exact
    # off-by-one a silently-dropped appliance line would produce.
    def test_appliance_reservation_bounds_repeat_growth(self):
        fit = self.tpl.parametric_fit(module_width_in=24.0)
        self.assertTrue(fit["ok"], fit["message"])
        self.assertEqual(
            fit["n_max"], 3,
            "the RANGE slot's 30in must be subtracted from the back "
            "wall's capacity BEFORE computing how many extra B1 modules "
            "can grow into it")
        # One more than n_max must fail — proves the cap is load-bearing,
        # not a coincidence of the default count.
        with self.assertRaises(UserError):
            self.tpl.action_instantiate(cabinet_count=4, module_width_in=24.0)

    def test_appliance_reservation_consumes_wall_length_no_overlap(self):
        """The 30in RANGE gap must occupy real, exclusive wall length —
        every other floor cabinet on the run packs AROUND it, never
        through it, and the run never exceeds the room."""
        design = self.tpl.action_instantiate(cabinet_count=3, module_width_in=24.0)
        floor = design.cabinet_line_ids.filtered(
            lambda l: l.wall == "back" and l.cabinet_type != "wall"
        ).sorted(lambda l: l.x_position_in)
        # SINK(33) + B1(24) + B1(24) + RANGE(30) — nothing absorbed
        # (no corner in this template) and nothing dropped.
        self.assertEqual(len(floor), 4)
        widths = floor.mapped("width_in")
        self.assertEqual(sum(widths), 111.0)
        # No overlap: each line's [x, x+width] interval is disjoint from
        # the next along the run.
        prev_end = 0.0
        for line in floor:
            self.assertGreaterEqual(
                line.x_position_in + 1e-6, prev_end,
                "cabinet run must never overlap the previous line's "
                "reserved footprint")
            prev_end = line.x_position_in + line.width_in
        self.assertLessEqual(prev_end, design.room_width_in + 1e-6)
        # The RANGE slot itself really did reserve exactly 30in.
        range_line = floor.filtered(lambda l: l.cabinet_type == "appliance")
        self.assertEqual(len(range_line), 1)
        self.assertEqual(range_line.width_in, 30.0)
        self.assertEqual(range_line.price_unit, 0.0)

    def test_instantiation_chatter_reconciles_with_line_count(self):
        """The instantiation chatter message must report the ACTUAL
        post-arrange line count (honesty contract, Task 16) — not the
        pre-arrange slot-plan count. On T2-SW-10 (no corners), nothing
        gets absorbed, so written == requested slots and both numbers
        must appear, reconciling with len(cabinet_line_ids)."""
        design = self.tpl.action_instantiate(cabinet_count=3, module_width_in=24.0)
        msg = design.message_ids.filtered(
            lambda m: "Instantiated from template" in (m.body or ""))
        self.assertEqual(len(msg), 1, "instantiation chatter must be posted")
        body = msg.body
        self.assertIn(
            "%d line(s) written" % len(design.cabinet_line_ids), body,
            "chatter's written-count must equal len(cabinet_line_ids): %s"
            % body)
        self.assertIn("0 absorbed", body)
        self.assertIn("0 dropped", body)
        self.assertIn("0 unresolved", body)
