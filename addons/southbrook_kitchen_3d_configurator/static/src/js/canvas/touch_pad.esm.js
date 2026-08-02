/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — touch editing control pad.
 *
 * Wave 2 of the touch-editing build (`docs/superpowers/specs/2026-08-02-
 * touch-editing-design.md` §1 decisions 1/3/4, §4 item 5, §5). Renders
 * the on-screen affordance pad both surfaces mount: `.o_sbk_touchpad`
 * (phone, bottom-docked) / `.o_sbk_touchpad--floating` (tablet, anchored
 * near the selection) — both classes shipped in Wave 1's touch_pad.scss
 * (T3). This file owns:
 *   - the pure family/visibility classification the spec's "scoped
 *     rotation" decision requires (exported so it's node-contract-
 *     testable without mounting OWL — see contracts/37_touch_pad.test.mjs)
 *   - the pure touch-capability / pad-mode / touch-stats-accumulation
 *     helpers both parents (kitchen_configurator.js, design_tab.esm.js)
 *     consume
 *   - the <TouchPad> OWL component itself — a thin, side-effect-free
 *     renderer. Every mutating action is a callback prop; this
 *     component never calls `rpc`/`fetch` itself.
 *
 * ─── Family classification (spec §1 decision 3, "scoped affordances,
 * honest glyphs") ───────────────────────────────────────────────────────
 *
 * Three manipulable families, plus "none" for cabinet_types that were
 * never independently selectable/editable before touch v1 either
 * (filler/panel — see kitchen_configurator.js's pre-existing
 * `_rotateSelected` guard and `_cycleSelection`'s filler exclusion):
 *
 *   - "corner"  — cabinet_type === "corner". Gets flip-hand only.
 *   - "island"  — zone === "island" (Q21 zone lexicon,
 *     southbrook_kitchen_3d_configurator/models/kitchen_design.py's
 *     ZONE_SELECTION). Gets free 45°-snap rotation + nudge.
 *   - "wallRun" — everything else manipulable (base/wall/tall/
 *     appliance, or a corner-shaped item with no zone override). Gets
 *     camera-to-elevation + send-to-wall instead of rotation.
 *
 * KNOWN GAP (flagged for the coordinator's reconciliation pass, see
 * this task's build note): neither `_serialize_configurator_lines()`
 * (backend) nor `_southbrook_design_payload()` (portal) currently
 * serializes `zone` onto the item dicts the client receives — only the
 * backend Order Builder's own views read/write that field today. Until
 * one of those two serializers is extended (out of this file's edit
 * scope), `item.zone` is always `undefined` for every item either pad
 * ever sees, so `classifyCabinetFamily` can never actually return
 * "island" in production — the rotate affordance is wired correctly
 * and will light up the moment `zone` starts flowing, but today only
 * "corner" and "wallRun" are reachable. This is deliberate,
 * spec-accurate code written against data that isn't wired yet, not a
 * bug in this file.
 */
import { Component, useState, xml } from "@odoo/owl";

// ─── Pure classification / affordance visibility ───────────────────────────

/**
 * @param {object|null} item — a selected cabinet item (client shape:
 *   `cabinet_type`, optionally `zone`).
 * @returns {"corner"|"island"|"wallRun"|"none"}
 */
export function classifyCabinetFamily(item) {
    if (!item) return "none";
    const type = item.cabinet_type;
    // Fillers/panels were never independently rotatable/manipulable
    // before touch v1 either (kitchen_configurator.js's pre-existing
    // _rotateSelected guard, _cycleSelection's exclusion) — touch
    // doesn't change that.
    if (type === "filler" || type === "panel") return "none";
    if (type === "corner") return "corner";
    if (item.zone === "island") return "island";
    return "wallRun";
}

/**
 * The pad button-visibility matrix, keyed off `classifyCabinetFamily`.
 * Pure function — this is the "pure visibility function" the task's
 * TESTS requirement extracts for the node contract suite.
 *
 * @param {object|null} item
 * @returns {{family: string, nudge: boolean, rotate: boolean,
 *   flipHand: boolean, cameraToElevation: boolean, sendToWall: boolean,
 *   remove: boolean}}
 */
export function getPadAffordances(item) {
    const family = classifyCabinetFamily(item);
    const manipulable = family !== "none";
    return {
        family,
        nudge: manipulable,
        rotate: family === "island",
        flipHand: family === "corner",
        cameraToElevation: family === "wallRun",
        sendToWall: family === "wallRun",
        remove: manipulable,
    };
}

// ─── Wall-cycle + elevation-view mapping (send-to-wall / camera-to-
// elevation) ────────────────────────────────────────────────────────────

// Perimeter walk order (spec §5 "send-to-wall (cycle icon)") — NOT the
// WALLS enum's declaration order (back/left/right/front), which jumps
// corner-to-corner; this is the room's actual physical perimeter.
const WALL_CYCLE_ORDER = ["back", "right", "front", "left"];

/**
 * @param {string} wall — current wall id ("back"/"left"/"right"/"front").
 * @returns {string} the next wall in perimeter-walk order. An unknown/
 *   missing `wall` starts the cycle at "back"'s successor ("right"),
 *   same as if the item were already on "back" — never throws.
 */
export function nextWall(wall) {
    const idx = WALL_CYCLE_ORDER.indexOf(wall);
    const from = idx === -1 ? 0 : idx;
    return WALL_CYCLE_ORDER[(from + 1) % WALL_CYCLE_ORDER.length];
}

// view_specs.esm.js's "front" ortho view is the camera positioned in
// front of the room looking AT the back wall (pos.z = rd+18, target.z
// = 0) — i.e. it IS the back-wall elevation, despite the name. "left"/
// "right" are genuinely named for the wall they face. There is no 4th
// elevation dedicated to the front wall (only 3 named elevations ship:
// front/left/right). Reconciled against T4's canvas-integration note
// (build-t4-canvas.md, `api.cameraToElevation(wall)`): the front wall
// (a low, always-visible "kick-strip", not a full wall — see
// constants.esm.js's FULL_HEIGHT_WALLS comment) has no dedicated
// elevation and falls back to "persp" there, NOT "front" — this
// mapping mirrors that exact fallback so the pad's own aria-pressed
// state and any _setView fallback path never disagree with what the
// canvas's imperative method actually does.
const WALL_ELEVATION_VIEW = {
    back:  "front",
    left:  "left",
    right: "right",
    front: "persp",
};

/**
 * @param {string} wall
 * @returns {string} the KitchenCanvas view key ("front"/"left"/"right")
 *   that best elevates this wall.
 */
export function elevationViewForWall(wall) {
    return WALL_ELEVATION_VIEW[wall] || "front";
}

// ─── Nudge step ─────────────────────────────────────────────────────────

// Matches drop_raycaster.esm.js's existing `snapIn = 6` drop-placement
// grid — reused, not invented, so a nudged cabinet lands on the same
// grid a drag-drop would have snapped it to.
export const NUDGE_STEP_IN = 6;

// ─── Touch-capability / pad-mode detection ─────────────────────────────

/**
 * Boot-time touch-capability probe. Mirrors kitchen_configurator.js's
 * existing `isNarrowViewportAtBoot` shape exactly (explicit `win`/`nav`
 * args rather than reading the globals, so it's testable with bare
 * fakes; guards a missing `matchMedia`/`maxTouchPoints` and any thrown
 * call rather than propagating).
 *
 * @param {Window|object} win
 * @param {Navigator|object} nav
 * @returns {boolean}
 */
export function isTouchCapableAtBoot(win, nav) {
    let coarse = false;
    try {
        coarse = !!(win && typeof win.matchMedia === "function"
            && win.matchMedia("(pointer: coarse)").matches);
    } catch (_err) {
        coarse = false;
    }
    let touchPoints = false;
    try {
        touchPoints = !!(nav && typeof nav.maxTouchPoints === "number"
            && nav.maxTouchPoints > 0);
    } catch (_err) {
        touchPoints = false;
    }
    return coarse || touchPoints;
}

// Same 767px boundary as kitchen_configurator.scss's shipped mobile
// gate (Task 34/SBK-019, "below-768px mobile gate") — the backend
// configurator is never reachable below this width at all, so its pad
// is always "floating"; the portal has no such gate and uses this same
// number to pick dock (phone) vs floating (tablet).
export const TOUCH_PAD_PHONE_MAX_WIDTH_PX = 767;

/**
 * @param {Window|object} win
 * @returns {"dock"|"floating"}
 */
export function touchPadModeForViewport(win) {
    try {
        if (win && typeof win.matchMedia === "function"
            && win.matchMedia(`(max-width: ${TOUCH_PAD_PHONE_MAX_WIDTH_PX}px)`).matches) {
            return "dock";
        }
    } catch (_err) {
        // fall through to floating
    }
    return "floating";
}

// ─── Touch health-counter accumulation (spec §4 item 3) ────────────────
// Pure accumulator — the RECEIVING end of the "gesture_rollbacks/
// gesture_abandons from canvas callbacks" contract. NO call site in
// this addon invokes `accumulateTouchStats` yet: kitchen_canvas.esm.js
// (sibling-owned, out of this task's edit scope) doesn't emit gesture-
// rollback/abandon signals. See the build note for the exact callback-
// prop contract the canvas-owning task still needs to add.

/** @returns {{gesture_rollbacks: number, gesture_abandons: number}} */
export function makeEmptyTouchStats() {
    return { gesture_rollbacks: 0, gesture_abandons: 0 };
}

/**
 * @param {{gesture_rollbacks?: number, gesture_abandons?: number}|null} stats
 * @param {"rollback"|"abandon"} kind
 * @returns {{gesture_rollbacks: number, gesture_abandons: number}} a NEW
 *   object — never mutates `stats`. An unrecognized `kind` returns an
 *   unchanged (but normalized/copied) counter pair rather than throwing.
 */
export function accumulateTouchStats(stats, kind) {
    const base = stats && typeof stats === "object" ? stats : makeEmptyTouchStats();
    const next = {
        gesture_rollbacks: base.gesture_rollbacks || 0,
        gesture_abandons:  base.gesture_abandons  || 0,
    };
    if (kind === "rollback") next.gesture_rollbacks += 1;
    else if (kind === "abandon") next.gesture_abandons += 1;
    return next;
}

/** @returns {boolean} true when either counter is non-zero. */
export function hasTouchStats(stats) {
    return !!(stats && ((stats.gesture_rollbacks || 0) > 0 || (stats.gesture_abandons || 0) > 0));
}

// ─── OWL component ──────────────────────────────────────────────────────
//
// Pure renderer + callback props — no rpc/fetch, no THREE, no direct
// state mutation of anything outside its own local `pulsing` UI flag.
// Both parents pass `props.selection` as the LIVE state.selected
// reference (not a copy); this component never writes to it.
export class TouchPad extends Component {
    setup() {
        this.state = useState({ pulsing: null });
    }

    get affordances() {
        return getPadAffordances(this.props.selection);
    }

    get capabilities() {
        return this.props.capabilities || {};
    }

    // camera-to-elevation is the one genuinely bi-state toggle this pad
    // renders (spec: "aria-pressed where toggling") — true when the
    // canvas is ALREADY showing this cabinet's mapped elevation.
    get isElevationActive() {
        const sel = this.props.selection;
        if (!sel) return false;
        return this.props.currentView === elevationViewForWall(sel.wall);
    }

    _pulse(key) {
        this.state.pulsing = key;
        setTimeout(() => {
            if (this.state.pulsing === key) this.state.pulsing = null;
        }, 100);
    }

    _onNudge(dir) {
        if (this.props.onNudge) this.props.onNudge(dir);
    }

    _onRotate(deltaDeg) {
        if (this.props.onRotate) this.props.onRotate(deltaDeg);
        this._pulse(deltaDeg < 0 ? "ccw" : "cw");
    }

    _onFlipHand() {
        if (this.props.onFlipHand) this.props.onFlipHand();
    }

    _onCameraToElevation() {
        if (this.props.onCameraToElevation) this.props.onCameraToElevation();
    }

    _onSendToWall() {
        if (this.props.onSendToWall) this.props.onSendToWall();
    }

    _onRemove() {
        if (this.props.onRemove) this.props.onRemove();
    }

    _onUndo() {
        if (this.props.onUndo) this.props.onUndo();
    }
}

TouchPad.props = {
    mode:         { type: String, optional: true },
    selection:    { type: [Object, { value: null }], optional: true },
    currentView:  { type: String, optional: true },
    capabilities: { type: Object, optional: true },
    onNudge:              { type: Function, optional: true },
    onRotate:             { type: Function, optional: true },
    onFlipHand:           { type: Function, optional: true },
    onCameraToElevation:  { type: Function, optional: true },
    onSendToWall:         { type: Function, optional: true },
    onRemove:             { type: Function, optional: true },
    onUndo:               { type: Function, optional: true },
};
TouchPad.defaultProps = {
    mode:         "floating",
    selection:    null,
    capabilities: {},
};

TouchPad.template = xml`
    <div t-att-class="props.mode === 'dock' ? 'o_sbk_touchpad' : 'o_sbk_touchpad--floating'"
         role="toolbar" aria-label="Cabinet touch controls">

        <t t-if="props.selection">
            <button t-if="affordances.nudge" type="button"
                    class="o_sbk_touchpad_btn"
                    aria-label="Nudge left"
                    title="Nudge left"
                    t-on-click="() => this._onNudge(-1)">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x2190;</span>
            </button>
            <button t-if="affordances.nudge" type="button"
                    class="o_sbk_touchpad_btn"
                    aria-label="Nudge right"
                    title="Nudge right"
                    t-on-click="() => this._onNudge(1)">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x2192;</span>
            </button>

            <button t-if="affordances.rotate" type="button"
                    t-att-class="'o_sbk_touchpad_btn' + (state.pulsing === 'ccw' ? ' is-pulsing' : '')"
                    aria-label="Rotate 45° counter-clockwise"
                    title="Rotate 45° counter-clockwise"
                    t-on-click="() => this._onRotate(-45)">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x21BA;</span>
            </button>
            <button t-if="affordances.rotate" type="button"
                    t-att-class="'o_sbk_touchpad_btn' + (state.pulsing === 'cw' ? ' is-pulsing' : '')"
                    aria-label="Rotate 45° clockwise"
                    title="Rotate 45° clockwise"
                    t-on-click="() => this._onRotate(45)">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x21BB;</span>
            </button>

            <button t-if="affordances.flipHand" type="button"
                    class="o_sbk_touchpad_btn"
                    aria-label="Flip corner hand"
                    title="Flip corner hand (swap to the mirrored-hand cabinet)"
                    t-on-click="_onFlipHand">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x21C4;</span>
            </button>

            <button t-if="affordances.cameraToElevation" type="button"
                    class="o_sbk_touchpad_btn"
                    t-att-disabled="capabilities.cameraReady === false"
                    t-att-aria-disabled="capabilities.cameraReady === false ? 'true' : 'false'"
                    aria-label="Camera to elevation view"
                    title="Camera to elevation view"
                    t-att-aria-pressed="isElevationActive ? 'true' : 'false'"
                    t-on-click="_onCameraToElevation">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x1F4F7;</span>
            </button>

            <button t-if="affordances.sendToWall &amp;&amp; capabilities.sendToWallSupported !== false"
                    type="button"
                    class="o_sbk_touchpad_btn"
                    t-att-disabled="capabilities.canSendToWall === false"
                    t-att-aria-disabled="capabilities.canSendToWall === false ? 'true' : 'false'"
                    aria-label="Send to next wall"
                    title="Send to next wall"
                    t-on-click="_onSendToWall">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x27F3;</span>
            </button>

            <button t-if="affordances.remove" type="button"
                    class="o_sbk_touchpad_btn"
                    aria-label="Remove cabinet"
                    title="Remove cabinet"
                    t-on-click="_onRemove">
                <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x2715;</span>
            </button>
        </t>

        <!-- Persistent undo affordance — shown even with nothing
             selected (e.g. right after a pad-driven Remove), per the
             build brief's explicit "persistent undo affordance"
             requirement. -->
        <button type="button"
                class="o_sbk_touchpad_btn"
                t-att-disabled="capabilities.canUndo === false"
                t-att-aria-disabled="capabilities.canUndo === false ? 'true' : 'false'"
                aria-label="Undo last change"
                title="Undo last change"
                t-on-click="_onUndo">
            <span class="o_sbk_touchpad_icon" aria-hidden="true">&#x21B6;</span>
        </button>
    </div>
`;
