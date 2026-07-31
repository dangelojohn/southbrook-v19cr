# SPDX-License-Identifier: LGPL-3.0-only
"""Task 12 (corner-remediation 2026-07-30, SBK-021/022) — controller-
level half of "room bounds, line cap, honest field errors":

  1. `save_design` rejects an out-of-range room with a readable
     {error, error_code: "ROOM_OUT_OF_BOUNDS"} response, BEFORE any
     create/write — live evidence: room_width_in=99999 generated 8,332
     lines / ~$1.77M and persisted verbatim, because nothing on this
     path checked a sane bound. `test_new_save_rejects_...` and
     `test_existing_save_rejects_...` lock "nothing persisted" for both
     the fresh-design and update-existing-design shapes.
  2. `/layout` caps engine-generated lines at 120
     (`_MAX_AUTOFILL_LINES`/`_MAX_AUTOFILL_PAIRS`, controllers/main.py)
     with an explicit, non-silent `ROOM_TOO_LARGE_FOR_AUTOFILL` warning
     — no filler is synthesized for the (now very large) leftover run,
     since a filler is meant to bridge a small end-of-run gap, not a
     multi-thousand-inch one (that would just be the same phantom-cost
     bug in a new shape — see test_filler_remainder_gate.py's SBK-023
     fix for the sibling n==0 case this mirrors).

Follows test_filler_remainder_gate.py's isolation pattern: this class's
own base/wall/filler templates must be the ONLY sale_ok candidates
`_first_product` can find, so the test's module_w=24 assumption can't
be silently invalidated by a pre-seeded catalog product on a full-
catalog Harness B clone.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_kitchen_3d_configurator.controllers import (
    main as ctrl_main,
)


@contextmanager
def _stubbed_request(env):
    saved = ctrl_main.request
    mock = MagicMock()
    mock.env = env
    mock.session = {}
    mock.params = {}
    ctrl_main.request = mock
    try:
        yield mock
    finally:
        ctrl_main.request = saved


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation")
class TestSaveDesignRoomBounds(TransactionCase):
    """save_design's ROOM_OUT_OF_BOUNDS pre-check. Uses items=[] — the
    bounds check runs before any per-item product resolution, so no
    catalog fixture is needed for this half."""

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def test_new_save_rejects_width_99999_and_persists_nothing(self):
        Design = self.env["southbrook.kitchen.design"]
        before = Design.search_count([])
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T12 should not persist",
                room={"width_in": 99999, "depth_in": 96, "height_in": 96},
                items=[],
                partner_id=False,
                design_id=False,
            )
        self.assertEqual(result.get("error_code"), "ROOM_OUT_OF_BOUNDS")
        self.assertIn("480", result.get("error", ""),
            "the readable message must name the actual bound, not a generic string")
        after = Design.search_count([])
        self.assertEqual(before, after, "no design record must be created")
        self.assertFalse(
            Design.search([("name", "=", "T12 should not persist")]))

    def test_existing_save_rejects_and_leaves_the_record_unchanged(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T12 existing", "room_width_in": 120.0,
            "room_depth_in": 96.0, "room_height_in": 96.0,
        })
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name=design.name,
                room={"width_in": 99999, "depth_in": 96, "height_in": 96},
                items=[],
                partner_id=False,
                design_id=design.id,
            )
        self.assertEqual(result.get("error_code"), "ROOM_OUT_OF_BOUNDS")
        design.invalidate_recordset()
        self.assertEqual(
            design.room_width_in, 120.0,
            "a rejected save must leave the existing record's room dims untouched")
        self.assertFalse(design.cabinet_line_ids)

    def test_rejects_depth_and_height_out_of_range_too(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result_d = controller.save_design(
                name="T12 depth",
                room={"width_in": 96, "depth_in": 1, "height_in": 96},
                items=[], partner_id=False, design_id=False)
        self.assertEqual(result_d.get("error_code"), "ROOM_OUT_OF_BOUNDS")
        with _stubbed_request(self.env):
            result_h = controller.save_design(
                name="T12 height",
                room={"width_in": 96, "depth_in": 96, "height_in": 9999},
                items=[], partner_id=False, design_id=False)
        self.assertEqual(result_h.get("error_code"), "ROOM_OUT_OF_BOUNDS")

    def test_non_numeric_room_dims_are_rejected_not_500ed(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T12 nan",
                room={"width_in": "not-a-number", "depth_in": 96, "height_in": 96},
                items=[], partner_id=False, design_id=False)
        self.assertEqual(result.get("error_code"), "ROOM_OUT_OF_BOUNDS")

    def test_boundary_values_are_accepted(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T12 boundary",
                room={"width_in": 480, "depth_in": 12, "height_in": 144},
                items=[], partner_id=False, design_id=False)
        self.assertNotEqual(result.get("error_code"), "ROOM_OUT_OF_BOUNDS")
        self.assertTrue(result.get("id"))
        design = self.env["southbrook.kitchen.design"].browse(result["id"])
        self.assertEqual(design.room_width_in, 480.0)
        self.assertEqual(design.room_depth_in, 12.0)
        self.assertEqual(design.room_height_in, 144.0)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation")
class TestLayoutAutofillCap(TransactionCase):
    """/layout's 120-line hard cap. Mirrors test_filler_remainder_gate.py's
    isolated-catalog setup (module_w=24) so the n_uncapped math is
    deterministic regardless of what else is seeded on this DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Same isolation rationale as test_filler_remainder_gate.py:
        # neutralize any pre-existing base/wall/filler candidate so
        # THIS class's own fixtures (module_w=24) are the only ones
        # `_first_product` can resolve.
        competitors = cls.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("product_tmpl_id.southbrook_cabinet_type", "in",
             ("base", "wall", "filler")),
            ("sale_ok", "=", True),
        ])
        if competitors:
            competitors.write({"sale_ok": False})

        cls.tmpl_base = cls.env["product.template"].create({
            "name": "T12 Base Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl_base.southbrook_is_cabinet = True
        cls.tmpl_base.southbrook_cabinet_type = "base"
        cls.tmpl_base.southbrook_width_in = 24.0
        cls.tmpl_base.southbrook_height_in = 34.5

        cls.tmpl_wall = cls.env["product.template"].create({
            "name": "T12 Wall Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 350.0,
        })
        cls.tmpl_wall.southbrook_is_cabinet = True
        cls.tmpl_wall.southbrook_cabinet_type = "wall"
        cls.tmpl_wall.southbrook_height_in = 30.0

        cls.tmpl_filler = cls.env["product.template"].create({
            "name": "T12 Filler Panel 3in", "type": "consu", "sale_ok": True,
            "list_price": 35.0,
        })
        cls.tmpl_filler.southbrook_is_cabinet = True
        cls.tmpl_filler.southbrook_cabinet_type = "filler"
        cls.tmpl_filler.southbrook_width_in = 3.0

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _layout(self, **kw):
        controller = self._controller()
        with _stubbed_request(self.env):
            return controller.layout(**kw)

    def test_huge_room_is_capped_at_120_lines_with_a_surfaced_warning(self):
        # module_w=24 -> floor(2000/24)=83 pairs uncapped (166 lines);
        # _MAX_AUTOFILL_PAIRS=59 -> capped to 59 pairs = 118 lines, no
        # filler on top (capped rooms never synthesize one).
        result = self._layout(room_width_in=2000, room_depth_in=96,
                               room_height_in=96)
        self.assertEqual(result.get("error", ""), "")
        self.assertLessEqual(
            len(result["items"]), 120,
            "engine-generated lines must never exceed the hard cap")
        self.assertEqual(len(result["items"]), 118,
            "59 capped pairs * 2 (base+wall), no filler on a capped room")
        self.assertEqual(result["summary"]["base_count"], 59)
        self.assertEqual(result["summary"]["wall_count"], 59)
        self.assertTrue(result["summary"].get("capped"),
            "machine-readable capped flag must be set")
        self.assertEqual(result["summary"]["filler_count"], 0,
            "no filler synthesized once capped — the leftover run is "
            "1416in+ wide, not a small end-of-run gap")

        codes = [w.get("code") for w in result.get("warnings", [])]
        self.assertIn("ROOM_TOO_LARGE_FOR_AUTOFILL", codes,
            "SBK-021/022 — capping must be NON-SILENT: an explicit "
            "warning must surface, not a quietly-truncated list")
        warning = next(w for w in result["warnings"]
                       if w["code"] == "ROOM_TOO_LARGE_FOR_AUTOFILL")
        self.assertIn("too large to auto-fill", warning["message"].lower())

    def test_ordinary_room_is_not_capped_and_carries_no_such_warning(self):
        result = self._layout(room_width_in=64, room_depth_in=24,
                               room_height_in=96)
        self.assertEqual(result["summary"]["base_count"], 2)
        self.assertFalse(result["summary"].get("capped"))
        codes = [w.get("code") for w in result.get("warnings", [])]
        self.assertNotIn("ROOM_TOO_LARGE_FOR_AUTOFILL", codes)

    def test_capped_room_reports_the_honest_geometric_remainder(self):
        # 2000 - 59*24 = 584 — a large but geometrically honest number
        # (not zero, not phantom); just never turned into a filler item.
        result = self._layout(room_width_in=2000, room_depth_in=96,
                               room_height_in=96)
        self.assertAlmostEqual(result["summary"]["remainder_in"], 584.0,
                                places=2)
        fillers = [it for it in result["items"]
                   if it["cabinet_type"] == "filler"]
        self.assertEqual(fillers, [])

    def test_width_exactly_at_the_cap_boundary_is_not_flagged_capped(self):
        # module_w=24 * 59 pairs = 1416in -> n_uncapped == 59 == the cap
        # itself: NOT capped (capped is strictly ">" the max, not ">=").
        result = self._layout(room_width_in=1416, room_depth_in=96,
                               room_height_in=96)
        self.assertEqual(result["summary"]["base_count"], 59)
        self.assertFalse(result["summary"].get("capped"))
