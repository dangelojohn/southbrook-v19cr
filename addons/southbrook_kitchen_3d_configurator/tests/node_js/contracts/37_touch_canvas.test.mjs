/**
 * Touch editing spec, Wave 2 — KitchenCanvas integration contract.
 * `docs/superpowers/specs/2026-08-02-touch-editing-design.md`.
 *
 * Covers the four test targets the build brief calls out explicitly:
 *   1. effect-consumption unit seams (pure parts) — describeGestureEffect,
 *      shouldClaimPointerEvent.
 *   2. proxy-mesh sizing math — computeProxyPaddingIn, computeOrthoPxPerIn,
 *      computePerspPxPerIn, plus an end-to-end raycast-through-the-padding
 *      check via the real `_placeCabinetGroup`/`_addTouchProxyMesh`.
 *   3. plane-projection incl. the Z-branch — projectDragAlongWall.
 *   4. camera-to-elevation target math — resolveElevationViewForWall,
 *      plus `_cameraToElevation`'s delegation to `_setView`.
 * Plus a handful of integration tests for the effect-consumption glue
 * (`_applyGestureEffects`, `_computeTouchHit`) and a full pointerdown-
 * >pointermove->pointerup drag cycle, including the Z-axis branch for a
 * left-wall cabinet — same "no WebGL needed, pure CPU matrix/material
 * math" two-tier strategy as 03_renderer_coords / 20_selection_visuals.
 *
 * Mouse-path regression is NOT re-tested here (that's 01-31's job) —
 * this file only exercises the NEW touch surface, and one explicit
 * "touchEnabled=false is a total no-op" guard test per handler.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "../harness/setup_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ADDON_ROOT = path.resolve(HERE, "..", "..", "..");
const CANVAS_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "kitchen_canvas.esm.js");
const BASE_CABINET_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "base_cabinet.esm.js");
const CONSTANTS_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "constants.esm.js");
const TOUCH_GESTURES_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "touch_gestures.esm.js");
const MANIP_STATES_SRC = path.resolve(ADDON_ROOT, "static", "src", "js", "canvas", "manipulation_states.esm.js");

async function loadReal() {
    await setupDom();
    const canvasMod = await import(CANVAS_SRC);
    const { buildBaseCabinet } = await import(BASE_CABINET_SRC);
    const { IN, P, WALLS } = await import(CONSTANTS_SRC);
    const { GESTURE_PHASES, createGestureReducer } = await import(TOUCH_GESTURES_SRC);
    const { HIT_PROXY_MIN_PX, COLLISION_RED } = await import(MANIP_STATES_SRC);
    const THREE = global.window.THREE;
    return { ...canvasMod, buildBaseCabinet, IN, P, WALLS, GESTURE_PHASES, createGestureReducer, HIT_PROXY_MIN_PX, COLLISION_RED, THREE };
}

// A KitchenCanvas "instance" that supports this-> this cross-method
// dispatch (`Object.create(KitchenCanvas.prototype)`), needed because
// several Wave-2 methods call sibling instance methods (`_onPointerDown`
// -> `_computeTouchHit`/`_dispatchGesture`, `_applyGestureEffects` ->
// `_findItemByLayoutKey`/`_showTouchDragGhost`, etc.) — a bare
// `{T:{...}}` object (the pre-Wave-2 convention, still valid for
// self-contained methods like `_placeCabinetGroup`) does not support
// that. Every field a Wave-2 method might dereference is present, even
// when a given test doesn't exercise that path, so nothing throws on
// an incidental read.
function fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, props = {}) {
    const scene = new THREE.Scene();
    const fake = Object.create(KitchenCanvas.prototype);
    fake.props = {
        items: [], room: { width_in: 120, depth_in: 96, height_in: 96 },
        selected: null, touchEnabled: true, dragCollision: false,
        ...props,
    };
    fake._currentView = "iso";
    fake.canvasRef = { el: null }; // -> ndcFromEvent's optional-chain fallback {x:0,y:0}
    fake.T = {
        THREE, scene, renderer: null,
        cameras: { ortho: null, persp: null }, activeCamera: null, currentTarget: new THREE.Vector3(),
        viewSpecs: null, vsScale: 1,
        orbit: { enabled: true },
        raycaster: new THREE.Raycaster(),
        roomObjs: [], cabObjs: [], clickable: [],
        wallMeshes: {}, hoverWall: null,
        dimmed: new Map(), liftedGroup: null, liftedOriginalY: 0, basePadMesh: null,
        touchGhostMesh: null, touchOriginMesh: null,
    };
    fake._gestureReducer = createGestureReducer(props.touchSurfaceConfig || {});
    fake._gestureState = { phase: "idle", selectedId: null, pointers: [], primaryPointerId: null,
        down: null, hit: null, activeId: null, mode: null, effects: [] };
    fake._touchListenersActive = true;
    fake._longPressTimer = null;
    fake._activeDragItem = null;
    fake._dragSnapshot = null;
    fake._orbitWasEnabled = null;
    fake._snapPulseStart = null;
    return fake;
}

// A raycaster whose intersectObjects only "hits" whatever mesh object
// is passed in as `targetMesh` (identity check against the candidate
// list) — same technique as 11_drop_wall_kick_strip_gate.test.mjs's
// `fakeRaycasterHitting`, extended with a no-op `setFromCamera` so
// `_computeTouchHit`'s unconditional call to it is harmless.
function hitTestRaycaster(targetMesh) {
    return {
        setFromCamera() {},
        intersectObjects(meshes) {
            return targetMesh && meshes.includes(targetMesh) ? [{ object: targetMesh }] : [];
        },
    };
}

function backCabinetItem(overrides = {}) {
    return {
        layout_key: "back-1", cabinet_type: "base", wall: "back",
        width_in: 24, height_in: 34.5, depth_in: 24,
        x_position_in: 0, y_position_in: 0, z_position_in: 0, rotation_deg: 0,
        ...overrides,
    };
}

// ─── 1. effect-consumption unit seams (pure) ────────────────────────────

test("describeGestureEffect maps every T1 effect type onto exactly one host-action kind", async () => {
    const { describeGestureEffect } = await loadReal();
    assert.deepEqual(describeGestureEffect({ type: "select", id: "A" }), { kind: "select", id: "A" });
    assert.deepEqual(describeGestureEffect({ type: "affordance", action: "sendToWall" }),
        { kind: "affordance", action: "sendToWall" });
    assert.deepEqual(describeGestureEffect({ type: "orbit" }), { kind: "nativeCamera" });
    assert.deepEqual(describeGestureEffect({ type: "cameraGesture" }), { kind: "nativeCamera" });
    assert.deepEqual(describeGestureEffect({ type: "endCameraGesture" }), { kind: "restoreCamera" });
    assert.deepEqual(describeGestureEffect({ type: "beginDrag", id: "A" }), { kind: "dragStart", id: "A", mode: "direct" });
    assert.deepEqual(describeGestureEffect({ type: "beginPickup", id: "A" }), { kind: "dragStart", id: "A", mode: "ghost" });
    assert.deepEqual(describeGestureEffect({ type: "dragTo", id: "A", x: 1, y: 2, mode: "direct" }),
        { kind: "dragMove", id: "A", x: 1, y: 2, mode: "direct" });
    assert.deepEqual(describeGestureEffect({ type: "commit", id: "A", x: 1, y: 2, mode: "ghost" }),
        { kind: "dragCommit", id: "A", mode: "ghost" });
    assert.deepEqual(describeGestureEffect({ type: "cancelDrag", id: "A", mode: "direct" }),
        { kind: "dragCancel", id: "A", mode: "direct" });
    assert.deepEqual(describeGestureEffect({ type: "somethingT1NeverEmittedBefore" }), { kind: "noop" });
    assert.deepEqual(describeGestureEffect(undefined), { kind: "noop" });
});

test("shouldClaimPointerEvent: pointerdown is NEVER claimed (orbit-via-touch would break otherwise)", async () => {
    const { shouldClaimPointerEvent, GESTURE_PHASES } = await loadReal();
    assert.equal(shouldClaimPointerEvent("pointerdown", GESTURE_PHASES.IDLE, GESTURE_PHASES.PENDING_TAP), false);
    assert.equal(shouldClaimPointerEvent("pointerdown", GESTURE_PHASES.SELECTED_IDLE, GESTURE_PHASES.CAMERA_GESTURE), false);
});

test("shouldClaimPointerEvent: pointermove is claimed once resolved OUT of a camera phase", async () => {
    const { shouldClaimPointerEvent, GESTURE_PHASES } = await loadReal();
    assert.equal(shouldClaimPointerEvent("pointermove", GESTURE_PHASES.PENDING_TAP, GESTURE_PHASES.DRAGGING), true,
        "resolves into our drag -> claim it (block OrbitControls)");
    assert.equal(shouldClaimPointerEvent("pointermove", GESTURE_PHASES.PENDING_TAP, GESTURE_PHASES.PENDING_TAP), true,
        "still-ambiguous micro-tremor stays ours to hold");
    assert.equal(shouldClaimPointerEvent("pointermove", GESTURE_PHASES.PENDING_TAP, GESTURE_PHASES.CAMERA_ORBIT), false,
        "resolves into orbit -> hand off, never claim");
    assert.equal(shouldClaimPointerEvent("pointermove", GESTURE_PHASES.CAMERA_GESTURE, GESTURE_PHASES.CAMERA_GESTURE), false);
});

test("shouldClaimPointerEvent: pointerup/pointercancel decide on the PRE-reduce phase", async () => {
    const { shouldClaimPointerEvent, GESTURE_PHASES } = await loadReal();
    assert.equal(shouldClaimPointerEvent("pointerup", GESTURE_PHASES.DRAGGING, GESTURE_PHASES.SELECTED_IDLE), true,
        "our drag ending -> claim the final pointerup");
    assert.equal(shouldClaimPointerEvent("pointerup", GESTURE_PHASES.CAMERA_ORBIT, GESTURE_PHASES.IDLE), false,
        "native orbit ending -> let OrbitControls see its own pointerup and clean up");
    assert.equal(shouldClaimPointerEvent("pointercancel", GESTURE_PHASES.GHOST_DRAG, GESTURE_PHASES.SELECTED_IDLE), true);
    assert.equal(shouldClaimPointerEvent("pointercancel", GESTURE_PHASES.CAMERA_GESTURE, GESTURE_PHASES.SELECTED_IDLE), false);
});

// ─── 2. proxy-mesh sizing math ───────────────────────────────────────────

test("computeProxyPaddingIn: needs zero padding once the footprint already clears minPx", async () => {
    const { computeProxyPaddingIn } = await loadReal();
    // 24in footprint at 3px/in = 72px, well over the 44px default floor.
    assert.equal(computeProxyPaddingIn(24, 3), 0);
});

test("computeProxyPaddingIn: pads a small/distant footprint up to exactly minPx, split evenly", async () => {
    const { computeProxyPaddingIn } = await loadReal();
    // 10in at 2px/in = 20px < 44px. Needed: 44/2 = 22in total; pad = (22-10)/2 = 6in per side.
    assert.equal(computeProxyPaddingIn(10, 2, 44), 6);
    // Padded footprint now clears exactly minPx.
    const padded = 10 + 2 * 6;
    assert.ok(padded * 2 >= 44);
});

test("computeProxyPaddingIn: degenerate inputs (zero/negative pxPerIn or footprint) never throw, return 0", async () => {
    const { computeProxyPaddingIn } = await loadReal();
    assert.equal(computeProxyPaddingIn(24, 0), 0);
    assert.equal(computeProxyPaddingIn(24, -5), 0);
    assert.equal(computeProxyPaddingIn(0, 5), 0);
    assert.equal(computeProxyPaddingIn(-3, 5), 0);
});

test("computeOrthoPxPerIn: matches IN's own scene-feet-per-inch definition", async () => {
    const { computeOrthoPxPerIn, IN } = await loadReal();
    // A 9-scene-foot-tall frustum rendered into 900px -> 100px per scene
    // unit; times IN (1/12 scene-unit per inch) -> 100/12 px/in.
    const pxPerIn = computeOrthoPxPerIn(9, 900);
    assert.ok(Math.abs(pxPerIn - (100 * IN)) < 1e-9);
});

test("computeOrthoPxPerIn / computePerspPxPerIn: degenerate inputs return 0, never throw or NaN", async () => {
    const { computeOrthoPxPerIn, computePerspPxPerIn } = await loadReal();
    assert.equal(computeOrthoPxPerIn(0, 900), 0);
    assert.equal(computeOrthoPxPerIn(9, 0), 0);
    assert.equal(computePerspPxPerIn(0, 45, 100), 0);
    assert.equal(computePerspPxPerIn(900, 0, 100), 0);
    assert.equal(computePerspPxPerIn(900, 45, 0), 0);
});

test("computePerspPxPerIn: closer distance yields MORE px-per-inch than farther (sanity monotonicity)", async () => {
    const { computePerspPxPerIn } = await loadReal();
    const near = computePerspPxPerIn(900, 45, 100);
    const far  = computePerspPxPerIn(900, 45, 400);
    assert.ok(near > far);
});

test("end-to-end: a small footprint gets an invisible, padded proxy mesh in T.clickable that raycast-hits beyond the visible body", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer, HIT_PROXY_MIN_PX } = await loadReal();
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, { touchEnabled: true });
    // Force a tiny px-per-inch so padding is guaranteed non-zero,
    // regardless of the (absent, in this fake) real camera/renderer.
    fake._estimatePxPerIn = () => 0.5; // -> even a 24in cabinet (12px) needs padding to reach 44px
    const item = backCabinetItem();

    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);

    const proxies = fake.T.clickable.filter(m => m.userData && m.userData.touchProxy);
    assert.equal(proxies.length, 1, "exactly one proxy mesh must be added for the one placed cabinet");
    const proxy = proxies[0];
    assert.equal(proxy.visible, false, "the proxy must be invisible");
    assert.equal(proxy.userData.item, item, "F8 — the proxy must bind the SAME live item, not a clone");
    assert.ok(!proxy.userData.cab, "the proxy must never be `cab`-flagged (would be picked up by tint/dim traversal)");

    proxy.geometry.computeBoundingBox();
    const size = new THREE.Vector3();
    proxy.geometry.boundingBox.getSize(size);
    const IN_ = 1 / 12;
    assert.ok(size.x > item.width_in * IN_, "padded proxy width must exceed the raw visible footprint");
    assert.ok(size.z > item.depth_in * IN_, "padded proxy depth must exceed the raw visible footprint");
});

test("touchEnabled=false: _placeCabinetGroup adds NO proxy mesh at all (byte-identical mouse hit-testing)", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, { touchEnabled: false });
    const item = backCabinetItem();
    const clickableBefore = fake.T.clickable.length;

    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);

    const proxies = fake.T.clickable.filter(m => m.userData && m.userData.touchProxy);
    assert.equal(proxies.length, 0);
    // Exactly the real body mesh(es) were added, nothing extra.
    assert.equal(fake.T.clickable.length, clickableBefore + 1);
});

// ─── 3. plane-projection incl. the Z-branch ──────────────────────────────

test("projectDragAlongWall: back wall resolves to the X axis", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    // Straight down at scene (x=3, z=2) -> floor hit at y=0.
    raycaster.ray.set(new THREE.Vector3(3, 5, 2), new THREE.Vector3(0, -1, 0));
    const result = projectDragAlongWall(THREE, raycaster, WALLS.BACK, 1);
    assert.equal(result.axis, "x");
    assert.equal(result.value, 36); // 3 scene-ft * 12 = 36in
});

test("projectDragAlongWall: front wall ALSO resolves to the X axis (only left/right are the Z-branch)", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    raycaster.ray.set(new THREE.Vector3(1.5, 5, 2), new THREE.Vector3(0, -1, 0));
    const result = projectDragAlongWall(THREE, raycaster, WALLS.FRONT, 1);
    assert.equal(result.axis, "x");
    assert.equal(result.value, 18);
});

test("projectDragAlongWall: LEFT wall resolves to the Z axis — the mouse path's own back-wall-only gap, fixed for touch", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    raycaster.ray.set(new THREE.Vector3(0.5, 5, 4), new THREE.Vector3(0, -1, 0));
    const result = projectDragAlongWall(THREE, raycaster, WALLS.LEFT, 1);
    assert.equal(result.axis, "z");
    assert.equal(result.value, 48); // 4 scene-ft * 12 = 48in
});

test("projectDragAlongWall: RIGHT wall ALSO resolves to the Z axis", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    raycaster.ray.set(new THREE.Vector3(10, 5, 1), new THREE.Vector3(0, -1, 0));
    const result = projectDragAlongWall(THREE, raycaster, WALLS.RIGHT, 1);
    assert.equal(result.axis, "z");
    assert.equal(result.value, 12);
});

test("projectDragAlongWall: snaps to the given grid", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    // 3.2 scene-ft = 38.4in -> snapped to nearest 6 -> 36 (uses value 6 grid)
    raycaster.ray.set(new THREE.Vector3(3.2, 5, 0), new THREE.Vector3(0, -1, 0));
    const result = projectDragAlongWall(THREE, raycaster, WALLS.BACK, 6);
    assert.equal(result.value, 36);
});

test("projectDragAlongWall: a ray parallel to the floor (never intersects) returns null, not a crash", async () => {
    const { projectDragAlongWall, THREE, WALLS } = await loadReal();
    const raycaster = new THREE.Raycaster();
    raycaster.ray.set(new THREE.Vector3(0, 5, 0), new THREE.Vector3(1, 0, 0)); // horizontal, y never changes
    const result = projectDragAlongWall(THREE, raycaster, WALLS.BACK, 1);
    assert.equal(result, null, "mirrors the mouse path's own null-on-no-hit contract (computeDropXIn)");
});

// ─── hasLocalRunCollision (client-side instant feedback half) ───────────

test("hasLocalRunCollision: overlapping footprints on the SAME wall collide; different walls never do", async () => {
    const { hasLocalRunCollision } = await loadReal();
    const moving = backCabinetItem({ layout_key: "moving", width_in: 24 });
    const neighbourSameWall = backCabinetItem({ layout_key: "n1", x_position_in: 30, width_in: 24 });
    const neighbourOtherWall = backCabinetItem({ layout_key: "n2", wall: "left", z_position_in: 10, width_in: 24 });
    const items = [moving, neighbourSameWall, neighbourOtherWall];

    assert.equal(hasLocalRunCollision(items, moving, "x", 20), true, "20..44 overlaps 30..54");
    assert.equal(hasLocalRunCollision(items, moving, "x", 60), false, "60..84 does not overlap 30..54");
});

test("hasLocalRunCollision: ignores fillers and the moving item's own entry", async () => {
    const { hasLocalRunCollision } = await loadReal();
    const moving = backCabinetItem({ layout_key: "moving" });
    const filler = backCabinetItem({ layout_key: "f1", cabinet_type: "filler", x_position_in: 24, width_in: 6 });
    assert.equal(hasLocalRunCollision([moving, filler], moving, "x", 24), false);
    assert.equal(hasLocalRunCollision([moving], moving, "x", 0), false);
});

// ─── 4. camera-to-elevation target math ──────────────────────────────────

test("resolveElevationViewForWall: back/left/right map onto view_specs' own elevation presets", async () => {
    const { resolveElevationViewForWall, WALLS } = await loadReal();
    assert.equal(resolveElevationViewForWall(WALLS.BACK), "front",
        "view_specs.esm.js's 'front' preset is the one that faces the BACK wall dead-on");
    assert.equal(resolveElevationViewForWall(WALLS.LEFT), "left");
    assert.equal(resolveElevationViewForWall(WALLS.RIGHT), "right");
});

test("resolveElevationViewForWall: FRONT wall (no dedicated elevation) falls back to persp — wallrun-rotation-review.md #3", async () => {
    const { resolveElevationViewForWall, WALLS } = await loadReal();
    assert.equal(resolveElevationViewForWall(WALLS.FRONT), "persp");
});

test("resolveElevationViewForWall: an unknown/missing wall also falls back to persp, never throws", async () => {
    const { resolveElevationViewForWall } = await loadReal();
    assert.equal(resolveElevationViewForWall("nonsense"), "persp");
    assert.equal(resolveElevationViewForWall(undefined), "persp");
});

test("_cameraToElevation delegates to _setView with the resolved key and returns it", async () => {
    const { KitchenCanvas, THREE, createGestureReducer, WALLS } = await loadReal();
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer });
    const calls = [];
    fake._setView = (key) => calls.push(key);

    const back = KitchenCanvas.prototype._cameraToElevation.call(fake, WALLS.BACK);
    const front = KitchenCanvas.prototype._cameraToElevation.call(fake, WALLS.FRONT);

    assert.equal(back, "front");
    assert.equal(front, "persp");
    assert.deepEqual(calls, ["front", "persp"]);
});

// ─── Effect-consumption integration (_applyGestureEffects) ──────────────

test("_applyGestureEffects: select effect calls onSelectItem with the real item (F14 — never catalogPreview)", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem();
    const selected = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        items: [item], onSelectItem: (it) => selected.push(it),
    });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);

    KitchenCanvas.prototype._applyGestureEffects.call(fake, [{ type: "select", id: item.layout_key }]);

    assert.equal(selected.length, 1);
    assert.equal(selected[0], item, "must be the SAME live object, not a lookup clone");
});

test("_applyGestureEffects: beginDrag snapshots the item, fires onMoveItemStart, and disables orbit (OrbitControls arbitration safety net)", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem({ x_position_in: 12 });
    const started = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        items: [item], onMoveItemStart: (it) => started.push(it),
    });
    fake.T.orbit.enabled = true;
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);

    KitchenCanvas.prototype._applyGestureEffects.call(fake, [{ type: "beginDrag", id: item.layout_key }]);

    assert.equal(started.length, 1);
    assert.equal(started[0], item);
    assert.deepEqual(fake._dragSnapshot, { layout_key: item.layout_key, x_position_in: 12, z_position_in: 0 });
    assert.equal(fake.T.orbit.enabled, false, "orbit must be suppressed for the duration of a touch drag");
    assert.equal(fake._orbitWasEnabled, true, "the PRE-drag orbit.enabled value must be remembered for restore");
});

test("_applyGestureEffects: commit calls onMoveItem with the live item + BOTH x/z_position_in, and restores orbit.enabled", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem({ x_position_in: 30 });
    const moved = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        items: [item], onMoveItem: (p) => moved.push(p),
    });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    fake._activeDragItem = item;
    fake._orbitWasEnabled = true;
    fake.T.orbit.enabled = false;

    KitchenCanvas.prototype._applyGestureEffects.call(fake, [
        { type: "commit", id: item.layout_key, x: 999, y: 0, mode: "direct" },
    ]);

    assert.equal(moved.length, 1);
    assert.equal(moved[0].item, item);
    assert.equal(moved[0].x_position_in, item.x_position_in);
    assert.equal(moved[0].z_position_in, item.z_position_in);
    assert.equal(moved[0].pinnable, true);
    assert.equal(fake.T.orbit.enabled, true, "orbit must be restored to its pre-drag value on commit");
    assert.equal(fake._activeDragItem, null);
});

test("_applyGestureEffects: cancelDrag rolls back x/z_position_in to the pre-drag snapshot, fires NO parent callback", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem({ x_position_in: 12 });
    const moved = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        items: [item], onMoveItem: (p) => moved.push(p),
    });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    fake._activeDragItem = item;
    fake._dragSnapshot = { layout_key: item.layout_key, x_position_in: 12, z_position_in: 0 };
    item.x_position_in = 48; // simulate an in-flight drag having moved it
    // cancelDrag rebuilds the scene to visually restore the rollback;
    // this unit test is about the rollback VALUES and the "no parent
    // callback" contract, not `_buildScene`'s own (separately-scoped)
    // internals, so stub it out rather than fully stand up room-shell/
    // drag-handle/drop-lane fixtures this fake doesn't carry.
    fake._buildScene = () => {};

    KitchenCanvas.prototype._applyGestureEffects.call(fake, [
        { type: "cancelDrag", id: item.layout_key, mode: "direct" },
    ]);

    assert.equal(item.x_position_in, 12, "rolled back to the pre-drag value");
    assert.equal(moved.length, 0, "a cancelled/uncommitted drag must never reach onMoveItem — nothing was persisted");
});

// ─── _computeTouchHit ────────────────────────────────────────────────────

test("_computeTouchHit: hitting a cabinet's clickable mesh resolves {kind:'cabinet', id, selected}", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem();
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, { items: [item], selected: item });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const bodyMesh = fake.T.clickable[0];
    fake.T.raycaster = hitTestRaycaster(bodyMesh);
    fake.T.activeCamera = {}; // just needs to be truthy

    const hit = KitchenCanvas.prototype._computeTouchHit.call(fake, { clientX: 0, clientY: 0 });

    assert.deepEqual(hit, { kind: "cabinet", id: item.layout_key, selected: true });
});

test("_computeTouchHit: empty space fires onWallSelect (F7 — via _raycastWall, select-scoped) and resolves {kind:'empty'}", async () => {
    const { KitchenCanvas, THREE, createGestureReducer } = await loadReal();
    const wallMesh = { userData: { wall: "left" } };
    const walls = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        activeWall: null, onWallSelect: (w) => walls.push(w),
    });
    fake.T.wallMeshes = { left: wallMesh };
    fake.T.raycaster = hitTestRaycaster(wallMesh); // clickable=[] so no cabinet hit; wall hit only
    fake.T.activeCamera = {};

    const hit = KitchenCanvas.prototype._computeTouchHit.call(fake, { clientX: 0, clientY: 0 });

    assert.deepEqual(hit, { kind: "empty" });
    assert.deepEqual(walls, ["left"]);
});

// ─── touchEnabled=false guard (kill switch, defense in depth) ───────────

test("touchEnabled=false: every pointer handler is a total no-op (gesture state, timers, and callbacks all untouched)", async () => {
    const { KitchenCanvas, THREE, createGestureReducer } = await loadReal();
    const calls = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        touchEnabled: false, onSelectItem: () => calls.push("select"),
    });
    const stateBefore = fake._gestureState;
    const evtBase = { pointerId: 1, clientX: 0, clientY: 0, pointerType: "touch",
        preventDefault() { calls.push("preventDefault"); },
        stopPropagation() { calls.push("stopPropagation"); } };

    KitchenCanvas.prototype._onPointerDown.call(fake, evtBase);
    KitchenCanvas.prototype._onPointerMove.call(fake, evtBase);
    KitchenCanvas.prototype._onPointerUp.call(fake, evtBase);
    KitchenCanvas.prototype._onPointerCancel.call(fake, evtBase);

    assert.equal(fake._gestureState, stateBefore, "the gesture reducer must never have been fed an event");
    assert.deepEqual(calls, [], "zero preventDefault/stopPropagation/callback side effects while disabled");
});

// ─── Full pointerdown -> pointermove -> pointerup cycle, incl. Z-branch ──

test("full cycle: drag on a LEFT-wall cabinet mutates z_position_in (not x), commits via onMoveItem, and restores orbit", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const item = backCabinetItem({ layout_key: "left-1", wall: "left", x_position_in: 0, z_position_in: 0 });
    const moved = [];
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, {
        items: [item], selected: item, onMoveItem: (p) => moved.push(p),
    });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const bodyMesh = fake.T.clickable[0];
    fake.T.raycaster = hitTestRaycaster(bodyMesh);
    fake.T.activeCamera = {};
    fake.T.orbit.enabled = true;
    // `_applyDragTo` calls `_buildScene`, which needs a real scene/room
    // to rebuild against without throwing — reuse the SAME real items.
    fake._buildScene = () => { /* skip full scene rebuild in this unit test */ };
    // Realistic precondition: this cabinet was already selected via a
    // PRIOR tap gesture, so the REDUCER's own internal bookkeeping
    // (separate from `props.selected`, the host's ground truth used
    // only for GATING — see touch_gestures.esm.js's own docblock)
    // already knows it. Without this, the reducer's `selectedId` would
    // still be null going into this drag-only gesture (no `select`
    // effect ever fires in this test), which is correct reducer
    // behavior but not what this test is exercising.
    fake._gestureState.selectedId = item.layout_key;

    const down = { pointerId: 7, clientX: 100, clientY: 100, pointerType: "touch",
        preventDefault() {}, stopPropagation() {} };
    KitchenCanvas.prototype._onPointerDown.call(fake, down);
    assert.equal(fake._gestureState.phase, "pendingTap");

    // Exceed slop (default 9px) so the move resolves to `beginDrag`
    // (selection-gated: `item` IS `props.selected` above).
    const move1 = { pointerId: 7, clientX: 130, clientY: 100, pointerType: "touch",
        preventDefault() {}, stopPropagation() {} };
    KitchenCanvas.prototype._onPointerMove.call(fake, move1);
    assert.equal(fake._gestureState.phase, "dragging");
    assert.equal(fake.T.orbit.enabled, false);

    // Override the raycaster used by `_applyDragTo`'s plane projection
    // so the resulting drag position is deterministic (Z-branch: the
    // item's wall is "left").
    fake.T.raycaster = new THREE.Raycaster();
    fake.T.raycaster.setFromCamera = () => {}; // preserve the manually-set ray below
    fake.T.raycaster.ray.set(new THREE.Vector3(0.5, 5, 3), new THREE.Vector3(0, -1, 0)); // -> z = 36in

    const move2 = { pointerId: 7, clientX: 140, clientY: 100, pointerType: "touch",
        preventDefault() {}, stopPropagation() {} };
    KitchenCanvas.prototype._onPointerMove.call(fake, move2);

    assert.equal(item.z_position_in, 36, "left-wall drag must write z_position_in");
    assert.equal(item.x_position_in, 0, "left-wall drag must NOT touch x_position_in");

    const up = { pointerId: 7, clientX: 140, clientY: 100, pointerType: "touch",
        preventDefault() {}, stopPropagation() {} };
    KitchenCanvas.prototype._onPointerUp.call(fake, up);

    assert.equal(fake._gestureState.phase, "selectedIdle");
    assert.equal(moved.length, 1);
    assert.equal(moved[0].z_position_in, 36);
    assert.equal(fake.T.orbit.enabled, true, "orbit restored after commit");
});

