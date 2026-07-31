# SPDX-License-Identifier: LGPL-3.0-only
"""Task 14, corner-remediation (2026-07-30, SBK-026) — "Wall-cabinet
mount height honours the selector".

Audit finding: `kitchen_layout_engine.py`'s zone table hardcodes the
"wall" zone's y_floor at 1400mm (55.12in) — a real 3D-print constant
kept as the module's OFFLINE-TEST fallback and as `sale.order.
_ZONE_LAYOUT`'s single class-level source of truth for every OTHER
zone. The client's "Wall Cab Top" selector (fixed_gap / to_ceiling /
to_soffit — kitchen_design.py's `wall_cab_top_alignment` +
`soffit_height_in` fields) already existed, but neither
`action_auto_arrange` nor `_place_lines_on_wall` ever consulted it —
both fed the engine `zone_layout=SaleOrder._ZONE_LAYOUT` verbatim, so
EVERY wall cabinet persisted at 1400mm/55.12in on every regenerate,
regardless of the selector.

Fix (caller-side only — kitchen_layout_engine.py is unmodified, and
`sale.order._ZONE_LAYOUT["wall"]` stays 1400mm as the documented
fallback for callers with no Wall Cabinet Top setting to honour):
`southbrook.kitchen.design._wall_cab_top_geometry()` resolves the
selector into an actual elevation, and `_wall_cab_zone_layout()`
returns a COPY of `sale.order._ZONE_LAYOUT` with only the "wall"
entry's y_floor replaced — fed into the engine at both call sites.

These tests pin the three selector states end-to-end through the
REAL persisted arrange path (`action_auto_arrange` /
`_place_lines_on_wall`), the gating rule for `to_soffit`, the new
`counter_thickness_in` setting, the "existing saved designs keep
their stored Y" no-migration guarantee, and that the fix does not
touch run-axis (X/Z) placement math at all (Y is elevation only).
"""
from odoo.tests.common import TransactionCase

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E


