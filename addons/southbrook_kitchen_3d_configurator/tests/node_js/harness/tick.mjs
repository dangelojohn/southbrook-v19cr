/**
 * Owl's renderer batches re-renders onto `requestAnimationFrame`
 * (harness/setup_dom.mjs shims it to `setTimeout(cb, 0)` since jsdom has
 * no real animation frame). Await this after a state mutation before
 * asserting on rendered DOM.
 *
 * `tick()` (no argument) waits for OWL'S OWN pending render, if any, to
 * actually land in the DOM — it does NOT wait a fixed wall-clock
 * duration. That used to be `setTimeout(resolve, 10)`, and the flake
 * audit (see README.md) traced every one of its intermittent full-suite
 * failures (contracts/04_active_wall_indicator,
 * 23_made_to_order_availability, 31_panel_collapse_responsive,
 * 32_toast_and_panel_a11y, 33_undo_redo) to that fixed 10ms being a real
 * race, not just an ugly-but-safe margin — confirmed live via `ps`
 * during a full-suite run: `node --test` forks one OS process PER TEST
 * FILE, and on this 8-core box that's 7 concurrent `node` workers each
 * pegged near 65-70% CPU. Under that genuine cross-process contention,
 * the OS can suspend this process for stretches regardless of how
 * "instant" the pending work normally is, so a fixed 10ms budget can
 * expire before Owl's renderer ever gets to run — the test then reads
 * stale, pre-patch DOM.
 *
 * Why this isn't "flush exactly N microtasks, then one
 * requestAnimationFrame": that was the first fix attempted here, and it
 * made things WORSE — 20/505 deterministic failures instead of an
 * occasional flake. Traced with a scratch instrumentation harness
 * (polling `component.__owl__.fiber` / `component.__owl__.app.scheduler
 * .{frame,tasks}` after each manually-awaited microtask): a plain state
 * mutation goes through `useState`'s `batched()` wrapper (owl.es.js
 * ~line 254, its own `await Promise.resolve()`) and THEN
 * `ComponentNode.render()`'s own separate `await Promise.resolve()`
 * before it calls `scheduler.flush()` (which is what actually calls
 * `requestAnimationFrame(() => this.processTasks())` —
 * `processTasks()` -> `RootFiber.complete()` -> `node._patch()` is the
 * real DOM write) — but the REAL hop count observed for this
 * component's actual render tree was 3, not the 2 that reading just
 * those two functions predicts (this component's template renders
 * multiple child components, and re-rendering the tree can involve
 * their own lifecycle hooks, which add more chained microtask hops than
 * a bare two-function reading suggests). Hard-coding 2 hops meant our
 * OWN `requestAnimationFrame` call got registered — and therefore
 * fired, same-delay timers being FIFO — BEFORE Owl ever registered its
 * real one. Hard-coding any other specific N would be just as fragile:
 * the true depth depends on which children a given state change
 * happens to re-render, which varies per test.
 *
 * The fix that's actually robust doesn't count hops at all: it's the
 * standard double-`requestAnimationFrame` wait. Node's microtask queue
 * drains EXHAUSTIVELY before any macrotask runs — including microtasks
 * that get newly enqueued mid-drain (i.e. chained `await`s, however
 * many) — so by the time our FIRST `requestAnimationFrame` callback
 * (itself a macrotask) fires, Owl's entire batched-render microtask
 * chain has already run to completion, WHATEVER its length, and its own
 * `requestAnimationFrame` macrotask (if the render needed one) is
 * already registered. Our SECOND `requestAnimationFrame` call is then
 * registered strictly after Owl's, so it's guaranteed to fire strictly
 * after Owl's DOM patch — an ordering guarantee that holds regardless
 * of CPU contention and regardless of how deep any particular render's
 * child tree happens to be, which is exactly what made hop-counting
 * unreliable. (Verified with a 5-trial repro of the exact scenario that
 * broke under hop-counting: 5/5 correct with double-rAF, and 23
 * consecutive full-suite runs afterwards with no new flakes — see
 * README.md's "Flake audit" section for the run tables.)
 *
 * `tick(ms)` — an explicit `ms` is a request to wait out a REAL
 * application timer of that exact duration (e.g. the
 * customer-autocomplete debounce in
 * contracts/35_customer_autocomplete.test.mjs) — left as a plain
 * `setTimeout(resolve, ms)`, unchanged. Those callers want wall-clock
 * time on purpose and already supply a wide margin; the flake audit's
 * 30-run baseline never once caught a failure on this path, so folding
 * it into the render-flush wait above isn't needed and would just be an
 * untested behavior change for no benefit.
 */
export function tick(ms) {
    if (ms !== undefined) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }
    return flushPendingOwlRender();
}

async function flushPendingOwlRender() {
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
}
