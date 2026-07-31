# SPDX-License-Identifier: LGPL-3.0-only
"""Task 10 (corner-remediation) — filler engine: no phantom filler/
remainder on an undersized/empty room, and an honest (geometric)
remainder once the room is populated.

Root cause (V5-partc §SBK-023): controllers/main.py's /layout route
computed `remainder = rw - n*module_w` UNCONDITIONALLY, even when n==0
(room narrower than one module — e.g. the client's own 12in minimum
room width vs. a 24in module default). The filler-creation branch below
then priced and placed that as a real filler line on a room with ZERO
real cabinets — the confirmed "[FP3] +$140 filler on an empty 12in
room" and "$70 persisted, zero cabinets" findings.

Fix: `remainder` is 0.0 whenever n==0 — one condition, which cascades
through both the filler-creation gate (`if remainder >= 1.0 and
filler_strategy != "scribe"`) and the `remainder_in` summary field. For
n >= 1, `remainder = rw - n*module_w` is UNCHANGED: V5-partc's own fix
sketch says to "derive remainder_in from the geometric room/cabinet
math", which is exactly what this already does once there's a real run
— including for filler_strategy="scribe", where a nonzero remainder_in
alongside a $0 filler_price is BY DESIGN (the true physical gap the
installer scribes on site; refuted as a bug per V5-partc, same pattern
as "Est. excludes filler by design").
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
        "southbrook_kitchen_3d_configurator", "kitchen_templates")
class TestFillerRemainderGate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpl_base = cls.env["product.template"].create({
            "name": "T10 Base Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 500.0,
        })
        cls.tmpl_base.southbrook_is_cabinet = True
        cls.tmpl_base.southbrook_cabinet_type = "base"
        cls.tmpl_base.southbrook_width_in = 24.0
        cls.tmpl_base.southbrook_height_in = 34.5

        cls.tmpl_wall = cls.env["product.template"].create({
            "name": "T10 Wall Cabinet", "type": "consu", "sale_ok": True,
            "list_price": 350.0,
        })
        cls.tmpl_wall.southbrook_is_cabinet = True
        cls.tmpl_wall.southbrook_cabinet_type = "wall"
        cls.tmpl_wall.southbrook_height_in = 30.0

        # Mirrors the demo FP3 fixture (list_price=35.00, width_in=3)
        # V5-partc traced its repro against.
        cls.tmpl_filler = cls.env["product.template"].create({
            "name": "T10 Filler Panel 3in", "type": "consu", "sale_ok": True,
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

    # ── AC — 0-cabinet design shows $0.00 and no filler anywhere ───────
    def test_undersized_room_yields_zero_cabinets_and_zero_filler(self):
        """rw=12 < module_w=24 -> n=0, for every filler strategy."""
        for strategy in ("split", "left", "right", "scribe"):
            result = self._layout(room_width_in=12, room_depth_in=24,
                                   room_height_in=96,
                                   filler_strategy=strategy)
            self.assertEqual(result.get("error", ""), "")
            self.assertEqual(
                result["items"], [],
                "strategy=%s: no items at all on an undersized room"
                % strategy)
            summary = result["summary"]
            self.assertEqual(summary["base_count"], 0)
            self.assertEqual(summary["wall_count"], 0)
            self.assertEqual(summary["price"], 0.0)
            self.assertEqual(summary["cabinet_price"], 0.0)
            self.assertEqual(
                summary["filler_price"], 0.0,
                "strategy=%s: no filler $ on a 0-cabinet design" % strategy)
            self.assertEqual(
                summary["remainder_in"], 0.0,
                "strategy=%s: no phantom remainder on a 0-cabinet design"
                % strategy)

    def test_zero_width_room_is_also_gated(self):
        """The degenerate n=0 case at rw=0 — must not divide/overflow or
        synthesize a filler from a nonexistent room."""
        result = self._layout(room_width_in=0, room_depth_in=24,
                               room_height_in=96)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["summary"]["remainder_in"], 0.0)
        self.assertEqual(result["summary"]["filler_price"], 0.0)

    # ── AC — remainder test pins run-length identity ───────────────────
    def test_run_length_identity_holds_once_populated(self):
        """rw == n*module_w + remainder_in must hold for every strategy
        once n >= 1 — the pre-existing geometric definition V5-partc
        confirms is correct (the only bug was the missing n>0 gate)."""
        rw = 64.0   # module_w=24 -> n=2, remainder=16
        module_w = 24.0
        for strategy in ("split", "left", "right", "scribe"):
            result = self._layout(room_width_in=rw, room_depth_in=24,
                                   room_height_in=96,
                                   filler_strategy=strategy)
            summary = result["summary"]
            self.assertEqual(summary["base_count"], 2, "strategy=%s" % strategy)
            self.assertAlmostEqual(
                rw, summary["base_count"] * module_w + summary["remainder_in"],
                places=2,
                msg="strategy=%s: run-length identity broke" % strategy)

    # ── AC — four filler strategies place correctly (regression guard) ──
    def test_split_places_half_width_filler_at_each_end(self):
        result = self._layout(room_width_in=30, room_depth_in=24,
                               room_height_in=96, filler_strategy="split")
        fillers = [it for it in result["items"]
                   if it["cabinet_type"] == "filler"]
        self.assertEqual(len(fillers), 2)
        self.assertEqual(sum(f["width_in"] for f in fillers), 6.0)
        self.assertEqual(round(sum(f["price"] for f in fillers), 2), 70.0)
        xs = sorted(f["x_position_in"] for f in fillers)
        self.assertEqual(xs[0], 0.0)
        self.assertEqual(xs[1], 27.0)   # n*module_w(24) + half(3)

    def test_left_places_single_filler_at_x_zero(self):
        result = self._layout(room_width_in=30, room_depth_in=24,
                               room_height_in=96, filler_strategy="left")
        fillers = [it for it in result["items"]
                   if it["cabinet_type"] == "filler"]
        self.assertEqual(len(fillers), 1)
        self.assertEqual(fillers[0]["x_position_in"], 0.0)
        self.assertEqual(fillers[0]["width_in"], 6.0)

    def test_right_places_single_filler_at_run_end(self):
        result = self._layout(room_width_in=30, room_depth_in=24,
                               room_height_in=96, filler_strategy="right")
        fillers = [it for it in result["items"]
                   if it["cabinet_type"] == "filler"]
        self.assertEqual(len(fillers), 1)
        self.assertEqual(fillers[0]["x_position_in"], 24.0)   # n*module_w
        self.assertEqual(fillers[0]["width_in"], 6.0)

    def test_scribe_creates_no_filler_item_but_reports_honest_remainder(self):
        result = self._layout(room_width_in=30, room_depth_in=24,
                               room_height_in=96, filler_strategy="scribe")
        fillers = [it for it in result["items"]
                   if it["cabinet_type"] == "filler"]
        self.assertEqual(fillers, [], "scribe never synthesizes a filler line")
        self.assertEqual(result["summary"]["filler_price"], 0.0)
        self.assertEqual(
            result["summary"]["remainder_in"], 6.0,
            "scribe still reports the true physical gap (by design) so "
            "the installer knows how much to scribe on site")
