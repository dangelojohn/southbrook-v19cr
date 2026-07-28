# -*- coding: utf-8 -*-
"""End-to-end L-shaped kitchen workflow, driven through the SAME server
routes the backend 3D Kitchen Configurator client action calls.

Scenario: Wall A (back) 4 base + 4 wall; Wall B (left) 4 base + 4 wall;
expected corner: 1 base corner + 1 wall corner. Nothing is committed.
"""
from unittest.mock import MagicMock
from odoo.addons.southbrook_kitchen_3d_configurator.controllers import main as c3d
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E

import builtins, functools
_out = open('/tmp/e2e_out.txt', 'w')
print = functools.partial(builtins.print, file=_out, flush=True)

MM = 25.4
STEP = [0]


def hdr(t):
    STEP[0] += 1
    print("\n" + "=" * 74)
    print("STEP %d — %s" % (STEP[0], t))
    print("=" * 74)


def note(t):
    print("   . " + t)


def bad(t):
    print("   !! DEFECT: " + t)


# ── stub `request` exactly as the controller sees it ────────────────────
saved = c3d.request
mock = MagicMock()
mock.env = env
mock.session = {}
mock.params = {}
c3d.request = mock
ctl = c3d.SouthbrookKitchenConfiguratorController()

try:
    hdr("Create a new kitchen project — load the catalog (/products)")
    prods = ctl.products()
    cat = prods.get("products", prods) if isinstance(prods, dict) else prods
    by_type = {}
    for p in cat:
        by_type.setdefault(p.get("cabinet_type"), []).append(p)
    print("   catalog types: %s" % {k: len(v) for k, v in by_type.items()})
    base_p = by_type.get("base", [{}])[0]
    wall_p = by_type.get("wall", [{}])[0]
    corner_p = (by_type.get("corner") or [None])[0]
    print("   base   product: %s w=%s d=%s" % (base_p.get("name"), base_p.get("width_in"), base_p.get("depth_in")))
    print("   wall   product: %s w=%s d=%s" % (wall_p.get("name"), wall_p.get("width_in"), wall_p.get("depth_in")))
    print("   corner product in catalog: %s" % (corner_p.get("name") if corner_p else "*** NONE ***"))
    if not corner_p:
        bad("no cabinet_type='corner' product is offered by /products — "
            "the user cannot manually place a corner cabinet at all.")

    hdr("Select the room layout — 120x120x96 in (square room, L kitchen)")
    room = {"width_in": 120.0, "depth_in": 120.0, "height_in": 96.0}
    print("   room = %s" % room)

    # ── client-side emulation: _addCabinetFromProduct + _recomputeLayoutFromItems
    items = []

    def add(product, wall, kind):
        """Mirror _addCabinetFromProduct (kitchen_configurator.js:812)."""
        t = product.get("cabinet_type") or "base"
        mount_y = 0.0
        if t == "wall":
            prev = [i for i in items if i["cabinet_type"] == "wall"]
            mount_y = prev[0]["y_position_in"] if prev else (34.5 + 1.5 + 18.0)
        it = {
            "product_id": product["product_id"],
            "cabinet_type": t,
            "layout_key": "%s-add-%d-%s" % (t, len(items), wall),
            "x_position_in": 1e6,          # the 1e6 append sentinel
            "y_position_in": mount_y,
            "z_position_in": 0.0,
            "width_in": product.get("width_in") or 24.0,
            "height_in": product.get("height_in") or (30.0 if t == "wall" else 34.5),
            "depth_in": product.get("depth_in") or (12.0 if t == "wall" else 24.0),
            "price": product.get("price") or 0.0,
            "pinned": False,
            "rotation_deg": 0.0,
            "wall": wall,
        }
        items.append(it)
        recompute()
        return it

    def pack_row(row):
        """canvas/pack_row.esm.js — unpinned pack from 0."""
        cur = 0.0
        for u in [i for i in row if not i.get("pinned")]:
            u["x_position_in"] = cur
            cur += u.get("width_in") or 0.0

    def recompute():
        """kitchen_configurator.js:_recomputeLayoutFromItems — packs the
        BACK-WALL subset of each bucket only (PR4)."""
        sx = lambda i: i.get("x_position_in") or 0.0
        is_back = lambda i: (i.get("wall") or "back") == "back"
        bases = sorted([i for i in items if i["cabinet_type"] == "base"], key=sx)
        walls = sorted([i for i in items if i["cabinet_type"] == "wall"], key=sx)
        tails = sorted([i for i in items if i["cabinet_type"] in ("tall", "corner")], key=sx)
        pack_row([i for i in bases if is_back(i)])
        pack_row([i for i in walls if is_back(i)])
        pack_row([i for i in tails if is_back(i)])

    def current_run_width():
        """kitchen_configurator.js:906 — sums EVERY item, wall-blind."""
        by = {}
        for i in items:
            by[i["cabinet_type"]] = by.get(i["cabinet_type"], 0.0) + (i.get("width_in") or 0.0)
        floor_row = sum(by.get(k, 0.0) for k in ("base", "filler", "tall", "panel", "corner", "other"))
        return max(floor_row, by.get("wall", 0.0))

    hdr("Wall A (back) — add 4 base + 4 wall cabinets")
    for _ in range(4):
        add(base_p, "back", "base")
    for _ in range(4):
        add(wall_p, "back", "wall")
    for i in items:
        print("   %-22s wall=%-5s x=%6.1f y=%5.1f z=%4.1f" % (
            i["layout_key"], i["wall"], i["x_position_in"], i["y_position_in"], i["z_position_in"]))
    req = current_run_width()
    print("   _currentRunWidthIn() = %.1f  (room.width_in = %.1f)" % (req, room["width_in"]))

    hdr("Wall B (left) — select the left wall, add 4 base + 4 wall cabinets")
    for _ in range(4):
        add(base_p, "left", "base")
    for _ in range(4):
        add(wall_p, "left", "wall")
    req = current_run_width()
    print("   _currentRunWidthIn() = %.1f  (room.width_in = %.1f)" % (req, room["width_in"]))
    if req > room["width_in"]:
        room["width_in"] = -(-req // 6) * 6
        bad("client auto-expanded ROOM WIDTH to %.1f in because "
            "_currentRunWidthIn() sums cabinets on EVERY wall "
            "(kitchen_configurator.js:886 + :906). Left-wall cabinets "
            "inflate the back-wall dimension." % room["width_in"])
    print("   left-wall items still carry the client sentinel x:")
    for i in items:
        if i["wall"] == "left":
            print("      %-22s x=%.1f" % (i["layout_key"], i["x_position_in"]))

    hdr("Save the design (/configurator/save → save_design)")
    res = ctl.save_design(name="E2E L-Shape", room=room, items=items,
                          partner_id=False, design_id=False)
    print("   response keys: %s" % sorted(res.keys()))
    design_id = res["id"]
    print("   design id=%s  placed(engine-repositioned)=%d" % (design_id, len(res.get("placed") or [])))
    Design = env["southbrook.kitchen.design"]
    design = Design.browse(design_id)

    def dump(title):
        print("   --- %s ---" % title)
        rows = []
        for l in design.cabinet_line_ids.sorted(lambda l: (l.wall or "", l.cabinet_type, l.x_position_in)):
            rows.append(l)
            print("   %-24s type=%-6s wall=%-5s x=%7.2f y=%5.1f z=%6.2f rot=%5.1f w=%4.1f d=%4.1f %s" % (
                (l.layout_key or "")[:24], l.cabinet_type, l.wall or "back",
                l.x_position_in, l.y_position_in, l.z_position_in,
                l.rotation_deg, l.width_in, l.depth_in,
                "[derived]" if l.layout_role == "derived" else ""))
        return rows

    lines = dump("persisted design lines")

    def layer(l):
        return "wall" if l.cabinet_type == "wall" else "base"

    def foot(l):
        cab = {"width_mm": (l.width_in or 0) * MM, "depth_mm": (l.depth_in or 0) * MM}
        place = {"x": (l.x_position_in or 0) * MM, "z": (l.z_position_in or 0) * MM,
                 "rotation_deg": l.rotation_deg or 0}
        return E.footprint_mm(cab, place)

    def collisions(rows, label):
        bad_pairs = []
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if layer(a) != layer(b):
                    continue
                if E.footprints_overlap(foot(a), foot(b)):
                    bad_pairs.append((a, b))
        print("   COLLIDING SAME-LAYER PAIRS (%s): %d" % (label, len(bad_pairs)))
        for a, b in bad_pairs[:12]:
            print("      %s(%s) x=%.1f z=%.1f  X  %s(%s) x=%.1f z=%.1f" % (
                a.cabinet_type, a.wall, a.x_position_in, a.z_position_in,
                b.cabinet_type, b.wall, b.x_position_in, b.z_position_in))
        return bad_pairs

    hdr("Validate cabinet placement after save")
    pairs = collisions(lines, "after save_design")
    if pairs:
        bad("%d physically overlapping cabinet pairs persisted." % len(pairs))
    corners = design.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "corner")
    print("   corner cabinets present: %d (expected 2: one base, one wall)" % len(corners))
    if not corners:
        bad("save_design NEVER runs corner resolution — it only calls "
            "_place_lines_on_wall (controllers/main.py:495). No corner "
            "cabinet is inserted on this page, ever.")
    print("   built-in validator says:")
    try:
        issues = design._validation_issues() if hasattr(design, "_validation_issues") else None
    except Exception as ex:
        issues = "raised %s" % ex
    print("      %s" % (issues if issues is not None else "no _validation_issues() method"))

    hdr("Regenerate the layout (design.action_auto_arrange — the L/U service)")
    try:
        out = design.action_auto_arrange()
        print("   action_auto_arrange -> %s" % out)
    except Exception as ex:
        bad("action_auto_arrange RAISED %s: %s" % (type(ex).__name__, ex))
        out = None
    design.invalidate_recordset()
    lines2 = dump("after auto-arrange")
    pairs2 = collisions(lines2, "after auto-arrange")
    corners2 = design.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "corner")
    print("   corner cabinets now: %d" % len(corners2))
    for c in corners2:
        print("      %s wall=%s x=%.1f z=%.1f rot=%.0f w=%.1f" % (
            c.product_id.default_code or c.product_id.name, c.wall,
            c.x_position_in, c.z_position_in, c.rotation_deg, c.width_in))
    active_left = design.cabinet_line_ids.filtered(lambda l: l.wall == "left")
    print("   lines still on wall=left: %d" % len(active_left))

    hdr("Reload the page (/load_design_lines) — what the user gets back")
    back = ctl.load_design_lines(design_id=design_id)
    got = back.get("lines") or back.get("items") or back
    n = len(got) if hasattr(got, "__len__") else "?"
    print("   lines returned: %s" % n)
    if isinstance(got, dict):
        sample = list(got.values())[:2]
    else:
        sample = list(got)[:2]
    for s in sample:
        print("      %s" % {k: s.get(k) for k in ("layout_key", "cabinet_type", "wall", "x_position_in", "z_position_in", "rotation_deg") if isinstance(s, dict)})
    walls_returned = set()
    seq = got.values() if isinstance(got, dict) else got
    for s in seq:
        if isinstance(s, dict):
            walls_returned.add(s.get("wall"))
    print("   walls represented in reload payload: %s" % walls_returned)

    hdr("Producing the final configuration — manufacturing mirror")
    print("   sale_order_id on design: %s" % (design.sale_order_id.id if design.sale_order_id else None))
    print("   design state: %s" % design.state)

finally:
    c3d.request = saved
    env.cr.rollback()
    print("\n[rolled back — nothing persisted]")
    _out.close()
