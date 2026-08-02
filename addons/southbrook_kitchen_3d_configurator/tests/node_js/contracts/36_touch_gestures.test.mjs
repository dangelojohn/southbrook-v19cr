/**
 * Touch editing spec, Wave 1 — gesture-arbitration reducer contract.
 * `docs/superpowers/specs/2026-08-02-touch-editing-design.md` §2.
 *
 * Pure-function module (no OWL, no DOM, no THREE, no timers) so this
 * contract imports it directly by relative path — same pattern as
 * contract #29/#30 (catalog_list.esm.js / cabinet_thumb.esm.js), the
 * closest siblings. `createGestureReducer` returns `(state, event) ->
 * state`; every test below drives it with plain event-array fixtures,
 * exactly the style the research recipe (touch-engineering-research.md
 * §4) prescribes for this exact kind of state machine.
 *
 * NOTE ON THE FILENAME NUMBER: the build brief that spawned this task
 * named the target `31_touch_gestures.test.mjs` ("next in sequence"),
 * but contracts 31-35 already exist in this tree (panel-collapse,
 * toast/a11y, undo/redo, validation chip, customer autocomplete —
 * commits 5748ca79/cc7e7de1, both predating this brief). This file is
 * `36_` — the actual next free slot — to avoid clobbering existing
 * contracts.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOUCH_GESTURES_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "canvas", "touch_gestures.esm.js",
);

const {
    createGestureReducer,
    createInitialGestureState,
    travelDistance,
    GESTURE_PHASES,
} = await import(TOUCH_GESTURES_SRC);

// ─── fixtures / helpers ──────────────────────────────────────────────────

const CABINET_A = { kind: "cabinet", id: "A", selected: false };
const CABINET_A_SELECTED = { kind: "cabinet", id: "A", selected: true };
const CABINET_B = { kind: "cabinet", id: "B", selected: false };
const EMPTY = { kind: "empty" };
const AFFORDANCE = { kind: "affordance", action: "sendToWall" };

function down(pointerId, x, y, hit, pointerType = "touch") {
    return { type: "pointerdown", pointerId, x, y, pointerType, hit };
}
function move(pointerId, x, y, pointerType = "touch") {
    return { type: "pointermove", pointerId, x, y, pointerType };
}
function up(pointerId, x, y, pointerType = "touch") {
    return { type: "pointerup", pointerId, x, y, pointerType };
}
function cancel(pointerId, pointerType = "touch") {
    return { type: "pointercancel", pointerId, pointerType };
}
function timer(pointerId) {
    return { type: "timerFired", pointerId };
}
function hitResult(pointerId, hit) {
    return { type: "hitResult", pointerId, hit };
}

/** Fold a sequence of events through a reducer, returning the final state. */
function run(reducer, events, initial) {
    return events.reduce((s, e) => reducer(s, e), initial);
}

// Selects cabinet A as a precondition, returning the resulting selectedIdle
// state, via the front-door (a real pointerdown+up tap) rather than by
// hand-building state — so preconditions exercise the same code path being
// tested elsewhere.
function selectA(reducer) {
    return run(reducer, [down(1, 0, 0, CABINET_A), up(1, 0, 0)], undefined);
}

// ─── createInitialGestureState / travelDistance ─────────────────────────

test("createInitialGestureState: idle, unselected, no pointers, no effects", () => {
    const s = createInitialGestureState();
    assert.equal(s.phase, GESTURE_PHASES.IDLE);
    assert.equal(s.selectedId, null);
    assert.deepEqual(s.pointers, []);
    assert.deepEqual(s.effects, []);
});

test("travelDistance: pure Euclidean distance", () => {
    assert.equal(travelDistance({ x: 0, y: 0 }, { x: 3, y: 4 }), 5);
    assert.equal(travelDistance({ x: 10, y: 10 }, { x: 10, y: 10 }), 0);
});

