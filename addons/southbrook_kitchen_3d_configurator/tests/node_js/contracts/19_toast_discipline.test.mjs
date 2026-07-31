/**
 * Task 17 (corner-remediation, SBK-030) — toast discipline.
 *
 * Audit evidence (live): adding six B24 cabinets to a run that only
 * fits four produced five stacked, permanent, ~unclickable "run too
 * long" toasts that occluded the header/right panel, and the
 * over-length add was allowed anyway.
 *
 * Part 1 exercises `ToastStore` (static/src/js/toast/toast_store.esm.js)
 * directly — a pure, DOM/OWL-agnostic reducer, importable head­lessly
 * with no jsdom/mount involved — covering the AC:
 *   - repeated identical (type+message) toasts dedupe into one, with a
 *     running count and a refreshed auto-dismiss clock;
 *   - non-sticky toasts auto-dismiss ~8s after their last bump, sticky
 *     ones never do;
 *   - the rendered view caps at 3 visible with an "N more" rollup;
 *   - dismiss()/dismissAll() are synchronous and always succeed.
 *
 * Part 2 exercises the real component (via the shared mount harness)
 * for the behavioral AC that lives in kitchen_configurator.js itself:
 * an over-length add is refused by default (nothing pushed into
 * state.items) and only proceeds when the toast's "Add anyway" action
 * is invoked — and repeated refused attempts of the SAME product
 * dedupe into one counted toast instead of stacking, exactly the
 * audit's six-B24 scenario.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { ToastStore } from "../../../static/src/js/toast/toast_store.esm.js";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

// ─── Part 1 — ToastStore (pure reducer, no DOM) ────────────────────────────

function fakeClock(startAt = 0) {
    let now = startAt;
    return {
        now: () => now,
        advance: (ms) => { now += ms; },
    };
}

test("add(): a fresh message creates one toast with count 1", () => {
    const store = new ToastStore();
    store.add("Design saved", { type: "success" });
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].count, 1);
    assert.equal(store.toasts[0].message, "Design saved");
    assert.equal(store.toasts[0].type, "success");
});

test("add(): identical (type, message) repeats dedupe into ONE toast with a running count", () => {
    const store = new ToastStore();
    store.add("Run needs 144″ — room is 96″.", { type: "warning" });
    store.add("Run needs 144″ — room is 96″.", { type: "warning" });
    store.add("Run needs 144″ — room is 96″.", { type: "warning" });
    assert.equal(store.toasts.length, 1, "three identical adds must never stack");
    assert.equal(store.toasts[0].count, 3);
});

test("add(): different messages (or different types) do NOT dedupe together", () => {
    const store = new ToastStore();
    store.add("Added Base 24", { type: "success" });
    store.add("Added Wall 24", { type: "success" });
    store.add("Added Base 24", { type: "danger" });   // same text, different type
    assert.equal(store.toasts.length, 3);
});

test("add(): a custom dedupeKey overrides the default (type|message) key", () => {
    const store = new ToastStore();
    store.add("Run needs 144″ — room is 96″.", { type: "warning", dedupeKey: "overflow" });
    store.add("Run needs 168″ — room is 96″.", { type: "warning", dedupeKey: "overflow" });
    assert.equal(store.toasts.length, 1, "shared dedupeKey collapses even differing text");
    assert.equal(store.toasts[0].count, 2);
    assert.equal(store.toasts[0].message, "Run needs 168″ — room is 96″.",
        "a bump refreshes the displayed text to the latest occurrence");
});

test("tick(): a non-sticky toast auto-dismisses ~8s after its LAST bump, not its first", () => {
    const clock = fakeClock();
    const store = new ToastStore({ now: clock.now });
    store.add("Added Base 24", { type: "success" });
    clock.advance(6000);
    store.add("Added Base 24", { type: "success" });   // bump refreshes the clock
    clock.advance(6000);   // 12s since creation, but only 6s since the bump
    let removed = store.tick(clock.now());
    assert.deepEqual(removed, [], "must not expire — the bump reset the 8s window");
    assert.equal(store.toasts.length, 1);
    clock.advance(2001);   // now 8.001s since the bump
    removed = store.tick(clock.now());
    assert.equal(removed.length, 1);
    assert.equal(store.toasts.length, 0);
});

test("tick(): a sticky toast never auto-dismisses regardless of elapsed time", () => {
    const clock = fakeClock();
    const store = new ToastStore({ now: clock.now });
    store.add("Saving is disabled — reload to try again.", { type: "danger", sticky: true });
    clock.advance(60000);
    const removed = store.tick(clock.now());
    assert.deepEqual(removed, []);
    assert.equal(store.toasts.length, 1);
});

test("getView(): caps visible toasts at maxVisible (3) with an 'N more' rollup", () => {
    const store = new ToastStore({ maxVisible: 3 });
    for (let i = 0; i < 5; i++) {
        store.add(`Message ${i}`, { type: "info" });
    }
    const { visible, overflowCount } = store.getView();
    assert.equal(visible.length, 3, "never more than 3 rendered at once");
    assert.equal(overflowCount, 2, "the other 2 summarize into a single rollup count");
});

test("getView(): overflowCount is 0 when everything fits under the cap", () => {
    const store = new ToastStore({ maxVisible: 3 });
    store.add("Only one", { type: "info" });
    const { visible, overflowCount } = store.getView();
    assert.equal(visible.length, 1);
    assert.equal(overflowCount, 0);
});

test("dismiss(id): removes exactly that toast on the first call, returns true", () => {
    const store = new ToastStore();
    const a = store.add("A", { type: "info" });
    const b = store.add("B", { type: "info" });
    const ok = store.dismiss(a.id);
    assert.equal(ok, true);
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].id, b.id);
});

test("dismiss(id): a second call for an already-gone id is a harmless no-op returning false", () => {
    const store = new ToastStore();
    const a = store.add("A", { type: "info" });
    assert.equal(store.dismiss(a.id), true);
    assert.equal(store.dismiss(a.id), false);
});

test("dismissAll(): clears every toast in one call, including anything past the visible cap", () => {
    const store = new ToastStore({ maxVisible: 3 });
    for (let i = 0; i < 6; i++) store.add(`Message ${i}`, { type: "warning" });
    assert.equal(store.getView().overflowCount, 3);
    store.dismissAll();
    assert.equal(store.toasts.length, 0);
    const { visible, overflowCount } = store.getView();
    assert.equal(visible.length, 0);
    assert.equal(overflowCount, 0);
});

test("add(): a later call can upgrade a deduped toast's actions/sticky (e.g. attach 'Add anyway')", () => {
    const store = new ToastStore();
    store.add("Won't fit", { type: "warning", dedupeKey: "k" });
    assert.equal(store.toasts[0].actions, null);
    const action = { label: "Add anyway", onClick: () => {} };
    store.add("Won't fit", { type: "warning", dedupeKey: "k", sticky: true, actions: [action] });
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].sticky, true);
    assert.deepEqual(store.toasts[0].actions, [action]);
});

// ─── Part 2 — component integration: refuse-by-default over-length add ────

const B24 = {
    product_id: 24, name: "B24 Base Cabinet", cabinet_type: "base", sku: "B24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
    available_qty: 10, material: "white_melamine",
};

async function mountEmptyRoom() {
    // Default state.room.width_in is 96″ (see kitchen_configurator.js
    // setup()) when no room params/hydration override it — four 24″
    // bases exactly fill it; a fifth overflows, matching the audit's
    // "run too long" scenario at 1/6th the cabinet count.
    return mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [B24],
            }),
        },
    });
}

test("SBK-030: an over-length add is refused by default — nothing is pushed into state.items", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    assert.equal(c.state.items.length, 4, "the room-filling four must all succeed");

    c._addCabinetFromProduct(B24);   // the 5th — 120″ needed, 96″ room
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 4, "the over-length 5th add must be REFUSED, not appended");

    const toasts = c._toastStore.toasts;
    const refusal = toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal, "a refusal toast was shown");
    assert.equal(refusal.sticky, true, "the decision toast must not auto-dismiss out from under the rep");
    assert.ok(Array.isArray(refusal.actions) && refusal.actions.length === 1);
    assert.equal(refusal.actions[0].label, "Add anyway");
});

test("SBK-030: repeated refused attempts of the SAME cabinet dedupe into ONE toast with a count (the audit's six-B24 stack)", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    // Six more refused attempts, same as the audit ("adding six B24").
    for (let i = 0; i < 6; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    assert.equal(c.state.items.length, 4, "none of the six refused attempts were added");
    const refusals = c._toastStore.toasts.filter((t) => /won't fit/i.test(t.message));
    assert.equal(refusals.length, 1, "six repeats must collapse into exactly one toast, not stack");
    assert.equal(refusals[0].count, 6);
    // Two DISTINCT toasts exist in total — the "Added" success from the
    // four room-filling adds (itself deduped to one entry, count 4) and
    // the "won't fit" refusal (deduped to one entry, count 6) — both
    // comfortably under the 3-visible cap, so the rollup never engages.
    assert.equal(h.component.state.toastView.visible.length, 2);
    assert.equal(h.component.state.toastView.overflowCount, 0);
    const added = c._toastStore.toasts.find((t) => /^Added /.test(t.message));
    assert.equal(added.count, 4, "the four successful adds also dedupe into one entry");
});

test("SBK-030: clicking 'Add anyway' on the refusal toast performs the add and dismisses the prompt", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    c._addCabinetFromProduct(B24);   // refused
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 4);

    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal);
    c._runToastAction(refusal, refusal.actions[0]);   // "Add anyway"
    clearTimeout(c._autoSaveTimer);

    assert.equal(c.state.items.length, 5, "opting in must actually add the cabinet");
    assert.equal(
        c._toastStore.toasts.find((t) => t.id === refusal.id), undefined,
        "the decision prompt dismisses itself once acted on",
    );
    const confirmation = c._toastStore.toasts.find((t) => /anyway —/i.test(t.message));
    assert.ok(confirmation, "an honest confirmation replaces the silent old 'Added X' toast");
    assert.equal(confirmation.sticky, false, "the confirmation itself is non-blocking");
});

test("SBK-030: a fitting add is never refused (room untouched, no refusal toast)", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 1);
    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.equal(refusal, undefined);
});

test("SBK-030: dismissAll() clears the whole visible stack in one call", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c._toast("One", { type: "info" });
    c._toast("Two", { type: "info" });
    c._toast("Three", { type: "info" });
    assert.equal(c.state.toastView.visible.length, 3);
    c._dismissAllToasts();
    assert.equal(c.state.toastView.visible.length, 0);
    assert.equal(c._toastStore.toasts.length, 0);
});
