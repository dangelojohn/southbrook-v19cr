/**
 * Task 12 (corner-remediation 2026-07-30, SBK-021/022) — client-side
 * half of "room bounds, line cap, honest field errors".
 *
 * Server-side bounds (models/kitchen_design.py's ROOM_WIDTH_MIN_IN/
 * MAX_IN etc. + the @api.constrains that enforces them, and
 * controllers/main.py's save_design pre-check + the /layout 120-line
 * cap) are covered by the Python test suite. This file locks the three
 * client-side sub-claims V5-partc confirmed:
 *
 *   5. (historical) "+24in mutates Width, not the active wall" — the
 *      ±24in stretch buttons were REMOVED 2026-08-01 (John); their
 *      axis-aware behavior note is kept for archaeology only. Was:
 *      to unconditionally write room.width_in regardless of
 *      state.activeWall. Fixed via _stretchAxisField(): back/front/no
 *      wall -> width_in, left/right -> depth_in.
 *   1. "no max bounds on room width/depth/height, client" — _changeRoom
 *      used to only Math.max() a floor, no ceiling at all. Fixed:
 *      clamps to the same 12-480/12-480/84-144 range the server
 *      enforces, PLUS sets an inline state.roomDimErrors[field] message
 *      (rendered next to the field, not a toast).
 *   6. "cabinet-width field shows no visible validation message" —
 *      _updateSelectedWidth used to bare-early-return on an out-of-
 *      range value. Fixed: sets state.selectedWidthError instead of
 *      silently doing nothing.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";
import { tick } from "../harness/tick.mjs";

function clearAutoSave(h) {
    if (h.component._autoSaveTimer) clearTimeout(h.component._autoSaveTimer);
}

// ─── _changeRoom — max clamp + inline error ────────────────────────────────

test("_changeRoom clamps an over-max width to 480 and sets an inline error", async () => {
    const h = await mountConfigurator({});
    await h.component._changeRoom("width_in", "99999");
    assert.equal(h.component.state.room.width_in, 480,
        "480 is the server's own ROOM_WIDTH_MAX_IN — never left unclamped");
    assert.ok(h.component.state.roomDimErrors.width_in,
        "an out-of-range value must set a visible message, not silently clamp");
    assert.match(h.component.state.roomDimErrors.width_in, /480/);
    clearAutoSave(h);
});

test("_changeRoom clears the width error once a subsequent value is back in range", async () => {
    const h = await mountConfigurator({});
    await h.component._changeRoom("width_in", "99999");
    assert.ok(h.component.state.roomDimErrors.width_in, "sanity: error armed");
    await h.component._changeRoom("width_in", "200");
    assert.equal(h.component.state.room.width_in, 200);
    assert.equal(h.component.state.roomDimErrors.width_in, "",
        "a valid edit must clear the stale error");
    clearAutoSave(h);
});

test("_changeRoom clamps depth to 480 and height to 144, independently of width", async () => {
    const h = await mountConfigurator({});
    await h.component._changeRoom("depth_in", "50000");
    assert.equal(h.component.state.room.depth_in, 480);
    assert.ok(h.component.state.roomDimErrors.depth_in);
    clearAutoSave(h);

    await h.component._changeRoom("height_in", "9000");
    assert.equal(h.component.state.room.height_in, 144);
    assert.ok(h.component.state.roomDimErrors.height_in);
    clearAutoSave(h);
});

test("_changeRoom still floors an under-min value (12/12/84) and flags it too", async () => {
    const h = await mountConfigurator({});
    await h.component._changeRoom("width_in", "1");
    assert.equal(h.component.state.room.width_in, 12);
    assert.ok(h.component.state.roomDimErrors.width_in,
        "the pre-existing floor behavior must ALSO surface a message now "
        + "(pre-fix it silently clamped with zero feedback)");
    clearAutoSave(h);

    await h.component._changeRoom("height_in", "10");
    assert.equal(h.component.state.room.height_in, 84);
    clearAutoSave(h);
});

test("_changeRoom renders the inline error text next to the field in the DOM", async () => {
    const h = await mountConfigurator({});
    await h.component._changeRoom("width_in", "99999");
    await tick();
    const errs = [...h.root.querySelectorAll(".o_sbk_field_error")];
    assert.ok(errs.length >= 1, "at least one inline field-error node must render");
    assert.ok(errs.some(el => /480/.test(el.textContent)),
        "the rendered text must mention the actual bound, not a generic message");
    clearAutoSave(h);
});

// ─── _updateSelectedWidth — inline error instead of a silent no-op ────────

test("_updateSelectedWidth sets an inline error and does not mutate the item on an out-of-range value", async () => {
    const h = await mountConfigurator({});
    const item = {
        layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        wall: "back", price: 500,
    };
    h.component.state.items = [item];
    h.component.state.selected = item;

    h.component._updateSelectedWidth("999");

    assert.equal(item.width_in, 24, "an out-of-range value must NOT mutate the item");
    assert.ok(h.component.state.selectedWidthError,
        "SBK-021/022 claim 6 — must set a visible message, not bare-early-return");
    assert.match(h.component.state.selectedWidthError, /6.*48|48.*6/);
});

test("_updateSelectedWidth clears the error and mutates on a valid value", async () => {
    const h = await mountConfigurator({});
    const item = {
        layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        wall: "back", price: 500,
    };
    h.component.state.items = [item];
    h.component.state.selected = item;

    h.component._updateSelectedWidth("999");
    assert.ok(h.component.state.selectedWidthError, "sanity: error armed");

    h.component._updateSelectedWidth("30");
    assert.equal(item.width_in, 30);
    assert.equal(h.component.state.selectedWidthError, "",
        "a valid edit must clear the stale error");
    clearAutoSave(h);
});

test("selecting a different cabinet clears a stale width error", async () => {
    const h = await mountConfigurator({});
    const itemA = {
        layout_key: "kA", cabinet_type: "base", product_name: "Base 24",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        wall: "back", price: 500,
    };
    const itemB = { ...itemA, layout_key: "kB", x_position_in: 30 };
    h.component.state.items = [itemA, itemB];
    h.component.state.selected = itemA;
    h.component._updateSelectedWidth("999");
    assert.ok(h.component.state.selectedWidthError, "sanity: error armed on A");

    h.component._selectCabinet(itemB);
    assert.equal(h.component.state.selectedWidthError, "",
        "switching selection must not carry the old field's error along");
});

test("_updateSelectedWidth's inline error renders in the DOM next to the Width field", async () => {
    const h = await mountConfigurator({});
    const item = {
        layout_key: "k1", cabinet_type: "base", product_name: "Base 24",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0,
        wall: "back", price: 500,
    };
    h.component.state.items = [item];
    h.component.state.selected = item;
    await tick();

    h.component._updateSelectedWidth("999");
    await tick();

    const errs = [...h.root.querySelectorAll(".o_sbk_field_error")];
    assert.ok(errs.length >= 1, "the drawer must render an inline error node");
});
