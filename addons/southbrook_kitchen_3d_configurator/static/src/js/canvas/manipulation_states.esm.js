/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — touch-editing manipulation states.
 *
 * Wave 1 of the touch-editing build (`docs/superpowers/specs/
 * 2026-08-02-touch-editing-design.md` §3, full detail in
 * `manipulation-visual-spec.md`). This module is scene-side constants +
 * PURE helpers only — no THREE, no DOM, no OWL, no wiring into
 * kitchen_canvas.esm.js/selection.esm.js yet (that's wave 2: the
 * @import + manifest + actual state-machine plumbing). Every value
 * below traces to the visual spec's measured contrast table, not a
 * guess — see the per-constant comments for the exact ratios.
 *
 * The 9 states this module names/serves (visual spec §"The 9 states"):
 *   1 Idle, 2 Selected, 3 Others-dimmed, 4 Drag-ghost, 5 Valid-drop,
 *   6 Invalid/collision, 7 Snap-moment, 8 Precision-readouts,
 *   9 Rotation-affordance.
 */

import { P } from "@southbrook_kitchen_3d_configurator/js/canvas/constants.esm";
import { mixHex } from "@southbrook_kitchen_3d_configurator/js/canvas/color_utils.esm";

// ─── Colours ──────────────────────────────────────────────────────────────

// State 2 (Selected) / State 5 (Valid-drop) / State 7 (Snap). Reused,
// not a new constant: identical to `P.sel`/`P.arrow` in constants.esm.js
// (both already 0x1866D4) and to `$sbk-accent` in kitchen_configurator.scss
// — the one converged accent thread across ~8 existing call sites.
// Measured (visual spec, "The 9 states" §2): 3.11:1 vs the 3D floor
// AND vs the default cabinet body, 4.18/3.81:1 vs wall1/wall2, 4.46:1
// vs the scene background — clears the 3:1 graphical minimum
// everywhere it appears in-scene. Derived from `P.sel` (not a second
// literal) so the two can never drift apart.
export const SELECT_BLUE = P.sel;

// State 6 (Invalid/collision) — NEW scene constant, not in `P` yet
// (wave 2 adds `collision: 0xA6281E` to the `P` palette per the visual
// spec's own instruction). Both existing `--sb-status-blocked-*`
// tokens were measured against the 3D floor/cabinet body and BOTH
// failed to clear the bar with a safe margin:
//   - `--sb-status-blocked-border` (#D9665E) = 2.01:1 vs floor — an
//     outright FAIL of the 3:1 graphical minimum.
//   - `--sb-status-blocked-fg` (#CB3227) = 3.01:1 vs floor/cab — a
//     zero-margin PASS. A "you cannot put it here" collision signal
//     is exactly the wrong place to run a knife-edge pass that a
//     future flooring-finish option could tip into failure.
// `#A6281E` is a darker, more-saturated match in the SAME hue family
// as `--sb-status-blocked-fg` (so "blocked" toast-red and in-scene
// collision-red read as the same semantic) chosen because it clears
// every scene surface with real margin: 4.12:1 vs floor, 4.11:1 vs
// cab, 6.23:1 vs the 2D report floor, 5.53:1 vs the back wall. UI
// tokens are contrast-checked against a pale tint/white page; this
// constant is checked against a saturated tan floor and a putty
// cabinet body instead — different backgrounds, one hex cannot serve
// both, which is exactly the case the design-system skill's "scene
// colours legitimately differ from UI tokens" spirit allows for.
export const COLLISION_RED = 0xA6281E;

// State 4 (Drag-ghost) origin marker — the faint wireframe left behind
// at the cabinet's pre-drag footprint. Reused from cabinet_thumb.esm.js's
// neutral carcass stroke (the design-system skill's cabinet-icon
// language), not invented: already the codebase's "this is schematic,
// not real" colour. Deliberately quiet/low-contrast by design (paired
// with ORIGIN_OPACITY below) — a secondary memory aid, not a primary
// action target.
export const ORIGIN_WIREFRAME = 0x7d858e;

// Default blend amount for `dimColor` (State 3 — others dimmed ~30%
// toward the scene background, never toward grey/desaturated).
export const DIM_AMOUNT = 0.30;

/**
 * State 3 (Others dimmed) — blend a non-selected cabinet's original
 * colour `amount` of the way toward the scene background colour.
 * Delegates to the shared `mixHex` (color_utils.esm.js) — same helper
 * `selection.esm.js`'s tint and `corner_cabinet.esm.js`'s CORNER_TINT
 * already share (SBK-036); this does NOT reimplement the blend.
 *
 * @param {number} origHex — the mesh's current/original 0xRRGGBB colour
 * @param {number} sceneBgHex — blend target, typically `P.scene`
 * @param {number} [amount=DIM_AMOUNT] — 0..1, how far toward sceneBgHex
 * @returns {number} 0xRRGGBB
 */
export function dimColor(origHex, sceneBgHex, amount = DIM_AMOUNT) {
    return mixHex(origHex, sceneBgHex, amount);
}

// ─── Timings (ms) ─────────────────────────────────────────────────────────

