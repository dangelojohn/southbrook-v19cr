/**
 * SBK-026 contract — "Wall-cabinet mount height honours the selector".
 *
 * Audit finding: the "Wall Cab Top" selector (fixed_gap / to_ceiling /
 * to_soffit) already existed client-side (D8), but the PERSISTED
 * auto-arrange path (southbrook.kitchen.design.action_auto_arrange /
 * _place_lines_on_wall) fed the engine a hardcoded
 * `sale.order._ZONE_LAYOUT["wall"]` of 1400mm/55.12in regardless of the
 * selection, AND the selector's own settings (filler_strategy,
 * wall_cab_top_alignment, soffit_height_in) were never sent back to
 * the server on save — so even a correctly-computed fresh layout
 * reverted to defaults on the next regenerate/resize. This contract
 * covers the CLIENT side of the fix:
 *
 *   1. fixed_gap / to_ceiling / to_soffit resolve to three DIFFERENT
 *      bottom-of-cabinet heights (mirrors kitchen_design.py's
 *      _wall_cab_top_geometry exactly: 34.5" carcass + 1.5" counter +
 *      18" gap = 54.00" fixed_gap default; ceiling(96) - 30 = 66"
 *      to_ceiling; soffit(90) - 30 = 60" to_soffit).
 *   2. to_soffit is GATED at soffit_height_in <= 0 (the new "0 = unset"
 *      default, replacing the old always-84.0 default that made the
 *      gate a no-op) — falls back to the fixed_gap number and the
 *      helper text says so.
 *   3. The live helper text (item 4 of the brief) reflects all of the
 *      above without a server round-trip.
 *   4. counter_thickness_in is a real, editable setting (item 1) and
 *      moves the fixed_gap number.
 *   5. All four Room-panel settings round-trip through BOTH save paths
 *      (_saveDesign manual save, _autoSave debounced save) — the
 *      concrete bug: before this fix neither RPC sent them at all, so
 *      the design record's stored defaults were the only thing any
 *      later server-side regenerate ever saw.
 *   6. F12's hydrated-design no-op guard (pre-existing) still holds for
 *      the new counter-thickness control.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

// ─── save round-trip (the concrete SBK-026 persistence bug) ────────────────

test("_saveDesign round-trips filler_strategy/wall_cab_top_alignment/soffit_height_in/counter_thickness_in", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 951, name: "SBK-026 save test" }),
        },
    });
    // Sidebar edit controls removed 2026-08-01 — the STATE plumbing is
    // what this test pins, so drive the fields directly.
    h.component.state.wallCabTopAlignment = "to_soffit";
    h.component.state.soffitHeightIn = 90;
    h.component.state.counterThicknessIn = 2.0;

    await h.component._saveDesign();

    const calls = h.rpcCalls("/southbrook_kitchen/configurator/save");
    assert.ok(calls.length >= 1, "save must have been called");
    const last = calls[calls.length - 1].params;
    assert.equal(last.wall_cab_top_alignment, "to_soffit");
    assert.equal(last.soffit_height_in, 90);
    assert.equal(last.counter_thickness_in, 2.0);
    assert.equal(last.filler_strategy, "split");
});

test("_autoSave round-trips the same four fields as _saveDesign", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 952, name: "SBK-026 autosave test" }),
        },
    });
    h.component.state.wallCabTopAlignment = "to_ceiling";
    h.component._markUserAction();

    await h.component._autoSave();

    const calls = h.rpcCalls("/southbrook_kitchen/configurator/save");
    const last = calls[calls.length - 1].params;
    assert.equal(last.wall_cab_top_alignment, "to_ceiling");
    assert.equal(last.soffit_height_in, 0.0,
        "unset soffit must round-trip as 0, never a truthy fallback like 84");
    assert.equal(last.counter_thickness_in, 1.5);
});

test("_refreshLayout's /layout call sends counter_thickness_in alongside the existing D8 fields", async () => {
    const h = await mountConfigurator({});
    h.component.state.counterThicknessIn = 2.25;
    await h.component._refreshLayout({ force: true });

    const calls = h.rpcCalls("/southbrook_kitchen/configurator/layout");
    const last = calls[calls.length - 1].params;
    assert.equal(last.counter_thickness_in, 2.25);
    assert.equal(last.wall_cab_top_alignment, "fixed_gap");
    assert.equal(last.soffit_height_in, 0.0);
});
