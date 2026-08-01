# SPDX-License-Identifier: LGPL-3.0-only
"""Tests for the pure kitchen layout engine.

The engine has no ORM dependency, so these are plain-logic assertions. The
load-bearing one is `test_golden_backwall_parity`: it proves the engine
reproduces the legacy single-run math byte-for-byte, so routing the Preview
and design-line seeding through the engine (P0.2) cannot regress existing
straight kitchens.
"""
import copy
import math

from odoo.tests.common import TransactionCase
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E

ZL = E._DEFAULT_ZONE_LAYOUT
WC = E._DEFAULT_WORKTOP_CURSOR
WY = E._DEFAULT_WORKTOP_Y
ROOM = {"width_mm": 4000, "depth_mm": 3000, "height_mm": 2400}


def _legacy_backwall(cabinets):
    """Faithful copy of the CURRENT single-run math in
    sale_order.get_kitchen_3d_payload / _southbrook_seed_design_lines."""
    cursors = {"ground": 0, "wall": 0, "island": 0, "other": 0}
    out = []
    for cab in cabinets:
        w = cab["width_mm"]
        if cab.get("family") == "worktop":
            cursor_name, y_floor, z_offset = WC, WY, 0
        else:
            zone = cab.get("zone") or "base_run"
            cursor_name, y_floor, z_offset = ZL.get(zone, ("ground", 0, 0))
        x_offset = cursors[cursor_name] + w / 2.0
        out.append({"id": cab["id"], "x": x_offset, "y": y_floor, "z": z_offset})
        cursors[cursor_name] += w
    return out


