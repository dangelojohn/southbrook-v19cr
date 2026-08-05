/**
 * C-3 fix (2026-08-05, per-wall fit check — reported by three
 * independent E2E rounds) — regression coverage for
 * kitchen_configurator.js's `_addCabinetFromProduct` over-length gate.
 *
 * Before this fix:
 *   (a) `_currentRunWidthIn` only ever measured the BACK wall, and the
 *       refusal always compared against `room.width_in` — a left/right
 *       wall run got ZERO validation (an over-length add there always
 *       silently "fit"), and the error text named numbers matching no
 *       actual wall whenever it fired for a non-back item at all.
 *   (b) a new item's along-run coordinate was hardcoded into
 *       `x_position_in`/`z_position_in=0` regardless of which wall it
 *       landed on — a left/right-wall add always started at the wall's
 *       own origin (z=0), landing on top of whatever (including a
 *       corner cell) was already there, instead of after it.
 *   (c) `wallOverride || activeWall || BACK` precedence: see
 *       contracts/09_drag_to_wall_convergence.test.mjs for the dedicated
 *       precedence-flip coverage; not repeated here.
 *
 * This file exercises (a) and (b): the generalized `_runWidthIn`/
 * `_wallRunLengthIn` gate on a LEFT wall, the wall-naming message, and
 * the "Add anyway" retry landing at the end of that wall's run rather
 * than at its origin.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

const B24 = {
    product_id: 24, name: "B24 Base Cabinet", cabinet_type: "base", sku: "B24",
    width_in: 24, height_in: 34.5, depth_in: 24, price: 180,
    available_qty: 10, material: "white_melamine",
};

async function mountEmptyRoom() {
    // Default room is 96"x96" (width_in x depth_in) — see
    // kitchen_configurator.js setup(). A left/right wall's run is
    // bounded by depth_in, exactly like a back/front wall is bounded by
    // width_in — both are 96 here, so the numbers mirror the existing
    // back-wall toast-discipline fixtures on purpose.
    return mountConfigurator({
        rpcRoutes: {
            "/southbrook_kitchen/configurator/products": () => ({
                channel: null, products: [B24],
            }),
        },
    });
}

test("a left-wall run now gets real fit validation (previously always silently fit)", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c.state.activeWall = "left";
    // 4 x 24in = 96in exactly fills the 96in-deep left wall — must NOT refuse.
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    assert.equal(c.state.items.length, 4, "the wall-filling four must all succeed");
    assert.equal(
        c._toastStore.toasts.find((t) => /won't fit/i.test(t.message)), undefined,
        "exactly filling the wall must not refuse");

    // The 5th needs 120in on a 96in-deep wall — must now be refused
    // (pre-fix: left/right walls got zero validation, this always
    // silently succeeded).
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 4, "the over-length 5th left-wall add must be REFUSED");

    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal, "a refusal toast was shown for a LEFT-wall overflow");
    h.destroy();
});

test("the per-wall fit message names the ACTUAL target wall and that wall's own bound, not the back wall's", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c.state.activeWall = "left";
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);

    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal);
    assert.match(refusal.message, /left wall/i,
        "the message must name the wall the cabinet actually targeted");
    assert.match(refusal.message, /needs 120/,
        "the required-length number must reflect the LEFT wall's own run (4x24 existing + 24 candidate)");
    assert.match(refusal.message, /wall is 96/,
        "the limit number must be THIS wall's own bound (room.depth_in for left/right), not room.width_in");
    // Never a leftover back-wall framing ("room is") on a per-wall message.
    assert.doesNotMatch(refusal.message, /room is/i);
    h.destroy();
});

test("'Add anyway' on a refused LEFT-wall add lands AT THE END of that wall's existing run, not at the origin on top of whatever's already there", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c.state.activeWall = "left";
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    c._addCabinetFromProduct(B24);   // refused — 5th needs 120in on a 96in wall
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 4);

    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal);
    c._runToastAction(refusal, refusal.actions[0]);   // "Add anyway"
    clearTimeout(c._autoSaveTimer);

    assert.equal(c.state.items.length, 5, "opting in must actually add the cabinet");
    const added = c.state.items[c.state.items.length - 1];
    assert.equal(added.wall, "left");
    // The along-run axis for a left/right wall is z_position_in
    // (_alongWallAxisField) — the fix: this must be the END of the
    // existing 4x24=96in run, never 0 (the pre-fix defect: every new
    // side-wall item was hardcoded to z=0 regardless of what already
    // occupied that wall).
    assert.equal(added.z_position_in, 96,
        "must append after the existing run, not restart at the wall's own origin");
    // x_position_in is not this wall's along-run axis; must stay a
    // neutral 0, never inherit a stray floor-X value.
    assert.equal(added.x_position_in, 0);
    h.destroy();
});

test("consecutive plain (non-refused) LEFT-wall adds append along z, never stack at z=0", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    c.state.activeWall = "left";
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);

    assert.equal(c.state.items.length, 2);
    const [first, second] = c.state.items;
    assert.equal(first.wall, "left");
    assert.equal(second.wall, "left");
    assert.equal(first.z_position_in, 0, "the first item on an empty wall starts at the wall origin");
    assert.equal(second.z_position_in, 24,
        "the second item must append after the first (its width), not restack at z=0");
    h.destroy();
});

test("back-wall behavior is byte-identical to the pre-fix gate (regression pin: _runWidthIn(BACK, …) === old _currentRunWidthIn(…))", async () => {
    const h = await mountEmptyRoom();
    const c = h.component;
    // No activeWall set -> defaults to BACK, exactly as every pre-existing
    // 19_toast_discipline.test.mjs back-wall scenario already exercises.
    for (let i = 0; i < 4; i++) {
        c._addCabinetFromProduct(B24);
        clearTimeout(c._autoSaveTimer);
    }
    c._addCabinetFromProduct(B24);
    clearTimeout(c._autoSaveTimer);
    assert.equal(c.state.items.length, 4);
    const refusal = c._toastStore.toasts.find((t) => /won't fit/i.test(t.message));
    assert.ok(refusal);
    assert.match(refusal.message, /back wall/i);
    assert.match(refusal.message, /needs 120/);
    assert.match(refusal.message, /wall is 96/);
    h.destroy();
});
