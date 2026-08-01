# SPDX-License-Identifier: LGPL-3.0-only
"""Southbrook Kitchen — pure layout engine.

The single source of truth for *where and how* cabinets sit in a room.

Design contract (P0, 2026-07-11):
  * PURE. No Odoo imports, no ORM, no `self`, no I/O, no globals. Every
    input arrives as plain data; every output is plain data. This module
    must be importable and runnable with a bare `python3`.
  * DETERMINISTIC + STATELESS. Same inputs → same outputs, always. No
    randomness, no clocks, no mutation of the inputs.
  * UI-INDEPENDENT. The engine never knows whether its caller is the
    read-only Preview, the drag editor, an auto-layout pass, an import
    wizard, or a future AI designer. It consumes *semantic* placement
    inputs and emits manufacturable placements.

Domain separation (John, 2026-07-11):
  * Manufacturing domain (`sale.order.line`) = product/qty/price/BOM. NOT
    an input concept here beyond product identity + dimensions.
  * Layout domain (`kitchen.design.line`) = wall, run_seq, offsets,
    rotation, adjacency. THIS is the engine's input.

Evolution path — Cabinet Relationship Graph:
  Today runs are derived from (wall, run_seq). That is deliberately isolated
  in `_resolve_runs()`. Tomorrow, runs come from an adjacency graph
  (connects_to / terminates_into / corner_with / filler_before / ...), and
  walls become just one constraint type. Only `_resolve_runs()` changes;
  the placement mechanics below are graph-agnostic.

Coordinate frame (millimetres, right-handed, matches the existing scene):
  * +X runs along the back wall, left→right.
  * +Z runs from the back wall (z=0) toward the front of the room (z=depth).
  * +Y is up. rotation_deg is a right-handed rotation about +Y.
  * A cabinet's local pose faces the room (out of the back wall) at
    rotation_deg = 0.
"""

import math

# Wall geometry table. Each wall declares:
#   along : which world axis the run advances on ("x" or "z")
#   rot   : the cabinet's Y-rotation (deg) when placed on this wall
# The cross-axis anchor (which wall the run hugs) is computed from the room
# and the zone's cross-offset in `_place_on_wall()`.
WALLS = ("back", "front", "left", "right")

_WALL_SPEC = {
    "back":  {"along": "x", "rot": 0},
    "front": {"along": "x", "rot": 180},
    "left":  {"along": "z", "rot": 90},
    "right": {"along": "z", "rot": 270},
}

ALLOWED_ROTATIONS = (0, 90, 180, 270)

# M5/M6 — headings within this tolerance of a right angle count as
# orthogonal; junctions outside it refuse auto corner resolution.
ORTHO_TOL_DEG = 1.0


# ── M5 — the room as a first-class wall graph ───────────────────────────
# 09-rule-engine-spec.md §2.1 (`wall-segment-local-frames`,
# `corner-junction-as-real-object`): walls are explicit segment objects
# and corners are junction objects derived from them, instead of the
# implicit 4-wall room. `wall_graph(room)` is the RECTANGLE
# specialization — segment ids equal today's wall names and its junction
# set is exactly `_CORNER_SPECS`, so every existing behavior is
# byte-identical (pinned by test). A caller may hand `resolve_and_layout`
# a custom graph instead; junctions that aren't orthogonal (M6) or don't
# map onto the four canonical walls refuse auto-resolution with a
# diagnostic rather than guessing.
#
# Segment: {"id", "origin_mm": (x, z), "length_mm",
#           "rot_deg"     — cabinet rotation on this wall (_WALL_SPEC),
#           "run_dir_deg" — world direction of the run cursor advance
#                           (0 = +X, 90 = +Z)}
# Junction: {"id", "wall_a", "wall_b" — wall_a X-running / wall_b
#            Z-running for the canonical four, "corner_point_mm": (x, z),
#            "interior_angle_deg", "kind": "inside"|"outside",
#            "xz_keys": (xk, zk) — cell anchoring, as in _CORNER_SPECS}

def wall_graph(room):
    """The default graph for a rectangular room. Segment ids are the
    legacy wall names; junction ids/order match `_CORNER_SPECS`."""
    W = float(room.get("width_mm", 0))
    D = float(room.get("depth_mm", 0))
    segments = [
        {"id": "back",  "origin_mm": (0.0, 0.0), "length_mm": W,
         "rot_deg": 0,   "run_dir_deg": 0},
        {"id": "front", "origin_mm": (0.0, D),   "length_mm": W,
         "rot_deg": 180, "run_dir_deg": 0},
        {"id": "left",  "origin_mm": (0.0, 0.0), "length_mm": D,
         "rot_deg": 90,  "run_dir_deg": 90},
        {"id": "right", "origin_mm": (W, 0.0),   "length_mm": D,
         "rot_deg": 270, "run_dir_deg": 90},
    ]
    junctions = [
        {"id": name, "wall_a": walls[0], "wall_b": walls[1],
         "corner_point_mm": ({"0": 0.0, "W": W}[xk],
                             {"0": 0.0, "D": D}[zk]),
         "interior_angle_deg": 90.0, "kind": "inside",
         "xz_keys": (xk, zk)}
        for name, walls, (xk, zk) in _CORNER_SPECS
    ]
    return {"segments": segments, "junctions": junctions}


def _graph_corner_specs(graph):
    """Split a graph's junctions into (auto_specs, refused) where
    auto_specs is a `_CORNER_SPECS`-shaped list for the junctions the
    engine may auto-resolve (orthogonal + both walls canonical), and
    refused carries a reason per junction the engine must not touch."""
    auto, refused = [], []
    for j in graph.get("junctions", ()):
        angle = j.get("interior_angle_deg", 90.0)
        if abs(angle - 90.0) > ORTHO_TOL_DEG:
            refused.append((j, "non-orthogonal junction (%.1f°) — auto "
                               "corner resolution refused; place a "
                               "custom corner manually" % angle))
            continue
        if (j.get("wall_a") not in _WALL_SPEC
                or j.get("wall_b") not in _WALL_SPEC
                or "xz_keys" not in j):
            refused.append((j, "junction walls %r/%r are not canonical "
                               "orthogonal walls — auto corner "
                               "resolution refused"
                               % (j.get("wall_a"), j.get("wall_b"))))
            continue
        auto.append((j["id"], (j["wall_a"], j["wall_b"]), j["xz_keys"]))
    return auto, refused


class LayoutCapacityExceeded(Exception):
    """Raised when cabinets cannot physically fit the available walls.

    Impossible layouts fail explicitly here rather than silently overflowing
    the room (which would ship a non-manufacturable design). Carries the
    numbers so a caller can surface e.g. ROOM_TOO_SMALL(capacity, requested).

    NOTE: the raise fires on the geometric postcondition (a cabinet's
    footprint falls outside the room), NOT on the capacity precheck — so
    requested_mm is not necessarily greater than capacity_mm (a corner
    reservation can push an otherwise-fitting run past the wall even when
    total requested run length is under capacity). The message must not
    claim ">" for that reason.

    `outside`, when given, is a list of dicts describing each cabinet that
    failed the in-room postcondition: {"id", "overflow_mm", "wall"} — the
    worst-case overhang (mm) past the room bounds and the wall it was on.
    """
    def __init__(self, capacity_mm, requested_mm, detail="", outside=None):
        self.capacity_mm = capacity_mm
        self.requested_mm = requested_mm
        self.detail = detail
        self.outside = outside or []
        super().__init__(
            "layout does not fit: %d cabinet(s) outside room "
            "(capacity %.0fmm, requested %.0fmm)%s" % (
                len(self.outside), capacity_mm, requested_mm,
                (" — %s" % detail) if detail else ""))