test("createGestureReducer: reduce(undefined, event) bootstraps from initial state with no crash", () => {
    const reducer = createGestureReducer();
    const s = reducer(undefined, down(1, 0, 0, EMPTY));
    assert.equal(s.phase, GESTURE_PHASES.PENDING_TAP);
});

test("createGestureReducer: an event with no/invalid type is a safe no-op", () => {
    const reducer = createGestureReducer();
    const s0 = createInitialGestureState();
    assert.deepEqual(reducer(s0, undefined), { ...s0, effects: [] });
    assert.deepEqual(reducer(s0, {}), { ...s0, effects: [] });
});

// ─── config exposure + defaults ──────────────────────────────────────────

test("createGestureReducer: default config is slopPx=9, longPressMs=500, longPressEnabled=true", () => {
    const reducer = createGestureReducer();
    assert.deepEqual(reducer.config, { slopPx: 9, longPressMs: 500, longPressEnabled: true });
});

test("createGestureReducer: surfaceConfig overrides are reflected verbatim on .config", () => {
    const reducer = createGestureReducer({ slopPx: 4, longPressMs: 300, longPressEnabled: false });
    assert.deepEqual(reducer.config, { slopPx: 4, longPressMs: 300, longPressEnabled: false });
});

test("createGestureReducer: two independently-configured reducers do not share state or behavior", () => {
    const portal = createGestureReducer({ slopPx: 9 });
    const backend = createGestureReducer({ slopPx: 3 });
    assert.notEqual(portal.config.slopPx, backend.config.slopPx);
});

// ─── idle -> pendingTap -> tap resolutions (up-within-slop) ─────────────

test("idle: pointerdown on empty, tap (up within slop) -> stays idle, no effects (soft lock never triggers on empty)", () => {
    const reducer = createGestureReducer();
    const afterDown = reducer(undefined, down(1, 100, 100, EMPTY));
    assert.equal(afterDown.phase, GESTURE_PHASES.PENDING_TAP);
    const afterUp = reducer(afterDown, up(1, 100, 100));
    assert.equal(afterUp.phase, GESTURE_PHASES.IDLE);
    assert.equal(afterUp.selectedId, null);
    assert.deepEqual(afterUp.effects, []);
});

test("idle: tap on an unselected cabinet -> selectedIdle, emits select", () => {
    const reducer = createGestureReducer();
    const s = run(reducer, [down(1, 0, 0, CABINET_A), up(1, 0, 0)], undefined);
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, [{ type: "select", id: "A" }]);
});

test("idle: tap on an affordance glyph (unusual but tolerated) -> affordance effect, no selection change", () => {
    const reducer = createGestureReducer();
    const s = run(reducer, [down(1, 0, 0, AFFORDANCE), up(1, 0, 0)], undefined);
    assert.equal(s.phase, GESTURE_PHASES.IDLE);
    assert.equal(s.selectedId, null);
    assert.deepEqual(s.effects, [{ type: "affordance", action: "sendToWall" }]);
});

// ─── reselect-in-one-tap (soft lock) ──────────────────────────────────────

test("selectedIdle: tapping a DIFFERENT cabinet reselects in exactly one tap", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    assert.equal(s.selectedId, "A");
    s = reducer(s, down(2, 50, 50, CABINET_B));
    s = reducer(s, up(2, 50, 50));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "B");
    assert.deepEqual(s.effects, [{ type: "select", id: "B" }]);
});

test("selectedIdle: tapping the ALREADY-selected cabinet is a true no-op (no redundant select effect)", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, up(2, 0, 0));
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, [], "no-op reselect must not re-emit select");
});

test("selectedIdle: tapping empty space never deselects (soft lock persists)", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, EMPTY));
    s = reducer(s, up(2, 0, 0));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, []);
});

// ─── one finger from empty = orbit ────────────────────────────────────────

test("idle: one-finger drag from empty space beyond slop -> cameraOrbit, emits orbit once", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, move(1, 20, 0)); // dist 20 >= 9
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT);
    assert.deepEqual(s.effects, [{ type: "orbit" }]);
});

