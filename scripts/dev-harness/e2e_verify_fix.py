# -*- coding: utf-8 -*-
"""POST-FIX verification: the same L-shaped workflow, asserting the fixed
contract end-to-end. Rolls back everything."""
import builtins, functools
_out = open('/tmp/verify_out.txt', 'w')
print = functools.partial(builtins.print, file=_out, flush=True)

from unittest.mock import MagicMock
from odoo.addons.southbrook_kitchen_3d_configurator.controllers import main as c3d
from odoo.addons.southbrook_estimating.models import kitchen_layout_engine as E

MM = 25.4
FAILS = []


def check(label, ok, detail=""):
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                            (" — " + str(detail)) if detail else ""))
    if not ok:
        FAILS.append(label)


saved = c3d.request
mock = MagicMock(); mock.env = env; mock.session = {}; mock.params = {}
c3d.request = mock
ctl = c3d.SouthbrookKitchenConfiguratorController()

# Persisted poses are ANCHOR convention post-fix.
foot_anchor = getattr(E, "footprint_from_anchor_mm", None)


def line_foot(l):
    cab = {"width_mm": (l.width_in or 0) * MM, "depth_mm": (l.depth_in or 0) * MM}
    place = {"x": (l.x_position_in or 0) * MM, "z": (l.z_position_in or 0) * MM,
             "rotation_deg": l.rotation_deg or 0}
    return foot_anchor(cab, place)


def layer(l):
    return "wall" if l.cabinet_type == "wall" else (
        "wall" if (l.cabinet_type == "corner" and l.zone == "wall") else "base")


def overlaps(design):
    rows = list(design.cabinet_line_ids.filtered(
        lambda l: l.cabinet_type not in ("filler", "panel")))
    out = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if layer(a) != layer(b):
                continue
            if E.footprints_overlap(line_foot(a), line_foot(b)):
                out.append((a, b))
    return out


def items_for(walls, n_base=4, n_wall=4, bw=12.0, ww=30.0):
    prods = ctl.products()
    cat = prods.get("products", prods) if isinstance(prods, dict) else prods
    bt = {}
    for p in cat:
        bt.setdefault(p.get("cabinet_type"), []).append(p)
    bp, wp = bt["base"][0], bt["wall"][0]
    items = []
    for wall in walls:
        for n in range(n_base):
            items.append(dict(product_id=bp["product_id"], cabinet_type="base",
                layout_key="b-%s-%d" % (wall, n), x_position_in=n * bw,
                y_position_in=0.0, z_position_in=0.0, width_in=bw,
                height_in=34.5, depth_in=24.0, price=0.0, pinned=False,
                rotation_deg=0.0, wall=wall))
        for n in range(n_wall):
            items.append(dict(product_id=wp["product_id"], cabinet_type="wall",
                layout_key="w-%s-%d" % (wall, n), x_position_in=n * ww,
                y_position_in=54.0, z_position_in=0.0, width_in=ww,
                height_in=30.0, depth_in=12.0, price=0.0, pinned=False,
                rotation_deg=0.0, wall=wall))
    return items


