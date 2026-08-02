# SPDX-License-Identifier: LGPL-3.0-only
"""P0 E2E fix (2026-08-02) — "Auto-arrange" must actually resolve an
active overlap blocker when called on a MANUAL (multi-wall) design, and
must tell the caller what it did.

Bug report: a user with an ACTIVE OVERLAP BLOCKER (two cabinets occupying
the same footprint) clicked "Auto-arrange" and got zero change, zero
feedback — same counts, same price, same blocker. `action_auto_arrange`'s
`manual=True` branch (any cabinet already on a non-"back" wall) passes
`auto_assign=False` to kitchen_layout_engine, which preserves each
cabinet's existing wall — correct, a Floor Manager's wall choice must
survive "Auto-arrange" — but the method's return value carried no
before/after signal, so a caller could not tell "resolved" from
"nothing happened" from "didn't fully resolve it".

This suite mirrors test_auto_arrange_service.py's fixture style
(TransactionCase, `product.product`/`product.template`/`res.partner`
fixtures created directly, `southbrook.kitchen.design.line` created via
`Line.create`) and pins:

  (a) two cabinets overlapping in the SAME wall+zone run — after a
      manual arrange, zero footprint overlaps remain and `moved > 0`.
  (b) an already-clean manual design — a second arrange reports
      `moved == 0` (a real, representable "nothing to do" outcome, not
      an absent/ambiguous key) and does not reposition anything.
  (c) wall assignments are preserved — the two cabinets from (a) are
      still on the wall the user put them on after the overlap is
      resolved.
  (d) the report dict always carries the documented keys
      (moved/overlaps_before/overlaps_after/summary), on both the
      overlap-resolving call and the empty-design short-circuit.
"""
from odoo.tests.common import TransactionCase