# ── Geometry helpers (shared by the engine + the invariant suite) ───────
# Footprint math matches the engine's placement convention exactly: the
# along-wall axis is CENTRED on the placement coord; the depth extends from
# the wall face into the room, in the direction set by rotation_deg.

def footprint_mm(cab, place):
    """World-space AABB (x0, x1, z0, z1) of a placed cabinet."""
    w = cab.get("width_mm", 0)
    d = cab.get("depth_mm", 0)
    x = place["x"]
    z = place["z"]
    rot = int(round(place.get("rotation_deg", 0))) % 360
    if rot == 0:        # back: along +X, depth +Z
        return (x - w / 2, x + w / 2, z, z + d)
    if rot == 180:      # front: along +X, depth -Z
        return (x - w / 2, x + w / 2, z - d, z)
    if rot == 90:       # left: along +Z, depth +X
        return (x, x + d, z - w / 2, z + w / 2)
    # rot == 270        # right: along +Z, depth -X
    return (x - d, x, z - w / 2, z + w / 2)


def anchor_pose_mm(cab, place):
    """Convert an engine placement (along-axis CENTRED — the module's
    internal convention, see footprint_mm) to the persisted anchor
    convention (COORDINATE_CONTRACT.md: back-left-bottom corner, mesh
    rotated about +Y around the anchor). Returns a NEW dict; inputs
    untouched. y and rotation_deg pass through unchanged."""
    w = cab.get("width_mm", 0)
    x = place["x"]
    z = place["z"]
    rot = int(round(place.get("rotation_deg", 0))) % 360
    out = dict(place)
    if rot == 0:
        out["x"] = x - w / 2.0
    elif rot == 90:
        out["z"] = z + w / 2.0
    elif rot == 180:
        out["x"] = x + w / 2.0
    else:   # 270
        out["z"] = z - w / 2.0
    return out


def footprint_from_anchor_mm(cab, place):
    """World AABB (x0,x1,z0,z1) of a cabinet whose place uses the
    persisted ANCHOR convention (back-left-bottom corner + rotation
    about the anchor) — the renderer's math, for validating persisted
    poses. Sibling of footprint_mm (which assumes the engine's
    along-axis-centred convention)."""
    w = cab.get("width_mm", 0)
    d = cab.get("depth_mm", 0)
    x = place["x"]
    z = place["z"]
    rot = int(round(place.get("rotation_deg", 0))) % 360
    if rot == 0:
        return (x, x + w, z, z + d)
    if rot == 180:
        return (x - w, x, z - d, z)
    if rot == 90:
        return (x, x + d, z - w, z)
    # rot == 270
    return (x - d, x, z, z + w)


def motion_envelope_from_anchor_mm(cab, place, clearance_front_mm):
    """M4 — AABB of the region a cabinet's doors/mechanism sweep IN FRONT
    of its face (the direction it faces per rotation), extending
    `clearance_front_mm` from the ANCHOR-convention footprint. Solid
    cabinets inside this box block the mechanism (motion-vs-solid =
    blocking per 09-rule-engine-spec.md §5)."""
    x0, x1, z0, z1 = footprint_from_anchor_mm(cab, place)
    c = clearance_front_mm
    rot = int(round(place.get("rotation_deg", 0))) % 360
    if rot == 0:        # faces +Z
        return (x0, x1, z1, z1 + c)
    if rot == 180:      # faces -Z
        return (x0, x1, z0 - c, z0)
    if rot == 90:       # faces +X
        return (x1, x1 + c, z0, z1)
    # rot == 270        # faces -X
    return (x0 - c, x0, z0, z1)


def footprints_overlap(a, b, eps=1.0):
    return (a[0] < b[1] - eps and b[0] < a[1] - eps and
            a[2] < b[3] - eps and b[2] < a[3] - eps)


# ── M6 — OBB narrow phase for arbitrary rotations ───────────────────────
# The AABB helpers above are exact ONLY for ALLOWED_ROTATIONS. A manually
# placed cabinet on a non-orthogonal wall (M6) carries an arbitrary
# rotation; its true footprint is an oriented box. Rotation sign matches
# the renderer (THREE grp.rotation.y = +deg): local +X → world
# (cos θ, −sin θ) and local +Z → world (sin θ, cos θ) in the (x, z) plane
# — verified against footprint_from_anchor_mm at 90/270 (pinned by test).

def obb_corners_from_anchor_mm(cab, place):
    """The 4 world-space (x, z) corner points of an ANCHOR-convention
    cabinet at any rotation. Order: anchor, +width, +width+depth, +depth
    (counter-clockwise or clockwise depending on rotation — SAT doesn't
    care)."""
    w = cab.get("width_mm", 0)
    d = cab.get("depth_mm", 0)
    x = place["x"]
    z = place["z"]
    th = math.radians(place.get("rotation_deg", 0) or 0)
    ux, uz = math.cos(th), -math.sin(th)     # local +X (width)
    vx, vz = math.sin(th), math.cos(th)      # local +Z (depth)
    return [
        (x, z),
        (x + w * ux, z + w * uz),
        (x + w * ux + d * vx, z + w * uz + d * vz),
        (x + d * vx, z + d * vz),
    ]


def convex_polys_overlap(pa, pb, eps=1.0):
    """SAT overlap test for two convex polygons (lists of (x, z)
    points). Same eps semantics as footprints_overlap: shapes merely
    touching within eps do NOT overlap."""
    for poly_a, poly_b in ((pa, pb), (pb, pa)):
        n = len(poly_a)
        for i in range(n):
            x1, z1 = poly_a[i]
            x2, z2 = poly_a[(i + 1) % n]
            # Edge normal (perpendicular in the plane).
            ax, az = -(z2 - z1), (x2 - x1)
            proj_a = [px * ax + pz * az for px, pz in poly_a]
            proj_b = [px * ax + pz * az for px, pz in poly_b]
            # Normalize eps by the axis length (unnormalized normal).
            norm = math.hypot(ax, az) or 1.0
            if (max(proj_a) <= min(proj_b) + eps * norm
                    or max(proj_b) <= min(proj_a) + eps * norm):
                return False   # separating axis found
    return True


def solid_overlap_from_anchor_mm(cab_a, place_a, cab_b, place_b, eps=1.0):
    """Do two ANCHOR-convention cabinets physically overlap? Fast AABB
    path when both rotations are orthogonal (exact there), SAT OBB
    narrow phase otherwise — the single entry point validators should
    use so a 45° manual cabinet is judged by its true oriented box, not
    a wrong axis-aligned branch."""
    def _ortho(place):
        rot = (place.get("rotation_deg", 0) or 0) % 360
        return min(abs(rot - a) for a in (0, 90, 180, 270, 360)) \
            <= ORTHO_TOL_DEG
    if _ortho(place_a) and _ortho(place_b):
        return footprints_overlap(
            footprint_from_anchor_mm(cab_a, place_a),
            footprint_from_anchor_mm(cab_b, place_b), eps=eps)
    return convex_polys_overlap(
        obb_corners_from_anchor_mm(cab_a, place_a),
        obb_corners_from_anchor_mm(cab_b, place_b), eps=eps)


