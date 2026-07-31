/**
 * Callback contract — every prop <KitchenCanvas> uses to report a
 * canvas-originated event back into the parent
 * (kitchen_configurator.js ~1811-1817) routes to the real, unmodified
 * handler and produces the documented state effect. onWallSelect is
 * covered by contracts/01; this covers the other five.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

test("onSelectItem -> _onCanvasSelect sets state.selected", async () => {
    const h = await mountConfigurator({});
    const item = { id: 42, name: "Base 24" };
    h.canvasProps().onSelectItem(item);
    // owl's useState() wraps assigned objects in a reactive Proxy, so
    // state.selected is a proxy OVER `item`, not `item` itself — assert
    // the data, not reference identity (Object.is would always fail).
    assert.deepEqual({ ...h.component.state.selected }, item);
});

test("onMoveItem -> _onCanvasMove pins the item, recomputes layout, notifies, queues autosave", async () => {
    const h = await mountConfigurator({});
    const item = { id: 1, name: "Base 24", layout_key: "k1", pinned: false };
    h.component.state.items = [item];
    h.canvasProps().onMoveItem({ item, x_position_in: 36, pinnable: true });
    assert.equal(item.pinned, true, "pinnable move sets item.pinned");
    // SBK-030 — local toast (see 02_persistence_reload for the rationale).
    assert.equal(h.component._toastStore.toasts.length, 1);
    assert.match(h.component._toastStore.toasts[0].message, /Moved .* to 36/);
    assert.ok(h.component._autoSaveTimer, "_queueAutoSave armed the debounce timer");
    // The real 3s debounce timer would otherwise fire well after this
    // test (and this file's process) moves on, hitting an unregistered
    // route in whatever test's rpc shim state happens to be current at
    // that moment — clear it, mirroring onWillUnmount's own cleanup.
    clearTimeout(h.component._autoSaveTimer);
});

test("onMoveItem with pinnable=false does not pin, still notifies", async () => {
    const h = await mountConfigurator({});
    const item = { id: 2, name: "Base 30", layout_key: "k2", pinned: false };
    h.component.state.items = [item];
    h.canvasProps().onMoveItem({ item, x_position_in: 12, pinnable: false });
    assert.equal(item.pinned, false);
    assert.equal(h.component._toastStore.toasts.length, 1);
    clearTimeout(h.component._autoSaveTimer);
});

test("onResizeRoom -> _onCanvasResize updates room width and refreshes layout when not inFlight", async () => {
    const h = await mountConfigurator({});
    const before = h.rpcCalls("/southbrook_kitchen/configurator/layout").length;
    h.canvasProps().onResizeRoom(60, false);
    assert.equal(h.component.state.room.width_in, 60);
    // _refreshLayout() is awaited internally by a .then() chain, not by
    // the callback itself (fire-and-forget from the canvas's point of
    // view) — the important, synchronously-observable contract is that
    // the room width state updated immediately.
    await new Promise((r) => setTimeout(r, 10));
    const after = h.rpcCalls("/southbrook_kitchen/configurator/layout").length;
    assert.ok(after > before, "a non-inFlight resize re-triggers /layout");
});

test("onResizeRoom with inFlight=true updates width but does not re-trigger /layout", async () => {
    const h = await mountConfigurator({});
    const before = h.rpcCalls("/southbrook_kitchen/configurator/layout").length;
    h.canvasProps().onResizeRoom(72, true);
    assert.equal(h.component.state.room.width_in, 72);
    await new Promise((r) => setTimeout(r, 10));
    const after = h.rpcCalls("/southbrook_kitchen/configurator/layout").length;
    assert.equal(after, before, "an in-flight drag does not re-trigger /layout on every frame");
});

test("onViewChange -> _onCanvasViewChange updates state.view", async () => {
    const h = await mountConfigurator({});
    assert.equal(h.component.state.view, "iso");
    h.canvasProps().onViewChange("top");
    assert.equal(h.component.state.view, "top");
});

test("onReady -> _onCanvasReady stores the imperative canvas API handle", async () => {
    const h = await mountConfigurator({});
    const api = { setView() {}, zoomBy() {}, computeDropX() {} };
    h.canvasProps().onReady(api);
    assert.equal(h.component._canvasApi, api);
});
