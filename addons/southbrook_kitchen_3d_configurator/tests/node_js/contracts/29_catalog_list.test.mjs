/**
 * Panel P2 (catalog redesign) contract — catalog_list.esm.js.
 *
 * Pure-function module (no OWL, no DOM, no THREE) so this contract
 * imports it directly by relative path — same pattern as contract
 * #3's real-file import — instead of going through
 * `mount_configurator.mjs`'s full component mount. No jsdom, no
 * `@odoo/owl`, nothing to mock: `parseProductName` / `liftHanding` /
 * `liftWidth` / `searchText` / `groupCatalog` are exercised exactly
 * as the (separately owned) catalog-panel component will call them.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CATALOG_LIST_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "canvas", "catalog_list.esm.js",
);

const {
    parseProductName,
    liftHanding,
    liftWidth,
    searchText,
    groupCatalog,
} = await import(CATALOG_LIST_SRC);

// ─── parseProductName ────────────────────────────────────────────────────────

test("parseProductName: strips the leading [SKU] bracket segment", () => {
    const { title } = parseProductName("[CB36-R] Corner Base 36 (Right Hand)");
    assert.equal(title, "Corner Base 36");
});

test("parseProductName: splits the trailing parenthetical into attrs, never into title", () => {
    const { title, attrs } = parseProductName(
        "[CB36-R] Corner Base 36 (Right Hand, White Melamine)",
    );
    assert.equal(title, "Corner Base 36");
    assert.equal(attrs, "Right Hand, White Melamine");
});

test("parseProductName: identical boilerplate attrs across products never leak into title", () => {
    // Two different cabinets sharing the exact same attrs boilerplate —
    // the title must still distinguish them and must never contain
    // the shared "(White Melamine, Shaker)" text.
    const a = parseProductName("[W24] Wall Cabinet 24 (White Melamine, Shaker)");
    const b = parseProductName("[W30] Wall Cabinet 30 (White Melamine, Shaker)");
    assert.equal(a.title, "Wall Cabinet 24");
    assert.equal(b.title, "Wall Cabinet 30");
    assert.equal(a.attrs, "White Melamine, Shaker");
    assert.equal(b.attrs, "White Melamine, Shaker");
    assert.ok(!a.title.includes("Melamine"));
    assert.ok(!b.title.includes("Melamine"));
});

test("parseProductName: no trailing parenthetical -> attrs is empty string", () => {
    const { title, attrs } = parseProductName("[B24] Base Cabinet");
    assert.equal(title, "Base Cabinet");
    assert.equal(attrs, "");
});

test("parseProductName: no leading [SKU] segment is tolerated (title/attrs still split)", () => {
    const { title, attrs } = parseProductName("Standalone Product (Maple)");
    assert.equal(title, "Standalone Product");
    assert.equal(attrs, "Maple");
});

test("parseProductName: null/undefined/empty input never throws", () => {
    assert.deepEqual(parseProductName(""), { title: "", attrs: "" });
    assert.deepEqual(parseProductName(undefined), { title: "", attrs: "" });
    assert.deepEqual(parseProductName(null), { title: "", attrs: "" });
});

// ─── liftHanding ─────────────────────────────────────────────────────────────

test("liftHanding: RH token in attrs -> R", () => {
    assert.equal(liftHanding({ sku: "CB36" }, "RH, White Melamine"), "R");
});

test("liftHanding: LH token in attrs -> L", () => {
    assert.equal(liftHanding({ sku: "CB36" }, "LH, White Melamine"), "L");
});

test("liftHanding: 'Right Hand' phrase in attrs -> R", () => {
    assert.equal(liftHanding({ sku: "CB36" }, "Right Hand, White Melamine"), "R");
});

test("liftHanding: 'Left Hand' phrase in attrs -> L", () => {
    assert.equal(liftHanding({ sku: "CB36" }, "Left Hand, White Melamine"), "L");
});

test("liftHanding: matching is case-insensitive", () => {
    assert.equal(liftHanding({ sku: "CB36" }, "right hand"), "R");
    assert.equal(liftHanding({ sku: "CB36" }, "lh"), "L");
});

test("liftHanding: falls back to the product's raw name when attrs has no token", () => {
    assert.equal(liftHanding({ name: "[CB36] Corner Base 36 RH", sku: "CB36" }, ""), "R");
    assert.equal(liftHanding({ name: "[CB36] Corner Base 36 LH", sku: "CB36" }, ""), "L");
});

test("liftHanding: trailing -R on the SKU is the fallback when no token is present anywhere", () => {
    assert.equal(liftHanding({ name: "Corner Base 36", sku: "CB36-R" }, ""), "R");
});

test("liftHanding: there is no bare -L SKU fallback — an unhanded -L SKU with no token stays null", () => {
    assert.equal(liftHanding({ name: "Corner Base 36", sku: "CB36-L" }, ""), null);
});

test("liftHanding: an RH/LH-shaped token embedded inside a larger word is not a match (word boundary)", () => {
    // "Airhorn" contains the literal substring "RH" but with no word
    // boundary on either side — must not false-positive as handing.
    assert.equal(liftHanding({ name: "Custom Airhorn Accessory", sku: "ACC-9" }, ""), null);
});

test("liftHanding: no token and no -R SKU suffix -> null", () => {
    assert.equal(liftHanding({ name: "Base Cabinet 24", sku: "B24" }, ""), null);
});

test("liftHanding: an attrs/name token takes priority over the SKU -R fallback", () => {
    // Deliberately conflicting signals: attrs says Left Hand, SKU ends -R.
    // The explicit token wins.
    assert.equal(liftHanding({ name: "Corner Base 36", sku: "CB36-R" }, "Left Hand"), "L");
});

// ─── liftWidth ───────────────────────────────────────────────────────────────

test("liftWidth: prefers product.width_in when present and positive", () => {
    assert.equal(liftWidth({ width_in: 24, name: "Base Cabinet (9 in)" }, "9 in"), 24);
});

test("liftWidth: recovers width from a '9 in' token in attrs when width_in is empty", () => {
    assert.equal(liftWidth({ width_in: 0, name: "[B9] Base Cabinet" }, "9 in, White Melamine"), 9);
});

test("liftWidth: recovers width from the title (via the product's name) when attrs has no match", () => {
    // The 9" base / 9" wall door case: no width_in, no width token in
    // attrs, but the human title itself carries it.
    assert.equal(liftWidth({ width_in: 0, name: '[WD9] Wall Door 9" ' }, "White Melamine"), 9);
});

test("liftWidth: attrs is checked before the title", () => {
    assert.equal(
        liftWidth({ width_in: 0, name: "[X] Cabinet 24 in Run" }, "12 in"),
        12,
        "attrs' 12 in must win over the title's 24 in",
    );
});

test("liftWidth: returns 0 when width_in is empty and no token is recoverable anywhere", () => {
    assert.equal(liftWidth({ width_in: 0, name: "[X] Mystery Cabinet" }, ""), 0);
});

test("liftWidth: missing product/attrs never throws", () => {
    assert.equal(liftWidth(undefined, undefined), 0);
    assert.equal(liftWidth({}, ""), 0);
});

// ─── searchText ──────────────────────────────────────────────────────────────

test("searchText: matches title + attrs + sku + material, lowercased", () => {
    const product = { sku: "CB36-R", material: "Maple" };
    const parsed = { title: "Corner Base 36", attrs: "Right Hand, Maple" };
    const hay = searchText(product, parsed);
    assert.ok(hay.includes("corner base 36"), "title must be searchable");
    assert.ok(hay.includes("right hand"), "attrs must be searchable even though the list hides it");
    assert.ok(hay.includes("cb36-r"), "sku must be searchable");
    assert.ok(hay.includes("maple"), "material must be searchable");
});

test("searchText: tolerates missing product/parsed fields", () => {
    assert.doesNotThrow(() => searchText(undefined, undefined));
    assert.equal(searchText({}, {}).trim(), "");
});

// ─── groupCatalog: category order + panels mapping ────────────────────────────

function product({ id, name, cabinetType, sku, width, price, material }) {
    return {
        product_id: id,
        name,
        sku: sku || "",
        cabinet_type: cabinetType,
        width_in: width,
        price: price != null ? price : 100,
        material: material || "white_melamine",
    };
}

test("groupCatalog: category order is exactly base, corner, wall, tall, panels, then unknown types alphabetically", () => {
    const products = [
        product({ id: 1, name: "[T1] Tall Pantry", cabinetType: "tall", sku: "T1", width: 24 }),
        product({ id: 2, name: "[C1] Corner Base", cabinetType: "corner", sku: "C1", width: 36 }),
        product({ id: 3, name: "[W1] Wall Cabinet", cabinetType: "wall", sku: "W1", width: 24 }),
        product({ id: 4, name: "[F1] Filler Strip", cabinetType: "filler", sku: "F1", width: 3 }),
        product({ id: 5, name: "[P1] End Panel", cabinetType: "panel", sku: "P1", width: 24 }),
        product({ id: 6, name: "[B1] Base Cabinet", cabinetType: "base", sku: "B1", width: 24 }),
        product({ id: 7, name: "[Z1] Zeta Widget", cabinetType: "zeta", sku: "Z1", width: 12 }),
        product({ id: 8, name: "[A1] Appliance Stub", cabinetType: "appliance", sku: "A1", width: 30 }),
    ];
    const { categories } = groupCatalog(products);
    assert.deepEqual(
        categories.map((c) => c.key),
        ["base", "corner", "wall", "tall", "panels", "appliance", "zeta"],
    );
    assert.deepEqual(
        categories.map((c) => c.label),
        [
            "Base Cabinets", "Corner Cabinets", "Wall Cabinets", "Tall Cabinets",
            "Panels & Fillers", "Appliance", "Zeta",
        ],
    );
});

test("groupCatalog: filler AND panel cabinet_type both map into the single 'panels' group", () => {
    const products = [
        product({ id: 1, name: "[F1] Filler Strip", cabinetType: "filler", sku: "F1", width: 3 }),
        product({ id: 2, name: "[P1] End Panel", cabinetType: "panel", sku: "P1", width: 24 }),
    ];
    const { categories } = groupCatalog(products);
    assert.equal(categories.length, 1);
    assert.equal(categories[0].key, "panels");
    assert.equal(categories[0].label, "Panels & Fillers");
    assert.equal(categories[0].count, 2);
});

test("groupCatalog: a category absent from the input is omitted, not emitted empty", () => {
    const products = [product({ id: 1, name: "[B1] Base Cabinet", cabinetType: "base", sku: "B1", width: 24 })];
    const { categories } = groupCatalog(products);
    assert.deepEqual(categories.map((c) => c.key), ["base"]);
});

// ─── groupCatalog: ascending width sort ───────────────────────────────────────

test("groupCatalog: rows sort ascending by width within a category, so adjacent diagonals sit together", () => {
    const products = [
        product({ id: 1, name: "[CD36] Diagonal Corner 36", cabinetType: "corner", sku: "CD36", width: 36, price: 700 }),
        product({ id: 2, name: "[CB24] Corner Base 24", cabinetType: "corner", sku: "CB24", width: 24, price: 500 }),
        product({ id: 3, name: "[CD33] Diagonal Corner 33", cabinetType: "corner", sku: "CD33", width: 33, price: 650 }),
        product({ id: 4, name: "[CB30] Corner Base 30", cabinetType: "corner", sku: "CB30", width: 30, price: 550 }),
    ];
    const { categories } = groupCatalog(products);
    const rows = categories[0].rows;
    assert.deepEqual(rows.map((r) => r.width_in), [24, 30, 33, 36]);
    const idx33 = rows.findIndex((r) => r.width_in === 33);
    const idx36 = rows.findIndex((r) => r.width_in === 36);
    assert.equal(idx36 - idx33, 1, "the 33in and 36in diagonals must be adjacent rows");
});

test("groupCatalog: ties on width break by title, then by price", () => {
    const products = [
        product({ id: 1, name: "[X2] Zeta Cab", cabinetType: "base", sku: "X2", width: 24, price: 100 }),
        product({ id: 2, name: "[X1] Alpha Cab", cabinetType: "base", sku: "X1", width: 24, price: 200 }),
        product({ id: 3, name: "[X3] Alpha Cab", cabinetType: "base", sku: "X3", width: 24, price: 50 }),
    ];
    const { categories } = groupCatalog(products);
    const titles = categories[0].rows.map((r) => `${r.title}:${r.price}`);
    assert.deepEqual(titles, ["Alpha Cab:50", "Alpha Cab:200", "Zeta Cab:100"]);
});

// ─── groupCatalog: L/R merge ───────────────────────────────────────────────────

test("groupCatalog: an L + R pair sharing (title, width, price) collapses into one 'pair' row carrying both products", () => {
    const products = [
        product({ id: 101, name: "[CB36-L] Corner Base 36 (Left Hand)", cabinetType: "corner", sku: "CB36-L", width: 36, price: 650 }),
        product({ id: 102, name: "[CB36-R] Corner Base 36 (Right Hand)", cabinetType: "corner", sku: "CB36-R", width: 36, price: 650 }),
    ];
    const { categories } = groupCatalog(products);
    assert.equal(categories[0].rows.length, 1);
    const row = categories[0].rows[0];
    assert.equal(row.kind, "pair");
    assert.equal(row.title, "Corner Base 36");
    assert.equal(row.width_in, 36);
    assert.equal(row.price, 650);
    assert.equal(row.left.product_id, 101);
    assert.equal(row.right.product_id, 102);
});

test("groupCatalog: a lone RH product with no matching LH counterpart stays a single row", () => {
    const products = [
        product({ id: 201, name: "[CB30-R] Corner Base 30 (Right Hand)", cabinetType: "corner", sku: "CB30-R", width: 30, price: 600 }),
    ];
    const { categories } = groupCatalog(products);
    assert.equal(categories[0].rows.length, 1);
    assert.equal(categories[0].rows[0].kind, "single");
    assert.equal(categories[0].rows[0].product.product_id, 201);
});

test("groupCatalog: two RH products sharing the same (title, width, price) key with no LH counterpart both stay single (no accidental self-pairing)", () => {
    const products = [
        product({ id: 301, name: "[CB30-RA] Corner Base 30 (Right Hand)", cabinetType: "corner", sku: "CB30-RA", width: 30, price: 600 }),
        product({ id: 302, name: "[CB30-RB] Corner Base 30 (Right Hand)", cabinetType: "corner", sku: "CB30-RB", width: 30, price: 600 }),
    ];
    const { categories } = groupCatalog(products);
    assert.equal(categories[0].rows.length, 2, "no L present, so this must not collapse to one row");
    assert.equal(categories[0].rows.every((r) => r.kind === "single"), true);
});

test("groupCatalog: an unhanded product never merges with anything, even if a same-key handed pair exists", () => {
    const products = [
        product({ id: 401, name: "[CB36-L] Corner Base 36 (Left Hand)", cabinetType: "corner", sku: "CB36-L", width: 36, price: 650 }),
        product({ id: 402, name: "[CB36-R] Corner Base 36 (Right Hand)", cabinetType: "corner", sku: "CB36-R", width: 36, price: 650 }),
        product({ id: 403, name: "[TALL1] Tall Pantry", cabinetType: "tall", sku: "TALL1", width: 24, price: 900 }),
    ];
    const { categories } = groupCatalog(products);
    const corner = categories.find((c) => c.key === "corner");
    const tall = categories.find((c) => c.key === "tall");
    assert.equal(corner.rows.length, 1);
    assert.equal(corner.rows[0].kind, "pair");
    assert.equal(tall.rows.length, 1);
    assert.equal(tall.rows[0].kind, "single");
});

// ─── groupCatalog: dataFlags ───────────────────────────────────────────────────

test("groupCatalog: a product with no SKU is flagged missing_sku (never hidden)", () => {
    const products = [product({ id: 1, name: "[?] Mystery Base", cabinetType: "base", sku: "", width: 24 })];
    const { categories } = groupCatalog(products);
    assert.deepEqual(categories[0].rows[0].dataFlags, ["missing_sku"]);
});

test("groupCatalog: a product whose raw width_in is empty is flagged missing_width even though the width was recovered for display", () => {
    const products = [
        product({ id: 1, name: '[WD9] Wall Door 9"', cabinetType: "wall", sku: "WD9", width: 0 }),
    ];
    const { categories } = groupCatalog(products);
    const row = categories[0].rows[0];
    assert.equal(row.width_in, 9, "display width is still recovered from the name");
    assert.deepEqual(row.dataFlags, ["missing_width"], "but the raw-data gap is still flagged");
});

test("groupCatalog: both flags can be present together", () => {
    const products = [product({ id: 1, name: "[?] Mystery Base", cabinetType: "base", sku: "", width: 0 })];
    const { categories } = groupCatalog(products);
    assert.deepEqual(categories[0].rows[0].dataFlags, ["missing_sku", "missing_width"]);
});

test("groupCatalog: a fully-clean product carries an empty dataFlags array (never omitted, never hidden)", () => {
    const products = [product({ id: 1, name: "[B24] Base Cabinet", cabinetType: "base", sku: "B24", width: 24 })];
    const { categories } = groupCatalog(products);
    assert.deepEqual(categories[0].rows[0].dataFlags, []);
});

test("groupCatalog: a pair row's dataFlags is the union of both sides' flags", () => {
    const products = [
        product({ id: 1, name: "[CB36-L] Corner Base 36 (Left Hand)", cabinetType: "corner", sku: "", width: 36, price: 650 }),
        product({ id: 2, name: "[CB36-R] Corner Base 36 (Right Hand)", cabinetType: "corner", sku: "CB36-R", width: 36, price: 650 }),
    ];
    const { categories } = groupCatalog(products);
    const row = categories[0].rows[0];
    assert.equal(row.kind, "pair");
    assert.deepEqual(row.dataFlags, ["missing_sku"]);
});

// ─── groupCatalog: derived widths list ────────────────────────────────────────

test("groupCatalog: widths is the sorted, deduplicated, DERIVED set of lifted widths (not hardcoded)", () => {
    // Shuffled input order, duplicate widths across categories, and one
    // width only recoverable via name-fallback (the 9in wall door) —
    // if this were a hardcoded literal, permuting/duplicating the
    // input like this couldn't possibly still produce the right set.
    const products = [
        product({ id: 1, name: "[A] Cab A", cabinetType: "base", sku: "A", width: 24 }),
        product({ id: 2, name: "[B] Cab B", cabinetType: "wall", sku: "B", width: 45 }),
        product({ id: 3, name: '[C] Wall Door 9"', cabinetType: "wall", sku: "C", width: 0 }),
        product({ id: 4, name: "[D] Cab D", cabinetType: "base", sku: "D", width: 12 }),
        product({ id: 5, name: "[E] Cab E", cabinetType: "corner", sku: "E", width: 36 }),
        product({ id: 6, name: "[F] Cab F", cabinetType: "corner", sku: "F", width: 33 }),
        product({ id: 7, name: "[G] Cab G", cabinetType: "tall", sku: "G", width: 24 }), // duplicate of id 1
        product({ id: 8, name: "[H] Cab H", cabinetType: "base", sku: "H", width: 18 }),
        product({ id: 9, name: "[I] Cab I", cabinetType: "wall", sku: "I", width: 15 }),
        product({ id: 10, name: "[J] Cab J", cabinetType: "base", sku: "J", width: 27 }),
        product({ id: 11, name: "[K] Cab K", cabinetType: "panel", sku: "K", width: 30 }),
        product({ id: 12, name: "[L] Cab L", cabinetType: "filler", sku: "L", width: 3 }),
    ];
    const { widths } = groupCatalog(products);
    assert.deepEqual(widths, [3, 9, 12, 15, 18, 24, 27, 30, 33, 36, 45]);
});

test("groupCatalog: widths excludes 0 (no width recoverable is a flag, not a filter option)", () => {
    const products = [product({ id: 1, name: "[?] Mystery", cabinetType: "base", sku: "", width: 0 })];
    const { widths } = groupCatalog(products);
    assert.deepEqual(widths, []);
});

// ─── groupCatalog: total ───────────────────────────────────────────────────────

test("groupCatalog: total is the raw product count, unaffected by L/R merging", () => {
    const products = [
        product({ id: 1, name: "[CB36-L] Corner Base 36 (Left Hand)", cabinetType: "corner", sku: "CB36-L", width: 36, price: 650 }),
        product({ id: 2, name: "[CB36-R] Corner Base 36 (Right Hand)", cabinetType: "corner", sku: "CB36-R", width: 36, price: 650 }),
        product({ id: 3, name: "[B24] Base Cabinet", cabinetType: "base", sku: "B24", width: 24 }),
    ];
    const { total, categories } = groupCatalog(products);
    assert.equal(total, 3);
    const totalRows = categories.reduce((sum, c) => sum + c.rows.length, 0);
    assert.equal(totalRows, 2, "3 raw products, 1 pair merge -> 2 rows");
});

test("groupCatalog: empty/missing input never throws", () => {
    assert.deepEqual(groupCatalog([]), { categories: [], widths: [], total: 0 });
    assert.deepEqual(groupCatalog(undefined), { categories: [], widths: [], total: 0 });
});

// ─── groupCatalog: the cited 48-product / 41-row / corner-9-to-6 shape ────────
//
// Pinned per the reviewed mockup spec: "On the current 48-product
// catalogue this collapses to 41 rows and takes corner from 9 rows
// to 6." This is a SYNTHETIC fixture built to the same structure
// (never read from the DB) — 48 raw products across the 5 known
// categories, with enough L/R pairs (7 total, 3 of them in corner)
// that the arithmetic (48 - 7 merges = 41 rows; corner's 9 - 3 = 6)
// lands exactly on the cited numbers.

function buildPairedCategory(cabinetType, pairCount, singleCount, seq) {
    const products = [];
    let id = seq.next;
    for (let i = 0; i < pairCount; i++) {
        const title = `${cabinetType} Pair ${i}`;
        const width = 12 + i;
        const price = 300 + i * 10;
        const base = `${cabinetType.toUpperCase()}-PR${i}`;
        products.push(product({
            id: id++, name: `[${base}-L] ${title} (Left Hand)`,
            cabinetType, sku: `${base}-L`, width, price,
        }));
        products.push(product({
            id: id++, name: `[${base}-R] ${title} (Right Hand)`,
            cabinetType, sku: `${base}-R`, width, price,
        }));
    }
    for (let i = 0; i < singleCount; i++) {
        const base = `${cabinetType.toUpperCase()}-SG${i}`;
        products.push(product({
            id: id++, name: `[${base}] ${cabinetType} Single ${i}`,
            cabinetType, sku: base, width: 40 + i, price: 400 + i * 10,
        }));
    }
    seq.next = id;
    return products;
}

test("groupCatalog: pins the 48-product / 41-row / corner-9-to-6 shape from the reviewed mockup", () => {
    const seq = { next: 1 };
    const fixture = [
        ...buildPairedCategory("base", 2, 10, seq),    // 14 raw -> 12 rows
        ...buildPairedCategory("wall", 1, 10, seq),    // 12 raw -> 11 rows
        ...buildPairedCategory("tall", 1, 6, seq),     //  8 raw ->  7 rows
        ...buildPairedCategory("corner", 3, 3, seq),   //  9 raw ->  6 rows
        product({ id: seq.next++, name: "[FIL1] Filler 1", cabinetType: "filler", sku: "FIL1", width: 3 }),
        product({ id: seq.next++, name: "[FIL2] Filler 2", cabinetType: "filler", sku: "FIL2", width: 6 }),
        product({ id: seq.next++, name: "[FIL3] Filler 3", cabinetType: "filler", sku: "FIL3", width: 9 }),
        product({ id: seq.next++, name: "[PNL1] End Panel 1", cabinetType: "panel", sku: "PNL1", width: 24 }),
        product({ id: seq.next++, name: "[PNL2] End Panel 2", cabinetType: "panel", sku: "PNL2", width: 30 }),
        // panels: 5 raw -> 5 rows (no handing in this category)
    ];

    assert.equal(fixture.length, 48, "fixture must be the same size as the real catalogue");

    const { categories, total } = groupCatalog(fixture);
    assert.equal(total, 48);

    const rowCountByKey = Object.fromEntries(categories.map((c) => [c.key, c.rows.length]));
    assert.equal(rowCountByKey.base, 12);
    assert.equal(rowCountByKey.wall, 11);
    assert.equal(rowCountByKey.tall, 7);
    assert.equal(rowCountByKey.corner, 6);
    assert.equal(rowCountByKey.panels, 5);

    const totalRows = categories.reduce((sum, c) => sum + c.rows.length, 0);
    assert.equal(totalRows, 41, "48-product catalogue collapses to 41 rows");

    const corner = categories.find((c) => c.key === "corner");
    assert.equal(corner.count, 6, "corner goes from 9 raw products to 6 rows");
    assert.equal(corner.rows.filter((r) => r.kind === "pair").length, 3);
    assert.equal(corner.rows.filter((r) => r.kind === "single").length, 3);
});
