# SPDX-License-Identifier: LGPL-3.0-only
"""Remainder-occupancy fix (2026-07-31) — `remainder_in` (models/
kitchen_design.py, computed in `_compute_totals`) used to be a pure
ROOM-WIDE modulo against a single global catalog module width. It read
NO per-line data, so a real multi-wall L/U design with a genuine leftover
gap on one wall could still read 0.00 whenever the room's total width
happened to be evenly divisible by the module width.

This pins the fix's contract:

  1. Once a design has at least one placed base-elevation line
     (`_base_run_eligible_lines`), `remainder_in` is derived from REAL
     per-wall occupancy (`_compute_remainder_from_occupancy_in`):
     project every eligible line's real anchor-convention footprint onto
     its wall's along-axis, merge overlapping/adjacent intervals, and
     sum (wall extent - covered length) across every wall that actually
     carries a base run.

  2. Corner lines carry only ONE presentational `wall` tag
     (kitchen_layout_engine.ROOM_CORNER_PLACEMENT — was
     `_CORNER_RESOLVE_PRESENTATION` before the 2026-07-31 corner-
     orientation fix) even though they physically bridge TWO walls
     (verified live, pre-fix: production design 108, a U-shape, had
     both corners tagged wall='left'/'right' and NEITHER wall='back',
     even though both corners sit in the back wall's run — post-fix,
     design 108's back-left corner is retagged wall='back' by the
     19.0.5.36.1 repair migration, and back-right stays wall='right'
     unchanged; either way NEITHER corner is ever tagged with BOTH walls
     it touches, which is the property this test actually exercises) —
     so a corner's footprint counts toward every wall plane it
     geometrically touches, on top of the wall named by its own field.
     An ordinary (non-corner) line counts ONLY toward the wall named by
     its own `wall` field — this is what keeps a plain, perfectly-
     tessellated single-wall run from spuriously "leaking" leftover onto
     the side walls its end cabinets happen to touch. This test's own
     fixtures below use their own directly-authored (wall, rotation)
     values, independent of which specific ROOM_CORNER_PLACEMENT
     rotation the live engine currently chooses — the remainder math
     under test reads real footprints (footprint_from_anchor_mm), and a
     square corner's anchor-convention AABB is identical for any of the
     four ALLOWED_ROTATIONS (see that module's own comment), so these
     numbers hold regardless.

  3. With NO placed base-elevation lines, `remainder_in` keeps the
     LEGACY room-wide modulo estimate — now with the n==0 guard
     controllers/main.py's /layout route has always had (a room
     narrower than one module has no run to complete, so it must not
     report the whole room width back as "leftover"; live evidence:
     production design 104, room_width_in=12, read remainder_in=12.00
     before this fix).

test_filler_remainder_gate.py (the controller-side /layout gate — an
entirely separate remainder computation living in controllers/main.py)
is untouched by this fix and keeps passing unchanged.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation")
class TestRemainderOccupancy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "T-Remainder Occupancy Customer"})
        cls.prod = cls.env["product.product"].create(
            {"name": "T-Remainder Generic Cabinet",
             "default_code": "ZZ-REMAINDER-GENERIC",
             "list_price": 200.0})

        # Test-isolation (matches test_filler_remainder_gate.py's own
        # setUpClass fix): `_find_cabinet_product("base", ...)` — the
        # LEGACY (no-placed-lines) fallback path only — has no `order=`
        # and no way to target a specific product; on a DB seeded with a
        # real catalog (demo_cabinets.xml ships B24/DB24/SB30, widths
        # 24/24/30) it could pick any of them, silently invalidating the
        # exact module_w=24 assumption the legacy-path assertions below
        # depend on. Neutralize every OTHER candidate so ours (created
        # below) is the only one left standing — scoped to this class's
        # own transaction only, never persisted.
        competitors = cls.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("product_tmpl_id.southbrook_cabinet_type", "=", "base"),
            ("sale_ok", "=", True),
        ])
        if competitors:
            competitors.write({"sale_ok": False})

        cls.legacy_base_tmpl = cls.env["product.template"].create({
            "name": "T-Remainder Legacy Base 24", "type": "consu",
            "sale_ok": True, "list_price": 180.0,
        })
        cls.legacy_base_tmpl.southbrook_is_cabinet = True
        cls.legacy_base_tmpl.southbrook_cabinet_type = "base"
        cls.legacy_base_tmpl.southbrook_width_in = 24.0

    def _design(self, **vals):
        base_vals = {
            "name": "T-Remainder design", "partner_id": self.partner.id,
            "room_width_in": 96.0, "room_depth_in": 96.0,
            "room_height_in": 96.0,
        }
        base_vals.update(vals)
        return self.env["southbrook.kitchen.design"].create(base_vals)

    def _line(self, design, **vals):
        line_vals = {
            "design_id": design.id, "product_id": self.prod.id,
            "quantity": 1, "price_unit": self.prod.list_price,
            "height_in": 34.5, "origin": "configurator",
        }
        line_vals.update(vals)
        return self.env["southbrook.kitchen.design.line"].create(line_vals)

    # ── AC1 — production design 107 (L-shape): real leftover, not 0.00 ──
    def test_l_shape_back_and_left_wall_leftover(self):
        """Room 120x96. Back wall: back-left corner (0->36), base
        (36->60), appliance (60->90), base (90->114) — a real 6" gap out
        of 120". Left wall: the SAME corner (0->36, its footprint
        bridges both legs), base (36->60), base (60->84) — a real 12"
        gap out of 96". Pre-fix this design read remainder_in=0.00
        (120 % 24 == 0, hiding the real gap entirely).
        """
        design = self._design(room_width_in=120.0, room_depth_in=96.0)

        # Back-left corner — directly-authored fixture pose (a square
        # corner's AABB is footprint-identical for any of the four
        # ALLOWED_ROTATIONS — see kitchen_layout_engine.
        # ROOM_CORNER_PLACEMENT's module comment — so this legacy
        # wall='left'/rotation=90 pose still exercises the exact same
        # geometry the live engine now emits as wall='back'/rotation=0):
        # anchor x=0,z=36, w=36,d=36, rot=90 -> AABB x:[0,36] z:[0,36],
        # touching BOTH the back wall's plane (z0=0) and the left wall's
        # plane (x0=0).
        self._line(design, cabinet_type="corner", wall="left",
                   rotation_deg=90, x_position_in=0, z_position_in=36,
                   width_in=36, depth_in=36)

        # Back wall run: base 36->60, appliance 60->90, base 90->114.
        self._line(design, cabinet_type="base", wall="back",
                   rotation_deg=0, x_position_in=36, z_position_in=0,
                   width_in=24, depth_in=24)
        self._line(design, cabinet_type="appliance", wall="back",
                   rotation_deg=0, x_position_in=60, z_position_in=0,
                   width_in=30, depth_in=24)
        self._line(design, cabinet_type="base", wall="back",
                   rotation_deg=0, x_position_in=90, z_position_in=0,
                   width_in=24, depth_in=24)

        # Left wall run (beyond the shared corner): base 36->60,
        # base 60->84.
        self._line(design, cabinet_type="base", wall="left",
                   rotation_deg=90, x_position_in=0, z_position_in=60,
                   width_in=24, depth_in=24)
        self._line(design, cabinet_type="base", wall="left",
                   rotation_deg=90, x_position_in=0, z_position_in=84,
                   width_in=24, depth_in=24)

        design.invalidate_recordset()
        self.assertAlmostEqual(design.remainder_in, 18.0, places=2,
                                msg="6\" back-wall gap + 12\" left-wall "
                                    "gap = 18\" real total leftover")

    # ── AC2 — production design 108 (U-shape): perfectly tessellated ────
    def test_u_shape_three_wall_leftover(self):
        """Room 120x96, two corners. Back wall — left-corner (0->36),
        base (36->60), base (60->84), right-corner (84->120) — perfectly
        tessellates the full 120" width: true leftover 0. Left wall —
        left-corner (0->36), sink (36->60), nothing beyond: 36" leftover
        out of 96". Right wall — right-corner (0->36), range (36->66),
        base (66->90): 6" leftover out of 96". Total 42".

        Grouping strictly by the `wall` field (both corners are tagged
        wall='left'/'right', never wall='back') would instead read the
        back wall as if 72" were empty — this pins that it does not.
        """
        design = self._design(room_width_in=120.0, room_depth_in=96.0)

        # Back-left corner: wall='left' tag, footprint touches back+left.
        self._line(design, cabinet_type="corner", wall="left",
                   rotation_deg=90, x_position_in=0, z_position_in=36,
                   width_in=36, depth_in=36)
        # Back-right corner: wall='right' tag, footprint touches
        # back+right (anchor x=120,z=0,w=36,d=36,rot=270 ->
        # AABB x:[84,120] z:[0,36]).
        self._line(design, cabinet_type="corner", wall="right",
                   rotation_deg=270, x_position_in=120, z_position_in=0,
                   width_in=36, depth_in=36)

        # Back wall run (between the two corners): base 36->60, base
        # 60->84.
        self._line(design, cabinet_type="base", wall="back",
                   rotation_deg=0, x_position_in=36, z_position_in=0,
                   width_in=24, depth_in=24)
        self._line(design, cabinet_type="base", wall="back",
                   rotation_deg=0, x_position_in=60, z_position_in=0,
                   width_in=24, depth_in=24)

        # Left wall run (beyond the shared corner): sink 36->60.
        self._line(design, cabinet_type="base", wall="left",
                   rotation_deg=90, x_position_in=0, z_position_in=60,
                   width_in=24, depth_in=24)

        # Right wall run (beyond the shared corner): range 36->66, base
        # 66->90. rot270's z-extent is [z, z+width_in] (z is the START,
        # unlike rot90 where z is the END) — see footprint_from_anchor_mm.
        self._line(design, cabinet_type="appliance", wall="right",
                   rotation_deg=270, x_position_in=120, z_position_in=36,
                   width_in=30, depth_in=24)
        self._line(design, cabinet_type="base", wall="right",
                   rotation_deg=270, x_position_in=120, z_position_in=66,
                   width_in=24, depth_in=24)

        design.invalidate_recordset()
        self.assertAlmostEqual(design.remainder_in, 42.0, places=2,
                                msg="back 0\" + left 36\" + right 6\" = "
                                    "42\" total leftover")

    # ── AC3 — a uniform single-wall run still yields 0.0 ─────────────
    def test_uniform_single_wall_run_yields_zero(self):
        """5 x 24" base cabinets, edge-to-edge along the back wall of a
        120" room: a perfect tessellation, real leftover 0. None of
        these lines is a corner, so the first/last cabinet's edges
        touching x=0 / x=120 must NOT leak a phantom leftover onto the
        left/right walls (which carry no run at all here) — only an
        actual corner line bridges two walls.
        """
        design = self._design(room_width_in=120.0, room_depth_in=96.0)
        for i in range(5):
            self._line(design, cabinet_type="base", wall="back",
                       rotation_deg=0, x_position_in=i * 24,
                       z_position_in=0, width_in=24, depth_in=24)
        design.invalidate_recordset()
        self.assertEqual(design.remainder_in, 0.0)

    # ── AC4 — no placed lines: legacy modulo fallback, byte-for-byte ────
    def test_empty_design_keeps_legacy_modulo_result(self):
        """No cabinet_line_ids at all -> `_base_run_eligible_lines()` is
        empty -> the legacy room-wide modulo estimate (room_width_in vs.
        the resolved base module width) applies unchanged. room=64,
        module_w=24 -> n=2, remainder=16 (matches
        test_filler_remainder_gate.py's own run-length identity for the
        controller-side /layout route, same formula)."""
        design = self._design(room_width_in=64.0, room_depth_in=24.0)
        self.assertFalse(design.cabinet_line_ids)
        self.assertAlmostEqual(design.remainder_in, 16.0, places=2)

    def test_undersized_empty_room_yields_zero_not_full_width(self):
        """The n==0 guard (2026-07-31 fix): room_width_in=12 < the 24"
        module -> n=0 -> remainder_in must be 0.0, not the whole 12"
        room reported back as "leftover". Before this fix the MODEL
        field had no such guard (only controllers/main.py's /layout
        route did) — live evidence: production design 104,
        room_width_in=12, read remainder_in=12.00."""
        design = self._design(room_width_in=12.0, room_depth_in=24.0)
        self.assertFalse(design.cabinet_line_ids)
        self.assertEqual(design.remainder_in, 0.0)
