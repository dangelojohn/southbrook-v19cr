/**
 * Owl's renderer batches re-renders onto `requestAnimationFrame`
 * (harness/setup_dom.mjs shims it to `setTimeout(cb, 0)` since jsdom has
 * no real animation frame). Await this after a state mutation before
 * asserting on rendered DOM.
 */
export function tick(ms = 10) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