class TestWallCabTopSelector(TransactionCase):

    def setUp(self):
        super().setUp()
        self.base_prod = self.env["product.product"].create(
            {"name": "Test Base 24", "default_code": "T14-BASE-24",
             "list_price": 200.0})
        self.wall_prod = self.env["product.product"].create(
            {"name": "Test Wall 24", "default_code": "T14-WALL-24",
             "list_price": 150.0})
        self.partner = self.env["res.partner"].create({"name": "T14 Test"})

    def _make_design(self, room_h=96.0, room_w=96.0, room_d=96.0, **vals):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T14 test", "partner_id": self.partner.id,
            "room_width_in": room_w, "room_depth_in": room_d,
            "room_height_in": room_h, "state": "draft",
            **vals,
        })
        Line = self.env["southbrook.kitchen.design.line"]
        Line.create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 34.5, "depth_in": 24.0,
            "sequence": 10, "origin": "configurator", "layout_key": "t14-base",
        })
        Line.create({
            "design_id": design.id, "product_id": self.wall_prod.id,
            "quantity": 1, "price_unit": 150.0, "cabinet_type": "wall",
            "width_in": 24.0, "height_in": 30.0, "depth_in": 12.0,
            "sequence": 20, "origin": "configurator", "layout_key": "t14-wall",
        })
        return design

    def _wall_line(self, design):
        return design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "wall")

    # ── AC1: 96in ceiling / 34.5 carcass / 1.5 top / 18 gap -> Y == 54.00 ──

    def test_fixed_gap_default_resolves_to_54in_on_fresh_arrange(self):
        design = self._make_design()
        self.assertEqual(design.wall_cab_top_alignment, "fixed_gap")
        self.assertEqual(design.counter_thickness_in, 1.5)
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertEqual(len(wl), 1)
        self.assertAlmostEqual(wl.y_position_in, 54.0, places=2)

    def test_no_1400_or_5512_pin_survives_a_fixed_gap_regenerate(self):
        """The literal defect: before this fix EVERY wall cabinet landed
        at 1400mm/55.12in on regenerate, regardless of the selector."""
        design = self._make_design()
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertNotAlmostEqual(wl.y_position_in, 55.12, places=1)
        self.assertNotAlmostEqual(wl.y_position_in * 25.4, 1400.0, places=0)

    # ── AC2: to_ceiling -> Y == ceiling - cab_h (unchanged formula) ────────

    def test_to_ceiling_resolves_to_ceiling_minus_wall_height(self):
        design = self._make_design(wall_cab_top_alignment="to_ceiling")
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertAlmostEqual(wl.y_position_in, 96.0 - 30.0, places=2)

    def test_to_ceiling_tracks_a_taller_room(self):
        design = self._make_design(room_h=108.0,
                                    wall_cab_top_alignment="to_ceiling")
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertAlmostEqual(wl.y_position_in, 108.0 - 30.0, places=2)

    # ── AC3: to_soffit gated, then resolves once set ───────────────────────

    def test_to_soffit_gated_falls_back_to_fixed_gap_while_unset(self):
        design = self._make_design(wall_cab_top_alignment="to_soffit")
        self.assertEqual(design.soffit_height_in, 0.0,
                          "new designs default to an UNSET (0) soffit height")
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertAlmostEqual(wl.y_position_in, 54.0, places=2,
                                msg="gated to_soffit must resolve as fixed_gap, "
                                    "not collapse toward Y=0")

    def test_to_soffit_resolves_once_soffit_height_is_set(self):
        design = self._make_design(wall_cab_top_alignment="to_soffit",
                                    soffit_height_in=90.0)
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertAlmostEqual(wl.y_position_in, 90.0 - 30.0, places=2)

    def test_three_states_are_visibly_different(self):
        fixed = self._make_design()
        fixed.action_auto_arrange()
        ceiling = self._make_design(wall_cab_top_alignment="to_ceiling")
        ceiling.action_auto_arrange()
        soffit = self._make_design(wall_cab_top_alignment="to_soffit",
                                    soffit_height_in=90.0)
        soffit.action_auto_arrange()
        ys = {round(self._wall_line(d).y_position_in, 2)
              for d in (fixed, ceiling, soffit)}
        self.assertEqual(len(ys), 3, "fixed_gap/to_ceiling/to_soffit(90) "
                                      "must resolve to 3 distinct heights, "
                                      "got %s" % ys)
        self.assertEqual(ys, {54.0, 66.0, 60.0})

    # ── item 1: counter_thickness_in moves the fixed_gap number ────────────

    def test_counter_thickness_in_moves_the_fixed_gap_number(self):
        design = self._make_design(counter_thickness_in=2.5)
        design.action_auto_arrange()
        wl = self._wall_line(design)
        # 34.5 + 2.5 + 18 = 55.0
        self.assertAlmostEqual(wl.y_position_in, 55.0, places=2)

    # ── the SAME geometry feeds _place_lines_on_wall (room-resize path) ────

    def test_place_lines_on_wall_honours_the_same_geometry(self):
        design = self._make_design(wall_cab_top_alignment="to_ceiling")
        design.action_auto_arrange()   # establishes wall assignment/run_seq
        wl = self._wall_line(design)
        result = design._place_lines_on_wall(wl)
        self.assertIn(wl.id, result)
        self.assertAlmostEqual(result[wl.id]["y_position_in"], 66.0, places=2)

    # ── item 5: existing saved designs keep their stored Y ─────────────────

    def test_existing_saved_position_is_not_migrated_by_changing_the_setting(self):
        design = self._make_design()
        design.action_auto_arrange()
        wl = self._wall_line(design)
        self.assertAlmostEqual(wl.y_position_in, 54.0, places=2)
        # Flip the selector WITHOUT re-running arrange — the stored Y must
        # not move on its own (no write-time recompute, no compute-store
        # side effect on the line's own y_position_in field).
        design.wall_cab_top_alignment = "to_ceiling"
        self.assertAlmostEqual(wl.y_position_in, 54.0, places=2,
                                msg="changing the setting must not, by "
                                    "itself, reposition an already-arranged "
                                    "cabinet — only the NEXT regenerate does")
        # The next explicit regenerate DOES apply the new setting.
        design.action_auto_arrange()
        self.assertAlmostEqual(wl.y_position_in, 66.0, places=2)

    # ── engine off-limits: zone_layout override doesn't touch the fallback ──

    def test_sale_order_zone_layout_class_constant_untouched(self):
        """kitchen_layout_engine.py and sale.order._ZONE_LAYOUT stay at
        1400mm as the documented fallback for callers with no Wall
        Cabinet Top setting (e.g. a bare engine unit test, or
        sale.order's own line-based 3D payload)."""
        self.assertEqual(
            self.env["sale.order"]._ZONE_LAYOUT["wall"], ("wall", 1400, 0))
        self.assertEqual(E._DEFAULT_ZONE_LAYOUT["wall"], ("wall", 1400, 0))

    def test_wall_cab_zone_layout_only_overrides_the_wall_entry(self):
        design = self._make_design(wall_cab_top_alignment="to_ceiling")
        zl = design._wall_cab_zone_layout()
        SaleOrder = self.env["sale.order"]
        for zone in ("base_run", "tall", "island", "accessory", "other"):
            self.assertEqual(zl[zone], SaleOrder._ZONE_LAYOUT[zone])
        self.assertNotEqual(zl["wall"], SaleOrder._ZONE_LAYOUT["wall"])
        self.assertEqual(zl["wall"][0], SaleOrder._ZONE_LAYOUT["wall"][0])
        self.assertEqual(zl["wall"][2], SaleOrder._ZONE_LAYOUT["wall"][2])

    # ── invariant: Y is elevation only — run-axis (X) math is unaffected ───

    def test_run_axis_x_positions_unaffected_by_wall_cab_top_alignment(self):
        """The fix only ever changes the "wall" zone's Y (elevation);
        the X-run cursor keying (kitchen_layout_engine._cabinet_run_key)
        is keyed on cursor NAME, never on y_floor, so run-length/adjacency
        identities must hold identically for every alignment mode."""
        xs = {}
        for mode, extra in (
            ("fixed_gap", {}),
            ("to_ceiling", {}),
            ("to_soffit", {"soffit_height_in": 90.0}),
        ):
            design = self._make_design(wall_cab_top_alignment=mode, **extra)
            design.action_auto_arrange()
            wl = self._wall_line(design)
            bl = design.cabinet_line_ids.filtered(
                lambda l: l.cabinet_type == "base")
            xs[mode] = (round(wl.x_position_in, 2), round(bl.x_position_in, 2),
                        wl.wall, bl.wall)
        vals = list(xs.values())
        self.assertTrue(all(v == vals[0] for v in vals),
                         "X/wall placement must be identical across every "
                         "Wall Cabinet Top mode — only Y may differ: %s" % xs)


