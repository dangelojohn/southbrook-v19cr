/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — reusable 3D canvas component.
 *
 * Rec D · Sprint 2d · Step 24c · visible-surface swap.
 *
 * The parent's <div t-ref="canvas3d"/> is gone. This component's own
 * <div t-ref="kitchenCanvas"> is now the ONLY visible 3D surface. It
 * owns:
 *   - the single WebGL context (parent's _initScene is a no-op stub)
 *   - the pointer pipeline (mousedown/move/up/leave/over on its own div)
 *   - the wheel-zoom on its own div
 *   - the camera controller (setView / animateCamera / applyOrthoFrustum)
 *   - the drop-lane visualisation
 *   - the selection highlight (post-hit + on-selected-prop-change)
 *
 * State-mutating side effects flow to the parent via 5 typed callbacks:
 *   props.onSelectItem(item)                    — cabinet click
 *   props.onMoveItemStart(item)                 — Task 35: move-drag
 *                                                  COMMITS (crosses the
 *                                                  drag threshold), fired
 *                                                  once, BEFORE this
 *                                                  component's own first
 *                                                  in-place x_position_in
 *                                                  write to `item` — the
 *                                                  parent's only chance
 *                                                  to snapshot the
 *                                                  pre-drag state, since
 *                                                  `item` is a live
 *                                                  reference into the
 *                                                  parent's state.items
 *                                                  and gets mutated
 *                                                  in-place on every
 *                                                  subsequent
 *                                                  _onMouseMove tick.
 *   props.onMoveItem({item, x_position_in, z_position_in,
 *                     pinnable})                — move-drag commit.
 *                                                  `z_position_in` is a
 *                                                  Wave-2 (touch) ADDITION
 *                                                  — additive, non-
 *                                                  breaking: existing
 *                                                  `{item, x_position_in,
 *                                                  pinnable}` destructuring
 *                                                  callers simply never
 *                                                  read it; correctness
 *                                                  never depends on the
 *                                                  caller reading it either
 *                                                  (item.z_position_in is
 *                                                  already mutated in
 *                                                  place before this
 *                                                  fires, and the standard
 *                                                  persist path reads the
 *                                                  live item, not this
 *                                                  payload).
 *   props.onResizeRoom(newWidthIn, inFlight)    — room-width drag
 *   props.onResizeRoomDepth(newDepthIn, inFlight) — room-depth drag
 *   props.onViewChange(view)                    — post-setView ack
 *
 * Parent-scoped keyboard + HTML5 drop handlers reach into this
 * component via `props.onReady(api)`, which yields:
 *   api.setView(key)         — imperative view change
 *   api.zoomBy(k)            — imperative ortho zoom
 *   api.computeDropX(ev)     — floor-plane raycast for drop targets
 *   api.cameraToElevation(wall) — Wave 2 (touch): lerp the camera to
 *                                 face `wall` (back/left/right/front)
 *                                 using the existing view_specs rig;
 *                                 returns the view key actually used
 *                                 (see resolveElevationViewForWall's
 *                                 own docblock for the FRONT-wall
 *                                 fallback). Zero data mutation, zero
 *                                 server round-trip.
 *
 * ─── Touch editing Wave 2 — parent-facing API summary ─────────────────
 * (full contract on each prop/callback declaration below): new props
 * `touchEnabled` (kill switch — false means byte-identical mouse
 * behaviour, no new listeners/meshes/visuals at all), optional
 * `touchSurfaceConfig` ({slopPx, longPressMs, longPressEnabled} —
 * forwarded to createGestureReducer), optional `dragCollision`
 * (server/parent collision flag for the in-flight touch drag); new
 * optional callback `onGestureAffordance(action, item)` (currently
 * unreachable — reserved for a future in-scene affordance glyph); new
 * imperative `api.cameraToElevation(wall)` via the existing `onReady`.
 * Every EXISTING callback (`onSelectItem`, `onMoveItemStart`,
 * `onMoveItem`, `onWallSelect`) is REUSED VERBATIM by the touch
 * pipeline — no parallel/duplicate persistence or selection path was
 * introduced (F8/F14 discipline, constraints doc §2/§7).
 */

import {
    Component, onMounted, onWillUnmount, onWillUpdateProps, useRef, xml,
} from "@odoo/owl";

import { IN, P, FULL_HEIGHT_WALLS, WALLS } from "@southbrook_kitchen_3d_configurator/js/canvas/constants.esm";
import { loadThreeJS }       from "@southbrook_kitchen_3d_configurator/js/canvas/three_loader.esm";
import { initScene }         from "@southbrook_kitchen_3d_configurator/js/canvas/scene_init.esm";
import { destroyScene }      from "@southbrook_kitchen_3d_configurator/js/canvas/scene_dispose.esm";
import { installPbrEnvMap }  from "@southbrook_kitchen_3d_configurator/js/canvas/pbr_env_map.esm";
import { makeMesh }          from "@southbrook_kitchen_3d_configurator/js/canvas/mesh_factory.esm";
import { buildRoomShell }    from "@southbrook_kitchen_3d_configurator/js/canvas/room_shell.esm";
import { buildDragHandle, buildDepthHandle } from "@southbrook_kitchen_3d_configurator/js/canvas/drag_handle.esm";
import { buildDropLanes }    from "@southbrook_kitchen_3d_configurator/js/canvas/drop_lanes.esm";
import { buildBaseCabinet }  from "@southbrook_kitchen_3d_configurator/js/canvas/base_cabinet.esm";
import { buildWallCabinet }  from "@southbrook_kitchen_3d_configurator/js/canvas/wall_cabinet.esm";
import {
    buildOtherCabinet, buildFillerPanel, buildEndCapPanel,
} from "@southbrook_kitchen_3d_configurator/js/canvas/other_cabinets.esm";
import {
    buildCornerCabinet, cornerBlindNearX0,
} from "@southbrook_kitchen_3d_configurator/js/canvas/corner_cabinet.esm";
import { ndcFromEvent }      from "@southbrook_kitchen_3d_configurator/js/canvas/pointer_helpers.esm";
import { computeViewSpecs }  from "@southbrook_kitchen_3d_configurator/js/canvas/view_specs.esm";
import { computeOrthoFrustum } from "@southbrook_kitchen_3d_configurator/js/canvas/ortho_frustum.esm";
import {
    applySelectionVisuals, clearSelectionVisuals, createSelectionState,
} from "@southbrook_kitchen_3d_configurator/js/canvas/selection.esm";
import { computeDropXIn }    from "@southbrook_kitchen_3d_configurator/js/canvas/drop_raycaster.esm";
import { easeInOutCubic }    from "@southbrook_kitchen_3d_configurator/js/canvas/easing.esm";
import {
    setView as setViewShared,
    animateCamera as animateCameraShared,
    onWheel as onWheelShared,
} from "@southbrook_kitchen_3d_configurator/js/canvas/camera_controller.esm";
import {
    isHandleActiveView, resolveRoomWidthFromDrag, resolveRoomDepthFromDrag,
    isCabinetDragCommitted, isPinnable, cursorForPointerState,
} from "@southbrook_kitchen_3d_configurator/js/canvas/pointer_pipeline.esm";
import {
    createGestureReducer, createInitialGestureState, GESTURE_PHASES,
} from "@southbrook_kitchen_3d_configurator/js/canvas/touch_gestures.esm";
import {
    dimColor, hapticTick, snapPulse, SNAP_PULSE_MS, GHOST_OPACITY,
    ORIGIN_OPACITY, ORIGIN_WIREFRAME, BASE_PAD_OPACITY, LIFT_IN,
    HIT_PROXY_MIN_PX, COLLISION_RED,
} from "@southbrook_kitchen_3d_configurator/js/canvas/manipulation_states.esm";

/**
 * Touch editing Wave 2 — pure, DOM/THREE-adjacent-but-argument-driven
 * helpers extracted to module scope so they're unit-testable with
 * plain `node:test` calls (same two-tier strategy pointer_pipeline.esm.js
 * / drop_raycaster.esm.js already established — see the constraints
 * doc's Testability section). Wired into the KitchenCanvas instance
 * methods below; kept OUT of the class body itself so no `this` ever
 * leaks into them.
 */

// ─── Effect-consumption seam (pure) ──────────────────────────────────────
// Maps a touch_gestures.esm.js `state.effects[i]` entry (T1's contract,
// consumed verbatim — see that file's own docblock) onto a small,
// host-agnostic descriptor of WHAT KIND of host action it calls for.
// `_applyGestureEffects` (instance method, below) is the only consumer;
// splitting this out means the "which effect maps to which action"
// question is testable with zero THREE/DOM/OWL in play, and any future
// drift in T1's effect vocabulary (a renamed/added/removed type) shows
// up here as a `noop` instead of silently doing nothing deep inside a
// THREE-touching method.
export function describeGestureEffect(effect) {
    switch (effect && effect.type) {
        case "select":           return { kind: "select", id: effect.id };
        case "affordance":       return { kind: "affordance", action: effect.action };
        case "orbit":            return { kind: "nativeCamera" };
        case "cameraGesture":    return { kind: "nativeCamera" };
        case "endCameraGesture": return { kind: "restoreCamera" };
        case "beginDrag":        return { kind: "dragStart", id: effect.id, mode: "direct" };
        case "beginPickup":      return { kind: "dragStart", id: effect.id, mode: "ghost" };
        case "dragTo":           return { kind: "dragMove", id: effect.id, x: effect.x, y: effect.y, mode: effect.mode };
        case "commit":           return { kind: "dragCommit", id: effect.id, mode: effect.mode };
        case "cancelDrag":       return { kind: "dragCancel", id: effect.id, mode: effect.mode };
        default:                 return { kind: "noop" };
    }
}

// ─── OrbitControls arbitration seam (pure) ───────────────────────────────
// Constraints doc §8 risk #1: OrbitControls listens for RAW `pointerdown`
// on `renderer.domElement` (a CHILD of `mount`, bubble phase, no
// capture) and is a complete no-op whenever `T.orbit.enabled === false`
// (true for every ortho view — iso/top/front/left/right — and only
// ever `true` in "persp"; verified against the vendored
// OrbitControls.js: `onPointerDown`/`onPointerMove`/`onPointerUp` each
// early-return on `scope.enabled === false`). The only real collision
// window is: persp view + a gesture that starts ambiguous (first
// finger down, phase `pendingTap`) and resolves into OUR drag/pickup
// on the next move.
//
// Policy (coded against touch_gestures.esm.js's own effect contract
// verbatim — its `orbit`/`cameraGesture` effect comments say "the host
// does NOT preventDefault/stopPropagate this gesture"):
//   - `pointerdown` is NEVER claimed — even the 2nd(+) finger that
//     immediately resolves to `cameraGesture`. Letting every
//     pointerdown reach OrbitControls unmolested is what lets it
//     register its OWN internal pointermove/pointerup listeners in
//     the first place (they're added dynamically FROM its own
//     onPointerDown per the vendored source, lines 753-754) — blocking
//     pointerdown here would permanently prevent orbit from ever
//     starting via touch, not just for this one gesture.
//   - `pointermove` is claimed (host stops it) the instant the
//     resulting phase is NOT a camera phase — i.e. the moment our own
//     reducer resolves the ambiguity into `dragging`/`ghostDrag`/still-
//     `pendingTap` (a micro-tremor that hasn't resolved either way
//     yet — also ours to hold, so OrbitControls doesn't accumulate a
//     tiny unwanted rotation from jitter during what may become a tap).
//   - `pointerup`/`pointercancel` are claimed based on the PRE-reduce
//     phase (the gesture that's ending), for the same reason.
//
// Residual, accepted risk (documented, not silently assumed away):
// when a gesture starts ambiguous and resolves into OUR drag, the
// initiating pointerdown already reached OrbitControls (harmlessly —
// no camera motion happens from pointerdown alone), but the following
// moves/up are then claimed by us, so OrbitControls' own onPointerUp
// cleanup for that pointerId never runs. `KitchenCanvas` additionally
// forces `T.orbit.enabled = false` for the exact duration of our
// drag/pickup (restored on commit/cancel) as a second, independent
// safety net — the public, documented OrbitControls on/off switch —
// so no camera motion is ever visible even if a stray event slips
// through. The only residual cost is a possibly-stale pointerId inside
// OrbitControls' own internal bookkeeping until its next fresh
// pointerdown/pointerup cycle; three.js's OrbitControls re-derives its
// pointer list from live pointerdown events, so this is self-healing
// in practice, not a compounding leak. Scoped to persp view only,
// exactly like the constraints doc's own framing of this risk.
export function shouldClaimPointerEvent(eventType, prePhase, postPhase) {
    const isCameraPhase = (p) =>
        p === GESTURE_PHASES.CAMERA_ORBIT || p === GESTURE_PHASES.CAMERA_GESTURE;
    if (eventType === "pointerdown") return false;
    if (eventType === "pointermove") return !isCameraPhase(postPhase);
    return !isCameraPhase(prePhase); // pointerup / pointercancel
}

// ─── Hit-tolerance proxy-mesh sizing (pure) ──────────────────────────────
// "Invisible enlarged proxy meshes ... sized to HIT_PROXY_MIN_PX
// equivalence" (brief item 2) — how much WORLD-INCH padding (split
// evenly on both sides of the visible footprint) is needed so the
// padded footprint's on-screen size clears `minPx`, given the current
// px-per-inch ratio. Returns 0 (no padding needed) once the visible
// footprint already clears the target on its own — most cabinets at
// typical zoom levels need none; this only inflates small/distant ones.
export function computeProxyPaddingIn(footprintIn, pxPerIn, minPx = HIT_PROXY_MIN_PX) {
    if (!(pxPerIn > 0) || !(footprintIn > 0)) return 0;
    const footprintPx = footprintIn * pxPerIn;
    if (footprintPx >= minPx) return 0;
    const neededIn = minPx / pxPerIn;
    return Math.max(0, (neededIn - footprintIn) / 2);
}

// Px-per-world-inch for the CURRENT ortho frustum — `frustumHeightVs`
// is (top - bottom) in scene-feet (computeOrthoFrustum's own unit),
// `rendererHeightPx` is the renderer's CSS/backing pixel height.
export function computeOrthoPxPerIn(frustumHeightVs, rendererHeightPx) {
    if (!(frustumHeightVs > 0) || !(rendererHeightPx > 0)) return 0;
    const pxPerSceneUnit = rendererHeightPx / frustumHeightVs;
    return pxPerSceneUnit * IN;
}

// Px-per-world-inch approximation for the perspective ("persp") camera
// at a given distance from its target — standard pinhole-camera
// visible-height-at-distance formula. Approximate by construction
// (perspective foreshortening varies with WHERE in frame an object
// sits, not just distance-to-target); good enough for hit-inflation
// sizing, never used for anything render-critical.
export function computePerspPxPerIn(rendererHeightPx, fovDeg, distanceIn) {
    if (!(rendererHeightPx > 0) || !(distanceIn > 0) || !(fovDeg > 0)) return 0;
    const fovRad = (fovDeg * Math.PI) / 180;
    const visibleHeightIn = 2 * distanceIn * Math.tan(fovRad / 2);
    if (!(visibleHeightIn > 0)) return 0;
    return rendererHeightPx / visibleHeightIn;
}

// ─── Constrained 1-DOF drag math (pure given THREE + a configured ray) ───
// Brief item 3: "constrained 1-DOF along-wall using a math THREE.Plane
// + local-space projection, WITH the Z-axis branch for left/right
// walls (fixing the mouse path's back-wall-only limitation — spec
// §5)". `raycaster` must already have `.ray` set (via
// `raycaster.setFromCamera(...)` or, in tests, `raycaster.set(origin,
// direction)` directly) — this function only reads `raycaster.ray`,
// mirroring `drop_raycaster.esm.js`'s `computeDropXIn` floor-plane
// idiom, generalized to pick X or Z by wall.
export function projectDragAlongWall(THREE, raycaster, wall, snapIn = 1) {
    const floor = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const hitPoint = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(floor, hitPoint)) return null;
    const isSideWall = wall === WALLS.LEFT || wall === WALLS.RIGHT;
    const axis = isSideWall ? "z" : "x";
    const rawIn = (isSideWall ? hitPoint.z : hitPoint.x) * 12; // scene-feet -> inches
    const value = Math.round(rawIn / snapIn) * snapIn;
    return { axis, value };
}