test("cameraOrbit: further moves emit no additional effects (native OrbitControls owns motion)", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, move(1, 20, 0));
    s = reducer(s, move(1, 40, 5));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT);
    assert.deepEqual(s.effects, []);
});

test("cameraOrbit: pointerup ends the orbit, returning to idle with no effects", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, move(1, 20, 0));
    s = reducer(s, up(1, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.IDLE);
    assert.deepEqual(s.effects, []);
});

test("cameraOrbit: from a selectedIdle precondition, ending orbit returns to selectedIdle (selection untouched)", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, EMPTY));
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT);
    s = reducer(s, up(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
});

// ─── selection-gating: drag on unselected orbits, no object effects ──────

test("selectedIdle: one-finger drag on a DIFFERENT (unselected) cabinet orbits, never drags it", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_B));
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT, "unselected-cabinet drag must orbit, not drag");
    assert.deepEqual(s.effects, [{ type: "orbit" }], "no beginDrag/dragTo — no object effects at all");
    s = reducer(s, up(2, 20, 0));
    assert.equal(s.selectedId, "A", "the drag-past-slop on B must never have selected B");
});

test("pendingTap: dragging off an affordance glyph orbits, never begins a drag", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, AFFORDANCE));
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT);
    assert.deepEqual(s.effects, [{ type: "orbit" }]);
});

// ─── direct drag on the selected cabinet ─────────────────────────────────

test("selectedIdle: one-finger drag on the SELECTED cabinet beyond slop -> dragging, emits beginDrag", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING);
    assert.deepEqual(s.effects, [{ type: "beginDrag", id: "A" }]);
});

test("dragging: subsequent moves emit dragTo with mode 'direct' and the dragged id", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0));
    s = reducer(s, move(2, 25, 3));
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING);
    assert.deepEqual(s.effects, [{ type: "dragTo", id: "A", x: 25, y: 3, mode: "direct" }]);
});

test("dragging: commit-on-up emits commit and returns to selectedIdle with the same selection", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0));
    s = reducer(s, up(2, 22, 1));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, [{ type: "commit", id: "A", x: 22, y: 1, mode: "direct" }]);
});

// ─── slop boundary ────────────────────────────────────────────────────────

test("slop boundary: distance exactly == slopPx counts as EXCEEDED (matches isCabinetDragCommitted's >= convention)", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 9, 0)); // dist === 9 === slopPx
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING, "== slop must commit, not stay pending");
});

test("slop boundary: distance just under slopPx stays pending (tap/pickup still eligible)", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 8.999, 0));
    assert.equal(s.phase, GESTURE_PHASES.PENDING_TAP);
    assert.deepEqual(s.effects, []);
});

test("slop boundary: a tap resolves correctly even after several sub-slop jitter moves", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = reducer(undefined, down(1, 0, 0, CABINET_A));
    s = reducer(s, move(1, 2, 1));
    s = reducer(s, move(1, -1, 2));
    s = reducer(s, move(1, 1, -1));
    assert.equal(s.phase, GESTURE_PHASES.PENDING_TAP, "jitter under slop must never commit to drag/orbit");
    s = reducer(s, up(1, 1, -1));
    assert.equal(s.selectedId, "A");
});

// ─── long-press pickup + ghost-drag ──────────────────────────────────────

test("selectedIdle: long-press (timerFired) on the SELECTED cabinet within slop -> ghostDrag, emits beginPickup", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2));
    assert.equal(s.phase, GESTURE_PHASES.GHOST_DRAG);
    assert.deepEqual(s.effects, [{ type: "beginPickup", id: "A" }]);
});

test("ghostDrag: subsequent moves emit dragTo with mode 'ghost'", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2));
    s = reducer(s, move(2, 5, 5));
    assert.deepEqual(s.effects, [{ type: "dragTo", id: "A", x: 5, y: 5, mode: "ghost" }]);
});

