# SPDX-License-Identifier: LGPL-3.0-only
"""M4 — collision-matrix regression suite.

Makes docs/research/corner-engine/06-collision-matrix.md's trailing
```json``` block (23 interference pairs — pair_id, elements,
geometric_condition, min_clearance_in) visible in CI, per the M4 task
brief. Pair_ids below were read from that JSON block at authoring time
(2026-07-27) and hardcoded — this file never parses the doc at runtime.

Coverage:

  Group A (pure kitchen_layout_engine, no ORM):
    - corner-door-vs-door           } test 1 (blind-corner filler run)
    - filler-corner-generic-rule    }
    - drawer-vs-perpendicular-drawer-corner   (test 2, raw layout())
    - dishwasher-corner-clearance             (test 3, AABB primitive)

  Group B (ORM, TransactionCase, mirrors test_zone_corner_l_shape_fixes.py):
    - action_auto_arrange -> no FOOTPRINT_COLLISION on a clean L design
    - a forced same-footprint write -> blocking FOOTPRINT_COLLISION
    - MOTION_ENVELOPE_COLLISION (validator check #10/#12 — a corner
      rule's clearance_front_mm box vs. a same-layer solid)
    - CORNER_FILLER_MISSING (validator check #11/#13 — a rule-demanded
      filler line deleted after auto-arrange)
    These two checks were landing CONCURRENTLY with this suite (see the
    M4 task brief) — `_validator_source_has()` feature-detects the code
    string in `_check_production_ready`'s source and skipTest()s rather
    than hard-failing if a check is absent from the snapshot under test.

  Group C — the remaining 19 pair_ids the engine cannot express yet
    (appliance/drawer/handle/mechanism modeling): one skipTest per
    pair_id so the full 23-pair matrix stays visible in CI even though
    only 4 pairs are geometrically exercised today.

4 pairs covered (Groups A/B) + 19 deferred (Group C) = 23, matching the
matrix's pair count exactly.
"""
import inspect

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E

MM = 25.4


