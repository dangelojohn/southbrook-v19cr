# SPDX-License-Identifier: LGPL-3.0-only
"""Task 14, corner-remediation (2026-07-30, SBK-026) — controller-level
half of "Wall-cabinet mount height honours the selector":

  1. `/layout` now accepts `counter_thickness_in` (previously a bare
     local constant, `ctr_t = 1.5`) and uses it in the SAME
     `base_h + ctr_t + gap` formula `southbrook.kitchen.design.
     _wall_cab_top_geometry` uses for the persisted arrange path, so a
     brand-new design's preview and a later regenerate never disagree.
  2. `save_design` round-trips `filler_strategy` / `wall_cab_top_
     alignment` / `soffit_height_in` / `counter_thickness_in` onto the
     design record — previously never sent by the client at all (see
     kitchen_configurator.js's pre-fix `_saveDesign`/`_autoSave`
     payloads), so even a session that correctly computed a fresh
     layout via `/layout` had its ALIGNMENT CHOICE silently dropped:
     the very next Auto-arrange/resize (which reads these fields off
     the persisted record, not off any client session) reverted to
     the model defaults. `None` (unsent) must leave the persisted
     value untouched — a partial/older payload must never clobber it.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

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
class TestLayoutCounterThickness(TransactionCase):
    """/layout's counter_thickness_in param. Isolation pattern mirrors
    test_filler_remainder_gate.py: neutralize every OTHER sale_ok
    base/wall candidate so this class's own fixtures are the only ones
    `_first_product` (no `order=`, `limit=1`) can return, independent
    of whatever else is seeded on a full-catalog Harness B clone."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        competitors = cls.env["product.product"].search([
            ("product_tmpl_id.southbrook_is_cabinet", "=", True),
            ("product_tmpl_id.southbrook_cabinet_type", "in", ("base", "wall")),
            ("sale_ok", "=", True),
        ])
        if competitors:
            competitors.write({"sale_ok": False})

        cls.tmpl_base = cls.env["product.template"].create({
            "name": "T14 Base Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl_base.southbrook_is_cabinet = True
        cls.tmpl_base.southbrook_cabinet_type = "base"
        cls.tmpl_base.southbrook_width_in = 24.0
        cls.tmpl_base.southbrook_height_in = 34.5

        cls.tmpl_wall = cls.env["product.template"].create({
            "name": "T14 Wall Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 350.0,
        })
        cls.tmpl_wall.southbrook_is_cabinet = True
        cls.tmpl_wall.southbrook_cabinet_type = "wall"
        cls.tmpl_wall.southbrook_height_in = 30.0

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def _layout(self, **kw):
        controller = self._controller()
        with _stubbed_request(self.env):
            return controller.layout(**kw)

    def test_default_counter_thickness_matches_the_1_5in_standard(self):
        result = self._layout(room_width_in=48, room_depth_in=96, room_height_in=96)
        self.assertEqual(result.get("error", ""), "")
        wall_items = [it for it in result["items"] if it["cabinet_type"] == "wall"]
        self.assertTrue(wall_items)
        for it in wall_items:
            # 34.5 + 1.5 + 18 = 54.0
            self.assertAlmostEqual(it["y_position_in"], 54.0, places=2)

    def test_custom_counter_thickness_moves_the_wall_mount_height(self):
        result = self._layout(room_width_in=48, room_depth_in=96, room_height_in=96,
                               counter_thickness_in=2.5)
        wall_items = [it for it in result["items"] if it["cabinet_type"] == "wall"]
        self.assertTrue(wall_items)
        for it in wall_items:
            # 34.5 + 2.5 + 18 = 55.0
            self.assertAlmostEqual(it["y_position_in"], 55.0, places=2)

    def test_to_ceiling_is_unaffected_by_counter_thickness(self):
        """to_ceiling's floor (min_z) still consults ctr_t, but ceiling
        - wall_h (66.0) is comfortably above that floor regardless, so
        the visible result is unchanged — locking that this param
        change didn't accidentally perturb the to_ceiling formula."""
        result = self._layout(room_width_in=48, room_depth_in=96, room_height_in=96,
                               wall_cab_top_alignment="to_ceiling",
                               counter_thickness_in=2.5)
        wall_items = [it for it in result["items"] if it["cabinet_type"] == "wall"]
        for it in wall_items:
            self.assertAlmostEqual(it["y_position_in"], 96.0 - 30.0, places=2)


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "corner_remediation")
class TestSaveDesignRoundTripsWallCabTopSettings(TransactionCase):
    """save_design's SBK-026 round-trip of the Room-panel settings."""

    def _controller(self):
        return ctrl_main.SouthbrookKitchenConfiguratorController()

    def test_new_design_persists_all_four_fields_when_sent(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T14 round-trip",
                room={"width_in": 96, "depth_in": 96, "height_in": 96},
                items=[],
                partner_id=False,
                design_id=False,
                filler_strategy="scribe",
                wall_cab_top_alignment="to_soffit",
                soffit_height_in=90.0,
                counter_thickness_in=2.0,
            )
        self.assertNotIn("error", result, result)
        design = self.env["southbrook.kitchen.design"].browse(result["id"])
        self.assertEqual(design.filler_strategy, "scribe")
        self.assertEqual(design.wall_cab_top_alignment, "to_soffit")
        self.assertEqual(design.soffit_height_in, 90.0)
        self.assertEqual(design.counter_thickness_in, 2.0)

    def test_omitted_fields_leave_the_existing_record_untouched(self):
        design = self.env["southbrook.kitchen.design"].create({
            "name": "T14 existing",
            "room_width_in": 96.0, "room_depth_in": 96.0, "room_height_in": 96.0,
            "wall_cab_top_alignment": "to_ceiling",
            "soffit_height_in": 84.0,
            "counter_thickness_in": 2.25,
        })
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name=design.name,
                room={"width_in": 96, "depth_in": 96, "height_in": 96},
                items=[],
                partner_id=False,
                design_id=design.id,
                # None (unsent) for all four — the real shape of an
                # older/partial client payload.
            )
        self.assertNotIn("error", result, result)
        design.invalidate_recordset()
        self.assertEqual(design.wall_cab_top_alignment, "to_ceiling",
                          "an unsent field must never revert to the model default")
        self.assertEqual(design.soffit_height_in, 84.0)
        self.assertEqual(design.counter_thickness_in, 2.25)

    def test_unknown_alignment_value_falls_back_to_fixed_gap_not_a_500(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T14 bad alignment",
                room={"width_in": 96, "depth_in": 96, "height_in": 96},
                items=[], partner_id=False, design_id=False,
                wall_cab_top_alignment="bogus_mode",
            )
        self.assertNotIn("error", result, result)
        design = self.env["southbrook.kitchen.design"].browse(result["id"])
        self.assertEqual(design.wall_cab_top_alignment, "fixed_gap")

    def test_negative_counter_thickness_is_clamped_not_persisted_negative(self):
        controller = self._controller()
        with _stubbed_request(self.env):
            result = controller.save_design(
                name="T14 negative ctr",
                room={"width_in": 96, "depth_in": 96, "height_in": 96},
                items=[], partner_id=False, design_id=False,
                counter_thickness_in=-5.0,
            )
        self.assertNotIn("error", result, result)
        design = self.env["southbrook.kitchen.design"].browse(result["id"])
        self.assertGreaterEqual(design.counter_thickness_in, 0.0)