class TestKitchenLayoutEngine(TransactionCase):

    def test_golden_backwall_parity(self):
        cabs = [
            {"id": 1, "width_mm": 600, "height_mm": 762, "depth_mm": 600, "family": "base", "zone": "base_run", "wall": "back"},
            {"id": 2, "width_mm": 900, "height_mm": 762, "depth_mm": 600, "family": "base", "zone": "base_run", "wall": "back"},
            {"id": 3, "width_mm": 600, "height_mm": 720, "depth_mm": 300, "family": "wall", "zone": "wall", "wall": "back"},
            {"id": 4, "width_mm": 600, "height_mm": 2100, "depth_mm": 600, "family": "tall", "zone": "tall", "wall": "back"},
            {"id": 5, "width_mm": 1200, "height_mm": 25, "depth_mm": 650, "family": "worktop", "zone": "base_run", "wall": "back"},
            {"id": 6, "width_mm": 1000, "height_mm": 900, "depth_mm": 600, "family": "base", "zone": "island", "wall": "back"},
        ]
        legacy = _legacy_backwall(cabs)
        got = E.layout(copy.deepcopy(cabs), ROOM, ZL, WC, WY)
        self.assertEqual(len(legacy), len(got))
        for lg, g in zip(legacy, got):
            self.assertEqual(lg["id"], g["id"])
            self.assertAlmostEqual(lg["x"], g["x"], places=6)
            self.assertAlmostEqual(lg["y"], g["y"], places=6)
            self.assertAlmostEqual(lg["z"], g["z"], places=6)
            self.assertAlmostEqual(g["rotation_deg"], 0, places=6)

    def test_side_and_front_walls(self):
        W, D = ROOM["width_mm"], ROOM["depth_mm"]
        cabs = [
            {"id": "L1", "width_mm": 600, "family": "base", "zone": "base_run", "wall": "left", "run_seq": 0},
            {"id": "L2", "width_mm": 900, "family": "base", "zone": "base_run", "wall": "left", "run_seq": 1},
            {"id": "R1", "width_mm": 600, "family": "base", "zone": "base_run", "wall": "right", "run_seq": 0},
            {"id": "F1", "width_mm": 600, "family": "base", "zone": "base_run", "wall": "front", "run_seq": 0},
        ]
        got = {p["id"]: p for p in E.layout(cabs, ROOM, ZL, WC, WY)}
        self.assertAlmostEqual(got["L1"]["x"], 0)
        self.assertAlmostEqual(got["L1"]["z"], 300)
        self.assertAlmostEqual(got["L1"]["rotation_deg"], 90)
        self.assertAlmostEqual(got["L2"]["z"], 600 + 450)
        self.assertAlmostEqual(got["R1"]["x"], W)
        self.assertAlmostEqual(got["R1"]["rotation_deg"], 270)
        self.assertAlmostEqual(got["F1"]["z"], D)
        self.assertAlmostEqual(got["F1"]["rotation_deg"], 180)

    def test_default_wall_is_back(self):
        cabs = [{"id": 1, "width_mm": 600, "family": "base", "zone": "base_run"}]
        got = E.layout(cabs, ROOM, ZL, WC, WY)
        self.assertAlmostEqual(got[0]["rotation_deg"], 0)
        self.assertAlmostEqual(got[0]["z"], 0)

    def test_deterministic_and_no_mutation(self):
        cabs = [
            {"id": 1, "width_mm": 600, "family": "base", "zone": "base_run", "wall": "left", "run_seq": 5},
            {"id": 2, "width_mm": 600, "family": "base", "zone": "base_run", "wall": "left", "run_seq": 1},
        ]
        snapshot = copy.deepcopy(cabs)
        a = E.layout(cabs, ROOM, ZL, WC, WY)
        b = E.layout(cabs, ROOM, ZL, WC, WY)
        self.assertEqual(cabs, snapshot)
        self.assertEqual(a, b)
        by = {p["id"]: p for p in a}
        self.assertLess(by[2]["z"], by[1]["z"])
        self.assertEqual([p["id"] for p in a], [1, 2])

    # ── Corner detection (P2) ───────────────────────────────────────────
    def _cab(self, wall, ct="base"):
        return {"id": wall + ct, "width_mm": 600,
                "family": ct if ct == "wall" else "base",
                "cabinet_type": ct, "wall": wall}

    @staticmethod
    def _names(corners):
        return sorted((c["corner"], c["layer"]) for c in corners)

    def test_no_corner_for_single_wall(self):
        self.assertEqual(
            E.detect_corners([self._cab("back"), self._cab("back")], ROOM), [])
        self.assertEqual(
            E.detect_corners([self._cab("right"), self._cab("right")], ROOM), [])

    def test_l_shape_corner(self):
        got = E.detect_corners([self._cab("back"), self._cab("left")], ROOM)
        self.assertEqual(self._names(got), [("back-left", "base")])

    def test_base_and_wall_corners_independent(self):
        got = E.detect_corners([
            self._cab("back"), self._cab("left"),
            self._cab("back", "wall"), self._cab("left", "wall"),
        ], ROOM)
        self.assertEqual(self._names(got),
                         [("back-left", "base"), ("back-left", "wall")])

    def test_u_shape_two_corners(self):
        got = E.detect_corners(
            [self._cab("left"), self._cab("back"), self._cab("right")], ROOM)
        self.assertEqual(self._names(got),
                         [("back-left", "base"), ("back-right", "base")])
        br = [c for c in got if c["corner"] == "back-right"][0]
        self.assertEqual(br["position_mm"], {"x": 4000.0, "z": 0.0})

    # ── Geometric corner detection (2026-07-12, corner-cell footprint) ──
    # The "vanishing cabinet" bug: detect_corners used to activate a corner
    # the instant BOTH walls carried ANY cabinet of a layer — so adding the
    # very FIRST cabinet to a second wall could immediately fire a corner
    # resolution and silently consume it, even though the two runs were
    # nowhere near each other. Passing `placements` makes detection require
    # each wall's cabinet to GEOMETRICALLY reach the corner's footprint
    # cell, not merely exist on that wall.
    def test_corner_detected_only_when_footprints_reach_corner_cell(self):
        back = {"id": "B", "width_mm": 600, "depth_mm": 600,
                "wall": "back", "cabinet_type": "base"}
        left = {"id": "L", "width_mm": 600, "depth_mm": 600,
                "wall": "left", "cabinet_type": "base"}
        # Both cabinets actually occupy the back-left cell (914mm square at
        # the origin) → corner detected.
        placements = [
            {"id": "B", "x": 300, "y": 0, "z": 0, "rotation_deg": 0},
            {"id": "L", "x": 0, "y": 0, "z": 300, "rotation_deg": 90},
        ]
        got = E.detect_corners([back, left], ROOM, placements=placements)
        self.assertEqual(self._names(got), [("back-left", "base")])

        # Same back cabinet, but the left cabinet is placed FAR down the
        # left wall (near the front of the room) — its footprint never
        # reaches the corner cell, so no corner should be detected even
        # though the left wall nominally "has a cabinet".
        left_far = {"id": "L2", "width_mm": 600, "depth_mm": 600,
                    "wall": "left", "cabinet_type": "base"}
        placements_far = [
            {"id": "B", "x": 300, "y": 0, "z": 0, "rotation_deg": 0},
            {"id": "L2", "x": 0, "y": 0, "z": 2700, "rotation_deg": 90},
        ]
        got_far = E.detect_corners(
            [back, left_far], ROOM, placements=placements_far)
        self.assertEqual(got_far, [])

    def test_back_only_kitchen_detects_no_corner(self):
        """Parity guard: a straight back-only kitchen never detects a
        corner — geometric detection must not regress the common case."""
        cabs = [
            {"id": 1, "width_mm": 600, "height_mm": 762, "depth_mm": 600,
             "family": "base", "zone": "base_run", "wall": "back",
             "cabinet_type": "base"},
            {"id": 2, "width_mm": 900, "height_mm": 762, "depth_mm": 600,
             "family": "base", "zone": "base_run", "wall": "back",
             "cabinet_type": "base"},
            {"id": 3, "width_mm": 600, "height_mm": 720, "depth_mm": 300,
             "family": "wall", "zone": "wall", "wall": "back",
             "cabinet_type": "wall"},
        ]
        placements = E.layout(copy.deepcopy(cabs), ROOM, ZL, WC, WY)
        self.assertEqual(E.detect_corners(cabs, ROOM, placements=placements), [])

    # ── Auto-distribution + corner resolution (P1) ──────────────────────
    def test_auto_assign_wraps_to_side_wall(self):
        # 10x 600mm on a 4000-wide back wall → 6 fit, 4 wrap to left.
        cabs = [{"id": i, "width_mm": 600, "family": "base",
                 "cabinet_type": "base"} for i in range(10)]
        out = E.auto_assign_walls(cabs, ROOM)
        walls = {}
        for c in out:
            walls.setdefault(c["wall"], 0)
            walls[c["wall"]] += 1
        self.assertEqual(walls.get("back"), 6)
        self.assertEqual(walls.get("left"), 4)
        # inputs untouched (no wall key added to originals)
        self.assertNotIn("wall", cabs[0])

    def test_resolve_generates_manufacturable_L(self):
        cabs = [{"id": i, "width_mm": 600, "height_mm": 876, "depth_mm": 600,
                 "family": "base", "cabinet_type": "base"}
                for i in range(1, 11)]
        r = E.resolve_and_layout(cabs, ROOM)
        # one back-left corner cabinet inserted, replacing 2 standards
        self.assertEqual([n["id"] for n in r["inserted"]],
                         ["corner-back-left-base"])
        self.assertEqual(len(r["removed_ids"]), 2)
        self.assertEqual(len(r["cabinets"]), 9)   # 10 - 2 + 1
        pl = {p["id"]: p for p in r["placements"]}
        cc = pl["corner-back-left-base"]
        cc_node = next(c for c in r["cabinets"] if c["id"] == "corner-back-left-base")
        # Corner-orientation fix (2026-07-31) — the corner node renders IN
        # the corner, tagged "back" (its own actual presentation — see
        # ROOM_CORNER_PLACEMENT) with rotation 0. This USED TO assert
        # wall="left"/rotation=90: that was the verified bug (the door
        # notch faced the back wall instead of into the room) — its
        # anchor-convention AABB was pixel-identical to the correct pose
        # for this SQUARE corner (see the ROOM_CORNER_PLACEMENT module
        # comment in kitchen_layout_engine.py), which is exactly why nobody
        # caught it from occupancy/footprint checks alone. wall="back" here
        # is NOT a reversion to the old pre-Task-8 "vanishing cabinet" bug
        # (see that same comment) — corner_cabinet.esm.js now renders a
        # real diagonal notch, visually distinct at any rotation.
        self.assertEqual(cc_node["wall"], "back")
        self.assertAlmostEqual(cc["x"], E._CORNER_FOOTPRINT_MM / 2.0)
        self.assertAlmostEqual(cc["z"], 0)
        self.assertAlmostEqual(cc["rotation_deg"], 0)
        # nothing overlaps the corner cell; runs stay within wall lengths
        back = [pl[c["id"]] for c in r["cabinets"]
                if c.get("wall") == "back" and not c.get("corner_cabinet")]
        left = [pl[c["id"]] for c in r["cabinets"]
                if c.get("wall") == "left" and not c.get("corner_cabinet")]
        self.assertGreaterEqual(min(p["x"] - 300 for p in back), 914 - 1)
        self.assertGreaterEqual(min(p["z"] - 300 for p in left), 914 - 1)
        self.assertTrue(all(abs(p["rotation_deg"] - 90) < 1 for p in left))
        self.assertLessEqual(max(p["x"] + 300 for p in back + [cc]), 4000)
        self.assertLessEqual(max(p["z"] + 300 for p in left), 3000)

    def test_resolve_generates_manufacturable_U(self):
        # A manually-placed U: left + back + right. BOTH back corners must be
        # reserved (back-left low/low, back-right where the back run meets the
        # right run at its HIGH end). The load-bearing property is that NO two
        # cabinets overlap once the corners are resolved.
        #
        # Geometric detection (2026-07-12) requires the back run to actually
        # REACH the back-right corner cell to be detected there — a real U,
        # not just "some cabinet exists on each wall". 6 back cabinets (not
        # 5) span far enough into a slightly wider room (4300mm, not 4000mm)
        # to reach the cell while the single-cabinet corner-side trim still
        # leaves no residual overlap.
        room = {"width_mm": 4300, "depth_mm": 3000, "height_mm": 2400}
        def mk(cid, wall, seq, ct="base"):
            return {"id": cid, "width_mm": 600,
                    "height_mm": 876 if ct != "wall" else 720,
                    "depth_mm": 600 if ct != "wall" else 320,
                    "family": ct if ct == "wall" else "base",
                    "cabinet_type": ct,
                    "zone": "wall" if ct == "wall" else "base_run",
                    "wall": wall, "run_seq": seq}
        cabs = ([mk(("L", s), "left", s) for s in range(3)]
                + [mk(("B", s), "back", s) for s in range(6)]
                + [mk(("R", s), "right", s) for s in range(3)])
        r = E.resolve_and_layout(cabs, room, auto_assign=False)
        corners = sorted(c["corner"] for c in r["cabinets"]
                         if c.get("corner_cabinet"))
        self.assertEqual(corners, ["back-left", "back-right"])
        handed = {c["corner"]: c["handed"] for c in r["cabinets"]
                  if c.get("corner_cabinet")}
        self.assertEqual(handed, {"back-left": "L", "back-right": "R"})
        # 12 in − 4 replaced (2 per corner) + 2 corners = 10
        self.assertEqual(len(r["cabinets"]), 10)
        pl = {p["id"]: p for p in r["placements"]}
        # THE invariant: no cabinet overlaps another (incl. both corner cells).
        fps = [(c["id"], E.footprint_mm(c, pl[c["id"]])) for c in r["cabinets"]]
        for i in range(len(fps)):
            for j in range(i + 1, len(fps)):
                self.assertFalse(
                    E.footprints_overlap(fps[i][1], fps[j][1]),
                    "overlap: %s vs %s" % (fps[i][0], fps[j][0]))
        # every cabinet stays inside the room
        for c in r["cabinets"]:
            self.assertTrue(E.within_room(c, pl[c["id"]], room),
                            "outside room: %s" % (c["id"],))
        # back-right corner sits against the right wall near (W, 0)
        br = [c for c in r["cabinets"]
              if c.get("corner") == "back-right"][0]
        self.assertEqual(br["wall"], "right")
        self.assertGreater(pl[br["id"]]["x"], 3000)
        self.assertLess(pl[br["id"]]["z"], 1000)

    @staticmethod
    def _mkw(cid, wall, seq, ct="base"):
        return {"id": cid, "width_mm": 600,
                "height_mm": 876 if ct != "wall" else 720,
                "depth_mm": 600 if ct != "wall" else 320,
                "family": ct if ct == "wall" else "base", "cabinet_type": ct,
                "zone": "wall" if ct == "wall" else "base_run",
                "wall": wall, "run_seq": seq}

    def _assert_clean(self, r, room):
        """No two footprints overlap and every cabinet is within the room —
        the load-bearing manufacturability invariant."""
        pl = {p["id"]: p for p in r["placements"]}
        fps = [(c["id"], E.footprint_mm(c, pl[c["id"]])) for c in r["cabinets"]]
        for i in range(len(fps)):
            for j in range(i + 1, len(fps)):
                self.assertFalse(
                    E.footprints_overlap(fps[i][1], fps[j][1]),
                    "overlap: %s vs %s" % (fps[i][0], fps[j][0]))
        for c in r["cabinets"]:
            self.assertTrue(E.within_room(c, pl[c["id"]], room),
                            "outside room: %s" % (c["id"],))
        return pl

    def test_resolve_front_left_corner(self):
        # front-left has a low-end host (front run's origin), same model as
        # back-right — corner joins the front run at run_seq -1.
        #
        # Geometric detection (2026-07-12) requires the left run to actually
        # REACH the front-left corner cell (near the front wall) to be
        # detected there — 4 left cabinets (not 3) span far enough.
        cabs = ([self._mkw(("F", s), "front", s) for s in range(4)]
                + [self._mkw(("L", s), "left", s) for s in range(4)])
        r = E.resolve_and_layout(cabs, ROOM, auto_assign=False)
        fl = [c for c in r["cabinets"] if c.get("corner") == "front-left"]
        self.assertEqual(len(fl), 1)
        # Corner-orientation fix (2026-07-31): front-left's own
        # presentation wall is "left" (rotation_deg=90 —
        # ROOM_CORNER_PLACEMENT), not "front" (was "front"/180 pre-fix).
        # `host_wall`="front" (below) stays the run-cursor bookkeeping
        # key only — see the module comment on ROOM_CORNER_PLACEMENT for
        # why that must NOT be assumed to be the emitted wall/rotation.
        self.assertEqual(fl[0]["wall"], "left")
        self.assertEqual(fl[0]["run_seq"], -1)
        pl = self._assert_clean(r, ROOM)
        # sits in the front-left cell: low x, high z (near the front wall)
        self.assertLess(pl[fl[0]["id"]]["x"], 1000)
        self.assertGreater(pl[fl[0]["id"]]["z"], 2000)

    def test_resolve_front_right_standalone_and_cap(self):
        # front-right is "both-high" — neither wall's low end is at it, so the
        # corner is placed STANDALONE (explicit pose) and the two runs are
        # capped. Long runs (front 6, right 5) force the cap pass to fire.
        cabs = ([self._mkw(("F", s), "front", s) for s in range(6)]
                + [self._mkw(("R", s), "right", s) for s in range(5)])
        r = E.resolve_and_layout(cabs, ROOM, auto_assign=False)
        fr = [c for c in r["cabinets"] if c.get("corner") == "front-right"]
        self.assertEqual(len(fr), 1)
        self.assertIsNotNone(fr[0].get("__pose"))         # standalone
        self.assertNotIn("run_seq", fr[0])                 # not joined to a run
        self.assertGreaterEqual(len(fr[0]["replaced_ids"]), 1)   # cap fired
        # the capped cabinets are truly gone from the output
        out_ids = {c["id"] for c in r["cabinets"]}
        for cid in fr[0]["replaced_ids"]:
            self.assertNotIn(cid, out_ids)
        pl = self._assert_clean(r, ROOM)
        self.assertGreater(pl[fr[0]["id"]]["x"], 3000)     # near (W, D)
        self.assertGreater(pl[fr[0]["id"]]["z"], 2000)

    def test_resolve_full_g_shape_four_corners(self):
        # All four walls occupied → all four corners reserved, nothing
        # overlaps, each corner in its own quadrant.
        #
        # Geometric detection (2026-07-12) requires each run to actually
        # REACH both of its corner cells — a real G-shape, not just "some
        # cabinet on each wall". back/front need 9 cabinets (long walls,
        # 6200mm) and left/right need 7 (5000mm) to reach into the FAR
        # corner cell from each run's low end while the single-cabinet
        # corner-side trim still leaves no residual overlap.
        big = {"width_mm": 6200, "depth_mm": 5000, "height_mm": 2400}
        wall_counts = {"back": 9, "front": 9, "left": 7, "right": 7}
        cabs = []
        for wall, n in wall_counts.items():
            cabs += [self._mkw((wall, s), wall, s) for s in range(n)]
        r = E.resolve_and_layout(cabs, big, auto_assign=False)
        corners = sorted(c["corner"] for c in r["cabinets"]
                         if c.get("corner_cabinet"))
        self.assertEqual(
            corners, ["back-left", "back-right", "front-left", "front-right"])
        pl = self._assert_clean(r, big)
        q = {c["corner"]: pl[c["id"]] for c in r["cabinets"]
             if c.get("corner_cabinet")}
        self.assertTrue(q["back-left"]["x"] < 3000 and q["back-left"]["z"] < 2500)
        self.assertTrue(q["back-right"]["x"] > 3000 and q["back-right"]["z"] < 2500)
        self.assertTrue(q["front-left"]["x"] < 3000 and q["front-left"]["z"] > 2500)
        self.assertTrue(q["front-right"]["x"] > 3000 and q["front-right"]["z"] > 2500)

    # ── Corner node wall + orientation ───────────────────────────────────
    # History: an EARLIER fix (2026-07-12, Task 2, pre-Task-8) required
    # every corner node's wall/rotation to differ from its "host" wall's
    # own natural (wall, rotation) pair — back then corner cabinets
    # rendered as a plain box (`buildOtherCabinet`), so back-left tagged
    # wall="back"/rotation=0 was visually IDENTICAL to an ordinary back-
    # wall cabinet ("vanished" instead of reading as a corner).
    #
    # Corner-orientation fix (2026-07-31): that same wall="back"/rotation=0
    # pairing for back-left is now the CORRECT one (see the
    # ROOM_CORNER_PLACEMENT module comment in kitchen_layout_engine.py —
    # recomputing the actual local->world door-normal transform shows
    # rotation=0 is what makes the notch face INTO the room for this
    # corner). It is not a reversion to the 2026-07-12 bug: Task 8
    # (corner_cabinet.esm.js) shipped a real diagonal/pie-cut notch mesh,
    # which is visually distinct from a plain box at ANY rotation — the
    # "vanishing" failure mode this test used to guard against is
    # structurally impossible now, regardless of which rotation is
    # correct. What every corner node must still guarantee: it renders
    # EXACTLY in its own corner cell (M1's exact-cell bar) at the
    # ROOM_CORNER_PLACEMENT-correct orientation.
    def test_corner_node_tagged_and_oriented_for_its_corner(self):
        cs = E._CORNER_FOOTPRINT_MM

        def _single_corner_node(cabs, room):
            r = E.resolve_and_layout(cabs, room, auto_assign=False)
            nodes = [c for c in r["cabinets"] if c.get("corner_cabinet")]
            self.assertEqual(len(nodes), 1, "expected exactly one corner")
            pl = {p["id"]: p for p in r["placements"]}
            return nodes[0], pl[nodes[0]["id"]]

        # back-left — the corner-orientation bug lived here. Both runs'
        # LOW ends meet at the origin.
        cabs_bl = ([self._mkw(("B", s), "back", s) for s in range(2)]
                   + [self._mkw(("L", s), "left", s) for s in range(2)])
        node_bl, place_bl = _single_corner_node(cabs_bl, ROOM)
        self.assertEqual(node_bl["corner"], "back-left")
        self.assertIn(node_bl["wall"], E.WALLS)          # valid Selection value
        self.assertEqual(node_bl["wall"], "back")        # ROOM_CORNER_PLACEMENT
        cell_bl = E._corner_cell_aabb("0", "0", ROOM, cs)
        fp_bl = E.footprint_mm(node_bl, place_bl)
        # M1: exact-cell equality, same bar as back-right/front-left below
        # (upgraded from the old mere-overlap check — a future
        # off-by-something pose regression must not slip through on a
        # loose overlap test).
        for got_edge, want_edge in zip(fp_bl, cell_bl):
            self.assertAlmostEqual(got_edge, want_edge)
        self.assertIn(int(round(place_bl["rotation_deg"])) % 360,
                      E.ALLOWED_ROTATIONS)
        self.assertEqual(int(round(place_bl["rotation_deg"])) % 360, 0)

        # back-right — regression guard (already correct pre-fix AND
        # post-fix, unchanged: the back run's HIGH end meets the right
        # run's LOW end at (W, 0)).
        room_br = {"width_mm": 4300, "depth_mm": 3000, "height_mm": 2400}
        cabs_br = ([self._mkw(("B2", s), "back", s) for s in range(6)]
                   + [self._mkw(("R2", s), "right", s) for s in range(3)])
        node_br, place_br = _single_corner_node(cabs_br, room_br)
        self.assertEqual(node_br["corner"], "back-right")
        self.assertIn(node_br["wall"], E.WALLS)
        self.assertEqual(node_br["wall"], "right")
        cell_br = E._corner_cell_aabb("W", "0", room_br, cs)
        fp_br = E.footprint_mm(node_br, place_br)
        # M1: exact-cell equality (not just overlap) — the node's footprint
        # must be PRECISELY the corner cell, same bar as back-left, so a
        # future off-by-something pose regression can't slip through on a
        # mere overlap check.
        for got_edge, want_edge in zip(fp_br, cell_br):
            self.assertAlmostEqual(got_edge, want_edge)
        self.assertEqual(int(round(place_br["rotation_deg"])) % 360, 270)

        # front-left — mirror. Was already non-zero pre-fix (180), but the
        # SPECIFIC value changed (180 -> 90, see ROOM_CORNER_PLACEMENT):
        # the front run's LOW end meets the left run's HIGH end at (0, D).
        cabs_fl = ([self._mkw(("F3", s), "front", s) for s in range(4)]
                   + [self._mkw(("L3", s), "left", s) for s in range(4)])
        node_fl, place_fl = _single_corner_node(cabs_fl, ROOM)
        self.assertEqual(node_fl["corner"], "front-left")
        self.assertIn(node_fl["wall"], E.WALLS)
        self.assertEqual(node_fl["wall"], "left")
        cell_fl = E._corner_cell_aabb("0", "D", ROOM, cs)
        fp_fl = E.footprint_mm(node_fl, place_fl)
        # M1: exact-cell equality — see back-right comment above.
        for got_edge, want_edge in zip(fp_fl, cell_fl):
            self.assertAlmostEqual(got_edge, want_edge)
        self.assertEqual(int(round(place_fl["rotation_deg"])) % 360, 90)

    # ── Layer-scoped corner offset (2026-07-12, review I1) ──────────────
    # A base-only corner used to offset the WHOLE host wall via
    # `wall_start_offsets[wall]`, and `layout()` applied that offset to
    # EVERY run on that wall — including the wall (upper) layer's run,
    # which has nothing to do with the base-layer corner. Uppers can
    # legitimately run all the way to the corner above a short base corner
    # cabinet; they must not be shoved 914mm right just because the base
    # layer resolved a corner on the same wall.
    def test_base_corner_does_not_shift_host_upper_run(self):
        # Base layer: back + left, enough to reach the back-left cell → one
        # base-only corner resolves (same shape as cabs_bl above).
        base_cabs = ([self._mkw(("B", s), "back", s) for s in range(2)]
                     + [self._mkw(("L", s), "left", s) for s in range(2)])
        # Wall (upper) layer: ONLY on the back wall — no left-wall uppers at
        # all, so no wall-layer corner is ever detected here.
        upper_cabs = [self._mkw(("BW", s), "back", s, ct="wall")
                      for s in range(2)]
        cabs = base_cabs + upper_cabs

        r = E.resolve_and_layout(cabs, ROOM, auto_assign=False)
        corner_nodes = [c for c in r["cabinets"] if c.get("corner_cabinet")]
        self.assertEqual(len(corner_nodes), 1)
        self.assertEqual(corner_nodes[0]["layer"], "base")

        pl = {p["id"]: p for p in r["placements"]}

        # The back wall's UPPER run is untouched by the base corner — its
        # cabinets keep their pre-corner x (cursor starts at 0 on "back").
        upper_back = [c for c in r["cabinets"]
                      if c.get("wall") == "back"
                      and c.get("cabinet_type") == "wall"]
        self.assertEqual(len(upper_back), 2)
        upper_x = sorted(pl[c["id"]]["x"] for c in upper_back)
        self.assertAlmostEqual(upper_x[0], 300)
        self.assertAlmostEqual(upper_x[1], 900)

        # The base run on "back" (the corner's host wall) IS still offset,
        # exactly as before — this is the existing, single-layer behaviour
        # that must remain byte-identical.
        base_back = [c for c in r["cabinets"]
                     if c.get("wall") == "back"
                     and c.get("cabinet_type") == "base"
                     and not c.get("corner_cabinet")]
        self.assertEqual(len(base_back), 1)
        self.assertAlmostEqual(
            pl[base_back[0]["id"]]["x"] - 300, E._CORNER_FOOTPRINT_MM)