def obb_within_room(cab, place, room, eps=2.0):
    """ANCHOR-convention in-room check valid at any rotation: every
    corner point inside the room rectangle (± eps)."""
    W = room.get("width_mm", 0)
    D = room.get("depth_mm", 0)
    return all(-eps <= px <= W + eps and -eps <= pz <= D + eps
               for px, pz in obb_corners_from_anchor_mm(cab, place))


def within_room(cab, place, room, eps=2.0):
    W = room.get("width_mm", 0)
    D = room.get("depth_mm", 0)
    x0, x1, z0, z1 = footprint_mm(cab, place)
    return (x0 >= -eps and x1 <= W + eps and z0 >= -eps and z1 <= D + eps)


def wall_capacity_mm(room, wall_order):
    """Total along-wall run length available across the walls in wall_order."""
    lengths = _wall_lengths(room)
    return sum(lengths.get(w, 0) for w in wall_order)


def check_capacity(cabinets, room, wall_order=("back", "left")):
    """Pre-flight capacity check (no raising). Returns
    {ok, capacity_mm, requested_mm} where requested is the widest single
    layer's total run length (base and wall layers each run the walls)."""
    cap = wall_capacity_mm(room, wall_order)
    per_layer = {"base": 0.0, "wall": 0.0}
    for c in cabinets:
        per_layer[_layer_of(c)] += c.get("width_mm", 0)
    requested = max(per_layer.values()) if per_layer else 0.0
    return {"ok": requested <= cap, "capacity_mm": cap,
            "requested_mm": requested}

# Fallback zone layout — the ORM caller MUST pass the model's authoritative
# `sale.order._ZONE_LAYOUT` so there is exactly one source of truth. This
# copy exists only so the pure module is self-contained for offline tests.
_DEFAULT_ZONE_LAYOUT = {
    "base_run":  ("ground", 0,    0),
    "wall":      ("wall",   1400, 0),
    "tall":      ("ground", 0,    0),
    "island":    ("island", 0,    -2500),
    "accessory": ("ground", 0,    0),
    "other":     ("other",  0,    -3000),
}
_DEFAULT_WORKTOP_CURSOR = "ground"
_DEFAULT_WORKTOP_Y = 762


def _cabinet_run_key(cab, zone_layout, worktop_cursor):
    """The accumulator bucket a cabinet advances.

    A run is identified by (wall, cursor_name): cabinets on the same wall
    that share a zone-cursor advance the same running offset. Back-wall
    base/tall/accessory all share the "ground" cursor exactly as the legacy
    single-run engine did — so an all-back kitchen is byte-identical.
    """
    if cab.get("family") == "worktop":
        cursor_name = worktop_cursor
    else:
        zone = cab.get("zone") or "base_run"
        cursor_name = zone_layout.get(zone, ("ground", 0, 0))[0]
    return (cab.get("wall") or "back", cursor_name)


def _cabinet_zone_offsets(cab, zone_layout, worktop_y):
    """(y_floor_mm, cross_offset_mm) for a cabinet, by family/zone."""
    if cab.get("family") == "worktop":
        return worktop_y, 0
    zone = cab.get("zone") or "base_run"
    _cursor, y_floor, z_offset = zone_layout.get(zone, ("ground", 0, 0))
    return y_floor, z_offset


def _resolve_runs(cabinets):
    """Group cabinets into ordered runs.

    P0: one run per wall, ordered by (run_seq, input index). This is the
    ONLY place wall-vs-graph organisation lives — the future relationship
    graph replaces this function and nothing else.

    Returns a dict {wall: [cab, cab, ...]} with each list run-ordered.
    """
    runs = {}
    for idx, cab in enumerate(cabinets):
        wall = cab.get("wall") or "back"
        runs.setdefault(wall, []).append((idx, cab))
    for wall in runs:
        runs[wall].sort(key=lambda pair: (pair[1].get("run_seq", 0), pair[0]))
        runs[wall] = [cab for _idx, cab in runs[wall]]
    return runs


def _place_on_wall(wall, along_cursor, cab, y_floor, cross_offset, room):
    """Compute one cabinet's (x, y, z, rotation_deg) on a given wall.

    `along_cursor` is the running offset (mm) already consumed on this wall's
    run before this cabinet; the cabinet's centre sits at along_cursor + w/2.
    `cross_offset` is the zone's cross-axis offset (island/other tuck-away on
    back/front; wall-inset on left/right).
    """
    w = cab.get("width_mm", 0)
    spec = _WALL_SPEC[wall]
    centre = along_cursor + w / 2.0
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)

    if wall == "back":
        return {"x": centre,               "y": y_floor, "z": cross_offset,
                "rotation_deg": spec["rot"]}
    if wall == "front":
        return {"x": centre,               "y": y_floor, "z": room_d - cross_offset,
                "rotation_deg": spec["rot"]}
    if wall == "left":
        return {"x": cross_offset,         "y": y_floor, "z": centre,
                "rotation_deg": spec["rot"]}
    # right
    return {"x": room_w - cross_offset,    "y": y_floor, "z": centre,
            "rotation_deg": spec["rot"]}


def layout(cabinets, room,
           zone_layout=None, worktop_cursor=None, worktop_y=None,
           wall_start_offsets=None):
    """Resolve semantic placement inputs into manufacturable placements.

    Args:
      cabinets: list of dicts. Each: {
          "id": hashable, "width_mm", "height_mm", "depth_mm",
          "family": str, "zone": str (optional),
          "wall": one of WALLS (default "back"),
          "run_seq": int (optional, default 0),
      }
      room: {"width_mm", "depth_mm", "height_mm"}.
      zone_layout / worktop_cursor / worktop_y: the model's authoritative
          constants. Defaults mirror sale.order for offline tests.

    Returns: list of placement dicts, ONE per input cabinet, in the SAME
      order as `cabinets`: {"id", "x", "y", "z", "rotation_deg"} (mm/deg).
      Inputs are never mutated.
    """
    if zone_layout is None:
        zone_layout = _DEFAULT_ZONE_LAYOUT
    if worktop_cursor is None:
        worktop_cursor = _DEFAULT_WORKTOP_CURSOR
    if worktop_y is None:
        worktop_y = _DEFAULT_WORKTOP_Y
    if wall_start_offsets is None:
        wall_start_offsets = {}

    # A cabinet may carry an explicit `__pose` (x/y/z/rotation_deg) — used for
    # "standalone" corner cabinets that don't belong to any run (a both-high
    # corner like front-right, where neither wall's low end is at the corner
    # so it can't be positioned by joining a run). Placed verbatim; consumes
    # no cursor.
    runs = _resolve_runs([c for c in cabinets if not c.get("__pose")])

    # Accumulate along-wall cursors per (wall, cursor_name) and place. A
    # wall's run may start past a reserved footprint (a corner cabinet on
    # the adjoining wall) via wall_start_offsets[wall].
    cursors = {}
    placed_by_id = {}
    for cab in cabinets:
        if cab.get("__pose"):
            pose = dict(cab["__pose"])
            pose["id"] = cab.get("id")
            placed_by_id[id(cab)] = pose
    for wall, run in runs.items():
        for cab in run:
            run_key = _cabinet_run_key(cab, zone_layout, worktop_cursor)
            # wall_start_offsets is keyed by (wall, layer) — a corner
            # resolved for ONE layer (base or wall) on a wall must not
            # shove the OTHER layer's run on that same wall (I1: a
            # base-only corner shouldn't shift the wall/upper run).
            along = cursors.get(
                run_key, wall_start_offsets.get((wall, _layer_of(cab)), 0))
            y_floor, cross_offset = _cabinet_zone_offsets(
                cab, zone_layout, worktop_y)
            pose = _place_on_wall(
                wall, along, cab, y_floor, cross_offset, room)
            pose["id"] = cab.get("id")
            placed_by_id[id(cab)] = pose
            cursors[run_key] = along + cab.get("width_mm", 0)

    # Emit in original input order for stable, caller-friendly output.
    return [placed_by_id[id(cab)] for cab in cabinets]


