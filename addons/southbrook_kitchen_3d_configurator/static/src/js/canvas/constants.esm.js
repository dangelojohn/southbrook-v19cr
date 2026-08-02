/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — canvas constants.
 *
 * Rec D · Sprint 2d · Step 1 · pure-literal extraction.
 *
 * Extracted verbatim from kitchen_configurator.js:20-47 to unlock
 * reuse by future shared canvas components (Sprint 2d step 5).
 * NO behaviour change — every constant value is identical to the
 * pre-2d state; only the module home moved.
 *
 * Consumers:
 *   - static/src/js/kitchen_configurator.js (existing internal client action)
 *   - future <KitchenCanvas> shared component (Sprint 2d step 5)
 *   - future /kitchen-planner mount (Sprint 2f)
 */

// ─── Cabinet constants (inches) ──────────────────────────────────────────────
export const IN  = 1 / 12;          // 1 inch in scene-feet
export const BW  = 24 * IN;         // base cabinet width  (2 ft)
export const BH  = 34.5 * IN;       // base cabinet height (34.5")
export const BD  = 24 * IN;         // base cabinet depth  (2 ft)
export const WW  = 24 * IN;         // wall cabinet width
export const WH  = 30 * IN;         // wall cabinet height (30")
export const WD  = 12 * IN;         // wall cabinet depth  (1 ft)
export const CTR = 1.5 * IN;        // countertop thickness
export const GAP = 18 * IN;         // clearance between counter and wall cab bottom
export const WBY = BH + CTR + GAP;  // wall cabinet bottom Y

// ─── Room-wall identifiers ────────────────────────────────────────────────────
// Room-wall identifiers — single source of truth for the client. Mirrors
// the pure layout engine's WALLS tuple (kitchen_layout_engine.py). Use
// this instead of scattering "back"/"left" string literals in NEW code.
export const WALLS = Object.freeze({
    BACK: "back",
    LEFT: "left",
    RIGHT: "right",
    FRONT: "front",
});

// F7 fix (2026-07-27) — walls room_shell.esm.js renders FULL HEIGHT
// (back + left). Right + front are the low 12" "kick-strip" walls —
// always visible, click-selectable, and hover-glowable exactly like
// the tall walls, but too short to occlude the open-cutaway view. A
// stray floor-plane ray from an HTML5 drop can still land on a
// kick-strip near the room boundary; before this fix that silently
// resolved to a wall pick and overrode the user's deliberately-chosen
// Active Wall (kitchen_configurator.js's wallOverride precedence, also
// fixed alongside this constant). kitchen_canvas.esm.js's drop-wall
// raycast (`_raycastDropWall`) filters to only this list so a drop can
// never be inferred onto a kick-strip wall; a CLICK on one (the
// toolbar / 3D click-to-select path, `_raycastWall`) is unaffected and
// still hits all four. Single source of truth so room_shell.esm.js's
// rendering and this drop-inference gate can't drift independently.
export const FULL_HEIGHT_WALLS = Object.freeze(["back", "left"]);

// ─── Colour palette ───────────────────────────────────────────────────────────
export const P = {
    scene:   0xECE9E3,
    floor:   0xCFC4A8,
    wall1:   0xE7E2D9,   // back wall
    wall2:   0xDED8CE,   // left wall
    grid:    0xBAB4A8,
    cab:     0xC9C4BC,
    cabDark: 0xA9A59E,
    counter: 0xE1DDD6,
    handle:  0x3A3530,
    sel:     0x1866D4,
    drag:    0x1866D4,
    toekick: 0x8A857E,
    arrow:   0x1866D4,
    // Touch editing Wave 2 (docs/superpowers/specs/2026-08-02-touch-
    // editing-design.md §3, manipulation-visual-spec.md state 6 —
    // Invalid/collision). Value matches manipulation_states.esm.js's
    // own `COLLISION_RED` (Wave 1) exactly, byte-for-byte — that
    // module derives its own colours from this `P` object elsewhere
    // (e.g. `SELECT_BLUE = P.sel`) and its own comment explicitly
    // instructs Wave 2 to add this entry here; kept as two identical
    // literals (not re-imported across the two files) only because
    // manipulation_states.esm.js is Wave-1-owned and out of this
    // task's edit scope — see kitchen_canvas.esm.js's touch-effects
    // section for the actual consumer.
    collision: 0xA6281E,
};