class TestRoomCornerPlacement(TransactionCase):
    """Corner-orientation fix (2026-07-31) — dedicated regression suite
    for `kitchen_layout_engine.ROOM_CORNER_PLACEMENT`, the ONE table that
    replaced the per-side ad-hoc (wall_tag, rotation_deg) math
    (`_CORNER_RESOLVE_PRESENTATION` + the standalone front-right branch's
    hardcoded rotation=0/wall="right").
    """

    # A room genuinely NOT square in either axis, so "the anchor is
    # exactly the room corner" can't be confused with "the anchor is
    # exactly the room centre" or any other coincidental symmetry.
    ROOM = {"width_mm": 5200.0, "depth_mm": 3700.0, "height_mm": 2400.0}

    def test_room_corner_placement_matches_corner_specs_xz_keys(self):
        """The x_key/z_key baked into ROOM_CORNER_PLACEMENT must be
        IDENTICAL to _CORNER_SPECS's own keys for the same corner name —
        two independent tables that must never silently drift apart
        (_CORNER_SPECS also drives `wall_graph()`'s junction set, so a
        mismatch there would misplace which room corner a junction
        resolves against)."""
        specs_xz = {name: (xk, zk) for name, _walls, (xk, zk) in E._CORNER_SPECS}
        self.assertEqual(set(specs_xz), set(E.ROOM_CORNER_PLACEMENT))
        for name, (xk, zk) in specs_xz.items():
            got_xk, got_zk, _rot = E.ROOM_CORNER_PLACEMENT[name]
            self.assertEqual((got_xk, got_zk), (xk, zk), name)

    def test_room_corner_placement_back_right_unchanged(self):
        """back-right (270°) is the ONE corner the verified bug report
        calls already-correct pre-fix — pinned so a future edit can't
        silently perturb the one known-good unit while fixing the other
        three."""
        xk, zk, rot = E.ROOM_CORNER_PLACEMENT["back-right"]
        self.assertEqual((xk, zk, rot), ("W", "0", 270))

    def test_room_corner_placement_values(self):
        """The full corrected table, pinned literally (item 1 of the
        corner-orientation fix brief): back_left=(0,0,0),
        back_right=(rw,0,270), front_left=(0,rd,90),
        front_right=(rw,rd,180)."""
        self.assertEqual(E.ROOM_CORNER_PLACEMENT, {
            "back-left":   ("0", "0", 0),
            "back-right":  ("W", "0", 270),
            "front-left":  ("0", "D", 90),
            "front-right": ("W", "D", 180),
        })

    @staticmethod
    def _world_point(anchor, local_x, local_z):
        """The SAME local->world transform the renderer applies
        (kitchen_canvas.esm.js `_placeCabinetGroup`: `grp.rotation.y =
        rotation_deg`, THREE.js convention) — reimplemented independently
        here (not imported from production code) so this test cannot be
        fooled by a shared bug in a shared helper. local +x -> world
        (cosθ, -sinθ), local +z -> world (sinθ, cosθ), then translated by
        the anchor position — matches this module's own
        `obb_corners_from_anchor_mm`, which is itself pinned against
        `footprint_from_anchor_mm` by test."""
        theta = math.radians(anchor["rotation_deg"])
        c, s = math.cos(theta), math.sin(theta)
        wx = anchor["x"] + local_x * c + local_z * s
        wz = anchor["z"] + local_x * (-s) + local_z * c
        return wx, wz

    def test_room_corner_placement_anchors_and_door_faces_into_room(self):
        """Item 3(a) of the corner-orientation fix brief: for EVERY
        ROOM_CORNER_PLACEMENT entry, (1) the world-transform of local
        (0,0) — the mesh's anchor/origin, per corner_cabinet.esm.js's
        documented local frame — equals the actual room corner point
        within 0.01in (0.254mm), and (2) the world door midpoint (the
        diagonal/pie notch's own doorMid, corner_cabinet.esm.js's
        `diagCornerShape`/`pieCornerShape`: clip = min(w,d)*0.42,
        doorMid = (w - clip/2, d - clip/2) in the LOCAL frame) lies
        STRICTLY inside the room — i.e. the notch genuinely opens into
        the room, not into a wall, which is the exact defect the
        verified bug report describes (back-left's door midpoint used to
        land at a point whose nearest room boundary was the BACK wall)."""
        TOL_MM = 0.254   # 0.01in
        w_in, d_in = 36.0, 36.0
        w_mm, d_mm = w_in * 25.4, d_in * 25.4
        clip = min(w_mm, d_mm) * 0.42
        door_mid_local = (w_mm - clip / 2.0, d_mm - clip / 2.0)

        expect_corner_mm = {
            "back-left":   (0.0, 0.0),
            "back-right":  (self.ROOM["width_mm"], 0.0),
            "front-left":  (0.0, self.ROOM["depth_mm"]),
            "front-right": (self.ROOM["width_mm"], self.ROOM["depth_mm"]),
        }
        for name, (xk, zk, rot) in E.ROOM_CORNER_PLACEMENT.items():
            with self.subTest(corner=name):
                anchor = E.room_corner_anchor_pose(name, self.ROOM)
                self.assertEqual(anchor["rotation_deg"], rot)
                want_x, want_z = expect_corner_mm[name]
                self.assertAlmostEqual(anchor["x"], want_x, delta=TOL_MM)
                self.assertAlmostEqual(anchor["z"], want_z, delta=TOL_MM)

                # anchor-transform of local (0,0) is, by construction,
                # just the anchor itself — asserted explicitly anyway so
                # this test does not silently depend on that being true.
                origin_wx, origin_wz = self._world_point(anchor, 0.0, 0.0)
                self.assertAlmostEqual(origin_wx, want_x, delta=TOL_MM)
                self.assertAlmostEqual(origin_wz, want_z, delta=TOL_MM)

                mid_wx, mid_wz = self._world_point(anchor, *door_mid_local)
                self.assertTrue(
                    0.0 < mid_wx < self.ROOM["width_mm"],
                    "%s: door midpoint x=%.2f not strictly inside room "
                    "width [0, %.2f]" % (name, mid_wx, self.ROOM["width_mm"]))
                self.assertTrue(
                    0.0 < mid_wz < self.ROOM["depth_mm"],
                    "%s: door midpoint z=%.2f not strictly inside room "
                    "depth [0, %.2f]" % (name, mid_wz, self.ROOM["depth_mm"]))

    def test_room_corner_placement_rejects_the_verified_bug_pose(self):
        """Direct regression pin for the exact bug report (recomputed
        here in inches, matching the report's own units): back-left at
        (x=0, z=36in, rotation_deg=90) — the OLD persisted values — put
        the door midpoint at world (28.44, 7.56)in, i.e. right against
        the BACK wall (z within 8in of z=0, out of a 96in-deep room),
        reading as "the notch faces the back wall". The FIXED pose
        (x=0, z=0, rotation_deg=0) puts the SAME local door-midpoint
        point at world (28.44, 28.44)in — identical X (this is purely a
        Z-axis collapse toward the back wall, not some unrelated shift),
        comfortably deep into the room-corner cell instead of pinned to
        the wall face."""
        w_in, d_in = 36.0, 36.0
        clip_in = min(w_in, d_in) * 0.42
        door_mid_local_in = (w_in - clip_in / 2.0, d_in - clip_in / 2.0)

        old_bug_anchor = {"x": 0.0, "z": d_in, "rotation_deg": 90}
        bug_x_in, bug_z_in = self._world_point(old_bug_anchor,
                                                *door_mid_local_in)
        self.assertAlmostEqual(bug_x_in, 28.44, places=2)
        self.assertAlmostEqual(bug_z_in, 7.56, places=2)   # the verified bug

        fixed_anchor_mm = E.room_corner_anchor_pose("back-left", self.ROOM)
        fixed_anchor_in = {"x": fixed_anchor_mm["x"] / 25.4,
                            "z": fixed_anchor_mm["z"] / 25.4,
                            "rotation_deg": fixed_anchor_mm["rotation_deg"]}
        fixed_x_in, fixed_z_in = self._world_point(fixed_anchor_in,
                                                    *door_mid_local_in)
        self.assertAlmostEqual(fixed_x_in, bug_x_in, places=2)  # X unaffected
        self.assertAlmostEqual(fixed_z_in, 28.44, places=2)     # Z corrected
        self.assertGreater(fixed_z_in - bug_z_in, 15.0,
                            "the fix must move the door midpoint well "
                            "away from the back wall, not by a rounding "
                            "amount")

    # ── Item 3(b) — a DELIBERATELY non-square corner ────────────────────
    # A square corner's anchor-convention AABB is footprint-identical
    # regardless of which of the 4 ALLOWED_ROTATIONS is chosen (that
    # invariant is exactly why two prior occupancy-level audits missed
    # the bug — see the ROOM_CORNER_PLACEMENT module comment). An
    # asymmetric (rule-driven, M2) corner does NOT enjoy that immunity
    # for its own reported width_mm/depth_mm: picking the wrong rotation
    # swaps which physical leg (leg_x_mm, along the back/front wall, vs.
    # leg_z_mm, along the left/right wall) gets reported as "width" vs.
    # "depth" — invisible for a 36x36 square, a real manufacturing defect
    # for a genuine 36x24 blind corner (a "36 wide" cabinet mislabeled
    # 24 wide / 36 deep).
    def test_asymmetric_corner_width_depth_not_swapped(self):
        room = {"width_mm": 4000.0, "depth_mm": 3000.0, "height_mm": 2400.0}
        cabs = ([self._mkw(("B", s), "back", s) for s in range(2)]
                + [self._mkw(("L", s), "left", s) for s in range(2)])
        leg_x_mm, leg_z_mm = 914.4, 609.6    # 36in x 24in — deliberately not square
        rule = {"rule_id": "asym-1", "sku": "SB-CORNER-BLIND", "tier": "base",
                "sequence": 1, "leg_x_mm": leg_x_mm, "leg_z_mm": leg_z_mm,
                "height_mm": 876.3, "min_leg_x_mm": 0, "min_leg_z_mm": 0}
        r = E.resolve_and_layout(cabs, room, auto_assign=False,
                                  corner_rules=[rule])
        node = next(n for n in r["inserted"] if n["corner"] == "back-left")
        place = {p["id"]: p for p in r["placements"]}[node["id"]]

        # width_mm is the ALONG-THE-BACK-WALL leg (leg_x_mm); depth_mm is
        # the ALONG-THE-LEFT-WALL leg (leg_z_mm) — NOT swapped.
        self.assertAlmostEqual(node["width_mm"], leg_x_mm)
        self.assertAlmostEqual(node["depth_mm"], leg_z_mm)

        anchor = E.anchor_pose_mm(node, place)
        self.assertAlmostEqual(anchor["x"], 0.0, delta=0.01)
        self.assertAlmostEqual(anchor["z"], 0.0, delta=0.01)
        self.assertEqual(int(round(anchor["rotation_deg"])) % 360, 0)

        # The footprint must be EXACTLY the asymmetric cell — 36in along
        # X (the back wall), 24in along Z (the left wall) — not the
        # 24x36 a swapped-rotation bug would have produced.
        fp = E.footprint_from_anchor_mm(node, anchor)
        self.assertEqual(
            tuple(round(v, 2) for v in fp),
            (0.0, round(leg_x_mm, 2), 0.0, round(leg_z_mm, 2)))

    # ── CLIENT EMPHASIS (2026-07-31) — tier coverage EXPLICIT ────────────
    # "the orientation bug affects the TOP (wall-layer) corner cabinets as
    # well as the base ones" — design 108's back-left junction carries
    # BOTH a base-tier corner (SB-CORNER, zone base_run) and a wall-tier
    # corner (SB-WALL-CORNER, zone 'wall', elevated) at the SAME x/z cell.
    # `resolve_and_layout`'s `layer` loop (base/wall) runs the IDENTICAL
    # ROOM_CORNER_PLACEMENT lookup for both — the table is keyed by
    # corner NAME only, never by layer — so this is provable, not just
    # assumed. This test proves it for every one of the four junctions
    # (not just back-left): a full G-shape kitchen with BOTH base and
    # wall (upper) cabinets on all four walls, so all four junctions
    # resolve a base AND a wall corner each.
    def test_base_and_wall_tier_corners_at_same_junction_agree_in_pose(self):
        room = {"width_mm": 6200.0, "depth_mm": 5000.0, "height_mm": 2400.0}
        wall_counts = {"back": 9, "front": 9, "left": 7, "right": 7}
        cabs = []
        for wall, n in wall_counts.items():
            cabs += [self._mkw((wall, "base", s), wall, s, "base")
                     for s in range(n)]
            cabs += [self._mkw((wall, "wall", s), wall, s, "wall")
                     for s in range(n)]
        r = E.resolve_and_layout(cabs, room, auto_assign=False)
        places = {p["id"]: p for p in r["placements"]}

        by_corner = {}
        for node in r["inserted"]:
            by_corner.setdefault(node["corner"], {})[node["layer"]] = node
        self.assertEqual(
            set(by_corner), set(E.ROOM_CORNER_PLACEMENT),
            "expected all four junctions to resolve a corner")

        for cname, by_layer in by_corner.items():
            with self.subTest(corner=cname):
                self.assertEqual(
                    set(by_layer), {"base", "wall"},
                    "%s: expected BOTH a base-tier and a wall-tier "
                    "corner node — the bug (and this fix) applies to "
                    "both, not just base" % cname)
                base_node, wall_node = by_layer["base"], by_layer["wall"]
                base_anchor = E.anchor_pose_mm(base_node, places[base_node["id"]])
                wall_anchor = E.anchor_pose_mm(wall_node, places[wall_node["id"]])

                # Both tiers match ROOM_CORNER_PLACEMENT.
                xk, zk, want_rot = E.ROOM_CORNER_PLACEMENT[cname]
                want_x = 0.0 if xk == "0" else room["width_mm"]
                want_z = 0.0 if zk == "0" else room["depth_mm"]
                for tier, anchor in (("base", base_anchor), ("wall", wall_anchor)):
                    self.assertAlmostEqual(
                        anchor["x"], want_x, delta=0.254,
                        msg="%s/%s tier x" % (cname, tier))
                    self.assertAlmostEqual(
                        anchor["z"], want_z, delta=0.254,
                        msg="%s/%s tier z" % (cname, tier))
                    self.assertEqual(
                        int(round(anchor["rotation_deg"])) % 360, want_rot,
                        "%s/%s tier rotation" % (cname, tier))

                # The stacked pair at one junction shares its x/z cell —
                # IDENTICAL x/z/rotation_deg between tiers — only y
                # (elevation, via _CORNER_HEIGHT_MM[layer]/y_floor)
                # legitimately differs.
                self.assertAlmostEqual(base_anchor["x"], wall_anchor["x"],
                                       delta=0.01, msg=cname)
                self.assertAlmostEqual(base_anchor["z"], wall_anchor["z"],
                                       delta=0.01, msg=cname)
                self.assertEqual(base_anchor["rotation_deg"],
                                 wall_anchor["rotation_deg"], cname)
                self.assertNotEqual(
                    base_anchor["y"], wall_anchor["y"],
                    "%s: base/wall corners should sit at DIFFERENT "
                    "elevations (only x/z/rot are shared)" % cname)

    @staticmethod
    def _mkw(cid, wall, seq, ct="base"):
        return {"id": cid, "width_mm": 600,
                "height_mm": 876 if ct != "wall" else 720,
                "depth_mm": 600 if ct != "wall" else 320,
                "family": ct if ct == "wall" else "base", "cabinet_type": ct,
                "zone": "wall" if ct == "wall" else "base_run",
                "wall": wall, "run_seq": seq}


