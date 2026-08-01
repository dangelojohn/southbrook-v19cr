/**
 * Task 35 (corner-remediation-2026-07-30) — undo/redo stack (minimal
 * default scope) + confirm-on-regenerate.
 *
 * Part 1 exercises `UndoStore` and `shouldConfirmArrange`
 * (static/src/js/undo/undo_store.esm.js) directly — pure,
 * DOM/OWL-agnostic reducers, importable head­lessly with no jsdom/mount
 * involved, exactly like 19_toast_discipline.test.mjs's Part 1 does for
 * `ToastStore`. Covers the brief's explicit semantics: snapshot
 * push/pop, the 25-snapshot cap, and redo-cleared-on-new-mutation.
 *
 * Part 2 exercises the real component (via the shared mount harness)
 * for every design-mutating op reachable from this screen the brief
 * lists — add, remove, move, width-resize, swap (both the plain
 * client-merge path and the server-governed corner path) — proving
 * each pushes exactly one PRE-OP snapshot (never on a refused/no-op
 * attempt) and that the first undo after each returns to the exact
 * prior state. Also covers: the two toolbar buttons (aria-label +
 * disabled state), the keyboard bindings (Ctrl+Z / Ctrl+Shift+Z /
 * Ctrl+Y — including that they're a no-op while typing in an INPUT,
 * per the pre-existing top-of-_onKeyDown guard), the explicit
 * Auto-arrange command's confirm-gate + its "undo snapshot pushed
 * before arranging" ordering, and the house rule that confirmation
 * NEVER uses window.confirm.
 *
 * Review fast-follow (Task 35 review, Important 1) — Q/E rotation
 * (_rotateSelected) is ALSO a design-mutating op (item.rotation_deg,
 * in place) and was initially shipped with no undo coverage at all: a
 * rep who rotated a cabinet and hit Ctrl+Z would silently undo an
 * unrelated earlier op instead. Added below (one pre-op snapshot per
 * Q/E keypress — not coalesced across repeated taps; the 25-entry cap
 * absorbs a burst the same way it absorbs a burst of any other op).
 *
 * "flip" (action_flip_layout, models/kitchen_design.py) is the ONE
 * item still in the brief's op list that is NOT reachable from this
 * screen — grepped: no `_flip*` method or UI control exists in
 * kitchen_configurator.js; the server action is only wired to a
 * button in the backend form view (kitchen_design_views.xml), a
 * different screen entirely. Nothing to wire here; see
 * task-35-report.md's per-AC table for the full note.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
    UndoStore, shouldConfirmArrange,
} from "../../../static/src/js/undo/undo_store.esm.js";
import { ConfirmationDialog } from "../shims/web_core_confirmation_dialog.mjs";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

// ─── Part 1 — UndoStore + shouldConfirmArrange (pure, no DOM) ──────────────

test("UndoStore: pushSnapshot then undo(current) returns the pushed snapshot and stashes current for redo", () => {
    const store = new UndoStore();
    store.pushSnapshot({ v: 1 });
    assert.equal(store.canUndo, true);
    assert.equal(store.canRedo, false);
    const popped = store.undo({ v: 2 });
    assert.deepEqual(popped, { v: 1 });
    assert.equal(store.canUndo, false);
    assert.equal(store.canRedo, true);
    const redone = store.redo({ v: 3 });
    assert.deepEqual(redone, { v: 2 }, "redo must return the snapshot undo() stashed for it");
    assert.equal(store.canUndo, true);
});

test("UndoStore: undo() on an empty undo stack returns null and mutates nothing", () => {
    const store = new UndoStore();
    assert.equal(store.undo({ v: 1 }), null);
    assert.equal(store.canRedo, false, "a null-returning undo must not have pushed onto redo");
    assert.equal(store.canUndo, false);
});

test("UndoStore: redo() on an empty redo stack returns null", () => {
    const store = new UndoStore();
    store.pushSnapshot({ v: 1 });
    assert.equal(store.redo({ v: 2 }), null, "nothing was ever undone, so redo has nothing to give back");
    assert.equal(store.canUndo, true, "the failed redo must not have disturbed the undo stack");
});

test("UndoStore: cap — pushing past maxDepth (default 25) silently drops the OLDEST snapshot", () => {
    const store = new UndoStore();
    for (let i = 0; i < 26; i++) store.pushSnapshot({ v: i });
    assert.equal(store.undoCount, 25, "26 pushes over a 25-cap must settle at exactly 25");
    // Pop everything and confirm snapshot #0 (the oldest) is gone but
    // #1..#25 all survived, in order.
    const popped = [];
    let current = { v: "sentinel" };
    while (store.canUndo) {
        current = store.undo(current);
        popped.push(current);
    }
    assert.deepEqual(popped.map((p) => p.v), [25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
});

test("UndoStore: a custom maxDepth is honored", () => {
    const store = new UndoStore({ maxDepth: 3 });
    for (let i = 0; i < 5; i++) store.pushSnapshot({ v: i });
    assert.equal(store.undoCount, 3);
});

test("UndoStore: pushSnapshot() ALWAYS clears the redo stack (redo cleared on new mutation)", () => {
    const store = new UndoStore();
    store.pushSnapshot({ v: 1 });
    store.undo({ v: 2 });
    assert.equal(store.canRedo, true, "sanity: something is redoable before the fresh mutation");
    store.pushSnapshot({ v: 3 });
    assert.equal(store.canRedo, false, "a fresh mutation must invalidate the old redo future");
    assert.equal(store.undoCount, 1, "only the fresh snapshot remains on the undo side");
});

test("UndoStore: clear() drops both stacks (design switch/reload)", () => {
    const store = new UndoStore();
    store.pushSnapshot({ v: 1 });
    store.pushSnapshot({ v: 2 });
    store.undo({ v: 3 });
    assert.ok(store.canUndo || store.canRedo);
    store.clear();
    assert.equal(store.canUndo, false);
    assert.equal(store.canRedo, false);
    assert.equal(store.undoCount, 0);
    assert.equal(store.redoCount, 0);
});

test("shouldConfirmArrange: an empty design never confirms — nothing placed, nothing to lose", () => {
    assert.equal(shouldConfirmArrange(0, true), false);
    assert.equal(shouldConfirmArrange(0, false), false);
    assert.equal(shouldConfirmArrange(0, undefined), false);
});

test("shouldConfirmArrange: with items, a known dirty flag is authoritative in both directions", () => {
    assert.equal(shouldConfirmArrange(3, true), true);
    assert.equal(shouldConfirmArrange(3, false), false);
});

test("shouldConfirmArrange: with items, an UNKNOWN dirty flag (null/undefined) falls back to the brief's stated default — confirm", () => {
    assert.equal(shouldConfirmArrange(3, null), true);
    assert.equal(shouldConfirmArrange(3, undefined), true);
});

// ─── Part 2 — component integration ────────────────────────────────────────

const BASE24 = {
    product_id: 24, name: "B24 Base Cabinet", cabinet_type: "base", sku: "B24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
    available_qty: 10, material: "white_melamine",
};
const BASE30 = {
    product_id: 30, name: "B30 Base Cabinet", cabinet_type: "base", sku: "B30",
    width_in: 30, height_in: 34.5, depth_in: 24, price: 220,
    available_qty: 10, material: "white_melamine",
};

function mountWithCatalog(rpcRoutes = {}) {
    return mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [BASE24, BASE30],
            }),
            ...rpcRoutes,
        },
    });
}

function clearAutoSave(h) {
    if (h.component._autoSaveTimer) clearTimeout(h.component._autoSaveTimer);
}

test("add (_addCabinetFromProduct): pushes one pre-op snapshot; undo returns to the exact prior (empty) state; redo re-adds it", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    assert.equal(c.state.items.length, 0);
    assert.equal(c.state.undoCount, 0);

    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    assert.equal(c.state.items.length, 1);
    assert.equal(c.state.undoCount, 1, "exactly one snapshot for one add");
    assert.equal(c.state.redoCount, 0);

    c._undo();
    assert.equal(c.state.items.length, 0, "undo restores the exact prior (empty) state");
    assert.equal(c.state.undoCount, 0);
    assert.equal(c.state.redoCount, 1);

    c._redo();
    assert.equal(c.state.items.length, 1, "redo re-applies the add");
    assert.equal(c.state.items[0].sku, "B24");
    h.destroy();
});

test("a REFUSED add (over-length, SBK-030 gate) pushes NO snapshot — nothing to undo", async () => {
    // Default room width_in is 96″ — five 24″ bases overflow it.
    const h = await mountWithCatalog();
    const c = h.component;
    for (let i = 0; i < 4; i++) { c._addCabinetFromProduct(BASE24); clearAutoSave(h); }
    assert.equal(c.state.items.length, 4);
    const undoCountBefore = c.state.undoCount;

    c._addCabinetFromProduct(BASE24);   // the 5th — refused
    clearAutoSave(h);
    assert.equal(c.state.items.length, 4, "refused add must not have been appended");
    assert.equal(c.state.undoCount, undoCountBefore, "a refused add must not push a wasted snapshot");
    h.destroy();
});

test("remove (_removeSelectedCabinet): pushes one pre-op snapshot; undo restores the removed cabinet", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = c.state.items[0];
    const undoCountBeforeRemove = c.state.undoCount;

    c._removeSelectedCabinet();
    assert.equal(c.state.items.length, 0);
    assert.equal(c.state.undoCount, undoCountBeforeRemove + 1);

    c._undo();
    assert.equal(c.state.items.length, 1, "undo restores the removed cabinet");
    assert.equal(c.state.items[0].sku, "B24");
    h.destroy();
});

test("width-resize (_updateSelectedWidth): pushes one pre-op snapshot; undo restores the previous width", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = c.state.items[0];
    assert.equal(c.state.items[0].width_in, 24);

    c._updateSelectedWidth("30");
    clearAutoSave(h);
    assert.equal(c.state.items[0].width_in, 30);

    c._undo();
    assert.equal(c.state.items[0].width_in, 24, "undo restores the pre-resize width");
    h.destroy();
});

test("an INVALID width-resize attempt (out of 6-48in bounds) pushes NO snapshot", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = c.state.items[0];
    const undoCountBefore = c.state.undoCount;

    c._updateSelectedWidth("999");
    assert.equal(c.state.items[0].width_in, 24, "invalid input must not mutate width");
    assert.equal(c.state.undoCount, undoCountBefore, "no snapshot for a no-op validation failure");
    h.destroy();
});

test("swap (_swapSelectedProduct, non-corner client-merge path): pushes one pre-op snapshot; undo restores the previous product", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = c.state.items[0];
    assert.equal(c.state.items[0].product_id, 24);

    await c._swapSelectedProduct(String(BASE30.product_id));
    clearAutoSave(h);
    assert.equal(c.state.items[0].product_id, 30);

    c._undo();
    assert.equal(c.state.items[0].product_id, 24, "undo restores the pre-swap product");
    h.destroy();
});

test("move (onMoveItemStart + onMoveItem): pushes exactly ONE pre-op snapshot at drag-commit, before the canvas's own position write; undo restores the pre-drag position", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    const preDragX = c.state.items[0].x_position_in;
    const undoCountBeforeMove = c.state.undoCount;   // the add itself already pushed one

    // Real gesture order (see kitchen_canvas.esm.js's onMoveItemStart
    // doc comment): the child fires onMoveItemStart ONCE, before it
    // ever mutates the item's x_position_in in place, then continues
    // mutating it live on every drag tick, then fires onMoveItem once
    // at release. The <KitchenCanvas> test mock doesn't simulate real
    // pointer events, so this drives the same two callbacks directly,
    // in that same order — the established idiom every other
    // canvasProps() test in this suite (e.g. 05_callback_contract)
    // already uses.
    h.canvasProps().onMoveItemStart(c.state.items[0]);
    assert.equal(c.state.undoCount, undoCountBeforeMove + 1,
        "drag-commit pushes exactly one snapshot, not one per mousemove tick");

    const moved = c.state.items[0];
    moved.x_position_in = (preDragX || 0) + 38;   // simulate the canvas's in-place write
    h.canvasProps().onMoveItem({ item: moved, x_position_in: moved.x_position_in, pinnable: false });
    clearAutoSave(h);
    assert.equal(c.state.undoCount, undoCountBeforeMove + 1,
        "onMoveItem itself must not push a SECOND snapshot for the same gesture");

    c._undo();
    assert.equal(c.state.items[0].x_position_in, preDragX, "undo restores the exact pre-drag position");
    h.destroy();
});

test("rotate (_rotateSelected, Q/E): pushes one pre-op snapshot per keypress; undo restores the previous rotation_deg (review fast-follow, Important 1)", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = c.state.items[0];
    // A freshly added item carries no rotation_deg field at all (see
    // _addCabinetFromProduct — it's never in the spread); _rotateSelected
    // is the first thing that ever introduces it, via `|| 0`. The
    // pre-rotation snapshot therefore captures `undefined`, not `0` —
    // asserted explicitly below so this test pins the REAL pre-op
    // state, not an assumed one.
    const preRotation = c.state.items[0].rotation_deg;
    assert.equal(!!preRotation, false, "sanity: no rotation yet");
    const undoCountBeforeRotate = c.state.undoCount;   // the add itself already pushed one

    c._rotateSelected(90);   // E
    clearAutoSave(h);
    assert.equal(c.state.items[0].rotation_deg, 90);
    assert.equal(c.state.undoCount, undoCountBeforeRotate + 1,
        "one rotate keypress pushes exactly one snapshot");

    c._rotateSelected(90);   // a second E — a SEPARATE snapshot, not coalesced
    clearAutoSave(h);
    assert.equal(c.state.items[0].rotation_deg, 180);
    assert.equal(c.state.undoCount, undoCountBeforeRotate + 2);

    c._undo();
    assert.equal(c.state.items[0].rotation_deg, 90,
        "first undo after the second rotate returns to the exact prior (90°) state");
    c._undo();
    assert.equal(c.state.items[0].rotation_deg, preRotation,
        "second undo returns to the exact pre-rotation state");
    h.destroy();
});

test("rotate: a no-op call (nothing selected, or a filler/panel that never rotates) pushes NO snapshot", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.selected = null;
    const undoCountBefore = c.state.undoCount;

    c._rotateSelected(90);
    assert.equal(c.state.undoCount, undoCountBefore, "nothing selected -> no-op, no snapshot");

    c.state.selected = { ...c.state.items[0], cabinet_type: "filler" };
    c._rotateSelected(90);
    assert.equal(c.state.undoCount, undoCountBefore, "fillers never rotate -> no-op, no snapshot");
    h.destroy();
});

test("Ctrl+Z with the customer-name INPUT focused is a no-op — the top-of-_onKeyDown editable-target guard covers the new shortcuts too (review fast-follow, Minor 3)", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    assert.equal(c.state.items.length, 1);
    const undoCountBefore = c.state.undoCount;

    c._onKeyDown({
        key: "z", ctrlKey: true,
        target: { tagName: "INPUT" },
        preventDefault() { throw new Error("must not even reach the Ctrl+Z branch"); },
    });

    assert.equal(c.state.items.length, 1, "Ctrl+Z while typing in an input must not undo anything");
    assert.equal(c.state.undoCount, undoCountBefore, "the stack itself must be untouched");
    h.destroy();
});

test("redo is cleared by a fresh mutation after an undo (component-level integration of the store's redo-clear semantics)", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c._undo();
    assert.equal(c.state.redoCount, 1);

    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    assert.equal(c.state.redoCount, 0, "a fresh mutation must clear the redo stack");
    h.destroy();
});

test("_resetForNewSession clears both stacks and resets the dirty flag to the conservative default (brief: cleared on design switch/reload)", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c._undo();
    assert.ok(c.state.undoCount + c.state.redoCount > 0, "sanity: something is on one of the stacks");
    c._placementsDirty = false;

    c._resetForNewSession();
    assert.equal(c.state.undoCount, 0);
    assert.equal(c.state.redoCount, 0);
    assert.equal(c._placementsDirty, true, "reset to the safe 'unknown, assume dirty' default");
    h.destroy();
});

test("undo/redo trigger the NORMAL debounced autosave (brief: 'restores client state AND triggers the normal autosave')", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    // Two cabinets so undoing the second leaves a non-empty design —
    // _queueAutoSave() has a pre-existing, unrelated guard that skips
    // scheduling entirely when state.items is empty (same guard
    // _removeSelectedCabinet's last-item case already hits), which
    // would otherwise mask the very thing this test checks.
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c._addCabinetFromProduct(BASE30);
    clearAutoSave(h);
    c._undo();
    assert.ok(c._autoSaveTimer, "_undo must arm the debounced autosave, same as any other mutation");
    clearAutoSave(h);
    h.destroy();
});

// ─── Corner swap (server-governed path) ────────────────────────────────────

const DESIGN_ID = 501;
const SERVER_LINE_ID = 4242;
const NEW_CORNER_PRODUCT = {
    product_id: 777, name: "Lazy Susan Corner", cabinet_type: "corner",
    sku: "SB-CORNER-SUSAN", width_in: 36, height_in: 34.5, depth_in: 24,
    price: 500, available_qty: 5, material: "white_melamine",
};
const ORIGINAL_CORNER_PRODUCT = {
    product_id: 111, name: "Blind Corner", cabinet_type: "corner",
    sku: "SB-CORNER-BLIND", width_in: 30, height_in: 34.5, depth_in: 24,
    price: 450, available_qty: 5, material: "white_melamine",
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
function freshCornerLine(overrides = {}) {
    return {
        id: NEW_CORNER_PRODUCT.product_id, product_id: NEW_CORNER_PRODUCT.product_id,
        product_name: "Lazy Susan Corner", default_code: "SB-CORNER-SUSAN",
        layout_key: "corner-1", cabinet_type: "corner",
        width_in: 36, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        rotation_deg: 90, pinned: false, price: 500, quantity: 1, wall: "left",
        appliance_type: false, is_unresolved: false,
        ...overrides,
    };
}
async function mountWithCornerLine({ callKw, loadLines } = {}) {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [ORIGINAL_CORNER_PRODUCT, NEW_CORNER_PRODUCT],
            }),
            ...(callKw ? { "/web/dataset/call_kw": callKw } : {}),
            ...(loadLines ? { "/southbrook_kitchen/configurator/load_design_lines": loadLines } : {}),
        },
    });
    h.component.state.designId = DESIGN_ID;
    h.component.state.items = [cornerItem()];
    h.component.state.selected = h.component.state.items[0];
    return h;
}

test("corner swap (server-governed): a SUCCESSFUL swap pushes exactly one pre-op snapshot; undo restores the previous corner product", async () => {
    const h = await mountWithCornerLine({
        callKw: (params) => {
            if (params.method === "search_read") return [{ id: SERVER_LINE_ID }];
            if (params.method === "action_swap_product") return { corners: 0, removed: 0, inserted: 0, swapped: true };
            throw new Error("unexpected call_kw method: " + params.method);
        },
        loadLines: () => ({ lines: [freshCornerLine()] }),
    });
    const c = h.component;
    assert.equal(c.state.undoCount, 0);

    await c._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));
    assert.equal(c.state.items[0].product_id, NEW_CORNER_PRODUCT.product_id);
    assert.equal(c.state.undoCount, 1, "exactly one snapshot for the successful server swap");

    c._undo();
    assert.equal(c.state.items[0].product_id, ORIGINAL_CORNER_PRODUCT.product_id,
        "undo restores the pre-swap corner product");
    h.destroy();
});

test("corner swap (server-governed): a REFUSED swap (UserError) pushes NO snapshot — nothing to undo", async () => {
    const h = await mountWithCornerLine({
        callKw: (params) => {
            if (params.method === "search_read") return [{ id: SERVER_LINE_ID }];
            if (params.method === "action_swap_product") throw new Error("too large for this junction — swap refused.");
            throw new Error("unexpected call_kw method: " + params.method);
        },
    });
    const c = h.component;
    await c._swapSelectedProduct(String(NEW_CORNER_PRODUCT.product_id));
    assert.equal(c.state.items[0].product_id, ORIGINAL_CORNER_PRODUCT.product_id, "refused — state unchanged");
    assert.equal(c.state.undoCount, 0, "a refused swap must not waste an undo slot");
    h.destroy();
});

// ─── Toolbar: undo/redo buttons ─────────────────────────────────────────────

test("toolbar: undo/redo buttons carry an aria-label (icon-only controls need an accessible name — Task 34 pattern) and start disabled", async () => {
    const h = await mountWithCatalog();
    const btns = [...h.root.querySelectorAll(".o_sbk_history_btn")];
    assert.equal(btns.length, 2, "one undo button, one redo button");
    assert.match(btns[0].getAttribute("aria-label"), /undo/i);
    assert.match(btns[1].getAttribute("aria-label"), /redo/i);
    assert.equal(btns[0].disabled, true, "nothing to undo yet");
    assert.equal(btns[1].disabled, true, "nothing to redo yet");
    h.destroy();
});

test("toolbar: undo/redo buttons enable/disable correctly across a mutation -> click-undo -> click-redo cycle, and clicking them actually mutates state", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    await tick();

    let [undoBtn, redoBtn] = [...h.root.querySelectorAll(".o_sbk_history_btn")];
    assert.equal(undoBtn.disabled, false, "undo enables after a mutation");
    assert.equal(redoBtn.disabled, true, "nothing undone yet");

    undoBtn.click();
    await tick();
    assert.equal(c.state.items.length, 0, "clicking the undo button actually undid the add");
    [undoBtn, redoBtn] = [...h.root.querySelectorAll(".o_sbk_history_btn")];
    assert.equal(undoBtn.disabled, true);
    assert.equal(redoBtn.disabled, false);

    redoBtn.click();
    await tick();
    assert.equal(c.state.items.length, 1, "clicking the redo button re-applied the add");
    h.destroy();
});

// ─── Keyboard bindings ──────────────────────────────────────────────────────

function fakeKey(key, { ctrlKey = false, shiftKey = false } = {}) {
    return { key, ctrlKey, shiftKey, target: { tagName: "DIV" }, preventDefault() {} };
}

test("Ctrl+Z calls _undo(); Ctrl+Shift+Z and Ctrl+Y both call _redo() — via the SAME _onKeyDown window listener (no second listener added)", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    assert.equal(c.state.items.length, 1);

    c._onKeyDown(fakeKey("z", { ctrlKey: true }));
    assert.equal(c.state.items.length, 0, "Ctrl+Z undid the add");

    c._onKeyDown(fakeKey("z", { ctrlKey: true, shiftKey: true }));
    assert.equal(c.state.items.length, 1, "Ctrl+Shift+Z redid it");

    c._onKeyDown(fakeKey("z", { ctrlKey: true }));
    assert.equal(c.state.items.length, 0);
    c._onKeyDown(fakeKey("y", { ctrlKey: true }));
    assert.equal(c.state.items.length, 1, "Ctrl+Y also redoes");
    h.destroy();
});

test("bare Z/Y (no Ctrl/Cmd) do nothing — undo/redo must not fire on plain typing", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c._onKeyDown(fakeKey("z", { ctrlKey: false }));
    c._onKeyDown(fakeKey("y", { ctrlKey: false }));
    assert.equal(c.state.items.length, 1, "no ctrl/cmd modifier -> no-op");
    h.destroy();
});

// ─── Confirm-on-regenerate (Auto-arrange) ──────────────────────────────────

test("_confirmedAutoArrange: a no-op with no saved design or an empty layout — never opens the dialog, never touches the undo stack", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    await c._confirmedAutoArrange();
    assert.equal(h.dialogCalls.length, 0);
    assert.equal(c.state.undoCount, 0);
    h.destroy();
});

test("_confirmedAutoArrange: dirty (the safe default) shows the Odoo ConfirmationDialog BEFORE arranging; nothing happens until confirmed", async () => {
    const h = await mountWithCatalog({
        "/southbrook_kitchen/configurator/rearrange": () => ({
            lines: [], room: { width_in: 96, depth_in: 96, height_in: 96 },
        }),
    });
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 55;
    assert.equal(c._placementsDirty, true, "fresh instance defaults to dirty (unknown -> assume dirty)");
    const undoCountBefore = c.state.undoCount;

    await c._confirmedAutoArrange();
    assert.equal(h.dialogCalls.length, 1, "a dirty design must confirm before arranging");
    assert.equal(h.dialogCalls[0].component, ConfirmationDialog,
        "must use the real Odoo ConfirmationDialog component, not a bespoke one");
    assert.equal(h.dialogCalls[0].props.confirmLabel, "Arrange");
    assert.equal(h.dialogCalls[0].props.cancelLabel, "Cancel");
    assert.equal(c.state.undoCount, undoCountBefore, "must not snapshot or arrange before the rep confirms");

    // Simulate clicking "Arrange".
    await h.dialogCalls[0].props.confirm();
    assert.equal(c.state.undoCount, undoCountBefore + 1,
        "a confirmed arrange pushes an undo snapshot FIRST — even a confirmed arrange is undoable");
    h.destroy();
});

test("_confirmedAutoArrange: cancelling the dialog arranges nothing and pushes no snapshot", async () => {
    const h = await mountWithCatalog({
        "/southbrook_kitchen/configurator/rearrange": () => ({ lines: [], room: {} }),
    });
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 55;
    const undoCountBefore = c.state.undoCount;

    await c._confirmedAutoArrange();
    h.dialogCalls[0].props.cancel();
    assert.equal(c.state.undoCount, undoCountBefore, "Cancel must not snapshot or arrange");
    h.destroy();
});

test("_confirmedAutoArrange: NOT dirty (after a prior successful arrange) skips the dialog and arranges immediately", async () => {
    const h = await mountWithCatalog({
        "/southbrook_kitchen/configurator/rearrange": () => ({
            lines: [], room: { width_in: 96, depth_in: 96, height_in: 96 },
        }),
    });
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 55;
    c._placementsDirty = false;
    const undoCountBefore = c.state.undoCount;   // the add itself already pushed one

    await c._confirmedAutoArrange();
    assert.equal(h.dialogCalls.length, 0, "nothing to lose -> no confirmation needed");
    assert.equal(c.state.undoCount, undoCountBefore + 1,
        "still pushes the pre-op snapshot — an un-confirmed arrange is undoable too");
    h.destroy();
});

test("_confirmedAutoArrange: a successful arrange clears the dirty flag so the NEXT arrange doesn't confirm again", async () => {
    const h = await mountWithCatalog({
        "/southbrook_kitchen/configurator/rearrange": () => ({
            lines: [], room: { width_in: 96, depth_in: 96, height_in: 96 },
        }),
    });
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 55;

    await c._confirmedAutoArrange();
    await h.dialogCalls[0].props.confirm();
    await tick();
    assert.equal(c._placementsDirty, false, "a successful arrange must clear the dirty flag");
    h.destroy();
});

test("house rule: confirm-on-regenerate NEVER calls window.confirm (browser modals wedge automation)", async () => {
    const h = await mountWithCatalog({
        "/southbrook_kitchen/configurator/rearrange": () => ({ lines: [], room: {} }),
    });
    const c = h.component;
    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 55;

    let nativeConfirmCalled = false;
    const original = h.dom.window.confirm;
    h.dom.window.confirm = () => { nativeConfirmCalled = true; return true; };

    await c._confirmedAutoArrange();

    assert.equal(nativeConfirmCalled, false, "must never fall back to window.confirm");
    assert.equal(h.dialogCalls.length, 1, "must use the Odoo dialog service instead");
    h.dom.window.confirm = original;
    h.destroy();
});

// ─── Toolbar: Auto-arrange button ───────────────────────────────────────────

function findButtonByText(root, text) {
    return [...root.querySelectorAll(".o_sbk_actions button")]
        .find((b) => b.textContent.trim() === text);
}

test("toolbar: the Auto-arrange button is disabled with no saved design or an empty layout, enabled once both exist", async () => {
    const h = await mountWithCatalog();
    const c = h.component;
    assert.equal(findButtonByText(h.root, "Auto-arrange").disabled, true, "no design id yet");

    c._addCabinetFromProduct(BASE24);
    clearAutoSave(h);
    c.state.designId = 7;
    await tick();
    assert.equal(findButtonByText(h.root, "Auto-arrange").disabled, false);
    h.destroy();
});
