# SPDX-License-Identifier: LGPL-3.0-only
"""Regression pins for the L-shaped-kitchen multi-defect blocker
(v19.0.5.20.0): interpenetrating cabinets + no corner cabinets on an
L-shaped (2-wall) design.

  C1 — `zone`'s truthy `default="base_run"` shadowed the "backfill on
       empty" compute, so every wall/tall/filler/panel cabinet silently
       persisted zone='base_run' forever. Fixed by removing the default
       (see models/kitchen_design.py's zone field + migrations/
       19.0.5.20.0/post-migration.py for the existing-row repair).
  C3 — `_place_lines_on_wall` fed existing DERIVED corner lines into
       layout() as ordinary run members with no wall_start_offsets, so
       a wall that already had a corner cabinet re-flowed its run from
       the room origin straight into the reserved corner cell.
  C4 — the pure engine emits along-axis-CENTRED poses; the persisted/
       rendered convention is a back-left-bottom-corner ANCHOR. Every
       engine-written pose needed `kitchen_layout_engine.anchor_pose_mm`
       applied before the mm->in conversion.
  C8 — corner lines inserted by action_auto_arrange omitted `zone`
       entirely, so both the base-layer AND wall-layer corner cabinets
       persisted the field's old truthy default.

Follows the fixture/helper style of test_auto_arrange_service.py.
"""
from odoo.tests.common import TransactionCase

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E


class TestZoneComputeBackfill(TransactionCase):
    """C1 — the zone compute must actually run on create()."""

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Zone Test"})
        self.wall_prod = self.env["product.product"].create(
            {"name": "Zone Test Wall Cab", "default_code": "ZZ-ZONE-WALL",
             "list_price": 150.0})
        self.design = self.env["southbrook.kitchen.design"].create({
            "name": "Zone compute test", "partner_id": self.partner.id,
        })

    def _line(self, **extra):
        vals = {
            "design_id": self.design.id, "product_id": self.wall_prod.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "origin": "configurator", "layout_key": "zone-test-1",
        }
        vals.update(extra)
        return self.env["southbrook.kitchen.design.line"].create(vals)

    def test_wall_cabinet_with_no_zone_backfills_wall(self):
        """No `zone` key in vals at all -> the compute must fire and
        fill 'wall' from cabinet_type='wall'. Pre-fix this silently
        stayed 'base_run' (the field's truthy default shadowed the
        compute's "only backfill on empty" guard)."""
        line = self._line()
        self.assertEqual(line.zone, "wall")

    def test_explicit_zone_override_is_preserved(self):
        """An explicit zone in vals (e.g. an island-mounted upper) must
        survive create() untouched — the compute must not clobber a
        user-supplied value."""
        line = self._line(zone="island")
        self.assertEqual(line.zone, "island")


class TestLShapeAutoArrange(TransactionCase):
    """C1 + C3 + C4 + C8 end-to-end: a real 2-wall (L-shaped) design
    must auto-arrange into exactly one base corner + one wall corner,
    with zero same-layer footprint overlaps and everything inside the
    room — the exact failure this whole defect chain caused
    (interpenetrating cabinets, no corner cabinets, LayoutCapacityExceeded
    on a perfectly reasonable L kitchen)."""

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
            {"name": "L-Test Base 24", "default_code": "ZZ-LTEST-B24",
             "list_price": 200.0})
        cls.wall_prod = cls.env["product.product"].create(
            {"name": "L-Test Wall 24", "default_code": "ZZ-LTEST-W24",
             "list_price": 150.0})
        cls.partner = cls.env["res.partner"].create({"name": "L-Shape Test"})

    def _make_l_design(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "L-shape test", "partner_id": self.partner.id,
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
                        "layout_key": "l-%s-%s-%d" % (wall, cabinet_type, i),
                    })
                    seq += 10
        return design

    def test_auto_arrange_resolves_one_corner_per_layer(self):
        design = self._make_l_design()
        design.action_auto_arrange()
        corners = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corners), 2, corners.mapped("layout_key"))
        zones = sorted(corners.mapped("zone"))
        self.assertEqual(zones, ["base_run", "wall"])

    def test_auto_arrange_produces_no_same_layer_overlaps_in_room(self):
        design = self._make_l_design()
        design.action_auto_arrange()
        MM = 25.4
        room = {"width_mm": design.room_width_in * MM,
                "depth_mm": design.room_depth_in * MM}
        by_layer = {}
        for line in design.cabinet_line_ids:
            if line.cabinet_type in ("filler", "panel"):
                continue
            # A corner line's layer comes from its zone (C8): the upper
            # corner legitimately shares the base corner's x/z cell at a
            # different elevation — same classification the production
            # validator (check #7) uses.
            if line.cabinet_type == "wall" or (
                    line.cabinet_type == "corner" and line.zone == "wall"):
                layer = "wall"
            else:
                layer = "base"
            cab = {"width_mm": (line.width_in or 0) * MM,
                   "depth_mm": (line.depth_in or 0) * MM}
            place = {"x": (line.x_position_in or 0) * MM,
                     "z": (line.z_position_in or 0) * MM,
                     "rotation_deg": line.rotation_deg or 0}
            fp = E.footprint_from_anchor_mm(cab, place)
            by_layer.setdefault(layer, []).append((line, fp))
            # every footprint must be inside the room (small epsilon)
            x0, x1, z0, z1 = fp
            self.assertGreaterEqual(x0, -1.0, line.layout_key)
            self.assertGreaterEqual(z0, -1.0, line.layout_key)
            self.assertLessEqual(x1, room["width_mm"] + 1.0, line.layout_key)
            self.assertLessEqual(z1, room["depth_mm"] + 1.0, line.layout_key)
        for layer, items in by_layer.items():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    line_a, fp_a = items[i]
                    line_b, fp_b = items[j]
                    self.assertFalse(
                        E.footprints_overlap(fp_a, fp_b),
                        "%s layer: %s overlaps %s (%r vs %r)" % (
                            layer, line_a.layout_key, line_b.layout_key,
                            fp_a, fp_b))