test("ghostDrag: commit-on-up emits commit with mode 'ghost', returns to selectedIdle", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2));
    s = reducer(s, up(2, 5, 5));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, [{ type: "commit", id: "A", x: 5, y: 5, mode: "ghost" }]);
});

test("long-press is inert on an UNSELECTED cabinet — no pickup, still resolvable as a plain tap", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer); // A selected
    s = reducer(s, down(2, 0, 0, CABINET_B)); // hold on B, which is unselected
    s = reducer(s, timer(2));
    assert.equal(s.phase, GESTURE_PHASES.PENDING_TAP, "no pickup on an unselected cabinet");
    assert.deepEqual(s.effects, []);
    s = reducer(s, up(2, 0, 0));
    assert.equal(s.selectedId, "B", "the pointer can still resolve to a normal select on release");
});

test("long-press is inert on empty space and on an affordance glyph", () => {
    const reducer = createGestureReducer();
    let sEmpty = reducer(undefined, down(1, 0, 0, EMPTY));
    sEmpty = reducer(sEmpty, timer(1));
    assert.equal(sEmpty.phase, GESTURE_PHASES.PENDING_TAP);
    assert.deepEqual(sEmpty.effects, []);

    let sAff = selectA(reducer);
    sAff = reducer(sAff, down(2, 0, 0, AFFORDANCE));
    sAff = reducer(sAff, timer(2));
    assert.equal(sAff.phase, GESTURE_PHASES.PENDING_TAP);
    assert.deepEqual(sAff.effects, []);
});

// ─── long-press vs. drag disambiguation (mutual exclusivity) ─────────────

test("disambiguation: move-beyond-slop-first locks in direct drag — a LATE timerFired is then a no-op", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0)); // -> dragging (direct)
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING);
    const before = s;
    s = reducer(s, timer(2)); // host should have cleared this timer; prove safety if it fires anyway
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING, "a stale timerFired must never retroactively start a pickup");
    assert.deepEqual(s.effects, []);
    assert.equal(s.mode, before.mode, "mode must stay 'direct', never flip to 'ghost'");
});

test("disambiguation: timerFired-first locks in ghost pickup — a subsequent slop-exceeding move never begins a second (direct) drag", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2)); // -> ghostDrag
    assert.equal(s.phase, GESTURE_PHASES.GHOST_DRAG);
    s = reducer(s, move(2, 50, 50)); // large move, well past slop
    assert.equal(s.phase, GESTURE_PHASES.GHOST_DRAG, "must stay in ghost mode, not re-enter dragging");
    assert.deepEqual(s.effects, [{ type: "dragTo", id: "A", x: 50, y: 50, mode: "ghost" }]);
});

// ─── two-finger always-camera preemption ─────────────────────────────────

test("two-finger preemption mid-pendingTap: no drag was active, so only cameraGesture is emitted (no cancelDrag)", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED)); // pendingTap, nothing committed yet
    s = reducer(s, down(3, 10, 10, EMPTY)); // 2nd finger — hit is irrelevant for finger #2
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [{ type: "cameraGesture" }]);
});

test("two-finger preemption mid-cameraOrbit: only cameraGesture is emitted (nothing to cancel)", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, move(1, 20, 0)); // -> cameraOrbit
    s = reducer(s, down(2, 5, 5, EMPTY));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [{ type: "cameraGesture" }]);
});

test("two-finger preemption MID-DRAG cancels cleanly: cancelDrag then cameraGesture, in that order", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0)); // -> dragging
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING);
    s = reducer(s, down(3, 100, 100, EMPTY)); // 2nd finger lands mid-drag
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [
        { type: "cancelDrag", id: "A", mode: "direct" },
        { type: "cameraGesture" },
    ]);
});

test("two-finger preemption MID-GHOST-DRAG also cancels cleanly with mode 'ghost'", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2)); // -> ghostDrag
    s = reducer(s, down(3, 100, 100, EMPTY));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [
        { type: "cancelDrag", id: "A", mode: "ghost" },
        { type: "cameraGesture" },
    ]);
});

