/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — catalog list grouping.
 *
 * Panel P2 (catalog redesign) · pure data layer.
 *
 * Pure functions only — no OWL, no DOM, no THREE. Consumed by the
 * (separately owned) catalog-panel component to turn the flat
 * `/southbrook_kitchen/configurator/products` payload into the
 * grouped, L/R-merged, sorted rows the redesigned inventory list
 * renders. Every product in that payload has at least
 * `{ product_id, name, sku, cabinet_type, width_in, price, material }`
 * (controllers/main.py `_product_payload`) — `name` is
 * `product.display_name`, i.e. Odoo's own
 * `[SKU] Human Name (attr, attr…)` convention for a variant with a
 * default_code.
 *
 * Today's inventory list sorts alphabetically by SKU/name and lists
 * every product.product row on its own line. Two problems that shows
 * up on the real 48-cabinet catalogue:
 *   1. A run of same-family cabinets that only differ by handing
 *      (RH/LH — mirror-image corner and blind units) shows as two
 *      near-identical rows, doubling the list for no information the
 *      rep needs at a glance (handing is chosen after picking the
 *      cabinet, not before).
 *   2. Products with no `default_code` sort last under an
 *      alphabetical-by-SKU order, and width-adjacent variants (e.g.
 *      the 33″ and 36″ diagonal corners) land far apart because SKUs
 *      don't sort by width.
 *
 * `groupCatalog` fixes both: same-family L/R pairs collapse into one
 * row carrying both product records, and rows sort by width first so
 * the size progression reads top-to-bottom.
 */

// ─── Name parsing ─────────────────────────────────────────────────────────────

