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
 * Mounts the REAL, unmodified kitchen_configurator.js end-to-end via
 * the harness's canonical mount helper (only <KitchenCanvas> — the
 * Three.js/WebGL boundary — is mocked; _cycleSelection itself is pure
 * state.items/state.selected bookkeeping with no canvas dependency).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mountConfigurator } from "../harness/mount_configurator.mjs";

// wall then run_seq then layout_key — the documented deterministic
// order. Deliberately listed here in a DIFFERENT order than the
// expected cycle order, and re-shuffled between tests, so a passing
// test proves the sort is doing the work, not incoming array order.
function sixMixedCabinets() {
    return [
        { layout_key: "a1", cabinet_type: "appliance", wall: "front", run_seq: 0 },
        { layout_key: "f1", cabinet_type: "filler",    wall: "back",  run_seq: 2 },
        { layout_key: "c1", cabinet_type: "corner",    wall: "right", run_seq: 0 },
        { layout_key: "b2", cabinet_type: "base",      wall: "back",  run_seq: 1 },
        { layout_key: "t1", cabinet_type: "tall",      wall: "left",  run_seq: 1 },
        { layout_key: "b1", cabinet_type: "base",      wall: "back",  run_seq: 0 },
        { layout_key: "w1", cabinet_type: "wall",      wall: "left",  run_seq: 0 },
    ];
}
const EXPECTED_ORDER = ["b1", "b2", "w1", "t1", "c1", "a1"]; // f1 (filler) excluded

test("forward cycling (dir=+1) visits every non-filler cabinet exactly once, in wall/run_seq/layout_key order, then wraps", async () => {
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

test("order is deterministic regardless of the incoming items array order (wall then run_seq then layout_key tiebreak)", async () => {
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

test("two items tied on wall AND run_seq sort by layout_key ascending as the final deterministic tiebreak", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        { layout_key: "zzz", cabinet_type: "base", wall: "back", run_seq: 0 },
        { layout_key: "aaa", cabinet_type: "base", wall: "back", run_seq: 0 },
    ];
    h.component.state.selected = null;
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "aaa",
        "the lexicographically-first layout_key must come first when wall+run_seq tie");
    h.component._cycleSelection(1);
    assert.equal(h.component.state.selected.layout_key, "zzz");
    h.destroy();
});

test("an item missing run_seq/wall entirely does not throw and sorts after items on known walls", async () => {
    const h = await mountConfigurator({});
    h.component.state.items = [
        { layout_key: "unplaced", cabinet_type: "base" }, // no wall, no run_seq
        { layout_key: "b1", cabinet_type: "base", wall: "back", run_seq: 0 },
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
        { layout_key: "f1", cabinet_type: "filler", wall: "back", run_seq: 0 },
    ];
    h.component.state.selected = null;
    assert.doesNotThrow(() => h.component._cycleSelection(1));
    assert.equal(h.component.state.selected, null);
    h.destroy();
});