test("cameraGesture ends (endCameraGesture) once fewer than 2 pointers remain, restoring the prior selection", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0));
    s = reducer(s, down(3, 100, 100, EMPTY)); // -> cameraGesture, drag cancelled
    s = reducer(s, up(2, 20, 0)); // one of the two fingers lifts
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A", "selection survives the whole two-finger interruption");
    assert.deepEqual(s.effects, [{ type: "endCameraGesture" }]);
});

test("cameraGesture: a 3rd simultaneous finger stays in cameraGesture with no additional effect", () => {
    const reducer = createGestureReducer();
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, down(2, 1, 1, EMPTY)); // -> cameraGesture
    assert.deepEqual(s.effects, [{ type: "cameraGesture" }]);
    s = reducer(s, down(3, 2, 2, EMPTY));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [], "no re-entry effect for a 3rd finger already in the gesture");
});

test("cameraGesture: releasing a 3rd finger down to 2 does not yet end the gesture", () => {
    const reducer = createGestureReducer();
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, down(2, 1, 1, EMPTY));
    s = reducer(s, down(3, 2, 2, EMPTY));
    s = reducer(s, up(3, 2, 2));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE, "2 fingers remain — still a camera gesture");
    assert.deepEqual(s.effects, []);
});

test("cameraGesture: pointercancel of one of two fingers also ends the gesture (identical to pointerup)", () => {
    const reducer = createGestureReducer();
    let s = reducer(undefined, down(1, 0, 0, EMPTY));
    s = reducer(s, down(2, 1, 1, EMPTY));
    s = reducer(s, cancel(1));
    assert.equal(s.phase, GESTURE_PHASES.IDLE);
    assert.deepEqual(s.effects, [{ type: "endCameraGesture" }]);
});

// ─── pointercancel handling (every cancel-able phase) ────────────────────

test("pointercancel while pendingTap: resets to resting state, no effects (nothing was ever committed)", () => {
    const reducer = createGestureReducer();
    let s = reducer(undefined, down(1, 0, 0, CABINET_A));
    s = reducer(s, cancel(1));
    assert.equal(s.phase, GESTURE_PHASES.IDLE);
    assert.deepEqual(s.effects, []);
    assert.deepEqual(s.pointers, []);
});

test("pointercancel while cameraOrbit: resets to resting state, no effects", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, EMPTY));
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_ORBIT);
    s = reducer(s, cancel(2));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, []);
});

test("pointercancel while dragging: emits cancelDrag(mode:'direct'), rolls back to selectedIdle", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0));
    s = reducer(s, cancel(2));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "A");
    assert.deepEqual(s.effects, [{ type: "cancelDrag", id: "A", mode: "direct" }]);
});

test("pointercancel while ghostDrag: emits cancelDrag(mode:'ghost'), rolls back to selectedIdle", () => {
    const reducer = createGestureReducer();
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, timer(2));
    s = reducer(s, cancel(2));
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.deepEqual(s.effects, [{ type: "cancelDrag", id: "A", mode: "ghost" }]);
});

// ─── defensive: mismatched / stray / unknown pointerId events ───────────

test("an event for an unrelated pointerId is ignored in pendingTap (no state change, no effects)", () => {
    const reducer = createGestureReducer();
    const s0 = reducer(undefined, down(1, 0, 0, CABINET_A));
    const s1 = reducer(s0, move(99, 500, 500));
    assert.equal(s1.phase, GESTURE_PHASES.PENDING_TAP);
    assert.deepEqual(s1.hit, s0.hit);
    assert.deepEqual(s1.effects, []);
});

test("an up/cancel/timerFired for an unrelated pointerId is ignored in dragging/ghostDrag", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0)); // -> dragging on pointer 2
    const dragging = reducer(s, up(999, 0, 0));
    assert.equal(dragging.phase, GESTURE_PHASES.DRAGGING, "up for a foreign pointerId must not commit the drag");
    assert.deepEqual(dragging.effects, []);
});

