/**
 * Task 38 (client hardening batch, corner-remediation 2026-07-30) —
 * walk-in/customer-assignment visibility fix.
 *
 * `_setCustomer()` always ended with `this._queueAutoSave()`, but
 * `_queueAutoSave()` itself no-ops until the T7 first-action gate
 * (`this._userActed`) is open (see 16_autosave_first_action_gate.test.mjs
 * for that gate's own contract). On a design that was REOPENED and not
 * yet edited, `_userActed` starts false — so assigning the walk-in
 * customer (topbar button, or the validation panel's "Assign walk-in
 * customer" fix action, both of which route through
 * `_useWalkInCustomer()` -> `_setCustomer()`) silently failed to queue a
 * save. The partner id lived only in `state.partnerId` until some LATER,
 * unrelated edit happened to open the gate — so the persistent
 * MISSING_CUSTOMER validation issue kept showing even though the rep had
 * just fixed it.
 *
 * The fix: `_setCustomer()` calls `_markUserAction()` before queueing
 * the auto-save, but only once its own `!partner || !partner.id` guard
 * has already passed — i.e. only when a real partner is actually being
 * set. This test asserts the gate is OPEN and a save gets queued on a
 * freshly reopened (never-edited) design.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

function mountReopenedDesign(rpcRoutes = {}) {
    return mountConfigurator({
        actionParams: { design_id: 3802 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "T38 reopened design",
                lines: [
                    { id: 601, layout_key: "k1", cabinet_type: "base",
                      product_name: "Base 24", width_in: 24, height_in: 34.5,
                      depth_in: 24, x_position_in: 0, y_position_in: 0,
                      z_position_in: 0, rotation_deg: 0 },
                ],
            }),
            ...rpcRoutes,
        },
    });
}

test("a reopened, not-yet-edited design starts with the T7 gate closed", async (t) => {
    const h = await mountReopenedDesign();
    assert.equal(h.component._userActed, false,
        "reopening a design must not itself count as a user action");
    t.after(() => { if (h.component._autoSaveTimer) clearTimeout(h.component._autoSaveTimer); h.destroy(); });
});

test("_setCustomer() on a fresh reopened design opens the T7 gate and queues the auto-save (walk-in fix stays visible)", async (t) => {
    const h = await mountReopenedDesign();
    const c = h.component;
    assert.equal(c._userActed, false, "gate starts closed");

    await c._setCustomer({ id: 4242, name: "Walk-in Customer" });

    assert.equal(c._userActed, true,
        "assigning a customer is a deliberate user action — the T7 gate must open");
    assert.ok(c._autoSaveTimer,
        "the partner assignment must be queued for save, not silently dropped by the T7 gate");
    assert.equal(c.state.partnerId, 4242);
    t.after(() => { if (c._autoSaveTimer) clearTimeout(c._autoSaveTimer); h.destroy(); });
});

test("_useWalkInCustomer() (the topbar button's exact flow) also opens the gate on a fresh reopened design", async (t) => {
    let walkinCalls = 0;
    const h = await mountReopenedDesign({
        "/web/dataset/call_kw": (params) => {
            if (params.method === "get_or_create_walkin_partner") {
                walkinCalls++;
                return 5150;
            }
            throw new Error("unexpected call_kw method: " + params.method);
        },
    });
    const c = h.component;
    assert.equal(c._userActed, false);

    await c._useWalkInCustomer();

    assert.equal(walkinCalls, 1);
    assert.equal(c.state.partnerId, 5150);
    assert.equal(c._userActed, true,
        "the walk-in button's real flow must open the T7 gate too");
    assert.ok(c._autoSaveTimer,
        "walk-in assignment must be queued for save on a never-edited reopened design");
    t.after(() => { if (c._autoSaveTimer) clearTimeout(c._autoSaveTimer); h.destroy(); });
});

test("a null/idless partner passed to _setCustomer is a no-op and does NOT open the gate", async (t) => {
    const h = await mountReopenedDesign();
    const c = h.component;
    await c._setCustomer(null);
    assert.equal(c._userActed, false,
        "an aborted/invalid assignment must not be treated as a genuine user action");
    await c._setCustomer({ name: "no id" });
    assert.equal(c._userActed, false);
    t.after(() => { if (c._autoSaveTimer) clearTimeout(c._autoSaveTimer); h.destroy(); });
});