# ── Corner resolution (P2) ──────────────────────────────────────────────
# An inside 90° corner exists wherever two adjacent walls BOTH carry a run
# in the same layer (base vs wall). Base and wall corners resolve
# independently. The four room corners and the wall pair that forms each:
_CORNER_SPECS = (
    # name,          (wall_a, wall_b),  (x_key, z_key)
    ("back-left",   ("back",  "left"),  ("0", "0")),
    ("back-right",  ("back",  "right"), ("W", "0")),
    ("front-left",  ("front", "left"),  ("0", "D")),
    ("front-right", ("front", "right"), ("W", "D")),
)


def _layer_of(cab):
    """Which corner layer a cabinet belongs to. Wall (upper) cabinets
    corner independently of base/floor cabinets."""
    if cab.get("cabinet_type") == "wall" or cab.get("family") == "wall":
        return "wall"
    return "base"


# Corner-cabinet footprint (square) + per-layer height, millimetres. Defined
# here (ahead of detect_corners) so it can serve as that function's default
# `corner_size_mm`; resolve_and_layout below re-exports/reuses the same
# constant.
_CORNER_FOOTPRINT_MM = 914.0            # 36"
# Task 4 (2026-07-31, height-drift) — base was 876.0mm, a round-metric
# approximation that is actually 34.488" (rounds to 34.49", not 34.50"
# like every other base cabinet in the catalogue). 876.3mm = 25.4 * 34.5
# is inch-exact. wall's 762.0mm = 25.4 * 30 was already inch-exact —
# left unchanged.
_CORNER_HEIGHT_MM = {"base": 876.3, "wall": 762.0}   # 34.5" / 30"
# M3 — a corner filler strip's depth matches its layer's run depth
# (24" base / 12" upper), same convention as the corner heights above.
_CORNER_FILLER_DEPTH_MM = {"base": 610.0, "wall": 305.0}


def _corner_cell_aabb(xk, zk, room, corner_size_mm):
    """World-space AABB of a corner's `corner_size_mm` square cell, anchored
    at the room corner named by (xk, zk) and extending toward the room
    interior."""
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    if xk == "0":
        x0, x1 = 0.0, corner_size_mm
    else:   # "W"
        x0, x1 = room_w - corner_size_mm, room_w
    if zk == "0":
        z0, z1 = 0.0, corner_size_mm
    else:   # "D"
        z0, z1 = room_d - corner_size_mm, room_d
    return (x0, x1, z0, z1)


def detect_corners(cabinets, room, placements=None,
                    corner_size_mm=_CORNER_FOOTPRINT_MM,
                    corner_specs=None):
    """Find every inside 90° corner that needs a corner cabinet.

    Pure + deterministic. Detection only — resolution (footprint
    reservation, standard-cabinet removal, corner SKU selection, run
    re-flow, manufacturing write-back) is layered on top in the Odoo
    integration, which owns product identity.

    When `placements` is given (the placed x/y/z/rotation_deg for each
    cabinet, as returned by `layout()`), a corner is "active" for a layer
    only when BOTH of its two walls have a cabinet in that layer whose
    PLACED footprint actually overlaps the corner's `corner_size_mm` cell
    (geometric occupancy) — merely having *some* cabinet on each wall is
    not enough; it must reach the corner. When `placements` is None (legacy
    callers), falls back to the old wall-occupancy test.

    Returns a list (stable order) of:
      {"corner": "back-left"|..., "layer": "base"|"wall",
       "walls": [wall_a, wall_b],
       "position_mm": {"x": float, "z": float}}
    """
    if corner_specs is None:
        # Legacy default — identical to wall_graph(room)'s junction set
        # for a rectangle (M5 parity, pinned by test).
        corner_specs = _CORNER_SPECS
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    coord = {"0": 0.0, "W": float(room_w), "D": float(room_d)}

    walls_with = {"base": set(), "wall": set()}
    for cab in cabinets:
        walls_with[_layer_of(cab)].add(cab.get("wall") or "back")

    placed_by_id = {p["id"]: p for p in placements} if placements is not None else None

    def _wall_reaches_cell(wall, layer, cell):
        for cab in cabinets:
            if (cab.get("wall") or "back") != wall or _layer_of(cab) != layer:
                continue
            place = placed_by_id.get(cab.get("id"))
            if place is None:
                continue
            if footprints_overlap(footprint_mm(cab, place), cell):
                return True
        return False

    out = []
    for name, (wa, wb), (xk, zk) in corner_specs:
        cell = (_corner_cell_aabb(xk, zk, room, corner_size_mm)
                if placed_by_id is not None else None)
        for layer in ("base", "wall"):
            if placed_by_id is None:
                active = wa in walls_with[layer] and wb in walls_with[layer]
            else:
                active = (_wall_reaches_cell(wa, layer, cell)
                          and _wall_reaches_cell(wb, layer, cell))
            if active:
                out.append({
                    "corner": name,
                    "layer": layer,
                    "walls": [wa, wb],
                    "position_mm": {"x": coord[xk], "z": coord[zk]},
                })
    return out


# ── Auto-distribution (P1) ──────────────────────────────────────────────
# Wrap a flat cabinet list across walls so an L/U kitchen falls out of a
# straight list without any UI. This is ONE layout strategy (the graph /
# drag editor will supply explicit wall assignments later); it only
# produces the semantic (wall, run_seq) inputs the engine already consumes.

def _wall_lengths(room):
    w = room.get("width_mm", 0)
    d = room.get("depth_mm", 0)
    return {"back": w, "front": w, "left": d, "right": d}


def auto_assign_walls(cabinets, room, wall_order=("back", "left")):
    """Assign each cabinet a wall + run_seq by filling walls in wall_order,
    wrapping to the next wall when the current one is full. Base and wall
    (upper) layers distribute independently over the same wall order, so
    uppers land on the same walls as their bases. Pure; returns COPIES with
    'wall'/'run_seq' set (inputs untouched)."""
    wall_len = _wall_lengths(room)
    result = [dict(c) for c in cabinets]
    for layer in ("base", "wall"):
        wi = 0
        used = 0.0
        seq = 0
        for cab in [c for c in result if _layer_of(c) == layer]:
            w = cab.get("width_mm", 0)
            # Wrap when this cabinet would overflow the current wall — but
            # always keep at least one cabinet per wall (used > 0), and
            # never past the last wall (the last wall takes the remainder).
            while (wi < len(wall_order) - 1 and used > 0
                   and used + w > wall_len[wall_order[wi]]):
                wi += 1
                used = 0.0
                seq = 0
            cab["wall"] = wall_order[wi]
            cab["run_seq"] = seq
            used += w
            seq += 1
    return result