test("pointerup/pointercancel for an unknown pointerId while idle is a harmless no-op", () => {
    const reducer = createGestureReducer();
    const s0 = createInitialGestureState();
    assert.deepEqual(reducer(s0, up(42, 0, 0)), { ...s0, effects: [] });
    assert.deepEqual(reducer(s0, cancel(42)), { ...s0, effects: [] });
});

test("a duplicate pointerdown for an already-tracked pointerId is ignored, not treated as a 2nd finger", () => {
    const reducer = createGestureReducer();
    let s = reducer(undefined, down(1, 0, 0, CABINET_A));
    s = reducer(s, down(1, 1, 1, CABINET_B)); // same pointerId re-fires
    assert.equal(s.phase, GESTURE_PHASES.PENDING_TAP, "must not escalate to cameraGesture on a duplicate id");
    assert.deepEqual(s.pointers, [1]);
});

// ─── hitResult: optional late-binding hit path ───────────────────────────

test("hitResult: fills in a hit that was unknown at pointerdown time, enabling a correct drag decision", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, { type: "pointerdown", pointerId: 2, x: 0, y: 0, pointerType: "touch" }); // no hit yet
    assert.deepEqual(s.hit, { kind: "empty" }, "missing hit defaults to empty, never throws");
    s = reducer(s, hitResult(2, CABINET_A_SELECTED));
    assert.deepEqual(s.hit, CABINET_A_SELECTED);
    s = reducer(s, move(2, 20, 0));
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING, "the late-bound hit must be used for the drag decision");
    assert.deepEqual(s.effects, [{ type: "beginDrag", id: "A" }]);
});

test("hitResult: ignored outside pendingTap (e.g. once already dragging) and for a mismatched pointerId", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = selectA(reducer);
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 20, 0)); // -> dragging
    const beforeStray = s;
    s = reducer(s, hitResult(2, EMPTY));
    assert.deepEqual(s, { ...beforeStray, effects: [] }, "hitResult must not perturb an active drag");

    let pending = reducer(undefined, down(1, 0, 0, EMPTY));
    const untouched = reducer(pending, hitResult(999, CABINET_A_SELECTED));
    assert.deepEqual(untouched.hit, { kind: "empty" }, "a hitResult for a foreign pointerId must not apply");
});

// ─── per-surface config differences ──────────────────────────────────────

test("per-surface config: a tighter slopPx commits to drag/orbit sooner than a looser one, given identical input", () => {
    const loose = createGestureReducer({ slopPx: 9 });
    const tight = createGestureReducer({ slopPx: 3 });

    let sLoose = reducer_selectA(loose);
    sLoose = loose(sLoose, down(2, 0, 0, CABINET_A_SELECTED));
    sLoose = loose(sLoose, move(2, 5, 0)); // dist 5: >= tight's 3, < loose's 9
    assert.equal(sLoose.phase, GESTURE_PHASES.PENDING_TAP, "loose surface: 5px is still within a 9px slop");

    let sTight = reducer_selectA(tight);
    sTight = tight(sTight, down(2, 0, 0, CABINET_A_SELECTED));
    sTight = tight(sTight, move(2, 5, 0));
    assert.equal(sTight.phase, GESTURE_PHASES.DRAGGING, "tight surface: 5px already exceeds a 3px slop");

    function reducer_selectA(r) {
        return run(r, [down(1, 0, 0, CABINET_A), up(1, 0, 0)], undefined);
    }
});