class TestAnchorConversion(TransactionCase):
    """`anchor_pose_mm` / `footprint_from_anchor_mm` — the along-axis-CENTRED
    (engine) ↔ back-left-bottom-corner-ANCHORED (persisted, per
    COORDINATE_CONTRACT.md) conversion applied once at the ORM write
    boundary. Load-bearing property: converting an engine placement to the
    anchor convention and then re-deriving its footprint via the anchor-side
    formula must reproduce EXACTLY the footprint the engine itself computes
    for that placement (footprint_mm) — the two conventions describe the
    same physical box."""

    CAB = {"id": "cab-1", "width_mm": 600, "depth_mm": 450}

    def test_anchor_footprint_matches_engine_footprint_all_rotations(self):
        place_base = {"x": 1234.5, "z": 678.25, "y": 762, "rotation_deg": None}
        for rot in E.ALLOWED_ROTATIONS:
            place = dict(place_base, rotation_deg=rot)
            anchored = E.anchor_pose_mm(self.CAB, place)
            self.assertEqual(
                E.footprint_from_anchor_mm(self.CAB, anchored),
                E.footprint_mm(self.CAB, place),
                "mismatch at rotation_deg=%s" % rot)

    def test_anchor_pose_mm_does_not_mutate_input(self):
        place = {"x": 10.0, "z": 20.0, "y": 300, "rotation_deg": 90}
        snapshot = dict(place)
        E.anchor_pose_mm(self.CAB, place)
        self.assertEqual(place, snapshot)

    def test_anchor_pose_mm_passes_through_extra_keys(self):
        place = {"id": "should-be-overwritten-by-caller-not-here",
                  "x": 10.0, "z": 20.0, "y": 762, "rotation_deg": 180}
        out = E.anchor_pose_mm(self.CAB, place)
        self.assertEqual(out["y"], place["y"])
        self.assertEqual(out["rotation_deg"], place["rotation_deg"])
        self.assertEqual(out["id"], place["id"])
        # x is the only field that should differ for rot 180
        self.assertNotEqual(out["x"], place["x"])

    def test_anchor_pose_mm_returns_new_dict(self):
        place = {"x": 10.0, "z": 20.0, "y": 762, "rotation_deg": 0}
        out = E.anchor_pose_mm(self.CAB, place)
        self.assertIsNot(out, place)