const BRACKET_PREFIX_RE = /^\[[^\]]*\]\s*/;
const TRAILING_PAREN_RE = /^([^(]+)\((.*)\)\s*$/;

/**
 * Split an Odoo variant display name into its human title and its
 * trailing attribute parenthetical.
 *
 * Names arrive as `[SKU] Human Name (attr, attr…)`. The leading
 * bracketed SKU segment is stripped unconditionally (`^\[[^\]]*\]\s*`);
 * what remains is split on a trailing `(...)` group
 * (`^([^(]+)\((.*)\)\s*$`). `attrs` exists ONLY for search matching
 * and detail panels — it is never part of what the list row displays
 * as its title.
 *
 * @param {string} displayName - e.g. `"[CB36-R] Corner Base 36 (Right Hand, White Melamine)"`.
 * @returns {{title: string, attrs: string}} `attrs` is `""` when the
 *   name carries no trailing parenthetical.
 */
export function parseProductName(displayName) {
    const source = displayName == null ? "" : String(displayName);
    const stripped = source.replace(BRACKET_PREFIX_RE, "");
    const match = stripped.match(TRAILING_PAREN_RE);
    if (match) {
        return { title: match[1].trim(), attrs: match[2].trim() };
    }
    return { title: stripped.trim(), attrs: "" };
}

// ─── Handing (L/R) ─────────────────────────────────────────────────────────────

// Word-boundary, case-insensitive. Longer phrases first in the
// alternation buys nothing for \b-delimited alternatives (each branch
// is matched independently), but the four literal tokens the spec
// calls out are kept exactly as named for grep-ability.
const HANDING_TOKEN_RE = /\b(RH|Right Hand|LH|Left Hand)\b/i;
const TRAILING_DASH_R_RE = /-R$/i;

/**
 * Determine a product's handing, "L" or "R", or `null` when the
 * product isn't handed at all.
 *
 * Checked in order: an `RH`/`Right Hand`/`LH`/`Left Hand` token
 * (word-boundary, case-insensitive) in `attrs` or the product's raw
 * `name`; failing that, a trailing `-R` on the SKU (the only fallback
 * — there is no bare `-L` convention in the catalogue).
 *
 * @param {{name?: string, sku?: string}} product
 * @param {string} attrs - the parenthetical from {@link parseProductName}.
 * @returns {"L"|"R"|null}
 */
export function liftHanding(product, attrs) {
    const name = (product && product.name) || "";
    const haystack = `${attrs || ""} ${name}`;
    const match = haystack.match(HANDING_TOKEN_RE);
    if (match) {
        const token = match[1].toLowerCase();
        return token === "rh" || token === "right hand" ? "R" : "L";
    }
    const sku = (product && product.sku) || "";
    if (TRAILING_DASH_R_RE.test(sku)) {
        return "R";
    }
    return null;
}

// ─── Width recovery ─────────────────────────────────────────────────────────────

const WIDTH_TOKEN_RE = /(\d+)\s*(?:in\b|")/i;

/**
 * Resolve a product's display width in inches.
 *
 * Prefers the product's own `width_in` field. A handful of
 * SKU-less catalogue rows (the 9″ base and 9″ wall doors) ship with
 * that field empty — for those, recover the width from a
 * `(\d+)\s*(?:in\b|")` match against `attrs` first, then the parsed
 * title (via {@link parseProductName} on the product's raw `name`).
 *
 * @param {{name?: string, width_in?: number}} product
 * @param {string} attrs - the parenthetical from {@link parseProductName}.
 * @returns {number} the width in inches, or `0` when none could be
 *   recovered from any source.
 */
export function liftWidth(product, attrs) {
    const direct = Number(product && product.width_in) || 0;
    if (direct > 0) {
        return direct;
    }
    if (attrs) {
        const fromAttrs = WIDTH_TOKEN_RE.exec(attrs);
        if (fromAttrs) {
            return Number(fromAttrs[1]);
        }
    }
    const { title } = parseProductName((product && product.name) || "");
    if (title) {
        const fromTitle = WIDTH_TOKEN_RE.exec(title);
        if (fromTitle) {
            return Number(fromTitle[1]);
        }
    }
    return 0;
}

// ─── Search ─────────────────────────────────────────────────────────────────────

/**
 * Build the lowercased haystack a catalog search box should match a
 * product against.
 *
 * The list row never displays `attrs` (see {@link parseProductName}),
 * but a rep searching "Right Hand" or "maple" must still find the
 * product it's on — so the search haystack covers title + attrs + sku
 * + material, independent of what the row renders.
 *
 * @param {{sku?: string, material?: string}} product
 * @param {{title: string, attrs: string}} parsed - the result of
 *   {@link parseProductName} for this product.
 * @returns {string} lowercased, space-joined search haystack.
 */
export function searchText(product, parsed) {
    const p = product || {};
    const title = (parsed && parsed.title) || "";
    const attrs = (parsed && parsed.attrs) || "";
    return [title, attrs, p.sku || "", p.material || ""]
        .join(" ")
        .toLowerCase();
}

// ─── Grouping ─────────────────────────────────────────────────────────────────

// Fixed category order + labels. `filler` and `panel` cabinet_type
// values both fold into the single "panels" key/tab — the inventory
// list has never distinguished them (D13's INVENTORY_CATEGORY_MAP in
// kitchen_configurator.js already merges them the same way for the
// tab filter), so the redesigned list keeps that merge.
const CATEGORY_ORDER = [
    { key: "base", label: "Base Cabinets" },
    { key: "corner", label: "Corner Cabinets" },
    { key: "wall", label: "Wall Cabinets" },
    { key: "tall", label: "Tall Cabinets" },
    { key: "panels", label: "Panels & Fillers" },
];
const KNOWN_CATEGORY_KEYS = new Set(CATEGORY_ORDER.map((c) => c.key));

function categoryKeyFor(cabinetType) {
    if (cabinetType === "filler" || cabinetType === "panel") {
        return "panels";
    }
    return cabinetType || "other";
}

function labelForUnknownCategory(key) {
    return key.charAt(0).toUpperCase() + key.slice(1);
}

function dataFlagsFor(product, rawWidth) {
    const flags = [];
    if (!(product && product.sku)) {
        flags.push("missing_sku");
    }
    if (!(rawWidth > 0)) {
        flags.push("missing_width");
    }
    return flags;
}

function mergeDataFlags(a, b) {
    const union = new Set([...(a || []), ...(b || [])]);
    const out = [];
    if (union.has("missing_sku")) out.push("missing_sku");
    if (union.has("missing_width")) out.push("missing_width");
    return out;
}

function singleRow(entry) {
    return {
        kind: "single",
        product: entry.product,
        title: entry.title,
        width_in: entry.width,
        price: entry.price,
        sku: entry.sku,
        dataFlags: entry.dataFlags,
    };
}

function pairRow(left, right) {
    return {
        kind: "pair",
        title: left.title,
        width_in: left.width,
        price: left.price,
        left: left.product,
        right: right.product,
        sku: `${left.sku || "?"} / ${right.sku || "?"}`,
        dataFlags: mergeDataFlags(left.dataFlags, right.dataFlags),
    };
}

function rowSort(a, b) {
    if (a.width_in !== b.width_in) {
        return a.width_in - b.width_in;
    }
    if (a.title !== b.title) {
        return a.title < b.title ? -1 : 1;
    }
    return a.price - b.price;
}

/**
 * @typedef {object} CatalogSingleRow
 * @property {"single"} kind
 * @property {object} product - the full product record.
 * @property {string} title
 * @property {number} width_in - lifted width ({@link liftWidth}).
 * @property {number} price
 * @property {string} sku
 * @property {Array<"missing_sku"|"missing_width">} dataFlags
 */
/**
 * @typedef {object} CatalogPairRow
 * @property {"pair"} kind
 * @property {string} title
 * @property {number} width_in - lifted width ({@link liftWidth}).
 * @property {number} price
 * @property {object} left - the "L"-handed product record.
 * @property {object} right - the "R"-handed product record.
 * @property {string} sku - `"<left sku> / <right sku>"`.
 * @property {Array<"missing_sku"|"missing_width">} dataFlags - union
 *   of both products' flags.
 */
/**
 * @typedef {object} CatalogCategory
 * @property {string} key
 * @property {string} label
 * @property {number} count - `rows.length` (post L/R merge).
 * @property {Array<CatalogSingleRow|CatalogPairRow>} rows
 */

/**
 * Group a flat product list into the catalog list's display shape.
 *
 * - Buckets by `cabinet_type` into fixed category order
 *   `base, corner, wall, tall, panels` (`filler` and `panel` both
 *   map onto `panels`); any other `cabinet_type` value gets its own
 *   category, appended after the five, in alphabetical order by key.
 *   Categories with no rows are omitted.
 * - Within a category, products sharing the same (category, parsed
 *   title, lifted width, price) are candidates for an L/R merge: when
 *   exactly one member is "L"-handed and one is "R"-handed
 *   ({@link liftHanding}), they collapse into a single `"pair"` row
 *   carrying both product records — selection still resolves to the
 *   correct `product_id` because both full records ride along.
 *   Anything else (a lone handed product, an unhanded product, or a
 *   same-key group that isn't a clean L+R pair) emits its own
 *   `"single"` row.
 * - Rows within a category sort ascending by `width_in`, then
 *   `title`, then `price`.
 *
 * @param {Array<object>} products - the flat catalog, e.g.
 *   `/southbrook_kitchen/configurator/products`'s `products` array.
 * @returns {{categories: CatalogCategory[], widths: number[], total: number}}
 *   `widths` is the sorted, deduplicated list of every positive
 *   lifted width present in `products` (derived, not hardcoded).
 *   `total` is `products.length` — the raw catalog size, unaffected
 *   by L/R merging.
 */
export function groupCatalog(products) {
    const list = Array.isArray(products) ? products : [];

    const entries = list.map((product) => {
        const { title, attrs } = parseProductName(product && product.name);
        const width = liftWidth(product, attrs);
        const handing = liftHanding(product, attrs);
        const rawWidth = Number(product && product.width_in) || 0;
        return {
            product,
            title,
            width,
            handing,
            price: Number(product && product.price) || 0,
            sku: (product && product.sku) || "",
            dataFlags: dataFlagsFor(product, rawWidth),
            categoryKey: categoryKeyFor(product && product.cabinet_type),
        };
    });

    // Bucket by the L/R merge composite key: (category, title, width, price).
    const mergeBuckets = new Map();
    for (const entry of entries) {
        const key = [entry.categoryKey, entry.title, entry.width, entry.price].join("");
        if (!mergeBuckets.has(key)) {
            mergeBuckets.set(key, []);
        }
        mergeBuckets.get(key).push(entry);
    }

    const rowsByCategory = new Map();
    const pushRow = (categoryKey, row) => {
        if (!rowsByCategory.has(categoryKey)) {
            rowsByCategory.set(categoryKey, []);
        }
        rowsByCategory.get(categoryKey).push(row);
    };

    for (const bucket of mergeBuckets.values()) {
        const leftIdx = bucket.findIndex((e) => e.handing === "L");
        const rightIdx = bucket.findIndex((e) => e.handing === "R");
        if (leftIdx !== -1 && rightIdx !== -1 && leftIdx !== rightIdx) {
            const left = bucket[leftIdx];
            const right = bucket[rightIdx];
            pushRow(left.categoryKey, pairRow(left, right));
            bucket.forEach((entry, idx) => {
                if (idx === leftIdx || idx === rightIdx) {
                    return;
                }
                pushRow(entry.categoryKey, singleRow(entry));
            });
        } else {
            for (const entry of bucket) {
                pushRow(entry.categoryKey, singleRow(entry));
            }
        }
    }

    const categories = [];
    for (const { key, label } of CATEGORY_ORDER) {
        const rows = rowsByCategory.get(key);
        if (!rows || !rows.length) {
            continue;
        }
        rows.sort(rowSort);
        categories.push({ key, label, count: rows.length, rows });
    }
    const extraKeys = [...rowsByCategory.keys()]
        .filter((key) => !KNOWN_CATEGORY_KEYS.has(key))
        .sort();
    for (const key of extraKeys) {
        const rows = rowsByCategory.get(key);
        rows.sort(rowSort);
        categories.push({ key, label: labelForUnknownCategory(key), count: rows.length, rows });
    }

    const widths = [...new Set(entries.map((e) => e.width).filter((w) => w > 0))]
        .sort((a, b) => a - b);

    return { categories, widths, total: list.length };
}