test("per-surface config: longPressEnabled=false disables pickup entirely on that surface only", () => {
    const withPickup = createGestureReducer({ longPressEnabled: true });
    const noPickup = createGestureReducer({ longPressEnabled: false });

    let s1 = run(withPickup, [down(1, 0, 0, CABINET_A), up(1, 0, 0)], undefined);
    s1 = withPickup(s1, down(2, 0, 0, CABINET_A_SELECTED));
    s1 = withPickup(s1, timer(2));
    assert.equal(s1.phase, GESTURE_PHASES.GHOST_DRAG, "pickup-enabled surface must transition on long-press");

    let s2 = run(noPickup, [down(1, 0, 0, CABINET_A), up(1, 0, 0)], undefined);
    s2 = noPickup(s2, down(2, 0, 0, CABINET_A_SELECTED));
    s2 = noPickup(s2, timer(2));
    assert.equal(s2.phase, GESTURE_PHASES.PENDING_TAP, "pickup-disabled surface must ignore the same timerFired");
    assert.deepEqual(s2.effects, []);
});

// ─── pointerType is accepted but never forks logic ───────────────────────

test("pointerType never affects the resulting state — mouse and touch pointerTypes behave identically", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    const mouseEvents = [
        down(1, 0, 0, CABINET_A_SELECTED, "mouse"),
        move(1, 20, 0, "mouse"),
        up(1, 20, 0, "mouse"),
    ];
    const touchEvents = [
        down(1, 0, 0, CABINET_A_SELECTED, "touch"),
        move(1, 20, 0, "touch"),
        up(1, 20, 0, "touch"),
    ];
    const selA = createInitialGestureState();
    const selectedInit = { ...selA, phase: GESTURE_PHASES.SELECTED_IDLE, selectedId: "A" };
    const mouseFinal = run(reducer, mouseEvents, selectedInit);
    const touchFinal = run(reducer, touchEvents, selectedInit);
    assert.deepEqual(mouseFinal, touchFinal);
});

// ─── full-sequence smoke test (holistic confidence, mirrors spec §7) ─────

test("smoke: select -> direct-drag -> commit -> reselect -> long-press pickup -> two-finger interrupt -> resume -> tap-empty no-op", () => {
    const reducer = createGestureReducer({ slopPx: 9 });
    let s = undefined;

    // Tap-select cabinet A.
    s = reducer(s, down(1, 0, 0, CABINET_A));
    s = reducer(s, up(1, 0, 0));
    assert.equal(s.selectedId, "A");

    // Direct-drag A.
    s = reducer(s, down(2, 0, 0, CABINET_A_SELECTED));
    s = reducer(s, move(2, 15, 0));
    assert.equal(s.phase, GESTURE_PHASES.DRAGGING);
    s = reducer(s, move(2, 18, 2));
    s = reducer(s, up(2, 18, 2));
    assert.deepEqual(s.effects, [{ type: "commit", id: "A", x: 18, y: 2, mode: "direct" }]);

    // Reselect B in one tap.
    s = reducer(s, down(3, 40, 40, CABINET_B));
    s = reducer(s, up(3, 40, 40));
    assert.equal(s.selectedId, "B");

    // Long-press pickup B, ghost-drag, two-finger interrupt mid-ghost.
    s = reducer(s, down(4, 40, 40, { kind: "cabinet", id: "B", selected: true }));
    s = reducer(s, timer(4));
    assert.equal(s.phase, GESTURE_PHASES.GHOST_DRAG);
    s = reducer(s, down(5, 200, 200, EMPTY)); // 2nd finger lands
    assert.equal(s.phase, GESTURE_PHASES.CAMERA_GESTURE);
    assert.deepEqual(s.effects, [
        { type: "cancelDrag", id: "B", mode: "ghost" },
        { type: "cameraGesture" },
    ]);
    s = reducer(s, up(4, 40, 40)); // one finger lifts -> gesture ends
    assert.equal(s.phase, GESTURE_PHASES.SELECTED_IDLE);
    assert.equal(s.selectedId, "B", "selection survived the whole interruption");

    // Tap empty space -> no-op, B stays selected (soft lock).
    s = reducer(s, down(6, 300, 300, EMPTY));
    s = reducer(s, up(6, 300, 300));
    assert.equal(s.selectedId, "B");
    assert.deepEqual(s.effects, []);
});