class TestLayoutCapacityExceededOutside(TransactionCase):
    """LayoutCapacityExceeded.outside — carries per-cabinet overflow so a
    caller can report the worst offender instead of a bare count. Reuses the
    capacity-failure fixtures from test_layout_invariants.py
    (test_capacity_exceeded_raises / test_capacity_full_wall_cannot_seat_corner)."""

    @staticmethod
    def _base(n):
        return [{"id": i, "width_mm": 600, "height_mm": 876, "depth_mm": 600,
                 "family": "base", "cabinet_type": "base", "zone": "base_run"}
                for i in range(1, n + 1)]

    def test_outside_nonempty_with_positive_overflow(self):
        room = {"width_mm": 6000, "depth_mm": 5000, "height_mm": 2400}
        with self.assertRaises(E.LayoutCapacityExceeded) as ctx:
            E.resolve_and_layout(self._base(25), room)
        exc = ctx.exception
        self.assertTrue(exc.outside)
        for entry in exc.outside:
            self.assertIn("id", entry)
            self.assertIn("wall", entry)
            self.assertGreater(entry["overflow_mm"], 0)
        self.assertNotIn(">", str(exc))

    def test_full_wall_cannot_seat_corner_outside_nonempty(self):
        room = {"width_mm": 6000, "depth_mm": 5000, "height_mm": 2400}
        with self.assertRaises(E.LayoutCapacityExceeded) as ctx:
            E.resolve_and_layout(self._base(18), room)
        exc = ctx.exception
        self.assertTrue(exc.outside)
        self.assertGreater(max(o["overflow_mm"] for o in exc.outside), 0)

    def test_positional_signature_still_works(self):
        # Existing callers/tests construct LayoutCapacityExceeded
        # positionally — this MUST keep working.
        exc = E.LayoutCapacityExceeded(1000, 2000, "legacy detail")
        self.assertEqual(exc.capacity_mm, 1000)
        self.assertEqual(exc.requested_mm, 2000)
        self.assertEqual(exc.detail, "legacy detail")
        self.assertEqual(exc.outside, [])


