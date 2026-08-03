# SPDX-License-Identifier: LGPL-3.0-only
"""SBK-048 (2026-08-03) — corner-absorption regression pin for
`SouthbrookKitchenDesignLine._write_then_rearrange`
(models/kitchen_design.py, ~line 5363), reached through the public
`action_move(wall, run_seq)` entry point (~line 5505).

THE BUG. Moving a cabinet to the LEFT wall in the 3D configurator failed
100% of the time with a bare "Odoo Server Error" (reproduced 12/12 against
the shipped U-Shape template, at its own 120x96 room AND an artificial
300x300 one — ruling out any genuine shortage of space). Root cause:
`_write_then_rearrange` refused any write that left `self.active` False,
but a canonical line goes inactive for two different reasons —

  1. it was ABSORBED into a corner unit (the engine inserts a real corner
     cabinet at an inside junction and supersedes the run-end cabinet(s)
     it consumes — ordinary cabinetry, not a failure), or
  2. it genuinely could not be placed (the room can't hold it).

Only case 2 should refuse. The old guard could not tell them apart and
refused BOTH, and the back-left junction rule
(`kitchen_layout_engine._CORNER_RESOLVE["back-left"]`) absorbs the lowest
run_seq member of whichever wall reaches it — exactly what a client
assigns a freshly-moved cabinet — so left-wall moves hit case 1 (a false
refusal) unconditionally.

THE FIX narrows the guard to `not self.active and not (res or {}).get(
"inserted")` — an absorption always comes with a real corner insertion
(see the "no false substitute" note below), so it now passes; a genuine
capacity failure still raises `kitchen_layout_engine.LayoutCapacityExceeded`,
caught immediately below and converted to the same actionable UserError.

THIS FILE proves both halves hold, across all four walls (nothing in the
existing suite exercised `action_move` onto "left" at all), and that a
refused move still rolls back completely. It is the "separately-tested
behaviour" `test_send_to_wall.py`'s own happy-path docstring points to
when it deliberately steers its "back" -> "front" move around cross-wall
corner absorption ("Adjacent-wall moves DO legitimately trigger corner
absorption ... that is correct, pre-existing, separately-tested
behaviour") — this is that file.

Fresh-DB safe: builds its own product templates/design/lines, no reliance
on demo data, the live catalog, or the shipped U-Shape template. Junction
placement rules already seeded on whatever DB this runs against are
neutralized (mirrors `test_auto_arrange_service.py`'s
`TestAutoArrangeCornerRuleSkuResolution.setUp` isolation trick) so every
corner here resolves through the legacy SB-CORNER/SB-WALL-CORNER fallback
deterministically.

A note on what "inserted" actually proves, and a limit of this test: the
returned `res["inserted"]` count is `kitchen_layout_engine.
resolve_and_layout`'s own topological insert list — it goes non-zero the
moment the ENGINE decides a junction should have a corner, before the
Odoo layer has resolved a real product variant for it. If that variant
resolution ever failed (no SB-CORNER template, no coded variant), the ORM
loop silently `continue`s past the `Line.create(...)` for that corner
(models/kitchen_design.py, the "if not variant: ... continue" branch)
while `res["inserted"]` still reports non-zero — the guard would not
raise even though nothing was actually substituted for the absorbed
cabinet. This file does not rely on the counter alone: every corner-
absorption test below also asserts a REAL derived `cabinet_type=="corner"`
line exists, which is what actually matters to a designer. That gap
(`res["inserted"]` can be a soft-true with no real substitute on some
other DB where the corner catalog is missing/broken) is pre-existing
behaviour, not something this task's fix touches, and is noted here
rather than silently worked around.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "southbrook",
        "southbrook_kitchen_3d_configurator")
class TestSendToWallCornerAbsorption(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Isolation (mirrors test_auto_arrange_service.py's
        # TestAutoArrangeCornerRuleSkuResolution.setUp) — neutralize any
        # already-seeded junction placement rules so every corner in this
        # file resolves through the legacy SB-CORNER/SB-WALL-CORNER
        # fallback deterministically, regardless of catalog state on
        # whatever DB this runs against.
        Rule = cls.env["southbrook.placement.rule"]
        Rule.search([("anchor_class", "=", "junction")]).write(
            {"active": False})

        Template = cls.env["product.template"]
        for code, name, price in [("SB-CORNER", "Corner Base", 625.0),
                                  ("SB-WALL-CORNER", "Corner Wall", 295.0)]:
            if not Template.search([("default_code", "=", code)], limit=1):
                Template.create({"name": name, "default_code": code,
                                 "list_price": price})

        cls.partner = cls.env["res.partner"].create({
            "name": "Corner-Absorption Test Customer",
            "email": "kitchen3d.corner.absorb@southbrook.test",
        })
        cls.tmpl = cls.env["product.template"].create({
            "name": "Corner-Absorption Test Base Cabinet",
            "type": "consu",
            "sale_ok": True,
            "list_price": 400.0,
        })
        cls.tmpl.southbrook_is_cabinet = True
        cls.tmpl.southbrook_cabinet_type = "base"
        cls.product = cls.tmpl.product_variant_id

    def _design(self, room_w, room_d, room_h=96.0):
        return self.env["southbrook.kitchen.design"].create({
            "name": "Corner absorption test",
            "partner_id": self.partner.id,
            "room_width_in": room_w,
            "room_depth_in": room_d,
            "room_height_in": room_h,
        })

    def _line(self, design, layout_key, wall, run_seq, width_in=24.0):
        return self.env["southbrook.kitchen.design.line"].create({
            "design_id":      design.id,
            "product_id":     self.product.id,
            "price_unit":     400.0,
            "cabinet_type":   "base",
            "origin":         "configurator",
            "layout_role":    "canonical",
            "layout_key":     layout_key,
            "wall":           wall,
            "run_seq":        run_seq,
            "width_in":       width_in,
            "height_in":      34.5,
            "depth_in":       24.0,
            "x_position_in":  0.0,
            "y_position_in":  0.0,
            "z_position_in":  0.0,
        })

    def _corner_lines(self, design):
        return design.cabinet_line_ids.filtered(
            lambda l: l.cabinet_type == "corner"
            and l.layout_role == "derived")

    # ------------------------------------------------------------------
    # 1. THE REGRESSION ITSELF, and 3. all four walls.
    #
    # Each test below builds an inside corner that is genuinely ACTIVE
    # (both of its walls have a run reaching the shared corner cell),
    # sends a fresh cabinet onto whichever wall becomes the JUST-MOVED
    # cabinet's own low-end (run_seq 0) — reproducing the exact
    # mechanism that made the shipped bug wall-specific but
    # unconditional on that wall — and asserts the move SUCCEEDS with a
    # real corner substituted, instead of raising.
    # ------------------------------------------------------------------

    def test_move_onto_left_wall_absorbed_into_corner_succeeds(self):
        """THE regression this task exists to pin. back-left junction
        (kitchen_layout_engine._CORNER_RESOLVE["back-left"] =
        host_wall="back", other_wall="left", other_end="first"): a run
        on "back" already reaches the corner; the just-moved cabinet
        becomes "left"'s new lowest run_seq member — exactly the
        client's own behaviour the bug report describes — and must be
        absorbed as the junction's "other" side, not refused.
        """
        design = self._design(200.0, 200.0)
        self._line(design, "cab-anchor-back", "back", 0)
        survivor_back = self._line(design, "cab-survivor-back", "back", 20)
        survivor_left = self._line(design, "cab-survivor-left", "left", 20)
        mover = self._line(design, "cab-mover", "back", 5)

        res = mover.action_move(wall="left", run_seq=0)

        self.assertTrue(
            res.get("inserted"),
            "back-left junction must have resolved a real corner")
        self.assertTrue(
            self._corner_lines(design),
            "a real derived corner line must exist as the mover's "
            "substitute")
        self.assertEqual(mover.wall, "left")
        self.assertEqual(mover.run_seq, 0)
        self.assertFalse(
            mover.active,
            "the moved cabinet is the back-left junction's lowest-"
            "run_seq left-wall member — it is absorbed into the "
            "corner, not dropped, and this must not raise")
        # The whole point of case 1 vs case 2: cabinets that did NOT
        # sit at a run's absorbed end are untouched survivors.
        self.assertTrue(survivor_back.active)
        self.assertTrue(survivor_left.active)

    def test_move_onto_back_wall_absorbed_into_corner_succeeds(self):
        """Same back-left junction, opposite role: the moved cabinet
        becomes BACK's low-end (host) member instead of LEFT's.
        Included because the bug's own root cause ("a freshly moved
        cabinet always gets a low run_seq") is generic to whichever
        wall receives the move, not left-specific — this pins that
        "back" was never actually a separate code path from "left"
        within the same junction rule.
        """
        design = self._design(200.0, 200.0)
        self._line(design, "cab-anchor-left", "left", 0)
        survivor_left = self._line(design, "cab-survivor-left", "left", 20)
        survivor_back = self._line(design, "cab-survivor-back", "back", 20)
        mover = self._line(design, "cab-mover", "front", 5)

        res = mover.action_move(wall="back", run_seq=0)

        self.assertTrue(res.get("inserted"))
        self.assertTrue(self._corner_lines(design))
        self.assertEqual(mover.wall, "back")
        self.assertEqual(mover.run_seq, 0)
        self.assertFalse(mover.active)
        self.assertTrue(survivor_left.active)
        self.assertTrue(survivor_back.active)

    def test_move_onto_right_wall_absorbed_into_corner_succeeds(self):
        """back-right junction (_CORNER_RESOLVE["back-right"] =
        host_wall="right", other_wall="back", other_end="last"): the
        moved cabinet becomes RIGHT's low-end (host) member; the
        junction needs BACK's own run to reach the SAME cell from its
        high (far) end. A narrow room_width_in (48) with a single
        24in back-wall cabinet is exactly wide enough for that one
        cabinet to be simultaneously its run's first AND only (hence
        also last) member, landing inside the back-right cell — the
        simplest fixture that makes the junction genuinely active
        without needing a multi-cabinet run.
        """
        design = self._design(48.0, 200.0)
        anchor_back = self._line(design, "cab-anchor-back", "back", 0)
        survivor_right = self._line(
            design, "cab-survivor-right", "right", 20)
        mover = self._line(design, "cab-mover", "front", 5)

        res = mover.action_move(wall="right", run_seq=0)

        self.assertTrue(res.get("inserted"))
        self.assertTrue(self._corner_lines(design))
        self.assertEqual(mover.wall, "right")
        self.assertEqual(mover.run_seq, 0)
        self.assertFalse(mover.active)
        self.assertFalse(
            anchor_back.active,
            "back's own run-end member is the junction's OTHER side "
            "and must be absorbed too")
        self.assertTrue(survivor_right.active)

    def test_move_onto_front_wall_absorbed_into_corner_succeeds(self):
        """front-left junction (_CORNER_RESOLVE["front-left"] =
        host_wall="front", other_wall="left", other_end="last"):
        mirrors the "right" test above on the opposite pair of walls —
        the moved cabinet becomes FRONT's low-end (host) member; a
        narrow room_depth_in (50) makes the single left-wall cabinet
        reach the same cell from its high end.
        """
        design = self._design(200.0, 50.0)
        anchor_left = self._line(design, "cab-anchor-left", "left", 0)
        survivor_front = self._line(
            design, "cab-survivor-front", "front", 20)
        mover = self._line(design, "cab-mover", "right", 5)

        res = mover.action_move(wall="front", run_seq=0)

        self.assertTrue(res.get("inserted"))
        self.assertTrue(self._corner_lines(design))
        self.assertEqual(mover.wall, "front")
        self.assertEqual(mover.run_seq, 0)
        self.assertFalse(mover.active)
        self.assertFalse(anchor_left.active)
        self.assertTrue(survivor_front.active)

    # ------------------------------------------------------------------
    # 2. The guard still works, and 4. rollback integrity.
    # ------------------------------------------------------------------

    def test_genuinely_impossible_move_still_refused_and_rolled_back(self):
        """A move that genuinely cannot be laid out must still refuse
        — via the mechanism the SBK-048 fix's own docstring names as
        the real detector for this case:
        `kitchen_layout_engine.LayoutCapacityExceeded`, caught by
        `_write_then_rearrange`'s except clause and re-raised as a
        UserError. A 24in cabinet moved onto a wall only 12in long
        (the model's own room-dimension floor, ROOM_DEPTH_MIN_IN)
        cannot possibly fit, and no corner can rescue it — no OTHER
        wall carries a cabinet at all, so no junction is even
        topologically possible here. The whole action_move call must
        roll back completely: wall/run_seq/active exactly as they
        were, nothing else in the design touched.
        """
        design = self._design(100.0, 12.0)
        mover = self._line(design, "cab-mover", "back", 0)
        wall_before, run_seq_before = mover.wall, mover.run_seq

        with self.assertRaises(UserError):
            mover.action_move(wall="left", run_seq=0)

        self.assertEqual(mover.wall, wall_before,
                         "a refused move must leave wall untouched")
        self.assertEqual(mover.run_seq, run_seq_before,
                         "a refused move must leave run_seq untouched")
        self.assertTrue(
            mover.active,
            "a refused move must not leave the cabinet archived")