# Corner resolution geometry, keyed by corner name:
#   (host_wall, other_wall, other_end, handed)
#   host_wall  — the run whose LOW end is at this corner; the corner cabinet
#                joins it at run_seq -1 (consuming its cursor so the run
#                flows past the reserved cell).
#   other_wall — the adjoining run.
#   other_end  — where the adjoining run meets the corner: "first" (its low
#                end → also offset it by cs) or "last" (its high end simply
#                terminates at the cell → no offset).
#   handed     — corner handedness hint for downstream SKU selection.
# Covers every corner that has a LOW-end host (a run whose origin is at the
# corner). front-right has neither wall's low end at it → _CORNER_STANDALONE.
_CORNER_RESOLVE = {
    "back-left":   ("back",  "left",  "first", "L"),
    "back-right":  ("right", "back",  "last",  "R"),
    "front-left":  ("front", "left",  "last",  "R"),
}

# "Both-high" corners: neither wall's LOW end is at the corner, so the corner
# cabinet can't be positioned by joining a run at run_seq -1. It is placed
# STANDALONE (explicit __pose filling the reserved cell), and both adjoining
# runs are capped — any standard cabinet overlapping the cell is removed
# (replaced by the corner). Keyed by name → (x_key, z_key) of the cell's FAR
# corner (the room corner); the cell is the cs-square inset from there.
_CORNER_STANDALONE = {
    "front-right": ("W", "D"),
}

# `(x_key, z_key)` of each _CORNER_RESOLVE corner's OWN cell (not the "far"
# corner used by _CORNER_STANDALONE) — reuses the same keys as _CORNER_SPECS,
# indexed by corner name for the pose math below.
_CORNER_XZ_KEYS = {name: (xk, zk) for name, _walls, (xk, zk) in _CORNER_SPECS}

# ── ROOM_CORNER_PLACEMENT — the ONE presentation table (corner-orientation
# fix, 2026-07-31) ───────────────────────────────────────────────────────
# SUPERSEDES `_CORNER_RESOLVE_PRESENTATION` (the per-corner (wall_tag,
# rotation_deg) table this used to be, and the per-side "host_wall"/
# hardcoded-standalone-rotation ad-hoc math scattered through
# `resolve_and_layout` below). Root-caused (verified by recomputing the
# transforms, 2026-07-31): the renderer applies `grp.rotation.y =
# rotation_deg` (kitchen_canvas.esm.js's `_placeCabinetGroup`) to a corner
# mesh whose LOCAL frame (corner_cabinet.esm.js) fixes local (0,0) at the
# ANCHOR and the diagonal/pie door face at local (width_in, depth_in) with
# outward normal (+1/√2, +1/√2). THREE's rotation.y=θ convention: local +x
# -> world (cosθ, -sinθ), local +z -> world (sinθ, cosθ) (matches this
# module's own `obb_corners_from_anchor_mm`, pinned by test against
# `footprint_from_anchor_mm`). So the correct θ for a given room corner is
# whichever value makes BOTH local-axis images point INTO the room (away
# from this corner's own two walls) — solving that for each of the four
# room corners gives the table below. The anchor itself is ALWAYS the room
# corner point exactly — (x_key, z_key) never need an ext_x/ext_z offset —
# because `_corner_node_pose_rect` + `anchor_pose_mm` (untouched by this
# fix) are already engineered so ANY of the four ALLOWED_ROTATIONS, paired
# with the matching node_w/node_d swap, converts a `_corner_cell_rect`
# anchored at (x_key, z_key) back to that SAME (x_key, z_key) anchor point
# — that invariant is exactly what let the pre-fix code "self-consistently"
# emit a WRONG rotation (90 instead of 0 for back-left) whose anchor-
# convention AABB was still pixel-identical to the correct one for a
# SQUARE cabinet: (x=0, z=0, rot=0) and (x=0, z=36in, rot=90) both cover
# footprint (0,36,0,36) — invisible to any AABB/occupancy-only audit (both
# prior corner-remediation passes checked exactly that and missed it). A
# NON-square corner (asymmetric legs, M2 rule-driven) makes the same wrong
# choice visibly wrong even in the AABB (see
# test_layout_engine.py::test_asymmetric_corner_rejects_wrong_rotation),
# which is why that shape is now a pinned regression test, not just the
# legacy square.
#
# Historical note — an EARLIER fix (pre-Task-8, when corner cabinets still
# rendered as a plain box via `buildOtherCabinet`, no real diagonal
# geometry) deliberately picked wall="left"/rot=90 for back-left specifically
# to stop the corner LOOKING like an ordinary back-wall cabinet ("vanishing"
# — a plain box has no visual tell for which way it's turned). That concern
# is now moot: `corner_cabinet.esm.js` (Task 8, 2026-07-30) renders a real
# diagonal-cut/pie-cut notch, which is visually distinct from a plain
# cabinet at ANY rotation — so there is no longer a reason to prefer a
# rotation other than the geometrically-correct one, and the geometrically
# correct one for back-left is 0.
#
# `wall_tag` (persisted to kitchen.design.line.wall) is derived from
# rotation_deg via `_wall_tag_for_rotation` — the wall whose OWN
# `_WALL_SPEC` rotation matches, so wall_tag and rotation_deg are always
# self-consistent. This is REQUIRED, not cosmetic: kitchen_design.py's
# `_check_production_ready` motion-envelope check (#12) derives which
# neighbouring wall's flush cabinet is the corner's OWN legitimate run
# continuation (`_facing_neighbor_wall`) from `_CORNER_RESOLVE[cname]` +
# the line's persisted `wall` — that derivation only lines up with the
# envelope's REAL facing direction (which comes from rotation_deg) when
# wall_tag and rotation_deg agree via this SAME `_WALL_SPEC` pairing.
#
#   back-left   -> rot 0   (matches "back" wall's own convention — by
#                  coincidence of geometry, NOT a re-introduction of the
#                  pre-Task-8 "vanishing" bug; see the historical note).
#   back-right  -> rot 270 (matches "right"). UNCHANGED — already correct
#                  pre-fix; pinned as a regression guard
#                  (test_room_corner_placement_back_right_unchanged).
#   front-left  -> rot 90  (matches "left"). Was 180 pre-fix.
#   front-right -> rot 180 (matches "front"). Was hardcoded 0 pre-fix (the
#                  `_CORNER_STANDALONE` branch below never went through
#                  `_CORNER_RESOLVE_PRESENTATION` at all).
ROOM_CORNER_PLACEMENT = {
    # corner name  -> (x_key, z_key, rotation_deg). x_key/z_key MUST match
    # `_CORNER_SPECS`'s own keys for the same corner — pinned by
    # test_room_corner_placement_matches_corner_specs_xz_keys so the two
    # tables can never silently drift apart.
    "back-left":   ("0", "0", 0),
    "back-right":  ("W", "0", 270),
    "front-left":  ("0", "D", 90),
    "front-right": ("W", "D", 180),
}


def _wall_tag_for_rotation(rotation_deg):
    """The WALLS member whose own `_WALL_SPEC` rotation equals
    `rotation_deg` — i.e. which wall's presentation convention this
    rotation matches. Keeps a corner node's persisted `wall` field and
    its `rotation_deg` self-consistent (see the module-level comment
    above `ROOM_CORNER_PLACEMENT`)."""
    rot = int(round(rotation_deg)) % 360
    return next(w for w, spec in _WALL_SPEC.items() if spec["rot"] == rot)


def room_corner_anchor_pose(corner_name, room):
    """The exact (x, z, rotation_deg) anchor pose for `corner_name`
    (one of `ROOM_CORNER_PLACEMENT`'s keys) in `room` — resolves the
    table's symbolic x_key/z_key ("0"/"W"/"0"/"D") against this room's
    actual width/depth. Pure convenience wrapper; the anchor is always
    exactly the room corner point (see the module comment above), never
    offset by a cabinet's own width/depth."""
    xk, zk, rotation_deg = ROOM_CORNER_PLACEMENT[corner_name]
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    x = 0.0 if xk == "0" else float(room_w)
    z = 0.0 if zk == "0" else float(room_d)
    return {"x": x, "z": z, "rotation_deg": rotation_deg}