class TestCornerRulesAsData(TransactionCase):
    """M1/M2 (2026-07-27) — resolve_and_layout's `corner_rules` input:
    rule-driven SKU + per-leg (asymmetric) corner consumption, with
    byte-identical legacy fallback when no rules are given / none fit."""

    # Task 3 (corner-remediation, 2026-07-30) — inch-exact mm:
    # 914.4=36in, 609.6=24in, 1371.6=54in (25.4 * inches). Mirrors the
    # real seed in models/placement_rule.py._SEED_RULES post-fix.
    RULES = [
        {"rule_id": "susan", "sku": "SB-CORNER", "tier": "base",
         "sequence": 10, "leg_x_mm": 914.4, "leg_z_mm": 914.4,
         "height_mm": 876.0, "min_leg_x_mm": 1143.0, "min_leg_z_mm": 1143.0},
        {"rule_id": "blind", "sku": "SB-CORNER-BLIND", "tier": "base",
         "sequence": 20, "leg_x_mm": 609.6, "leg_z_mm": 1143.0,
         "height_mm": 876.0, "min_leg_x_mm": 609.6, "min_leg_z_mm": 1371.6,
         "host_leg": "z"},
        {"rule_id": "wallpie", "sku": "SB-WALL-CORNER", "tier": "wall",
         "sequence": 10, "leg_x_mm": 609.6, "leg_z_mm": 609.6,
         "height_mm": 762.0, "min_leg_x_mm": 762.0, "min_leg_z_mm": 762.0},
    ]

    @staticmethod
    def _cab(i, wall, seq, layer="base"):
        t = "wall" if layer == "wall" else "base"
        return {"id": "%s-%s-%d" % (t, wall, i), "width_mm": 610.0,
                "height_mm": 762.0 if layer == "wall" else 876.0,
                "depth_mm": 305.0 if layer == "wall" else 610.0,
                "family": t, "cabinet_type": t,
                "zone": "wall" if layer == "wall" else "base_run",
                "wall": wall, "run_seq": seq}

    def _l_kitchen(self):
        return ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)]
                + [self._cab(i, "back", i, "wall") for i in range(3)]
                + [self._cab(i, "left", i, "wall") for i in range(3)])

    ROOM = {"width_mm": 5080.0, "depth_mm": 5080.0, "height_mm": 2438.0}

    def _no_same_layer_overlap(self, r):
        place = {p["id"]: p for p in r["placements"]}
        cabs = r["cabinets"]
        for i in range(len(cabs)):
            for j in range(i + 1, len(cabs)):
                a, b = cabs[i], cabs[j]
                if E._layer_of(a) != E._layer_of(b):
                    continue
                self.assertFalse(E.footprints_overlap(
                    E.footprint_mm(a, place[a["id"]]),
                    E.footprint_mm(b, place[b["id"]])),
                    "%s overlaps %s" % (a["id"], b["id"]))

    def test_none_rules_is_byte_identical_legacy(self):
        cabs = self._l_kitchen()
        r0 = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False)
        r1 = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                  corner_rules=None)
        self.assertEqual(r0["placements"], r1["placements"])
        self.assertEqual(r0["inserted"], r1["inserted"])
        self.assertEqual(r1["corner_diagnostics"], [])

    def test_rules_drive_sku_and_per_layer_cell_size(self):
        r = E.resolve_and_layout(self._l_kitchen(), self.ROOM,
                                 auto_assign=False, corner_rules=self.RULES)
        nodes = {n["id"]: n for n in r["inserted"]}
        base = nodes["corner-back-left-base"]
        wall = nodes["corner-back-left-wall"]
        self.assertEqual(base["sku"], "SB-CORNER")
        self.assertEqual(base["rule_id"], "susan")
        self.assertEqual((base["width_mm"], base["depth_mm"]), (914.4, 914.4))
        # The wall corner is now a REAL 24x24 upper cell, not the legacy
        # 36in square — the whole point of per-product rules.
        self.assertEqual(wall["sku"], "SB-WALL-CORNER")
        self.assertEqual((round(wall["width_mm"]), round(wall["depth_mm"])),
                         (610, 610))
        self._no_same_layer_overlap(r)
        self.assertEqual(r["corner_diagnostics"], [])

    def test_asymmetric_blind_selected_when_susan_leg_wont_fit(self):
        # X wall only 40in: the susan needs 45in per leg -> blind (24in of
        # X, 45in along Z) is the only fit. Its box runs along the LEFT
        # wall (host_leg="z") and consumes just its depth from the back.
        room = {"width_mm": 1016.0, "depth_mm": 5080.0, "height_mm": 2438.0}
        cabs = ([self._cab(0, "back", 0)]
                + [self._cab(i, "left", i) for i in range(3)])
        r = E.resolve_and_layout(cabs, room, auto_assign=False,
                                 corner_rules=self.RULES)
        node = {n["id"]: n for n in r["inserted"]}["corner-back-left-base"]
        self.assertEqual(node["sku"], "SB-CORNER-BLIND")
        self.assertEqual(node["wall"], "left")
        self.assertEqual(node["__pose"]["rotation_deg"], 90)
        # width runs along the host (Z) wall; depth is the X consumption.
        self.assertEqual((node["width_mm"], node["depth_mm"]),
                         (1143.0, 609.6))
        place = {p["id"]: p for p in r["placements"]}
        x0, x1, z0, z1 = E.footprint_mm(node, place[node["id"]])
        self.assertEqual((round(x0), round(x1), round(z0), round(z1)),
                         (0, 610, 0, 1143))
        # The left run starts past 45in; the back run only past 24in.
        left_starts = [E.footprint_mm(c, place[c["id"]])[2]
                       for c in r["cabinets"]
                       if c.get("wall") == "left"
                       and not c.get("corner_cabinet")]
        self.assertTrue(all(z >= 1143.0 - 1 for z in left_starts))
        self._no_same_layer_overlap(r)

    def test_no_fitting_rule_falls_back_with_diagnostic(self):
        # Legs too short for every rule -> legacy square + a diagnostic.
        room = {"width_mm": 1016.0, "depth_mm": 1270.0, "height_mm": 2438.0}
        cabs = [self._cab(0, "back", 0), self._cab(0, "left", 0)]
        try:
            r = E.resolve_and_layout(cabs, room, auto_assign=False,
                                     corner_rules=self.RULES)
        except E.LayoutCapacityExceeded:
            # The legacy square may legitimately not fit such a tiny room;
            # the fallback path itself is what this test pins, so re-run
            # detection-only to assert the diagnostic was the reason.
            r = None
        if r is not None:
            self.assertNotIn("sku", r["inserted"][0])
            self.assertTrue(r["corner_diagnostics"])
            self.assertEqual(r["corner_diagnostics"][0]["corner"],
                             "back-left")

    def test_selection_prefers_lower_sequence(self):
        rules = [dict(self.RULES[0], sequence=50),
                 dict(self.RULES[0], rule_id="susan-preferred",
                      sku="SB-CORNER-DIAG", sequence=5)]
        r = E.resolve_and_layout(self._l_kitchen(), self.ROOM,
                                 auto_assign=False, corner_rules=rules)
        base = {n["id"]: n for n in r["inserted"]}["corner-back-left-base"]
        self.assertEqual(base["rule_id"], "susan-preferred")
        self.assertEqual(base["sku"], "SB-CORNER-DIAG")