class TestWallCabTopGeometryHelper(TransactionCase):
    """Unit coverage of `_wall_cab_top_geometry` / `_compute_wall_cab_bottom_in`
    directly — the shared helper the arrange feed, the production-readiness
    check, and the Room panel's live helper text all consult."""

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "T14 Geo"})

    def _design(self, **vals):
        return self.env["southbrook.kitchen.design"].create({
            "name": "T14 geo", "partner_id": self.partner.id,
            "room_width_in": 96.0, "room_depth_in": 96.0,
            "room_height_in": 96.0, "state": "draft",
            **vals,
        })

    def test_defaults(self):
        d = self._design()
        geo = d._wall_cab_top_geometry()
        self.assertEqual(geo["base_h"], 34.5)
        self.assertEqual(geo["wall_h"], 30.0)
        self.assertEqual(geo["ctr_t"], 1.5)
        self.assertEqual(geo["gap"], 18.0)
        self.assertEqual(geo["alignment"], "fixed_gap")
        self.assertEqual(geo["y_in"], 54.0)
        self.assertFalse(geo["soffit_gated_off"])

    def test_to_soffit_gated_flag(self):
        d = self._design(wall_cab_top_alignment="to_soffit")
        geo = d._wall_cab_top_geometry()
        self.assertTrue(geo["soffit_gated_off"])
        self.assertEqual(geo["alignment"], "fixed_gap")

    def test_helper_text_reflects_current_selection(self):
        d = self._design()
        self.assertEqual(d.wall_cab_bottom_in, 54.0)
        self.assertIn("54.00", d.wall_cab_bottom_helper)
        self.assertIn("gap above counter", d.wall_cab_bottom_helper)

        d.wall_cab_top_alignment = "to_ceiling"
        self.assertEqual(d.wall_cab_bottom_in, 66.0)
        self.assertIn("66.00", d.wall_cab_bottom_helper)
        self.assertIn("up to ceiling", d.wall_cab_bottom_helper)

        d.wall_cab_top_alignment = "to_soffit"
        self.assertIn("soffit height not set", d.wall_cab_bottom_helper)
        self.assertEqual(d.wall_cab_bottom_in, 54.0)

        d.soffit_height_in = 90.0
        self.assertEqual(d.wall_cab_bottom_in, 60.0)
        self.assertIn("60.00", d.wall_cab_bottom_helper)
        self.assertNotIn("soffit height not set", d.wall_cab_bottom_helper)

    def test_tallest_actual_cabinet_widens_base_and_wall_height(self):
        d = self._design()
        base_prod = self.env["product.product"].create(
            {"name": "Tall Base", "default_code": "T14-TALLBASE",
             "list_price": 300.0})
        self.env["southbrook.kitchen.design.line"].create({
            "design_id": d.id, "product_id": base_prod.id, "quantity": 1,
            "price_unit": 300.0, "cabinet_type": "base",
            "width_in": 24.0, "height_in": 40.0, "depth_in": 24.0,
            "origin": "configurator", "layout_key": "geo-tall-base",
        })
        geo = d._wall_cab_top_geometry()
        self.assertEqual(geo["base_h"], 40.0)
        self.assertEqual(geo["y_in"], 40.0 + 1.5 + 18.0)

    # ── Task 14 review, finding 3 (SBK-026 fix-round) ──────────────────────
    # The D12 check #6 refactor onto the shared `_wall_cab_top_geometry`
    # helper changed real behaviour, not just deduplicated code: a
    # to_soffit selection with an UNSET soffit_height_in used to compute
    # `eff_top = self.soffit_height_in or 0` -> 0, and the check's own
    # `and eff_top > 0` guard then SKIPPED it entirely — a design stuck
    # in "to_soffit, forgot to set the height" silently got ZERO
    # ceiling-clearance protection. The shared helper resolves that same
    # gated case to `alignment="fixed_gap"` with `eff_ceiling=room_height_in`
    # (the real room ceiling), so the check is now ACTIVE against the
    # room ceiling instead of silently disabled. Reviewer verdict: KEEP
    # this — it is strictly safer — but pin BOTH branches so a future
    # change can't silently re-disable the gated case, and so the
    # "checks against soffit once soffit IS set" branch stays distinct.
    def _add_wall_line(self, design, height_in=30.0):
        wall_prod = self.env["product.product"].create(
            {"name": "Geo Wall", "default_code": "T14-GEO-WALL",
             "list_price": 150.0})
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": wall_prod.id, "quantity": 1,
            "price_unit": 150.0, "cabinet_type": "wall",
            "width_in": 24.0, "height_in": height_in, "depth_in": 12.0,
            "origin": "configurator", "layout_key": "geo-wall",
        })

    def test_to_soffit_gated_case_now_checks_against_the_room_ceiling(self):
        """Before finding 3's fix, this shape (to_soffit selected, soffit
        never set) raised NOTHING — the check was a silent no-op. It
        must now flag WALL_CAB_EXCEEDS_CEILING against the room's real
        ceiling. room_height_in=84.0 is the model's own MINIMUM
        (ROOM_HEIGHT_MIN_IN, kitchen_design.py) — a genuinely short
        room, not an out-of-bounds one — paired with an oversized 40in
        wall cabinet (fixed_gap floor 54.0 + 40 = 94.0) to force the
        overflow without needing an invalid room height."""
        d = self._design(room_height_in=84.0, wall_cab_top_alignment="to_soffit")
        self._add_wall_line(d, height_in=40.0)
        self.assertEqual(d.soffit_height_in, 0.0, "unset")
        issues = d._check_production_ready()
        hits = [i for i in issues if i["code"] == "WALL_CAB_EXCEEDS_CEILING"]
        self.assertEqual(len(hits), 1,
                          "gated to_soffit must be checked against the room "
                          "ceiling, not silently skipped")
        self.assertIn("ceiling", hits[0]["message"])
        self.assertIn("84.0", hits[0]["message"])

    def test_to_soffit_set_case_checks_against_the_soffit_not_the_ceiling(self):
        """A tall room (144in ceiling — ROOM_HEIGHT_MAX_IN, the model's
        own maximum) but a LOW soffit (50in) must still flag against
        the soffit — the room ceiling alone would never catch this
        (fixed-gap floor 54.0 + standard 30in wall = 84.0, comfortably
        under 144 but over the 50in soffit), proving the check reads
        eff_ceiling from the RESOLVED alignment, not always
        room_height_in."""
        d = self._design(room_height_in=144.0, wall_cab_top_alignment="to_soffit",
                          soffit_height_in=50.0)
        self._add_wall_line(d)
        issues = d._check_production_ready()
        hits = [i for i in issues if i["code"] == "WALL_CAB_EXCEEDS_CEILING"]
        self.assertEqual(len(hits), 1)
        self.assertIn("soffit", hits[0]["message"])
        self.assertIn("50.0", hits[0]["message"])

    def test_to_soffit_gated_case_with_room_that_fits_raises_nothing(self):
        """Non-regression: a gated to_soffit design that DOES fit under
        the room ceiling must not spuriously flag."""
        d = self._design(room_height_in=96.0, wall_cab_top_alignment="to_soffit")
        self._add_wall_line(d)
        issues = d._check_production_ready()
        hits = [i for i in issues if i["code"] == "WALL_CAB_EXCEEDS_CEILING"]
        self.assertEqual(hits, [])
