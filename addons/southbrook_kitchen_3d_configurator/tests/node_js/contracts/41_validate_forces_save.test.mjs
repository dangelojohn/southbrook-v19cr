/**
 * C-12 fix (2026-08-05) — "Validate must never bless stale server
 * state". `_runValidateAction` (the toolbar's explicit "Validate"
 * button — action_validate_production) used to fetch a fresh
 * server-side validation without ever checking whether local state
 * (state.items/state.room) had diverged from what the server actually
 * has on file. Reported shape: an edit that hadn't saved yet (or a
 * SILENTLY-failed background auto-save) meant Validate green-lit the
 * server's stale copy while the screen showed something different
 * ("the stale 14-line copy while the screen shows 15").
 *
 * Fix: `_runValidateAction` now checks a new reactive
 * `state.unsavedChanges` flag (see kitchen_configurator.js's
 * `_markPlacementsDirty` doc comment for the full contract — a
 * deliberately SEPARATE flag from Task 35's `_placementsDirty`, which
 * means something else and must not be repurposed) and, if dirty,
 * awaits `_saveDesign()` before validating. If that save fails, the
 * gate returns without validating at all — `_saveDesign` has already
 * surfaced the reason via its own sticky danger toast.
 *
 * Also covers: the auto-save status pill's new third "Unsaved changes"
 * state (state.unsavedChanges rendered ahead of the "Auto-saved"
 * branch), and that a clean design (nothing dirty) validates directly
 * with no extra save RPC.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

const LINES = [
    { id: 501, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
      wall: "back", price: 500 },
];

function mountHydrated(rpcRoutes = {}) {
    return mountConfigurator({
        actionParams: { design_id: 5100 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "C-12 test design", lines: LINES,
            }),
            ...rpcRoutes,
        },
    });
}

test("a hydrated (clean) design validates directly — no forced save when nothing is dirty", async () => {
    let saveCalls = 0;
    let validateCalls = 0;
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => { saveCalls++; return { id: 5100, name: "x" }; },
        "/web/dataset/call_kw": (params) => {
            if (params.method === "action_validate_production") {
                validateCalls++;
                return { type: "ir.actions.client", tag: "display_notification", params: {} };
            }
            throw new Error("unexpected call_kw: " + params.method);
        },
    });
    assert.equal(h.component.state.unsavedChanges, false, "a freshly hydrated design starts clean");
    await h.component._runValidateAction();
    assert.equal(saveCalls, 0, "no save was forced — nothing was dirty");
    assert.equal(validateCalls, 1, "validate still ran normally");
    h.destroy();
});

test("an unsaved local edit forces a save BEFORE validating, and clears unsavedChanges on success", async () => {
    let saveCalls = 0;
    let validateCalls = 0;
    const callOrder = [];
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => {
            saveCalls++;
            callOrder.push("save");
            return { id: 5100, name: "x" };
        },
        "/web/dataset/call_kw": (params) => {
            if (params.method === "action_validate_production") {
                validateCalls++;
                callOrder.push("validate");
                return { type: "ir.actions.client", tag: "display_notification", params: {} };
            }
            throw new Error("unexpected call_kw: " + params.method);
        },
    });

    // A real local mutation — removes the one cabinet, exactly like the
    // reported "14 lines on the server, screen shows 15/13" divergence.
    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();
    assert.equal(h.component.state.unsavedChanges, true,
        "a placement mutation marks state dirty (via _markPlacementsDirty)");

    await h.component._runValidateAction();

    assert.equal(saveCalls, 1, "the gate forced exactly one save before validating");
    assert.equal(validateCalls, 1, "validate ran only after the save resolved");
    assert.deepEqual(callOrder, ["save", "validate"],
        "save must complete BEFORE validate fires, never the other way round");
    assert.equal(h.component.state.unsavedChanges, false,
        "a successful save clears the dirty flag");
    h.destroy();
});

test("validate is SKIPPED entirely when the forced save fails — never blesses the stale server copy", async () => {
    let validateCalls = 0;
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => ({ error: "boom", detail: "Save rejected" }),
        "/web/dataset/call_kw": (params) => {
            if (params.method === "action_validate_production") {
                validateCalls++;
                return { type: "ir.actions.client", tag: "display_notification", params: {} };
            }
            throw new Error("unexpected call_kw: " + params.method);
        },
    });

    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();
    assert.equal(h.component.state.unsavedChanges, true);

    await h.component._runValidateAction();

    assert.equal(validateCalls, 0,
        "action_validate_production must NEVER fire when the forced save failed");
    assert.equal(h.component.state.unsavedChanges, true,
        "the flag stays dirty — the save genuinely did not persist");
    const err = h.component._toastStore.toasts.find((t) => t.type === "danger");
    assert.ok(err, "the save failure's own toast surfaced the reason");
    h.destroy();
});

test("a save-rejection response (ok:false) also skips validate, same as a thrown save error", async () => {
    let validateCalls = 0;
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => ({ ok: false, reason: "forbidden" }),
        "/web/dataset/call_kw": (params) => {
            if (params.method === "action_validate_production") {
                validateCalls++;
                return { type: "ir.actions.client", tag: "display_notification", params: {} };
            }
            throw new Error("unexpected call_kw: " + params.method);
        },
    });
    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();

    await h.component._runValidateAction();
    assert.equal(validateCalls, 0);
    assert.equal(h.component.state.unsavedChanges, true);
    h.destroy();
});

test("the auto-save pill renders 'Unsaved changes' while state.unsavedChanges is true, ahead of a stale 'Auto-saved' memory", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => ({ id: 5100, name: "x" }),
    });
    // Prime lastAutoSaveAt with an earlier successful save, exactly like
    // the pre-fix two-state pill would have left it stuck on "Auto-saved"
    // forever afterward regardless of what happens next.
    h.component.state.items = [{ ...LINES[0], name: "Base 24" }];
    await h.component._saveDesign();
    await tick();
    let pill = h.root.querySelector(".o_sbk_autosave");
    assert.ok(pill.classList.contains("is-ok"));
    assert.equal(pill.textContent.trim(), "✓ Auto-saved");

    // Now make a real local edit without saving it.
    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();
    await tick();

    pill = h.root.querySelector(".o_sbk_autosave");
    assert.ok(pill, "pill still renders");
    assert.ok(pill.classList.contains("is-dirty"));
    assert.match(pill.textContent, /Unsaved changes/);
    h.destroy();
});

test("a silently-failing auto-save retry keeps the pill on 'Unsaved changes' (no console toast spam, but a visible signal)", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/save": () => { throw new Error("network down"); },
    });
    h.component._userActed = true;   // T7 gate — auto-save no-ops otherwise
    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();
    assert.equal(h.component.state.unsavedChanges, true);

    await h.component._autoSave();   // fails silently by design (console.warn only)
    await tick();

    assert.equal(h.component.state.unsavedChanges, true,
        "a failed auto-save must never clear the dirty flag");
    const pill = h.root.querySelector(".o_sbk_autosave");
    assert.ok(pill.classList.contains("is-dirty"));
    // No danger toast for the FIRST silent auto-save failure — that's
    // the pre-existing, deliberately-unchanged "silent on auto-save"
    // behavior (contracts/26_save_error_handling.test.mjs covers the
    // dedup/first-failure-toast contract in full; not duplicated here).
    h.destroy();
});

test("a successful arrange (_rearrangeOnServer) also clears unsavedChanges — it IS a persisted server round-trip", async () => {
    const h = await mountHydrated({
        "/southbrook_kitchen/configurator/rearrange": () => ({
            lines: LINES,
            room: { width_in: 96, depth_in: 96, height_in: 96 },
        }),
    });
    h.component._selectCabinet(h.component.state.items[0]);
    h.component._removeSelectedCabinet();
    assert.equal(h.component.state.unsavedChanges, true);

    const ok = await h.component._rearrangeOnServer();
    assert.equal(ok, true);
    assert.equal(h.component.state.unsavedChanges, false,
        "a successful arrange fully replaces state.items from the server response — clean again");
    h.destroy();
});
