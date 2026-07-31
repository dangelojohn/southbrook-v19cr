/**
 * Task 7 (SBK-033, corner-remediation 2026-07-30) — ONE server-computed
 * counter summary, client side.
 *
 * V4-counters-templates.md claim 1 (CONFIRMED): four different places in
 * this codebase each computed their OWN count of "how many cabinets" for
 * one design — the sidebar's base/wall-only tally, the header's
 * _nonFillerCount() (now deleted), and the server's base_count/
 * wall_count/total_cabinets fields — and none of them agreed once a
 * design had a corner cabinet ("header 9, sidebar 4+2, record 13,
 * summary 10+2").
 *
 * This file pins the fix's client-side contract:
 *   1. countCabinetBuckets(items) is the ONE partition function (mirrors
 *      southbrook.kitchen.design._get_counter_summary() field-for-field).
 *   2. IDENTITY: the seven cabinet_type buckets sum to items.length.
 *   3. unplaced_count is a diagnostic OVERLAY, not an eighth additive
 *      bucket — an is_unresolved item is already inside its own
 *      cabinet_type bucket.
 *   4. Sidebar buckets == server summary: a hydrated session's local
 *      recompute reproduces EXACTLY the numbers the (mocked) server
 *      sent, for a U-shape-shaped fixture where base/wall/corner/
 *      appliance are all non-zero.
 *   5. header == sidebar-total, in the REAL RENDERED DOM (not just the
 *      underlying state object) — the header strip and the sidebar
 *      metric tiles read from the same state.summary, so they can never
 *      disagree again.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONFIGURATOR_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "kitchen_configurator.js",
);

// Owl's ESM build reads DOM globals (Element, Node, ...) at module-
// evaluation time — a bare top-level import of kitchen_configurator.js
// (which transitively imports "@odoo/owl") would throw before any test
// runs setupDom(). Unlike 17_corner_identity.test.mjs's direct
// setupDom()+import() pattern (safe for THAT file, which never also
// mounts the full parent component), this file's later tests DO mount
// the real component — and an independent setupDom()+import() done
// BEFORE the first real owl.mount() leaves Owl/the component's compiled
// template bound to a jsdom realm that is never actually used for
// mounting, which breaks every subsequent mount in this process (proven
// empirically: state.screen correctly reads "workspace" but the
// rendered DOM stays stuck on the landing-screen markup). Routing every
// load through mountConfigurator() FIRST — even a throwaway landing-
// screen mount — avoids that: it's the same jsdom realm every real
// mount in this file already uses, and the dynamic import() afterward
// is a plain cache hit (kitchen_configurator.js only evaluates once).
async function loadCountCabinetBuckets() {
    await mountConfigurator({});
    const { countCabinetBuckets } = await import(CONFIGURATOR_SRC);
    return countCabinetBuckets;
}

const _BUCKET_KEYS = [
    "base_count", "wall_count", "tall_count", "corner_count",
    "filler_count", "panel_count", "appliance_count",
];

function identitySum(counts) {
    return _BUCKET_KEYS.reduce((sum, k) => sum + (counts[k] || 0), 0);
}

// ─── Unit tests: countCabinetBuckets in isolation ───────────────────────────

test("countCabinetBuckets: every cabinet_type buckets exactly once, sum == items.length", async () => {
    const countCabinetBuckets = await loadCountCabinetBuckets();
    const items = [
        { cabinet_type: "base" }, { cabinet_type: "base" },
        { cabinet_type: "wall" },
        { cabinet_type: "tall" },
        { cabinet_type: "corner" }, { cabinet_type: "corner" },
        { cabinet_type: "filler" },
        { cabinet_type: "panel" },
        { cabinet_type: "appliance" },
    ];
    const counts = countCabinetBuckets(items);
    assert.equal(counts.base_count, 2);
    assert.equal(counts.wall_count, 1);
    assert.equal(counts.tall_count, 1);
    assert.equal(counts.corner_count, 2);
    assert.equal(counts.filler_count, 1);
    assert.equal(counts.panel_count, 1);
    assert.equal(counts.appliance_count, 1);
    assert.equal(counts.unplaced_count, 0);
    assert.equal(counts.total, items.length,
        "total must equal the active item count (all buckets, incl. fillers)");
    assert.equal(identitySum(counts), items.length,
        "sum of the seven typed buckets must equal items.length");
});

test("countCabinetBuckets: an is_unresolved item is an OVERLAY, not a double count", async () => {
    const countCabinetBuckets = await loadCountCabinetBuckets();
    const items = [
        { cabinet_type: "base" },
        { cabinet_type: "base", is_unresolved: true },
        { cabinet_type: "wall" },
    ];
    const counts = countCabinetBuckets(items);
    // The unresolved item is still a "base" — counted there, same as the
    // resolved one (matches the server's base_count contract, which has
    // never carved unresolved lines out of their own type bucket).
    assert.equal(counts.base_count, 2);
    assert.equal(counts.wall_count, 1);
    assert.equal(counts.unplaced_count, 1);
    // IDENTITY holds: the bucket sum is items.length, NOT items.length+1 —
    // unplaced_count must not be added a second time on top of base_count.
    assert.equal(identitySum(counts), items.length);
    assert.equal(counts.total, items.length);
});

test("countCabinetBuckets: unknown/missing cabinet_type is silently excluded from every bucket (no throw)", async () => {
    const countCabinetBuckets = await loadCountCabinetBuckets();
    const counts = countCabinetBuckets([
        { cabinet_type: "base" },
        { cabinet_type: "not_a_real_type" },
        {},
        null,
    ]);
    assert.equal(counts.base_count, 1);
    assert.equal(identitySum(counts), 1,
        "only the recognised base item contributes to the identity sum");
});

test("countCabinetBuckets: empty/undefined input never throws, returns an all-zero shape", async () => {
    const countCabinetBuckets = await loadCountCabinetBuckets();
    assert.equal(identitySum(countCabinetBuckets([])), 0);
    assert.equal(identitySum(countCabinetBuckets(undefined)), 0);
    assert.equal(countCabinetBuckets([]).total, 0);
});

// ─── Mounted-component tests: sidebar buckets == server summary ────────────

const U_SHAPE_ITEMS = [
    { layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, wall: "back" },
    { layout_key: "base-2", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
      x_position_in: 24, y_position_in: 0, z_position_in: 0, wall: "back" },
    { layout_key: "wall-1", cabinet_type: "wall", product_name: "Wall 24",
      width_in: 24, height_in: 30, depth_in: 12, price: 120,
      x_position_in: 0, y_position_in: 54, z_position_in: 0, wall: "back" },
    { layout_key: "corner-back-left", cabinet_type: "corner", product_name: "Corner base",
      width_in: 36, height_in: 34.5, depth_in: 24, price: 900,
      x_position_in: 48, y_position_in: 0, z_position_in: 0, wall: "back" },
    { layout_key: "corner-back-right", cabinet_type: "corner", product_name: "Wall corner",
      width_in: 36, height_in: 30, depth_in: 12, price: 900,
      x_position_in: 84, y_position_in: 54, z_position_in: 0, wall: "back" },
    { layout_key: "tall-1", cabinet_type: "tall", product_name: "Tall pantry",
      width_in: 24, height_in: 84, depth_in: 24, price: 380,
      x_position_in: 120, y_position_in: 0, z_position_in: 0, wall: "right" },
    // Right-cap of base-2 (x=24, width=24 -> right edge 48): satisfies
    // _validateEndCapPlacement's host-attachment rule so this fixture
    // doesn't also trip an unrelated end-cap-placement warning.
    { layout_key: "panel-1", cabinet_type: "panel", product_name: "End panel",
      width_in: 0.75, height_in: 34.5, depth_in: 24, price: 50,
      x_position_in: 48, y_position_in: 0, z_position_in: 0, wall: "back" },
    { layout_key: "appliance-1", cabinet_type: "appliance", product_name: "Range space",
      width_in: 30, height_in: 34.5, depth_in: 24, price: 0,
      x_position_in: 60, y_position_in: 0, z_position_in: 96, wall: "right",
      appliance_type: "range" },
    { layout_key: "filler-1", cabinet_type: "filler", product_name: "Filler 3",
      width_in: 3, height_in: 34.5, depth_in: 0.75, price: 35,
      x_position_in: 144, y_position_in: 0, z_position_in: 0, wall: "back" },
    { layout_key: "ghost-1", cabinet_type: "base", product_name: "Unresolved slot",
      width_in: 24, height_in: 34.5, depth_in: 24, price: 0,
      x_position_in: 0, y_position_in: 0, z_position_in: 96, wall: "left",
      is_unresolved: true },
];

// The server's own summary for the SAME 10 lines — hand-derived, not
// computed via countCabinetBuckets, so the parity assertion below is
// meaningful (two independent tallies of the same fixture must agree).
const SERVER_SUMMARY = {
    base_count: 3, wall_count: 1, tall_count: 1, corner_count: 2,
    filler_count: 1, panel_count: 1, appliance_count: 1, unplaced_count: 1,
    total: 10, price: 2710, cabinet_price: 2710, filler_price: 35,
};

function mountUShapeHydrated() {
    return mountConfigurator({
        actionParams: { design_id: 102 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 120, depth_in: 96, height_in: 96 },
                design_name: "T7 U-shape counter fixture",
                lines: U_SHAPE_ITEMS,
                summary: SERVER_SUMMARY,
            }),
        },
    });
}

test("sidebar buckets == server summary: local recompute reproduces the server's own tally exactly", async () => {
    const h = await mountUShapeHydrated();
    const s = h.component.state.summary;
    for (const key of Object.keys(SERVER_SUMMARY)) {
        if (key === "price" || key === "cabinet_price" || key === "filler_price") continue;
        assert.equal(s[key], SERVER_SUMMARY[key],
            `state.summary.${key} (${s[key]}) must match the server's `
            + `independently-derived ${SERVER_SUMMARY[key]}`);
    }
});

test("identity: sum(parts) == len(active items) for a U-shape-shaped fixture", async () => {
    const h = await mountUShapeHydrated();
    const s = h.component.state.summary;
    assert.equal(identitySum(s), h.component.state.items.length);
    assert.equal(s.total, h.component.state.items.length);
    // unplaced_count is an overlay — must not inflate the identity sum.
    assert.equal(s.unplaced_count, 1);
    assert.equal(identitySum(s), 10, "not 11 — the ghost item is not a second bucket");
});

test("U-shape-shaped fixture: base/wall/corner/appliance are ALL non-zero (V4 claim 1's exact gap)", async () => {
    const h = await mountUShapeHydrated();
    const s = h.component.state.summary;
    assert.ok(s.base_count > 0, "base_count must be > 0");
    assert.ok(s.wall_count > 0, "wall_count must be > 0");
    assert.ok(s.corner_count > 0, "corner_count must be > 0 — the audited gap");
    assert.ok(s.appliance_count > 0, "appliance_count must be > 0");
});

test("header == sidebar-total in the rendered DOM", async () => {
    const h = await mountUShapeHydrated();
    await tick();

    const headerItem = [...h.root.querySelectorAll(".o_sbk_count_item")][0];
    const headerText = headerItem.textContent.replace(/\s+/g, " ").trim();
    assert.match(headerText, /^10\s+cabinets$/,
        "header must read the same total the sidebar sums to");

    const metrics = [...h.root.querySelectorAll(".o_sbk_metric")];
    const metricValue = (labelSubstring) => {
        const el = metrics.find((n) => n.textContent.includes(labelSubstring));
        assert.ok(el, `no .o_sbk_metric tile found for "${labelSubstring}"`);
        return Number(el.querySelector("strong").textContent.trim());
    };
    assert.equal(metricValue("base cabinets"), 3);
    assert.equal(metricValue("wall cabinets"), 1);
    assert.equal(metricValue("corner units"), 2);
    assert.equal(metricValue("tall cabinets"), 1);
    assert.equal(metricValue("appliance spaces"), 1);
    assert.equal(metricValue("end panels"), 1);
    assert.equal(metricValue("unresolved"), 1);

    // header total == every rendered typed-bucket tile + the one bucket
    // that has no dedicated count tile (filler, shown as a $ breakdown
    // row instead — see state.summary.filler_price).
    const renderedSum = metricValue("base cabinets")
        + metricValue("wall cabinets")
        + metricValue("corner units")
        + metricValue("tall cabinets")
        + metricValue("appliance spaces")
        + metricValue("end panels")
        + h.component.state.summary.filler_count;
    assert.equal(renderedSum, 10, "header==sidebar-total identity, read straight from the DOM");
});

test("a design with NO corner/tall/appliance/panel/unresolved lines hides those optional tiles", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 7 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 48, depth_in: 24, height_in: 96 },
                design_name: "T7 plain fixture",
                lines: [
                    { layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                      width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
                      x_position_in: 0, y_position_in: 0, z_position_in: 0, wall: "back" },
                    { layout_key: "wall-1", cabinet_type: "wall", product_name: "Wall 24",
                      width_in: 24, height_in: 30, depth_in: 12, price: 120,
                      x_position_in: 0, y_position_in: 54, z_position_in: 0, wall: "back" },
                ],
            }),
        },
    });
    await tick();
    const metrics = [...h.root.querySelectorAll(".o_sbk_metric")];
    assert.ok(!metrics.some((n) => n.textContent.includes("tall cabinets")));
    assert.ok(!metrics.some((n) => n.textContent.includes("appliance spaces")));
    assert.ok(!metrics.some((n) => n.textContent.includes("end panels")));
    assert.ok(!metrics.some((n) => n.textContent.includes("unresolved")));
    // base/wall/corner stay always-visible even at zero (corner_count=0
    // here) — the AC requires base/wall/corner side by side, not hidden.
    assert.ok(metrics.some((n) => n.textContent.includes("corner units")));

    const headerItem = [...h.root.querySelectorAll(".o_sbk_count_item")][0];
    assert.match(headerItem.textContent.replace(/\s+/g, " ").trim(), /^2\s+cabinets$/);
});
