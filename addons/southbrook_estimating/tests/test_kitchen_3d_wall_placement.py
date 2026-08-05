# SPDX-License-Identifier: LGPL-3.0-only
"""C-2 / H-10 (2026-08-05) — the 3D Kitchen Preview must render the
kitchen that was designed.

get_kitchen_3d_payload used to place EVERY cabinet from the flat
_ZONE_LAYOUT X-cursor and NEVER read order_line.wall_id (grep confirmed
zero references) — so a genuine 3-wall U-shape room collapsed onto two
straight runs sharing one X axis. This covers the fix: lines grouped by
wall_id place at right angles per the wall's room role (back/left/
right/front), and a line with no wall_id — or an unresolvable one —
still falls back to the original zone-cursor behaviour byte-for-byte,
so older orders with no Room-First wall data don't regress.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook", "kitchen_3d_payload")
class TestKitchen3dWallPlacement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Wall Placement Test Customer"})

    def _variant(self):
        # southbrook_estimating.base_1dr is create_variant='dynamic'
        # (Q6) — its product_variant_ids is an EMPTY recordset until a
        # config-session materialises one (see test_room_wall_
        # assignment.py's own comment on the same trap). A plain,
        # directly-created product whose default_code is the EXACT
        # "SB-BASE-1DR" key resolves through Session._resolve_template_
        # sku the same way (tmpl.default_code truthy check) without
        # depending on OCA config-session state.
        return self.env["product.product"].create({
            "name": "Wall Placement Test Cabinet",
            "default_code": "SB-BASE-1DR",
            "type": "consu",
            "list_price": 500.0,
        })

    @staticmethod
    def _line_panels(payload, line_id):
        prefix = "L%d_" % line_id
        return [p for p in payload["panels"] if p["name"].startswith(prefix)]

    @staticmethod
    def _xz_span(panels):
        xs = [p["pos"]["x"] for p in panels]
        zs = [p["pos"]["z"] for p in panels]
        return min(xs), max(xs), min(zs), max(zs)

    def test_three_wall_u_shape_places_cabinets_on_their_own_walls(self):
        variant = self._variant()
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id})
        room = self.env["southbrook.room"].create({
            "name": "U-Shape Kitchen", "order_id": order.id,
            "layout_shape": "u_shape",
            "wall_ids": [
                (0, 0, {"name": "Wall A", "length_mm": 2000,
                        "wall_order": 10}),
                (0, 0, {"name": "Wall B", "length_mm": 1500,
                        "wall_order": 20}),
                (0, 0, {"name": "Wall C", "length_mm": 1500,
                        "wall_order": 30}),
            ],
        })
        wall_back, wall_left, wall_right = room.wall_ids.sorted(
            key=lambda w: (w.wall_order, w.id))

        def _mk_line(wall):
            return self.env["sale.order.line"].create({
                "order_id": order.id, "product_id": variant.id,
                "product_uom_qty": 1.0,
                "name": "Test cabinet on %s" % wall.name,
                "wall_id": wall.id, "position_from_left_mm": 0,
            })

        line_back = _mk_line(wall_back)
        line_left = _mk_line(wall_left)
        line_right = _mk_line(wall_right)

        payload = order.get_kitchen_3d_payload()
        self.assertTrue(payload.get("panels"))

        panels_back = self._line_panels(payload, line_back.id)
        panels_left = self._line_panels(payload, line_left.id)
        panels_right = self._line_panels(payload, line_right.id)
        self.assertTrue(panels_back, "back-wall line must still render")
        self.assertTrue(panels_left, "left-wall line must still render")
        self.assertTrue(panels_right, "right-wall line must still render")

        bx0, bx1, bz0, bz1 = self._xz_span(panels_back)
        lx0, lx1, lz0, lz1 = self._xz_span(panels_left)
        rx0, rx1, rz0, rz1 = self._xz_span(panels_right)

        # Back wall cabinet: back against Z=0, carcass extends toward
        # -Z (the door face itself sits a few mm PROUD of the Z=0
        # carcass-front plane — DOOR_TH/2 + DOOR_REVEAL — so bz1 is
        # small-positive, not exactly 0).
        self.assertLess(bz1, 20)
        self.assertLess(bz0, bz1)

        # Left wall cabinet: touches X=0 (the wall), extends INTO the
        # room along +X. The closest panel CENTER to the wall is the
        # back panel, whose own center sits BACK_TH/2 (~3.2mm) proud of
        # the true wall plane — so lx0 is small-positive, not exactly 0.
        self.assertLess(lx0, 10)
        self.assertGreaterEqual(lx0, 0)
        self.assertGreater(lx1, lx0)

        # Right wall cabinet: touches X=room_width (Wall A's own
        # length, 2000mm), extends into the room along -X from there —
        # same BACK_TH/2 margin, mirrored.
        self.assertGreater(rx1, 1990)
        self.assertLessEqual(rx1, 2000)
        self.assertLess(rx0, rx1)

        # The defect this closes: before the fix, every cabinet shared
        # ONE flat X-cursor regardless of wall_id — left and right ran
        # collapsed onto the same strip as the back run. Now each
        # wall's cabinet occupies a genuinely distinct X span.
        self.assertNotEqual(
            (round(lx0), round(lx1)), (round(rx0), round(rx1)),
            "left and right wall cabinets must not collapse onto the "
            "same X span")
        self.assertNotEqual(
            (round(lx0), round(lx1)), (round(bx0), round(bx1)),
            "left wall cabinet must not collapse onto the back run's "
            "X span")

    def test_no_wall_id_falls_back_to_zone_cursor(self):
        variant = self._variant()
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": variant.id, "product_uom_qty": 1,
                "zone": "base_run",
            })],
        })
        line = order.order_line[:1]
        self.assertFalse(line.wall_id)
        payload = order.get_kitchen_3d_payload()
        panels = self._line_panels(payload, line.id)
        self.assertTrue(panels)
        _x0, _x1, z0, z1 = self._xz_span(panels)
        # Original _ZONE_LAYOUT base_run behaviour is untouched by this
        # fix: z_offset=0, carcass spans [-depth, 0] (door face sits a
        # few mm proud of 0 — DOOR_TH/2 + DOOR_REVEAL — so z1 is
        # small-positive, not exactly 0).
        self.assertLess(z1, 20)
        self.assertLess(z0, 0)

    def test_unresolvable_wall_role_falls_back_to_zone_cursor(self):
        """A room's 5th+ wall gets no role (only 4 roles exist). A
        line placed on it must not crash or silently vanish — it
        degrades to zone-cursor placement like a line with no
        wall_id at all."""
        variant = self._variant()
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id})
        room = self.env["southbrook.room"].create({
            "name": "Five-Wall Room", "order_id": order.id,
            "layout_shape": "custom",
            "wall_ids": [
                (0, 0, {"name": "Wall A", "length_mm": 1000,
                        "wall_order": n})
                for n in (10, 20, 30, 40, 50)
            ],
        })
        fifth_wall = room.wall_ids.sorted(
            key=lambda w: (w.wall_order, w.id))[-1]
        line = self.env["sale.order.line"].create({
            "order_id": order.id, "product_id": variant.id,
            "product_uom_qty": 1.0, "name": "Test cabinet on 5th wall",
            "wall_id": fifth_wall.id, "position_from_left_mm": 0,
        })
        payload = order.get_kitchen_3d_payload()
        panels = self._line_panels(payload, line.id)
        self.assertTrue(
            panels, "an unresolvable wall role must fall back to zone "
            "placement, not silently drop the line")