def _corner_node_pose(xk, zk, room, corner_size_mm, rotation_deg, y_floor):
    """Explicit (x, y, z, rotation_deg) pose whose footprint AABB (per
    `footprint_mm`'s own placement convention) is EXACTLY the corner's
    `corner_size_mm` cell, for any of the four `ALLOWED_ROTATIONS`. Used so
    the inserted corner node's `__pose` lands in the corner cell regardless
    of which run's cursor (`host_wall`) it was bookkept against."""
    cell = _corner_cell_aabb(xk, zk, room, corner_size_mm)
    return _corner_node_pose_rect(cell, rotation_deg, y_floor)


# ── M2 (2026-07-27) — per-leg (asymmetric) corner cells ─────────────────
# A corner cabinet does not necessarily consume the SAME wall length on
# both legs: a blind corner runs ~45" along its host wall but only its
# 24" depth along the other. The square `corner_size_mm` cell above stays
# as the legacy default; these rect variants carry independent extents.
# Convention (matches _CORNER_SPECS): every corner's wall pair is
# (wall_a, wall_b) with wall_a X-running (back/front) and wall_b
# Z-running (left/right); `ext_x` is the consumption along wall_a's axis,
# `ext_z` along wall_b's.

def _corner_cell_rect(xk, zk, room, ext_x, ext_z):
    """World-space AABB of a corner's rectangular cell: `ext_x` along the
    X axis by `ext_z` along Z, anchored at the room corner named by
    (xk, zk), extending toward the room interior."""
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    if xk == "0":
        x0, x1 = 0.0, ext_x
    else:   # "W"
        x0, x1 = room_w - ext_x, room_w
    if zk == "0":
        z0, z1 = 0.0, ext_z
    else:   # "D"
        z0, z1 = room_d - ext_z, room_d
    return (x0, x1, z0, z1)


def _corner_node_pose_rect(cell, rotation_deg, y_floor):
    """Centre-convention pose whose `footprint_mm` AABB is EXACTLY `cell`
    for the given rotation. The caller must size the node so width runs
    along the rotation's along-axis (rot 0/180 → width spans x, rot
    90/270 → width spans z)."""
    x0, x1, z0, z1 = cell
    rot = int(round(rotation_deg)) % 360
    if rot == 0:
        x, z = (x0 + x1) / 2.0, z0
    elif rot == 180:
        x, z = (x0 + x1) / 2.0, z1
    elif rot == 90:
        x, z = x0, (z0 + z1) / 2.0
    else:   # 270
        x, z = x1, (z0 + z1) / 2.0
    return {"x": x, "y": y_floor, "z": z, "rotation_deg": rot}


def _select_corner_rule(corner_name, layer, corner_rules, room):
    """M2 — pick the placement rule for a detected corner, or None.

    A rule is a plain dict (see southbrook.placement.rule.engine_dicts):
      {"rule_id", "sku", "tier": "base"|"wall", "sequence",
       "leg_x_mm", "leg_z_mm", "height_mm",
       "min_leg_x_mm", "min_leg_z_mm", "host_leg": "x"|"z" (optional)}

    Filter: tier must match the corner's layer, and each of the corner's
    two walls must be long enough for the rule's min leg on that axis.
    Rank: ascending `sequence`, then rule_id for determinism. Returns the
    winning rule dict or None (caller falls back to the legacy constants
    and records a diagnostic).
    """
    if not corner_rules:
        return None
    walls = {w for _n, ws, _xz in _CORNER_SPECS
             for w in (ws if _n == corner_name else ())}
    if not walls:
        return None
    lengths = _wall_lengths(room)
    # wall_a is the X-running member, wall_b the Z-running member.
    len_x = min(lengths[w] for w in walls if w in ("back", "front"))
    len_z = min(lengths[w] for w in walls if w in ("left", "right"))
    fits = [r for r in corner_rules
            if r.get("tier") == layer
            and len_x >= (r.get("min_leg_x_mm") or 0)
            and len_z >= (r.get("min_leg_z_mm") or 0)
            and (r.get("leg_x_mm") or 0) > 0
            and (r.get("leg_z_mm") or 0) > 0]
    if not fits:
        return None
    fits.sort(key=lambda r: (r.get("sequence", 100), r.get("rule_id") or ""))
    return fits[0]


