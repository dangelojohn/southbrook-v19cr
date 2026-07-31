/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — corner cabinet mesh builder.
 *
 * Task 8 (corner-remediation-2026-07-30). Corner design lines
 * (`cabinet_type === "corner"`) previously fell through to
 * `buildOtherCabinet`'s generic tall/panel box path — a plain
 * rectangular box on the corner's cell, visually indistinguishable
 * from a misplaced slab (John's literal complaint; see
 * task-8-brief.md).
 *
 * PLACEMENT IS UNTOUCHED. This module only builds LOCAL-FRAME
 * geometry — `_placeCabinetGroup` (kitchen_canvas.esm.js) still owns
 * every world x/y/z/rotation transform (COORDINATE_CONTRACT.md). Every
 * shape below stays strictly inside the local bounding box
 * `[0,width_in] x [0,height_in] x [0,depth_in]` — the exact engine
 * cell footprint — so neighbouring-run edges still abut exactly where
 * the layout engine put them; only material is removed from an
 * INTERIOR corner of that box, never added outside it (the one
 * intentional exception is the countertop, which — like every other
 * base-cabinet countertop in this file set, see base_cabinet.esm.js —
 * overhangs its own cabinet by a small fixed margin; that's existing,
 * accepted styling, not a footprint/collision change).
 *
 * Style selection is by the design line's product default_code prefix
 * (V1-geometry.md renderer section / task-8-brief.md):
 *   SB-WALL-CORNER*   -> pie-cut prism at wall-cabinet depth/height
 *                         (mounted like buildWallCabinet: no toe-kick,
 *                         no countertop)
 *   SB-CORNER-BLIND*  -> blind L-massing: the TRUE shape of a real
 *                         blind-corner box is a plain elongated
 *                         rectangle (full cell, unclipped) — the
 *                         distinguishing feature is a visible blind
 *                         end panel (no hardware) on the leg nearest
 *                         the room corner, with the door concentrated
 *                         on the open leg
 *   SB-CORNER* (incl. SB-CORNER-R) or any other populated code
 *                     -> diagonal clipped-corner prism (classic
 *                         Lazy-Susan corner plan), door face on the
 *                         diagonal
 *   no/unrecognized code -> degrade honestly to the pre-existing plain
 *                         box (buildOtherCabinet) — never guess a
 *                         shape from an unknown SKU
 *
 * `controllers/main.py`'s `_serialize_design_lines` (the /load_design_lines
 * and /rearrange line payload) sends `default_code` directly per line
 * (code-review fix, 2026-07-30) — this reads `item.default_code`,
 * falling back to `item.sku` (the field name the /products and
 * /layout endpoints use) for the paths that carry it that way instead
 * (fresh drag/+Add, the /layout generator). kitchen_configurator.js's
 * `_lineToItem` also keeps a `state.products` catalog-lookup fallback
 * for the rare case a line dict is missing it from both sources —
 * deliberately NOT the primary path, since a derived corner-node line
 * can legitimately reference a product outside the
 * `southbrook_is_cabinet=True` panel-catalog filter `/products` gates
 * on, so a catalog-only lookup could never resolve those SKUs.
 *
 * WINDING NOTE: every new shape here is a hand-rolled convex prism
 * (explicit position/index BufferGeometry, computeVertexNormals) and
 * is rendered DoubleSide (see mesh_factory.esm.js). This environment
 * has no WebGL/browser to visually confirm face winding — DoubleSide
 * removes "wrong winding = invisible face" as a failure mode without
 * touching any footprint/placement guarantee.
 */

import { IN, CTR } from "@southbrook_kitchen_3d_configurator/js/canvas/constants.esm";
import { buildOtherCabinet } from "@southbrook_kitchen_3d_configurator/js/canvas/other_cabinets.esm";
// Task 22 review fix (SBK-036, 2026-07-30) — mixHex used to be
// duplicated here and in selection.esm.js; now a shared helper.
import { mixHex } from "@southbrook_kitchen_3d_configurator/js/canvas/color_utils.esm";

const TOEKICK_H = 3.5 * IN;

// Muted teal corner-unit accent — subtle on purpose (brief: "subtle
// material tint"), blended into the normal cabinet palette below.
const CORNER_TINT = 0x2E6F63;

