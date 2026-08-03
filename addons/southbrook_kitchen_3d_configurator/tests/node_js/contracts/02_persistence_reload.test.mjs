/**
 * PR2/PR2.5/PR2.5a contract — persistence after reload.
 *   1. `_hydrateFromDesign` populates state.items from a mocked
 *      /load_design_lines and never calls /layout (the destructive-
 *      reopen bug PR2.5 fixed: /layout is a generator that would
 *      silently replace the saved fill).
 *   2. Fail-loud: a rejected load_design_lines sets `_hydrationFailed`
 *      and disables _saveDesign() (no /save RPC, danger notification).
 *   3. Param intake: props.action.params.design_id is read directly.
 *   4. URL-restore fallback: params.actionStack's frame is used to
 *      recover a design_id ONLY when its `model` is genuinely
 *      "southbrook.kitchen.design" with a numeric resId (model-gated —
 *      a "bare" reload whose preceding frame is some other model must
 *      NOT be treated as a design id).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

const LINES = [
    { id: 501, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
      width_in: 24, height_in: 34.5, depth_in: 24,
      x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0 },
    { id: 502, layout_key: "k2", cabinet_type: "wall", product_name: "Wall 24",
      width_in: 24, height_in: 30, depth_in: 12,
      x_position_in: 0, y_position_in: 54, z_position_in: 0, rotation_deg: 0 },
];

test("_hydrateFromDesign populates state.items from /load_design_lines and never calls /layout", async () => {
    let hydrateCalls = 0;
    const h = await mountConfigurator({
        actionParams: { design_id: 83 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                hydrateCalls++;
                assert.equal(params.design_id, 83);
                return { room: { width_in: 96, depth_in: 96, height_in: 96 },
                          design_name: "Design 83", lines: LINES };
            },
        },
    });
    assert.equal(hydrateCalls, 1);
    assert.equal(h.component.state.items.length, 2);
    assert.equal(h.component.state.items[0].name, "Base 24", "name aliased from product_name");
    assert.equal(h.component._hydratedFromDesign, true);
    assert.equal(
        h.rpcCalls("/southbrook_kitchen/configurator/layout").length, 0,
        "/layout must never run for a hydrated (reopened) design — it would silently discard the saved fill",
    );
});

test("fail-loud: a rejected load_design_lines sets _hydrationFailed and disables save", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 84 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => {
                throw new Error("boom: transient RPC failure");
            },
        },
    });
    assert.equal(h.component._hydrationFailed, true);
    assert.equal(h.component.state.errorCode, "HYDRATION_FAILED");

    // Manual save must refuse — no /save RPC, sticky danger toast —
    // exactly the overwrite class PR2.5a closed (a failed hydrate must
    // never let the next save full-replace the saved design).
    // SBK-030 — this component's toasts route through its own local
    // ToastStore now, not the (still-mocked but unused) `notification`
    // service — see state.toastView / _toastStore.
    await h.component._saveDesign();
    assert.equal(h.rpcCalls("/southbrook_kitchen/configurator/save").length, 0);
    assert.equal(h.component._toastStore.toasts.length, 1);
    assert.equal(h.component._toastStore.toasts[0].type, "danger");
    assert.equal(h.component._toastStore.toasts[0].sticky, true);

    // The debounced auto-save path is gated the same way.
    h.component._queueAutoSave();
    assert.equal(h.component._autoSaveTimer, null, "_queueAutoSave is a no-op after a failed hydration");
});

test("param intake: props.action.params.design_id is read directly", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 91 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({ lines: [] }),
        },
    });
    assert.equal(h.component.state.designId, 91);
});

test("URL-restore fallback: actionStack recovers design_id when params.design_id is absent", async () => {
    const h = await mountConfigurator({
        actionParams: {
            actionStack: [
                { model: "southbrook.kitchen.design", resId: 77 },
                { action: "southbrook_kitchen_configurator", active_id: 77 },
            ],
        },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                assert.equal(params.design_id, 77);
                return { lines: [] };
            },
        },
    });
    assert.equal(h.component.state.designId, 77);
});

test("URL-restore fallback is model-gated: a non-design preceding frame is never treated as a design id", async () => {
    const h = await mountConfigurator({
        actionParams: {
            // A "bare" reload of southbrook_room.py's action_open_kitchen_3d:
            // the preceding actionStack frame is the ROOM, not a design.
            actionStack: [
                { model: "southbrook.room", resId: 12 },
                { action: "southbrook_kitchen_configurator", active_id: 12 },
            ],
        },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/layout": () => ({
                items: [], summary: { base_count: 0, wall_count: 0, total: 0, price: 0, remainder_in: 0 },
            }),
        },
    });
    assert.equal(h.component.state.designId, null, "resId:12 must NOT be adopted as a design id");
    assert.equal(
        h.rpcCalls("/southbrook_kitchen/configurator/load_design_lines").length, 0,
        "no hydration attempt when no real design id was recovered",
    );
});

test("URL-restore fallback STILL recovers the genuine model-less action-<id> shape", async () => {
    // SBK-050 regression guard. The model-less check added alongside this
    // test must not break the case the fallback exists for: a deep link to
    // the persisted ir.actions.client record, e.g.
    //   /odoo/action-1460/6/southbrook_kitchen_configurator
    // router.js pushes THREE frames for that path and NONE carries a
    // `model` key, because an `action-<id>` segment has no model of its own.
    // Without a test for this, the guard could be "fixed" by deleting the
    // fallback and everything would still look green.
    const h = await mountConfigurator({
        actionParams: {
            actionStack: [
                { action: 1460 },
                { action: 1460, resId: 6 },
                { active_id: 6, action: "southbrook_kitchen_configurator" },
            ],
        },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": (params) => {
                assert.equal(params.design_id, 6);
                return { lines: [] };
            },
        },
    });
    assert.equal(
        h.component.state.designId, 6,
        "the model-less action-<id> shape is exactly what this fallback is for",
    );
});

test("URL-restore fallback never treats resId:'new' as a numeric design id", async () => {
    const h = await mountConfigurator({
        actionParams: {
            actionStack: [
                { model: "southbrook.kitchen.design", resId: "new" },
                { action: "southbrook_kitchen_configurator" },
            ],
        },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/layout": () => ({
                items: [], summary: { base_count: 0, wall_count: 0, total: 0, price: 0, remainder_in: 0 },
            }),
        },
    });
    assert.equal(h.component.state.designId, null);
});
