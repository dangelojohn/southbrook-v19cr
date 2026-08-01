# SPDX-License-Identifier: LGPL-3.0-only
"""Corner-orientation fix (2026-07-31) — idempotent repair for
back-left/front-left/front-right corner design lines persisted under the
old (verified) wrong presentation.

THE BUG (verified by recomputing the transforms): the renderer applies
`grp.rotation.y = rotation_deg` (kitchen_canvas.esm.js's
`_placeCabinetGroup`) to a corner mesh whose LOCAL frame
(corner_cabinet.esm.js) fixes local (0,0) at the anchor (= the room
corner) and the diagonal/pie door face at local (width_in, depth_in) with
outward normal (+1/sqrt(2), +1/sqrt(2)). The OLD
`kitchen_layout_engine._CORNER_RESOLVE_PRESENTATION` table picked the
WRONG rotation for three of the four room corners (back-left, front-left,
and the standalone front-right branch) — back-right (270 degrees) was
already correct. A SQUARE corner's anchor-convention AABB is pixel-
identical for any of the four ALLOWED_ROTATIONS (e.g. back-left at
(x=0, z=0, rot=0) and the old (x=0, z=depth_in, rot=90) both cover the
SAME footprint), which is exactly why two prior occupancy-level audits
never caught it — see `kitchen_layout_engine.ROOM_CORNER_PLACEMENT`'s own
module comment for the full derivation and
`southbrook_estimating/tests/test_layout_engine.py::TestRoomCornerPlacement`
for the regression suite. Live evidence (design 108, a U-shape): its two
back-left lines (base + wall corner) read (x=0, z=36in, rotation_deg=90);
this migration rewrites them to (x=0, z=0, rotation_deg=0).

Scope: every ACTIVE `southbrook.kitchen.design.line` with
cabinet_type == 'corner' and pinned == False (pinned lines are USER DATA
— a manually placed/rotated corner is never touched by this or any other
auto-arrange-adjacent repair). The junction each line belongs to is
identified from its `layout_key` first (the
"corner-<design_id>-<corner_name>-<layer>" convention every OTHER
corner-parsing call site in this addon already uses — see
`_place_lines_on_wall`, `_sb_corner_expected_hand`), falling back to a
geometric guess (nearest room corner, narrowed by the line's own `wall`
tag against `_CORNER_SPECS`'s wall pairs when that narrows it) for a
manually-added corner line with no engine-shaped layout_key.

Safety (never corrupt occupancy): this migration writes ONLY
x_position_in / z_position_in / rotation_deg — it never touches
width_in / depth_in. Before writing, it asserts the anchor-convention
AABB (`kitchen_layout_engine.footprint_from_anchor_mm`, the SAME
primitive the production collision/remainder checks use) computed from
the row's CURRENT width_in/depth_in is unchanged between the old and new
pose. For a genuinely SQUARE corner line this assertion always holds (see
the module comment referenced above) and the row is repaired. For an
asymmetric (M2 rule-driven) corner line whose width_in/depth_in may
already be mislabeled by the old wrong-rotation write, changing only the
pose WOULD change the footprint — that row is left untouched (logged,
not written) rather than risk corrupting its manufacturable geometry;
those are a distinct, out-of-scope repair.

Idempotent: a line already at its target pose (within field precision)
is a no-op; a second run of this migration changes nothing further.

Fires on the 19.0.5.36.1 upgrade (bumped from 19.0.5.36.0, the version
this fix was authored against).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_MM_PER_IN = 25.4
# Field precision is digits=(6, 2) on x/z_position_in and rotation_deg —
# treat anything within field-rounding noise as "already correct" so a
# second run (or a run against data another session already repaired) is
# a true no-op rather than a redundant identical write every time.
_POSE_TOL_IN = 0.02
_ROT_TOL_DEG = 0.5
# AABB-preservation gate, in millimetres — generous enough to absorb the
# same field-rounding noise, tight enough that a genuine footprint change
# (an asymmetric/mislabeled row) is always caught.
_AABB_TOL_MM = 0.75


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "southbrook.kitchen.design.line" not in env:
        _logger.warning(
            "19.0.5.36.1 post-migrate: southbrook.kitchen.design.line "
            "model not found — skipping corner-orientation repair.")
        return
    _repair_corner_orientations(env)


def _corner_name_for_line(E, line, design):
    """The ROOM_CORNER_PLACEMENT key this corner line belongs to, from
    its layout_key first, else a geometric best guess."""
    key = line.layout_key or ""
    prefix = "corner-%s-" % design.id
    if key.startswith(prefix):
        for suffix in ("-base", "-wall"):
            if key.endswith(suffix):
                name = key[len(prefix):-len(suffix)]
                if name in E.ROOM_CORNER_PLACEMENT:
                    return name

    room_w = design.room_width_in or 0.0
    room_d = design.room_depth_in or 0.0
    x = line.x_position_in or 0.0
    z = line.z_position_in or 0.0
    room_corner_in = {
        "back-left": (0.0, 0.0), "back-right": (room_w, 0.0),
        "front-left": (0.0, room_d), "front-right": (room_w, room_d),
    }
    corner_walls = {name: walls for name, walls, _xz in E._CORNER_SPECS}
    candidates = [n for n, walls in corner_walls.items()
                  if line.wall in walls]
    if not candidates:
        candidates = list(room_corner_in)
    return min(
        candidates,
        key=lambda n: (x - room_corner_in[n][0]) ** 2
        + (z - room_corner_in[n][1]) ** 2)


def _repair_corner_orientations(env):
    from odoo.addons.southbrook_estimating.models import (
        kitchen_layout_engine as E,
    )

    Line = env["southbrook.kitchen.design.line"]
    lines = Line.search([("cabinet_type", "=", "corner"),
                          ("pinned", "=", False)])
    repaired = skipped_aabb = skipped_unresolved = already_correct = 0
    touched_ids = []

    for line in lines:
        design = line.design_id
        if not design:
            skipped_unresolved += 1
            continue
        corner_name = _corner_name_for_line(E, line, design)
        if corner_name not in E.ROOM_CORNER_PLACEMENT:
            skipped_unresolved += 1
            _logger.info(
                "19.0.5.36.1 post-migrate: line %s (design %s) — could "
                "not resolve a ROOM_CORNER_PLACEMENT junction from "
                "layout_key=%r / wall=%r — skipping.",
                line.id, design.id, line.layout_key, line.wall)
            continue

        room = {"width_mm": (design.room_width_in or 0.0) * _MM_PER_IN,
                "depth_mm": (design.room_depth_in or 0.0) * _MM_PER_IN}
        target = E.room_corner_anchor_pose(corner_name, room)
        new_x_in = target["x"] / _MM_PER_IN
        new_z_in = target["z"] / _MM_PER_IN
        new_rot = float(target["rotation_deg"])

        old_x_in = line.x_position_in or 0.0
        old_z_in = line.z_position_in or 0.0
        old_rot = line.rotation_deg or 0.0

        if (abs(old_x_in - new_x_in) <= _POSE_TOL_IN
                and abs(old_z_in - new_z_in) <= _POSE_TOL_IN
                and abs((old_rot - new_rot) % 360) <= _ROT_TOL_DEG):
            already_correct += 1
            continue

        cab = {"width_mm": (line.width_in or 0.0) * _MM_PER_IN,
               "depth_mm": (line.depth_in or 0.0) * _MM_PER_IN}
        old_place = {"x": old_x_in * _MM_PER_IN, "z": old_z_in * _MM_PER_IN,
                     "rotation_deg": old_rot}
        new_place = {"x": new_x_in * _MM_PER_IN, "z": new_z_in * _MM_PER_IN,
                     "rotation_deg": new_rot}
        old_fp = E.footprint_from_anchor_mm(cab, old_place)
        new_fp = E.footprint_from_anchor_mm(cab, new_place)
        if any(abs(o - n) > _AABB_TOL_MM for o, n in zip(old_fp, new_fp)):
            skipped_aabb += 1
            _logger.warning(
                "19.0.5.36.1 post-migrate: line %s (design %s, corner "
                "%s) — repair would change its anchor-convention "
                "footprint (%r -> %r), most likely an asymmetric corner "
                "whose width_in/depth_in are already mislabeled from "
                "the old wrong-rotation write. Skipping this row rather "
                "than risk corrupting its occupancy — needs a separate, "
                "targeted repair.",
                line.id, design.id, corner_name, old_fp, new_fp)
            continue

        line.write({
            "x_position_in": round(new_x_in, 2),
            "z_position_in": round(new_z_in, 2),
            "rotation_deg": new_rot,
        })
        repaired += 1
        touched_ids.append(line.id)

    _logger.info(
        "19.0.5.36.1 post-migrate: corner-orientation repair — %d "
        "repaired, %d already correct, %d skipped (footprint would "
        "change), %d skipped (unresolvable junction), out of %d "
        "candidate corner line(s). Repaired line ids: %s",
        repaired, already_correct, skipped_aabb, skipped_unresolved,
        len(lines), touched_ids)