/**
 * Style key from a product default_code, or null when it doesn't match
 * any known corner family (caller degrades to the plain box).
 * @param {string|null|undefined} code
 * @returns {"pie"|"blind"|"diag"|null}
 */
export function cornerStyleFromCode(code) {
    const c = (code || "").toUpperCase();
    if (!c) return null;
    if (c.startsWith("SB-WALL-CORNER")) return "pie";
    if (c.startsWith("SB-CORNER-BLIND")) return "blind";
    if (c.startsWith("SB-CORNER")) return "diag";
    return null;
}

// ─── Convex polygon prism (hand-rolled BufferGeometry) ───────────────────────

/**
 * Extrude a convex, perimeter-ordered [x,z] point list from y0 to
 * y0+height. Positions only stay inside the input polygon's own [x,z]
 * bounds — callers are responsible for keeping that polygon inside the
 * cell.
 */
function polygonPrismGeometry(THREE, pointsXZ, y0, height) {
    const n = pointsXZ.length;
    const pos = [];
    for (const [x, z] of pointsXZ) pos.push(x, y0, z);
    for (const [x, z] of pointsXZ) pos.push(x, y0 + height, z);
    const idx = [];
    for (let i = 0; i < n; i++) {
        const a = i, b = (i + 1) % n, aTop = a + n, bTop = b + n;
        idx.push(a, b, bTop,  a, bTop, aTop);
    }
    for (let i = 1; i < n - 1; i++) idx.push(0, i, i + 1);           // bottom fan
    for (let i = 1; i < n - 1; i++) idx.push(n, n + i + 1, n + i);   // top fan
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    return geo;
}

// Countertop overhang — DIRECTIONAL, not a uniform scale. Code-review
// fix (2026-07-30): a uniform centroid scale pushed every edge outward
// a few tenths of an inch, including the two straight edges (z=0
// against the host wall; x=0/x=w against the perpendicular wall or a
// neighbouring run) that must stay flush for footprint/neighbour
// alignment. base_cabinet.esm.js's own countertop only overhangs the
// open FRONT face (+0.07 scene-ft) and stays ~flush on the back/sides
// (+0.005 total width, i.e. an even split of ~0.0025 per side) — this
// mirrors that: only the door-chord points (the diagonal/arc "open"
// face, identified by exact value-match against `shape.doorChord`,
// the same array these points come from) move outward along the
// shape's own outward door normal; every other perimeter point (the
// three wall/neighbour-abutting corners) is left byte-identical.
function overhangCountertopPoints(shape, frontOverhang) {
    const chordKeys = new Set(shape.doorChord.map(([x, z]) => `${x},${z}`));
    const [nx, nz] = shape.doorNormal;
    return shape.points.map(([x, z]) => (
        chordKeys.has(`${x},${z}`)
            ? [x + nx * frontOverhang, z + nz * frontOverhang]
            : [x, z]
    ));
}

// Diagonal (Lazy-Susan) clip: cut the corner farthest from the host
// wall (local z=0) with a single 45°-ish chord — the classic diagonal
// corner-cabinet plan. `clipFrac` is against min(w,d) so the chord
// always stays inside the cell for any aspect ratio.
function diagCornerShape(w, d, clipFrac = 0.42) {
    const clip = Math.min(w, d) * clipFrac;
    const p1 = [w, d - clip], p2 = [w - clip, d];
    return {
        points: [[0, 0], [w, 0], p1, p2, [0, d]],
        doorChord: [p1, p2],
        doorMid: [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2],
        doorNormal: [Math.SQRT1_2, Math.SQRT1_2],
    };
}

// Pie-cut: same corner, rounded with a faceted quarter-circle arc
// instead of a single chord — the wall-corner treatment.
function pieCornerShape(w, d, clipFrac = 0.42, segments = 6) {
    const clip = Math.min(w, d) * clipFrac;
    const cx = w, cz = d;
    const arc = [];
    for (let i = 0; i <= segments; i++) {
        const theta = (1.5 - 0.5 * (i / segments)) * Math.PI;
        arc.push([cx + clip * Math.cos(theta), cz + clip * Math.sin(theta)]);
    }
    const mid = arc[Math.round(segments / 2)];
    return {
        points: [[0, 0], [w, 0], ...arc, [0, d]],
        doorChord: arc,
        doorMid: mid,
        doorNormal: [Math.SQRT1_2, Math.SQRT1_2],
    };
}

