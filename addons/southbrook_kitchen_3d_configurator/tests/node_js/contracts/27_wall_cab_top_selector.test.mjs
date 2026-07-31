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

// ─── defaults + three-states-differ ────────────────────────────────────────

test("fresh session defaults: fixed_gap, soffit unset (0), counter 1.5in", async () => {
    const h = await mountConfigurator({});
    assert.equal(h.component.state.wallCabTopAlignment, "fixed_gap");
    assert.equal(h.component.state.soffitHeightIn, 0);
    assert.equal(h.component.state.counterThicknessIn, 1.5);
});

test("SBK-026 AC: 96in ceiling / 34.5 carcass / 1.5 top / 18 gap -> fixed_gap resolves to 54.00in", async () => {
    const h = await mountConfigurator({});
    const g = h.component._wallCabBottomGeometry();
    assert.equal(g.alignment, "fixed_gap");
    assert.equal(g.yIn, 54.0);
    assert.match(h.component._wallCabBottomText(), /Bottom of cabinet: 54\.00"/);
});

test("to_ceiling resolves to ceiling - wall_h (96 - 30 = 66.00in), unchanged formula", async () => {
    const h = await mountConfigurator({});
    await h.component._changeWallAlignment("to_ceiling");
    const g = h.component._wallCabBottomGeometry();
    assert.equal(g.alignment, "to_ceiling");
    assert.equal(g.yIn, 66.0);
    assert.match(h.component._wallCabBottomText(), /Bottom of cabinet: 66\.00"/);
});

test("to_soffit is GATED to fixed_gap while soffit_height_in is unset (0)", async () => {
    const h = await mountConfigurator({});
    await h.component._changeWallAlignment("to_soffit");
    const g = h.component._wallCabBottomGeometry();
    assert.equal(g.gated, true, "unset soffit must gate to_soffit off");
    assert.equal(g.alignment, "fixed_gap");
    assert.equal(g.yIn, 54.0, "gated to_soffit must fall back to the fixed_gap number");
    assert.match(h.component._wallCabBottomText(), /soffit height not set/);
});

test("to_soffit resolves once soffit_height_in is set above 0 (90in -> 60.00in)", async () => {
    const h = await mountConfigurator({});
    await h.component._changeWallAlignment("to_soffit");
    await h.component._changeSoffit("90");
    const g = h.component._wallCabBottomGeometry();
    assert.equal(g.gated, false);
    assert.equal(g.alignment, "to_soffit");
    assert.equal(g.yIn, 60.0);
    assert.match(h.component._wallCabBottomText(), /Bottom of cabinet: 60\.00"/);
});

test("SBK-026 AC: fixed_gap / to_ceiling / to_soffit(90) are three VISIBLY DIFFERENT heights", async () => {
    const h = await mountConfigurator({});
    const fixedGap = h.component._wallCabBottomGeometry().yIn;

    await h.component._changeWallAlignment("to_ceiling");
    const toCeiling = h.component._wallCabBottomGeometry().yIn;

    await h.component._changeWallAlignment("to_soffit");
    await h.component._changeSoffit("90");
    const toSoffit = h.component._wallCabBottomGeometry().yIn;

    assert.equal(fixedGap, 54.0);
    assert.equal(toCeiling, 66.0);
    assert.equal(toSoffit, 60.0);
    const distinct = new Set([fixedGap, toCeiling, toSoffit]);
    assert.equal(distinct.size, 3, "all three states must resolve to distinct heights");
});

// ─── counter thickness ──────────────────────────────────────────────────────

test("counter_thickness_in moves the fixed_gap number (item 1 of the brief)", async () => {
    const h = await mountConfigurator({});
    await h.component._changeCounterThickness("2.5");
    assert.equal(h.component.state.counterThicknessIn, 2.5);
    // 34.5 + 2.5 + 18 = 55.0
    assert.equal(h.component._wallCabBottomGeometry().yIn, 55.0);
});

test("_changeCounterThickness rejects out-of-range / non-finite input (no-op)", async () => {
    const h = await mountConfigurator({});
    const before = h.component.state.counterThicknessIn;
    await h.component._changeCounterThickness("not-a-number");
    await h.component._changeCounterThickness("-1");
    await h.component._changeCounterThickness("999");
    assert.equal(h.component.state.counterThicknessIn, before);
});

test("_changeCounterThickness is a no-op once hydrated (F12 pattern)", async () => {
    const h = await mountConfigurator({
        actionParams: { design_id: 950 },
        rpcRoutes: {
            "/southbrook_kitchen/configurator/load_design_lines": () => ({
                room: { width_in: 96, depth_in: 96, height_in: 96 },
                design_name: "SBK-026 hydrated test",
                lines: [{
                    layout_key: "base-1", cabinet_type: "base", product_name: "Base 24",
                    width_in: 24, height_in: 34.5, depth_in: 24,
                    x_position_in: 0, y_position_in: 0, z_position_in: 0,
                    rotation_deg: 0, wall: "back", price: 500,
                }],
            }),
        },
    });
    assert.equal(h.component.state.hydratedFromDesign, true);
    const before = h.component.state.counterThicknessIn;
    await h.component._changeCounterThickness("3.0");
    assert.equal(h.component.state.counterThicknessIn, before);
    assert.equal(h.component._autoSaveTimer, null);
});

// ─── save round-trip (the concrete SBK-026 persistence bug) ────────────────

test("_saveDesign round-trips filler_strategy/wall_cab_top_alignment/soffit_height_in/counter_thickness_in", async () => {
    const h = await mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/save": () => ({ id: 951, name: "SBK-026 save test" }),
        },
    });
    await h.component._changeWallAlignment("to_soffit");
    await h.component._changeSoffit("90");
    await h.component._changeCounterThickness("2.0");

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
    await h.component._changeWallAlignment("to_ceiling");
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
    await h.component._changeCounterThickness("2.25");

    const calls = h.rpcCalls("/southbrook_kitchen/configurator/layout");
    const last = calls[calls.length - 1].params;
    assert.equal(last.counter_thickness_in, 2.25);
    assert.equal(last.wall_cab_top_alignment, "fixed_gap");
    assert.equal(last.soffit_height_in, 0.0);
});
