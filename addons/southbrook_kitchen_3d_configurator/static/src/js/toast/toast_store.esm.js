/** @odoo-module **/
/**
 * Southbrook Kitchen 3D Configurator — toast discipline (SBK-030).
 *
 * Pure, DOM/OWL-agnostic reducer for the configurator's own toast
 * stack. It replaces the ad-hoc `useService("notification").add(...)`
 * calls that used to be scattered across kitchen_configurator.js.
 *
 * Why a local store instead of Odoo's global notification service:
 * that service is a page-level singleton — it stacks every call
 * unboundedly, has no dedupe, auto-dismiss is per-call-not-per-message,
 * and it renders in a fixed screen corner it doesn't know is, for THIS
 * client action, sitting on top of the configurator's own header and
 * right-hand inventory panel. The audit evidence (SBK-030): adding six
 * B24 cabinets to a run that only fits four produced five stacked,
 * permanent, ~unclickable "run too long" toasts that covered the UI —
 * and the add was allowed anyway.
 *
 * This module owns none of that rendering — it is the reducer half
 * only, so it can be exercised head­lessly in Node (no jsdom) per the
 * task-17 test requirement. kitchen_configurator.js wires it to
 * `state.toastView` (an OWL `useState` mirror) via `_toast()` /
 * `_dismissToast()` / `_dismissAllToasts()` / `_tickToasts()`; the
 * `.o_sbk_toast_stack` block in the template renders `state.toastView`.
 *
 * Contract (SBK-030 acceptance criteria):
 *  - `add()` dedupes on (type, message) by default — a second call
 *    with the SAME text bumps `count` on the existing toast instead of
 *    stacking a new one, and refreshes its auto-dismiss clock (a
 *    warning that keeps recurring shouldn't expire mid-recurrence).
 *  - Non-sticky toasts auto-dismiss `autoDismissMs` (~8s) after their
 *    last bump; sticky toasts (errors that need a decision, e.g. the
 *    over-length "Add anyway" prompt) never auto-dismiss.
 *  - `getView()` caps the rendered list at `maxVisible` (3) — anything
 *    beyond that is summarized as a single `overflowCount`, never
 *    individually stacked.
 *  - `dismiss(id)` / `dismissAll()` are synchronous and always
 *    succeed on the first call — no toast can get "stuck".
 */
export const AUTO_DISMISS_MS = 8000;
export const MAX_VISIBLE = 3;

let _uid = 1;

export class ToastStore {
    /**
     * @param {object} [opts]
     * @param {() => number} [opts.now] — injectable clock (tests pass
     *   a fake one so timer behavior doesn't depend on wall-clock waits).
     * @param {number} [opts.autoDismissMs]
     * @param {number} [opts.maxVisible]
     */
    constructor({ now, autoDismissMs = AUTO_DISMISS_MS, maxVisible = MAX_VISIBLE } = {}) {
        this._now = now || (() => Date.now());
        this._autoDismissMs = autoDismissMs;
        this._maxVisible = maxVisible;
        // Newest-first — a rep who triggers several different warnings
        // in a row sees the most recent one on top, matching the
        // instinctive "what did I just do" read.
        this.toasts = [];
    }

    /**
     * @param {string} message
     * @param {object} [opts]
     * @param {"info"|"success"|"warning"|"danger"} [opts.type]
     * @param {boolean} [opts.sticky] — never auto-dismisses.
     * @param {string} [opts.dedupeKey] — defaults to `${type}|${message}`.
     * @param {Array<{label:string, onClick:Function, dismissOnClick?:boolean}>} [opts.actions]
     * @returns {object} the (new or bumped) toast entry.
     */
    add(message, opts = {}) {
        const type = opts.type || "info";
        const sticky = !!opts.sticky;
        const dedupeKey = opts.dedupeKey || `${type}|${message}`;
        const now = this._now();

        const existing = this.toasts.find((t) => t.dedupeKey === dedupeKey);
        if (existing) {
            existing.count += 1;
            existing.lastAt = now;
            existing.expiresAt = sticky ? null : now + this._autoDismissMs;
            existing.message = message;   // a bumped count can update the text (e.g. an inches figure)
            existing.type = type;
            existing.sticky = sticky;
            if (opts.actions) existing.actions = opts.actions;
            return existing;
        }

        const toast = {
            id: _uid++,
            message,
            type,
            sticky,
            dedupeKey,
            count: 1,
            createdAt: now,
            lastAt: now,
            expiresAt: sticky ? null : now + this._autoDismissMs,
            actions: opts.actions || null,
        };
        this.toasts.unshift(toast);
        return toast;
    }

    /** @returns {boolean} whether a toast with that id was present. */
    dismiss(id) {
        const before = this.toasts.length;
        this.toasts = this.toasts.filter((t) => t.id !== id);
        return this.toasts.length !== before;
    }

    dismissAll() {
        this.toasts = [];
    }

    /**
     * Drop every expired non-sticky toast as of `now`.
     * @returns {number[]} ids removed (empty when nothing changed).
     */
    tick(now) {
        const at = now === undefined ? this._now() : now;
        const removed = [];
        this.toasts = this.toasts.filter((t) => {
            const expired = t.expiresAt !== null && at >= t.expiresAt;
            if (expired) removed.push(t.id);
            return !expired;
        });
        return removed;
    }

    /**
     * Selector for what actually renders: the newest `maxVisible`
     * toasts plus a count of everything past the cap. The overflow is
     * intentionally NOT individually addressable — cap+rollup, not
     * pagination — `dismissAll()` (or waiting out auto-dismiss) is how
     * it clears.
     */
    getView() {
        const visible = this.toasts.slice(0, this._maxVisible);
        const overflowCount = Math.max(0, this.toasts.length - this._maxVisible);
        return { visible, overflowCount };
    }
}