class TestPlaceLinesOnWallRespectsExistingCorner(TransactionCase):
    """C3 — a newly-added cabinet on a wall that already has a DERIVED
    corner cabinet must not be placed on top of that corner's reserved
    cell."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "Corner Offset Test"})
        cls.corner_prod = cls.env["product.product"].create(
            {"name": "Corner Offset Test Corner", "default_code": "ZZ-COT-CORNER",
             "list_price": 625.0})
        cls.base_prod = cls.env["product.product"].create(
            {"name": "Corner Offset Test Base", "default_code": "ZZ-COT-BASE",
             "list_price": 200.0})

    def test_new_left_wall_base_line_clears_the_corner_cell(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "Corner offset test", "partner_id": self.partner.id,
            "room_width_in": 120.0, "room_depth_in": 120.0,
            "room_height_in": 96.0,
        })
        Line = self.env["southbrook.kitchen.design.line"]
        # A pre-existing DERIVED base-layer corner cabinet at back-left,
        # exactly as action_auto_arrange would have created it (C8: zone
        # set; layout_key matches the "corner-<design_id>-<name>-<layer>"
        # convention _place_lines_on_wall's C3 parser expects). Corner-
        # orientation fix (2026-07-31): back-left's own presentation is
        # wall="back"/rotation_deg=0 (kitchen_layout_engine.
        # ROOM_CORNER_PLACEMENT) — this reservation logic doesn't consult
        # `wall`/`rotation_deg` off THIS field to decide which walls to
        # reserve (that comes from the corner name in `layout_key` via
        # `_CORNER_SPECS`, unaffected by this fix), but the fixture is
        # kept in sync with the real engine output anyway so it isn't a
        # misleading "impossible" (wall, rotation) pairing.
        Line.create({
            "design_id": design.id, "product_id": self.corner_prod.id,
            "quantity": 1, "price_unit": 625.0, "cabinet_type": "corner",
            "zone": "base_run", "width_in": 36.0, "height_in": 34.5,
            "depth_in": 36.0, "wall": "back", "rotation_deg": 0,
            "run_seq": -1,
            "pinned": False, "origin": "configurator",
            "layout_key": "corner-%s-back-left-base" % design.id,
            "layout_role": "derived",
        })
        # A new left-wall base cabinet the user just added.
        new_line = Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "wall": "left", "run_seq": 0, "origin": "configurator",
            "layout_key": "corner-offset-new-base",
        })
        design._place_lines_on_wall(new_line)

        MM = 25.4
        corner_width_in = 36.0
        # The new cabinet must clear the corner cell edge (36in along
        # the left wall's Z run), not overlap it.
        self.assertGreaterEqual(new_line.z_position_in, corner_width_in - 0.5)

        # Belt-and-braces: the actual AABBs (anchor convention) must not
        # overlap either.
        corner_line = design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner")
        self.assertEqual(len(corner_line), 1)
        corner_cab = {"width_mm": corner_line.width_in * MM,
                     "depth_mm": corner_line.depth_in * MM}
        corner_place = {"x": corner_line.x_position_in * MM,
                        "z": corner_line.z_position_in * MM,
                        "rotation_deg": corner_line.rotation_deg or 0}
        new_cab = {"width_mm": new_line.width_in * MM,
                  "depth_mm": new_line.depth_in * MM}
        new_place = {"x": new_line.x_position_in * MM,
                    "z": new_line.z_position_in * MM,
                    "rotation_deg": new_line.rotation_deg or 0}
        fp_corner = E.footprint_from_anchor_mm(corner_cab, corner_place)
        fp_new = E.footprint_from_anchor_mm(new_cab, new_place)
        self.assertFalse(E.footprints_overlap(fp_corner, fp_new))