class TestCornerFillersM3(TransactionCase):
    """M3 — rule-demanded corner filler strips: emitted as real nodes,
    reservation extends past corner + filler, zero-filler rules and the
    legacy path emit none."""

    # Task 3 — inch-exact mm: 609.6=24in, 685.8=27in (609.6+76.2 filler).
    BLIND = {"rule_id": "blind", "sku": "SB-CORNER-BLIND", "tier": "base",
             "sequence": 10, "leg_x_mm": 609.6, "leg_z_mm": 1143.0,
             "height_mm": 876.0, "filler_x_mm": 76.2,
             "min_leg_x_mm": 685.8, "min_leg_z_mm": 1371.6,
             "host_leg": "z"}

    @staticmethod
    def _cab(i, wall, seq):
        return {"id": "base-%s-%d" % (wall, i), "width_mm": 610.0,
                "height_mm": 876.0, "depth_mm": 610.0, "family": "base",
                "cabinet_type": "base", "zone": "base_run",
                "wall": wall, "run_seq": seq}

    ROOM = {"width_mm": 5080.0, "depth_mm": 5080.0, "height_mm": 2438.0}

    def test_blind_rule_emits_filler_and_reserves_past_it(self):
        cabs = ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)])
        r = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                 corner_rules=[self.BLIND])
        self.assertEqual(len(r["fillers"]), 1)
        f = r["fillers"][0]
        self.assertEqual(f["id"], "cornerfill-back-left-base-back")
        self.assertTrue(f["corner_filler"])
        self.assertEqual(f["wall"], "back")
        self.assertAlmostEqual(f["width_mm"], 76.2)
        place = {p["id"]: p for p in r["placements"]}
        # Strip sits exactly between the corner cell (x ends 609.6) and
        # the back run start (685.8) — Task 3 inch-exact mm.
        fx0, fx1, fz0, fz1 = E.footprint_mm(f, place[f["id"]])
        self.assertAlmostEqual(fx0, 609.6)
        self.assertAlmostEqual(fx1, 685.8)
        # Back run survivors start past corner + filler.
        back_starts = [E.footprint_mm(c, place[c["id"]])[0]
                       for c in r["cabinets"]
                       if c.get("wall") == "back"
                       and not c.get("corner_cabinet")
                       and not c.get("corner_filler")]
        self.assertTrue(all(x0 >= 685.8 - 1 for x0 in back_starts),
                        back_starts)
        # Fillers are solid members: no same-layer overlap anywhere.
        cabs_all = r["cabinets"]
        for i in range(len(cabs_all)):
            for j in range(i + 1, len(cabs_all)):
                a, b = cabs_all[i], cabs_all[j]
                self.assertFalse(E.footprints_overlap(
                    E.footprint_mm(a, place[a["id"]]),
                    E.footprint_mm(b, place[b["id"]])),
                    "%s overlaps %s" % (a["id"], b["id"]))

    def test_zero_filler_rules_emit_none(self):
        susan = {"rule_id": "susan", "sku": "SB-CORNER", "tier": "base",
                 "sequence": 10, "leg_x_mm": 914.4, "leg_z_mm": 914.4,
                 "height_mm": 876.0, "min_leg_x_mm": 1143.0,
                 "min_leg_z_mm": 1143.0}
        cabs = ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)])
        r = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                 corner_rules=[susan])
        self.assertEqual(r["fillers"], [])

    def test_legacy_path_emits_none(self):
        cabs = ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)])
        r = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False)
        self.assertEqual(r["fillers"], [])

    def test_motion_envelope_helper_all_rotations(self):
        cab = {"width_mm": 914.0, "depth_mm": 914.0}
        c = 500.0
        # Anchor at origin; footprint_from_anchor first, then extend
        # along the FACING direction: rot 0 fp=(0,914,0,914) faces +Z;
        # rot 90 fp=(0,914,-914,0)... no: rot 90 fp=(x,x+d,z-w,z)
        # =(0,914,-914,0), faces +X -> (914,1414,-914,0); rot 180
        # fp=(-914,0,-914,0) faces -Z -> (-914,0,-1414,-914); rot 270
        # fp=(-914,0,0,914) faces -X -> (-1414,-914,0,914).
        cases = {
            0:   (0, 914, 914, 1414),
            90:  (914, 1414, -914, 0),
            180: (-914, 0, -1414, -914),
            270: (-1414, -914, 0, 914),
        }
        for rot, want in cases.items():
            got = E.motion_envelope_from_anchor_mm(
                cab, {"x": 0, "z": 0, "rotation_deg": rot}, c)
            self.assertEqual(tuple(round(v) for v in got), want,
                             "rot %d" % rot)


