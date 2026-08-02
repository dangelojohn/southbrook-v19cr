/**
 * Task 38 (client hardening batch, corner-remediation 2026-07-30) —
 * `_autoSave()` in-flight-drop fix.
 *
 * `_autoSave()` used to bail with a bare `if (this.state.autoSaving)
 * return;` whenever a save fired while a previous one was still in
 * flight — silently discarding it. The in-flight request already
 * snapshotted `state.items`/`state.room` at ITS OWN start, so a
 * mutation that landed and re-queued a save mid-flight would never get
 * persisted until some LATER, unrelated edit happened to queue another
 * one.
 *
 * The fix: mark `this._autoSavePending = true` instead of dropping; the
 * in-flight save's own `finally` block clears the flag and runs ONE
 * follow-up `_autoSave()` once it settles — latest-wins, no queue depth
 * beyond one pending, matching `_queueAutoSave()`'s own debounce
 * semantics.
 *
 * Uses the rpc shim's documented direct-import path (see
 * shims/web_core_rpc.mjs's header comment) to control exactly when the
 * in-flight `/save` RPC resolves, so the mid-flight second call can be
 * observed and asserted on before the first completes.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { __onRoute } from "../shims/web_core_rpc.mjs";

const LINE = {
    id: 701, layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
    width_in: 24, height_in: 34.5, depth_in: 24,
    x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
};

function mountHydrated() {
    return mountConfigurator({
        actionParams: { design_id: 3803 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "T38 autosave design", lines: [LINE],
            }),
        },
    });
}

function controllableSaveRoute() {
    const resolvers = [];
    let saveCalls = 0;
    __onRoute("/southbrook_kitchen/configurator/save", () => {
        saveCalls++;
        return new Promise((resolve) => resolvers.push(resolve));
    });
    return { resolvers, callCount: () => saveCalls };
}

test("a save requested while one is already in flight is marked pending, not dropped, and runs once the first completes", async () => {
    const h = await mountHydrated();
    const c = h.component;
    c.state.items = [{ ...LINE }];
    const { resolvers, callCount } = controllableSaveRoute();

    const p1 = c._autoSave();                 // first save: starts immediately
    assert.equal(c.state.autoSaving, true, "first save is in flight");
    assert.equal(callCount(), 1);

    const p2 = c._autoSave();                 // fires WHILE the first is in flight
    await p2;                                  // the drop-path resolves immediately (no RPC)
    assert.equal(callCount(), 1,
        "the mid-flight call must NOT fire a second RPC immediately");
    assert.equal(c._autoSavePending, true,
        "it must be marked pending instead of silently dropped");

    // Let the first save resolve.
    resolvers[0]({ id: 3803, name: "T38 autosave design", placed: [] });
    await p1;

    // The pending follow-up must have run automatically, exactly once.
    assert.equal(callCount(), 2,
        "the follow-up save must fire once the first completes");
    assert.equal(c._autoSavePending, false,
        "the pending flag is cleared once its follow-up has been queued");
    assert.equal(c.state.autoSaving, true,
        "the follow-up save is now itself in flight");

    // Clean up the follow-up so nothing is left hanging.
    resolvers[1]({ id: 3803, name: "T38 autosave design", placed: [] });
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(c.state.autoSaving, false);
    assert.equal(c._autoSavePending, false);
    h.destroy();
});

test("multiple saves fired mid-flight collapse to a single follow-up (latest-wins, no queue depth beyond one)", async () => {
    const h = await mountHydrated();
    const c = h.component;
    c.state.items = [{ ...LINE }];
    const { resolvers, callCount } = controllableSaveRoute();

    const p1 = c._autoSave();
    assert.equal(callCount(), 1);

    // Three more requests arrive while the first is still in flight.
    await c._autoSave();
    await c._autoSave();
    await c._autoSave();
    assert.equal(callCount(), 1, "none of the three mid-flight calls fire their own RPC");
    assert.equal(c._autoSavePending, true);

    resolvers[0]({ id: 3803, name: "T38 autosave design", placed: [] });
    await p1;

    // Exactly ONE follow-up, not three.
    assert.equal(callCount(), 2,
        "three collapsed pending requests must still produce only ONE follow-up save");

    resolvers[1]({ id: 3803, name: "T38 autosave design", placed: [] });
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(callCount(), 2, "settling the follow-up must not spawn a further one");
    h.destroy();
});

test("no regression: a save requested with nothing in flight still runs immediately (the common case)", async () => {
    const h = await mountHydrated();
    const c = h.component;
    c.state.items = [{ ...LINE }];
    __onRoute("/southbrook_kitchen/configurator/save",
        () => ({ id: 3803, name: "T38 autosave design", placed: [] }));

    await c._autoSave();
    assert.equal(c.state.autoSaving, false);
    assert.equal(c._autoSavePending, false);
    h.destroy();
});
