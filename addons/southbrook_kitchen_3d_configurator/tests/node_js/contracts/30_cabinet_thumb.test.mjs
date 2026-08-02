/**
 * Panel P2 (catalog redesign) contract — cabinet_thumb.esm.js.
 *
 * Pure-function module (no OWL, no DOM, no THREE) so this contract
 * imports it directly by relative path — same pattern as contract
 * #29 (catalog_list.esm.js, the closest sibling) and contract #3's
 * real-file import. No jsdom, no `@odoo/owl`, nothing to mock:
 * `thumbFamily` / `cabinetThumbSVG` are exercised exactly as the
 * (separately owned) catalog-panel component will call them.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CABINET_THUMB_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "canvas", "cabinet_thumb.esm.js",
);

const { thumbFamily, cabinetThumbSVG } = await import(CABINET_THUMB_SRC);

// ─── helpers ─────────────────────────────────────────────────────────────

// Every element this module emits is a self-closing <polygon .../> or
// <line .../> with a fixed attribute order — a light regex is enough to
// prove well-formedness without pulling in a real XML parser.
const INNER_RE = new RegExp(
    "^(?:" +
    '<polygon points="[^"]*" fill="[^"]*"(?: stroke="[^"]*" stroke-width="[^"]*")?/>|' +
    '<line x1="[^"]*" y1="[^"]*" x2="[^"]*" y2="[^"]*" stroke="[^"]*" stroke-width="[^"]*"/>' +
    ")*$",
);

function isWellFormed(svg) {
    const m = svg.match(/^<svg([^>]*)>([\s\S]*)<\/svg>$/);
    if (!m) return false;
    const [, openAttrs, inner] = m;
    if (!/role="img"/.test(openAttrs)) return false;
    if (!/aria-label="[^"]+"/.test(openAttrs)) return false;
    // No stray top-level <svg or </svg> inside the body.
    if (/<svg|<\/svg>/.test(inner)) return false;
    return INNER_RE.test(inner);
}

function allCoords(svg) {
    const nums = [];
    for (const m of svg.matchAll(/points="([^"]+)"/g)) {
        for (const pair of m[1].trim().split(/\s+/)) nums.push(...pair.split(",").map(Number));
    }
    for (const m of svg.matchAll(/<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"/g)) {
        nums.push(+m[1], +m[2], +m[3], +m[4]);
    }
    return nums;
}

function firstPoint(svg, fillPattern) {
    const re = new RegExp(`<polygon points="([^"]+)" fill="${fillPattern}"`);
    const m = svg.match(re);
    assert.ok(m, `expected a polygon with fill matching ${fillPattern}`);
    return m[1].trim().split(/\s+/)[0].split(",").map(Number);
}

// Derives the family's height (h, in model units) purely from the
// rendered SVG: the top face's first point is always project(0,h,0) =
// (0,-h), and the shadow's first point is always project(0,0,0)+[1.2,1.2]
// = (1.2,1.2) (both footprint builders start at [0,0]). The ratio of
// their raw X/Y deltas cancels the fit's scale+offset, so this recovers
// h exactly from the module's own published projection formula —
// X=(x-z)*0.866, Y=(x+z)*0.5-y — without assuming anything about the
// implementation beyond that formula and the fit being affine.
function deriveH(svg) {
    const top0 = firstPoint(svg, "#f2f4f7");
    const shadow0 = firstPoint(svg, String.raw`rgba\(20,26,34,\.10\)`);
    const scale = (shadow0[0] - top0[0]) / 1.2;
    return (shadow0[1] - top0[1]) / scale - 1.2;
}

function topFacePointCount(svg) {
    const m = svg.match(/<polygon points="([^"]+)" fill="#f2f4f7"/);
    assert.ok(m, "expected a top-face polygon");
    return m[1].trim().split(/\s+/).length;
}

const FAMILY_PRODUCTS = {
    base1: { name: "Base Cabinet 24", default_code: "SB-BASE-24", cabinet_type: "base", width_in: 24 },
    base2: { name: "Base Cabinet 2-Door 30", default_code: "SB-BASE2-30", cabinet_type: "base", width_in: 30 },
    drawer: { name: "Drawer Bank 24", default_code: "SB-DRW-24", cabinet_type: "base", width_in: 24 },
    sink: { name: "Sink Base 36", default_code: "SB-SINK-36", cabinet_type: "base", width_in: 36 },
    vanity: { name: "Vanity Base 30", default_code: "SB-VANITY-30", cabinet_type: "base", width_in: 30 },
    wall: { name: "Wall Cabinet 24", default_code: "SB-WALL-24", cabinet_type: "wall", width_in: 24 },
    tall: { name: "Tall Pantry 24", default_code: "SB-TALL-24", cabinet_type: "tall", width_in: 24 },
    panel: { name: "End Panel 24", default_code: "SB-PANEL-24", cabinet_type: "panel", width_in: 24 },
    cornerL: { name: "Corner Base 36", default_code: "SB-CORNER-36", cabinet_type: "corner", width_in: 36 },
    cornerBlind: { name: "Blind Corner Base 42", default_code: "SB-CORNER-BLIND-42", cabinet_type: "corner", width_in: 42 },
    cornerDiag: { name: "Diagonal Corner Base 36", default_code: "SB-CORNER-DIAG-36", cabinet_type: "corner", width_in: 36 },
};

// ─── thumbFamily: precedence (explicit beats name keyword) ────────────────

test("thumbFamily: cabinet_type=corner + explicit BLIND code wins even when the name says 'Diagonal'", () => {
    const product = {
        cabinet_type: "corner",
        default_code: "SB-CORNER-BLIND-42",
        name: "Diagonal Corner Unit 42", // deliberately conflicting
    };
    assert.equal(thumbFamily(product), "cornerBlind");
});

test("thumbFamily: cabinet_type=corner + explicit DIAG/LSUSAN code wins over a conflicting name", () => {
    assert.equal(
        thumbFamily({ cabinet_type: "corner", default_code: "SB-CORNER-DIAG-36", name: "Blind Corner 36" }),
        "cornerDiag",
    );
    assert.equal(
        thumbFamily({ cabinet_type: "corner", default_code: "SB-LSUSAN-33", name: "Blind Corner 33" }),
        "cornerDiag",
    );
});

test("thumbFamily: cabinet_type=corner + explicit WALL-CORNER code wins over a conflicting name", () => {
    assert.equal(
        thumbFamily({ cabinet_type: "corner", default_code: "SB-WALL-CORNER-24", name: "Blind Corner 24" }),
        "cornerL",
    );
});

test("thumbFamily: sku is read when default_code is absent (the /products payload's field name)", () => {
    assert.equal(thumbFamily({ cabinet_type: "corner", sku: "SB-CORNER-BLIND-42" }), "cornerBlind");
});

// ─── thumbFamily: explicit southbrook_thumb_family field precedence ───────
// Debt fix (2026-08-02) — product.template.southbrook_thumb_family, read
// here as product.thumb_family (the /products payload's field name for
// it — see controllers/main.py's _product_payload). Explicit wins
// outright over cabinet_type + SKU + name; unset falls back UNCHANGED.

const ALL_FAMILIES = [
    "base1", "base2", "drawer", "sink", "vanity", "wall", "tall", "panel",
    "cornerL", "cornerBlind", "cornerDiag",
];

test("thumbFamily: explicit thumb_family wins outright over cabinet_type/code/name, for all 11 values", () => {
    for (const family of ALL_FAMILIES) {
        // Every other signal deliberately disagrees with `family`.
        const product = {
            thumb_family: family,
            cabinet_type: "wall",
            default_code: "SB-CORNER-BLIND-99",
            name: "Diagonal Corner Cabinet 99",
        };
        assert.equal(
            thumbFamily(product), family,
            `explicit thumb_family=${family} must win regardless of conflicting cabinet_type/code/name`,
        );
    }
});

test("thumbFamily: explicit thumb_family also wins when cabinet_type/code/name are entirely absent", () => {
    for (const family of ALL_FAMILIES) {
        assert.equal(thumbFamily({ thumb_family: family }), family);
    }
});

test("thumbFamily: unset thumb_family (false/null/undefined/empty string/absent key) falls back to inference, unchanged", () => {
    const base = { cabinet_type: "corner", default_code: "SB-CORNER-BLIND-42", name: "Diagonal Corner Unit 42" };
    assert.equal(thumbFamily(base), "cornerBlind", "no thumb_family key at all");
    assert.equal(thumbFamily({ ...base, thumb_family: false }), "cornerBlind");
    assert.equal(thumbFamily({ ...base, thumb_family: null }), "cornerBlind");
    assert.equal(thumbFamily({ ...base, thumb_family: undefined }), "cornerBlind");
    assert.equal(thumbFamily({ ...base, thumb_family: "" }), "cornerBlind");
});

test("thumbFamily: an unrecognised thumb_family value is ignored, never thrown, falls back to inference", () => {
    const base = { cabinet_type: "wall", name: "Wall Cabinet 24" };
    assert.equal(thumbFamily({ ...base, thumb_family: "bogus-value" }), "wall");
    assert.equal(thumbFamily({ ...base, thumb_family: "CORNERL" }), "wall",
        "vocabulary is case-sensitive — a near-miss is still an unrecognised value, not a silent coercion");
});

test("cabinetThumbSVG: every explicit thumb_family value renders a well-formed SVG carrying that family's own aria-label", () => {
    for (const family of ALL_FAMILIES) {
        const svg = cabinetThumbSVG({ thumb_family: family, cabinet_type: "base", width_in: 24 }, 46);
        assert.ok(isWellFormed(svg), `${family}: not well-formed via explicit override:\n${svg}`);
        assert.equal(thumbFamily({ thumb_family: family }), family);
    }
});

// ─── thumbFamily: name-keyword fallback ────────────────────────────────────

test("thumbFamily: corner name fallback works when no code is present", () => {
    assert.equal(thumbFamily({ cabinet_type: "corner", name: "Blind Corner Cabinet 42" }), "cornerBlind");
    assert.equal(thumbFamily({ cabinet_type: "corner", name: "Diagonal Corner Cabinet 36" }), "cornerDiag");
    assert.equal(thumbFamily({ cabinet_type: "corner", name: "Corner Base 36" }), "cornerL",
        "no code, no recognised keyword -> plain L corner, never a guess at blind/diagonal");
});

test("thumbFamily: base-bucket name fallback (sink / vanity / drawer / 2-door)", () => {
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Sink Base 36" }), "sink");
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Vanity Base 30" }), "vanity");
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Drawer Bank 24" }), "drawer");
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Base Cabinet 2-Door 30" }), "base2");
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Base Cabinet Double 30" }), "base2");
    assert.equal(thumbFamily({ cabinet_type: "base", name: "Base Cabinet 24" }), "base1",
        "no matching keyword -> the plain single-door default");
});

// ─── thumbFamily: top-level bucket mapping ─────────────────────────────────

test("thumbFamily: cabinet_type maps wall/tall/panel/filler directly, base is the default bucket", () => {
    assert.equal(thumbFamily({ cabinet_type: "wall", name: "Anything" }), "wall");
    assert.equal(thumbFamily({ cabinet_type: "tall", name: "Anything" }), "tall");
    assert.equal(thumbFamily({ cabinet_type: "panel", name: "Anything" }), "panel");
    assert.equal(thumbFamily({ cabinet_type: "filler", name: "Anything" }), "panel",
        "fillers have no dedicated family; they render as a detail-free panel");
    assert.equal(thumbFamily({}), "base1", "missing cabinet_type degrades to the base bucket, never throws");
    assert.equal(thumbFamily(undefined), "base1", "missing product never throws");
});

// ─── cabinetThumbSVG: blind corner return-face darkening ──────────────────

test("cabinetThumbSVG: a blind corner emits the #9ea6b0 return-face fill; a plain cornerL never does", () => {
    const blindSvg = cabinetThumbSVG(FAMILY_PRODUCTS.cornerBlind, 46);
    const plainSvg = cabinetThumbSVG(FAMILY_PRODUCTS.cornerL, 46);
    assert.ok(blindSvg.includes("#9ea6b0"), "cornerBlind must darken its return leg");
    assert.ok(!plainSvg.includes("#9ea6b0"), "a plain L corner must never emit the blind return-face colour");
});

test("cabinetThumbSVG: no other family ever emits the blind return-face colour", () => {
    for (const [family, product] of Object.entries(FAMILY_PRODUCTS)) {
        if (family === "cornerBlind") continue;
        assert.ok(
            !cabinetThumbSVG(product, 46).includes("#9ea6b0"),
            `${family} must not emit the blind-only #9ea6b0 fill`,
        );
    }
});

// ─── cabinetThumbSVG: footprint point counts (L vs diagonal) ──────────────

test("cabinetThumbSVG: the diagonal corner's footprint has 5 points, the L corner's has 6", () => {
    assert.equal(topFacePointCount(cabinetThumbSVG(FAMILY_PRODUCTS.cornerDiag, 46)), 5);
    assert.equal(topFacePointCount(cabinetThumbSVG(FAMILY_PRODUCTS.cornerL, 46)), 6);
    assert.equal(topFacePointCount(cabinetThumbSVG(FAMILY_PRODUCTS.cornerBlind, 46)), 6,
        "cornerBlind shares the L's 6-point footprint — only the fill differs");
});

// ─── cabinetThumbSVG: every family -> well-formed SVG within the viewBox ──

test("cabinetThumbSVG: every family produces a well-formed <svg> with role/aria-label", () => {
    for (const [family, product] of Object.entries(FAMILY_PRODUCTS)) {
        const svg = cabinetThumbSVG(product, 46);
        assert.ok(isWellFormed(svg), `${family}: not well-formed:\n${svg}`);
        assert.match(svg, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="46" height="46" viewBox="0 0 46 46"/,
            `${family}: root <svg> attributes`);
    }
});

test("cabinetThumbSVG: every family's emitted points land inside the padded 46x46 viewBox", () => {
    for (const [family, product] of Object.entries(FAMILY_PRODUCTS)) {
        const svg = cabinetThumbSVG(product, 46);
        const nums = allCoords(svg);
        assert.ok(nums.length > 0, `${family}: expected at least one coordinate`);
        for (const n of nums) {
            assert.ok(n >= -0.01 && n <= 46.01, `${family}: coordinate ${n} outside the fitted [0,46] viewBox`);
        }
    }
});

test("cabinetThumbSVG: sizePx controls both the pixel box and the viewBox", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.base1, 90);
    assert.match(svg, /width="90" height="90" viewBox="0 0 90 90"/);
});

test("cabinetThumbSVG: never throws on a missing/empty product, and still returns well-formed SVG", () => {
    assert.doesNotThrow(() => cabinetThumbSVG(undefined, 46));
    assert.doesNotThrow(() => cabinetThumbSVG({}, 46));
    assert.ok(isWellFormed(cabinetThumbSVG({}, 46)));
});

// ─── cabinetThumbSVG: width clamping ───────────────────────────────────────

test("cabinetThumbSVG: width_in below 3 clamps to the same geometry as exactly 3", () => {
    const svgBelow = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 1 }, 46);
    const svgAt = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 3 }, 46);
    assert.equal(svgBelow, svgAt, "1in and 3in must render byte-identical (both clamp to the 3in floor)");
});

test("cabinetThumbSVG: width_in above 45 clamps to the same geometry as exactly 45", () => {
    const svgAbove = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 100 }, 46);
    const svgAt = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 45 }, 46);
    assert.equal(svgAbove, svgAt, "100in and 45in must render byte-identical (both clamp to the 45in ceiling)");
});

test("cabinetThumbSVG: width actually matters in the unclamped range (clamping isn't accidentally a no-op)", () => {
    const svg24 = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 24 }, 46);
    const svg36 = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 36 }, 46);
    assert.notEqual(svg24, svg36);
});

test("cabinetThumbSVG: missing/non-positive width_in defaults to 24in, not a thrown error or NaN", () => {
    const svgDefault = cabinetThumbSVG({ cabinet_type: "base", name: "Mystery Base" }, 46);
    const svg24 = cabinetThumbSVG({ ...FAMILY_PRODUCTS.base1, width_in: 24, default_code: undefined, name: "Mystery Base" }, 46);
    assert.equal(svgDefault, svg24);
    assert.ok(!svgDefault.includes("NaN"));
});

// ─── cabinetThumbSVG: wall-corner vs base-corner height ────────────────────

test("cabinetThumbSVG: a wall-mounted corner (SB-WALL-CORNER*) renders at h=12; a floor corner at h=16", () => {
    const wallCorner = { name: "Wall Corner 24", default_code: "SB-WALL-CORNER-24", cabinet_type: "corner", width_in: 24 };
    const baseCorner = { name: "Corner Base 36", default_code: "SB-CORNER-36", cabinet_type: "corner", width_in: 36 };
    assert.equal(thumbFamily(wallCorner), "cornerL");
    assert.equal(thumbFamily(baseCorner), "cornerL");

    // Large sizePx keeps the toFixed(2) coordinate rounding's relative
    // error tiny so the derived h lands within a hundredth of the true
    // value.
    const hWall = deriveH(cabinetThumbSVG(wallCorner, 4600));
    const hBase = deriveH(cabinetThumbSVG(baseCorner, 4600));
    assert.ok(Math.abs(hWall - 12) < 0.05, `expected wall-corner h~12, derived ${hWall}`);
    assert.ok(Math.abs(hBase - 16) < 0.05, `expected floor-corner h~16, derived ${hBase}`);
});

test("cabinetThumbSVG: a wall-mounted corner has no toe-kick detail; a floor corner does", () => {
    const wallCorner = { name: "Wall Corner 24", default_code: "SB-WALL-CORNER-24", cabinet_type: "corner", width_in: 24 };
    const baseCorner = { name: "Corner Base 36", default_code: "SB-CORNER-36", cabinet_type: "corner", width_in: 36 };
    const wallLines = (cabinetThumbSVG(wallCorner, 46).match(/<line/g) || []).length;
    const baseLines = (cabinetThumbSVG(baseCorner, 46).match(/<line/g) || []).length;
    assert.equal(wallLines, 1, "wall-corner: door stile only, no toe-kick line");
    assert.equal(baseLines, 2, "floor corner: toe-kick + door stile");
});

// ─── cabinetThumbSVG: no user text ever enters the markup ─────────────────

test("cabinetThumbSVG: aria-label is drawn from a fixed per-family lookup, not echoed from product.name", () => {
    const evil = { cabinet_type: "base", name: '"><script>alert(1)</script>', default_code: "" };
    const svg = cabinetThumbSVG(evil, 46);
    assert.ok(!svg.includes("<script"), "raw product.name must never reach the markup");
    assert.match(svg, /aria-label="Base cabinet icon"/);
});

// ─── cabinetThumbSVG: door/drawer-front accent (walnut #C9762B) ───────────
// Client amendment (Blum-reference screenshot): the carcass stays on the
// neutral FILL_* palette above; only door/drawer-front leaves carry
// colour. Count the accent fills per family — this is the discriminator,
// not exact leaf geometry.

function countFill(svg, hex) {
    const re = new RegExp(`fill="${hex}"`, "g");
    return (svg.match(re) || []).length;
}

test("cabinetThumbSVG: base1 emits exactly one walnut door-leaf fill", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.base1, 46);
    assert.equal(countFill(svg, "#C9762B"), 1);
});

test("cabinetThumbSVG: base2 and vanity each emit exactly two walnut door-leaf fills (split at the centre stile)", () => {
    assert.equal(countFill(cabinetThumbSVG(FAMILY_PRODUCTS.base2, 46), "#C9762B"), 2);
    assert.equal(countFill(cabinetThumbSVG(FAMILY_PRODUCTS.vanity, 46), "#C9762B"), 2);
});

test("cabinetThumbSVG: sink emits exactly two walnut door-leaf fills, and the basin inset stays neutral", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.sink, 46);
    assert.equal(countFill(svg, "#C9762B"), 2);
    // The basin inset is the stroke-only fill="none" rect from
    // buildDetails — untouched, still exactly one, never walnut.
    assert.equal(countFill(svg, "none"), 1, "basin inset must stay a single neutral fill=none rect");
});

test("cabinetThumbSVG: drawer emits exactly three walnut drawer-front fills (between the two rail lines)", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.drawer, 46);
    assert.equal(countFill(svg, "#C9762B"), 3);
});

test("cabinetThumbSVG: wall emits one walnut leaf for a single door, two for a 2-door wall cabinet", () => {
    const single = cabinetThumbSVG(FAMILY_PRODUCTS.wall, 46);
    assert.equal(countFill(single, "#C9762B"), 1);
    const twoDoor = cabinetThumbSVG(
        { ...FAMILY_PRODUCTS.wall, name: "Wall Cabinet 2-Door 30", default_code: "SB-WALL-2DOOR-30" },
        46,
    );
    assert.equal(countFill(twoDoor, "#C9762B"), 2);
});

test("cabinetThumbSVG: tall emits exactly two walnut leaves (split at the 0.62h mid rail)", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.tall, 46);
    assert.equal(countFill(svg, "#C9762B"), 2);
});

test("cabinetThumbSVG: panel emits zero walnut fills — a panel is just a panel", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.panel, 46);
    assert.equal(countFill(svg, "#C9762B"), 0);
});

test("cabinetThumbSVG: cornerL and cornerDiag each emit exactly one walnut door-leaf fill", () => {
    assert.equal(countFill(cabinetThumbSVG(FAMILY_PRODUCTS.cornerL, 46), "#C9762B"), 1);
    assert.equal(countFill(cabinetThumbSVG(FAMILY_PRODUCTS.cornerDiag, 46), "#C9762B"), 1);
});

test("cabinetThumbSVG: cornerBlind emits both the walnut accessible-leg door and the darkened #9ea6b0 return leg", () => {
    const svg = cabinetThumbSVG(FAMILY_PRODUCTS.cornerBlind, 46);
    assert.equal(countFill(svg, "#C9762B"), 1, "one door leaf, on the accessible leg only");
    assert.ok(svg.includes("#9ea6b0"), "the darkened return leg is unchanged");
});

test("cabinetThumbSVG: every door-leaf accent polygon uses the same stroke as every other painted face", () => {
    for (const [family, product] of Object.entries(FAMILY_PRODUCTS)) {
        const svg = cabinetThumbSVG(product, 46);
        const re = /<polygon points="[^"]*" fill="#C9762B"([^/]*)\/>/g;
        for (const m of svg.matchAll(re)) {
            assert.match(m[1], /stroke="#7d858e" stroke-width="0\.75"/, `${family}: accent stroke mismatch`);
        }
    }
});
