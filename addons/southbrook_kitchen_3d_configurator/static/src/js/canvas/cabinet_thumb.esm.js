/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — cabinet catalog thumbnail generator.
 *
 * Panel P2 (catalog redesign), 3-agent split — this module owns the 46px
 * axonometric SVG icon rendered per catalog row (`catalog_list.esm.js`
 * groups/sorts the rows; `kitchen_configurator.js` — owned by a sibling
 * agent — is where the two get wired into the catalog panel's markup).
 * Interface is FIXED for this split: `thumbFamily(product)` and
 * `cabinetThumbSVG(product, sizePx)`, both pure functions.
 *
 * Pure-function module — no OWL, no DOM APIs, no THREE. These render at
 * catalog-list time inside an OWL `t-out`/data-URI context, so
 * `cabinetThumbSVG` just returns a raw `<svg>` string; the caller owns
 * how it's embedded (`t-out`, `data:image/svg+xml,...`, etc).
 *
 * ─── Family inference (`thumbFamily`) ────────────────────────────────────
 * This is the ONE place cabinet family is inferred from a product record,
 * on purpose: the icon must never be able to disagree with what the mesh
 * builders (`base_cabinet.esm.js` / `wall_cabinet.esm.js` /
 * `corner_cabinet.esm.js` / `other_cabinets.esm.js`) actually place, and
 * the only way to guarantee that is a single seam.
 *
 * Debt fix (2026-08-02) — `product.template` now carries an explicit
 * `southbrook_thumb_family` Selection field (the honest fix this
 * comment used to call for: "explicit footprint-type fields on the
 * product record that drive both the 3D mesh and this icon from the
 * same source"). PRECEDENCE: `product.thumb_family` — a value from the
 * SAME fixed 11-value vocabulary as this function's return type — wins
 * outright, over everything below. Everything below it is unchanged and
 * is now purely the FALLBACK for when that field is unset (the default
 * for every product until someone opts in), so existing catalog
 * behaviour never changes on its own.
 *
 * Fallback order once there's no explicit field value: it prefers the
 * same explicit fields the mesh builders already trust — `cabinet_type`
 * (the `base/wall/tall/corner/panel/filler` bucket every design line
 * carries, see `kitchen_canvas.esm.js`) and, for the corner subfamily,
 * the SKU/`default_code` prefix (`cornerStyleFromCode` in
 * `corner_cabinet.esm.js` is the prior art for BLIND / WALL-CORNER /
 * plain-SB-CORNER prefixes; this mirrors that reasoning, extended with
 * DIAG/LSUSAN as synonyms for the diagonal/Lazy-Susan style).
 * Name-keyword matching is a FALLBACK OF THE FALLBACK, used only when
 * neither the explicit field nor cabinet_type/SKU resolve it — keying
 * off free-text product names is fragile, which is exactly why the
 * explicit field above exists now.
 */

// ─── Fixed geometry constants (model units, not inches) ────────────────────

const S = 20; // corner leg length — fixed regardless of product width_in

// ─── Palette (exact, per spec) ──────────────────────────────────────────────

const FILL_TOP = "#f2f4f7";
const FILL_ROOM = "#dadee4";
const FILL_SIDE = "#b4bbc4";
const FILL_ANGLED = "#c8ced6";
// Blind-corner return leg. The single most important line in this file:
// a blind corner and a plain corner are geometrically identical L-shapes
// (`cornerLFootprint`) — darkening the inaccessible return leg's face is
// the ONLY thing that makes them tell apart at 46px.
const FILL_RETURN = "#9ea6b0";
const STROKE = "#7d858e";
const STROKE_W = "0.75";
const DETAIL_STROKE_W = "0.6";
const SHADOW_FILL = "rgba(20,26,34,.10)";
// Door/drawer-front accent (P2 amendment — client-supplied Blum reference:
// icons read best small when the carcass stays neutral and only the
// door/front elements carry colour). Full opacity, same STROKE as every
// other painted face — see buildDoorAccents.
const DOOR_FILL = "#C9762B";

const FAMILY_LABEL = {
    base1: "Base cabinet icon",
    base2: "2-door base cabinet icon",
    drawer: "Drawer base cabinet icon",
    sink: "Sink base cabinet icon",
    vanity: "Vanity base cabinet icon",
    wall: "Wall cabinet icon",
    tall: "Tall cabinet icon",
    panel: "Panel cabinet icon",
    cornerL: "Corner base cabinet icon",
    cornerBlind: "Blind corner base cabinet icon",
    cornerDiag: "Diagonal corner base cabinet icon",
};

// ─── Family inference ────────────────────────────────────────────────────

/**
 * Infer the thumbnail family for a catalog product record. See the
 * module doc comment — `product.thumb_family` (backed by
 * `southbrook_thumb_family` on product.template) wins outright when
 * it's a recognised family value; everything else here is the
 * unchanged fallback for when it's unset.
 *
 * @param {object} product — a catalog row; `thumb_family`,
 *   `cabinet_type`, `default_code`/`sku`, and `name` are read.
 * @returns {"base1"|"base2"|"drawer"|"sink"|"vanity"|"wall"|"tall"|
 *   "panel"|"cornerL"|"cornerBlind"|"cornerDiag"}
 */
export function thumbFamily(product) {
    const p = product || {};
    // Explicit field wins outright — see the module doc comment. Only a
    // value from the fixed FAMILY_LABEL vocabulary is honoured; unset
    // (false/null/undefined/"") or any unrecognised string is treated as
    // "no override" and falls straight through to the inference below,
    // unchanged.
    if (Object.prototype.hasOwnProperty.call(FAMILY_LABEL, p.thumb_family)) {
        return p.thumb_family;
    }

    const type = String(p.cabinet_type || "base").toLowerCase();
    // Same fallback order `corner_cabinet.esm.js`'s `cornerStyleFromCode`
    // already uses: default_code first, sku (the /products payload's
    // field name for the same value) second.
    const code = String(p.default_code || p.sku || "").toUpperCase();
    const name = String(p.name || "").toLowerCase();

    if (type === "corner") {
        // Explicit SKU/default_code beats name keywords.
        if (code.includes("BLIND")) return "cornerBlind";
        if (code.includes("DIAG") || code.includes("LSUSAN")) return "cornerDiag";
        if (code.includes("WALL-CORNER")) return "cornerL";
        // Name-keyword fallback.
        if (name.includes("blind")) return "cornerBlind";
        if (name.includes("diagonal")) return "cornerDiag";
        return "cornerL";
    }

    if (type === "wall") return "wall";
    if (type === "tall") return "tall";
    // Fillers get no dedicated icon family — a plain, detail-free panel
    // silhouette is the honest rendering for a strip with no doors/drawers.
    if (type === "panel" || type === "filler") return "panel";

    // Everything else (cabinet_type "base", missing, or unrecognised)
    // falls into the base bucket; subfamily is name/code-keyword only —
    // there's no explicit field for this the way corner has default_code.
    const hay = code + " " + name.toUpperCase();
    if (hay.includes("SINK")) return "sink";
    if (hay.includes("VANITY")) return "vanity";
    if (hay.includes("DRAWER")) return "drawer";
    if (/2[- ]?DOOR|DOUBLE/.test(hay)) return "base2";
    return "base1";
}

// ─── Axonometric projection ─────────────────────────────────────────────────

/**
 * @param {number} x
 * @param {number} y — height
 * @param {number} z
 * @returns {[number, number]} [X, Y] in projected (pre-fit) 2D space
 */
function project(x, y, z) {
    return [(x - z) * 0.866, (x + z) * 0.5 - y];
}

// ─── Footprints ──────────────────────────────────────────────────────────

function rectFootprint(w, d) {
    return [[0, 0], [w, 0], [w, d], [0, d]];
}

// cornerL / cornerBlind share this footprint: an L with a square notch
// clipped from the far corner (the corner AWAY from the room's actual
// corner at the origin) — that far notch is where the perpendicular run
// of cabinets already occupies the floor.
function cornerLFootprint() {
    const k = 0.46 * S;
    return [[0, 0], [S, 0], [S, k], [k, k], [k, S], [0, S]];
}

// cornerDiag: the same outer square with the far corner clipped by a
// single diagonal cut instead of a square notch — the classic
// Lazy-Susan/diagonal corner plan (5 points vs the L's 6).
function cornerDiagFootprint() {
    const k = 0.40 * S;
    return [[0, 0], [S, 0], [S, k], [k, S], [0, S]];
}

// ─── Visible side faces from a CCW footprint ────────────────────────────────

/**
 * Extrude a CCW [x,z] footprint from y0 to y1 and return only the
 * visible side faces (outward normal (dz,-dx) with nx+nz > 0), each
 * carrying its own edge index so family builders can target a specific
 * physical face (e.g. "the blind leg's outer wall") deterministically
 * instead of guessing from face count/category alone.
 */
function visibleSideFaces(footprint, y0, y1) {
    const faces = [];
    const n = footprint.length;
    for (let i = 0; i < n; i++) {
        const [xa, za] = footprint[i];
        const [xb, zb] = footprint[(i + 1) % n];
        const dx = xb - xa, dz = zb - za;
        const nx = dz, nz = -dx;
        if (nx + nz <= 0) continue;
        // Room-facing = normal mostly +z, side = mostly +x, angled =
        // otherwise. Every footprint edge here is axis-aligned except
        // the diagonal cut, so nx or nz is exactly 0 for every
        // rectangle/L face and neither is 0 for the diagonal cut.
        let category;
        if (nx === 0) category = "room";
        else if (nz === 0) category = "side";
        else category = "angled";
        faces.push({ edgeIndex: i, a: [xa, za], b: [xb, zb], y0, y1, category });
    }
    return faces;
}

function faceQuadPoints(face) {
    const [xa, za] = face.a, [xb, zb] = face.b;
    return [
        project(xa, face.y0, za),
        project(xb, face.y0, zb),
        project(xb, face.y1, zb),
        project(xa, face.y1, za),
    ];
}

function faceMeanXZ(face) {
    const [xa, za] = face.a, [xb, zb] = face.b;
    return ((xa + za) + (xb + zb)) / 2;
}

// The face's own physical horizontal length (model units) — used to turn
// a "~1 unit" door-leaf inset into a t-fraction independent of the
// face's width (a corner leg and a 24" base front need different
// fractions for the same 1-unit gap).
function faceLength(face) {
    const [xa, za] = face.a, [xb, zb] = face.b;
    return Math.hypot(xb - xa, zb - za);
}

// A point on a face's plane at horizontal fraction t (0..1 along a->b)
// and vertical fraction u (0..1 along y0->y1) — the primitive every
// "thin projected line" detail (toe-kick, stiles, rails) is built from.
function facePoint(face, t, u) {
    const [xa, za] = face.a, [xb, zb] = face.b;
    const x = xa + (xb - xa) * t;
    const z = za + (zb - za) * t;
    const y = face.y0 + (face.y1 - face.y0) * u;
    return project(x, y, z);
}

// A point on the TOP face (the footprint itself, at height h) at
// fractional (tx, tz) across a rectangular footprint's own width/depth.
function topPoint(w, d, h, tx, tz) {
    return project(w * tx, h, d * tz);
}

// ─── Family geometry table ───────────────────────────────────────────────

/**
 * @returns {{footprint: number[][], h: number, corner: null|"L"|"diag",
 *   toeKick: boolean, centreStile: boolean, drawerRails: boolean,
 *   midRailFrac: number|null, basin: boolean, blindReturn: boolean}}
 */
function familyGeometry(family, ctx) {
    const { W, isWallCorner, isTwoDoor } = ctx;
    const base = {
        corner: null, toeKick: false, centreStile: false,
        drawerRails: false, midRailFrac: null, basin: false, blindReturn: false,
    };
    switch (family) {
        case "base1":
            return { ...base, footprint: rectFootprint(W, 12), h: 16, toeKick: true };
        case "base2":
        case "vanity":
            return { ...base, footprint: rectFootprint(W, 12), h: 16, toeKick: true, centreStile: true };
        case "drawer":
            return { ...base, footprint: rectFootprint(W, 12), h: 16, toeKick: true, drawerRails: true };
        case "sink":
            return { ...base, footprint: rectFootprint(W, 12), h: 16, toeKick: true, centreStile: true, basin: true };
        case "wall":
            return { ...base, footprint: rectFootprint(W, 8), h: 12, centreStile: isTwoDoor };
        case "tall":
            return { ...base, footprint: rectFootprint(W, 10), h: 26, toeKick: true, midRailFrac: 0.62 };
        case "panel":
            return { ...base, footprint: rectFootprint(W, 10), h: 22 };
        case "cornerL":
            return {
                ...base, footprint: cornerLFootprint(), corner: "L",
                h: isWallCorner ? 12 : 16, toeKick: !isWallCorner,
            };
        case "cornerBlind":
            return { ...base, footprint: cornerLFootprint(), corner: "L", h: 16, toeKick: true, blindReturn: true };
        case "cornerDiag":
            return { ...base, footprint: cornerDiagFootprint(), corner: "diag", h: 16, toeKick: true };
        default:
            return { ...base, footprint: rectFootprint(W, 12), h: 16, toeKick: true };
    }
}

// ─── Front details (toe kick / stiles / rails / basin) ──────────────────────
// Thin projected quads/lines on the appropriate visible face — subtle,
// stroke-only, 0.5-0.75 width. Returns raw (pre-fit) 2D segments/quads.

function buildDetails(g, faces) {
    const lines = []; // each: [[x1,y1],[x2,y2]]
    const rects = [];  // each: [[x,y], ...] closed polygon (basin inset)
    const findFace = (idx) => faces.find((f) => f.edgeIndex === idx);

    if (!g.corner) {
        // Rectangle footprints always emit exactly 2 visible faces:
        // edgeIndex 1 = the depth (side) face, edgeIndex 2 = the full-
        // width (room/front, door) face — see rectFootprint's point
        // order. Every "door front" detail lives on the front face.
        const front = findFace(2);
        if (front) {
            if (g.toeKick) lines.push([facePoint(front, 0.06, 0.10), facePoint(front, 0.94, 0.10)]);
            if (g.centreStile) lines.push([facePoint(front, 0.5, 0.08), facePoint(front, 0.5, 0.95)]);
            if (g.drawerRails) {
                lines.push([facePoint(front, 0.06, 0.42), facePoint(front, 0.94, 0.42)]);
                lines.push([facePoint(front, 0.06, 0.70), facePoint(front, 0.94, 0.70)]);
            }
            if (g.midRailFrac != null) {
                lines.push([facePoint(front, 0.06, g.midRailFrac), facePoint(front, 0.94, g.midRailFrac)]);
            }
        }
        if (g.basin) {
            // Basin inset on the TOP face, not a side face — a small
            // recessed rect roughly centred on the footprint.
            const w = g.footprintW, d = g.footprintD, h = g.h;
            rects.push([
                topPoint(w, d, h, 0.28, 0.30),
                topPoint(w, d, h, 0.72, 0.30),
                topPoint(w, d, h, 0.72, 0.78),
                topPoint(w, d, h, 0.28, 0.78),
            ]);
        }
    } else if (g.corner === "L") {
        // Leg A's outer wall (edgeIndex 1, x=S face) is the return leg
        // — the one FILL_RETURN darkens for cornerBlind. Leg B's outer
        // wall (edgeIndex 4, z=S face) is the front/door leg: that's
        // where the door stile lives, on both cornerL and cornerBlind
        // ("cornerBlind = same L plus the return face filled...").
        const front = findFace(4);
        if (front) {
            if (g.toeKick) lines.push([facePoint(front, 0.06, 0.08), facePoint(front, 0.94, 0.08)]);
            lines.push([facePoint(front, 0.5, 0.08), facePoint(front, 0.5, 0.92)]);
        }
    } else if (g.corner === "diag") {
        // The diagonal cut face (edgeIndex 2) carries the door — the
        // one visibly angled face of this footprint.
        const angled = findFace(2);
        if (angled) {
            if (g.toeKick) lines.push([facePoint(angled, 0.08, 0.08), facePoint(angled, 0.92, 0.08)]);
            lines.push([facePoint(angled, 0.5, 0.08), facePoint(angled, 0.5, 0.92)]);
        }
    }

    return { lines, rects };
}

// ─── Door/drawer-front accents ──────────────────────────────────────────
// Per-family walnut door leaves — inset quads on the family's front (or,
// for cornerDiag, angled) face. Each accent is tagged with its parent
// `face` object (not a copy of its mean) so the caller can give it the
// exact same sort key as the face it sits on: with a stable sort and the
// face pushed into the paint list before its accent, that guarantees the
// accent paints immediately after its own face and never bleeds through
// a nearer one. Geometry, palette, culling and paint order are otherwise
// untouched — this only ADDS entries to the existing paint list.

function insetT(face) {
    const it = 1 / faceLength(face);
    return [it, 1 - it];
}

// `toeKickU` is the toe-kick line's own height-fraction (0.10 for the
// rectangular families' front face, 0.08 for the corner families' front/
// angled face — see buildDetails above) — pass it when the family has a
// toe kick so the leaf's bottom edge sits above the kick, not inset from
// the raw bottom edge.
function insetU(face, toeKickU) {
    const iv = 1 / (face.y1 - face.y0);
    const u0 = toeKickU != null ? toeKickU + iv : iv;
    const u1 = 1 - iv;
    return [u0, u1];
}

function leafQuad(face, t0, t1, u0, u1) {
    return [
        facePoint(face, t0, u0),
        facePoint(face, t1, u0),
        facePoint(face, t1, u1),
        facePoint(face, t0, u1),
    ];
}

// Two leaves split at the horizontal centre stile (t=0.5), each inset
// from the stile by the same fraction as the outer-edge inset.
function splitLeavesH(face, t0, t1, u0, u1) {
    const gap = t0 * 0.5;
    return [
        leafQuad(face, t0, 0.5 - gap, u0, u1),
        leafQuad(face, 0.5 + gap, t1, u0, u1),
    ];
}

function buildDoorAccents(family, g, faces) {
    const findFace = (idx) => faces.find((f) => f.edgeIndex === idx);
    const accents = []; // each: { face, quad }
    const add = (face, quad) => accents.push({ face, quad });

    if (family === "panel") {
        // A panel is just a panel — an accent would lie about what it is.
        return accents;
    }

    if (family === "base1") {
        const front = findFace(2);
        if (front) {
            const [t0, t1] = insetT(front);
            const [u0, u1] = insetU(front, g.toeKick ? 0.10 : null);
            add(front, leafQuad(front, t0, t1, u0, u1));
        }
    } else if (family === "base2" || family === "vanity" || family === "sink") {
        // sink: doors like base2 — the basin inset (buildDetails' `rects`)
        // stays neutral, untouched here.
        const front = findFace(2);
        if (front) {
            const [t0, t1] = insetT(front);
            const [u0, u1] = insetU(front, g.toeKick ? 0.10 : null);
            for (const quad of splitLeavesH(front, t0, t1, u0, u1)) add(front, quad);
        }
    } else if (family === "drawer") {
        const front = findFace(2);
        if (front) {
            const [t0, t1] = insetT(front);
            const iv = 1 / (front.y1 - front.y0);
            const gap = iv * 0.5;
            const bottom = g.toeKick ? 0.10 + iv : iv;
            const top = 1 - iv;
            // Three fronts between the two drawer-rail lines drawn in
            // buildDetails at u=0.42 and u=0.70.
            const bands = [[bottom, 0.42 - gap], [0.42 + gap, 0.70 - gap], [0.70 + gap, top]];
            for (const [ub0, ub1] of bands) add(front, leafQuad(front, t0, t1, ub0, ub1));
        }
    } else if (family === "wall") {
        const front = findFace(2);
        if (front) {
            const [t0, t1] = insetT(front);
            const [u0, u1] = insetU(front, null);
            if (g.centreStile) {
                for (const quad of splitLeavesH(front, t0, t1, u0, u1)) add(front, quad);
            } else {
                add(front, leafQuad(front, t0, t1, u0, u1));
            }
        }
    } else if (family === "tall") {
        const front = findFace(2);
        if (front) {
            const [t0, t1] = insetT(front);
            const iv = 1 / (front.y1 - front.y0);
            const gap = iv * 0.5;
            const mid = g.midRailFrac;
            const bottom = g.toeKick ? 0.10 + iv : iv;
            const top = 1 - iv;
            add(front, leafQuad(front, t0, t1, bottom, mid - gap));
            add(front, leafQuad(front, t0, t1, mid + gap, top));
        }
    } else if (family === "cornerL" || family === "cornerBlind") {
        // The front/door leg only (edgeIndex 4) — cornerBlind's darkened
        // FILL_RETURN leg (edgeIndex 1) is untouched by design: coloured
        // door vs darkened return is the whole discriminator.
        const front = findFace(4);
        if (front) {
            const [t0, t1] = insetT(front);
            const [u0, u1] = insetU(front, g.toeKick ? 0.08 : null);
            add(front, leafQuad(front, t0, t1, u0, u1));
        }
    } else if (family === "cornerDiag") {
        const angled = findFace(2);
        if (angled) {
            const [t0, t1] = insetT(angled);
            const [u0, u1] = insetU(angled, g.toeKick ? 0.08 : null);
            add(angled, leafQuad(angled, t0, t1, u0, u1));
        }
    }

    return accents;
}

// ─── Number formatting ───────────────────────────────────────────────────

function r2(n) {
    const v = Math.round(n * 100) / 100;
    return (v === 0 ? 0 : v).toFixed(2);
}

function ptsAttr(pts) {
    return pts.map(([x, y]) => `${r2(x)},${r2(y)}`).join(" ");
}

// ─── Main entry point ─────────────────────────────────────────────────────

/**
 * Build a complete, self-contained axonometric `<svg>` string for a
 * catalog product's 46px thumbnail. Pure function — no DOM, safe to
 * call at render time in a `t-out`/data-URI context.
 *
 * @param {object} product
 * @param {number} [sizePx=46]
 * @returns {string} a complete `<svg ...>...</svg>` document
 */
export function cabinetThumbSVG(product, sizePx) {
    const size = sizePx || 46;
    const p = product || {};
    const family = thumbFamily(p);

    const code = String(p.default_code || p.sku || "").toUpperCase();
    const name = String(p.name || "").toLowerCase();
    const isWallCorner = code.includes("WALL-CORNER");
    const isTwoDoor = /2[- ]?DOOR|DOUBLE/.test((code + " " + name).toUpperCase());

    const rawWidth = Number(p.width_in);
    const width = Number.isFinite(rawWidth) && rawWidth > 0 ? rawWidth : 24;
    const clampedW = Math.min(45, Math.max(3, width));
    const W = 8 + (clampedW / 45) * 16;

    const g = familyGeometry(family, { W, isWallCorner, isTwoDoor });
    const { footprint, h } = g;
    // Only meaningful for rect (non-corner) footprints; harmless
    // otherwise since only the basin detail (base-bucket-only) reads it.
    g.footprintW = footprint[1] ? footprint[1][0] : 0;
    g.footprintD = footprint[2] ? footprint[2][1] : 0;

    const sideFaces = visibleSideFaces(footprint, 0, h);

    // Ground shadow — footprint at y=0, offset, painted first.
    const shadowPts = footprint.map(([x, z]) => {
        const [X, Y] = project(x, 0, z);
        return [X + 1.2, Y + 1.2];
    });

    // Top face + visible side faces, each tagged with its own
    // mean(x+z) for the back-to-front paint sort.
    const paintFaces = [];
    paintFaces.push({
        pts: footprint.map(([x, z]) => project(x, h, z)),
        fill: FILL_TOP,
        mean: footprint.reduce((s, [x, z]) => s + x + z, 0) / footprint.length,
    });
    for (const f of sideFaces) {
        let fill = f.category === "room" ? FILL_ROOM : f.category === "side" ? FILL_SIDE : FILL_ANGLED;
        if (g.blindReturn && f.edgeIndex === 1) fill = FILL_RETURN;
        paintFaces.push({ pts: faceQuadPoints(f), fill, mean: faceMeanXZ(f) });
    }
    // Door/drawer-front accents, tagged with their parent face's exact
    // `mean` (not a copy — reusing the same computation) so the stable
    // sort below places each accent immediately after the face it was
    // pushed after above, never before it and never floating free at a
    // different depth.
    for (const acc of buildDoorAccents(family, g, sideFaces)) {
        paintFaces.push({ pts: acc.quad, fill: DOOR_FILL, mean: faceMeanXZ(acc.face) });
    }
    paintFaces.sort((a, b) => a.mean - b.mean);

    const { lines, rects } = buildDetails(g, sideFaces);

    // ─── Fit: bbox of ALL emitted points, pad 2.5, scale/translate ─────────
    const allPts = [
        ...shadowPts,
        ...paintFaces.flatMap((f) => f.pts),
        ...lines.flatMap((l) => l),
        ...rects.flatMap((rc) => rc),
    ];
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of allPts) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }
    const pad = 2.5;
    minX -= pad; maxX += pad; minY -= pad; maxY += pad;
    const bw = maxX - minX, bh = maxY - minY;
    const scale = size / Math.max(bw, bh);
    const offX = (size - bw * scale) / 2;
    const offY = (size - bh * scale) / 2;
    const fit = ([x, y]) => [(x - minX) * scale + offX, (y - minY) * scale + offY];

    // ─── Assemble markup ─────────────────────────────────────────────────
    const parts = [];
    parts.push(`<polygon points="${ptsAttr(shadowPts.map(fit))}" fill="${SHADOW_FILL}"/>`);
    for (const f of paintFaces) {
        parts.push(
            `<polygon points="${ptsAttr(f.pts.map(fit))}" fill="${f.fill}" stroke="${STROKE}" stroke-width="${STROKE_W}"/>`,
        );
    }
    for (const [a, b] of lines) {
        const [x1, y1] = fit(a), [x2, y2] = fit(b);
        parts.push(
            `<line x1="${r2(x1)}" y1="${r2(y1)}" x2="${r2(x2)}" y2="${r2(y2)}" stroke="${STROKE}" stroke-width="${DETAIL_STROKE_W}"/>`,
        );
    }
    for (const rc of rects) {
        parts.push(
            `<polygon points="${ptsAttr(rc.map(fit))}" fill="none" stroke="${STROKE}" stroke-width="${DETAIL_STROKE_W}"/>`,
        );
    }

    const label = FAMILY_LABEL[family] || "Cabinet icon";
    return (
        `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
        `viewBox="0 0 ${size} ${size}" role="img" aria-label="${label}">` +
        parts.join("") +
        `</svg>`
    );
}
