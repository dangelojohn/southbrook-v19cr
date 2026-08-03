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
 * race, not just an ugly-but-safe margin:
 *
 * owl.es.js's `ComponentNode.render()` does its own `await
 * Promise.resolve()` BEFORE calling `scheduler.flush()` — and `flush()`
 * is what actually calls `requestAnimationFrame(() =>
 * this.processTasks())`; `processTasks()` -> `RootFiber.complete()` ->
 * `node._patch()` is where the real DOM write happens. So a state
 * mutation needs (a) one microtask hop, THEN (b) one
 * requestAnimationFrame-scheduled macrotask, before the DOM reflects
 * it — and Owl doesn't even REGISTER that macrotask until partway
 * through (a).
 *
 * `node --test` forks one OS process PER TEST FILE — confirmed live via
 * `ps` during a full-suite run: 7 concurrent `node` workers on this
 * 8-core box, each pegged near 65-70% CPU. That's genuine cross-process
 * CPU contention, not a hypothetical. Under it, step (a)'s microtask can
 * itself take longer than a fixed 10ms wall-clock budget to actually
 * run (the OS can suspend this whole process for stretches regardless
 * of how "instant" the JS work inside a microtask normally is). When
 * that happens, Owl's rAF callback gets REGISTERED later than the old
 * fixed timer's deadline, so the fixed timer fires — and the test reads
 * the DOM — BEFORE Owl's callback ever ran: stale, pre-patch DOM.
 *
 * The fix is an ORDERING guarantee, not a bigger timing guess: flush
 * one microtask first (`Promise.resolve().then(...)`) so Owl's own
 * render continuation — if a render is pending — runs and registers ITS
 * requestAnimationFrame callback BEFORE we register ours. Node/libuv
 * fires same-delay timers in registration (FIFO) order, so our
 * `requestAnimationFrame(resolve)` is then guaranteed to fire strictly
 * after Owl's, however long either one actually takes in wall-clock
 * time — this holds regardless of CPU contention, which is exactly what
 * a fixed timeout can't promise.
 *
 * `tick(ms)` — an explicit `ms` is a request to wait out a REAL
 * application timer of that exact duration (e.g. the
 * customer-autocomplete debounce in
 * contracts/35_customer_autocomplete.test.mjs) — left as a plain
 * `setTimeout(resolve, ms)`, unchanged. Those callers want wall-clock
 * time on purpose and already supply a wide margin; the flake audit's
 * 30-run baseline never once caught a failure on this path, so folding
 * it into the ordering-based wait above isn't needed and would just be
 * an untested behavior change for no benefit.
 */
export function tick(ms) {
    if (ms !== undefined) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }
    return new Promise((resolve) => {
        Promise.resolve().then(() => {
            requestAnimationFrame(() => resolve());
        });
    });
}
