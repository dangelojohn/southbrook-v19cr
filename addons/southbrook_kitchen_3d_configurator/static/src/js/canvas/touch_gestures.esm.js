/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — touch gesture arbitration.
 *
 * Touch editing spec, Wave 1 (`docs/superpowers/specs/2026-08-02-touch-
 * editing-design.md` §2 "Gesture grammar") — the pure `(state, event) ->
 * state` reducer the spec calls for. This module owns ONLY the
 * arbitration decision (which of: select / direct-drag / long-press
 * pickup+ghost-drag / camera-orbit / two-finger-camera is in play, and
 * when it starts/continues/commits/cancels). It never touches the DOM,
 * `THREE`, `OrbitControls`, raycasting, or timers — those are host
 * concerns. This keeps the whole safety-critical arbitration policy
 * unit-testable with plain objects (`node:test`, zero jsdom/WebGL), per
 * the research recipe's own testability note (`touch-engineering-
 * research.md` §4): "Doing hit-testing *inside* the reducer... makes
 * the machine impossible to unit test... always pass the hit-test
 * result in as part of the event."
 *
 * Wave 2 (integration into `kitchen_canvas.esm.js`) owns:
 *   - Registering `pointerdown` at CAPTURE phase on `mount` (not
 *     `mousedown` — see constraints doc §8 risk #1: OrbitControls
 *     listens for `pointerdown` on `renderer.domElement`, a child of
 *     `mount`; only a same-event-type, capture-phase listener can win).
 *   - Raycasting on pointerdown to build the `hit` field this reducer
 *     consumes, and re-raycasting per rAF tick during a drag to turn a
 *     `dragTo` effect into an actual constrained-drag mutation.
 *   - Scheduling the `timerFired` event via a single `setTimeout` per
 *     pointer, cleared on that pointer's up/cancel/slop-exceeded move
 *     (a *late* `timerFired` — e.g. one whose clearTimeout raced a
 *     `pointerup` — is still handled safely: see "Stale-event safety"
 *     below).
 *   - Consuming `state.effects` after every `reduce()` call and doing
 *     the actual `_selectCabinet` / `_savePinnedPosition` /
 *     `_queueAutoSave` / OrbitControls-enable / ghost-mesh work.
 *
 * ─── Event contract ─────────────────────────────────────────────────────
 *
 * Every event is a plain object with a `type` field. Coordinates are
 * CSS pixels (matching native Pointer Events — no DPR correction
 * needed, per the research recipe §1).
 *
 *   { type: 'pointerdown', pointerId, x, y, pointerType, hit }
 *   { type: 'pointermove', pointerId, x, y, pointerType }
 *   { type: 'pointerup',   pointerId, x, y, pointerType }
 *   { type: 'pointercancel', pointerId, pointerType }
 *   { type: 'timerFired', pointerId }
 *   { type: 'hitResult',  pointerId, hit }
 *
 * `hit` (required on `pointerdown`; the host's raycast result) is one of:
 *
 *   { kind: 'cabinet',    id, selected: boolean }
 *   { kind: 'empty' }
 *   { kind: 'affordance', action }   // e.g. 'flipHand' | 'cameraToElevation'
 *                                    // | 'sendToWall' — spec §1 decision 3's
 *                                    // scoped, honest-glyph affordances
 *
 * `hit.selected` is the HOST's ground truth for "is this the current
 * selection" (the host already carries `state.selected`/F14's split —
 * see constraints doc §2). This reducer trusts it verbatim for gating
 * decisions and keeps its own `selectedId` only for effect bookkeeping
 * (which id to commit/cancel, and the idle/selectedIdle phase split).
 * If a host wires MULTIPLE selection-mutating paths (catalog cycling,
 * keyboard, etc.) it must keep `hit.selected` truthful at raycast time;
 * this reducer does not — and structurally cannot — reconcile a stale
 * external selection on its own.
 *
 * `pointerType` is accepted for contract completeness but this reducer
 * never reads it. Per the research recipe (§1): "use `pointerType` only
 * to tweak constants..., never to fork logic paths." Any per-pointerType
 * constant tuning (e.g. a smaller slop for mouse) belongs in the HOST
 * choosing a different `surfaceConfig` — see `createGestureReducer`.
 *
 * `hitResult` is an optional, defensive path for a host whose raycast
 * completes asynchronously/after `pointerdown` (the default, expected
 * flow is that `pointerdown.hit` is already populated — the existing
 * mouse pipeline raycasts synchronously inline, see constraints doc §1,
 * and touch should do the same). A `hitResult` only takes effect while
 * still `pendingTap` for the matching `pointerId`; otherwise it's a
 * no-op.
 *
 * ─── Stale-event safety ─────────────────────────────────────────────────
 *
 * Every handler below re-checks `event.pointerId` against the tracked
 * pointer(s) and the current `phase` before acting. A `timerFired` that
 * arrives after its pointer already lifted/cancelled/exceeded slop is a
 * pure no-op (the phase will no longer be `pendingTap` for that
 * pointer), matching the research recipe's warning about ambient
 * `setTimeout` leaks (§4 pitfalls) — the *reducer* can't leak a timer
 * (it doesn't own one), and it can't misfire on a late one either.
 *
 * ─── effects ─────────────────────────────────────────────────────────────
 *
 * `state.effects` is the batch of declarative actions produced by the
 * MOST RECENT `reduce()` call only (never accumulated). The host drains
 * it after every call. Effect shapes:
 *
 *   { type: 'select', id }
 *     Emitted on tap-up over a cabinet whose id differs from the
 *     current selection (soft lock engage / reselect). Never emitted
 *     for a no-op tap on the already-selected cabinet.
 *   { type: 'affordance', action }
 *     Emitted on tap-up over a scoped affordance glyph (spec §1
 *     decision 3). Selection is unchanged.
 *   { type: 'orbit' }
 *     "Hand off to the native camera" — one finger dragging from empty
 *     space, from an unselected cabinet (selection-gated), or off an
 *     affordance glyph. The host does not preventDefault/stopPropagate
 *     this gesture; OrbitControls (already listening natively) drives
 *     the camera. Emitted once, on the transition into `cameraOrbit`.
 *   { type: 'cameraGesture' }
 *     "Hand off to the native camera" for a 2(+)-finger gesture — ALWAYS
 *     wins, per spec §2, regardless of what was happening a moment ago.
 *     Emitted once, on the transition into `cameraGesture`.
 *   { type: 'endCameraGesture' }
 *     Emitted when pointer count drops back below 2 and the machine
 *     returns to idle/selectedIdle.
 *   { type: 'beginDrag', id }
 *     Direct constrained-drag started on the selected cabinet (1-finger
 *     move beyond slop, before long-press fires).
 *   { type: 'beginPickup', id }
 *     Long-press-pickup fired on the selected cabinet (still within
 *     slop at 500ms). This is the spec's "longPressPickup" transition —
 *     it names the momentary event, not a distinct resting phase; the
 *     resulting resting phase is `ghostDrag` (mirrors the research
 *     recipe's own "PickedUp(H) (ghost-follow mode)").
 *   { type: 'dragTo', id, x, y, mode }
 *     Continuous position update while `dragging` or `ghostDrag`.
 *     `mode` is `'direct'` or `'ghost'` so the host can render the
 *     shipped-blue direct drag vs. the ghost-preview ("Drag ghost" in
 *     spec §3) differently, while both route through the SAME
 *     downstream mutator per the research recipe §3's Model A/Model B
 *     convergence contract — that mutator lives in the host, not here.
 *   { type: 'commit', id, x, y, mode }
 *     Drag/ghost-drag finished on pointerup. Host performs the
 *     optimistic-then-server-reconciled commit (constraints doc: two-
 *     call `_savePinnedPosition` + `_queueAutoSave` pattern).
 *   { type: 'cancelDrag', id, mode }
 *     Drag/ghost-drag aborted — either `pointercancel` (OS gesture
 *     interception, etc.) or a 2nd pointer preempting into
 *     `cameraGesture` mid-manipulation. Host rolls back the ghost/
 *     optimistic transform to last-known-good.
 *
 * ─── State shape ─────────────────────────────────────────────────────────
 *
 * Treat every field as reducer-internal/opaque EXCEPT `phase`,
 * `selectedId`, and `effects`, which are the stable, intended-for-host
 * surface. `phase` is one of the seven `GESTURE_PHASES` values below.
 */

/** @type {Readonly<Record<string,string>>} The seven reachable phases. */
export const GESTURE_PHASES = Object.freeze({
    IDLE: "idle",
    SELECTED_IDLE: "selectedIdle",
    PENDING_TAP: "pendingTap",
    CAMERA_ORBIT: "cameraOrbit",
    DRAGGING: "dragging",
    GHOST_DRAG: "ghostDrag",
    CAMERA_GESTURE: "cameraGesture",
});

/**
 * Build a fresh, no-pointers-down, nothing-selected initial state.
 * `createGestureReducer`'s returned function also accepts `null`/
 * `undefined` as its `state` argument and will call this internally —
 * callers may use whichever is more convenient for their bootstrap.
 *
 * @returns {object} a new initial gesture state
 */
export function createInitialGestureState() {
    return {
        phase: GESTURE_PHASES.IDLE,
        selectedId: null,
        pointers: [],
        primaryPointerId: null,
        down: null,
        hit: null,
        activeId: null,
        mode: null,
        effects: [],
    };
}

/**
 * Euclidean pointer travel since pointerdown. Exported because it's a
 * genuinely pure, independently-useful geometric helper (matches the
 * existing `isCabinetDragCommitted` precedent in `pointer_pipeline.esm.js`)
 * — the reducer body itself calls this identically.
 *
 * @param {{x:number,y:number}} down
 * @param {{x:number,y:number}} current
 * @returns {number}
 */
export function travelDistance(down, current) {
    return Math.hypot(current.x - down.x, current.y - down.y);
}

// ─── resting-state helpers ──────────────────────────────────────────────

function restingState(selectedId, effects) {
    return {
        phase: selectedId != null ? GESTURE_PHASES.SELECTED_IDLE : GESTURE_PHASES.IDLE,
        selectedId: selectedId != null ? selectedId : null,
        pointers: [],
        primaryPointerId: null,
        down: null,
        hit: null,
        activeId: null,
        mode: null,
        effects,
    };
}

function unchanged(state) {
    return { ...state, effects: [] };
}

// ─── pointerdown ─────────────────────────────────────────────────────────
// A 2nd (or 3rd+) concurrent pointer ALWAYS wins into cameraGesture,
// from ANY phase — spec §2: "Two fingers are always camera... never
// object" — regardless of what the first pointer was mid-doing. This is
// checked before anything phase-specific, which is what makes the
// preemption unconditional rather than a per-phase special case.

function handlePointerDown(state, event, config) {
    const { pointerId, x, y, hit } = event;
    if (state.pointers.includes(pointerId)) {
        // Duplicate pointerdown for an already-tracked pointer (host bug
        // or duplicate dispatch) — defensively ignored, not a new gesture.
        return unchanged(state);
    }

    const pointers = [...state.pointers, pointerId];
    if (pointers.length >= 2) {
        const effects = [];
        if (state.phase === GESTURE_PHASES.DRAGGING || state.phase === GESTURE_PHASES.GHOST_DRAG) {
            effects.push({ type: "cancelDrag", id: state.activeId, mode: state.mode });
        }
        if (state.phase !== GESTURE_PHASES.CAMERA_GESTURE) {
            effects.push({ type: "cameraGesture" });
        }
        return {
            phase: GESTURE_PHASES.CAMERA_GESTURE,
            selectedId: state.selectedId,
            pointers,
            primaryPointerId: null,
            down: null,
            hit: null,
            activeId: null,
            mode: null,
            effects,
        };
    }

    // First pointer down from a resting phase (idle/selectedIdle) — open
    // a pending tap. `hit` defaults to 'empty' so a host that forgets to
    // raycast synchronously degrades to orbit-on-move / no-op-on-tap
    // rather than throwing.
    return {
        phase: GESTURE_PHASES.PENDING_TAP,
        selectedId: state.selectedId,
        pointers,
        primaryPointerId: pointerId,
        down: { x, y },
        hit: hit || { kind: "empty" },
        activeId: null,
        mode: null,
        effects: [],
    };
}

// ─── pointermove ─────────────────────────────────────────────────────────

function handlePointerMove(state, event, config) {
    const { pointerId, x, y } = event;

    switch (state.phase) {
        case GESTURE_PHASES.PENDING_TAP: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            const dist = travelDistance(state.down, { x, y });
            if (dist < config.slopPx) {
                return unchanged(state); // micro-tremor — still tap/pickup-eligible
            }
            const hit = state.hit;
            if (hit && hit.kind === "cabinet" && hit.selected === true) {
                // Selection-gated direct drag: only the CURRENT selection drags.
                return {
                    ...state,
                    phase: GESTURE_PHASES.DRAGGING,
                    activeId: hit.id,
                    mode: "direct",
                    effects: [{ type: "beginDrag", id: hit.id }],
                };
            }
            // Empty space, an unselected cabinet, or an affordance glyph:
            // all orbit identically — the selection-gating policy from the
            // research recipe §4 ("drag-from-object only moves it if
            // already selected... otherwise orbits like empty space").
            return {
                ...state,
                phase: GESTURE_PHASES.CAMERA_ORBIT,
                activeId: null,
                mode: null,
                effects: [{ type: "orbit" }],
            };
        }

        case GESTURE_PHASES.DRAGGING:
        case GESTURE_PHASES.GHOST_DRAG: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            return {
                ...state,
                effects: [{ type: "dragTo", id: state.activeId, x, y, mode: state.mode }],
            };
        }

        case GESTURE_PHASES.CAMERA_ORBIT:
        case GESTURE_PHASES.CAMERA_GESTURE:
            // Native camera controls (OrbitControls / pinch) own the actual
            // motion once handed off; nothing for this reducer to emit
            // per-move.
            return unchanged(state);

        default: // idle / selectedIdle — no active pointer to move
            return unchanged(state);
    }
}

// ─── pointerup ───────────────────────────────────────────────────────────

function handlePointerUp(state, event) {
    const { pointerId, x, y } = event;

    switch (state.phase) {
        case GESTURE_PHASES.PENDING_TAP: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            const hit = state.hit;
            const effects = [];
            let selectedId = state.selectedId;
            if (hit.kind === "cabinet") {
                if (hit.id !== state.selectedId) {
                    effects.push({ type: "select", id: hit.id });
                }
                selectedId = hit.id;
            } else if (hit.kind === "affordance") {
                effects.push({ type: "affordance", action: hit.action });
            }
            // hit.kind === 'empty': soft lock (spec §1 decision 2) — a tap
            // on empty space is a deliberate no-op, never a deselect.
            return restingState(selectedId, effects);
        }

        case GESTURE_PHASES.DRAGGING:
        case GESTURE_PHASES.GHOST_DRAG: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            return restingState(state.selectedId, [
                { type: "commit", id: state.activeId, x, y, mode: state.mode },
            ]);
        }

        case GESTURE_PHASES.CAMERA_ORBIT: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            return restingState(state.selectedId, []);
        }

        case GESTURE_PHASES.CAMERA_GESTURE:
            return releasePointerFromGesture(state, pointerId);

        default:
            return unchanged(state);
    }
}

// ─── pointercancel ───────────────────────────────────────────────────────
// Spec/research recipe: "any state -(pointercancel)-> Idle, roll back any
// ghost transform." Only dragging/ghostDrag actually have a transform to
// roll back; every other phase just resets with no effect.

function handlePointerCancel(state, event) {
    const { pointerId } = event;

    switch (state.phase) {
        case GESTURE_PHASES.PENDING_TAP:
        case GESTURE_PHASES.CAMERA_ORBIT: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            return restingState(state.selectedId, []);
        }

        case GESTURE_PHASES.DRAGGING:
        case GESTURE_PHASES.GHOST_DRAG: {
            if (pointerId !== state.primaryPointerId) return unchanged(state);
            return restingState(state.selectedId, [
                { type: "cancelDrag", id: state.activeId, mode: state.mode },
            ]);
        }

        case GESTURE_PHASES.CAMERA_GESTURE:
            return releasePointerFromGesture(state, pointerId);

        default:
            return unchanged(state);
    }
}

function releasePointerFromGesture(state, pointerId) {
    const pointers = state.pointers.filter((id) => id !== pointerId);
    if (pointers.length >= 2) {
        return { ...state, pointers, effects: [] };
    }
    return restingState(state.selectedId, [{ type: "endCameraGesture" }]);
}

// ─── timerFired (long-press, 500ms default) ─────────────────────────────
// Time itself is never read in here — the host owns the setTimeout and
// injects this event once it fires (research recipe §4's explicit
// testability guidance). Because ANY pointermove that exceeds slop
// immediately leaves `pendingTap` (see handlePointerMove above), the
// mere fact that `phase === pendingTap` when this arrives already PROVES
// "still within slop" — no separate distance re-check is needed here.

function handleTimerFired(state, event, config) {
    const { pointerId } = event;
    if (state.phase !== GESTURE_PHASES.PENDING_TAP || pointerId !== state.primaryPointerId) {
        return unchanged(state); // stale/foreign timer — safe no-op
    }
    if (!config.longPressEnabled) {
        return unchanged(state);
    }
    const hit = state.hit;
    if (hit && hit.kind === "cabinet" && hit.selected === true) {
        // "longPressPickup" is this transition itself, not a resting
        // phase — the machine lands directly in the durable `ghostDrag`
        // (ghost-follow) phase.
        return {
            ...state,
            phase: GESTURE_PHASES.GHOST_DRAG,
            activeId: hit.id,
            mode: "ghost",
            effects: [{ type: "beginPickup", id: hit.id }],
        };
    }
    // Long-press on empty space / an unselected cabinet / an affordance
    // glyph is deliberately inert per spec §2 ("on the selected cabinet")
    // — the pointer may still resolve to a plain tap on release.
    return unchanged(state);
}

// ─── hitResult (optional late-binding hit info) ─────────────────────────

function handleHitResult(state, event) {
    const { pointerId, hit } = event;
    if (state.phase !== GESTURE_PHASES.PENDING_TAP || pointerId !== state.primaryPointerId || !hit) {
        return unchanged(state);
    }
    return { ...state, hit, effects: [] };
}

/**
 * Build the pure gesture-arbitration reducer for one surface (portal
 * phone/tablet vs. backend tablet get independent instances — spec §2:
 * "parameterized per surface"; constraints doc §5's opposite mobile
 * postures are exactly why two call sites will want two configs).
 *
 * @param {object} [surfaceConfig]
 * @param {number} [surfaceConfig.slopPx=9] CSS-px drag threshold before
 *   a pointerdown+move commits to drag/orbit instead of resolving as a
 *   tap on release. Research recipe §1: ~8-10px for touch.
 * @param {number} [surfaceConfig.longPressMs=500] Informational only —
 *   this reducer never measures time itself (see handleTimerFired's
 *   header comment); it is exposed back via `reducer.config.longPressMs`
 *   purely so the HOST's `setTimeout(..., config.longPressMs)` and this
 *   reducer's behavioral assumptions can never drift apart.
 * @param {boolean} [surfaceConfig.longPressEnabled=true] When false,
 *   every `timerFired` is a no-op — that surface can only manipulate the
 *   selected cabinet via direct drag, never long-press pickup.
 * @returns {(state: object|null|undefined, event: object) => object}
 *   A pure reducer. Call it with `state === null`/`undefined` on the
 *   very first invocation (or seed with {@link createInitialGestureState}
 *   yourself — both are equivalent). The returned function also carries
 *   a frozen `.config` property exposing the resolved (defaults-applied)
 *   configuration for this surface.
 */
export function createGestureReducer(surfaceConfig = {}) {
    const config = Object.freeze({
        slopPx: surfaceConfig.slopPx != null ? surfaceConfig.slopPx : 9,
        longPressMs: surfaceConfig.longPressMs != null ? surfaceConfig.longPressMs : 500,
        longPressEnabled: surfaceConfig.longPressEnabled !== false,
    });

    function reduce(state, event) {
        const current = state || createInitialGestureState();
        if (!event || typeof event.type !== "string") {
            return unchanged(current);
        }
        switch (event.type) {
            case "pointerdown":
                return handlePointerDown(current, event, config);
            case "pointermove":
                return handlePointerMove(current, event, config);
            case "pointerup":
                return handlePointerUp(current, event, config);
            case "pointercancel":
                return handlePointerCancel(current, event, config);
            case "timerFired":
                return handleTimerFired(current, event, config);
            case "hitResult":
                return handleHitResult(current, event, config);
            default:
                return unchanged(current);
        }
    }

    reduce.config = config;
    return reduce;
}
