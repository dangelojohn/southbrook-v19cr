/**
 * Touch editing Wave 1 contract — manipulation_states.esm.js.
 *
 * Pure-constants + pure-function module (no OWL, no DOM, no THREE) so
 * this contract imports it directly by relative path — same pattern
 * as contract #29 (catalog_list.esm.js) / #30 (cabinet_thumb.esm.js).
 * Unlike those two, this module DOES import from sibling canvas files
 * (`@southbrook_kitchen_3d_configurator/js/canvas/constants.esm` for
 * `P`, `.../color_utils.esm` for `mixHex`) using the same bare-alias
 * convention every other canvas module uses (selection.esm.js,
 * corner_cabinet.esm.js, kitchen_canvas.esm.js, ...) — those resolve
 * fine here because `harness/loader.mjs` maps the
 * "@southbrook_kitchen_3d_configurator/..." prefix generically for the
 * whole process (registered via `node --import ./harness/register.mjs`
 * in package.json's `test` script), exactly as contract #3 already
 * relies on for kitchen_canvas.esm.js's own alias imports.
 *
 * Scope: wave 1 ships this file as scene-side constants + pure helpers
 * ONLY — no wiring into selection.esm.js/kitchen_canvas.esm.js yet
 * (wave 2). This contract pins the constant VALUES (traceable to
 * manipulation-visual-spec.md's measured contrast table) and the pure
 * helpers' behaviour, so wave 2's wiring has a stable, tested surface
 * to build on.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CANVAS_DIR = path.resolve(HERE, "..", "..", "..", "static", "src", "js", "canvas");

const MANIPULATION_STATES_SRC = path.join(CANVAS_DIR, "manipulation_states.esm.js");
const COLOR_UTILS_SRC = path.join(CANVAS_DIR, "color_utils.esm.js");
const CONSTANTS_SRC = path.join(CANVAS_DIR, "constants.esm.js");

const {
    SELECT_BLUE,
    COLLISION_RED,
    ORIGIN_WIREFRAME,
    DIM_AMOUNT,
    dimColor,
    LIFT_MS,
    DIM_MS,
    ROLLBACK_MS,
    CORRECTION_MS,
    SNAP_PULSE_MS,
    LIFT_IN,
    GHOST_OPACITY,
    ORIGIN_OPACITY,
    BASE_PAD_OPACITY,
    HIT_PROXY_MIN_PX,
    MANIPULATION_STATE,
    snapPulse,
    hapticTick,
} = await import(MANIPULATION_STATES_SRC);

const { mixHex } = await import(COLOR_UTILS_SRC);
const { P } = await import(CONSTANTS_SRC);

// ─── Colour constants ───────────────────────────────────────────────────────

test("colour constants match the visual spec's measured hex values", () => {
    assert.equal(SELECT_BLUE, 0x1866D4);
    assert.equal(COLLISION_RED, 0xA6281E);
    assert.equal(ORIGIN_WIREFRAME, 0x7d858e);
});

test("SELECT_BLUE is the SAME source as constants.esm.js's P.sel/P.arrow — not a duplicated literal that could drift", () => {
    assert.equal(SELECT_BLUE, P.sel);
    assert.equal(SELECT_BLUE, P.arrow);
});

// ─── Timings (ms) ───────────────────────────────────────────────────────────

test("timing constants (ms) match the visual spec", () => {
    assert.equal(LIFT_MS, 180);
    assert.equal(DIM_MS, 120);
    assert.equal(ROLLBACK_MS, 200);
    assert.equal(CORRECTION_MS, 150);
    assert.equal(SNAP_PULSE_MS, 220);
});

// ─── Geometry ───────────────────────────────────────────────────────────────

test("geometry constants match the visual spec", () => {
    assert.equal(LIFT_IN, 0.25);
    assert.equal(GHOST_OPACITY, 0.35);
    assert.equal(ORIGIN_OPACITY, 0.18);
    assert.equal(BASE_PAD_OPACITY, 0.35);
    assert.equal(HIT_PROXY_MIN_PX, 44);
});

// ─── dimColor delegates to mixHex (SBK-036 — must not fork) ────────────────

test("DIM_AMOUNT matches the spec's mixHex(orig, sceneBg, 0.30) blend amount", () => {
    assert.equal(DIM_AMOUNT, 0.30);
});

test("dimColor delegates to the shared mixHex — identical output for identical inputs, not a reimplementation", () => {
    const cases = [
        [0xC9C4BC, 0xECE9E3, 0.30],
        [0xA9A59E, 0xECE9E3, 0.30],
        [0xFFFFFF, 0x000000, 0.5],
        [0x1866D4, 0xECE9E3, 0],
        [0x1866D4, 0xECE9E3, 1],
    ];
    for (const [orig, bg, amt] of cases) {
        assert.equal(dimColor(orig, bg, amt), mixHex(orig, bg, amt));
    }
});

test("dimColor defaults its blend amount to DIM_AMOUNT (0.30) when omitted", () => {
    assert.equal(dimColor(0xC9C4BC, 0xECE9E3), mixHex(0xC9C4BC, 0xECE9E3, 0.30));
    assert.equal(dimColor(0xC9C4BC, 0xECE9E3), dimColor(0xC9C4BC, 0xECE9E3, 0.30));
});

// ─── hapticTick — NEVER throws ──────────────────────────────────────────────

/**
 * Temporarily stubs `globalThis.navigator` for the duration of `fn`,
 * restoring the original property descriptor afterward (Node's own
 * `navigator` global is a configurable accessor property, not a plain
 * writable value — see globalThis's own descriptor — so this always
 * goes through `defineProperty`, never `navigator = ...`).
 */