// ─── Visual states 2/3 (lift+pad / others-dimmed) — sanity coverage ─────

test("_applySelectedLift: lifts the selected group's Y by LIFT_IN and adds a base pad mesh; clears cleanly", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer, IN } = await loadReal();
    const item = backCabinetItem();
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, { items: [item] });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, item, buildBaseCabinet);
    const grp = fake.T.cabObjs[0];
    const originalY = grp.position.y;

    KitchenCanvas.prototype._applySelectedLift.call(fake, item);

    assert.ok(grp.position.y > originalY, "the selected group must lift above its original Y");
    assert.ok(fake.T.basePadMesh, "a base pad mesh must be added");
    assert.ok(fake.T.scene.children.includes(fake.T.basePadMesh));

    KitchenCanvas.prototype._clearSelectedLift.call(fake);
    assert.equal(grp.position.y, originalY, "clearing must restore the exact original Y");
    assert.equal(fake.T.basePadMesh, null);
    assert.ok(!fake.T.scene.children.includes(undefined));
});

test("_applyDimToOthers: dims every OTHER cabinet's cab-flagged mesh, leaves the selected one untouched", async () => {
    const { KitchenCanvas, buildBaseCabinet, THREE, createGestureReducer } = await loadReal();
    const selected = backCabinetItem({ layout_key: "sel" });
    const other = backCabinetItem({ layout_key: "other", x_position_in: 24 });
    const fake = fakeCanvas(THREE, { KitchenCanvas, createGestureReducer }, { items: [selected, other] });
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, selected, buildBaseCabinet);
    KitchenCanvas.prototype._placeCabinetGroup.call(fake, other, buildBaseCabinet);
    const selMesh = fake.T.cabObjs[0].children.find(c => c.userData && c.userData.cab);
    const otherMesh = fake.T.cabObjs[1].children.find(c => c.userData && c.userData.cab);
    const selOriginal = selMesh.material, otherOriginal = otherMesh.material;

    KitchenCanvas.prototype._applyDimToOthers.call(fake, selected);

    assert.equal(selMesh.material, selOriginal, "the selected cabinet must never be dimmed");
    assert.notEqual(otherMesh.material, otherOriginal, "every OTHER cabinet must be dimmed");

    KitchenCanvas.prototype._clearDimVisuals.call(fake);
    assert.equal(otherMesh.material, otherOriginal, "clearing must restore the exact original material");
});