// ─── Cheap client-side same-run overlap check (pure) ─────────────────────
// "collision tint COLLISION_RED when the server/local check flags"
// (brief item 4). This is the LOCAL half — instant feedback while
// dragging, before any server round trip. Checked against siblings on
// the SAME wall only (matches the 1-DOF along-wall constraint), ignores
// fillers (server-derived, not real placement conflicts) and the
// moving item itself.
export function hasLocalRunCollision(items, movingItem, axis, proposedValueIn) {
    if (!movingItem) return false;
    const wall = movingItem.wall || WALLS.BACK;
    const widthIn = movingItem.width_in || 0;
    const lo = proposedValueIn, hi = proposedValueIn + widthIn;
    return (items || []).some((other) => {
        if (!other || other.layout_key === movingItem.layout_key) return false;
        if ((other.wall || WALLS.BACK) !== wall) return false;
        if (other.cabinet_type === "filler") return false;
        const otherStart = axis === "z" ? (other.z_position_in || 0) : (other.x_position_in || 0);
        const otherEnd = otherStart + (other.width_in || 0);
        return lo < otherEnd && hi > otherStart;
    });
}

// ─── Camera-to-elevation target (pure) ───────────────────────────────────
// Brief item 5 / wallrun-rotation-review.md #3: back/left/right walls
// map onto view_specs.esm.js's own `front`/`left`/`right` ortho
// elevation presets (the "front" preset is parked OUTSIDE the front
// room edge looking at z=0 — i.e. it faces the BACK wall dead-on, per
// that module's own geometry). WALLS.FRONT (the low kick-strip wall —
// FULL_HEIGHT_WALLS excludes it) has NO dedicated elevation preset;
// the review's flagged fallback is "persp" (free look) — the closest
// "go look at it" the shipped view rig offers today.
const WALL_TO_ELEVATION_VIEW = Object.freeze({
    [WALLS.BACK]:  "front",
    [WALLS.LEFT]:  "left",
    [WALLS.RIGHT]: "right",
    [WALLS.FRONT]: "persp",
});

export function resolveElevationViewForWall(wall) {
    return WALL_TO_ELEVATION_VIEW[wall] || "persp";
}

