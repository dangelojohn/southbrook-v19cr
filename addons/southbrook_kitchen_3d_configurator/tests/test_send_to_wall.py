# SPDX-License-Identifier: LGPL-3.0-only
"""Touch editing v1 (2026-08-02 spec §5) — "send-to-wall" exposure.

`SouthbrookKitchenDesignLine.action_move(wall, run_seq)`
(models/kitchen_design.py) already existed and was already unit-tested
(test_layout_manipulation.py::test_move_reorders_run_and_rearranges)
before touch v1 — the spec's own claim is it is "server-complete and
tested with zero client wiring". This file pins the touch-v1-specific
hardening on top of that pre-existing behaviour:

  1. Happy path — moving a canonical line to a different wall
     succeeds, re-derives poses (existing behaviour), and now ALSO
     returns the relaid lines directly in the response (`res["lines"]`)
     so the client never needs the action_swap_product-style
     follow-up /load_design_lines round trip.
  2. Invalid wall — a wall string outside kitchen_layout_engine.WALLS
     is refused (UserError) before any write; the line's wall is left
     completely untouched.
  3. Pinned lines untouched — a DIFFERENT canonical line's `pinned`
     FIELD survives the rearrange action_move triggers, even though
     its geometric pose (x/y/z) may legitimately change because the
     run it belongs to was repacked. `pinned` is a manual-placement
     flag, not a "don't touch this line at all" guarantee — action_
     auto_arrange re-derives every canonical line's pose by design
     (test_layout_manipulation.py's own module docstring); the
     regression this guards is action_move/auto_arrange silently
     resetting the FLAG itself, which would strand the client's
     "this was manually placed" bookkeeping.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.southbrook_estimating.models import kitchen_layout_engine


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator", "touch_editing")
class TestSendToWall(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Send-to-Wall Test Customer",
            "email": "kitchen3d.sendtowall@southbrook.test",
        })
        # Generous room — 200in x 200in — so a cabinet placed on any of
        # the four walls fits comfortably; this test is about the
        # wall/pinned CONTRACT, not corner-fit edge cases (those are
        # covered by test_layout_manipulation.py's own capacity test).
        cls.design = cls.env["southbrook.kitchen.design"].create({
            "name": "Send-to-wall test",
            "partner_id": cls.partner.id,
            "room_width_in": 200,
            "room_depth_in": 200,
            "room_height_in": 96,
        })
        cls.tmpl = cls.env["product.template"].create({
            "name": "Send-to-Wall Test Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 400.0,
        })
        cls.tmpl.southbrook_is_cabinet = True
        cls.tmpl.southbrook_cabinet_type = "base"
        cls.product = cls.tmpl.product_variant_id

    def _make_line(self, layout_key, wall, run_seq, pinned=False):
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id":      self.design.id,
            "product_id":     self.product.id,
            "price_unit":     400.0,
            "cabinet_type":   "base",
            "origin":         "configurator",
            "layout_role":    "canonical",
            "layout_key":     layout_key,
            "wall":           wall,
            "run_seq":        run_seq,
            "width_in":       24.0,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  0.0,
            "y_position_in":  0.0,
            "z_position_in":  0.0,
            "pinned":         pinned,
        })

    # ------------------------------------------------------------------
    def test_happy_path_moves_wall_and_returns_relaid_lines(self):
        # "back" -> "front" deliberately: those two walls are never a
        # kitchen_layout_engine._CORNER_SPECS pair (only the four
        # adjacent-wall combos — back-left/back-right/front-left/
        # front-right — form a junction), so this move is a pure
        # relocation with zero corner-engine interaction. Adjacent-wall
        # moves DO legitimately trigger corner absorption (the engine
        # substitutes a real corner cabinet and archives both run-end
        # cabinets) — that is correct, pre-existing, separately-tested
        # behaviour (test_zone_corner_l_shape_fixes.py etc.), not this
        # test's contract.
        mover = self._make_line("stw-mover", "back", 10)
        other = self._make_line("stw-other", "back", 20)

        res = mover.action_move(wall="front", run_seq=0)

        self.assertEqual(mover.wall, "front")
        # Pre-existing contract (action_auto_arrange's documented
        # shape) — must still be present.
        self.assertIn("corners", res)
        self.assertIn("removed", res)
        self.assertIn("inserted", res)
        # Touch v1 addition — relaid lines in ONE hop, no follow-up
        # /load_design_lines RPC needed.
        self.assertIn("lines", res)
        self.assertIsInstance(res["lines"], list)
        keys_by_layout_key = {l["layout_key"]: l for l in res["lines"]}
        self.assertIn("stw-mover", keys_by_layout_key)
        self.assertIn("stw-other", keys_by_layout_key)
        self.assertEqual(keys_by_layout_key["stw-mover"]["wall"], "front")
        # Same shape controllers/main.py._serialize_design_lines has
        # always emitted — spot check a representative field set.
        for field in ("id", "product_id", "product_name", "cabinet_type",
                      "x_position_in", "y_position_in", "z_position_in",
                      "rotation_deg", "pinned", "price", "quantity",
                      "wall", "is_unresolved"):
            self.assertIn(field, keys_by_layout_key["stw-mover"])

    def test_invalid_wall_refused_and_untouched(self):
        mover = self._make_line("stw-badwall", "back", 10)
        wall_before = mover.wall
        run_seq_before = mover.run_seq
        with self.assertRaises(UserError):
            mover.action_move(wall="ceiling", run_seq=99)
        self.assertEqual(mover.wall, wall_before,
                          "a refused move must leave wall untouched")
        self.assertEqual(mover.run_seq, run_seq_before,
                          "a refused move must leave run_seq untouched")

    def test_wall_validated_against_kitchen_layout_engine_walls(self):
        """Every value in the canonical WALLS enumeration must be
        accepted; anything outside it must be refused — proves the
        check is against the shared constant, not a stale local copy
        that could silently drift from save_design's own validation."""
        mover = self._make_line("stw-allwalls", "back", 10)
        for wall in kitchen_layout_engine.WALLS:
            mover.action_move(wall=wall, run_seq=0)
            self.assertEqual(mover.wall, wall)
        with self.assertRaises(UserError):
            mover.action_move(wall="up", run_seq=0)

    def test_pinned_flag_on_other_line_survives_rearrange(self):
        """Moving one line to a new wall triggers a full re-derive pass
        (action_auto_arrange re-derives every canonical line's pose) —
        a DIFFERENT line's manual-placement `pinned` FLAG must still
        come through untouched, even if its position was repacked.
        "back" -> "front" again (see the happy-path test's comment) so
        this is a plain relocation, not a corner-junction absorption —
        the latter is a SEPARATE, legitimate behaviour this test isn't
        about."""
        mover = self._make_line("stw-pin-mover", "back", 10)
        pinned_line = self._make_line(
            "stw-pin-other", "back", 20, pinned=True)
        self.assertTrue(pinned_line.pinned, "fixture sanity check")

        mover.action_move(wall="front", run_seq=0)

        self.assertTrue(
            pinned_line.pinned,
            "action_move's rearrange must never reset another line's "
            "pinned flag",
        )

    def test_pinned_flag_survives_when_pinned_line_itself_is_in_run(self):
        """Same as above but pins the line that STAYS on the run being
        repacked (the more likely real-world touch scenario: a rep
        pins one cabinet, then sends a neighbour to a different
        wall)."""
        pinned_line = self._make_line(
            "stw-pin-stays", "back", 10, pinned=True)
        mover = self._make_line("stw-pin-leaves", "back", 20)

        mover.action_move(wall="front", run_seq=0)

        self.assertTrue(pinned_line.pinned)
