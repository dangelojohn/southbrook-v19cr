/**
 * Touch editing Wave 2 contract — touch_pad.esm.js.
 *
 * Pure-function coverage — the two pieces this task's TESTS requirement
 * calls out explicitly:
 *   1. "pad button-visibility matrix by family (node contract on a pure
 *      visibility function you extract)" -> classifyCabinetFamily /
 *      getPadAffordances.
 *   2. "touch_stats accumulation logic (pure)" -> makeEmptyTouchStats /
 *      accumulateTouchStats / hasTouchStats.
 * Plus the other pure helpers this file exports (nextWall,
 * elevationViewForWall, isTouchCapableAtBoot, touchPadModeForViewport),
 * and a real OWL-mount smoke pass on <TouchPad> itself (props/template
 * compile + render + button-click wiring) — the pure visibility matrix
 * only proves WHICH buttons should show; mounting is what actually
 * proves the template renders them correctly and wires clicks to the
 * right callback prop (a template-expression typo, e.g. `and` instead
 * of the required `&amp;&amp;` for OWL/QWeb boolean expressions, would
 * pass every pure-function test above and still be a runtime error;
 * the mount tests below are what would have caught that class of bug).
 *
 * touch_pad.esm.js DOES import "@odoo/owl" (the <TouchPad> component
 * class), unlike contract #32's manipulation_states.esm.js — Owl's ESM
 * build reads DOM globals (Element, Node, ...) at module-evaluation
 * time (see harness/setup_dom.mjs's own header + mount_configurator.mjs's
 * comment on the same constraint), so `setupDom()` must run BEFORE this
 * file is imported.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "../harness/setup_dom.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOUCH_PAD_SRC = path.resolve(
    HERE, "..", "..", "..", "static", "src", "js", "canvas", "touch_pad.esm.js",
);

const dom = await setupDom();
const owl = await import("@odoo/owl");

const {
    classifyCabinetFamily,
    getPadAffordances,
    nextWall,
    elevationViewForWall,
    NUDGE_STEP_IN,
    isTouchCapableAtBoot,
    touchPadModeForViewport,
    TOUCH_PAD_PHONE_MAX_WIDTH_PX,
    makeEmptyTouchStats,
    accumulateTouchStats,
    hasTouchStats,
    TouchPad,
} = await import(TOUCH_PAD_SRC);

// ─── classifyCabinetFamily ──────────────────────────────────────────────

test("classifyCabinetFamily: null/undefined selection is 'none'", () => {
    assert.equal(classifyCabinetFamily(null), "none");
    assert.equal(classifyCabinetFamily(undefined), "none");
});

test("classifyCabinetFamily: filler/panel are 'none' regardless of zone", () => {
    assert.equal(classifyCabinetFamily({ cabinet_type: "filler" }), "none");
    assert.equal(classifyCabinetFamily({ cabinet_type: "panel" }), "none");
    assert.equal(
        classifyCabinetFamily({ cabinet_type: "filler", zone: "island" }),
        "none",
        "filler stays non-manipulable even with an island zone override",
    );
});

test("classifyCabinetFamily: cabinet_type 'corner' is always 'corner'", () => {
    assert.equal(classifyCabinetFamily({ cabinet_type: "corner" }), "corner");
    assert.equal(
        classifyCabinetFamily({ cabinet_type: "corner", zone: "island" }),
        "corner",
        "corner cabinet_type wins over any zone value",
    );
});

test("classifyCabinetFamily: zone 'island' on a non-corner item is 'island'", () => {
    assert.equal(classifyCabinetFamily({ cabinet_type: "base", zone: "island" }), "island");
    assert.equal(classifyCabinetFamily({ cabinet_type: "wall", zone: "island" }), "island");
    assert.equal(classifyCabinetFamily({ cabinet_type: "tall", zone: "island" }), "island");
});

test("classifyCabinetFamily: everything else manipulable is 'wallRun'", () => {
    assert.equal(classifyCabinetFamily({ cabinet_type: "base" }), "wallRun");
    assert.equal(classifyCabinetFamily({ cabinet_type: "wall" }), "wallRun");
    assert.equal(classifyCabinetFamily({ cabinet_type: "tall" }), "wallRun");
    assert.equal(classifyCabinetFamily({ cabinet_type: "appliance" }), "wallRun");
    assert.equal(
        classifyCabinetFamily({ cabinet_type: "base", zone: "base_run" }),
        "wallRun",
        "a non-island zone value never promotes to island",
    );
});

// ─── getPadAffordances — the button-visibility matrix ──────────────────

test("getPadAffordances: 'none' family hides every button", () => {
    const a = getPadAffordances({ cabinet_type: "filler" });
    assert.equal(a.family, "none");
    assert.equal(a.nudge, false);
    assert.equal(a.rotate, false);
    assert.equal(a.flipHand, false);
    assert.equal(a.cameraToElevation, false);
    assert.equal(a.sendToWall, false);
    assert.equal(a.remove, false);
});

test("getPadAffordances: 'corner' family — flip-hand only, no rotate/camera/send-to-wall", () => {
    const a = getPadAffordances({ cabinet_type: "corner" });
    assert.equal(a.family, "corner");
    assert.equal(a.nudge, true);
    assert.equal(a.rotate, false);
    assert.equal(a.flipHand, true);
    assert.equal(a.cameraToElevation, false);
    assert.equal(a.sendToWall, false);
    assert.equal(a.remove, true);
});

test("getPadAffordances: 'island' family — free rotate, no flip-hand/camera/send-to-wall", () => {
    const a = getPadAffordances({ cabinet_type: "base", zone: "island" });
    assert.equal(a.family, "island");
    assert.equal(a.nudge, true);
    assert.equal(a.rotate, true);
    assert.equal(a.flipHand, false);
    assert.equal(a.cameraToElevation, false);
    assert.equal(a.sendToWall, false);
    assert.equal(a.remove, true);
});

test("getPadAffordances: 'wallRun' family — camera-to-elevation + send-to-wall, no rotate/flip-hand", () => {
    const a = getPadAffordances({ cabinet_type: "base" });
    assert.equal(a.family, "wallRun");
    assert.equal(a.nudge, true);
    assert.equal(a.rotate, false);
    assert.equal(a.flipHand, false);
    assert.equal(a.cameraToElevation, true);
    assert.equal(a.sendToWall, true);
    assert.equal(a.remove, true);
});

test("getPadAffordances: exactly one of rotate/flipHand/(cameraToElevation+sendToWall) is ever true", () => {
    const families = [
        { cabinet_type: "corner" },
        { cabinet_type: "base", zone: "island" },
        { cabinet_type: "wall" },
    ];
    for (const item of families) {
        const a = getPadAffordances(item);
        const exclusiveFlags = [a.rotate, a.flipHand, a.cameraToElevation && a.sendToWall];
        const trueCount = exclusiveFlags.filter(Boolean).length;
        assert.equal(trueCount, 1,
            `family ${a.family} must satisfy exactly one of rotate/flipHand/wallRun-pair`);
    }
});

// ─── nextWall / elevationViewForWall ────────────────────────────────────

test("nextWall: perimeter-walk cycle back -> right -> front -> left -> back", () => {
    assert.equal(nextWall("back"), "right");
    assert.equal(nextWall("right"), "front");
    assert.equal(nextWall("front"), "left");
    assert.equal(nextWall("left"), "back");
});

test("nextWall: unknown/missing wall never throws and returns a valid wall", () => {
    const VALID = new Set(["back", "left", "right", "front"]);
    for (const bogus of [undefined, null, "", "diagonal"]) {
        assert.ok(VALID.has(nextWall(bogus)), `nextWall(${JSON.stringify(bogus)}) must be a valid wall`);
    }
});

test("elevationViewForWall: left/right map to themselves, back maps to 'front' (its elevation), front maps to 'persp' (no dedicated elevation — T4's canvas confirms this exact fallback)", () => {
    assert.equal(elevationViewForWall("back"), "front");
    assert.equal(elevationViewForWall("left"), "left");
    assert.equal(elevationViewForWall("right"), "right");
    assert.equal(elevationViewForWall("front"), "persp");
});

test("elevationViewForWall: unknown/missing wall falls back to 'front', never throws", () => {
    assert.equal(elevationViewForWall(undefined), "front");
    assert.equal(elevationViewForWall(null), "front");
    assert.equal(elevationViewForWall("diagonal"), "front");
});

// ─── NUDGE_STEP_IN ───────────────────────────────────────────────────────

test("NUDGE_STEP_IN matches drop_raycaster.esm.js's existing 6in snap grid", () => {
    assert.equal(NUDGE_STEP_IN, 6);
});

// ─── isTouchCapableAtBoot ────────────────────────────────────────────────

test("isTouchCapableAtBoot: true when matchMedia(pointer: coarse) matches", () => {
    const win = { matchMedia: () => ({ matches: true }) };
    assert.equal(isTouchCapableAtBoot(win, {}), true);
});

test("isTouchCapableAtBoot: true when maxTouchPoints > 0, independent of matchMedia", () => {
    const win = { matchMedia: () => ({ matches: false }) };
    assert.equal(isTouchCapableAtBoot(win, { maxTouchPoints: 5 }), true);
});

test("isTouchCapableAtBoot: false when neither signal is present", () => {
    const win = { matchMedia: () => ({ matches: false }) };
    assert.equal(isTouchCapableAtBoot(win, { maxTouchPoints: 0 }), false);
    assert.equal(isTouchCapableAtBoot(win, {}), false);
});

test("isTouchCapableAtBoot: never throws on missing globals or a throwing matchMedia", () => {
    assert.equal(isTouchCapableAtBoot(null, null), false);
    assert.equal(isTouchCapableAtBoot(undefined, undefined), false);
    const throwingWin = { matchMedia: () => { throw new Error("nope"); } };
    assert.equal(isTouchCapableAtBoot(throwingWin, {}), false);
});

// ─── touchPadModeForViewport ─────────────────────────────────────────────

test("touchPadModeForViewport: 'dock' when the phone-width media query matches", () => {
    const win = { matchMedia: (q) => ({ matches: q.includes(`${TOUCH_PAD_PHONE_MAX_WIDTH_PX}px`) }) };
    assert.equal(touchPadModeForViewport(win), "dock");
});

test("touchPadModeForViewport: 'floating' when it doesn't match, or matchMedia is missing/throws", () => {
    const win = { matchMedia: () => ({ matches: false }) };
    assert.equal(touchPadModeForViewport(win), "floating");
    assert.equal(touchPadModeForViewport(null), "floating");
    const throwingWin = { matchMedia: () => { throw new Error("nope"); } };
    assert.equal(touchPadModeForViewport(throwingWin), "floating");
});

// ─── touch_stats accumulation (pure) ─────────────────────────────────────

test("makeEmptyTouchStats: zeroed pair", () => {
    assert.deepEqual(makeEmptyTouchStats(), { gesture_rollbacks: 0, gesture_abandons: 0 });
});

test("accumulateTouchStats: increments the matching counter only", () => {
    let stats = makeEmptyTouchStats();
    stats = accumulateTouchStats(stats, "rollback");
    assert.deepEqual(stats, { gesture_rollbacks: 1, gesture_abandons: 0 });
    stats = accumulateTouchStats(stats, "rollback");
    assert.deepEqual(stats, { gesture_rollbacks: 2, gesture_abandons: 0 });
    stats = accumulateTouchStats(stats, "abandon");
    assert.deepEqual(stats, { gesture_rollbacks: 2, gesture_abandons: 1 });
});

test("accumulateTouchStats: never mutates the input object", () => {
    const original = { gesture_rollbacks: 3, gesture_abandons: 1 };
    const frozen = Object.freeze({ ...original });
    const next = accumulateTouchStats(frozen, "rollback");
    assert.deepEqual(frozen, original, "input object must be untouched");
    assert.notEqual(next, frozen, "a NEW object must be returned");
    assert.deepEqual(next, { gesture_rollbacks: 4, gesture_abandons: 1 });
});

test("accumulateTouchStats: unrecognized kind is a normalized no-op copy, never throws", () => {
    const stats = { gesture_rollbacks: 2, gesture_abandons: 5 };
    assert.deepEqual(accumulateTouchStats(stats, "bogus"), stats);
    assert.deepEqual(accumulateTouchStats(stats, undefined), stats);
});

test("accumulateTouchStats: null/malformed base normalizes to zero before incrementing", () => {
    assert.deepEqual(accumulateTouchStats(null, "rollback"), { gesture_rollbacks: 1, gesture_abandons: 0 });
    assert.deepEqual(accumulateTouchStats(undefined, "abandon"), { gesture_rollbacks: 0, gesture_abandons: 1 });
    assert.deepEqual(accumulateTouchStats("not an object", "rollback"), { gesture_rollbacks: 1, gesture_abandons: 0 });
});

test("hasTouchStats: false for zeroed/empty/missing, true once either counter is positive", () => {
    assert.equal(hasTouchStats(makeEmptyTouchStats()), false);
    assert.equal(hasTouchStats(null), false);
    assert.equal(hasTouchStats(undefined), false);
    assert.equal(hasTouchStats({}), false);
    assert.equal(hasTouchStats({ gesture_rollbacks: 1, gesture_abandons: 0 }), true);
    assert.equal(hasTouchStats({ gesture_rollbacks: 0, gesture_abandons: 1 }), true);
});

// ─── Sanity: the component class itself is exported and shaped as expected

test("TouchPad is exported and declares the documented callback props", () => {
    assert.equal(typeof TouchPad, "function");
    const propNames = Object.keys(TouchPad.props);
    for (const expected of [
        "mode", "selection", "currentView", "capabilities",
        "onNudge", "onRotate", "onFlipHand", "onCameraToElevation",
        "onSendToWall", "onRemove", "onUndo",
    ]) {
        assert.ok(propNames.includes(expected), `TouchPad.props must declare ${expected}`);
    }
    assert.equal(TouchPad.defaultProps.mode, "floating");
});

// ─── Real OWL mounts — template compiles + renders + wires clicks ──────

let mountSeq = 0;
async function mountTouchPad(props) {
    const root = dom.window.document.createElement("div");
    dom.window.document.body.appendChild(root);
    mountSeq += 1;
    const component = await owl.mount(TouchPad, root, { props, env: {}, test: true, name: `touchpad-${mountSeq}` });
    return {
        root, component,
        query: (sel) => root.querySelector(sel),
        queryAll: (sel) => Array.from(root.querySelectorAll(sel)),
        destroy: () => { const app = component.__owl__ && component.__owl__.app; if (app) app.destroy(); },
    };
}

const WALL_RUN_ITEM = { cabinet_type: "base", wall: "back", layout_key: "base-1" };
const CORNER_ITEM = { cabinet_type: "corner", wall: "back", layout_key: "corner-1", sku: "SB-CORNER" };
const ISLAND_ITEM = { cabinet_type: "base", zone: "island", wall: "back", layout_key: "island-1" };

test("mount: dock mode renders .o_sbk_touchpad; floating mode renders .o_sbk_touchpad--floating", async () => {
    const dock = await mountTouchPad({ mode: "dock", selection: null });
    assert.ok(dock.query(".o_sbk_touchpad"), "dock mode root class");
    assert.equal(dock.query(".o_sbk_touchpad--floating"), null);
    dock.destroy();

    const floating = await mountTouchPad({ mode: "floating", selection: null });
    assert.ok(floating.query(".o_sbk_touchpad--floating"), "floating mode root class");
    assert.equal(floating.query(".o_sbk_touchpad"), null);
    floating.destroy();
});

test("mount: no selection renders ONLY the persistent undo button", async () => {
    const h = await mountTouchPad({ mode: "floating", selection: null });
    const buttons = h.queryAll(".o_sbk_touchpad_btn");
    assert.equal(buttons.length, 1, "only the undo button should render with nothing selected");
    assert.equal(buttons[0].getAttribute("aria-label"), "Undo last change");
    h.destroy();
});

test("mount: wallRun selection renders nudge x2, camera-to-elevation, send-to-wall, remove, undo — 6 buttons", async () => {
    const h = await mountTouchPad({
        mode: "floating", selection: WALL_RUN_ITEM,
        capabilities: { sendToWallSupported: true, canSendToWall: true },
    });
    const labels = h.queryAll(".o_sbk_touchpad_btn").map((b) => b.getAttribute("aria-label"));
    assert.deepEqual(labels, [
        "Nudge left", "Nudge right", "Camera to elevation view",
        "Send to next wall", "Remove cabinet", "Undo last change",
    ]);
    h.destroy();
});

test("mount: wallRun selection with sendToWallSupported:false hides Send to next wall (portal gap)", async () => {
    const h = await mountTouchPad({
        mode: "floating", selection: WALL_RUN_ITEM,
        capabilities: { sendToWallSupported: false },
    });
    const labels = h.queryAll(".o_sbk_touchpad_btn").map((b) => b.getAttribute("aria-label"));
    assert.ok(!labels.includes("Send to next wall"));
    assert.ok(labels.includes("Camera to elevation view"), "cameraToElevation is unaffected by sendToWallSupported");
    h.destroy();
});

test("mount: corner selection renders nudge x2, flip corner hand, remove, undo — 5 buttons", async () => {
    const h = await mountTouchPad({ mode: "floating", selection: CORNER_ITEM });
    const labels = h.queryAll(".o_sbk_touchpad_btn").map((b) => b.getAttribute("aria-label"));
    assert.deepEqual(labels, [
        "Nudge left", "Nudge right", "Flip corner hand", "Remove cabinet", "Undo last change",
    ]);
    h.destroy();
});

test("mount: island selection renders nudge x2, rotate CCW/CW, remove, undo — 6 buttons", async () => {
    const h = await mountTouchPad({ mode: "floating", selection: ISLAND_ITEM });
    const labels = h.queryAll(".o_sbk_touchpad_btn").map((b) => b.getAttribute("aria-label"));
    assert.deepEqual(labels, [
        "Nudge left", "Nudge right",
        "Rotate 45° counter-clockwise", "Rotate 45° clockwise",
        "Remove cabinet", "Undo last change",
    ]);
    h.destroy();
});

test("mount: button clicks invoke the correct callback prop with the correct argument", async () => {
    const calls = [];
    const h = await mountTouchPad({
        mode: "floating", selection: WALL_RUN_ITEM,
        capabilities: { sendToWallSupported: true, canSendToWall: true },
        onNudge: (dir) => calls.push(["nudge", dir]),
        onCameraToElevation: () => calls.push(["camera"]),
        onSendToWall: () => calls.push(["sendToWall"]),
        onRemove: () => calls.push(["remove"]),
        onUndo: () => calls.push(["undo"]),
    });
    h.query('[aria-label="Nudge left"]').click();
    h.query('[aria-label="Nudge right"]').click();
    h.query('[aria-label="Camera to elevation view"]').click();
    h.query('[aria-label="Send to next wall"]').click();
    h.query('[aria-label="Remove cabinet"]').click();
    h.query('[aria-label="Undo last change"]').click();
    assert.deepEqual(calls, [
        ["nudge", -1], ["nudge", 1], ["camera"], ["sendToWall"], ["remove"], ["undo"],
    ]);
    h.destroy();
});

test("mount: rotate buttons pass ±45° to onRotate (spec §1 decision 3's 45° snap, not the keyboard's 90°)", async () => {
    const calls = [];
    const h = await mountTouchPad({
        mode: "floating", selection: ISLAND_ITEM,
        onRotate: (deg) => calls.push(deg),
    });
    h.query('[aria-label="Rotate 45° counter-clockwise"]').click();
    h.query('[aria-label="Rotate 45° clockwise"]').click();
    assert.deepEqual(calls, [-45, 45]);
    h.destroy();
});

test("mount: undo button is disabled + aria-disabled when capabilities.canUndo is false", async () => {
    const h = await mountTouchPad({ mode: "floating", selection: null, capabilities: { canUndo: false } });
    const btn = h.query('[aria-label="Undo last change"]');
    assert.equal(btn.disabled, true);
    assert.equal(btn.getAttribute("aria-disabled"), "true");
    h.destroy();
});

test("mount: camera-to-elevation aria-pressed reflects whether currentView already matches the mapped elevation", async () => {
    const notActive = await mountTouchPad({
        mode: "floating", selection: WALL_RUN_ITEM, currentView: "top",
    });
    assert.equal(
        notActive.query('[aria-label="Camera to elevation view"]').getAttribute("aria-pressed"),
        "false",
    );
    notActive.destroy();

    // WALL_RUN_ITEM.wall === "back" -> elevationViewForWall("back") === "front".
    const active = await mountTouchPad({
        mode: "floating", selection: WALL_RUN_ITEM, currentView: "front",
    });
    assert.equal(
        active.query('[aria-label="Camera to elevation view"]').getAttribute("aria-pressed"),
        "true",
    );
    active.destroy();
});

test("mount: every rendered button meets the 44px minimum touch-target floor (spec §3)", async () => {
    // jsdom has no layout engine (getBoundingClientRect is always 0),
    // so this pins the SCSS CONTRACT instead: every button carries the
    // shared .o_sbk_touchpad_btn class, which touch_pad.scss (Wave 1,
    // T3) sizes to 56px (dock) / 48px (floating) — both above the 44px
    // floor HIT_PROXY_MIN_PX documents in manipulation_states.esm.js.
    const h = await mountTouchPad({ mode: "floating", selection: WALL_RUN_ITEM });
    for (const btn of h.queryAll("button")) {
        assert.ok(btn.classList.contains("o_sbk_touchpad_btn"),
            `every pad button must carry .o_sbk_touchpad_btn (min-height/width 44px floor) — offender: ${btn.outerHTML}`);
    }
    h.destroy();
});