function nowMs() {
    return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export class KitchenCanvas extends Component {
    // Task 22 (SBK-036) — tabindex makes the canvas a focusable surface
    // (keyboard shortcuts already work regardless, via the parent's
    // window-scoped keydown listener; this only adds the DOM focus
    // state a :focus outline can key off — see the SCSS focus-ring
    // rule for .o_sbk_canvas3d). aria-label gives it an accessible
    // name for screen readers tabbing through the page.
    //
    // Task 34 (SBK-020) — split the old single aria-label (name +
    // full keyboard-shortcut prose) into a NAME (this element's own
    // job per WAI-ARIA: "what is this") and a DESCRIPTION referenced
    // via aria-describedby (a separate, visually-hidden node — "how
    // do I use it"). The name now identifies the CURRENT DESIGN
    // (falls back to a generic label pre-hydration/landing, where
    // props.designName is still ""), so two designs open in two tabs
    // read as distinguishable landmarks instead of two identical "3D
    // kitchen scene" names. The shortcut prose — including the ←/→
    // cycling this task adds a11y coverage for — moved into
    // #sbkCanvasHint verbatim (no shortcut dropped), using the
    // Bootstrap 5 `.visually-hidden` utility already shipped in
    // web.assets_backend (no new CSS needed — southbrook design-
    // system convention is to reuse a platform utility before adding
    // a bespoke one).
    static template = xml`
        <div t-ref="kitchenCanvas" class="o_sbk_canvas3d" tabindex="0"
             t-att-aria-label="_ariaLabel()"
             aria-describedby="sbkCanvasHint">
            <span id="sbkCanvasHint" class="visually-hidden">
                Click a cabinet to select it. Left and right arrow keys
                cycle through every placed cabinet. Number keys 1
                through 6 switch camera views; R resets to isometric.
                Q and E rotate the selected cabinet. Delete or
                Backspace removes it. Escape clears the selection.
            </span>
        </div>
    `;
    static props = {
        items:          { type: Array, optional: true },
        room:           { type: Object, optional: true },
        view:           { type: String, optional: true },
        selected:       { type: [Object, { value: null }], optional: true },
        draggedProduct: { type: [Object, { value: null }], optional: true },
        dragHover:      { type: Boolean, optional: true },
        onSelectItem:   { type: Function, optional: true },
        // Task 35 — see the header comment block above for why this
        // exists as its own callback rather than being inferred from
        // onMoveItem's payload.
        onMoveItemStart: { type: Function, optional: true },
        onMoveItem:     { type: Function, optional: true },
        onResizeRoom:   { type: Function, optional: true },
        onResizeRoomDepth: { type: Function, optional: true },
        onViewChange:   { type: Function, optional: true },
        onReady:        { type: Function, optional: true },
        // Phase 1 interactive walls — the parent's active wall + the
        // click-to-select callback.
        onWallSelect:   { type: Function, optional: true },
        activeWall:     { type: [String, { value: null }], optional: true },
        // Task 34 (SBK-020) — the open design's name, purely for the
        // canvas's accessible name (see the template comment above).
        // Never read for anything else in this component.
        designName:     { type: String, optional: true },
        // ── Touch editing Wave 2 (docs/superpowers/specs/2026-08-02-
        // touch-editing-design.md) — parent-facing API. See the block
        // comment above the class for the full contract; summary:
        //   touchEnabled       — kill switch. false (default): ZERO new
        //                        listeners registered, zero proxy
        //                        meshes, zero new visual states — mouse
        //                        behaviour is byte-identical to pre-
        //                        touch. The coarse-pointer check +
        //                        ir.config_parameter kill switch live
        //                        in the PARENT; this prop is the single
        //                        gate this component itself trusts.
        //   touchSurfaceConfig — optional {slopPx, longPressMs,
        //                        longPressEnabled} forwarded verbatim
        //                        to createGestureReducer (spec §2:
        //                        "parameterized per surface" — portal/
        //                        phone vs backend/tablet each pass
        //                        their own). Read once at mount; not
        //                        live-reactive if changed later.
        //   dragCollision      — optional server/parent-computed
        //                        collision flag for whatever is
        //                        CURRENTLY being touch-dragged. ORed
        //                        with a cheap local same-wall overlap
        //                        pre-check (hasLocalRunCollision) this
        //                        component already does for instant
        //                        feedback; true paints the drag ghost
        //                        COLLISION_RED instead of shipped blue.
        //   onGestureAffordance(action, item) — fired if an IN-SCENE
        //                        affordance-glyph mesh is ever tapped
        //                        (reducer hit.kind==='affordance').
        //                        Currently unreachable in v1: this
        //                        component builds no in-scene
        //                        affordance meshes — the touch pad
        //                        (sibling-owned) calls
        //                        `api.cameraToElevation(wall)` directly
        //                        and drives send-to-wall/flip-hand
        //                        through the PARENT's own RPC wiring,
        //                        not through this canvas. Reserved for
        //                        a future in-scene glyph increment.
        touchEnabled:       { type: Boolean, optional: true },
        touchSurfaceConfig: { type: Object, optional: true },
        dragCollision:      { type: Boolean, optional: true },
        onGestureAffordance: { type: Function, optional: true },
        // Seam fix (2026-08-02) — health-counter producers. Emitted on
        // dragCancel consumption: ghost-mode cancel = abandon, any
        // other = rollback. Parents route these to _recordTouchEvent.
        onGestureRollback:  { type: Function, optional: true },
        onGestureAbandon:   { type: Function, optional: true },
    };
    static defaultProps = {
        items: [], room: {}, view: "iso",
        selected: null, draggedProduct: null, dragHover: false,
        activeWall: null, designName: "",
        touchEnabled: false, dragCollision: false,
    };

    // Task 34 (SBK-020) — accessible name for the canvas element.
    // Falls back to a generic label when no design is named yet
    // (landing/new/unsaved) so the element is never nameless.
    _ariaLabel() {
        const name = (this.props.designName || "").trim();
        return name
            ? `3D kitchen scene — ${name}`
            : "3D kitchen scene";
    }

    setup() {
        this.canvasRef = useRef("kitchenCanvas");
        this._currentView    = this.props.view || "iso";
        this._lastResizeWidth = null;
        this._lastResizeDepth = null;
        this._resizeObserver  = null;
        this.T = {
            THREE: null, scene: null, renderer: null,
            cameras: { ortho: null, persp: null },
            activeCamera: null, currentTarget: null,
            viewSpecs: null, vsScale: 1,
            orbit: null, raycaster: null,
            roomObjs: [], cabObjs: [], clickable: [],
            wallMeshes: {}, hoverWall: null,
            handleMesh: null, arrowMeshes: [],
            depthHandleMesh: null, depthArrowMeshes: [],
            laneMeshes: null,
            dragging: false, dragX0: 0, dragW0: 0,
            draggingDepth: false, dragY0: 0, dragD0: 0,
            animId: null, animatingCamera: false,
            movingItem: null, moveStartClient: { x: 0, y: 0 },
            moveLastX: null, moveCommitted: false,
            // Task 22 (SBK-036) — persistent across `_buildScene`
            // rebuilds; cleared (never recreated) at the top of every
            // rebuild so it never holds a reference into a torn-down
            // group. See selection.esm.js's docstring.
            selectionVisual: createSelectionState(),
            // Touch editing Wave 2 — visual-state bookkeeping, all
            // gated behind `this.props.touchEnabled` at every call
            // site (kill-switch discipline). `dimmed` mirrors
            // `selectionVisual.tinted`'s Map<mesh, originalMaterial>
            // shape; `liftedGroup`/`basePadMesh`/`touchGhostMesh`/
            // `touchOriginMesh` are cleared+rebuilt every `_buildScene`
            // (lift/dim) or explicitly by the drag lifecycle (ghost).
            dimmed: new Map(),
            liftedGroup: null, liftedOriginalY: 0, basePadMesh: null,
            touchGhostMesh: null, touchOriginMesh: null,
        };
        this._onMouseDown = this._onMouseDown.bind(this);
        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp   = this._onMouseUp.bind(this);
        this._onMouseOver = this._onMouseOver.bind(this);
        this._onWheel     = this._onWheel.bind(this);
        this._onPointerDown   = this._onPointerDown.bind(this);
        this._onPointerMove   = this._onPointerMove.bind(this);
        this._onPointerUp     = this._onPointerUp.bind(this);
        this._onPointerCancel = this._onPointerCancel.bind(this);

        // Touch editing Wave 2 — the T1 reducer is instantiated
        // unconditionally (pure, cheap, side-effect-free) but only
        // ever FED events when `touchEnabled` — see every handler's
        // own early-return guard below. `touchSurfaceConfig` is read
        // once here (spec §2 "parameterized per surface"); not
        // live-reactive if the parent changes it post-mount.
        this._gestureReducer = createGestureReducer(this.props.touchSurfaceConfig || {});
        this._gestureState = createInitialGestureState();
        this._touchListenersActive = false;
        this._longPressTimer = null;
        this._activeDragItem = null;
        this._dragSnapshot = null;
        this._orbitWasEnabled = null;
        this._snapPulseStart = null;

        onMounted(async () => {
            // TRIPWIRE:kitchen_canvas_mount_24c
            const mount = this.canvasRef.el;
            if (!mount) return;
            const THREE = await loadThreeJS();
            this.T.THREE = THREE;
            this.T.raycaster = new THREE.Raycaster();
            Object.assign(this.T, initScene(THREE, mount, P));
            installPbrEnvMap(THREE, this.T.scene, this.T.renderer);

            const tick = () => {
                this.T.animId = requestAnimationFrame(tick);
                if (this.T.orbit && this.T.orbit.enabled) this.T.orbit.update();
                // Touch editing Wave 2 — State 7 (Snap) pulse decay.
                // `_fireSnap` stamps `_snapPulseStart`; this is the
                // only per-frame consumer, so it's fine to leave the
                // full render loop as the single tick source rather
                // than a second rAF loop.
                if (this.T.touchGhostMesh && this._snapPulseStart != null) {
                    const tNorm = (nowMs() - this._snapPulseStart) / SNAP_PULSE_MS;
                    if (tNorm > 1) {
                        this._snapPulseStart = null;
                        this.T.touchGhostMesh.material.opacity = GHOST_OPACITY;
                    } else {
                        this.T.touchGhostMesh.material.opacity =
                            GHOST_OPACITY + snapPulse(tNorm) * (1 - GHOST_OPACITY);
                    }
                }
                this.T.renderer.render(this.T.scene, this.T.activeCamera);
            };
            tick();

            this._resizeObserver = new ResizeObserver(() => {
                const nw = mount.clientWidth, nh = mount.clientHeight;
                if (!nw || !nh) return;
                this.T.cameras.persp.aspect = nw / nh;
                this.T.cameras.persp.updateProjectionMatrix();
                this.T.renderer.setSize(nw, nh);
                this._applyOrthoFrustum();
            });
            this._resizeObserver.observe(mount);

            mount.addEventListener("mousedown",  this._onMouseDown, { capture: true });
            mount.addEventListener("mousemove",  this._onMouseMove);
            mount.addEventListener("mouseup",    this._onMouseUp);
            mount.addEventListener("mouseleave", this._onMouseUp);
            mount.addEventListener("mousemove",  this._onMouseOver);
            mount.addEventListener("wheel",      this._onWheel, { passive: false });
            // Touch editing Wave 2 — no-op unless props.touchEnabled.
            this._syncTouchListeners(!!this.props.touchEnabled);

            this._buildScene(this.props);

            // Golden-scene harness — expose a serialisable snapshot of every
            // cabinet leaf-mesh world transform, for visual-regression
            // baselines (straight vs L). Inspection-only; no rendering effect.
            // Capture in the browser console via:
            //   document.querySelector('.o_sbk_canvas3d').__sbSceneSnapshot()
            mount.__sbSceneSnapshot = () => this._sbSceneSnapshot();

            if (this.props.onReady) {
                this.props.onReady({
                    setView: (key) => this._setView(key),
                    zoomBy:  (k)   => this._zoomBy(k),
                    computeDropX: (ev) => this._computeDropX(ev),
                    // PR4b — sibling to computeDropX: which wall the HTML5
                    // drop landed on (null when the ray misses every wall).
                    computeDropWall: (ev) => this._computeDropWall(ev),
                    // T7 — drag ghost preview (visual affordance only;
                    // placement math stays server-side).
                    updateDragGhost: (ev) => this._updateDragGhost(ev),
                    clearDragGhost:  ()   => this._clearDragGhost(),
                    // Touch editing Wave 2 (brief item 5) — imperative
                    // camera-to-elevation, for the touch pad's camera-
                    // icon button (sibling-owned). Zero data mutation.
                    cameraToElevation: (wall) => this._cameraToElevation(wall),
                });
            }
        });

        onWillUpdateProps((next) => {
            if (!this.T.scene) return;
            // Touch editing Wave 2 — toggle listener registration
            // dynamically (e.g. an async kill-switch RPC flips
            // `touchEnabled` after first mount). No-op if unchanged.
            if (next.touchEnabled !== this.props.touchEnabled) {
                this._syncTouchListeners(!!next.touchEnabled);
            }
            // In-flight drag: child is source of truth; ignore
            // prop echos from parent to avoid a re-render loop. Extended
            // for touch: a gesture mid-`dragging`/`ghostDrag` is JUST as
            // much "child is source of truth" as the mouse equivalents.
            const touchGestureActive = this._gestureState &&
                (this._gestureState.phase === GESTURE_PHASES.DRAGGING ||
                 this._gestureState.phase === GESTURE_PHASES.GHOST_DRAG);
            if (this.T.dragging || this.T.draggingDepth || this.T.movingItem || touchGestureActive) return;

            const roomChanged = next.room !== this.props.room ||
                (next.room && this.props.room && (
                    next.room.width_in  !== this.props.room.width_in  ||
                    next.room.depth_in  !== this.props.room.depth_in  ||
                    next.room.height_in !== this.props.room.height_in));
            const itemsChanged  = next.items !== this.props.items ||
                (next.items && this.props.items &&
                 next.items.length !== this.props.items.length);
            const selChanged    = next.selected !== this.props.selected;
            const dragUiChanged = next.dragHover !== this.props.dragHover ||
                                  next.draggedProduct !== this.props.draggedProduct;
            const viewChanged   = next.view !== this._currentView;

            if (roomChanged || itemsChanged) {
                this._buildScene(next);
            } else if (selChanged) {
                applySelectionVisuals(this.T, next.selected, P);
                // Touch editing Wave 2 — State 2 (lift+pad) / State 3
                // (others dimmed) compose with the existing tint/
                // outline/bbox above; gated on `next.touchEnabled` so
                // mouse-only surfaces never see them.
                if (next.touchEnabled) {
                    this._applySelectedLift(next.selected);
                    this._applyDimToOthers(next.selected);
                } else {
                    this._clearSelectedLift();
                    this._clearDimVisuals();
                }
            }
            // Active-wall change: just repaint the wall glow (no rebuild).
            if (!(roomChanged || itemsChanged)
                && next.activeWall !== this.props.activeWall) {
                this._applyWallHighlight(next.activeWall);
            }
            if (dragUiChanged) this._updateLaneVisibility(next);
            // Touch editing Wave 2 — a server round-trip can flip
            // `dragCollision` mid-drag (parent-computed, ORed with our
            // own local same-wall check inside `_applyDragTo`); retint
            // the live ghost immediately rather than waiting for the
            // next pointermove tick.
            if (next.dragCollision !== this.props.dragCollision && this.T.touchGhostMesh) {
                const stillLocal = this._activeDragItem &&
                    hasLocalRunCollision(next.items, this._activeDragItem,
                        (this._activeDragItem.wall === WALLS.LEFT || this._activeDragItem.wall === WALLS.RIGHT) ? "z" : "x",
                        this._activeDragItem.wall === WALLS.LEFT || this._activeDragItem.wall === WALLS.RIGHT
                            ? (this._activeDragItem.z_position_in || 0) : (this._activeDragItem.x_position_in || 0));
                this._updateTouchDragGhostTint(!!next.dragCollision || !!stillLocal);
            }
            if (viewChanged) {
                this._currentView = next.view;
                this._setView(next.view, /*instant=*/false, /*skipCallback=*/true);
            }
        });

        onWillUnmount(() => {
            if (this.T.animId) cancelAnimationFrame(this.T.animId);
            if (this._resizeObserver) this._resizeObserver.disconnect();
            const mount = this.canvasRef.el;
            if (mount) {
                mount.removeEventListener("mousedown",  this._onMouseDown, { capture: true });
                mount.removeEventListener("mousemove",  this._onMouseMove);
                mount.removeEventListener("mouseup",    this._onMouseUp);
                mount.removeEventListener("mouseleave", this._onMouseUp);
                mount.removeEventListener("mousemove",  this._onMouseOver);
                mount.removeEventListener("wheel",      this._onWheel);
            }
            this._clearLongPressTimer();
            this._syncTouchListeners(false);
            this._teardownScene(mount);
        });
    }

    // Touch editing Wave 2 — (de)register the pointer-event capture-
    // phase listeners on `mount` and scope `touch-action: none` to the
    // inner `<canvas>` element only (brief item 1). Called once from
    // `onMounted` and again whenever `props.touchEnabled` toggles at
    // runtime (e.g. an async kill-switch RPC resolves after first
    // paint) — idempotent either direction. Disabling also resets all
    // in-flight touch state so a mid-gesture kill-switch flip can never
    // leave a dangling timer/ghost/lift/dim behind.
    _syncTouchListeners(enabled) {
        const mount = this.canvasRef.el;
        if (!mount) return;
        const cvs = this.T.renderer && this.T.renderer.domElement;
        if (enabled && !this._touchListenersActive) {
            mount.addEventListener("pointerdown",   this._onPointerDown,   { capture: true });
            mount.addEventListener("pointermove",   this._onPointerMove,   { capture: true });
            mount.addEventListener("pointerup",     this._onPointerUp,     { capture: true });
            mount.addEventListener("pointercancel", this._onPointerCancel, { capture: true });
            if (cvs) cvs.style.touchAction = "none";
            this._touchListenersActive = true;
        } else if (!enabled && this._touchListenersActive) {
            mount.removeEventListener("pointerdown",   this._onPointerDown,   { capture: true });
            mount.removeEventListener("pointermove",   this._onPointerMove,   { capture: true });
            mount.removeEventListener("pointerup",     this._onPointerUp,     { capture: true });
            mount.removeEventListener("pointercancel", this._onPointerCancel, { capture: true });
            if (cvs) cvs.style.touchAction = "";
            this._touchListenersActive = false;
            this._gestureState = createInitialGestureState();
            this._clearLongPressTimer();
            this._hideTouchDragGhost();
            this._clearSelectedLift();
            this._clearDimVisuals();
            this._activeDragItem = null;
            this._dragSnapshot = null;
            this._orbitWasEnabled = null;
        }
    }

    // Task 22 review fix (SBK-036, 2026-07-30) — extracted from
    // onWillUnmount's body so it's independently callable, same as
    // `_placeCabinetGroup`/`_sbSceneSnapshot` above (this environment
    // has no WebGL, so a full owl.mount()+unmount() of this component
    // isn't possible in the node test harness — THREE.WebGLRenderer's
    // constructor throws without a real GL context; see scene_init.esm.js.
    // Tests instead call this method directly against a fake `this`,
    // exactly like every other `KitchenCanvas.prototype.*.call(fake, ...)`
    // test in this suite).
    //
    // Unmounting while a cabinet was selected used to leak the tint
    // clone's material, every outline LineSegments' geometry+material,
    // and the Box3Helper's geometry+material: `destroyScene` only
    // disposes the renderer/orbit, never traverses/disposes the scene
    // graph (a pre-existing characteristic of roomObjs/cabObjs too —
    // out of this task's scope to fix generally), so nothing else
    // would have caught these Task-22-introduced objects either.
    // `clearSelectionVisuals` mirrors the same call at the top of every
    // `_buildScene` rebuild.
    _teardownScene(mount) {
        clearSelectionVisuals(this.T.selectionVisual);
        // Touch editing Wave 2 — same leak class Task 22 fixed for
        // tint/outline/bbox, extended to the lift/dim/ghost overlays.
        // Optional-chained: some existing node contracts call this
        // method against a hand-built fake `this` (a bare `{T:{...}}`
        // object, not `Object.create(KitchenCanvas.prototype)`) that
        // predates these Wave-2 methods — see
        // 20_selection_visuals.test.mjs's "unmounting while selected"
        // test. A real KitchenCanvas instance always has these on its
        // prototype, so this is a no-op safety net in production, not
        // a behavior change.
        this._clearSelectedLift?.();
        this._clearDimVisuals?.();
        this._hideTouchDragGhost?.();
        destroyScene(this.T, mount);
    }

    _buildScene(props) {
        this._clearDragGhost();   // T7 — never survive a rebuild
        // Task 22 (SBK-036) — clear the selection tint/outline/bbox
        // BEFORE the old cabObjs are disposed below: this restores
        // every tinted mesh's material to its original reference and
        // disposes our own clones/overlays deterministically, instead
        // of leaving the original (untinted) material objects to leak
        // as orphaned references once the old groups are torn down.
        clearSelectionVisuals(this.T.selectionVisual);
        // Touch editing Wave 2 — same "clear before the old groups are
        // disposed" timing as the tint/outline/bbox above: `liftedGroup`
        // and every dimmed mesh reference are about to be torn down by
        // the dispose loop below, so restore-then-clear now, before
        // that happens, exactly like clearSelectionVisuals already does.
        this._clearSelectedLift();
        this._clearDimVisuals();
        const { THREE, scene } = this.T;
        if (!scene) return;
        const room = props.room || {};
        const rw = (room.width_in  || 12) * IN;
        const rd = (room.depth_in  || 24) * IN;
        const rh = (room.height_in || 96) * IN;

        const dispose = (n) => {
            if (!n) return;
            if (n.geometry) n.geometry.dispose();
            if (n.material) {
                if (Array.isArray(n.material)) n.material.forEach(x => x.dispose());
                else n.material.dispose();
            }
        };
        [...this.T.roomObjs, ...this.T.cabObjs].forEach(m => {
            if (m && typeof m.traverse === "function") m.traverse(dispose);
            else dispose(m);
            scene.remove(m);
        });
        if (this.T.handleMesh) { scene.remove(this.T.handleMesh); dispose(this.T.handleMesh); }
        this.T.arrowMeshes.forEach(m => { scene.remove(m); dispose(m); });
        if (this.T.depthHandleMesh) { scene.remove(this.T.depthHandleMesh); dispose(this.T.depthHandleMesh); }
        this.T.depthArrowMeshes.forEach(m => { scene.remove(m); dispose(m); });
        if (this.T.laneMeshes) {
            for (const k of ["base", "wall", "tall"]) {
                const m = this.T.laneMeshes[k];
                if (!m) continue;
                scene.remove(m); dispose(m);
            }
            this.T.laneMeshes = null;
        }
        this.T.roomObjs = []; this.T.cabObjs = []; this.T.clickable = [];
        this.T.wallMeshes = {};
        this.T.handleMesh = null; this.T.arrowMeshes = [];
        this.T.depthHandleMesh = null; this.T.depthArrowMeshes = [];

        const mk = (geo, color, pos, rotE, opts) =>
            makeMesh(THREE, scene, geo, color, pos, rotE, opts);

        const { objects: shellObjects, walls } =
            buildRoomShell(THREE, scene, mk, P, rw, rh, rd);
        this.T.roomObjs.push(...shellObjects);
        this.T.wallMeshes = walls || {};

        const items = props.items || [];
        const knownTypes = new Set(["base", "wall", "filler", "panel", "corner"]);
        // PR3.1 — every cabinet type routes through the single central
        // group-transform path (_placeCabinetGroup); the old per-type
        // rotation-gated dual dispatch (legacy absolute path vs. group
        // path) is gone. See _placeCabinetGroup's docstring for why this
        // is byte-identical for rotation_deg=0 (back-wall) cabinets.
        items.filter(it => it.cabinet_type === "base").forEach(it =>
            this._placeCabinetGroup(it, buildBaseCabinet));
        items.filter(it => it.cabinet_type === "wall").forEach(it =>
            this._placeCabinetGroup(it, buildWallCabinet));
        // Task 8 (corner-remediation) — corner lines get real corner
        // massing (diagonal/pie-cut/blind) instead of falling into the
        // generic buildOtherCabinet box below. cornerBlindNearX0 is a
        // pure geometry helper (position + room dims only, no placement
        // change) that tells the blind-corner style which end of the
        // cell sits nearest an actual room corner; every other style
        // ignores it. Passed via `extra` (code-review fix, 2026-07-30)
        // — NOT by substituting a copy for `it` — so the real state
        // item, not a clone, is what _placeCabinetGroup binds as
        // userData.item (drag/pin/select identity; see its docstring).
        items.filter(it => it.cabinet_type === "corner").forEach(it =>
            this._placeCabinetGroup(it, buildCornerCabinet,
                { __blindNearX0: cornerBlindNearX0(it, room) }));
        items.filter(it => !knownTypes.has(it.cabinet_type)).forEach(it =>
            this._placeCabinetGroup(it, buildOtherCabinet));
        items.filter(it => it.cabinet_type === "filler").forEach(it =>
            this._placeCabinetGroup(it, buildFillerPanel));
        items.filter(it => it.cabinet_type === "panel").forEach(it =>
            this._placeCabinetGroup(it, buildEndCapPanel));

        const { handleMesh, arrowMeshes } =
            buildDragHandle(THREE, scene, P, rw, rd);
        this.T.handleMesh = handleMesh;
        this.T.arrowMeshes.push(...arrowMeshes);
        const { depthHandleMesh, depthArrowMeshes } =
            buildDepthHandle(THREE, scene, P, rw, rd);
        this.T.depthHandleMesh = depthHandleMesh;
        this.T.depthArrowMeshes.push(...depthArrowMeshes);
        this.T.laneMeshes = buildDropLanes(THREE, scene, rw, rh);
        this._updateLaneVisibility(props);

        this._recomputeViews(rw, rh, rd);
        this._setView(props.view || this._currentView, /*instant=*/true, /*skipCallback=*/true);
        if (props.selected) applySelectionVisuals(this.T, props.selected, P);
        // Touch editing Wave 2 — reapply lift+pad / dim-others on every
        // rebuild, same timing as the tint/outline/bbox line above.
        // Gated on `props.touchEnabled`: mouse-only renders never pay
        // for (or see) either.
        if (props.touchEnabled && props.selected) {
            this._applySelectedLift(props.selected);
            this._applyDimToOthers(props.selected);
        }
        this._applyWallHighlight(props.activeWall);
    }

    // Phase 1 — paint each room wall by its interaction state: the active
    // (selected) wall gets a strong blue glow, the hovered wall a soft one,
    // everything else no glow. Purely material emissive; re-applied after
    // every rebuild and on hover/select changes.
    _applyWallHighlight(active) {
        if (active === undefined) active = this.props.activeWall;
        const hover  = this.T.hoverWall;
        for (const [name, mesh] of Object.entries(this.T.wallMeshes)) {
            if (!mesh || !mesh.material) continue;
            let hex = 0x000000, intensity = 0.0;
            if (name === active) { hex = 0x1866D4; intensity = 0.45; }
            else if (name === hover) { hex = 0x1866D4; intensity = 0.16; }
            mesh.material.emissive.setHex(hex);
            mesh.material.emissiveIntensity = intensity;
        }
    }

    // ── T7 — drag ghost preview ─────────────────────────────────────
    // ONE translucent box at the hovered wall's run end while a catalog
    // product is dragged over the canvas. Extends the wall hover soft-
    // glow with a concrete "it would land about here" affordance. NO
    // placement math is duplicated: the box parks at the end of the
    // hovered wall's current run (max along + width); the real pose
    // still comes from the server-side engine on drop.
    _updateDragGhost(ev) {
        const THREE = this.T.THREE;
        const prod = this.props.draggedProduct;
        if (!THREE || !this.T.scene || !this.T.activeCamera || !prod) {
            this._clearDragGhost();
            return;
        }
        this.T.raycaster.setFromCamera(
            this._ndcFromEvent(ev), this.T.activeCamera);
        const wall = this._raycastWall(this.T.raycaster);
        if (!wall) {
            this._clearDragGhost();
            return;
        }
        if (wall !== this.T.hoverWall) {
            this.T.hoverWall = wall;
            this._applyWallHighlight();
        }
        const room = this.props.room || {};
        const roomW = room.width_in || 12, roomD = room.depth_in || 24;
        const isUpper = prod.cabinet_type === "wall";
        const w = prod.width_in  || 24;
        const h = prod.height_in || (isUpper ? 30 : 34.5);
        const d = prod.depth_in  || (isUpper ? 12 : 24);
        const alongOf = (it) => (wall === "back" || wall === "front")
            ? (it.x_position_in || 0) : (it.z_position_in || 0);
        const runEnd = (this.props.items || [])
            .filter(it => (it.wall || "back") === wall
                && (it.cabinet_type === "wall") === isUpper)
            .reduce((m, it) => Math.max(m, alongOf(it) + (it.width_in || 0)), 0);
        let y = 0;
        if (isUpper) {
            const anyUpper = (this.props.items || []).find(
                it => it.cabinet_type === "wall");
            y = (anyUpper && anyUpper.y_position_in) || 54;
        }
        // Centre-of-box position per wall (axis-aligned; side walls
        // swap width/depth so the box hugs the wall).
        let cx, cz, gw = w, gd = d;
        if (wall === "back")       { cx = runEnd + w / 2; cz = d / 2; }
        else if (wall === "front") { cx = runEnd + w / 2; cz = roomD - d / 2; }
        else if (wall === "left")  { gw = d; gd = w; cx = d / 2; cz = runEnd + w / 2; }
        else                       { gw = d; gd = w; cx = roomW - d / 2; cz = runEnd + w / 2; }
        if (!this.T.ghostMesh) {
            const geo = new THREE.BoxGeometry(1, 1, 1);
            const mat = new THREE.MeshStandardMaterial({
                color: 0x1866D4, transparent: true, opacity: 0.35,
                depthWrite: false,
            });
            this.T.ghostMesh = new THREE.Mesh(geo, mat);
            this.T.ghostMesh.renderOrder = 999;
            this.T.scene.add(this.T.ghostMesh);
        }
        this.T.ghostMesh.scale.set(gw * IN, h * IN, gd * IN);
        this.T.ghostMesh.position.set(
            cx * IN, (y + h / 2) * IN, cz * IN);
        this.T.ghostMesh.visible = true;
    }

    _clearDragGhost() {
        const g = this.T.ghostMesh;
        if (!g) return;
        this.T.scene && this.T.scene.remove(g);
        if (g.geometry) g.geometry.dispose();
        if (g.material) g.material.dispose();
        this.T.ghostMesh = null;
    }

    _updateLaneVisibility(props) {
        const lanes = this.T.laneMeshes;
        if (!lanes) return;
        const show = !!(props.dragHover && props.draggedProduct);
        const type = (props.draggedProduct && props.draggedProduct.cabinet_type) || "";
        const baseHi = (type === "base" || type === "filler");
        const wallHi = (type === "wall");
        const tallHi = (type === "tall" || type === "corner" || type === "panel");
        lanes.base.visible = show;
        lanes.wall.visible = show;
        lanes.tall.visible = show;
        if (show) {
            lanes.base.material.opacity = baseHi ? 0.42 : 0.14;
            lanes.wall.material.opacity = wallHi ? 0.42 : 0.14;
            lanes.tall.material.opacity = tallHi ? 0.42 : 0.14;
        }
    }

    _recomputeViews(rw, rh, rd) {
        this.T.viewSpecs = computeViewSpecs(this.T.THREE, rw, rh, rd);
    }

    _applyOrthoFrustum() {
        const t = this.T;
        const cam = t.cameras?.ortho;
        if (!cam || !t.renderer) return;
        const spec = t.viewSpecs?.[this._currentView];
        const f = computeOrthoFrustum(
            spec && spec.vs, t.vsScale,
            t.renderer.domElement.width,
            t.renderer.domElement.height,
        );
        cam.left = f.left; cam.right = f.right;
        cam.top  = f.top;  cam.bottom = f.bottom;
        cam.near = f.near; cam.far    = f.far;
        cam.updateProjectionMatrix();
    }

    _setView(key, instant = false, skipCallback = false) {
        if (!this.T.viewSpecs || !this.T.viewSpecs[key]) return;
        this._currentView = key;
        const ok = setViewShared(this.T, key, {
            instant,
            applyOrthoFrustum: () => this._applyOrthoFrustum(),
            animateCamera: (p, tgt, up, ms) => this._animateCamera(p, tgt, up, ms),
        });
        if (ok && !skipCallback && this.props.onViewChange) {
            this.props.onViewChange(key);
        }
    }

    _animateCamera(toPos, toTarget, toUp, ms = 400) {
        animateCameraShared(
            this.T, toPos, toTarget, toUp, ms,
            easeInOutCubic, requestAnimationFrame,
        );
    }

    _onWheel(e) { onWheelShared(this.T, e, () => this._applyOrthoFrustum()); }

    _zoomBy(k) {
        this.T.vsScale = Math.max(0.3, Math.min(3.0, (this.T.vsScale || 1) * k));
        this._applyOrthoFrustum();
    }

    _ndcFromEvent(ev) { return ndcFromEvent(ev, this.canvasRef.el); }

    _computeDropX(ev) {
        return computeDropXIn(
            this.T.THREE, this.T.activeCamera, this.T.raycaster,
            this._ndcFromEvent(ev),
            (this.props.room && this.props.room.width_in) || 0,
        );
    }

    // PR4b — cast the HTML5 drop point against the tagged room-wall
    // meshes and return which wall (back/left/right/front) the pointer
    // dropped onto. This adds NO placement math: it only names the
    // wall. Returns null when the ray misses every eligible wall mesh
    // (e.g. a drop over open floor) so the caller can fall back to
    // activeWall.
    //
    // F7 fix (2026-07-27) — this used to call the shared `_raycastWall`
    // (all four wall meshes, including the low 12" kick-strips
    // room_shell.esm.js renders for right/front). A drop near the room
    // boundary could land on an invisible kick-strip and silently
    // resolve to that wall — overriding the user's actual Active Wall
    // pick even though they never intended to target right/front.
    // Drop-inference is now restricted to `_raycastDropWall`, which only
    // considers FULL_HEIGHT_WALLS (constants.esm.js). Click-to-select
    // (`_onMouseDown`/`_onMouseOver`, still via `_raycastWall`) is
    // unaffected — kick-strip walls stay explicitly clickable.
    _computeDropWall(ev) {
        const { activeCamera, raycaster } = this.T;
        if (!activeCamera || !raycaster) return null;
        raycaster.setFromCamera(this._ndcFromEvent(ev), activeCamera);
        return this._raycastDropWall(raycaster);
    }

    // F7 fix (2026-07-27) — drop-inference-only wall raycast: filters
    // `wallMeshes` down to the full-height walls (back/left today) before
    // testing intersection, so a stray ray landing on a low kick-strip
    // (right/front) never gets inferred as a deliberate drop-onto-wall.
    // Sibling to `_raycastWall` (used by click/hover, all four walls).
    _raycastDropWall(raycaster) {
        const meshes = Object.entries(this.T.wallMeshes)
            .filter(([name, mesh]) => mesh && FULL_HEIGHT_WALLS.includes(name))
            .map(([, mesh]) => mesh);
        if (!meshes.length) return null;
        const hit = raycaster.intersectObjects(meshes, false);
        return hit.length ? (hit[0].object.userData && hit[0].object.userData.wall) || null : null;
    }

    // Golden-scene harness. Returns a sorted, order-independent list of every
    // cabinet leaf-mesh world transform [x, y, z, rotY°] (mm/deg, rounded).
    // Traversing to leaf meshes keeps the snapshot stable across the coming
    // THREE.Group refactor: a straight kitchen must produce an IDENTICAL
    // snapshot before and after each builder migration (compatibility gate).
    _sbSceneSnapshot() {
        const THREE = this.T.THREE, scene = this.T.scene;
        if (!THREE || !scene) return [];
        scene.updateMatrixWorld(true);
        const out = [];
        const p = new THREE.Vector3(), q = new THREE.Quaternion(),
              s = new THREE.Vector3(), e = new THREE.Euler();
        const collect = (obj) => {
            if (!obj || !obj.isMesh) return;
            obj.matrixWorld.decompose(p, q, s);
            e.setFromQuaternion(q, "YXZ");
            out.push([
                +p.x.toFixed(3), +p.y.toFixed(3), +p.z.toFixed(3),
                +((e.y * 180) / Math.PI).toFixed(1),
            ]);
        };
        for (const obj of this.T.cabObjs) {
            if (obj && obj.traverse) obj.traverse(collect);
            else collect(obj);
        }
        out.sort();
        return out;
    }

    // PR3.1 — the single placement path for every cabinet: a THREE.Group
    // carrying its world transform (x/y/z_position_in + rotation_deg per
    // COORDINATE_CONTRACT). The builder builds in a LOCAL frame
    // (x_position_in forced to 0; each builder that reads y_position_in
    // also zeroes it locally via item.__localFrame — see base/wall/other
    // builders); the group applies the world position + Y-rotation.
    // Teardown already handles groups (traverse); raycasting still hits
    // the real body mesh (its matrixWorld includes the group transform).
    //
    // Byte-identical for rotation_deg=0 (back-wall) cabinets by
    // construction: PR3.0 guarantees z_position_in=0 for every back-wall
    // cabinet_type, and y_position_in is 0 for floor-standing types
    // (base/tall/corner/panel/filler — every writer emits y=0,z=0 for
    // these) or the real D8 mount height for wall cabinets (every writer
    // emits a nonzero y for wall cabinets; wall_cabinet.esm.js's wbY
    // fallback additionally reproduces the pre-D8 WBY default locally
    // when y_position_in is ever falsy, so the group's grp.position.y=0
    // in that case doesn't drop the cabinet to the floor). With those
    // offsets zero (or reproduced locally), grp.position + local mesh
    // coordinates sum to exactly the old legacy absolute-path world
    // coordinates.
    // `extra` (optional) — additional read-only geometry hints merged
    // ONLY into the local-frame copy the builder sees (e.g. corner_
    // cabinet.esm.js's __blindNearX0), never onto `it` itself and never
    // onto the object bound as userData.item below. Task 8 code-review
    // fix (2026-07-30): an earlier version of the corner dispatch
    // computed such a hint by passing a NEW shallow-copied item as `it`
    // — that copy, not the real state.items entry, is what got bound as
    // userData.item, breaking drag/pin/select for every corner cabinet
    // (the exact F8 bug class described below, reintroduced by that
    // call site). `extra` fixes this at the root: `it` passed to this
    // method is ALWAYS the real state item, unconditionally.
    _placeCabinetGroup(it, builder, extra) {
        const THREE = this.T.THREE, scene = this.T.scene;
        const grp = new THREE.Group();
        const mkG = (geo, color, pos, rotE, opts) =>
            makeMesh(THREE, grp, geo, color, pos, rotE, opts);
        const res = builder(THREE, mkG, P,
            { ...it, ...(extra || null), x_position_in: 0, __localFrame: true });
        // F8 fix (2026-07-27) — the builder above ran against `{ ...it,
        // x_position_in: 0, __localFrame: true }`, a SHALLOW COPY used
        // only so the builder's local-frame geometry math (walls/panels
        // reading __localFrame to zero their own Y/X contribution — see
        // wall_cabinet.esm.js / other_cabinets.esm.js) works regardless
        // of the group's world transform below. Every builder attaches
        // that copy as `mesh.userData.item` (base/wall/other_cabinets'
        // `ud: { cab, cabType, item }`), so without this rebind
        // `mesh.userData.item !== it` — the real state.items entry.
        // Consequence (verified live): `_onMouseDown`'s drag-move and
        // Q/E rotate mutate the clone's x_position_in/rotation_deg
        // (never seen again — it's discarded on the next rebuild) while
        // `_savePinnedPosition`/`onMoveItem` still persist the clone's
        // values to the DB by layout_key — a silent no-op in the
        // client's own state that only surfaces as a divergence on
        // reload. The clone's x_position_in is also permanently forced
        // to 0, so `state.selected` (populated straight from
        // `h.userData.item` in `_onMouseDown`) read x=0 for whatever was
        // selected.
        //
        // Fix: after the builder returns, walk every mesh it attached
        // (the group + every child, since `mkG` above adds children
        // directly onto `grp`) and rebind any `userData.item` that
        // still points at the local-frame copy back onto the REAL item
        // `it`. The copy is discarded here; nothing downstream ever
        // sees it again. This does not touch the builder's geometry math
        // (which already ran) — only the ATTACHED INTERACTION IDENTITY.
        grp.traverse((obj) => {
            if (obj.userData && obj.userData.item) {
                obj.userData.item = it;
            }
        });
        grp.userData.item = it;
        grp.position.set(
            (it.x_position_in || 0) * IN,
            (it.y_position_in || 0) * IN,
            (it.z_position_in || 0) * IN,
        );
        grp.rotation.y = ((it.rotation_deg || 0) * Math.PI) / 180;
        scene.add(grp);
        this.T.cabObjs.push(grp);
        if (res && res.clickable) this.T.clickable.push(...res.clickable);
        // Touch editing Wave 2 (brief item 2) — gated behind
        // `touchEnabled` (defensive `this.props &&` since several
        // existing node contracts call this method against a fake
        // `this` with no `props` key at all — see 03_renderer_coords/
        // 10_scene_selection_identity/17_corner_identity/20_selection_
        // visuals). Shares the SAME `T.clickable` list mouse raycasts
        // already consult (see `_computeTouchHit`'s docblock for why
        // that's a deliberate, low-risk choice, not a second parallel
        // hit-test path).
        if (this.props && this.props.touchEnabled) {
            this._addTouchProxyMesh(grp, it);
        }
    }

    // Touch editing Wave 2 (brief item 2) — an invisible, enlarged
    // BoxGeometry mesh added as a CHILD of the cabinet's own group (so
    // it inherits the exact same world transform, F8-bound to the SAME
    // real `it`), padded just enough on width/depth that its on-screen
    // footprint clears HIT_PROXY_MIN_PX. `visible=false` meshes still
    // raycast in three.js by default — "free" hit inflation, no extra
    // render cost. Never `userData.cab`-flagged (selection.esm.js's
    // tint traversal filters on that key), so it's never mistaken for
    // a tintable/dimmable surface.
    _addTouchProxyMesh(grp, it) {
        const THREE = this.T.THREE;
        if (!THREE) return;
        const widthIn = it.width_in || 24, heightIn = it.height_in || 34.5, depthIn = it.depth_in || 24;
        const pxPerIn = this._estimatePxPerIn();
        const padWIn = computeProxyPaddingIn(widthIn, pxPerIn);
        const padDIn = computeProxyPaddingIn(depthIn, pxPerIn);
        const geo = new THREE.BoxGeometry(
            (widthIn + 2 * padWIn) * IN, heightIn * IN, (depthIn + 2 * padDIn) * IN);
        const mat = new THREE.MeshBasicMaterial({ visible: false });
        const proxy = new THREE.Mesh(geo, mat);
        proxy.visible = false;
        // Local frame: the group's own origin is the item's corner
        // (see the class docblock above), so the proxy's centre is
        // half the (padded) footprint in from that corner — same
        // local-frame convention every builder already uses.
        proxy.position.set(widthIn * IN / 2, heightIn * IN / 2, depthIn * IN / 2);
        proxy.userData = { item: it, touchProxy: true };
        grp.add(proxy);
        this.T.clickable.push(proxy);
    }

    // Touch editing Wave 2 — px-per-world-inch for whichever camera is
    // currently active, feeding `computeProxyPaddingIn`'s sizing math.
    // Ortho: exact, from the live frustum. Persp: an approximation (see
    // `computePerspPxPerIn`'s own docblock) — acceptable since this only
    // sizes an invisible hit-target, never anything render-critical.
    _estimatePxPerIn() {
        const t = this.T;
        if (!t.renderer || !t.activeCamera || !t.renderer.domElement) return 0;
        const pxH = t.renderer.domElement.height;
        if (!pxH) return 0;
        if (t.cameras && t.activeCamera === t.cameras.ortho) {
            const spec = t.viewSpecs && t.viewSpecs[this._currentView];
            const frustum = computeOrthoFrustum(
                spec && spec.vs, t.vsScale, t.renderer.domElement.width, pxH);
            return computeOrthoPxPerIn(frustum.top - frustum.bottom, pxH);
        }
        const dist = (t.currentTarget && t.activeCamera.position)
            ? t.activeCamera.position.distanceTo(t.currentTarget) / IN
            : 0;
        return computePerspPxPerIn(pxH, t.activeCamera.fov || 45, dist);
    }

    _onMouseDown(e) {
        // Task 22 (SBK-036) — canvas focus ring. Several branches below
        // call e.preventDefault() (handle-drag, cabinet-select), which
        // in some browsers suppresses the native "click focuses the
        // element" default action — so focus is claimed explicitly
        // here, unconditionally, rather than relying on that default.
        const mount = this.canvasRef.el;
        if (mount && typeof mount.focus === "function") {
            mount.focus({ preventScroll: true });
        }
        const { activeCamera, raycaster, handleMesh, arrowMeshes,
                depthHandleMesh, depthArrowMeshes, clickable } = this.T;
        if (!activeCamera || !raycaster) return;
        raycaster.setFromCamera(this._ndcFromEvent(e), activeCamera);

        // A handle's grab target is the whole visible cluster — the ●
        // sphere AND its flanking arrow cones. Sphere-only hit-testing
        // made the arrows dead: the width arrows happened to overlap
        // their sphere in screen space (so pulling them "worked"), but
        // the depth arrows don't, so pulling them hit nothing.
        const hitsWidth = handleMesh &&
            raycaster.intersectObjects([handleMesh, ...arrowMeshes], false).length;
        const hitsDepth = depthHandleMesh &&
            raycaster.intersectObjects([depthHandleMesh, ...depthArrowMeshes], false).length;

        const handleActive = isHandleActiveView(this._currentView);
        // TEMP DIAG (2026-07-11): opt-in, off by default. In the browser
        // console run `window.__SBK_DEPTH_DEBUG__ = true` then click the
        // depth pin to see why the raycast is/ isn't hitting. Zero effect
        // for normal users. Remove once the depth handle is confirmed.
        if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
            // eslint-disable-next-line no-console
            console.debug("[sbk-depth] mousedown", {
                view: this._currentView, handleActive,
                hasWidthMesh: !!handleMesh, hasDepthMesh: !!depthHandleMesh,
                widthHit: hitsWidth || 0, depthHit: hitsDepth || 0,
                client: [e.clientX, e.clientY],
            });
        }
        if (handleActive && hitsWidth) {
            this.T.dragging = true;
            this.T.dragX0   = e.clientX;
            this.T.dragW0   = (this.props.room && this.props.room.width_in) || 0;
            this._lastResizeWidth = null;
            e.preventDefault();
            return;
        }
        if (handleActive && hitsDepth) {
            this.T.draggingDepth = true;
            this.T.dragY0   = e.clientY;
            this.T.dragD0   = (this.props.room && this.props.room.depth_in) || 0;
            this._lastResizeDepth = null;
            e.preventDefault();
            return;
        }

        const hits = raycaster.intersectObjects(clickable, false);
        if (hits.length) {
            const h = hits[0].object;
            if (h.userData?.item) {
                if (this.props.onSelectItem) this.props.onSelectItem(h.userData.item);
                if (h.userData.item.cabinet_type !== "filler") {
                    this.T.movingItem      = h.userData.item;
                    this.T.moveStartClient = { x: e.clientX, y: e.clientY };
                    this.T.moveLastX       = h.userData.item.x_position_in;
                    this.T.moveCommitted   = false;
                    e.preventDefault();
                    e.stopPropagation();
                }
            }
        }

        // Wall pick — lowest priority, only when no handle or cabinet was hit.
        // Clicking a room wall makes it the active wall (Phase 1).
        if (!hits.length) {
            const wall = this._raycastWall(raycaster);
            if (wall && wall !== this.props.activeWall && this.props.onWallSelect) {
                this.props.onWallSelect(wall);
            }
        }
    }

    _raycastWall(raycaster) {
        const meshes = Object.values(this.T.wallMeshes).filter(Boolean);
        if (!meshes.length) return null;
        const hit = raycaster.intersectObjects(meshes, false);
        return hit.length ? (hit[0].object.userData && hit[0].object.userData.wall) || null : null;
    }

    _onMouseMove(e) {
        if (this.T.dragging) {
            const nw = resolveRoomWidthFromDrag(e.clientX, this.T.dragX0, this.T.dragW0);
            if (nw !== this._lastResizeWidth) {
                this._lastResizeWidth = nw;
                if (this.props.onResizeRoom) this.props.onResizeRoom(nw, /*inFlight=*/true);
            }
            return;
        }

        if (this.T.draggingDepth) {
            const nd = resolveRoomDepthFromDrag(e.clientY, this.T.dragY0, this.T.dragD0);
            if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
                // eslint-disable-next-line no-console
                console.debug("[sbk-depth] move", { clientY: e.clientY, dragY0: this.T.dragY0, dragD0: this.T.dragD0, nd });
            }
            if (nd !== this._lastResizeDepth) {
                this._lastResizeDepth = nd;
                if (this.props.onResizeRoomDepth) this.props.onResizeRoomDepth(nd, /*inFlight=*/true);
            }
            return;
        }

        if (this.T.movingItem) {
            if (!this.T.moveCommitted &&
                !isCabinetDragCommitted(this.T.moveStartClient, e)) return;
            if (!this.T.moveCommitted) {
                this.T.moveCommitted = true;
                const cvs = this.T.renderer && this.T.renderer.domElement;
                if (cvs) cvs.style.cursor = "move";
                // Task 35 — the ONE point in this gesture where
                // `this.T.movingItem` is still fully pristine: the
                // very next lines below are what first mutate its
                // x_position_in in place. Fired exactly once per drag
                // gesture (guarded by the moveCommitted flip above,
                // same as the cursor change next to it), never on a
                // plain click that never crosses the drag threshold.
                if (this.props.onMoveItemStart) {
                    this.props.onMoveItemStart(this.T.movingItem);
                }
            }
            const targetX = this._computeDropX(e);
            if (targetX == null) return;
            if (this.T.movingItem.x_position_in === targetX) return;
            this.T.movingItem.x_position_in = targetX;
            this.T.moveLastX = targetX;
            this._buildScene(this.props);
        }
    }

    _onMouseUp() {
        if (this.T.dragging) {
            this.T.dragging = false;
            const finalNw = this._lastResizeWidth ??
                ((this.props.room && this.props.room.width_in) || 0);
            this._lastResizeWidth = null;
            if (this.props.onResizeRoom) this.props.onResizeRoom(finalNw, /*inFlight=*/false);
        }
        if (this.T.draggingDepth) {
            this.T.draggingDepth = false;
            const finalNd = this._lastResizeDepth ??
                ((this.props.room && this.props.room.depth_in) || 0);
            this._lastResizeDepth = null;
            if (typeof window !== "undefined" && window.__SBK_DEPTH_DEBUG__) {
                // eslint-disable-next-line no-console
                console.debug("[sbk-depth] up commit", { finalNd });
            }
            if (this.props.onResizeRoomDepth) this.props.onResizeRoomDepth(finalNd, /*inFlight=*/false);
        }
        if (this.T.movingItem) {
            const item = this.T.movingItem;
            const wasCommitted = this.T.moveCommitted;
            this.T.movingItem    = null;
            this.T.moveCommitted = false;
            const cvs = this.T.renderer && this.T.renderer.domElement;
            if (cvs) cvs.style.cursor = "";
            if (wasCommitted && this.props.onMoveItem) {
                this.props.onMoveItem({
                    item,
                    x_position_in: item.x_position_in,
                    pinnable:      isPinnable(item.cabinet_type),
                });
            }
        }
    }

    _onMouseOver(e) {
        const mount = this.canvasRef.el;
        if (!mount || !this.T.activeCamera || !this.T.raycaster) return;
        if (this.T.movingItem && this.T.moveCommitted) return;
        this.T.raycaster.setFromCamera(this._ndcFromEvent(e), this.T.activeCamera);
        const handleActive = isHandleActiveView(this._currentView);
        const onHandle = handleActive && this.T.handleMesh &&
            this.T.raycaster.intersectObjects([this.T.handleMesh, ...this.T.arrowMeshes], false).length > 0;
        const onDepthHandle = !onHandle && handleActive && this.T.depthHandleMesh &&
            this.T.raycaster.intersectObjects([this.T.depthHandleMesh, ...this.T.depthArrowMeshes], false).length > 0;
        const onCabinet = !onHandle && !onDepthHandle &&
            this.T.raycaster.intersectObjects(this.T.clickable, false).length > 0;

        // Wall hover — soft-glow the wall under the cursor (only when not over
        // a handle or cabinet). Repaint only when the hovered wall changes.
        const wall = (!onHandle && !onDepthHandle && !onCabinet)
            ? this._raycastWall(this.T.raycaster) : null;
        if (wall !== this.T.hoverWall) {
            this.T.hoverWall = wall;
            this._applyWallHighlight();
        }

        const cvs = this.T.renderer?.domElement;
        if (cvs) {
            const cur = cursorForPointerState({
                dragging: this.T.dragging, draggingDepth: this.T.draggingDepth,
                onHandle, onDepthHandle, onCabinet,
            });
            // A hoverable wall gets the pointer cursor when nothing else claims it.
            cvs.style.cursor = (cur === "default" && wall) ? "pointer" : cur;
        }
    }

    // ═══════════════════════════════════════════════════════════════════
    // Touch editing Wave 2 — pointer pipeline
    // ═══════════════════════════════════════════════════════════════════
    // Registered at CAPTURE phase on `mount` (see `_syncTouchListeners`),
    // gated behind `props.touchEnabled` at every entry point below (kill-
    // switch discipline — every handler ALSO early-returns even if a
    // stray call ever reached it while disabled, belt-and-suspenders on
    // top of not being registered at all). Feeds `touch_gestures.esm.js`'s
    // `createGestureReducer` verbatim per its own event contract; consumes
    // `state.effects` via `_applyGestureEffects`/`describeGestureEffect`.
    // Mouse's own `_onMouseDown/_onMouseMove/_onMouseUp/_onMouseOver`
    // above are completely untouched by any of this.

    _onPointerDown(e) {
        if (!this.props.touchEnabled) return;
        const prePhase = this._gestureState.phase;
        // Only raycast (and only allow the F7-compliant wall-select side
        // channel) for the FIRST concurrent pointer — see
        // `_computeTouchHit`'s docblock. The reducer's own 2nd-pointer
        // preemption never reads `hit` at all (touch_gestures.esm.js's
        // `handlePointerDown`), so this is a pure efficiency/correctness
        // guard, not a behavior gap.
        const isFirstPointer = this._gestureState.pointers.length === 0;
        const hit = isFirstPointer ? this._computeTouchHit(e) : { kind: "empty" };
        const { postPhase } = this._dispatchGesture({
            type: "pointerdown", pointerId: e.pointerId, x: e.clientX, y: e.clientY,
            pointerType: e.pointerType, hit,
        });

        // setPointerCapture on `mount` regardless of claim decision (brief
        // item 1) — keeps OUR future move/up events targeted here even if
        // the physical touch drifts outside `mount`'s bounds. Harmless to
        // OrbitControls: if this resolves to orbit, ITS OWN onPointerDown
        // (which still fires — see shouldClaimPointerEvent's docblock)
        // reassigns capture to `renderer.domElement` on its own.
        const mount = this.canvasRef.el;
        if (mount && typeof mount.setPointerCapture === "function") {
            try { mount.setPointerCapture(e.pointerId); } catch (_err) { /* not capturable */ }
        }

        if (shouldClaimPointerEvent("pointerdown", prePhase, postPhase)) {
            e.preventDefault();
            e.stopPropagation();
        }

        // Long-press timer — host-owned (the reducer never reads a
        // clock itself; see touch_gestures.esm.js's own handleTimerFired
        // docblock). Armed ONLY for a pending tap on the already-
        // selected cabinet — mirrors the reducer's own "long-press
        // pickup fires ONLY on the currently-selected cabinet" rule, so
        // no timer is ever scheduled for a gesture that could not
        // possibly consume it.
        this._clearLongPressTimer();
        if (postPhase === GESTURE_PHASES.PENDING_TAP && hit.kind === "cabinet" && hit.selected) {
            const pointerId = e.pointerId;
            const ms = this._gestureReducer.config.longPressMs;
            this._longPressTimer = setTimeout(() => {
                this._longPressTimer = null;
                this._dispatchGesture({ type: "timerFired", pointerId });
            }, ms);
        }
    }

    _onPointerMove(e) {
        if (!this.props.touchEnabled) return;
        const { prePhase, postPhase } = this._dispatchGesture({
            type: "pointermove", pointerId: e.pointerId, x: e.clientX, y: e.clientY,
            pointerType: e.pointerType,
        });
        // Any move that resolves OUT of pendingTap (drag/orbit/camera)
        // invalidates a still-pending long-press timer for that pointer
        // — touch_gestures.esm.js's own header: "cleared on that
        // pointer's up/cancel/slop-exceeded move."
        if (postPhase !== GESTURE_PHASES.PENDING_TAP) this._clearLongPressTimer();
        if (shouldClaimPointerEvent("pointermove", prePhase, postPhase)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    _onPointerUp(e) {
        if (!this.props.touchEnabled) return;
        this._clearLongPressTimer();
        const { prePhase, postPhase } = this._dispatchGesture({
            type: "pointerup", pointerId: e.pointerId, x: e.clientX, y: e.clientY,
            pointerType: e.pointerType,
        });
        if (shouldClaimPointerEvent("pointerup", prePhase, postPhase)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    _onPointerCancel(e) {
        if (!this.props.touchEnabled) return;
        this._clearLongPressTimer();
        const { prePhase, postPhase } = this._dispatchGesture({
            type: "pointercancel", pointerId: e.pointerId, pointerType: e.pointerType,
        });
        if (shouldClaimPointerEvent("pointercancel", prePhase, postPhase)) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    _clearLongPressTimer() {
        if (this._longPressTimer) {
            clearTimeout(this._longPressTimer);
            this._longPressTimer = null;
        }
    }

    // Feed one event through the T1 reducer and immediately consume the
    // resulting effects. Returns {prePhase, postPhase} so callers can
    // make the OrbitControls-arbitration propagation decision without
    // re-deriving phase bookkeeping themselves. This is the ONE seam
    // every pointer handler AND the long-press timer callback share.
    _dispatchGesture(event) {
        const prePhase = this._gestureState.phase;
        const next = this._gestureReducer(this._gestureState, event);
        this._gestureState = next;
        this._applyGestureEffects(next.effects);
        return { prePhase, postPhase: next.phase };
    }

    // Build the reducer's `hit` field for a pointerdown (constraints
    // doc §1: touch should raycast synchronously inline, exactly like
    // the existing mouse pipeline). Shares `T.clickable` with mouse
    // (including any touch proxy meshes — see `_addTouchProxyMesh`),
    // so this is F8/F14-safe by construction: every hit resolves to
    // the SAME live `userData.item` the mouse pipeline already trusts,
    // never a parallel reference.
    //
    // F7 — when nothing clickable is hit, this ALSO runs the exact
    // select-scoped wall raycast (`_raycastWall`, all four walls) mouse
    // click already uses for its lowest-priority wall-pick branch —
    // never the drop-scoped `_raycastDropWall` (FULL_HEIGHT_WALLS-
    // filtered), since a tap is a SELECT context, not drop-target
    // inference. This is a side channel independent of the reducer's
    // own `{kind:'empty'}` resolution (which soft-locks — spec §1
    // decision 2 — never deselecting on an empty tap).
    _computeTouchHit(e) {
        const { activeCamera, raycaster, clickable } = this.T;
        if (!activeCamera || !raycaster) return { kind: "empty" };
        raycaster.setFromCamera(this._ndcFromEvent(e), activeCamera);
        const hits = raycaster.intersectObjects(clickable, false);
        if (hits.length) {
            const item = hits[0].object.userData && hits[0].object.userData.item;
            if (item && item.layout_key) {
                const selId = this.props.selected && this.props.selected.layout_key;
                return { kind: "cabinet", id: item.layout_key, selected: item.layout_key === selId };
            }
        }
        const wall = this._raycastWall(raycaster);
        if (wall && wall !== this.props.activeWall && this.props.onWallSelect) {
            this.props.onWallSelect(wall);
        }
        return { kind: "empty" };
    }

    _findItemByLayoutKey(id) {
        return (this.props.items || []).find((it) => it.layout_key === id) || null;
    }

    // Consume T1's effect batch (see describeGestureEffect's own
    // docblock for the effect->kind mapping this switches on).
    _applyGestureEffects(effects) {
        for (const eff of effects || []) {
            const desc = describeGestureEffect(eff);
            switch (desc.kind) {
                case "select": {
                    // F14 — the ONLY selection-mutating call this
                    // pipeline ever makes is `onSelectItem`, the exact
                    // same callback mouse click-select uses; never
                    // `catalogPreview`.
                    const item = this._findItemByLayoutKey(desc.id);
                    if (item && this.props.onSelectItem) this.props.onSelectItem(item);
                    break;
                }
                case "affordance": {
                    if (this.props.onGestureAffordance) {
                        this.props.onGestureAffordance(desc.action, this.props.selected);
                    }
                    break;
                }
                case "dragStart": {
                    const item = this._findItemByLayoutKey(desc.id);
                    if (!item) break;
                    this._activeDragItem = item;
                    this._dragSnapshot = {
                        layout_key: item.layout_key,
                        x_position_in: item.x_position_in,
                        z_position_in: item.z_position_in,
                    };
                    // Constraints doc: "reuse `_rotateSelected(deltaDeg)`
                    // ... reuse `onMoveItemStart` verbatim" — this IS
                    // that exact reuse: the moment `item` is still fully
                    // pristine, same as mouse's own drag-commit moment.
                    if (this.props.onMoveItemStart) this.props.onMoveItemStart(item);
                    // Second, independent safety net against OrbitControls
                    // — see shouldClaimPointerEvent's docblock.
                    this._orbitWasEnabled = this.T.orbit ? this.T.orbit.enabled : null;
                    if (this.T.orbit) this.T.orbit.enabled = false;
                    this._showTouchDragGhost(item);
                    break;
                }
                case "dragMove":
                    this._applyDragTo(desc);
                    break;
                case "dragCommit":
                    this._commitTouchDrag(desc);
                    break;
                case "dragCancel":
                    this._cancelTouchDrag(desc);
                    // Seam fix (2026-08-02, coordinator) — the v1 health
                    // counters had NO producer: the parents'
                    // _recordTouchEvent and the server's touch_stats both
                    // existed, but nothing here ever emitted. A cancel
                    // whose mode is "ghost" means the user picked up and
                    // bailed (abandon); any other cancelled drag is a
                    // rollback (incl. two-finger camera preemption).
                    // Optional-chained: parents that don't wire the prop
                    // lose telemetry, never crash.
                    if (desc.mode === "ghost") {
                        this.props.onGestureAbandon?.();
                    } else {
                        this.props.onGestureRollback?.();
                    }
                    break;
                case "nativeCamera":
                case "restoreCamera":
                case "noop":
                default:
                    break; // native camera handles itself; nothing to do here
            }
        }
    }

    // Brief item 3 — constrained 1-DOF along-wall drag, WITH the Z-axis
    // branch for left/right walls (the mouse path's own back-wall-only
    // gap — constraints doc §2 — fixed here for touch, mouse untouched).
    // Mutates the LIVE `userData.item` in place (F8) — `item` came from
    // `_findItemByLayoutKey(this.props.items)`, the SAME array
    // `_placeCabinetGroup` already bound identity against.
    _applyDragTo(desc) {
        const item = this._activeDragItem;
        if (!item || item.layout_key !== desc.id) return;
        const { activeCamera, raycaster, THREE } = this.T;
        if (!activeCamera || !raycaster || !THREE) return;
        raycaster.setFromCamera(this._ndcFromEvent({ clientX: desc.x, clientY: desc.y }), activeCamera);
        const wall = item.wall || WALLS.BACK;
        const proj = projectDragAlongWall(THREE, raycaster, wall, 1);
        // No-hit (side-view ray parallel to the floor plane, etc.) is a
        // silent no-op — mirrors the existing mouse path's own
        // `if (targetX == null) return;` in `_onMouseMove`.
        if (!proj) return;

        const room = this.props.room || {};
        const maxIn = Math.max(0, proj.axis === "z" ? (room.depth_in || 0) : (room.width_in || 0));
        const clamped = Math.max(0, Math.min(maxIn, proj.value));
        const prevIn = proj.axis === "z" ? (item.z_position_in || 0) : (item.x_position_in || 0);
        const prevSnap = Math.round(prevIn / 6) * 6;

        if (proj.axis === "z") item.z_position_in = clamped;
        else item.x_position_in = clamped;

        const colliding = !!this.props.dragCollision ||
            hasLocalRunCollision(this.props.items, item, proj.axis, clamped);
        const newSnap = Math.round(clamped / 6) * 6;

        this._buildScene(this.props); // matches mouse's own per-tick rebuild
        this._updateTouchDragGhostPose(item);
        this._updateTouchDragGhostTint(colliding);
        if (newSnap !== prevSnap) this._fireSnap();
    }

    _commitTouchDrag(desc) {
        const item = this._activeDragItem;
        this._hideTouchDragGhost();
        if (this.T.orbit && this._orbitWasEnabled != null) this.T.orbit.enabled = this._orbitWasEnabled;
        this._orbitWasEnabled = null;
        if (item && item.layout_key === desc.id && this.props.onMoveItem) {
            // Reuses the EXISTING callback + the EXISTING two-call
            // persist contract downstream of it (`_savePinnedPosition`
            // + `_queueAutoSave`, constraints doc §2) verbatim.
            // `z_position_in` is additive — see the class docblock.
            this.props.onMoveItem({
                item,
                x_position_in: item.x_position_in,
                z_position_in: item.z_position_in,
                pinnable: isPinnable(item.cabinet_type),
            });
        }
        this._activeDragItem = null;
        this._dragSnapshot = null;
    }

    // Rollback to last-known-good (spec: "pointercancel -> roll back any
    // ghost transform"). No parent callback here — nothing was ever
    // persisted for an uncommitted drag, so there is nothing to undo
    // server-side; this is purely a local, client-state revert.
    _cancelTouchDrag(desc) {
        const item = this._activeDragItem;
        this._hideTouchDragGhost();
        if (this.T.orbit && this._orbitWasEnabled != null) this.T.orbit.enabled = this._orbitWasEnabled;
        this._orbitWasEnabled = null;
        if (item && item.layout_key === desc.id && this._dragSnapshot) {
            // ROLLBACK_MS (manipulation_states.esm.js) is reserved for a
            // future eased tween; v1 snaps back instantly — correctness
            // (the position IS restored) lands now, matching this
            // file's own established "visual polish deferred, the data
            // is right" precedent (see `_rotateSelected`'s deferred-
            // mesh-rotation comment in kitchen_configurator.js).
            item.x_position_in = this._dragSnapshot.x_position_in;
            item.z_position_in = this._dragSnapshot.z_position_in;
            this._buildScene(this.props);
        }
        this._activeDragItem = null;
        this._dragSnapshot = null;
    }

    // ── Drag ghost + origin wireframe (manipulation-visual-spec state 4) ──

    _showTouchDragGhost(item) {
        this._hideTouchDragGhost();
        const THREE = this.T.THREE, scene = this.T.scene;
        if (!THREE || !scene || !item) return;
        const grp = (this.T.cabObjs || []).find(
            g => g && g.userData && g.userData.item && g.userData.item.layout_key === item.layout_key);
        const w = (item.width_in || 24) * IN, h = (item.height_in || 34.5) * IN, d = (item.depth_in || 24) * IN;
        const geo = new THREE.BoxGeometry(w, h, d);

        const ghost = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
            color: P.sel, transparent: true, opacity: GHOST_OPACITY, depthWrite: false,
        }));
        ghost.renderOrder = 999;
        scene.add(ghost);
        this.T.touchGhostMesh = ghost;

        if (grp) {
            // Origin wireframe — snapshot of the PRE-drag footprint,
            // positioned once here and never moved again (spec §3:
            // "faint wireframe left at origin").
            const origin = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                new THREE.LineBasicMaterial({
                    color: ORIGIN_WIREFRAME, transparent: true, opacity: ORIGIN_OPACITY,
                }));
            this._positionGhostLike(origin, grp, w, h, d);
            scene.add(origin);
            this.T.touchOriginMesh = origin;
        }
        this._updateTouchDragGhostPose(item);
    }

    // Shared centre-of-footprint placement math for the ghost/origin
    // meshes: the group's own origin is the item's CORNER (local-frame
    // convention every builder uses — see `_placeCabinetGroup`), so the
    // mesh centre is the group's world position plus a half-footprint
    // offset rotated by the group's own Y rotation.
    _positionGhostLike(mesh, grp, wScene, hScene, dScene) {
        const THREE = this.T.THREE;
        const local = new THREE.Vector3(wScene / 2, hScene / 2, dScene / 2);
        local.applyAxisAngle(new THREE.Vector3(0, 1, 0), grp.rotation.y);
        mesh.position.set(
            grp.position.x + local.x, grp.position.y + local.y, grp.position.z + local.z);
        mesh.rotation.y = grp.rotation.y;
    }

    _updateTouchDragGhostPose(item) {
        if (!this.T.touchGhostMesh || !item) return;
        const grp = (this.T.cabObjs || []).find(
            g => g && g.userData && g.userData.item && g.userData.item.layout_key === item.layout_key);
        if (!grp) return;
        const w = (item.width_in || 24) * IN, h = (item.height_in || 34.5) * IN, d = (item.depth_in || 24) * IN;
        this._positionGhostLike(this.T.touchGhostMesh, grp, w, h, d);
    }

    // manipulation-visual-spec state 6 (Invalid/collision) — swap the
    // ghost between shipped blue and COLLISION_RED. `P.collision` and
    // manipulation_states.esm.js's `COLLISION_RED` are the SAME literal
    // (0xA6281E) by construction — see constants.esm.js's own comment;
    // imported from manipulation_states.esm.js here since that's this
    // file's one dependency on the Wave-1 module for this constant.
    _updateTouchDragGhostTint(isColliding) {
        if (!this.T.touchGhostMesh) return;
        this.T.touchGhostMesh.material.color.setHex(isColliding ? COLLISION_RED : P.sel);
    }

    _hideTouchDragGhost() {
        const T = this.T;
        if (T.touchGhostMesh) {
            T.scene && T.scene.remove(T.touchGhostMesh);
            T.touchGhostMesh.geometry && T.touchGhostMesh.geometry.dispose();
            T.touchGhostMesh.material && T.touchGhostMesh.material.dispose();
            T.touchGhostMesh = null;
        }
        if (T.touchOriginMesh) {
            T.scene && T.scene.remove(T.touchOriginMesh);
            T.touchOriginMesh.geometry && T.touchOriginMesh.geometry.dispose();
            T.touchOriginMesh.material && T.touchOriginMesh.material.dispose();
            T.touchOriginMesh = null;
        }
    }

    // State 7 (Snap) — haptic + a brief ghost-opacity pulse (see the
    // render-loop consumer in `onMounted`'s `tick()`). Fired whenever a
    // drag tick crosses into a new 6" grid cell (the same grid the
    // mouse path/`computeDropXIn` already snap to elsewhere).
    _fireSnap() {
        hapticTick();
        this._snapPulseStart = nowMs();
    }

    // ── Selected lift + base pad (manipulation-visual-spec state 2) ──────

    _applySelectedLift(item) {
        this._clearSelectedLift();
        if (!item) return;
        const THREE = this.T.THREE, scene = this.T.scene;
        if (!THREE || !scene) return;
        const grp = (this.T.cabObjs || []).find(
            g => g && g.userData && g.userData.item && g.userData.item.layout_key === item.layout_key);
        if (!grp) return;
        this.T.liftedGroup = grp;
        this.T.liftedOriginalY = grp.position.y;
        grp.position.y = this.T.liftedOriginalY + LIFT_IN * IN;

        const w = (item.width_in || 24) * IN, d = (item.depth_in || 24) * IN;
        const pad = new THREE.Mesh(
            new THREE.BoxGeometry(w * 1.08, 0.01, d * 1.08),
            new THREE.MeshBasicMaterial({
                color: P.sel, transparent: true, opacity: BASE_PAD_OPACITY, depthWrite: false,
            }));
        const local = new THREE.Vector3(w / 2, 0, d / 2);
        local.applyAxisAngle(new THREE.Vector3(0, 1, 0), grp.rotation.y);
        pad.position.set(grp.position.x + local.x, 0.005, grp.position.z + local.z);
        pad.rotation.y = grp.rotation.y;
        scene.add(pad);
        this.T.basePadMesh = pad;
    }

    _clearSelectedLift() {
        if (this.T.liftedGroup) {
            this.T.liftedGroup.position.y = this.T.liftedOriginalY || 0;
            this.T.liftedGroup = null;
            this.T.liftedOriginalY = 0;
        }
        if (this.T.basePadMesh) {
            this.T.scene && this.T.scene.remove(this.T.basePadMesh);
            this.T.basePadMesh.geometry && this.T.basePadMesh.geometry.dispose();
            this.T.basePadMesh.material && this.T.basePadMesh.material.dispose();
            this.T.basePadMesh = null;
        }
    }

    // ── Others dimmed (manipulation-visual-spec state 3) ──────────────────
    // Same tint-clone/restore-by-reference pattern as
    // `applySelectionVisuals`/`clearSelectionVisuals` (selection.esm.js),
    // reimplemented here (not imported — that module is out of this
    // task's edit scope) rather than forked math: delegates the actual
    // blend to `dimColor` (manipulation_states.esm.js), same as that
    // module's own docblock instructs.

    _applyDimToOthers(selectedItem) {
        this._clearDimVisuals();
        if (!selectedItem) return;
        for (const grp of this.T.cabObjs || []) {
            if (!grp || !grp.userData || !grp.userData.item) continue;
            if (grp.userData.item.layout_key === selectedItem.layout_key) continue;
            grp.traverse((obj) => {
                if (!(obj.userData && obj.userData.cab)) return;
                const original = obj.material;
                if (!original || !original.color) return;
                const clone = original.clone();
                clone.color.setHex(dimColor(original.color.getHex(), P.scene));
                obj.material = clone;
                this.T.dimmed.set(obj, original);
            });
        }
    }

    _clearDimVisuals() {
        const dimmed = this.T.dimmed;
        if (!dimmed) return;
        for (const [mesh, original] of dimmed) {
            if (mesh.material && mesh.material !== original && typeof mesh.material.dispose === "function") {
                mesh.material.dispose();
            }
            mesh.material = original;
        }
        dimmed.clear();
    }

    // ── Camera-to-elevation (brief item 5) ────────────────────────────────
    // Exposed via `props.onReady`'s `api.cameraToElevation(wall)` — the
    // touch pad's camera-icon button calls this directly. Zero data
    // mutation, zero server round-trip (wallrun-rotation-review.md #3's
    // own effort rating: "S ... this is the single safest of the four
    // candidates since it never writes anything").
    _cameraToElevation(wall) {
        const key = resolveElevationViewForWall(wall);
        this._setView(key);
        return key;
    }
}