def _validator_source_has(design_model, code):
    """Best-effort feature-detection over `_check_production_ready`'s
    source text. This suite is written against a validator that (per
    the M4 task brief) is being extended CONCURRENTLY with two new
    checks — MOTION_ENVELOPE_COLLISION and CORNER_FILLER_MISSING. Tests
    that depend on either one must degrade to a clean skip, not a hard
    failure, when run against a snapshot that predates it.
    """
    try:
        src = inspect.getsource(design_model._check_production_ready)
    except (OSError, TypeError):
        return True
    return code in src


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "collision_matrix")
class TestCollisionMatrixPureEngine(TransactionCase):
    """Group A — pure kitchen_layout_engine assertions, no ORM writes.
    Follows the style of southbrook_estimating/tests/test_layout_engine.py
    (same engine, same helpers), framed against specific matrix pair_ids.
    """

    ROOM = {"width_mm": 3000.0, "depth_mm": 3000.0, "height_mm": 2440.0}

    def test_blind_corner_filler_bridges_cell_and_first_run_cabinet(self):
        """pair_id: corner-door-vs-door / filler-corner-generic-rule.

        An L-kitchen (back+left walls) resolved through `corner_rules`
        with a rule carrying filler_x_mm=76.2 (3in — the matrix's
        converged "3-inch corner filler" figure, doc §14 /
        filler-corner-generic-rule) must produce (a) zero same-layer
        footprint overlaps, always checkable, and (b) — if the engine
        emits filler nodes (M3) — a filler strip that exactly bridges
        the corner cell and the first surviving run cabinet on the
        filler's wall, sized to the rule's filler_x_mm.
        """
        cabs = [
            {"id": "back0", "width_mm": 900.0, "depth_mm": 609.6,
             "height_mm": 876.0, "family": "base", "cabinet_type": "base",
             "zone": "base_run", "wall": "back", "run_seq": 0},
            {"id": "back1", "width_mm": 900.0, "depth_mm": 609.6,
             "height_mm": 876.0, "family": "base", "cabinet_type": "base",
             "zone": "base_run", "wall": "back", "run_seq": 1},
            {"id": "left0", "width_mm": 900.0, "depth_mm": 609.6,
             "height_mm": 876.0, "family": "base", "cabinet_type": "base",
             "zone": "base_run", "wall": "left", "run_seq": 0},
            {"id": "left1", "width_mm": 900.0, "depth_mm": 609.6,
             "height_mm": 876.0, "family": "base", "cabinet_type": "base",
             "zone": "base_run", "wall": "left", "run_seq": 1},
        ]
        rule = {
            "rule_id": "test-blind-filler", "sku": "SB-CORNER-TEST",
            "tier": "base", "sequence": 1,
            "leg_x_mm": 900.0, "leg_z_mm": 900.0, "height_mm": 876.0,
            "min_leg_x_mm": 900.0, "min_leg_z_mm": 900.0,
            "filler_x_mm": 76.2, "filler_z_mm": 0.0,
        }
        r = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                  corner_rules=[rule])

        # (a) zero same-layer overlaps — always checkable, regardless of
        # whether filler emission (M3) has landed.
        place = {p["id"]: p for p in r["placements"]}
        cabinets_out = r["cabinets"]
        for i in range(len(cabinets_out)):
            for j in range(i + 1, len(cabinets_out)):
                a, b = cabinets_out[i], cabinets_out[j]
                if E._layer_of(a) != E._layer_of(b):
                    continue
                self.assertFalse(
                    E.footprints_overlap(E.footprint_mm(a, place[a["id"]]),
                                          E.footprint_mm(b, place[b["id"]])),
                    "%s overlaps %s" % (a["id"], b["id"]))

        # (b) feature-detect M3 filler emission.
        fillers = r.get("fillers")
        if not fillers:
            self.skipTest("engine filler emission (M3) not yet present")
        self.assertEqual(len(fillers), 1, fillers)
        filler = fillers[0]
        self.assertAlmostEqual(filler["width_mm"], 76.2, delta=0.5)
        fp_filler = E.footprint_mm(filler, filler["__pose"])
        fp_back1 = E.footprint_mm({"width_mm": 900.0, "depth_mm": 609.6},
                                   place["back1"])
        # The filler's near edge sits at the corner leg (900mm) and its
        # far edge meets the first surviving back-wall cabinet's near
        # edge — it exactly bridges the reserved gap.
        self.assertAlmostEqual(fp_filler[0], 900.0, delta=1.0)
        self.assertAlmostEqual(fp_filler[1], fp_back1[0], delta=1.0)

    def test_raw_layout_without_corner_resolution_interpenetrates(self):
        """pair_id: drawer-vs-perpendicular-drawer-corner.

        Two BASE cabinets on adjoining walls, placed by the bare
        `layout()` with NO corner detection/resolution at all,
        interpenetrate at the shared corner — this IS the failure mode
        a corner cell exists to prevent (matrix §4: "both extend...
        their fronts... occupy the same corner airspace"). Pins the
        failure this whole engine exists to solve, not the fix.
        """
        cabs = [
            {"id": "back0", "width_mm": 900.0, "depth_mm": 609.6,
             "family": "base", "cabinet_type": "base", "zone": "base_run",
             "wall": "back", "run_seq": 0},
            {"id": "left0", "width_mm": 900.0, "depth_mm": 609.6,
             "family": "base", "cabinet_type": "base", "zone": "base_run",
             "wall": "left", "run_seq": 0},
        ]
        placements = E.layout(cabs, self.ROOM)
        place = {p["id"]: p for p in placements}
        fp_back = E.footprint_mm(cabs[0], place["back0"])
        fp_left = E.footprint_mm(cabs[1], place["left0"])
        self.assertTrue(
            E.footprints_overlap(fp_back, fp_left),
            "expected back0/left0 to interpenetrate at the corner "
            "without resolution: %r vs %r" % (fp_back, fp_left))

    def test_dishwasher_corner_clearance_2in_as_pure_geometry(self):
        """pair_id: dishwasher-corner-clearance.

        Geometry-primitive documentation of the matrix's 2in (50.8mm)
        minimum corner clearance for a dishwasher: inflate a corner
        cell's footprint by 50.8mm along the appliance-facing edge, then
        confirm an appliance box 1in away collides with the inflated
        cell while one 3in away clears it. Pure AABB math — no
        appliance model, no product involved.
        """
        clearance_mm = 50.8   # 2in, matrix pair min_clearance_in=2
        corner_cell = (0.0, 900.0, 0.0, 900.0)
        inflated = (corner_cell[0], corner_cell[1] + clearance_mm,
                    corner_cell[2], corner_cell[3])

        def _appliance_at(gap_mm):
            x0 = corner_cell[1] + gap_mm
            return (x0, x0 + 600.0, 0.0, 600.0)

        appliance_1in = _appliance_at(25.4)     # 1in separation
        appliance_3in = _appliance_at(76.2)     # 3in separation

        self.assertTrue(
            E.footprints_overlap(inflated, appliance_1in),
            "1in gap with a 2in required clearance must still collide")
        self.assertFalse(
            E.footprints_overlap(inflated, appliance_3in),
            "3in gap clears the 2in required clearance")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "collision_matrix")
