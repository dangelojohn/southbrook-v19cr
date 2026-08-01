# SPDX-License-Identifier: LGPL-3.0-only
"""Task 1 (corner-remediation 2026-07-30) — validator repair regression
suite: collision (#3 rewrite), out-of-room bounds (new), corner hand
mismatch (new).

Companion to test_collision_matrix.py (which covers #7 FOOTPRINT_COLLISION
and #12/#13 MOTION_ENVELOPE_COLLISION/CORNER_FILLER_MISSING) and the
U/L-shape "no geometry/hand blocks" pins in test_u_shape_template.py /
test_corner_repair.py. This file targets the three checks whose logic
Task 1 actually rewrote or added, with each scenario asserting EXACTLY
one targeted block naming the offending line (brief AC).
"""
from odoo.tests.common import TransactionCase, tagged

from .test_corner_repair import ensure_repaired_corner


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_repair")
class TestCabinetCollisionCheck(TransactionCase):
    """Check #3 rewrite — grouped by (wall, zone), real anchor
    footprints, 2mm tolerance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wall_corner_prod = cls.env["product.product"].create(
            {"name": "VR Corner Wall", "default_code": "ZZ-VR-WCORNER",
             "list_price": 295.0})
        cls.corner_l_prod = cls.env["product.product"].create(
            {"name": "VR Corner Base L", "default_code": "ZZ-VR-CORNER-L",
             "list_price": 625.0})
        cls.corner_r_prod = cls.env["product.product"].create(
            {"name": "VR Corner Base R", "default_code": "ZZ-VR-CORNER-R",
             "list_price": 625.0})
        cls.w24_prod = cls.env["product.product"].create(
            {"name": "VR Wall 24", "default_code": "ZZ-VR-W24",
             "list_price": 150.0})
        cls.sink_prod = cls.env["product.product"].create(
            {"name": "VR Sink Base", "default_code": "ZZ-VR-SINK",
             "list_price": 220.0})
        cls.b24_prod = cls.env["product.product"].create(
            {"name": "VR Base 24", "default_code": "ZZ-VR-B24",
             "list_price": 200.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Validator Repair Test"})

    def _make_design(self, room_width_in=120.0, room_depth_in=96.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "Validator repair collision test",
            "partner_id": self.partner.id,
            "room_width_in": room_width_in, "room_depth_in": room_depth_in,
            "room_height_in": 96.0, "state": "draft",
        })

    def test_record_102_shaped_left_right_corner_pairs_no_false_positive(self):
        """record-102-shaped data (V1-geometry.md §3, both walls' tables,
        hand-verified against the real anchor-pose math): a base-layer
        corner stacked under a wall-layer corner at the SAME x/z cell,
        contiguous with the next run cabinet, on BOTH the left and right
        wall. Under the OLD "group by cabinet_type, sort by
        x_position_in" heuristic this guaranteed a false CABINET_COLLISION
        on every such pair (all four "corner"-typed lines tie on
        x_position_in per wall — a left/right-wall cabinet's x is a
        constant cross-offset, not an along-wall coordinate). The
        rewritten (wall, zone)-grouped, real-footprint check must report
        none of it.
        """
        design = self._make_design()
        Line = self.env["southbrook.kitchen.design.line"]

        # LEFT wall (rot=90, footprint = [z-w, z]).
        Line.create({
            "design_id": design.id, "product_id": self.wall_corner_prod.id,
            "quantity": 1, "price_unit": 295.0, "cabinet_type": "corner",
            "zone": "wall", "wall": "left", "rotation_deg": 90,
            "width_in": 24.02, "height_in": 30.0, "depth_in": 24.02,
            "x_position_in": 0.0, "z_position_in": 24.02,
            "run_seq": -1, "origin": "configurator",
            "layout_key": "corner-%s-back-left-wall" % design.id,
            "layout_role": "derived",
        })
        Line.create({
            "design_id": design.id, "product_id": self.w24_prod.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "zone": "wall", "wall": "left", "rotation_deg": 90,
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "x_position_in": 0.0, "z_position_in": 48.02,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vr-left-wall-w24",
        })
        Line.create({
            "design_id": design.id, "product_id": self.corner_l_prod.id,
            "quantity": 1, "price_unit": 625.0, "cabinet_type": "corner",
            "zone": "base_run", "wall": "left", "rotation_deg": 90,
            "width_in": 35.98, "height_in": 34.5, "depth_in": 35.98,
            "x_position_in": 0.0, "z_position_in": 35.98,
            "run_seq": -1, "origin": "configurator",
            "layout_key": "corner-%s-back-left-base" % design.id,
            "layout_role": "derived",
        })
        Line.create({
            "design_id": design.id, "product_id": self.sink_prod.id,
            "quantity": 1, "price_unit": 220.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "left", "rotation_deg": 90,
            "width_in": 30.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 65.98,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vr-left-base-sink",
        })

        # RIGHT wall (rot=270, footprint = [z, z+w]).
        Line.create({
            "design_id": design.id, "product_id": self.wall_corner_prod.id,
            "quantity": 1, "price_unit": 295.0, "cabinet_type": "corner",
            "zone": "wall", "wall": "right", "rotation_deg": 270,
            "width_in": 24.02, "height_in": 30.0, "depth_in": 24.02,
            "x_position_in": 120.0, "z_position_in": 0.0,
            "run_seq": -1, "origin": "configurator",
            "layout_key": "corner-%s-back-right-wall" % design.id,
            "layout_role": "derived",
        })
        Line.create({
            "design_id": design.id, "product_id": self.w24_prod.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "zone": "wall", "wall": "right", "rotation_deg": 270,
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "x_position_in": 120.0, "z_position_in": 24.02,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vr-right-wall-w24",
        })
        Line.create({
            "design_id": design.id, "product_id": self.corner_r_prod.id,
            "quantity": 1, "price_unit": 625.0, "cabinet_type": "corner",
            "zone": "base_run", "wall": "right", "rotation_deg": 270,
            "width_in": 35.98, "height_in": 34.5, "depth_in": 35.98,
            "x_position_in": 120.0, "z_position_in": 0.0,
            "run_seq": -1, "origin": "configurator",
            "layout_key": "corner-%s-back-right-base" % design.id,
            "layout_role": "derived",
        })
        Line.create({
            "design_id": design.id, "product_id": self.b24_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "right", "rotation_deg": 270,
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 120.0, "z_position_in": 35.98,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vr-right-base-b24",
        })

        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("CABINET_COLLISION", codes, issues)

    def test_genuine_same_wall_same_zone_overlap_still_blocks(self):
        """Two base_run cabinets on the SAME wall with truly overlapping
        footprints (14" of real z-overlap, far past the 2mm tolerance)
        must still block — and, per the brief's AC ("exactly ONE
        targeted BLOCK"), exactly once. Task 1 code-review Finding 2:
        check #3's (wall, zone) grouping is always a subset of check
        #7's layer grouping, so this exact pair is ALSO same-layer and
        would otherwise double-fire CABINET_COLLISION +
        FOOTPRINT_COLLISION for one physical overlap; #3 now dedupes by
        deferring to #7's richer (coordinate + rotation) message. BOMs
        are seeded on both products so MISSING_BOM doesn't also block,
        keeping the total blocking-issue count meaningful."""
        design = self._make_design()
        Bom = self.env["mrp.bom"]
        Bom.create({"product_tmpl_id": self.sink_prod.product_tmpl_id.id})
        Bom.create({"product_tmpl_id": self.b24_prod.product_tmpl_id.id})
        Line = self.env["southbrook.kitchen.design.line"]
        a = Line.create({
            "design_id": design.id, "product_id": self.sink_prod.id,
            "quantity": 1, "price_unit": 220.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "left", "rotation_deg": 90,
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 30.0,
            "run_seq": 0, "origin": "configurator",
            "layout_key": "vr-overlap-a",
        })
        b = Line.create({
            "design_id": design.id, "product_id": self.b24_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run", "wall": "left", "rotation_deg": 90,
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 0.0, "z_position_in": 40.0,
            "run_seq": 1, "origin": "configurator",
            "layout_key": "vr-overlap-b",
        })
        issues = design._check_production_ready()
        blocking = [i for i in issues if i["severity"] == "blocking"]
        self.assertEqual(len(blocking), 1, issues)
        self.assertEqual(blocking[0]["code"], "FOOTPRINT_COLLISION", issues)
        codes = [i["code"] for i in issues]
        self.assertNotIn("CABINET_COLLISION", codes, issues)
        self.assertIn("(line #%d)" % a.id, blocking[0]["message"])
        self.assertIn("(line #%d)" % b.id, blocking[0]["message"])


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_repair")
class TestOutOfRoomBoundsCheck(TransactionCase):
    """New check — every ACTIVE line's footprint must lie within the
    room (kitchen_layout_engine.obb_within_room), previously never
    wired into the validator."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prod = cls.env["product.product"].create(
            {"name": "VR OOB Base", "default_code": "ZZ-VR-OOB",
             "list_price": 200.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Validator Repair OOB Test"})

    def test_line_parked_far_outside_room_blocks(self):
        """Real prod debris shape (design 99): a line parked at
        x=1,000,000in must produce exactly one blocking
        OUT_OF_ROOM_BOUNDS naming the line."""
        design = self.env["southbrook.kitchen.design"].create({
            "name": "OOB test", "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })
        debris = self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": self.prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 1000000.0, "z_position_in": 0.0,
            "rotation_deg": 0, "wall": "back", "run_seq": 0,
            "origin": "configurator", "layout_key": "vr-oob-debris",
        })
        issues = design._check_production_ready()
        oob = [i for i in issues if i["code"] == "OUT_OF_ROOM_BOUNDS"]
        self.assertEqual(len(oob), 1, issues)
        self.assertEqual(oob[0]["severity"], "blocking")
        self.assertIn("(line #%d)" % debris.id, oob[0]["message"])

    def test_line_inside_room_does_not_block(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "In-bounds test", "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
        })
        self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": self.prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "zone": "base_run",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "x_position_in": 10.0, "z_position_in": 0.0,
            "rotation_deg": 0, "wall": "back", "run_seq": 0,
            "origin": "configurator", "layout_key": "vr-in-bounds",
        })
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("OUT_OF_ROOM_BOUNDS", codes, issues)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "validator_repair")
class TestCornerHandMismatchCheck(TransactionCase):
    """New check — a derived corner line's assigned product must carry
    the Hinge Side the junction actually needs (V2-validator.md Claim
    4d: the corner writer's `handed[:1] or variant` fallback had no
    validator backstop)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.corner_tmpl = ensure_repaired_corner(cls.env)

    def test_wrong_hand_corner_blocks(self):
        if len(self.corner_tmpl.product_variant_ids) < 2:
            # Known Harness-B data drift (baseline-tests.md) — see the
            # identical guard on TestCornerSkuRepair.
            # test_l10x8_no_geometry_hand_blocks for the full rationale.
            self.skipTest(
                "corner repair did not produce an LH+RH twin on this DB "
                "(known Harness-B data drift)")
        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        self.assertTrue(tpl, "L-10X8 flagship must ship active")
        design = tpl.action_instantiate()
        corner = design.cabinet_line_ids.filtered(
            lambda l: l.layout_role == "derived"
            and l.cabinet_type == "corner" and l.zone == "base_run")
        self.assertEqual(len(corner), 1, corner)
        self.assertEqual(corner.product_id.default_code, "SB-CORNER",
                          "back-left must be hand-picked LH pre-swap")

        # Force the WRONG hand onto the line, exactly the failure mode
        # the silent `handed[:1] or variant` fallback allowed to ship
        # unnoticed.
        wrong_hand = self.env["product.product"].search(
            [("default_code", "=", "SB-CORNER-R")], limit=1)
        self.assertTrue(wrong_hand, "repair must have created the RH twin")
        corner.write({"product_id": wrong_hand.id})

        issues = design._check_production_ready()
        mismatches = [i for i in issues if i["code"] == "CORNER_HAND_MISMATCH"]
        self.assertEqual(len(mismatches), 1, issues)
        self.assertEqual(mismatches[0]["severity"], "blocking")
        self.assertIn("(line #%d)" % corner.id, mismatches[0]["message"])

    def test_correct_hand_corner_does_not_block(self):
        tpl = self.env["southbrook.kitchen.template"].search(
            [("code", "=", "L-10X8")], limit=1)
        design = tpl.action_instantiate()
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("CORNER_HAND_MISMATCH", codes, issues)
