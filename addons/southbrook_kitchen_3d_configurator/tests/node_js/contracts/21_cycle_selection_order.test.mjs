/**
 * Task 22 (SBK-036 · corner-remediation-2026-07-30) — ←/→ cycling
 * contract for `_cycleSelection` (kitchen_configurator.js).
 *
 * Audit finding this fixes: "←/→ cycling advances unreliably and
 * repeats items". Root cause: the pre-Task-22 implementation only
 * ever included `cabinet_type === "base" || cabinet_type === "wall"`
 * — tall/corner/appliance cabinets (real, independently clickable
 * placed units — the "Selected Cabinet" drawer is generic across
 * every cabinet_type) were silently unreachable via the keyboard, so
 * a design with any of those mixed in only ever cycled a SUBSET of
 * what was actually on screen. There was also a genuine off-by-one:
 * the very first ArrowLeft press with nothing selected landed on the
 * SECOND-TO-LAST item, not the last one (idx defaulted to -1 for
 * both directions, so `-1 + -1` wrapped one slot short).
 *
 * Review fix (2026-07-30): the first pass of this sorted within-wall
 * on `run_seq`, but BOTH engine corner-insertion paths give EVERY
 * corner design line `run_seq = -1` UNCONDITIONALLY —
 * kitchen_layout_engine.py's corner-node builder hardcodes it, and
 * kitchen_design.py's standalone/derived-line path defaults to that
 * same -1 (`node.get("run_seq", -1)` just reads back the engine's own
 * hardcoded value in the normal case; this is universal, not the
 * narrow "front-right standalone corner" case that call site's own
 * comment describes). `_cycleSelection` now sorts within a wall by
 * POSITION along that wall's run axis instead (x_position_in for
 * back/front, z_position_in for left/right — real, populated data for
 * every cabinet_type including corners, with no sentinel to
 * normalize). Every fixture below uses the REAL corner run_seq value
 * (-1) precisely so a regression back to run_seq-based sorting would
 * fail these tests instead of silently passing on unrealistic data.
 *
 * Mounts the REAL, unmodified kitchen_configurator.js end-to-end via
 * the harness's canonical mount helper (only <KitchenCanvas> — the
 * Three.js/WebGL boundary — is mocked; _cycleSelection itself is pure
 * state.items/state.selected bookkeeping with no canvas dependency).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

// wall then position-along-run then layout_key — the documented
// deterministic order. Deliberately listed here in a DIFFERENT order
// than the expected cycle order, and re-shuffled between tests, so a
// passing test proves the sort is doing the work, not incoming array
// order. The corner (c1) carries run_seq: -1 — the REAL value every
// corner design line gets from both engine insertion paths — to pin
// against a regression to run_seq-based sorting.
function sixMixedCabinets() {
    return [
        { layout_key: "a1", cabinet_type: "appliance", wall: "front", x_position_in: 0 },
        { layout_key: "f1", cabinet_type: "filler",    wall: "back",  x_position_in: 48 },
        { layout_key: "c1", cabinet_type: "corner",    wall: "right", run_seq: -1, z_position_in: 10 },
        { layout_key: "b2", cabinet_type: "base",      wall: "back",  x_position_in: 24 },
        { layout_key: "t1", cabinet_type: "tall",      wall: "left",  z_position_in: 24 },
        { layout_key: "b1", cabinet_type: "base",      wall: "back",  x_position_in: 0 },
        { layout_key: "w1", cabinet_type: "wall",      wall: "left",  z_position_in: 0 },
    ];
}
const EXPECTED_ORDER = ["b1", "b2", "w1", "t1", "c1", "a1"]; // f1 (filler) excluded

test("forward cycling (dir=+1) visits every non-filler cabinet exactly once, in wall/position/layout_key order, then wraps", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = sixMixedCabinets();
    h.component.state.selected = null;

    const seen = [];
    for (let i = 0; i < EXPECTED_ORDER.length; i++) {
        h.component._cycleSelection(1);
        seen.push(h.component.state.selected.layout_key);
    }
    assert.deepEqual(seen, EXPECTED_ORDER,
        "one full lap forward must visit each selectable cabinet exactly once, in order");

    // One more press must wrap back to the first item, not stall or skip.
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, EXPECTED_ORDER[0],
        "cycling past the last item must wrap to the first");

    assert.ok(!seen.includes("f1"), "a filler panel must never be reachable via ArrowLeft/ArrowRight");
    h.destroy();
});

test("backward cycling (dir=-1) is the exact reverse order, and the FIRST press with nothing selected lands on the LAST item (not second-to-last)", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = sixMixedCabinets();
    h.component.state.selected = null;

    h.component._cycleSelection(-1);
    assert.equal(h.component.state.selected.layout_key, EXPECTED_ORDER[EXPECTED_ORDER.length - 1],
        "first ArrowLeft with nothing selected must land on the LAST selectable item");

    const seenAfterFirst = [h.component.state.selected.layout_key];
    for (let i = 1; i < EXPECTED_ORDER.length; i++) {
        h.component._cycleSelection(-1);
        seenAfterFirst.push(h.component.state.selected.layout_key);
    }
    assert.deepEqual(seenAfterFirst, [...EXPECTED_ORDER].reverse(),
        "one full lap backward must be the exact reverse of the forward order");

    // Wrap the other direction too.
    h.component._cycleSelection(-1);
    assert.equal(h.component.state.selected.layout_key, EXPECTED_ORDER[EXPECTED_ORDER.length - 1],
        "cycling before the first item must wrap to the last");
    h.destroy();
});

test("first ArrowRight with nothing selected lands on the FIRST item (dir=+1 boundary)", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = sixMixedCabinets();
    h.component.state.selected = null;
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, EXPECTED_ORDER[0]);
    h.destroy();
});

test("cycling from a selection that no longer exists in state.items (e.g. just deleted) falls back to the same boundary behaviour as no selection", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = sixMixedCabinets();
    h.component.state.selected = { layout_key: "stale-deleted-item", cabinet_type: "base" };

    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, EXPECTED_ORDER[0]);
    h.destroy();
});

test("order is deterministic regardless of the incoming items array order (wall then position then layout_key tiebreak)", async () => {
    const shuffledA = sixMixedCabinets();
    const shuffledB = [...sixMixedCabinets()].reverse();

    const hA = await mountConfigurator({});
    hA.component.state.items = shuffledA;
    hA.component.state.selected = null;
    const seenA = [];
    for (let i = 0; i < EXPECTED_ORDER.length; i++) {
        hA.component._cycleSelection(1);
        seenA.push(hA.component.state.selected.layout_key);
    }
    hA.destroy();

    const hB = await mountConfigurator({});
    hB.component.state.items = shuffledB;
    hB.component.state.selected = null;
    const seenB = [];
    for (let i = 0; i < EXPECTED_ORDER.length; i++) {
        hB.component._cycleSelection(1);
        seenB.push(hB.component.state.selected.layout_key);
    }
    hB.destroy();

    assert.deepEqual(seenA, EXPECTED_ORDER);
    assert.deepEqual(seenB, EXPECTED_ORDER);
});

test("two items tied on wall AND position sort by layout_key ascending as the final deterministic tiebreak", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        { layout_key: "zzz", cabinet_type: "base", wall: "back", x_position_in: 0 },
        { layout_key: "aaa", cabinet_type: "base", wall: "back", x_position_in: 0 },
    ];
    h.component.state.selected = null;
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "aaa",
        "the lexicographically-first layout_key must come first when wall+position tie");
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "zzz");
    h.destroy();
});

// Review fix (Finding 2) — the case the pre-fix run_seq-based sort got
// wrong: two REAL corner design lines sharing a wall. Both carry the
// universal run_seq=-1 sentinel (so a regression back to sorting on
// run_seq would see them tied and fall through to the alphabetical
// layout_key tiebreak); their z_position_in values are deliberately
// set so the CORRECT (position-based) order is the OPPOSITE of what
// alphabetical layout_key ordering would produce, so this fails loudly
// under a run_seq-based regression instead of passing by coincidence.
test("two corners sharing a wall (both run_seq=-1, the universal engine sentinel) sort by their actual position, not alphabetically", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        // Alphabetically first layout_key, but the FARTHER corner (z=30).
        { layout_key: "aaa-corner", cabinet_type: "corner", wall: "right", run_seq: -1, z_position_in: 30 },
        // Alphabetically last layout_key, but the NEARER corner (z=6).
        { layout_key: "zzz-corner", cabinet_type: "corner", wall: "right", run_seq: -1, z_position_in: 6 },
    ];
    h.component.state.selected = null;
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "zzz-corner",
        "the corner nearer the start of the run (z=6) must come first, despite its layout_key sorting last alphabetically");
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "aaa-corner");
    // Full wrap.
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "zzz-corner");
    h.destroy();
});

test("an item missing wall/position entirely does not throw and sorts after items on known walls", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        { layout_key: "unplaced", cabinet_type: "base" }, // no wall, no position
        { layout_key: "b1", cabinet_type: "base", wall: "back", x_position_in: 0 },
    ];
    h.component.state.selected = null;
    assert.doesNotThrow(() => h.component._cycleSelection(1));
    assert.equal(h.component.state.selected.layout_key, "b1",
        "a known-wall item must sort before one with no wall at all");
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "unplaced");
    h.destroy();
});

test("no selectable cabinets: _cycleSelection is a no-op (never throws, selection stays null)", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        { layout_key: "f1", cabinet_type: "filler", wall: "back", x_position_in: 0 },
    ];
    h.component.state.selected = null;
    assert.doesNotThrow(() => h.component._cycleSelection(1));
    assert.equal(h.component.state.selected, null);
    h.destroy();
});