// A thin, flat strip following an ordered chord/arc of [x,z] points,
// pushed `offset` outward along `normal` and extruded vertically from
// y0 to y0+height — the visible "door face" proud of the clipped
// carcass surface (same flush-inset trick base_cabinet.esm.js's
// shaker door uses, generalised to a non-planar-in-general chord).
function faceStripGeometry(THREE, chordPts, normal, offset, y0, height) {
    const n = chordPts.length;
    const off = chordPts.map(([x, z]) => [x + normal[0] * offset, z + normal[1] * offset]);
    const pos = [];
    for (const [x, z] of off) pos.push(x, y0, z);
    for (const [x, z] of off) pos.push(x, y0 + height, z);
    const idx = [];
    for (let i = 0; i < n - 1; i++) {
        const a = i, b = i + 1, aTop = a + n, bTop = b + n;
        idx.push(a, b, bTop,  a, bTop, aTop);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geo.setIndex(idx);
    geo.computeVertexNormals();
    return geo;
}

function buildClippedCorner(THREE, mk, palette, item, style) {
    const w = item.width_in * IN, h = item.height_in * IN, d = item.depth_in * IN;
    const isWall = style === "pie";
    const shape = isWall ? pieCornerShape(w, d) : diagCornerShape(w, d);
    const objects = [], clickable = [];
    const cabTint  = mixHex(palette.cab,     CORNER_TINT, 0.16);
    const darkTint = mixHex(palette.cabDark, CORNER_TINT, 0.16);

    if (!isWall) {
        objects.push(mk(
            polygonPrismGeometry(THREE, shape.points, 0, TOEKICK_H),
            palette.toekick, [0, 0, 0], null,
            { cs: true, rough: 0.95, metal: 0.0, side: "double" },
        ));
    }
    const y0 = isWall ? 0 : TOEKICK_H;
    const bh = isWall ? h : h - TOEKICK_H;
    const body = mk(
        polygonPrismGeometry(THREE, shape.points, y0, bh),
        cabTint, [0, 0, 0], null,
        { cs: true, rs: true, rough: 0.55, metal: 0.0, side: "double",
          ud: { cab: true, cabType: "corner", item } },
    );
    objects.push(body);
    clickable.push(body);

    // Door face — a thin panel following the same diagonal/arc chord as
    // the carcass cut, pushed slightly proud so it reads as an inset
    // door rather than being coplanar with (and z-fighting) the body.
    //
    // Task 22 review fix (SBK-036, 2026-07-30) — this is the single
    // most prominent visible surface on a clipped-corner cabinet (the
    // whole diagonal/arc "lazy susan" face this style exists for), but
    // it was missing the `ud: { cab: true, ... }` flag every other
    // cabinet builder's door/body meshes carry — selection.esm.js's
    // `applySelectionVisuals` only tints/outlines cab-flagged meshes,
    // so selecting a diag/pie corner cabinet changed the (mostly
    // hidden, clipped-away) box body while its actual door face stayed
    // untinted. Flagged here (and on the handle just below, since it
    // sits proud of this same face and reads as part of the same
    // surface) so both participate like base_cabinet.esm.js /
    // wall_cabinet.esm.js's body+door do. Toe-kick and countertop stay
    // unflagged, matching every other builder in this file — they're
    // structural/material surfaces, not door/carcass identity.
    const doorH = bh * 0.60;
    objects.push(mk(
        faceStripGeometry(THREE, shape.doorChord, shape.doorNormal, 0.006,
                           y0 + bh * 0.18, doorH),
        darkTint, [0, 0, 0], null,
        { rough: 0.6, metal: 0.05, side: "double",
          ud: { cab: true, cabType: "corner", item } },
    ));
    // Handle on the door face — brushed metal, aligned roughly along
    // the cut chord.
    const [mx, mz] = shape.doorMid;
    const [nx, nz] = shape.doorNormal;
    objects.push(mk(
        new THREE.BoxGeometry(Math.min(w, d) * 0.30, 0.025, 0.040), palette.handle,
        [mx + nx * 0.03, y0 + bh * 0.55, mz + nz * 0.03], [0, -Math.PI / 4, 0],
        { rough: 0.35, metal: 0.85, ud: { cab: true, cabType: "corner", item } },
    ));

    if (!isWall) {
        // 0.065 scene-ft ≈ 0.78in front overhang — same magnitude as
        // base_cabinet.esm.js's countertop front overhang; zero on the
        // wall/neighbour-abutting edges (see overhangCountertopPoints).
        objects.push(mk(
            polygonPrismGeometry(THREE, overhangCountertopPoints(shape, 0.065), h, CTR),
            palette.counter, [0, 0, 0], null,
            { cs: true, rough: 0.4, metal: 0.05, side: "double" },
        ));
    }

    if (item.pinned) {
        const pin = Math.min(w, d) * 0.15;
        objects.push(mk(
            new THREE.SphereGeometry(0.06, 14, 14), 0x18B4A6,
            [pin, h + (isWall ? 0 : CTR) + 0.16, pin], null,
            { rough: 0.3, metal: 0.5 },
        ));
    }
    return { objects, clickable };
}

// ─── Blind corner (full box, no clip) ────────────────────────────────────────

function buildBlindCorner(THREE, mk, palette, item, blindNearX0) {
    const w = item.width_in * IN, h = item.height_in * IN, d = item.depth_in * IN;
    const objects = [], clickable = [];
    const cabTint  = mixHex(palette.cab,     CORNER_TINT, 0.16);
    const darkTint = mixHex(palette.cabDark, CORNER_TINT, 0.22);

    objects.push(mk(
        new THREE.BoxGeometry(w - 0.01, TOEKICK_H, d - 0.01), palette.toekick,
        [w / 2, TOEKICK_H / 2, d / 2], null,
        { cs: true, rough: 0.95, metal: 0.0 },
    ));
    const body = mk(
        new THREE.BoxGeometry(w - 0.02, h - TOEKICK_H, d - 0.02), cabTint,
        [w / 2, TOEKICK_H + (h - TOEKICK_H) / 2, d / 2], null,
        { cs: true, rs: true, rough: 0.55, metal: 0.0,
          ud: { cab: true, cabType: "corner", item } },
    );
    objects.push(body);
    clickable.push(body);

    // Blind end (nearest the room corner, per the geometric hint
    // computed in kitchen_canvas.esm.js) vs. the open/working end.
    const panelW = w * 0.38;
    const panelX = blindNearX0 ? 0 : (w - panelW);
    const doorX  = blindNearX0 ? panelW : 0;
    const doorW  = w - panelW;

    // Blind panel — flush, no reveal, no hardware: the inaccessible
    // end of a real blind-corner box.
    objects.push(mk(
        new THREE.BoxGeometry(panelW - 0.06, (h - TOEKICK_H) * 0.86, 0.016), darkTint,
        [panelX + panelW / 2, TOEKICK_H + (h - TOEKICK_H) / 2, d - 0.001], null,
        { rough: 0.7, metal: 0.0, ud: { cab: true, cabType: "corner", item } },
    ));

    // Door face — shaker inset, same proportions as buildBaseCabinet,
    // confined to the open leg.
    const dH = (h - TOEKICK_H) * 0.60;
    objects.push(mk(
        new THREE.BoxGeometry(doorW - 0.10, dH, 0.016), palette.cabDark,
        [doorX + doorW / 2, TOEKICK_H + (h - TOEKICK_H) * 0.72 - dH / 2, d - 0.001], null,
        { rough: 0.6, metal: 0.05, ud: { cab: true, cabType: "corner", item } },
    ));
    objects.push(mk(
        new THREE.BoxGeometry(doorW * 0.5, 0.025, 0.040), palette.handle,
        [doorX + doorW * 0.72, TOEKICK_H + (h - TOEKICK_H) * 0.42, d + 0.018], null,
        { rough: 0.35, metal: 0.85 },
    ));

    objects.push(mk(
        new THREE.BoxGeometry(w + 0.005, CTR, d + 0.07), palette.counter,
        [w / 2, h + CTR / 2, d / 2 + 0.03], null,
        { cs: true, rough: 0.4, metal: 0.05 },
    ));
    objects.push(mk(
        new THREE.BoxGeometry(w + 0.005, CTR * 0.6, 0.022), palette.cabDark,
        [w / 2, h + CTR * 0.3, d + 0.07], null,
        { rough: 0.5, metal: 0.05 },
    ));

    if (item.pinned) {
        objects.push(mk(
            new THREE.SphereGeometry(0.06, 14, 14), 0x18B4A6,
            [w - 0.10, h + CTR + 0.16, d - 0.10], null,
            { rough: 0.3, metal: 0.5 },
        ));
    }
    return { objects, clickable };
}

/**
 * @param {typeof THREE} THREE
 * @param {(geo:any, color:number, pos:any, rotE:any, opts?:object) => THREE.Mesh} mk
 * @param {object} palette
 * @param {object} item — southbrook.kitchen.design.line dict; reads
 *   default_code (falls back to sku) to pick a style, and
 *   __blindNearX0 (see cornerBlindNearX0 below, merged in by
 *   _placeCabinetGroup's `extra` param — never a substitute item) for
 *   blind massing.
 * @returns {{ objects: THREE.Mesh[], clickable: THREE.Mesh[] }}
 */
export function buildCornerCabinet(THREE, mk, palette, item) {
    const style = cornerStyleFromCode(item.default_code || item.sku);
    if (style === "pie")   return buildClippedCorner(THREE, mk, palette, item, "pie");
    if (style === "diag")  return buildClippedCorner(THREE, mk, palette, item, "diag");
    if (style === "blind") return buildBlindCorner(THREE, mk, palette, item, item.__blindNearX0 !== false);
    // Unknown/missing default_code — degrade honestly to the
    // pre-existing plain box rather than guess a shape.
    return buildOtherCabinet(THREE, mk, palette, item);
}

/**
 * Geometric hint for buildBlindCorner: which local width-axis end
 * (x=0 vs x=width_in) sits nearest an actual room corner. Blind-corner
 * boxes run full-length along their "host leg" (against one wall) and
 * are blind (inaccessible, panel-only) at the end nearest the room
 * corner — but that end is x=0 for some placements and x=width_in for
 * others depending on which of the room's four corners the cabinet
 * sits in (COORDINATE_CONTRACT's anchor convention is rotation-
 * relative, not room-relative), and the wall-adjacent `zone`/`handed`
 * data the layout engine resolved this from never reaches the client
 * payload. This resolves it purely from already-payload-available
 * fields (position + rotation) plus the room's own dimensions: compare
 * the two local back-edge corners (z=0, the edge that's always flush
 * against the host wall) and see which one lands closer to any of the
 * room's four corners in world space. Pure/no side effects.
 *
 * Code-review fix (2026-07-30): this used to return a full item copy
 * (`{...item, __blindNearX0}`) that the caller substituted for the
 * canonical `it` passed into `_placeCabinetGroup` — since that copy is
 * what got bound as `mesh.userData.item`, every corner cabinet's
 * drag/pin/select operated on a disconnected clone instead of the real
 * `state.items` entry (the exact F8 bug class kitchen_canvas.esm.js's
 * `_placeCabinetGroup` docstring describes). Returns a bare
 * boolean/undefined instead — the caller merges it into the builder's
 * local-frame copy via `_placeCabinetGroup`'s `extra` param, never onto
 * the real item.
 *
 * @param {object} item
 * @param {{width_in?: number, depth_in?: number}} room
 * @returns {boolean|undefined} true/false once resolved; undefined
 *   when room dimensions aren't available (buildBlindCorner's own
 *   `!== false` check then defaults to true).
 */
export function cornerBlindNearX0(item, room) {
    const rw = (room && room.width_in) || 0;
    const rd = (room && room.depth_in) || 0;
    if (!item || !rw || !rd || !item.width_in) return undefined;
    const th = ((item.rotation_deg || 0) * Math.PI) / 180;
    const c = Math.cos(th), s = Math.sin(th);
    const x0 = item.x_position_in || 0, z0 = item.z_position_in || 0;
    const worldOf = (lx, lz) => [x0 + lx * c + lz * s, z0 + (-lx * s + lz * c)];
    const distToNearestRoomCorner = ([wx, wz]) => Math.min(
        Math.hypot(wx, wz),
        Math.hypot(wx - rw, wz),
        Math.hypot(wx, wz - rd),
        Math.hypot(wx - rw, wz - rd),
    );
    const dNearX0 = distToNearestRoomCorner(worldOf(0, 0));
    const dNearXW = distToNearestRoomCorner(worldOf(item.width_in, 0));
    return dNearX0 <= dNearXW;
}
