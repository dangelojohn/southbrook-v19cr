/**
 * Task 27 (corner-remediation 2026-07-30, opportunity-trace.md §1) —
 * "Swap UX reachability" gap fix.
 *
 * The trace confirmed the Selected Cabinet dropdown correctly lists
 * corner substyles, but choosing one never invoked
 * `action_swap_product` — `_swapSelectedProduct` was a pure
 * client-side merge + `_queueAutoSave()` -> `save_design`'s plain
 * `line.write()`, which only re-validates the /products catalog
 * contract (sale_ok / is_cabinet). NONE of `action_swap_product`'s
 * Task-6 corner governance (corner-only restriction, junction
 * footprint-fit, hand re-derive/refusal) was ever enforced from the
 * browser.
 *
 * The fix (kitchen_configurator.js): a line with
 * `cabinet_type === "corner"` AND a persisted design
 * (`state.designId` set) now routes the swap through
 * `southbrook.kitchen.design.line.action_swap_product` via a plain
 * `/web/dataset/call_kw` ORM call, resolving the line's real DB id
 * first via `search_read` (state.items only ever carries the
 * PRODUCT id under "id"/"product_id" — see _serialize_design_lines,
 * controllers/main.py — never the design line's own id). Every other
 * cabinet_type, and any corner line the server doesn't know about yet
 * (unsaved design, or a resolve-miss), stays on the original
 * client-only merge.
 *
 * This file locks four contracts:
 *   1. A persisted corner swap calls call_kw action_swap_product with
 *      the resolved line id and the new product id (spy on the RPC).
 *   2. A server refusal (UserError) surfaces via the toast store with
 *      the server's exact actionable message, and leaves state.items
 *      completely unchanged.
 *   3. A server success applies the server's authoritative line data
 *      (product/price/dims) onto state.items/state.selected, and
 *      never queues the legacy full-design autosave (the write is
 *      already durably persisted server-side).
 *   4. A non-corner swap is completely unaffected — never touches
 *      call_kw, still goes through the pre-existing client-only merge
 *      + debounced /save.
 *
 * Two edge cases from the fix's own fallback branch are also covered:
 * an unsaved design (no state.designId) and a search_read resolve-miss
 * (line genuinely not round-tripped to the server yet) both fall back
 * to the client-only merge rather than silently doing nothing.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

const DESIGN_ID = 501;
const SERVER_LINE_ID = 4242;

const NEW_CORNER_PRODUCT = {
    product_id: 777, name: "Lazy Susan Corner", cabinet_type: "corner",
    sku: "SB-CORNER-SUSAN", width_in: 36, height_in: 34.5, depth_in: 24,
    price: 500, available_qty: 5, material: "white_melamine",
};

function cornerItem(overrides = {}) {
    return {
        layout_key: "corner-1", cabinet_type: "corner", wall: "left",
        product_id: 111, name: "Blind Corner", default_code: "SB-CORNER-BLIND",
        width_in: 30, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 90,
        pinned: false, price: 450,
        ...overrides,
    };
}

// Shape matching controllers/main.py _serialize_design_lines /
// _lineToItem's expectations — what /load_design_lines echoes back
// after a successful server-side swap.
function freshCornerLine(overrides = {}) {
    return {
        id: NEW_CORNER_PRODUCT.product_id,
        product_id: NEW_CORNER_PRODUCT.product_id,
        product_name: "Lazy Susan Corner",
        default_code: "SB-CORNER-SUSAN",
        layout_key: "corner-1",
        cabinet_type: "corner",
        width_in: 36, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        rotation_deg: 90, pinned: false,
        price: 500, quantity: 1, wall: "left",
        appliance_type: false, is_unresolved: false,
        ...overrides,
    };
}

async function mountWithCornerLine({ callKw, loadLines, designId = DESIGN_ID } = {}) {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [NEW_CORNER_PRODUCT],
            }),
            ...(callKw ? { "/web/dataset/call_kw": callKw } : {}),
            ...(loadLines ? { "/southbrook_kitchen/configurator/load_design_lines": loadLines } : {}),
        },
    });
    h.component.state.designId = designId;
    h.component.state.items = [cornerItem()];
    h.component.state.selected = h.component.state.items[0];
    return h;
}

function clearAutoSave(h) {
    if (h.component._autoSaveTimer) clearTimeout(h.component._autoSaveTimer);
}

// ─── 1. Corner swap calls the RPC ──────────────────────────────────────────

test("corner swap: resolves the line id via search_read, then calls action_swap_product via call_kw", async () => {
    const calls = [];
    const h = await mountWithCornerLine({
        callKw: (params) => {
            calls.push(params);
            if (params.method === "search_read") return [{ id: SERVER_LINE_ID }];
            if (params.method === "action_swap_product") {
                return { corners: 0, removed: 0, inserted: 0, swapped: true };
            }
            throw new Error("unexpected call_kw method: " + params.method);
        },
        loadLines: () => ({ lines: [freshCornerLine()] }),
    });

    await h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    const searchReadCall = calls.find(c => c.method === "search_read");
    assert.ok(searchReadCall, "must resolve the server-side line id first");
    assert.equal(searchReadCall.model, "southbrook.kitchen.design.line");
    assert.deepEqual(searchReadCall.args[0], [
        ["design_id", "=", DESIGN_ID],
        ["layout_key", "=", "corner-1"],
    ]);

    const swapCall = calls.find(c => c.method === "action_swap_product");
    assert.ok(swapCall, "must call action_swap_product");
    assert.equal(swapCall.model, "southbrook.kitchen.design.line");
    assert.deepEqual(swapCall.args, [[SERVER_LINE_ID], NEW_CORNER_PRODUCT.product_id],
        "args must be [[resolved line id], new product id] — the exact action_swap_product(product_id) signature");
    h.destroy();
});

// ─── 2. Refusal shows toast + state unchanged ──────────────────────────────

test("corner swap refusal: the server's UserError message is toasted and state.items is left untouched", async () => {
    const REFUSAL_MSG = "SB-CORNER-BLIND-XL (37.0\" x 24.0\") is too large for "
        + "this junction's reserved space (30.0\" x 24.0\") — swap refused.";
    const h = await mountWithCornerLine({
        callKw: (params) => {
            if (params.method === "search_read") return [{ id: SERVER_LINE_ID }];
            if (params.method === "action_swap_product") {
                throw new Error(REFUSAL_MSG);
            }
            throw new Error("unexpected call_kw method: " + params.method);
        },
    });
    const before = JSON.parse(JSON.stringify(h.component.state.items[0]));

    await h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    assert.deepEqual(
        JSON.parse(JSON.stringify(h.component.state.items[0])), before,
        "a refused swap must leave state.items[idx] byte-identical to before the attempt");
    assert.equal(h.component.state.selected.product_id, before.product_id,
        "state.selected must not have been swapped to the refused product either");

    const toast = h.component._toastStore.toasts.find(t => t.message === REFUSAL_MSG);
    assert.ok(toast, "the server's exact actionable refusal message must be shown, verbatim");
    assert.equal(toast.type, "danger");
    assert.equal(toast.sticky, true, "a refusal must not silently auto-dismiss");
    h.destroy();
});

// ─── 3. Success applies the server's authoritative result ─────────────────

test("corner swap success: applies the server's line (product/price/dims), no legacy autosave queued", async () => {
    const h = await mountWithCornerLine({
        callKw: (params) => {
            if (params.method === "search_read") return [{ id: SERVER_LINE_ID }];
            if (params.method === "action_swap_product") {
                return { corners: 0, removed: 0, inserted: 0, swapped: true };
            }
            throw new Error("unexpected call_kw method: " + params.method);
        },
        loadLines: () => ({ lines: [freshCornerLine()] }),
    });

    await h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    const item = h.component.state.items[0];
    assert.equal(item.product_id, NEW_CORNER_PRODUCT.product_id);
    assert.equal(item.name, "Lazy Susan Corner",
        "_lineToItem mirrors product_name onto .name");
    assert.equal(item.default_code, "SB-CORNER-SUSAN");
    assert.equal(item.price, 500, "server-authoritative price, not the client's guess");
    assert.equal(item.width_in, 36, "server-authoritative footprint");
    // Placement fields are untouched by _swap_derived_corner server-side
    // and echoed back identically by /load_design_lines.
    assert.equal(item.layout_key, "corner-1");
    assert.equal(item.wall, "left");

    assert.equal(h.component.state.selected.product_id, NEW_CORNER_PRODUCT.product_id,
        "state.selected (the edit-drawer binding) must also reflect the swapped-in product");

    const saveCalls = h.rpcCalls("/southbrook_kitchen/configurator/save");
    assert.equal(saveCalls.length, 0,
        "a successful server-side swap is already durably persisted — must not also queue the full-design autosave");

    const successToast = h.component._toastStore.toasts.find(
        t => t.type === "success" && /swap/i.test(t.message));
    assert.ok(successToast, "a success confirmation should be shown");
    h.destroy();
});

// ─── 4. Non-corner swap keeps the legacy client-only path ─────────────────

test("non-corner swap: never touches call_kw, still goes through the client-only merge + autosave", async () => {
    const BASE_PRODUCT = {
        product_id: 900, name: "Base 24", cabinet_type: "base", sku: "B24",
        width_in: 24, height_in: 34.5, depth_in: 24, price: 300,
        available_qty: 5, material: "white_melamine",
    };
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [BASE_PRODUCT],
            }),
        },
    });
    h.component.state.designId = DESIGN_ID;
    const baseItem = {
        layout_key: "base-1", cabinet_type: "base", product_id: 1, name: "Old Base",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, price: 250,
    };
    h.component.state.items = [baseItem];
    h.component.state.selected = baseItem;

    await h.component._swapSelectedProduct(String(BASE_PRODUCT.product_id));

    assert.equal(h.component.state.items[0].product_id, BASE_PRODUCT.product_id,
        "the legacy merge still performs the swap");
    assert.equal(h.component.state.items[0].layout_key, "base-1",
        "position/layout_key are preserved, same as before Task 27");

    assert.equal(h.rpcCalls("/web/dataset/call_kw").length, 0,
        "a non-corner swap must never attempt the server-side ORM route");
    clearAutoSave(h);
    h.destroy();
});

// ─── Fallback edge cases ────────────────────────────────────────────────────

test("corner swap with no persisted design (designId unset) falls back to the client-only merge", async () => {
    const h = await mountWithCornerLine({ designId: null });

    await h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    assert.equal(h.component.state.items[0].product_id, NEW_CORNER_PRODUCT.product_id,
        "an unsaved design still lets the rep swap locally");
    assert.equal(h.rpcCalls("/web/dataset/call_kw").length, 0,
        "no server id exists yet to govern — must never attempt call_kw");
    clearAutoSave(h);
    h.destroy();
});

test("corner swap: a search_read resolve-miss (line not round-tripped yet) falls back to the client-only merge", async () => {
    const h = await mountWithCornerLine({
        callKw: (params) => {
            if (params.method === "search_read") return [];   // no matching server row
            throw new Error("action_swap_product must never be called without a resolved line id");
        },
    });

    await h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    assert.equal(h.component.state.items[0].product_id, NEW_CORNER_PRODUCT.product_id,
        "falls back to the legacy merge so the swap still visibly happens");
    clearAutoSave(h);
    h.destroy();
});

// ─── Regression guard — F14 no-op path is unaffected by making the method async ──

test("F14 regression: _swapSelectedProduct is still a synchronous no-op when state.selected has no layout_key", async () => {
    const h = await mountWithCornerLine({});
    h.component.state.selected = NEW_CORNER_PRODUCT;   // no layout_key
    const before = h.component.state.items[0].product_id;

    // Deliberately not awaited — the guard clause must fire before any
    // RPC/await point, exactly like the pre-Task-27 synchronous version.
    h.component._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));

    assert.equal(h.component.state.items[0].product_id, before, "must no-op synchronously");
    assert.equal(h.rpcCalls("/web/dataset/call_kw").length, 0);
    h.destroy();
});