class TestCollisionMatrixFootprintValidator(TransactionCase):
    """Group B — action_auto_arrange + `_check_production_ready`'s
    FOOTPRINT_COLLISION check (#7), mirroring
    test_zone_corner_l_shape_fixes.py's TestLShapeAutoArrange fixture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Template = cls.env["product.template"]
        for code, name, price in [("SB-CORNER", "Corner Base", 625.0),
                                   ("SB-WALL-CORNER", "Corner Wall", 295.0)]:
            if not Template.search([("default_code", "=", code)], limit=1):
                Template.create({"name": name, "default_code": code,
                                 "list_price": price})
        cls.base_prod = cls.env["product.product"].create(
            {"name": "CM-Test Base 24", "default_code": "ZZ-CM-B24",
             "list_price": 200.0})
        cls.wall_prod = cls.env["product.product"].create(
            {"name": "CM-Test Wall 24", "default_code": "ZZ-CM-W24",
             "list_price": 150.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Collision Matrix Test"})

    def _make_l_design(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Collision matrix L-shape", "partner_id": self.partner.id,
            "room_width_in": 200.0, "room_depth_in": 200.0,
            "room_height_in": 96.0, "state": "draft",
        })
        Line = self.env["southbrook.kitchen.design.line"]
        seq = 0
        for wall in ("back", "left"):
            for cabinet_type, prod in (("base", self.base_prod),
                                        ("wall", self.wall_prod)):
                for i in range(3):
                    Line.create({
                        "design_id": design.id, "product_id": prod.id,
                        "quantity": 1, "price_unit": prod.list_price,
                        "cabinet_type": cabinet_type,
                        "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
                        "sequence": seq, "run_seq": i, "wall": wall,
                        "origin": "configurator",
                        "layout_key": "cm-%s-%s-%d" % (wall, cabinet_type, i),
                    })
                    seq += 10
        return design

    def test_full_l_design_auto_arrange_has_no_footprint_collision(self):
        design = self._make_l_design()
        design.action_auto_arrange()
        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("FOOTPRINT_COLLISION", codes, issues)

    def test_forced_overlap_is_reported_as_blocking_footprint_collision(self):
        design = self._make_l_design()
        design.action_auto_arrange()
        base_lines = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "base")
        self.assertGreaterEqual(len(base_lines), 2, base_lines)
        a, b = base_lines[0], base_lines[1]
        # Bypass the engine entirely — direct line.write onto a's exact
        # footprint forces a same-layer collision the validator must
        # catch regardless of how it got there.
        b.write({
            "x_position_in": a.x_position_in,
            "z_position_in": a.z_position_in,
            "rotation_deg": a.rotation_deg,
            "width_in": a.width_in,
            "depth_in": a.depth_in,
        })
        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("FOOTPRINT_COLLISION", by_code, issues)
        self.assertEqual(by_code["FOOTPRINT_COLLISION"]["severity"],
                          "blocking")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "collision_matrix")
class TestCollisionMatrixMotionEnvelopeAndFiller(TransactionCase):
    """Group B (continued) — validator checks #10/#12
    (MOTION_ENVELOPE_COLLISION) and #11/#13 (CORNER_FILLER_MISSING).
    Both are gated behind `southbrook.placement.rule` existing and were
    landing CONCURRENTLY with this suite — every test here
    feature-detects via `_validator_source_has()` before asserting."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Template = cls.env["product.template"]
        cls.corner_tmpl = Template.search(
            [("default_code", "=", "SB-CORNER")], limit=1)
        if not cls.corner_tmpl:
            cls.corner_tmpl = Template.create(
                {"name": "Corner Base", "default_code": "SB-CORNER",
                 "list_price": 625.0})
        cls.base_prod = cls.env["product.product"].create(
            {"name": "CM-Motion Test Base", "default_code": "ZZ-CM-MOTION",
             "list_price": 200.0})
        cls.partner = cls.env["res.partner"].create(
            {"name": "Collision Matrix Motion Test"})

    def _make_corner_design(self, room_width_in=200.0, room_depth_in=200.0,
                             back_width_in=24.0, left_width_in=24.0):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Collision matrix motion/filler",
            "partner_id": self.partner.id,
            "room_width_in": room_width_in, "room_depth_in": room_depth_in,
            "room_height_in": 96.0, "state": "draft",
        })
        Line = self.env["southbrook.kitchen.design.line"]
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": back_width_in, "height_in": 34.5, "depth_in": 24.0,
            "wall": "back", "run_seq": 0, "origin": "configurator",
            "layout_key": "cm-motion-back-0",
        })
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": left_width_in, "height_in": 34.5, "depth_in": 24.0,
            "wall": "left", "run_seq": 0, "origin": "configurator",
            "layout_key": "cm-motion-left-0",
        })
        return design

    def test_motion_envelope_collision_flags_intruding_cabinet(self):
        """validator check #10/#12 (MOTION_ENVELOPE_COLLISION) — the M4
        generalisation of the matrix's mechanism-sweep pairs (LeMans arm
        sweep, Magic Corner stroke, lazy-susan rotation) into one
        rule-driven geometric check: a corner rule's clearance_front_mm
        box, extended in front of the resolved corner cell, colliding
        with another same-layer solid cabinet blocks the mechanism.
        """
        Design = self.env["southbrook.kitchen.design"]
        if not _validator_source_has(Design, "MOTION_ENVELOPE_COLLISION"):
            self.skipTest(
                "validator check #10 (MOTION_ENVELOPE_COLLISION) not yet "
                "present")

        self.env["southbrook.placement.rule"].create({
            "name": "CM motion-envelope test rule",
            "product_tmpl_id": self.corner_tmpl.id,
            "corner_type_id": "cm-test-motion-envelope",
            "anchor_class": "junction", "tier": "base", "sequence": 1,
            "payload": {
                "leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
                "min_leg_x_mm": 914.0, "min_leg_z_mm": 914.0,
                "clearance_front_mm": 254.0,   # 10in
            },
        })
        design = self._make_corner_design()
        design.action_auto_arrange()

        corner_line = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_line), 1, corner_line)
        # back-left with no host_leg override presents on the left wall,
        # rotation 90 (_CORNER_RESOLVE_PRESENTATION) — asserted so a
        # future presentation change fails loudly here instead of
        # silently mis-deriving the envelope zone below.
        self.assertEqual(corner_line.rotation_deg, 90)

        # Compute the SAME envelope box the validator computes, via the
        # same public engine helper, so the planted cabinet's position
        # is geometrically guaranteed correct rather than hand-derived.
        cab = {"width_mm": corner_line.width_in * MM,
               "depth_mm": corner_line.depth_in * MM}
        place = {"x": corner_line.x_position_in * MM,
                 "z": corner_line.z_position_in * MM,
                 "rotation_deg": corner_line.rotation_deg}
        ex0, ex1, ez0, ez1 = E.motion_envelope_from_anchor_mm(
            cab, place, 254.0)

        # Plant a base cabinet in the middle half of the envelope box —
        # comfortably inside on both axes, immune to rounding noise.
        plant_x0_mm = ex0 + (ex1 - ex0) * 0.25
        plant_z0_mm = ez0 + (ez1 - ez0) * 0.25
        plant_w_mm = (ex1 - ex0) * 0.5
        plant_d_mm = (ez1 - ez0) * 0.5
        self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": plant_w_mm / MM, "height_in": 34.5,
            "depth_in": plant_d_mm / MM,
            "x_position_in": plant_x0_mm / MM,
            "z_position_in": plant_z0_mm / MM,
            "rotation_deg": 0, "wall": "back", "run_seq": -1,
            "origin": "configurator", "layout_key": "cm-motion-plant",
        })

        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertIn("MOTION_ENVELOPE_COLLISION", codes, issues)

    def test_motion_envelope_excludes_legitimate_adjoining_run_neighbor(self):
        """Task 1 code-review Finding 1, direction (a) — keep the
        false-positive fix: the corner's own immediately-adjoining run
        neighbour (the cabinet the corner's leg reservation made room
        for, flush at zero standoff) must NOT trip
        MOTION_ENVELOPE_COLLISION. Unlike
        test_motion_envelope_collision_flags_intruding_cabinet (whose
        planted cabinet floats with a real standoff gap inside the
        envelope), this needs a SURVIVING same-wall neighbour after
        corner resolution — _make_corner_design's single-cabinet-per-
        wall shape has no survivor (both get consumed by the corner),
        so this uses two back-wall cabinets.
        """
        Design = self.env["southbrook.kitchen.design"]
        if not _validator_source_has(Design, "MOTION_ENVELOPE_COLLISION"):
            self.skipTest(
                "validator check #10 (MOTION_ENVELOPE_COLLISION) not yet "
                "present")

        self.env["southbrook.placement.rule"].create({
            "name": "CM motion-envelope neighbor-exclusion rule",
            "product_tmpl_id": self.corner_tmpl.id,
            "corner_type_id": "cm-test-motion-envelope-neighbor",
            "anchor_class": "junction", "tier": "base", "sequence": 1,
            "payload": {
                "leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
                "min_leg_x_mm": 914.0, "min_leg_z_mm": 914.0,
                "clearance_front_mm": 254.0,   # 10in — well under a 24in bay
            },
        })
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Collision matrix motion — legit neighbor",
            "partner_id": self.partner.id,
            "room_width_in": 200.0, "room_depth_in": 200.0,
            "room_height_in": 96.0, "state": "draft",
        })
        Line = self.env["southbrook.kitchen.design.line"]
        # TWO back-wall cabinets: run_seq 0 is absorbed by the corner
        # (host_wall="back" for back-left), run_seq 1 SURVIVES and
        # becomes the real, engine-placed, flush-against-the-corner
        # neighbour this test exercises.
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "back", "run_seq": 0, "origin": "configurator",
            "layout_key": "cm-neighbor-back-0",
        })
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "back", "run_seq": 1, "origin": "configurator",
            "layout_key": "cm-neighbor-back-1",
        })
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "left", "run_seq": 0, "origin": "configurator",
            "layout_key": "cm-neighbor-left-0",
        })
        design.action_auto_arrange()

        corner_line = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_line), 1, corner_line)
        survivor = design.cabinet_line_ids.filtered(
            lambda l: l.layout_key == "cm-neighbor-back-1")
        self.assertTrue(survivor, "back run_seq=1 must survive corner "
                                   "substitution and be repositioned "
                                   "flush after it")
        self.assertAlmostEqual(survivor.x_position_in,
                                corner_line.x_position_in
                                + corner_line.width_in, delta=0.1)

        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertNotIn("MOTION_ENVELOPE_COLLISION", codes, issues)

    def test_motion_envelope_flags_zero_standoff_intruder_on_wrong_wall(self):
        """Task 1 code-review Finding 1, direction (b) — a candidate at
        ZERO standoff, sitting in the exact same touch pattern a
        legitimate adjoining-run neighbour would (flush at the corner's
        own edge, cross-axis span overlapping), must still fire
        MOTION_ENVELOPE_COLLISION when it does NOT carry the corner's
        facing-axis wall tag — i.e. it is provably not the cabinet the
        corner's leg reservation made room for, so pure touch-geometry
        alone must not exclude it. This is the worst-case violation
        (zero clearance at all) and no other check backstops it (flush
        footprints don't overlap, so #7 FOOTPRINT_COLLISION can't catch
        it either).
        """
        Design = self.env["southbrook.kitchen.design"]
        if not _validator_source_has(Design, "MOTION_ENVELOPE_COLLISION"):
            self.skipTest(
                "validator check #10 (MOTION_ENVELOPE_COLLISION) not yet "
                "present")

        self.env["southbrook.placement.rule"].create({
            "name": "CM motion-envelope zero-standoff rule",
            "product_tmpl_id": self.corner_tmpl.id,
            "corner_type_id": "cm-test-motion-envelope-zero-standoff",
            "anchor_class": "junction", "tier": "base", "sequence": 1,
            "payload": {
                "leg_x_mm": 914.0, "leg_z_mm": 914.0, "height_mm": 876.0,
                "min_leg_x_mm": 914.0, "min_leg_z_mm": 914.0,
                "clearance_front_mm": 254.0,
            },
        })
        # Single cabinet per wall: both back0/left0 are consumed by the
        # corner, so NO real adjoining-run neighbour survives here — any
        # hit below is unambiguously the planted candidate, not a
        # legitimate engine-placed cabinet.
        design = self._make_corner_design()
        design.action_auto_arrange()
        corner_line = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_line), 1, corner_line)
        self.assertEqual(corner_line.rotation_deg, 90)

        cab = {"width_mm": corner_line.width_in * MM,
               "depth_mm": corner_line.depth_in * MM}
        place = {"x": corner_line.x_position_in * MM,
                 "z": corner_line.z_position_in * MM,
                 "rotation_deg": corner_line.rotation_deg}
        fx0, fx1, fz0, fz1 = E.footprint_from_anchor_mm(cab, place)

        # Plant a cabinet FLUSH at the corner's own edge (zero standoff
        # — fx1 is exactly where the envelope starts, the SAME boundary
        # a legitimate back-wall neighbour would touch) with its z-span
        # inside the corner's cross extent — geometrically identical
        # touch pattern to test (a)'s legitimate survivor. Tag it
        # wall="left" (the corner's OWN presentation wall, i.e. NOT the
        # facing-axis wall "back") so only the wall-identity check can
        # tell it apart from a legitimate neighbour.
        plant_w_mm = 200.0
        plant_d_mm = 300.0
        self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": plant_w_mm / MM, "height_in": 34.5,
            "depth_in": plant_d_mm / MM,
            "x_position_in": fx1 / MM, "z_position_in": fz0 / MM,
            "rotation_deg": 0, "wall": "left", "run_seq": -1,
            "origin": "configurator", "layout_key": "cm-zero-standoff-plant",
        })

        issues = design._check_production_ready()
        codes = [i["code"] for i in issues]
        self.assertIn("MOTION_ENVELOPE_COLLISION", codes, issues)

    def test_corner_filler_missing_flags_deleted_derived_filler_line(self):
        """validator check #11/#13 (CORNER_FILLER_MISSING) — a
        blind-corner rule's filler_x_mm demands a real derived filler
        line (M3); if a user deletes it, the validator must warn rather
        than silently ship a gap the BOM no longer covers.
        """
        Design = self.env["southbrook.kitchen.design"]
        if not _validator_source_has(Design, "CORNER_FILLER_MISSING"):
            self.skipTest(
                "validator check #11 (CORNER_FILLER_MISSING) not yet "
                "present")

        self.env["southbrook.placement.rule"].create({
            "name": "CM filler-missing test rule",
            "product_tmpl_id": self.corner_tmpl.id,
            "corner_type_id": "cm-test-filler-missing",
            "anchor_class": "junction", "tier": "base", "sequence": 1,
            "payload": {
                "leg_x_mm": 610.0, "leg_z_mm": 1143.0, "height_mm": 876.0,
                # M3 constraint: min_leg_x must cover leg_x + filler_x.
                "min_leg_x_mm": 686.2, "min_leg_z_mm": 1143.0,
                "filler_x_mm": 76.2, "filler_z_mm": 0.0, "host_leg": "z",
            },
        })
        # Narrow back wall (~40in) so a susan-style rule (45in min leg)
        # can't fit — mirrors the matrix's blind-corner-forcing scenario,
        # even though this test's own rule (sequence=1) already wins the
        # ranking deterministically regardless of room size.
        design = self._make_corner_design(
            room_width_in=40.0, room_depth_in=100.0,
            back_width_in=20.0, left_width_in=30.0)
        design.action_auto_arrange()

        filler_lines = design.cabinet_line_ids.filtered(
            lambda l: (l.layout_key or "").startswith("cornerfill-"))
        if not filler_lines:
            self.skipTest(
                "corner filler design-line emission (M3 ORM writeback) "
                "not yet present")
        filler_lines.unlink()

        issues = design._check_production_ready()
        by_code = {i["code"]: i for i in issues}
        self.assertIn("CORNER_FILLER_MISSING", by_code, issues)
        self.assertEqual(by_code["CORNER_FILLER_MISSING"]["severity"],
                          "warning")


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "collision_matrix")
class TestCollisionMatrixDeferredPairs(TransactionCase):
    """Group C — the 19 collision-matrix pair_ids that cannot be
    expressed by the engine yet (appliance/drawer/handle/mechanism
    modeling: M5+ per docs/research/corner-engine/09-rule-engine-spec.md
    §5). One skipTest per pair_id, hardcoded from the matrix's trailing
    JSON block (read 2026-07-27) minus the 4 pairs Groups A/B cover, so
    the full 23-pair matrix stays visible (skipped, not silently absent)
    in CI test output."""

    def test_deferred_corner_door_vs_door_bifold_hinge(self):
        self.skipTest(
            "pair corner-door-vs-door-bifold-hinge requires hinge "
            "mechanism modeling — deferred to M5+ per "
            "09-rule-engine-spec.md §5")

    def test_deferred_corner_door_vs_drawer_handle(self):
        self.skipTest(
            "pair corner-door-vs-drawer-handle requires handle "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_drawer_front_static_tolerance(self):
        self.skipTest(
            "pair drawer-front-static-tolerance requires drawer "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_handle_projection_knob(self):
        self.skipTest(
            "pair handle-projection-knob requires handle modeling — "
            "deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_handle_projection_bar_pull(self):
        self.skipTest(
            "pair handle-projection-bar-pull requires handle modeling — "
            "deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_blind_pullout_vs_dishwasher(self):
        self.skipTest(
            "pair blind-pullout-vs-dishwasher requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_dishwasher_standing_clearance(self):
        self.skipTest(
            "pair dishwasher-standing-clearance requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_corner_door_vs_tall_pantry(self):
        self.skipTest(
            "pair corner-door-vs-tall-pantry requires door-swing arc "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_upper_corner_door_vs_range_hood(self):
        self.skipTest(
            "pair upper-corner-door-vs-range-hood requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_refrigerator_door_swing_vs_opposite_run(self):
        self.skipTest(
            "pair refrigerator-door-swing-vs-opposite-run requires "
            "appliance modeling — deferred to M5+ per "
            "09-rule-engine-spec.md §5")

    def test_deferred_refrigerator_crisper_drawer_vs_door_angle(self):
        self.skipTest(
            "pair refrigerator-crisper-drawer-vs-door-angle requires "
            "appliance modeling — deferred to M5+ per "
            "09-rule-engine-spec.md §5")

    def test_deferred_refrigerator_vs_corner_wall(self):
        self.skipTest(
            "pair refrigerator-vs-corner-wall requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_lazy_susan_tray_vs_door_close(self):
        self.skipTest(
            "pair lazy-susan-tray-vs-door-close requires mechanism "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_lemans_arm_sweep_vs_door_angle(self):
        self.skipTest(
            "pair lemans-arm-sweep-vs-door-angle requires mechanism "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_lemans_arm_sweep_vs_adjacent_cabinet(self):
        self.skipTest(
            "pair lemans-arm-sweep-vs-adjacent-cabinet requires "
            "mechanism modeling — deferred to M5+ per "
            "09-rule-engine-spec.md §5")

    def test_deferred_magic_corner_vs_cabinet_box(self):
        self.skipTest(
            "pair magic-corner-vs-cabinet-box requires mechanism "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_range_side_clearance(self):
        self.skipTest(
            "pair range-side-clearance requires appliance modeling — "
            "deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_range_vs_wall_cabinet_vertical(self):
        self.skipTest(
            "pair range-vs-wall-cabinet-vertical requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")

    def test_deferred_wall_oven_door_drop_envelope(self):
        self.skipTest(
            "pair wall-oven-door-drop-envelope requires appliance "
            "modeling — deferred to M5+ per 09-rule-engine-spec.md §5")