class TestManualArrangeResolvesOverlap(TransactionCase):

    def setUp(self):
        super().setUp()
        Template = self.env["product.template"]
        # Corner templates aren't expected to be used by these
        # single-wall fixtures (no junction exists with cabinets on
        # only one wall — see the module docstring), but are seeded
        # anyway, matching test_auto_arrange_service.py's setUp, so a
        # test never silently depends on incidental catalog state.
        for code, name, price in [("SB-CORNER", "Corner Base", 625.0),
                                  ("SB-WALL-CORNER", "Corner Wall", 295.0)]:
            if not Template.search([("default_code", "=", code)], limit=1):
                Template.create({"name": name, "default_code": code,
                                 "list_price": price})
        self.base_prod = self.env["product.product"].create(
            {"name": "Test Base 24 (manual-arrange)",
             "default_code": "ZZ-MA-B24", "list_price": 200.0})
        self.partner = self.env["res.partner"].create(
            {"name": "Manual Arrange Test"})

    def _make_design(self, room_w=140.0, room_d=200.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "Manual arrange test", "partner_id": self.partner.id,
            "room_width_in": room_w, "room_depth_in": room_d,
            "room_height_in": 96.0, "state": "draft"})

    def _make_line(self, design, wall, run_seq, x=0.0, z=0.0,
                   width_in=24.0):
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id": design.id, "product_id": self.base_prod.id,
            "quantity": 1, "price_unit": 200.0, "cabinet_type": "base",
            "width_in": width_in, "height_in": 34.5, "depth_in": 24.0,
            "wall": wall, "run_seq": run_seq,
            "x_position_in": x, "y_position_in": 0.0, "z_position_in": z,
            "origin": "configurator", "layout_role": "canonical",
            "layout_key": "ma-%s-%s" % (wall, run_seq)})

    def test_overlapping_same_run_resolves_and_reports_moved(self):
        design = self._make_design()
        # Both cabinets on the LEFT wall (non-"back" => manual=True,
        # auto_assign=False), pinned to the EXACT same stored footprint
        # — a same-run collision, the shape the bug report describes.
        a = self._make_line(design, "left", 0, x=0.0, z=0.0)
        b = self._make_line(design, "left", 1, x=0.0, z=0.0)
        self.assertEqual(design._sb_count_footprint_overlaps(), 1,
            "fixture sanity: the two cabinets must start overlapping")

        res = design.action_auto_arrange(sync=False)

        self.assertEqual(res["overlaps_before"], 1)
        self.assertEqual(res["overlaps_after"], 0,
            "manual arrange must resolve a same-run overlap")
        self.assertEqual(design._sb_count_footprint_overlaps(), 0)
        self.assertGreater(res["moved"], 0,
            "at least one cabinet must have actually been repositioned")
        # The two cabinets no longer share a footprint (real, persisted
        # positions differ) — not just the counter claiming so.
        a_pos = (round(a.x_position_in, 2), round(a.z_position_in, 2))
        b_pos = (round(b.x_position_in, 2), round(b.z_position_in, 2))
        self.assertNotEqual(a_pos, b_pos)

    def test_wall_assignment_preserved_across_manual_arrange(self):
        design = self._make_design()
        a = self._make_line(design, "left", 0, x=0.0, z=0.0)
        b = self._make_line(design, "left", 1, x=0.0, z=0.0)
        design.action_auto_arrange(sync=False)
        # The user put both cabinets on the LEFT wall; "Auto-arrange"
        # must resolve the overlap WITHOUT relocating either to a
        # different wall (auto_assign=False contract).
        self.assertEqual(a.wall, "left")
        self.assertEqual(b.wall, "left")

    def test_already_clean_design_reports_no_op(self):
        design = self._make_design()
        self._make_line(design, "left", 0, x=0.0, z=0.0)
        self._make_line(design, "left", 1, x=100.0, z=1000.0)
        # First call settles the design into the engine's own canonical
        # packed positions (mirrors test_auto_arrange_service.py's
        # test_idempotent_active_lines two-phase pattern) — the fixture
        # positions above are deliberately NOT pre-computed to match the
        # packer's cursor math, so this call is expected to move them
        # once; only the SECOND call is the no-op under test.
        design.action_auto_arrange(sync=False)
        self.assertEqual(design._sb_count_footprint_overlaps(), 0,
            "fixture sanity: settled design must be overlap-free")

        res = design.action_auto_arrange(sync=False)

        self.assertEqual(res["overlaps_before"], 0)
        self.assertEqual(res["overlaps_after"], 0)
        self.assertEqual(res["moved"], 0,
            "an already-arranged design must report moved=0, not silence")
        self.assertEqual(res["corners"], 0)
        self.assertEqual(res["removed"], 0)
        self.assertEqual(res["inserted"], 0)
        self.assertIn("Already arranged", res["summary"])

    def test_report_dict_has_documented_keys(self):
        design = self._make_design()
        self._make_line(design, "left", 0, x=0.0, z=0.0)
        self._make_line(design, "left", 1, x=0.0, z=0.0)
        res = design.action_auto_arrange(sync=False)
        for key in ("corners", "removed", "inserted", "moved",
                    "overlaps_before", "overlaps_after", "summary"):
            self.assertIn(key, res, "missing report key: %s" % key)
        self.assertIsInstance(res["moved"], int)
        self.assertIsInstance(res["overlaps_before"], int)
        self.assertIsInstance(res["overlaps_after"], int)
        self.assertIsInstance(res["summary"], str)
        self.assertTrue(res["summary"])

    def test_empty_design_report_has_documented_keys_too(self):
        design = self._make_design()
        res = design.action_auto_arrange(sync=False)
        self.assertTrue(res.get("empty"))
        for key in ("corners", "removed", "inserted", "moved",
                    "overlaps_before", "overlaps_after", "summary"):
            self.assertIn(key, res, "missing report key: %s" % key)
        self.assertEqual(res["moved"], 0)
        self.assertEqual(res["overlaps_before"], 0)
        self.assertEqual(res["overlaps_after"], 0)
