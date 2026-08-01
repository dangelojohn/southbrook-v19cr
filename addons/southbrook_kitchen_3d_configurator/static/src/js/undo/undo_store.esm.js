/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — undo/redo stack (Task 35).
 *
 * MINIMAL INDUSTRY DEFAULT scope, per the task brief: a plain,
 * client-only, snapshot-based undo/redo of design-mutating operations
 * (add / remove / move / width-resize / swap / auto-arrange) — no
 * operation-diffing, no server-side history, no cross-session
 * persistence. This is deliberately the smallest thing a rep would
 * expect from "undo" in a design tool, not a full command pattern.
 *
 * Pure, DOM/OWL-agnostic reducer, exactly like toast_store.esm.js
 * (SBK-030) — kitchen_configurator.js is the only caller, and owns
 * ALL the "what is a snapshot" and "when do we push one" decisions;
 * this module only owns the stack mechanics, so it's exercisable
 * head­lessly in Node with plain object literals as snapshots (no
 * jsdom, no mounted component required).
 *
 * Contract (task-35 brief):
 *  - `pushSnapshot(snapshot)` records a PRE-OP snapshot — the caller
 *    is responsible for capturing it BEFORE applying the mutation, so
 *    the first `undo()` after an op returns to the exact prior state.
 *  - The undo stack is capped at `maxDepth` (25) snapshots — pushing
 *    past the cap silently drops the OLDEST entry, never refuses the
 *    push.
 *  - Every `pushSnapshot()` call clears the redo stack — a fresh
 *    mutation invalidates whatever "future" an undo had rewound into
 *    (standard undo/redo semantics; the brief: "redo cleared on new
 *    mutation").
 *  - `undo(current)` / `redo(current)` are two-way: the caller passes
 *    its OWN current snapshot so the opposite stack can restore it
 *    later. Both return `null` (and mutate nothing) when their stack
 *    is empty — the caller must not apply a null return.
 *  - `clear()` drops both stacks — the brief: "stack cleared on
 *    design switch/reload".
 */
export const MAX_UNDO_DEPTH = 25;

export class UndoStore {
    /**
     * @param {object} [opts]
     * @param {number} [opts.maxDepth]
     */
    constructor({ maxDepth = MAX_UNDO_DEPTH } = {}) {
        this._maxDepth = maxDepth;
        // Most-recent-last (push/pop from the end) for both stacks.
        this._undo = [];
        this._redo = [];
    }

    get canUndo() { return this._undo.length > 0; }
    get canRedo() { return this._redo.length > 0; }
    get undoCount() { return this._undo.length; }
    get redoCount() { return this._redo.length; }

    /** @param {*} snapshot — opaque to this class; caller's shape. */
    pushSnapshot(snapshot) {
        this._undo.push(snapshot);
        if (this._undo.length > this._maxDepth) {
            this._undo.shift();
        }
        this._redo = [];
    }

    /**
     * @param {*} current — the caller's state AS IT IS RIGHT NOW,
     *   about to be replaced by the popped snapshot; stashed on the
     *   redo stack so a follow-up redo() can restore it.
     * @returns {*|null} the snapshot to apply, or null if there was
     *   nothing to undo (caller must leave its own state untouched).
     */
    undo(current) {
        if (!this._undo.length) return null;
        const snapshot = this._undo.pop();
        this._redo.push(current);
        if (this._redo.length > this._maxDepth) this._redo.shift();
        return snapshot;
    }

    /** Mirror of undo() — see its doc comment. */
    redo(current) {
        if (!this._redo.length) return null;
        const snapshot = this._redo.pop();
        this._undo.push(current);
        if (this._undo.length > this._maxDepth) this._undo.shift();
        return snapshot;
    }

    /** Full reset — design switch/reload per the brief. */
    clear() {
        this._undo = [];
        this._redo = [];
    }
}

/**
 * Task 35 — confirm-on-regenerate decision, extracted as a pure
 * function so it's independently unit-testable (brief: "extract it as
 * a pure function so it's testable").
 *
 * Heuristic (brief, verbatim): confirm ONLY when the design has
 * manual placements the operation would discard — "any line
 * moved/added since last arrange" when the client can track that
 * cheaply (it can — kitchen_configurator.js flips a plain instance
 * flag on every add/remove/move/width-resize/swap and clears it after
 * a successful arrange); otherwise (the flag is unknown — e.g.
 * `undefined`/`null`, never established this session) fall back to
 * "confirm whenever there ARE lines", per the brief's own explicit
 * fallback.
 *
 * @param {number} itemCount — state.items.length at decision time.
 * @param {boolean|null|undefined} dirtySinceArrange — whether a
 *   manual placement op has happened since the last successful
 *   arrange this session; `null`/`undefined` means "unknown".
 * @returns {boolean} whether to show the confirmation dialog before
 *   arranging. `false` means arrange immediately, no dialog.
 */
export function shouldConfirmArrange(itemCount, dirtySinceArrange) {
    if (!itemCount) return false;   // nothing placed — nothing to lose
    if (typeof dirtySinceArrange === "boolean") return dirtySinceArrange;
    return true;   // can't know cheaply — the brief's stated fallback
}