// State 2 — lift-in/lift-out ease-out duration (also the base-pad fade,
// which animates together with the lift).
export const LIFT_MS = 180;
// State 3 — dim-in/dim-out ease duration for the non-selected cabinets.
export const DIM_MS = 120;
// State 4 — full rollback-to-origin ease-out on a rejected/cancelled
// drag (collision or pointercancel). Longer than CORRECTION_MS: a full
// rollback is a bigger visual event than a minor server correction.
export const ROLLBACK_MS = 200;
// State 4 — minor ease-out when the server-authoritative position
// differs slightly from the ghost's last optimistic position on a
// successful commit (rare). Shorter than ROLLBACK_MS on purpose.
export const CORRECTION_MS = 150;
// State 7 — one triangular ease-in/ease-out pulse for the snap guide
// line (0 -> 0.9 -> 0 opacity). NOT a repeating buzz.
export const SNAP_PULSE_MS = 220;

// ─── Geometry ─────────────────────────────────────────────────────────────

// State 2 — +0.25in (~6.35mm) lift on the selected group's local Y.
// Authored in inches to match the codebase's native unit system (`IN`
// in constants.esm.js), not as a metric literal. Deliberately small:
// cabinets are floor-anchored real objects, not floating UI chrome.
export const LIFT_IN = 0.25;
// State 4/5 — moving drag-ghost opacity (shipped value, reused as-is
// from kitchen_canvas.esm.js's `_updateDragGhost`).
export const GHOST_OPACITY = 0.35;
// State 4 — origin wireframe opacity, deliberately lower than
// GHOST_OPACITY so the ghost stays the dominant signal (hierarchy:
// ghost > origin marker).
export const ORIGIN_OPACITY = 0.18;
// State 2 — base radial affordance pad opacity under the selected
// footprint. Reuses the existing ghost/lane opacity convention (0.35)
// rather than inventing a new alpha.
export const BASE_PAD_OPACITY = 0.35;
// State 9 (and the general hit-tolerance recipe) — minimum
// screen-space hit-target footprint in CSS px-equivalent. 44px is the
// WCAG 2.5.5/iOS baseline; the design-system skill's 56px shop-floor
// size is the bump-up if usability testing on a showroom tablet shows
// mis-taps (dealer/showroom touch is a lighter-glove context than the
// shop floor, so 44/48 is the starting point here, not 56 by default).
export const HIT_PROXY_MIN_PX = 44;

// ─── State-name enum ──────────────────────────────────────────────────────

/**
 * The 9 manipulation states from the visual spec, in spec order.
 * String values (not raw indices) so they're stable across refactors,
 * log/debug-friendly, and safe to use as CSS class-name suffixes.
 */
export const MANIPULATION_STATE = Object.freeze({
    IDLE: "idle",
    SELECTED: "selected",
    DIMMED: "dimmed",
    DRAG_GHOST: "drag_ghost",
    VALID_DROP: "valid_drop",
    COLLISION: "collision",
    SNAP: "snap",
    PRECISION_READOUT: "precision_readout",
    ROTATION_AFFORDANCE: "rotation_affordance",
});

// ─── Pure helpers ─────────────────────────────────────────────────────────

// State 7's pulse peak — never a full 1.0, per the spec's "0 -> 0.9 -> 0".
const SNAP_PULSE_PEAK = 0.9;

/**
 * State 7 (Snap-moment) pulse envelope — a single triangular
 * ease-in/ease-out, NOT a repeating buzz (touch-engineering-research.md
 * §5: patterns longer than a very short tick read as buzzy; keep to
 * single short pulses). `tNorm` is the pulse's own progress, 0..1
 * (caller maps elapsed-ms / SNAP_PULSE_MS to this range).
 *
 * @param {number} tNorm — 0..1 normalized time through the pulse
 * @returns {number} opacity, 0..SNAP_PULSE_PEAK; 0 for any tNorm
 *   outside [0, 1] (including NaN) — never throws, never extrapolates.
 */
export function snapPulse(tNorm) {
    if (!(tNorm >= 0 && tNorm <= 1)) {
        return 0;
    }
    return SNAP_PULSE_PEAK * (1 - Math.abs(tNorm * 2 - 1));
}

/**
 * State 7's `navigator.vibrate(10)` companion — Android-only garnish
 * per the spec (iOS Safari has no reliable JS-triggerable haptic as of
 * the 2026 research). Feature-detected and guarded: correctness of the
 * snap feedback must NEVER depend on this firing, so it never throws —
 * not on servers/Node (no `navigator`), not on iOS/desktop (no
 * `navigator.vibrate`), not if the browser throws from inside
 * `vibrate()` itself (some implementations do, e.g. when called
 * outside a user gesture).
 *
 * @returns {boolean} true if `navigator.vibrate(10)` was called and
 *   returned a truthy result; false in every other case.
 */
export function hapticTick() {
    try {
        if (typeof navigator === "undefined") {
            return false;
        }
        if (typeof navigator.vibrate !== "function") {
            return false;
        }
        return Boolean(navigator.vibrate(10));
    } catch (_err) {
        return false;
    }
}