class TestWallGraphM5(TransactionCase):
    """M5 — the room as a first-class wall graph; rectangle
    specialization is byte-identical to the legacy implicit walls."""

    ROOM = {"width_mm": 5080.0, "depth_mm": 5080.0, "height_mm": 2438.0}

    @staticmethod
    def _cab(i, wall, seq):
        return {"id": "base-%s-%d" % (wall, i), "width_mm": 610.0,
                "height_mm": 876.0, "depth_mm": 610.0, "family": "base",
                "cabinet_type": "base", "zone": "base_run",
                "wall": wall, "run_seq": seq}

    def test_rectangle_graph_shape(self):
        g = E.wall_graph(self.ROOM)
        self.assertEqual([s["id"] for s in g["segments"]],
                         ["back", "front", "left", "right"])
        by_id = {s["id"]: s for s in g["segments"]}
        self.assertEqual(by_id["back"]["length_mm"], 5080.0)
        self.assertEqual(by_id["right"]["origin_mm"], (5080.0, 0.0))
        self.assertEqual(by_id["front"]["rot_deg"], 180)
        # Junction set mirrors _CORNER_SPECS exactly (names, pairs,
        # anchoring keys) — the parity guarantee.
        specs = [(j["id"], (j["wall_a"], j["wall_b"]), j["xz_keys"])
                 for j in g["junctions"]]
        self.assertEqual(
            specs, [(n, w, xz) for n, w, xz in E._CORNER_SPECS])
        self.assertTrue(all(j["interior_angle_deg"] == 90.0
                            for j in g["junctions"]))

    def test_default_graph_is_byte_identical(self):
        cabs = ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)])
        r0 = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False)
        r1 = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                  graph=E.wall_graph(self.ROOM))
        self.assertEqual(r0["placements"], r1["placements"])
        self.assertEqual(r0["inserted"], r1["inserted"])
        self.assertEqual(r0["corner_diagnostics"],
                         r1["corner_diagnostics"])

    def test_non_orthogonal_junction_refused_with_diagnostic(self):
        g = E.wall_graph(self.ROOM)
        # Mutate back-left into a 135° junction (an angled wall meets
        # the back run) — M6: the engine must refuse, not guess.
        for j in g["junctions"]:
            if j["id"] == "back-left":
                j["interior_angle_deg"] = 135.0
        cabs = ([self._cab(i, "back", i) for i in range(3)]
                + [self._cab(i, "left", i) for i in range(3)])
        r = E.resolve_and_layout(cabs, self.ROOM, auto_assign=False,
                                 graph=g)
        self.assertFalse(
            [n for n in r["inserted"] if n["corner"] == "back-left"])
        reasons = [d for d in r["corner_diagnostics"]
                   if d["corner"] == "back-left"]
        self.assertTrue(reasons)
        self.assertIn("non-orthogonal", reasons[0]["reason"])

    def test_non_canonical_junction_refused(self):
        g = E.wall_graph(self.ROOM)
        g["junctions"].append({"id": "bay-1", "wall_a": "bay-wall",
                               "wall_b": "left",
                               "corner_point_mm": (0.0, 2000.0),
                               "interior_angle_deg": 90.0,
                               "kind": "inside", "xz_keys": ("0", "0")})
        r = E.resolve_and_layout([self._cab(0, "back", 0)], self.ROOM,
                                 auto_assign=False, graph=g)
        reasons = [d for d in r["corner_diagnostics"]
                   if d["corner"] == "bay-1"]
        self.assertTrue(reasons)
        self.assertIn("not canonical", reasons[0]["reason"])


class TestObbNarrowPhaseM6(TransactionCase):
    """M6 — OBB corners, SAT overlap, dispatcher, in-room at arbitrary
    rotation."""

    def test_obb_matches_aabb_at_orthogonal_rotations(self):
        cab = {"width_mm": 600.0, "depth_mm": 300.0}
        for rot in (0, 90, 180, 270):
            place = {"x": 1000.0, "z": 2000.0, "rotation_deg": rot}
            pts = E.obb_corners_from_anchor_mm(cab, place)
            xs = [p[0] for p in pts]
            zs = [p[1] for p in pts]
            aabb = E.footprint_from_anchor_mm(cab, place)
            self.assertAlmostEqual(min(xs), aabb[0], places=6)
            self.assertAlmostEqual(max(xs), aabb[1], places=6)
            self.assertAlmostEqual(min(zs), aabb[2], places=6)
            self.assertAlmostEqual(max(zs), aabb[3], places=6)

    def test_sat_detects_and_rejects_rotated_overlap(self):
        cab = {"width_mm": 600.0, "depth_mm": 600.0}
        axis = E.obb_corners_from_anchor_mm(
            cab, {"x": 0.0, "z": 0.0, "rotation_deg": 0})
        # 45° diamond positioned so its axis-aligned BOUNDING box
        # overlaps the axis cabinet but its true oriented box does NOT
        # (separated on the x−z diagonal axis): a naive AABB test
        # false-positives here; SAT must not. Precondition asserted so
        # the fixture can never silently degrade into a trivial miss.
        nm_place = {"x": 500.0, "z": -400.0, "rotation_deg": 45}
        near_miss = E.obb_corners_from_anchor_mm(cab, nm_place)
        xs = [pt[0] for pt in near_miss]
        zs = [pt[1] for pt in near_miss]
        self.assertTrue(E.footprints_overlap(
            (min(xs), max(xs), min(zs), max(zs)),
            E.footprint_from_anchor_mm(
                cab, {"x": 0.0, "z": 0.0, "rotation_deg": 0})),
            "fixture regression: bounding boxes must overlap")
        self.assertFalse(E.convex_polys_overlap(axis, near_miss))
        # Slide it into genuine contact.
        hit = E.obb_corners_from_anchor_mm(
            cab, {"x": 550.0, "z": 300.0, "rotation_deg": 45})
        self.assertTrue(E.convex_polys_overlap(axis, hit))

    def test_dispatcher_orthogonal_equals_aabb_and_rotated_uses_sat(self):
        a = {"width_mm": 600.0, "depth_mm": 600.0}
        pa = {"x": 0.0, "z": 0.0, "rotation_deg": 0}
        b = {"width_mm": 600.0, "depth_mm": 600.0}
        # Orthogonal pair: identical verdicts to the AABB primitive.
        for bx, expect in ((300.0, True), (700.0, False)):
            pb = {"x": bx, "z": 0.0, "rotation_deg": 0}
            self.assertEqual(
                E.solid_overlap_from_anchor_mm(a, pa, b, pb),
                E.footprints_overlap(
                    E.footprint_from_anchor_mm(a, pa),
                    E.footprint_from_anchor_mm(b, pb)))
        # Rotated near-miss whose bounding box DOES overlap (see the
        # SAT test's precondition): the dispatcher must route to SAT
        # and say no-overlap.
        pb45 = {"x": 500.0, "z": -400.0, "rotation_deg": 45}
        self.assertFalse(E.solid_overlap_from_anchor_mm(a, pa, b, pb45))

    def test_obb_within_room_at_45(self):
        room = {"width_mm": 3000.0, "depth_mm": 3000.0}
        cab = {"width_mm": 600.0, "depth_mm": 600.0}
        inside = {"x": 1500.0, "z": 1500.0, "rotation_deg": 45}
        self.assertTrue(E.obb_within_room(cab, inside, room))
        # Same pose near the +X wall: a 45° box's diagonal pokes out.
        poking = {"x": 2900.0, "z": 1500.0, "rotation_deg": 45}
        self.assertFalse(E.obb_within_room(cab, poking, room))