try:
    print("engine helpers present: anchor_pose_mm=%s footprint_from_anchor_mm=%s"
          % (hasattr(E, "anchor_pose_mm"), foot_anchor is not None))
    check("engine anchor helpers exist",
          hasattr(E, "anchor_pose_mm") and foot_anchor is not None)

    # ── T1: L-shape that FITS (168x168) saved via the backend page route ──
    print("\nT1 — L-shape (4+4 per wall) in 168x168 room via save_design")
    res = ctl.save_design(name="Verify L fits",
                          room={"width_in": 168.0, "depth_in": 168.0, "height_in": 96.0},
                          items=items_for(("back", "left")),
                          partner_id=False, design_id=False)
    d = env["southbrook.kitchen.design"].browse(res["id"])
    d.invalidate_recordset()
    print("   response: relaid=%s layout_warning=%r lines=%s"
          % (res.get("relaid"), res.get("layout_warning"),
             len(res.get("lines") or []) if res.get("lines") is not None else None))
    check("save_design returns relaid=True", res.get("relaid") is True)
    check("save_design returns lines payload", bool(res.get("lines")))
    corners = d.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "corner")
    check("exactly 2 corner cabinets inserted", len(corners) == 2,
          "got %d" % len(corners))
    zones = sorted(corners.mapped("zone"))
    check("corner zones are base_run + wall", zones == ["base_run", "wall"],
          zones)
    skus = sorted(corners.mapped("product_id.default_code"))
    check("corner SKUs SB-CORNER + SB-WALL-CORNER",
          skus == ["SB-CORNER", "SB-WALL-CORNER"], skus)
    uppers = d.cabinet_line_ids.filtered(
        lambda l: l.cabinet_type == "wall" and (l.wall or "back") != "back")
    check("non-back uppers elevated (y>40in)",
          uppers and all(l.y_position_in > 40 for l in uppers),
          [round(l.y_position_in, 1) for l in uppers])
    check("all upper zones = wall",
          all(l.zone == "wall" for l in d.cabinet_line_ids
              if l.cabinet_type == "wall"),
          set(d.cabinet_line_ids.filtered(
              lambda l: l.cabinet_type == "wall").mapped("zone")))
    ov = overlaps(d)
    check("zero same-layer overlaps (anchor footprints)", not ov,
          ["%s/%s x %s/%s" % (a.cabinet_type, a.wall, b.cabinet_type, b.wall)
           for a, b in ov[:5]])
    room_mm = {"width_mm": 168 * MM, "depth_mm": 168 * MM}
    inroom = all(
        f[0] >= -2 and f[1] <= room_mm["width_mm"] + 2
        and f[2] >= -2 and f[3] <= room_mm["depth_mm"] + 2
        for f in (line_foot(l) for l in d.cabinet_line_ids))
    check("every cabinet within room", inroom)
    arch = d.with_context(active_test=False).cabinet_line_ids.filtered(
        lambda l: not l.active)
    check("consumed originals archived not deleted", len(arch) == 4,
          arch.mapped("layout_key"))
    print("   final lines:")
    for l in d.cabinet_line_ids.sorted(lambda l: (l.wall or "", l.cabinet_type, l.z_position_in, l.x_position_in)):
        print("      %-16s %-6s wall=%-5s zone=%-8s x=%7.2f y=%5.1f z=%7.2f rot=%3.0f" % (
            (l.layout_key or "")[:16], l.cabinet_type, l.wall or "back", l.zone,
            l.x_position_in, l.y_position_in, l.z_position_in, l.rotation_deg))
    env.cr.rollback()

    # ── T2: capacity failure is a warning, not a 500, and save survives ──
    print("\nT2 — genuinely over-full room (80x80)")
    res2 = ctl.save_design(name="Verify capacity warn",
                           room={"width_in": 80.0, "depth_in": 80.0, "height_in": 96.0},
                           items=items_for(("back", "left")),
                           partner_id=False, design_id=False)
    d2 = env["southbrook.kitchen.design"].browse(res2["id"])
    check("save still succeeds (design id returned)", bool(res2.get("id")))
    check("layout_warning present and human-readable",
          bool(res2.get("layout_warning")), repr(res2.get("layout_warning")))
    check("no exception leaked to caller", True)
    env.cr.rollback()

    # ── T3: idempotence — saving the relaid result again is stable ──
    print("\nT3 — re-save idempotence")
    res3 = ctl.save_design(name="Verify idempotent",
                           room={"width_in": 168.0, "depth_in": 168.0, "height_in": 96.0},
                           items=items_for(("back", "left")),
                           partner_id=False, design_id=False)
    d3 = env["southbrook.kitchen.design"].browse(res3["id"])
    lines1 = res3.get("lines") or []
    # feed the relaid lines straight back (what the client autosave would do)
    resend = [dict(x, price=x.get("price", 0.0)) for x in lines1]
    res4 = ctl.save_design(name="Verify idempotent",
                           room={"width_in": 168.0, "depth_in": 168.0, "height_in": 96.0},
                           items=resend, partner_id=False, design_id=res3["id"])
    d3.invalidate_recordset()
    corners3 = d3.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "corner")
    check("still exactly 2 corners after re-save", len(corners3) == 2,
          len(corners3))
    check("no overlaps after re-save", not overlaps(d3))
    env.cr.rollback()

    # ── T4: single-wall design untouched (regression guard) ──
    print("\nT4 — single back wall stays legacy (no corners, no relaid)")
    res5 = ctl.save_design(name="Verify single wall",
                           room={"width_in": 168.0, "depth_in": 120.0, "height_in": 96.0},
                           items=items_for(("back",)),
                           partner_id=False, design_id=False)
    d5 = env["southbrook.kitchen.design"].browse(res5["id"])
    check("single-wall: relaid falsy", not res5.get("relaid"), res5.get("relaid"))
    check("single-wall: no corner lines",
          not d5.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "corner"))
    back_bases = d5.cabinet_line_ids.filtered(lambda l: l.cabinet_type == "base")
    check("single-wall: client x preserved (0,12,24,36)",
          sorted(round(l.x_position_in, 2) for l in back_bases) == [0.0, 12.0, 24.0, 36.0],
          sorted(round(l.x_position_in, 2) for l in back_bases))
    env.cr.rollback()

    print("\n" + "=" * 60)
    print("RESULT: %s (%d failures)" % ("ALL PASS" if not FAILS else "FAILURES",
                                        len(FAILS)))
    for f in FAILS:
        print("   FAIL: " + f)
finally:
    c3d.request = saved
    env.cr.rollback()
    print("\n[rolled back]")
    _out.close()