def resolve_and_layout(cabinets, room, corner_size_mm=_CORNER_FOOTPRINT_MM,
                       wall_order=("back", "left"), auto_assign=True,
                       corner_rules=None, graph=None,
                       **layout_kwargs):
    """Full pipeline: (optionally distribute a flat cabinet list across
    walls), detect inside corners, insert a corner-cabinet node at each
    resolved corner (reserving its footprint so nothing overlaps), and lay
    everything out. Pure + deterministic. The engine does NOT assign product
    SKUs — it tags the inserted node `corner_cabinet=True` + corner/layer/
    handed and the Odoo layer maps that to SB-CORNER* / SB-WALL-CORNER.

    auto_assign=True  → wrap a flat list across walls (the button on an
                        unassigned order).
    auto_assign=False → RESPECT the cabinets' existing wall/run_seq (the
                        interactive flow, where the user placed each cabinet
                        on a wall) and just resolve corners in place.

    Resolves ALL FOUR inside corners: back-left, back-right and front-left
    have a low-end host (a run whose origin sits at the corner) so the corner
    cabinet joins that run at run_seq -1; front-right is "both-high" (neither
    wall's low end is there) so it is placed standalone in its cell and both
    adjoining runs are capped. Handles an L, a U, or a full G-shape.

    corner_rules (M2, 2026-07-27): optional list of plain rule dicts (see
    `_select_corner_rule`) — rules-as-data replacing the constant square
    cell. Per resolved corner, the winning rule supplies the SKU, per-leg
    wall consumption (asymmetric corners like blind units), height, and
    optionally which wall the box runs along (`host_leg`). When None/empty
    or when no rule fits a given corner, that corner falls back to the
    legacy `corner_size_mm` square + the Odoo layer's default SKU mapping,
    and a diagnostic is recorded in the returned "corner_diagnostics".

    Returns {"cabinets", "assigned", "placements", "corners", "inserted",
    "removed_ids", "corner_diagnostics"}.
    """
    # M5 — the room is a wall GRAPH. Default: the rectangle
    # specialization, whose junction set equals _CORNER_SPECS exactly
    # (parity pinned by test). A custom graph's non-orthogonal or
    # non-canonical junctions are refused up front (M6) with a
    # diagnostic — the engine never guesses at a corner it has no
    # placement math for.
    if graph is None:
        graph = wall_graph(room)
    auto_specs, refused_junctions = _graph_corner_specs(graph)

    assigned = (auto_assign_walls(cabinets, room, wall_order) if auto_assign
                else [dict(c) for c in cabinets])
    # Detect corners against REAL placed footprints (geometric occupancy),
    # not mere wall occupancy — a provisional layout of the as-assigned
    # cabinets (before any corner insertion/removal) is enough to tell
    # whether each run's cabinets actually reach the shared corner cell.
    provisional_placements = layout(assigned, room, **layout_kwargs)
    corners = detect_corners(assigned, room, placements=provisional_placements,
                              corner_size_mm=corner_size_mm,
                              corner_specs=auto_specs)
    inserted = []
    fillers = []            # M3 — rule-demanded corner filler strips
    removed_ids = set()
    offsets = {}
    standalone_cells = []   # [(cell_aabb, corner_node)] for the cap pass
    corner_diagnostics = []
    for j, reason in refused_junctions:
        corner_diagnostics.append({
            "corner": j.get("id"), "layer": None, "reason": reason})
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    zl = layout_kwargs.get("zone_layout") or _DEFAULT_ZONE_LAYOUT

    def _run_end(cabs, wall, layer, end):
        run = [c for c in cabs if (c.get("wall") or "back") == wall
               and _layer_of(c) == layer and not c.get("corner_cabinet")]
        pick = min if end == "first" else max
        return pick(run, key=lambda c: c.get("run_seq", 0), default=None)

    def _corner_geometry(corner, layer):
        """(rule, ext_x, ext_z, height_mm) for one detected corner —
        rule-driven when a rule fits (M2), legacy square otherwise."""
        rule = _select_corner_rule(corner["corner"], layer, corner_rules,
                                   room)
        if rule is None:
            if corner_rules:
                corner_diagnostics.append({
                    "corner": corner["corner"], "layer": layer,
                    "reason": "no placement rule fits — legacy %.0fmm "
                              "square used" % corner_size_mm,
                })
            return None, corner_size_mm, corner_size_mm, \
                _CORNER_HEIGHT_MM[layer]
        return (rule, rule["leg_x_mm"], rule["leg_z_mm"],
                rule.get("height_mm") or _CORNER_HEIGHT_MM[layer])

    for corner in corners:
        layer = corner["layer"]
        if corner["corner"] in _CORNER_STANDALONE:
            # Both-high corner (front-right): place the corner cabinet
            # STANDALONE filling its cell, and mark the cell for the cap pass.
            rule, ext_x, ext_z, height_mm = _corner_geometry(corner, layer)
            cell = (room_w - ext_x, room_w, room_d - ext_z, room_d)
            y_floor = zl.get("wall" if layer == "wall" else "base_run",
                             ("ground", 0, 0))[1]
            _xk_fr, _zk_fr, fr_rotation_deg = ROOM_CORNER_PLACEMENT[
                corner["corner"]]
            node = {
                "id": "corner-%s-%s" % (corner["corner"], layer),
                "corner_cabinet": True,
                "corner": corner["corner"],
                "handed": "L",
                "layer": layer,
                "cabinet_type": "wall" if layer == "wall" else "base",
                "family": "wall" if layer == "wall" else "base",
                # rot 180 (ROOM_CORNER_PLACEMENT["front-right"]) → width
                # still spans X, depth still spans Z (footprint_mm: both
                # rot 0 and rot 180 keep width on the along-X axis).
                "width_mm": ext_x,
                "depth_mm": ext_z,
                "height_mm": height_mm,
                "zone": "wall" if layer == "wall" else "base_run",
                "wall": _wall_tag_for_rotation(fr_rotation_deg),
                # Explicit pose whose AABB is exactly the cell, oriented
                # so the door notch (local (w,d), normal (+1/√2,+1/√2))
                # faces into the room instead of into the front/right
                # walls — see the ROOM_CORNER_PLACEMENT module comment.
                "__pose": _corner_node_pose_rect(
                    cell, fr_rotation_deg, y_floor),
                "replaced_ids": [],
            }
            if rule is not None:
                node["rule_id"] = rule.get("rule_id")
                node["sku"] = rule.get("sku")
            inserted.append(node)
            standalone_cells.append((cell, node))
            continue
        spec = _CORNER_RESOLVE.get(corner["corner"])
        if spec is None:
            continue   # remaining corners not yet footprint-reserved
        host_wall, other_wall, other_end, handed = spec
        rule, ext_x, ext_z, height_mm = _corner_geometry(corner, layer)
        # The corner cabinet REPLACES the two standard cabinets that meet at
        # the corner — otherwise they'd overlap the corner cell / overflow.
        # `host_wall` identifies the run whose LOW end is at this corner —
        # used ONLY to find which two standard cabinets to remove and to
        # reserve that run's cursor space (via `offsets[host_wall]` below).
        # It is deliberately NOT used to derive the emitted `wall`/rotation
        # (see `ROOM_CORNER_PLACEMENT`) — the corner gets an explicit
        # `__pose` centred in ITS OWN corner cell, so it renders in the
        # corner regardless of which run's cursor it was bookkept against.
        host_first = _run_end(assigned, host_wall, layer, "first")
        other_cab = _run_end(assigned, other_wall, layer, other_end)
        for c in (host_first, other_cab):
            if c is not None:
                removed_ids.add(c["id"])
        _xk_p, _zk_p, rotation_deg = ROOM_CORNER_PLACEMENT[corner["corner"]]
        wall_tag = _wall_tag_for_rotation(rotation_deg)
        # M2 — a rule may pin which wall the box runs along (`host_leg`):
        # "x" → the pair's X-running wall (wall_a), "z" → Z-running
        # (wall_b). A blind corner genuinely runs flush along one wall —
        # its presentation IS that wall's, even when the default table
        # would tag the other (e.g. back-left defaults to back/0, but a
        # host_leg="z" blind box lies along the left wall at rot 90 and
        # correctly renders like a normal left-wall cabinet with a blind
        # face).
        if rule is not None and rule.get("host_leg") in ("x", "z"):
            pair = next(ws for n, ws, _xz in _CORNER_SPECS
                        if n == corner["corner"])
            wall_tag = (pair[0] if rule["host_leg"] == "x" else pair[1])
            rotation_deg = _WALL_SPEC[wall_tag]["rot"]
        xk, zk = _CORNER_XZ_KEYS[corner["corner"]]
        y_floor = zl.get("wall" if layer == "wall" else "base_run",
                         ("ground", 0, 0))[1]
        cell = _corner_cell_rect(xk, zk, room, ext_x, ext_z)
        pose = _corner_node_pose_rect(cell, rotation_deg, y_floor)
        # Node dims follow the pose convention (footprint_mm): width runs
        # along the rotation's along-axis. rot 0/180 → width spans the
        # cell's X extent; rot 90/270 → width spans its Z extent.
        rot_n = int(round(rotation_deg)) % 360
        node_w = ext_x if rot_n in (0, 180) else ext_z
        node_d = ext_z if rot_n in (0, 180) else ext_x
        node = {
            "id": "corner-%s-%s" % (corner["corner"], layer),
            "corner_cabinet": True,
            "corner": corner["corner"],
            "handed": handed,
            "layer": layer,
            "cabinet_type": "wall" if layer == "wall" else "base",
            "family": "wall" if layer == "wall" else "base",
            "width_mm": node_w,
            "depth_mm": node_d,
            "height_mm": height_mm,
            "zone": "wall" if layer == "wall" else "base_run",
            "wall": wall_tag,
            "run_seq": -1,
            "__pose": pose,
            "replaced_ids": [c["id"] for c in (host_first, other_cab)
                             if c is not None],
        }
        if rule is not None:
            node["rule_id"] = rule.get("rule_id")
            node["sku"] = rule.get("sku")
        inserted.append(node)
        # The corner no longer consumes host_wall's run cursor by joining it
        # (it carries an explicit __pose instead), so host_wall's own
        # surviving cabinets need the SAME explicit start-offset the
        # adjoining run gets below — host_wall's low end is always at this
        # corner by construction (that's what makes it "host").
        #
        # I1: keyed by (wall, layer) — NOT just wall — so a base-layer
        # corner's offset never bleeds into that same wall's wall (upper)
        # run, and vice versa. Each detected corner is already scoped to
        # exactly one layer (`corner["layer"]`); the offset must stay
        # scoped to it too.
        # M2 — the reservation is PER LEG: each wall's run starts past the
        # corner's consumption ALONG THAT WALL's axis (X-running walls use
        # ext_x, Z-running use ext_z). Symmetric rules and the legacy
        # square reduce to the old single corner_size_mm behavior.
        #
        # M3 — a rule may additionally demand a FILLER strip on a leg
        # (filler_x_mm / filler_z_mm — e.g. the 3" blind-corner filler
        # per KraftMaid/NKBA, docs 02/06 `filler-blind-corner-min`). The
        # filler is a REAL node (emitted below, → BOM/cutlist in the ORM
        # layer) and the leg's run starts past corner + filler.
        filler_x = (rule.get("filler_x_mm") or 0.0) if rule else 0.0
        filler_z = (rule.get("filler_z_mm") or 0.0) if rule else 0.0

        def _leg_for(wall):
            return ext_x if wall in ("back", "front") else ext_z

        def _filler_for(wall):
            return filler_x if wall in ("back", "front") else filler_z

        def _emit_filler(wall):
            fw = _filler_for(wall)
            if fw <= 0:
                return
            fdepth = _CORNER_FILLER_DEPTH_MM[layer]
            # Strip cell: between the corner cell's interior edge and the
            # run start on `wall`, hugging that wall.
            if wall in ("back", "front"):
                if xk == "0":
                    fx0, fx1 = ext_x, ext_x + fw
                else:   # "W"
                    fx0, fx1 = room_w - ext_x - fw, room_w - ext_x
                fz0, fz1 = (0.0, fdepth) if zk == "0" else (room_d - fdepth,
                                                            room_d)
            else:
                if zk == "0":
                    fz0, fz1 = ext_z, ext_z + fw
                else:   # "D"
                    fz0, fz1 = room_d - ext_z - fw, room_d - ext_z
                fx0, fx1 = (0.0, fdepth) if xk == "0" else (room_w - fdepth,
                                                            room_w)
            frot = _WALL_SPEC[wall]["rot"]
            cell_f = (fx0, fx1, fz0, fz1)
            fillers.append({
                "id": "cornerfill-%s-%s-%s" % (corner["corner"], layer,
                                               wall),
                "corner_filler": True,
                "corner": corner["corner"],
                "layer": layer,
                "cabinet_type": "filler",
                "family": "filler",
                # width runs along the strip's wall (pose convention).
                "width_mm": fw,
                "depth_mm": fdepth,
                "height_mm": height_mm,
                "zone": "wall" if layer == "wall" else "accessory",
                "wall": wall,
                "__pose": _corner_node_pose_rect(cell_f, frot, y_floor),
                "rule_id": rule.get("rule_id") if rule else None,
            })

        offsets[(host_wall, layer)] = max(
            offsets.get((host_wall, layer), 0),
            _leg_for(host_wall) + _filler_for(host_wall))
        _emit_filler(host_wall)
        if other_end == "first":
            # The adjoining low-end run must start past corner + filler.
            offsets[(other_wall, layer)] = max(
                offsets.get((other_wall, layer), 0),
                _leg_for(other_wall) + _filler_for(other_wall))
            _emit_filler(other_wall)
        elif _filler_for(other_wall) > 0:
            # High-end adjoining run (it TERMINATES at the corner): the
            # engine cannot reserve space for a filler there — the run's
            # last cabinet may legitimately end flush at the cell, and a
            # strip emitted into that gap could silently overlap it (the
            # postcondition checks in-room, not pairwise overlap). Skip
            # the strip and record why; the installer scribes this edge
            # on site (docs 05 `absorb-out-of-square-at-shorter-leg-end`).
            corner_diagnostics.append({
                "corner": corner["corner"], "layer": layer,
                "reason": "filler on %s skipped: run terminates at the "
                          "corner (high-end leg) — scribe on site"
                          % other_wall,
            })

    final = [dict(c) for c in assigned if c["id"] not in removed_ids]
    final.extend(dict(n) for n in inserted)
    # M3 — filler strips are real, solid layout members: they ride the
    # postcondition (in-room) check and reach the ORM layer for BOM.
    final.extend(dict(f) for f in fillers)
    placements = layout(final, room, wall_start_offsets=offsets,
                        **layout_kwargs)

    # Cap pass — a standalone (both-high) corner reserves its cell but can't
    # push a run past it the way a low-end join does, so any standard cabinet
    # overlapping the cell is removed (replaced by that corner) and the layout
    # re-run. The overlappers are always the runs' HIGH-end cabinets, so
    # dropping them never shifts a survivor into the cell — one pass converges.
    if standalone_cells:
        place_by_id = {p["id"]: p for p in placements}
        capped = set()
        for cell, node in standalone_cells:
            for c in final:
                if (c.get("corner_cabinet") or c.get("corner_filler")
                        or c["id"] in capped):
                    continue
                if footprints_overlap(
                        footprint_mm(c, place_by_id[c["id"]]), cell):
                    capped.add(c["id"])
                    node["replaced_ids"].append(c["id"])
        if capped:
            removed_ids |= capped
            final = [c for c in final if c["id"] not in capped]
            placements = layout(final, room, wall_start_offsets=offsets,
                                **layout_kwargs)

    # Enforced postcondition: every emitted layout is physically valid.
    # If a cabinet spilled outside the room, the walls can't hold the run —
    # fail explicitly instead of returning an unmanufacturable layout.
    by_id = {c["id"]: c for c in final}
    place_by_id = {p["id"]: p for p in placements}
    room_w = room.get("width_mm", 0)
    room_d = room.get("depth_mm", 0)
    outside = []
    for c in final:
        cid = c["id"]
        if within_room(by_id[cid], place_by_id[cid], room):
            continue
        x0, x1, z0, z1 = footprint_mm(by_id[cid], place_by_id[cid])
        overflow_mm = max(0.0, -x0, x1 - room_w, -z0, z1 - room_d)
        outside.append({"id": cid, "overflow_mm": overflow_mm,
                        "wall": by_id[cid].get("wall") or "back"})
    if outside:
        cap = wall_capacity_mm(room, wall_order)
        req = check_capacity(cabinets, room, wall_order)["requested_mm"]
        worst = max(outside, key=lambda o: o["overflow_mm"])
        detail = ("%d cabinet(s) extend past the end of their wall; "
                  "worst: id=%s overflows %.0fmm on wall=%s"
                  % (len(outside), worst["id"], worst["overflow_mm"],
                     worst["wall"]))
        raise LayoutCapacityExceeded(cap, req, detail=detail, outside=outside)

    # "assigned" — every input cabinet's post-distribution wall/run_seq,
    # INCLUDING the cabinets a corner replaced (which "cabinets" omits).
    # The ORM caller must persist wall/run_seq for the replaced-and-
    # archived cabinets too: if it doesn't, a later reset-and-restore
    # resurrects them with their PRE-distribution wall, reconstructing a
    # different (possibly overfull) run than the one this call resolved
    # — the auto-arrange idempotence break (2026-07-26).
    return {"cabinets": final, "assigned": assigned,
            "placements": placements, "corners": corners,
            "inserted": inserted, "fillers": fillers,
            "removed_ids": sorted(removed_ids),
            "corner_diagnostics": corner_diagnostics}
