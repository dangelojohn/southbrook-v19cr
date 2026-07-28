"""Repro harness for the two reported layout rules.

Rule A: a base/wall cabinet must be affixed to exactly ONE wall and must not
        occupy the adjacent wall's space (no straddling the corner).
Rule B: at a corner junction BOTH an upper corner and a base corner cabinet
        must be auto-inserted.
"""
import sys
sys.path.insert(0, "/Users/naadmin/southbrook-v19cr/addons/southbrook_estimating/models")
import kitchen_layout_engine as E

ROOM = {"width_mm": 3600.0, "depth_mm": 3600.0, "height_mm": 2440.0}


def cab(i, wall, seq, layer, w=600.0, d=600.0):
    return {
        "id": i, "width_mm": w, "depth_mm": d,
        "height_mm": 876.0 if layer == "base" else 762.0,
        "family": "wall" if layer == "wall" else "base",
        "cabinet_type": "wall" if layer == "wall" else "base",
        "zone": "wall" if layer == "wall" else "base_run",
        "wall": wall, "run_seq": seq,
    }


def overlaps(cabs, places):
    p = {q["id"]: q for q in places}
    bad = []
    for i in range(len(cabs)):
        for j in range(i + 1, len(cabs)):
            a, b = cabs[i], cabs[j]
            if E._layer_of(a) != E._layer_of(b):
                continue
            fa = E.footprint_mm(a, p[a["id"]])
            fb = E.footprint_mm(b, p[b["id"]])
            if E.footprints_overlap(fa, fb):
                bad.append((a["id"], a["wall"], fa, b["id"], b["wall"], fb))
    return bad


def banner(t):
    print("\n" + "=" * 72 + "\n" + t + "\n" + "=" * 72)


# ── Scenario 1: interactive add — FIRST cabinet on a brand-new wall ───────
# Controller gate `wall_had_cabinets` is False → action_auto_arrange is NOT
# called → only kitchen_layout_engine.layout() runs (via _place_lines_on_wall).
banner("S1  interactive add, FIRST cabinet on left wall (no corner resolve)")
cabs = [cab(1, "back", 0, "base"), cab(2, "back", 1, "base"),
        cab(3, "back", 0, "wall", d=300.0), cab(4, "back", 1, "wall", d=300.0),
        cab(5, "left", 0, "base"), cab(6, "left", 0, "wall", d=300.0)]
places = E.layout(cabs, ROOM)
for c, p in zip(cabs, places):
    print("  id=%s wall=%-5s layer=%-4s  pose=(x=%.0f z=%.0f rot=%d)  aabb=%s"
          % (c["id"], c["wall"], E._layer_of(c), p["x"], p["z"],
             p["rotation_deg"],
             tuple(round(v) for v in E.footprint_mm(c, p))))
bad = overlaps(cabs, places)
print("  OVERLAPPING PAIRS (same layer): %d" % len(bad))
for b in bad:
    print("    id=%s(%s) %s  X  id=%s(%s) %s" % b)

# ── Scenario 2: same design, corner resolution DOES run ───────────────────
banner("S2  same design through resolve_and_layout(auto_assign=False)")
r = E.resolve_and_layout(cabs, ROOM, auto_assign=False)
print("  corners detected: %s"
      % [(c["corner"], c["layer"]) for c in r["corners"]])
print("  inserted        : %s"
      % [(n["corner"], n["layer"], n["wall"]) for n in r["inserted"]])
print("  removed_ids     : %s" % r["removed_ids"])
bad = overlaps(r["cabinets"], r["placements"])
print("  OVERLAPPING PAIRS: %d" % len(bad))
for b in bad:
    print("    id=%s(%s) %s  X  id=%s(%s) %s" % b)

# ── Scenario 3: bases on both walls, uppers on ONE wall only ─────────────
banner("S3  bases meet at corner, uppers only on back wall")
cabs3 = [cab(1, "back", 0, "base"), cab(2, "back", 1, "base"),
         cab(3, "back", 0, "wall", d=300.0),
         cab(5, "left", 0, "base"), cab(7, "left", 1, "base")]
r3 = E.resolve_and_layout(cabs3, ROOM, auto_assign=False)
print("  corners detected: %s"
      % [(c["corner"], c["layer"]) for c in r3["corners"]])
print("  inserted        : %s"
      % [(n["corner"], n["layer"]) for n in r3["inserted"]])

# ── Scenario 4: after a corner exists, add ANOTHER cabinet to host wall ──
# Mirrors _place_lines_on_wall: engine.layout() with NO wall_start_offsets,
# and 'corner' lines are NOT excluded from the cab list.
banner("S4  add-another-cabinet after corner exists (no wall_start_offsets)")
survivors = [c for c in r["cabinets"]]
new = cab(99, "back", 9, "base")
cabs4 = survivors + [new]
places4 = E.layout(cabs4, ROOM)          # exactly what _place_lines_on_wall does
p4 = {q["id"]: q for q in places4}
print("  corner nodes present in run: %s"
      % [c["id"] for c in cabs4 if c.get("corner_cabinet")])
for c in cabs4:
    q = p4[c["id"]]
    print("  id=%-22s wall=%-5s seq=%-3s pose=(x=%.0f z=%.0f rot=%d) aabb=%s"
          % (c["id"], c.get("wall"), c.get("run_seq"), q["x"], q["z"],
             q["rotation_deg"], tuple(round(v) for v in E.footprint_mm(c, q))))
bad4 = overlaps(cabs4, places4)
print("  OVERLAPPING PAIRS: %d" % len(bad4))
for b in bad4:
    print("    id=%s(%s) %s  X  id=%s(%s) %s" % b)