function withNavigator(value, fn) {
    const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    Object.defineProperty(globalThis, "navigator", {
        value, configurable: true, writable: true, enumerable: true,
    });
    try {
        return fn();
    } finally {
        if (original) {
            Object.defineProperty(globalThis, "navigator", original);
        } else {
            delete globalThis.navigator;
        }
    }
}

test("hapticTick never throws and returns false when navigator is undefined", () => {
    withNavigator(undefined, () => {
        assert.doesNotThrow(() => assert.equal(hapticTick(), false));
    });
});

test("hapticTick never throws and returns false when navigator.vibrate is not a function", () => {
    withNavigator({}, () => {
        assert.doesNotThrow(() => assert.equal(hapticTick(), false));
    });
});

test("hapticTick calls navigator.vibrate(10) and returns true when the browser reports success (Android path)", () => {
    let calledWith = null;
    withNavigator({ vibrate: (ms) => { calledWith = ms; return true; } }, () => {
        assert.equal(hapticTick(), true);
    });
    assert.equal(calledWith, 10);
});

test("hapticTick never throws even if navigator.vibrate itself throws (e.g. called outside a user gesture)", () => {
    withNavigator({
        vibrate: () => { throw new Error("NotAllowedError: vibrate requires a user gesture"); },
    }, () => {
        assert.doesNotThrow(() => assert.equal(hapticTick(), false));
    });
});

// ─── snapPulse — pure single triangular pulse, never a repeating buzz ──────

test("snapPulse: 0 at both ends, 0.9 peak at the midpoint (spec: '0 -> 0.9 -> 0')", () => {
    assert.equal(snapPulse(0), 0);
    assert.equal(snapPulse(1), 0);
    assert.equal(snapPulse(0.5), 0.9);
});

test("snapPulse: symmetric ease-in/ease-out around the midpoint", () => {
    assert.equal(snapPulse(0.25), snapPulse(0.75));
    assert.ok(snapPulse(0.25) > 0 && snapPulse(0.25) < 0.9);
});

test("snapPulse: never exceeds the 0.9 peak or goes negative across the full domain", () => {
    for (let t = 0; t <= 1; t += 0.05) {
        const v = snapPulse(t);
        assert.ok(v >= 0 && v <= 0.9 + 1e-9, `snapPulse(${t}) = ${v} out of [0, 0.9]`);
    }
});

test("snapPulse: out-of-range or non-numeric input never throws and returns 0", () => {
    assert.equal(snapPulse(-0.1), 0);
    assert.equal(snapPulse(1.1), 0);
    assert.equal(snapPulse(NaN), 0);
    assert.equal(snapPulse(undefined), 0);
    assert.equal(snapPulse("not a number"), 0);
});

// ─── MANIPULATION_STATE — the 9-state enum, complete and frozen ───────────

test("MANIPULATION_STATE enumerates exactly the 9 states from the visual spec, in spec order", () => {
    assert.deepEqual(Object.keys(MANIPULATION_STATE), [
        "IDLE",
        "SELECTED",
        "DIMMED",
        "DRAG_GHOST",
        "VALID_DROP",
        "COLLISION",
        "SNAP",
        "PRECISION_READOUT",
        "ROTATION_AFFORDANCE",
    ]);
});

test("MANIPULATION_STATE has 9 unique string values (no accidental collisions)", () => {
    const values = Object.values(MANIPULATION_STATE);
    assert.equal(values.length, 9);
    assert.equal(new Set(values).size, 9);
    for (const v of values) {
        assert.equal(typeof v, "string");
    }
});

test("MANIPULATION_STATE is frozen — a mutation attempt throws (module is strict-mode ESM)", () => {
    assert.throws(() => { MANIPULATION_STATE.IDLE = "nope"; }, TypeError);
    assert.equal(MANIPULATION_STATE.IDLE, "idle");
});
